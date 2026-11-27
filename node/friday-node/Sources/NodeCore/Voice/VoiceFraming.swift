// `friday-voice/v1` framing (Phase 11 brief D6; Mac session M1). A port of
// node/friday-voice/friday_voice/protocol/framing.py:
//
//     frame = length:u32 big-endian (1 .. 1 MiB) || body
//     body  = header (canonical UTF-8 JSON object, no raw newline) || "\n" || payload
//
// Only the audio types (`audio` host->child, `play` child->host) carry a
// payload: 16 kHz mono PCM16 little-endian, `pcm_bytes` long (<= 64 KiB,
// even). The canonical header is sorted keys, no whitespace, UTF-8 with
// non-ASCII kept (JSON.canonical gives the same bytes as Python's
// `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)` for
// every header the protocol can carry: ASCII keys, integers only).
//
// Every refusal carries the Python side's code, in the Python side's order:
// the length prefix, the header line (16 KiB), strict UTF-8, the JSON
// (`VoiceJSON` below mirrors `json.loads` with the duplicate-key, float and
// constant hooks), the object check, the payload, then (with a direction)
// the schema. Three hostile-input-only differences, documented in the Phase
// 11 brief's M1 record: a lone `\uD800` escape (Python keeps it, Swift
// strings cannot) is `schema_invalid`; nesting deeper than 128 is
// `invalid_json` (Python parses any depth that fits the 16 KiB header and
// then refuses the frame as `header_not_object`, `unknown_type` or
// `schema_invalid`: the link drops either way; 128 keeps the recursion far
// inside a 512 KiB dispatch-thread stack, JSON.maxDepth's reasoning); an
// integer of more than 4300 digits is `invalid_json` in both, and one
// outside Int64 fails its schema bound in both.

import Foundation

public enum VoiceFraming {
    /// Encode one frame. For an audio type `pcm_bytes` is set from the
    /// payload. With `direction`, the header is schema-checked first.
    public static func encode(_ header: [String: JSONValue], payload: Data = Data(), direction: VoiceProtocol.Direction?)
        throws -> Data
    {
        var header = header
        if let type = header["type"]?.stringValue, VoiceProtocol.audioTypes[type] != nil {
            header["pcm_bytes"] = .int(Int64(payload.count))
        }
        try checkPayload(header, payload)
        if let direction { try VoiceProtocol.validate(.object(header), direction) }
        guard let text = try? JSON.canonical(.object(header)) else { throw VoiceProtocolError("schema_invalid") }
        var body = text
        body.append(0x0A)
        body.append(payload)
        guard body.count <= VoiceProtocol.maxFrameBytes else { throw VoiceProtocolError("frame_too_large") }
        return lengthPrefix(body.count) + body
    }

    /// The 4-byte big-endian length.
    public static func lengthPrefix(_ length: Int) -> Data {
        let value = UInt32(truncatingIfNeeded: length)
        return Data([UInt8(value >> 24), UInt8((value >> 16) & 0xff), UInt8((value >> 8) & 0xff), UInt8(value & 0xff)])
    }

    static func checkLength(_ length: Int) throws {
        if length == 0 { throw VoiceProtocolError("frame_empty") }
        if length > VoiceProtocol.maxFrameBytes { throw VoiceProtocolError("frame_too_large") }
    }

    static func checkPayload(_ header: [String: JSONValue], _ payload: Data) throws {
        if let type = header["type"]?.stringValue, VoiceProtocol.audioTypes[type] != nil {
            guard case .int(let declared)? = header["pcm_bytes"], declared == Int64(payload.count) else {
                throw VoiceProtocolError("payload_length_mismatch")
            }
            if payload.count > VoiceProtocol.maxPCMBytes { throw VoiceProtocolError("pcm_too_large") }
            if payload.count % 2 != 0 { throw VoiceProtocolError("pcm_odd_length") }
        } else if !payload.isEmpty {
            throw VoiceProtocolError("payload_unexpected")
        }
    }

    /// Decode one frame body (no prefix) into (header, payload).
    public static func decodeBody(_ body: Data, direction: VoiceProtocol.Direction? = nil) throws
        -> (header: [String: JSONValue], payload: Data)
    {
        let bytes = [UInt8](body)
        let limit = min(bytes.count, VoiceProtocol.maxHeaderBytes + 1)
        guard let newline = bytes[0..<limit].firstIndex(of: 0x0A) else {
            if bytes.count > VoiceProtocol.maxHeaderBytes { throw VoiceProtocolError("header_too_large") }
            throw VoiceProtocolError("header_unterminated")
        }
        let rawHeader = Array(bytes[0..<newline])
        let payload = Data(bytes[(newline + 1)...])
        guard String(validating: rawHeader, as: UTF8.self) != nil else { throw VoiceProtocolError("invalid_utf8") }
        let value = try VoiceJSON.parse(rawHeader)
        guard case .object(let header) = value else { throw VoiceProtocolError("header_not_object") }
        try checkPayload(header, payload)
        if let direction { try VoiceProtocol.validate(.object(header), direction) }
        return (header, payload)
    }

    /// Decode exactly one complete frame (prefix included).
    public static func decode(_ data: Data, direction: VoiceProtocol.Direction? = nil) throws
        -> (header: [String: JSONValue], payload: Data)
    {
        let bytes = [UInt8](data)
        guard bytes.count >= 4 else { throw VoiceProtocolError("short_read") }
        let length = Int(bytes[0]) << 24 | Int(bytes[1]) << 16 | Int(bytes[2]) << 8 | Int(bytes[3])
        try checkLength(length)
        let body = bytes.count - 4
        if body != length { throw VoiceProtocolError(body < length ? "short_read" : "payload_length_mismatch") }
        return try decodeBody(Data(bytes[4...]), direction: direction)
    }

    /// The type of an encoded frame (tests and the runners).
    public static func type(of frame: Data) -> String? {
        (try? decode(frame))?.header["type"]?.stringValue
    }
}

/// Incremental decoder for a byte stream (framing.FrameDecoder): `feed`
/// returns the complete frames decoded before any error, then the error.
/// After an error the decoder is dead and every later feed returns it.
public final class VoiceFrameDecoder {
    public let direction: VoiceProtocol.Direction?
    private var buffer: [UInt8] = []
    public private(set) var failed: String?

    public init(direction: VoiceProtocol.Direction? = nil) { self.direction = direction }

    public func feed(_ data: Data) -> (frames: [(header: [String: JSONValue], payload: Data)], error: String?) {
        if let failed { return ([], failed) }
        buffer.append(contentsOf: data)
        var frames: [(header: [String: JSONValue], payload: Data)] = []
        var start = 0
        do {
            while buffer.count - start >= 4 {
                let length =
                    Int(buffer[start]) << 24 | Int(buffer[start + 1]) << 16 | Int(buffer[start + 2]) << 8
                    | Int(buffer[start + 3])
                try VoiceFraming.checkLength(length)
                if buffer.count - start < 4 + length { break }
                let body = Data(buffer[(start + 4)..<(start + 4 + length)])
                start += 4 + length
                frames.append(try VoiceFraming.decodeBody(body, direction: direction))
            }
            buffer.removeFirst(start)
        } catch let error as VoiceProtocolError {
            failed = error.code
            buffer = []
        } catch {
            failed = "invalid_json"
            buffer = []
        }
        return (frames, failed)
    }

    /// End of stream: `short_read` when bytes were left inside a frame,
    /// else the earlier error or nil.
    public func close() -> String? {
        if !buffer.isEmpty && failed == nil {
            failed = "short_read"
            buffer = []
        }
        return failed
    }
}

/// `json.loads(text, object_pairs_hook=_reject_duplicates,
/// parse_float=_reject_float, parse_constant=_reject_constant)` over bytes
/// already known to be valid UTF-8, with the framing codes.
enum VoiceJSON {
    static let maxDepth = 128
    static let maxIntegerDigits = 4300  // CPython's int_max_str_digits

    static func parse(_ bytes: [UInt8]) throws -> JSONValue {
        var parser = Scanner(bytes: bytes)
        parser.skipWhitespace()
        let value = try parser.value()
        parser.skipWhitespace()
        guard parser.index == bytes.count else { throw VoiceProtocolError("invalid_json") }
        return value
    }

    private struct Scanner {
        let bytes: [UInt8]
        var index = 0
        var depth = 0

        init(bytes: [UInt8]) { self.bytes = bytes }

        var invalid: VoiceProtocolError { VoiceProtocolError("invalid_json") }

        mutating func skipWhitespace() {
            while index < bytes.count, [0x20, 0x09, 0x0A, 0x0D].contains(bytes[index]) { index += 1 }
        }

        func peek(_ offset: Int = 0) -> UInt8? {
            index + offset < bytes.count ? bytes[index + offset] : nil
        }

        mutating func value() throws -> JSONValue {
            guard let byte = peek() else { throw invalid }
            switch byte {
            case UInt8(ascii: "{"): return try object()
            case UInt8(ascii: "["): return try array()
            case UInt8(ascii: "\""): return .string(try string())
            case UInt8(ascii: "t"): try literal("true"); return .bool(true)
            case UInt8(ascii: "f"): try literal("false"); return .bool(false)
            case UInt8(ascii: "n"): try literal("null"); return .null
            case UInt8(ascii: "-"), UInt8(ascii: "0")...UInt8(ascii: "9"): return try number()
            default: throw invalid  // NaN, Infinity included (parse_constant)
            }
        }

        mutating func literal(_ word: String) throws {
            let expected = Array(word.utf8)
            guard index + expected.count <= bytes.count, Array(bytes[index..<(index + expected.count)]) == expected
            else { throw invalid }
            index += expected.count
        }

        mutating func enter() throws {
            depth += 1
            if depth > VoiceJSON.maxDepth { throw invalid }
        }

        mutating func object() throws -> JSONValue {
            try enter()
            defer { depth -= 1 }
            index += 1
            var pairs: [(String, JSONValue)] = []
            skipWhitespace()
            if peek() == UInt8(ascii: "}") {
                index += 1
                return .object([:])
            }
            while true {
                skipWhitespace()
                guard peek() == UInt8(ascii: "\"") else { throw invalid }
                let key = try string()
                skipWhitespace()
                guard peek() == UInt8(ascii: ":") else { throw invalid }
                index += 1
                skipWhitespace()
                pairs.append((key, try value()))
                skipWhitespace()
                guard let next = peek() else { throw invalid }
                index += 1
                if next == UInt8(ascii: "}") { break }
                guard next == UInt8(ascii: ",") else { throw invalid }
            }
            // The pairs hook runs once the object is complete.
            var fields: [String: JSONValue] = [:]
            for (key, value) in pairs {
                guard fields[key] == nil else { throw VoiceProtocolError("duplicate_key") }
                fields[key] = value
            }
            return .object(fields)
        }

        mutating func array() throws -> JSONValue {
            try enter()
            defer { depth -= 1 }
            index += 1
            var items: [JSONValue] = []
            skipWhitespace()
            if peek() == UInt8(ascii: "]") {
                index += 1
                return .array([])
            }
            while true {
                skipWhitespace()
                items.append(try value())
                skipWhitespace()
                guard let next = peek() else { throw invalid }
                index += 1
                if next == UInt8(ascii: "]") { break }
                guard next == UInt8(ascii: ",") else { throw invalid }
            }
            return .array(items)
        }

        static func isDigit(_ byte: UInt8?) -> Bool { byte.map { $0 >= 0x30 && $0 <= 0x39 } ?? false }

        /// NUMBER_RE `(-?(?:0|[1-9]\d*))(\.\d+)?([eE][-+]?\d+)?`: a fraction
        /// or an exponent is a float (refused); a number that ends early
        /// leaves the rest to the caller (which refuses it as extra data).
        mutating func number() throws -> JSONValue {
            let start = index
            if peek() == UInt8(ascii: "-") { index += 1 }
            guard Self.isDigit(peek()) else { throw invalid }
            if peek() == UInt8(ascii: "0") {
                index += 1
            } else {
                while Self.isDigit(peek()) { index += 1 }
            }
            let integerEnd = index
            if peek() == UInt8(ascii: "."), Self.isDigit(peek(1)) { throw VoiceProtocolError("number_not_integer") }
            if let e = peek(), e == UInt8(ascii: "e") || e == UInt8(ascii: "E") {
                var probe = 1
                if let sign = peek(1), sign == UInt8(ascii: "+") || sign == UInt8(ascii: "-") { probe = 2 }
                if Self.isDigit(peek(probe)) { throw VoiceProtocolError("number_not_integer") }
            }
            let text = String(decoding: bytes[start..<integerEnd], as: UTF8.self)
            let digits = text.hasPrefix("-") ? text.count - 1 : text.count
            if digits > VoiceJSON.maxIntegerDigits { throw invalid }
            if let value = Int64(text) { return .int(value) }
            // Valid JSON, outside Int64: every integer of the protocol is
            // bounded far inside it, so the schema refuses it (as in Python).
            return .int(text.hasPrefix("-") ? Int64.min : Int64.max)
        }

        mutating func hex4() throws -> UInt32 {
            guard index + 4 <= bytes.count else { throw invalid }
            var value: UInt32 = 0
            for byte in bytes[index..<(index + 4)] {
                let digit: UInt32
                switch byte {
                case 0x30...0x39: digit = UInt32(byte - 0x30)
                case 0x41...0x46: digit = UInt32(byte - 0x41 + 10)
                case 0x61...0x66: digit = UInt32(byte - 0x61 + 10)
                default: throw invalid
                }
                value = value * 16 + digit
            }
            index += 4
            return value
        }

        mutating func string() throws -> String {
            index += 1  // the opening quote
            var out = [UInt8]()
            var scalars = String.UnicodeScalarView()
            func flush() {
                if !out.isEmpty {
                    scalars.append(contentsOf: String(decoding: out, as: UTF8.self).unicodeScalars)
                    out.removeAll()
                }
            }
            while true {
                guard let byte = peek() else { throw invalid }
                if byte == UInt8(ascii: "\"") {
                    index += 1
                    break
                }
                if byte < 0x20 { throw invalid }  // strict: no raw control character
                if byte != UInt8(ascii: "\\") {
                    out.append(byte)
                    index += 1
                    continue
                }
                flush()
                index += 1
                guard let escape = peek() else { throw invalid }
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
                    var code = try hex4()
                    if (0xD800...0xDBFF).contains(code), peek() == UInt8(ascii: "\\"), peek(1) == UInt8(ascii: "u") {
                        let save = index
                        index += 2
                        let low = try hex4()
                        if (0xDC00...0xDFFF).contains(low) {
                            code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)
                        } else {
                            index = save
                        }
                    }
                    // A lone surrogate is a str in Python; no Swift string
                    // can hold it: refused as its schema would (module note).
                    guard let scalar = Unicode.Scalar(code) else { throw VoiceProtocolError("schema_invalid") }
                    scalars.append(scalar)
                default:
                    throw invalid
                }
            }
            flush()
            return String(scalars)
        }
    }
}
