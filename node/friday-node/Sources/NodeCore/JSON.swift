// Strict JSON and canonical JSON for protocol v1 (Phase 9 brief D5).
//
// Mirrors backend/nodes/protocol/canonical.py:
// - `JSON.parse` is `parse_strict`: UTF-8, no duplicate keys, integers only
//   (within +-(2^53 - 1)), no fractions or exponents, no NaN/Infinity, no
//   trailing data, no raw control characters in strings.
// - `JSON.canonical` is `canonicalize`: the RFC 8785 (JCS) subset the
//   schemas allow — keys sorted by UTF-16 code units, no whitespace,
//   integers in plain decimal, JCS string escapes, everything else literal.
//
// Two unavoidable differences, both on hostile input only:
// - a Swift `String` cannot hold a lone surrogate, so a `\ud800` escape
//   without its pair is refused here, at parse time (`malformed`), where
//   Python refuses it later (canonicalization / signature);
// - Swift compares strings by canonical equivalence, so two keys that differ
//   only in Unicode normalization ("é" vs "e" + U+0301) are a duplicate key
//   here and two keys in Python. Every protocol key and enum value is ASCII.

import CryptoKit
import Foundation

public enum JSONValue: Equatable, Sendable {
    case null
    case bool(Bool)
    case int(Int64)
    case string(String)
    case array([JSONValue])
    case object([String: JSONValue])

    public subscript(key: String) -> JSONValue? {
        if case .object(let fields) = self { return fields[key] }
        return nil
    }

    public var stringValue: String? {
        if case .string(let value) = self { return value }
        return nil
    }

    public var intValue: Int64? {
        if case .int(let value) = self { return value }
        return nil
    }

    public var boolValue: Bool? {
        if case .bool(let value) = self { return value }
        return nil
    }

    public var objectValue: [String: JSONValue]? {
        if case .object(let fields) = self { return fields }
        return nil
    }

    public var arrayValue: [JSONValue]? {
        if case .array(let items) = self { return items }
        return nil
    }

    public var isNull: Bool { self == .null }
}

extension JSONValue: ExpressibleByStringLiteral, ExpressibleByIntegerLiteral,
    ExpressibleByBooleanLiteral, ExpressibleByNilLiteral, ExpressibleByArrayLiteral,
    ExpressibleByDictionaryLiteral
{
    public init(stringLiteral value: String) { self = .string(value) }
    public init(integerLiteral value: Int64) { self = .int(value) }
    public init(booleanLiteral value: Bool) { self = .bool(value) }
    public init(nilLiteral: ()) { self = .null }
    public init(arrayLiteral elements: JSONValue...) { self = .array(elements) }
    public init(dictionaryLiteral elements: (String, JSONValue)...) {
        var fields: [String: JSONValue] = [:]
        for (key, value) in elements { fields[key] = value }
        self = .object(fields)
    }
}

public struct JSONError: Error, Equatable, CustomStringConvertible {
    public let reason: String
    public var description: String { reason }
}

public enum JSON {
    public static let maxSafeInteger: Int64 = 9_007_199_254_740_991
    /// Deeper documents are refused as malformed. Valid frames nest about a
    /// dozen levels; 128 stays far inside a 512 KiB dispatch-thread stack in
    /// an unoptimized build (Python's limit is ~1000: a frame nested 129-999
    /// deep is `malformed` here and `schema` there, both frame errors).
    public static let maxDepth = 128

    // MARK: Parsing

    public static func parse(_ data: Data) throws -> JSONValue {
        var parser = Parser(bytes: [UInt8](data))
        return try parser.document()
    }

    public static func parse(_ text: String) throws -> JSONValue {
        try parse(Data(text.utf8))
    }

    // MARK: Canonical serialization

    public static func canonical(_ value: JSONValue) throws -> Data {
        var out = ""
        try serialize(value, into: &out)
        return Data(out.utf8)
    }

    public static func canonicalString(_ value: JSONValue) throws -> String {
        var out = ""
        try serialize(value, into: &out)
        return out
    }

    /// sha256 (lower-case hex) of the canonical JSON: the `args_sha256`,
    /// `output_sha256` and `catalogue_sha256` rule.
    public static func sha256Hex(_ value: JSONValue) throws -> String {
        hexDigest(try canonical(value))
    }

    public static func hexDigest(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    private static func serialize(_ value: JSONValue, into out: inout String) throws {
        switch value {
        case .null: out += "null"
        case .bool(let flag): out += flag ? "true" : "false"
        case .int(let number):
            guard number >= -maxSafeInteger, number <= maxSafeInteger else {
                throw JSONError(reason: "integer outside the I-JSON safe range")
            }
            out += String(number)
        case .string(let text): escape(text, into: &out)
        case .array(let items):
            out += "["
            for (index, item) in items.enumerated() {
                if index > 0 { out += "," }
                try serialize(item, into: &out)
            }
            out += "]"
        case .object(let fields):
            out += "{"
            let keys = fields.keys.sorted { Array($0.utf16).lexicographicallyPrecedes(Array($1.utf16)) }
            for (index, key) in keys.enumerated() {
                if index > 0 { out += "," }
                escape(key, into: &out)
                out += ":"
                try serialize(fields[key]!, into: &out)
            }
            out += "}"
        }
    }

    private static func escape(_ text: String, into out: inout String) {
        out += "\""
        for scalar in text.unicodeScalars {
            switch scalar {
            case "\"": out += "\\\""
            case "\\": out += "\\\\"
            case "\u{08}": out += "\\b"
            case "\t": out += "\\t"
            case "\n": out += "\\n"
            case "\u{0C}": out += "\\f"
            case "\r": out += "\\r"
            default:
                if scalar.value < 0x20 {
                    out += String(format: "\\u%04x", scalar.value)
                } else {
                    out.unicodeScalars.append(scalar)
                }
            }
        }
        out += "\""
    }

    /// Plain (non-canonical) JSON text for a frame on the wire. Canonical
    /// output is valid JSON, so the wire form simply reuses it.
    public static func encode(_ value: JSONValue) throws -> String {
        try canonicalString(value)
    }
}

// MARK: - The strict parser

private struct Parser {
    let bytes: [UInt8]
    var index = 0
    var depth = 0

    init(bytes: [UInt8]) { self.bytes = bytes }

    mutating func document() throws -> JSONValue {
        skipWhitespace()
        let value = try parseValue()
        skipWhitespace()
        guard index == bytes.count else { throw fail("trailing data") }
        return value
    }

    func fail(_ reason: String) -> JSONError { JSONError(reason: reason) }

    mutating func skipWhitespace() {
        while index < bytes.count {
            switch bytes[index] {
            case 0x20, 0x09, 0x0A, 0x0D: index += 1
            default: return
            }
        }
    }

    mutating func parseValue() throws -> JSONValue {
        guard index < bytes.count else { throw fail("unexpected end") }
        switch bytes[index] {
        case UInt8(ascii: "{"): return try parseObject()
        case UInt8(ascii: "["): return try parseArray()
        case UInt8(ascii: "\""): return .string(try parseString())
        case UInt8(ascii: "t"): try literal("true"); return .bool(true)
        case UInt8(ascii: "f"): try literal("false"); return .bool(false)
        case UInt8(ascii: "n"): try literal("null"); return .null
        case UInt8(ascii: "-"), UInt8(ascii: "0")...UInt8(ascii: "9"): return try parseNumber()
        default: throw fail("unexpected character")
        }
    }

    mutating func literal(_ word: String) throws {
        let expected = Array(word.utf8)
        guard index + expected.count <= bytes.count,
            Array(bytes[index..<index + expected.count]) == expected
        else { throw fail("bad literal") }
        index += expected.count
    }

    mutating func enter() throws {
        depth += 1
        if depth > JSON.maxDepth { throw fail("nesting too deep") }
    }

    mutating func parseObject() throws -> JSONValue {
        try enter()
        defer { depth -= 1 }
        index += 1
        var fields: [String: JSONValue] = [:]
        skipWhitespace()
        if index < bytes.count, bytes[index] == UInt8(ascii: "}") {
            index += 1
            return .object(fields)
        }
        while true {
            skipWhitespace()
            guard index < bytes.count, bytes[index] == UInt8(ascii: "\"") else {
                throw fail("object key must be a string")
            }
            let key = try parseString()
            skipWhitespace()
            guard index < bytes.count, bytes[index] == UInt8(ascii: ":") else { throw fail("missing colon") }
            index += 1
            skipWhitespace()
            let value = try parseValue()
            guard fields[key] == nil else { throw fail("duplicate object key") }
            fields[key] = value
            skipWhitespace()
            guard index < bytes.count else { throw fail("unexpected end") }
            if bytes[index] == UInt8(ascii: ",") {
                index += 1
                continue
            }
            if bytes[index] == UInt8(ascii: "}") {
                index += 1
                return .object(fields)
            }
            throw fail("expected , or }")
        }
    }

    mutating func parseArray() throws -> JSONValue {
        try enter()
        defer { depth -= 1 }
        index += 1
        var items: [JSONValue] = []
        skipWhitespace()
        if index < bytes.count, bytes[index] == UInt8(ascii: "]") {
            index += 1
            return .array(items)
        }
        while true {
            skipWhitespace()
            items.append(try parseValue())
            skipWhitespace()
            guard index < bytes.count else { throw fail("unexpected end") }
            if bytes[index] == UInt8(ascii: ",") {
                index += 1
                continue
            }
            if bytes[index] == UInt8(ascii: "]") {
                index += 1
                return .array(items)
            }
            throw fail("expected , or ]")
        }
    }

    mutating func parseNumber() throws -> JSONValue {
        let start = index
        if bytes[index] == UInt8(ascii: "-") { index += 1 }
        guard index < bytes.count, isDigit(bytes[index]) else { throw fail("bad number") }
        if bytes[index] == UInt8(ascii: "0") {
            index += 1
        } else {
            while index < bytes.count, isDigit(bytes[index]) { index += 1 }
        }
        if index < bytes.count {
            let next = bytes[index]
            if next == UInt8(ascii: ".") || next == UInt8(ascii: "e") || next == UInt8(ascii: "E") {
                throw fail("non-integer numbers are not allowed")
            }
            if isDigit(next) { throw fail("leading zero") }
        }
        let text = String(decoding: bytes[start..<index], as: UTF8.self)
        guard let number = Int64(text), number >= -JSON.maxSafeInteger, number <= JSON.maxSafeInteger else {
            throw fail("integer outside the I-JSON safe range")
        }
        return .int(number)
    }

    func isDigit(_ byte: UInt8) -> Bool { byte >= 0x30 && byte <= 0x39 }

    mutating func parseString() throws -> String {
        index += 1  // opening quote
        var scalars = String.UnicodeScalarView()
        while true {
            guard index < bytes.count else { throw fail("unterminated string") }
            let byte = bytes[index]
            if byte == UInt8(ascii: "\"") {
                index += 1
                return String(scalars)
            }
            if byte == UInt8(ascii: "\\") {
                index += 1
                guard index < bytes.count else { throw fail("bad escape") }
                let escape = bytes[index]
                index += 1
                switch escape {
                case UInt8(ascii: "\""): scalars.append("\"")
                case UInt8(ascii: "\\"): scalars.append("\\")
                case UInt8(ascii: "/"): scalars.append("/")
                case UInt8(ascii: "b"): scalars.append("\u{08}")
                case UInt8(ascii: "f"): scalars.append("\u{0C}")
                case UInt8(ascii: "n"): scalars.append("\n")
                case UInt8(ascii: "r"): scalars.append("\r")
                case UInt8(ascii: "t"): scalars.append("\t")
                case UInt8(ascii: "u"):
                    let unit = try hex4()
                    if (0xD800...0xDBFF).contains(unit) {
                        guard index + 1 < bytes.count, bytes[index] == UInt8(ascii: "\\"),
                            bytes[index + 1] == UInt8(ascii: "u")
                        else { throw fail("lone surrogate") }
                        index += 2
                        let low = try hex4()
                        guard (0xDC00...0xDFFF).contains(low) else { throw fail("lone surrogate") }
                        let value = 0x10000 + ((unit - 0xD800) << 10) + (low - 0xDC00)
                        scalars.append(Unicode.Scalar(value)!)
                    } else if (0xDC00...0xDFFF).contains(unit) {
                        throw fail("lone surrogate")
                    } else {
                        scalars.append(Unicode.Scalar(unit)!)
                    }
                default: throw fail("bad escape")
                }
                continue
            }
            if byte < 0x20 { throw fail("control character in string") }
            scalars.append(try utf8Scalar())
        }
    }

    mutating func hex4() throws -> UInt32 {
        guard index + 4 <= bytes.count else { throw fail("bad unicode escape") }
        var value: UInt32 = 0
        for _ in 0..<4 {
            let byte = bytes[index]
            index += 1
            let digit: UInt32
            switch byte {
            case 0x30...0x39: digit = UInt32(byte - 0x30)
            case 0x41...0x46: digit = UInt32(byte - 0x41 + 10)
            case 0x61...0x66: digit = UInt32(byte - 0x61 + 10)
            default: throw fail("bad unicode escape")
            }
            value = value << 4 | digit
        }
        return value
    }

    /// One UTF-8 encoded scalar (strict: no overlongs, no surrogates).
    mutating func utf8Scalar() throws -> Unicode.Scalar {
        let first = bytes[index]
        let length: Int
        var value: UInt32
        switch first {
        case 0x00...0x7F: index += 1; return Unicode.Scalar(first)
        case 0xC2...0xDF: length = 2; value = UInt32(first & 0x1F)
        case 0xE0...0xEF: length = 3; value = UInt32(first & 0x0F)
        case 0xF0...0xF4: length = 4; value = UInt32(first & 0x07)
        default: throw fail("not UTF-8")
        }
        guard index + length <= bytes.count else { throw fail("not UTF-8") }
        for offset in 1..<length {
            let byte = bytes[index + offset]
            guard byte & 0xC0 == 0x80 else { throw fail("not UTF-8") }
            value = value << 6 | UInt32(byte & 0x3F)
        }
        let minimum: UInt32 = length == 2 ? 0x80 : length == 3 ? 0x800 : 0x10000
        guard value >= minimum, value <= 0x10FFFF, let scalar = Unicode.Scalar(value) else {
            throw fail("not UTF-8")
        }
        index += length
        return scalar
    }
}
