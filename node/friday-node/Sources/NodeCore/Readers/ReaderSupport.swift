// Shared helpers of the readers (Phase 9 brief M2; a port of the helpers in
// backend/nodes/reference/readers.py, whose normative rules the conformance
// cases pin).
//
// - Paths: `normalise`, `pathForms`, `matchesPrefixes` (segment boundaries
//   only, both the absolute and the `~/` form of the node's own home).
// - Transcript lines are parsed like Python's `json.loads` (floats, NaN /
//   Infinity, duplicate keys with the last one winning), not with the
//   protocol's strict parser: a transcript is not a protocol frame.
// - Files are listed like pathlib (CPython 3.12): `*/<f>` follows a
//   symlinked directory, `**/<f>` does not; dot files are included; the
//   order is pathlib's, component by component; then the reference filter
//   (no symlinked file, realpath under the root, at most MAX_FILES).

import Foundation

enum ReaderLimits {
    static let maxFiles = 2000
    static let maxFileBytes = 32 * 1024 * 1024
    static let maxLineBytes = 1024 * 1024
    static let maxTurnChars = 2000
    static let maxTitleChars = 512
    static let maxTitles = 50
    static let activeSeconds: Int64 = 300
    static let gitTimeoutSeconds: TimeInterval = 10
}

struct ReaderError: Error {}

// MARK: - Code-point string comparisons (Python's str semantics)

/// Swift `String` compares by canonical equivalence; Python compares code
/// points. Paths and identifiers are compared the Python way.
enum Scalars {
    static func equal(_ a: String, _ b: String) -> Bool {
        a.unicodeScalars.elementsEqual(b.unicodeScalars)
    }

    static func hasPrefix(_ text: String, _ prefix: String) -> Bool {
        text.unicodeScalars.starts(with: prefix.unicodeScalars)
    }

    static func dropFirst(_ text: String, _ count: Int) -> String {
        String(String.UnicodeScalarView(text.unicodeScalars.dropFirst(count)))
    }

    static func count(_ text: String) -> Int { text.unicodeScalars.count }

    static func prefix(_ text: String, _ count: Int) -> String {
        String(String.UnicodeScalarView(text.unicodeScalars.prefix(count)))
    }

    static func less(_ a: String, _ b: String) -> Bool {
        a.unicodeScalars.map(\.value).lexicographicallyPrecedes(b.unicodeScalars.map(\.value))
    }
}

// MARK: - Paths

enum ReaderPaths {
    /// posixpath.normpath.
    static func normpath(_ path: String) -> String {
        guard !path.isEmpty else { return "." }
        let absolute = Scalars.hasPrefix(path, "/")
        // POSIX keeps exactly two leading slashes; more collapse to one.
        let initialSlashes = absolute ? (Scalars.hasPrefix(path, "//") && !Scalars.hasPrefix(path, "///") ? 2 : 1) : 0
        var parts: [String] = []
        for piece in path.unicodeScalars.split(separator: "/", omittingEmptySubsequences: true) {
            let component = String(String.UnicodeScalarView(piece))
            if Scalars.equal(component, ".") { continue }
            if Scalars.equal(component, "..") {
                if let last = parts.last, !Scalars.equal(last, "..") {
                    parts.removeLast()
                } else if !absolute {
                    parts.append("..")
                }
                continue
            }
            parts.append(component)
        }
        let joined = String(repeating: "/", count: initialSlashes) + parts.joined(separator: "/")
        return joined.isEmpty ? "." : joined
    }

    /// The `~`-preserving normalisation; nil for an empty, relative or
    /// home-climbing path.
    static func normalise(_ path: String) -> String? {
        guard !path.isEmpty, !path.unicodeScalars.contains("\u{0}") else { return nil }
        if Scalars.equal(path, "~") || Scalars.hasPrefix(path, "~/") {
            let tail = String(String.UnicodeScalarView(path.unicodeScalars.dropFirst().drop(while: { $0 == "/" })))
            let relative = normpath(tail.isEmpty ? "." : tail)
            if Scalars.equal(relative, "..") || Scalars.hasPrefix(relative, "../") { return nil }
            return Scalars.equal(relative, ".") ? "~" : "~/" + relative
        }
        guard Scalars.hasPrefix(path, "/") else { return nil }
        var normalised = normpath(path)
        if Scalars.hasPrefix(normalised, "//") {
            normalised = "/" + String(String.UnicodeScalarView(normalised.unicodeScalars.drop(while: { $0 == "/" })))
        }
        return normalised
    }

    /// `(display, forms)`: the normalised absolute form and, under `home`,
    /// its `~` form; nil when unusable.
    static func pathForms(_ path: String, home: String) -> (display: String, forms: [String])? {
        guard let value = normalise(path) else { return nil }
        let absolute: String
        if Scalars.equal(value, "~") {
            absolute = home
        } else if Scalars.hasPrefix(value, "~/") {
            guard let expanded = normalise(home + Scalars.dropFirst(value, 1)) else { return nil }
            absolute = expanded
        } else {
            absolute = value
        }
        var display = absolute
        var base = home
        while Scalars.hasPrefix(String(String.UnicodeScalarView(base.unicodeScalars.reversed())), "/") {
            base = String(String.UnicodeScalarView(base.unicodeScalars.dropLast()))
        }
        if Scalars.equal(absolute, home) {
            display = "~"
        } else if Scalars.hasPrefix(absolute, base + "/") {
            display = "~/" + Scalars.dropFirst(absolute, Scalars.count(base) + 1)
        }
        return (display, Scalars.equal(display, absolute) ? [absolute] : [absolute, display])
    }

    static func pathMatches(_ prefix: String, _ path: String) -> Bool {
        if Scalars.equal(prefix, "/") || Scalars.equal(prefix, "~") {
            let trimmed = Scalars.equal(prefix, "/") ? "" : prefix
            return Scalars.equal(path, prefix) || Scalars.hasPrefix(path, trimmed + "/")
        }
        return Scalars.equal(path, prefix) || Scalars.hasPrefix(path, prefix + "/")
    }

    /// True when no prefixes were asked for, or one of `forms` lies under
    /// one of them.
    static func matchesPrefixes(_ prefixes: [String]?, _ forms: [String]) -> Bool {
        guard let prefixes else { return true }
        for raw in prefixes {
            guard let prefix = normalise(raw) else { continue }
            if forms.contains(where: { pathMatches(prefix, $0) }) { return true }
        }
        return false
    }

    /// os.path.realpath via realpath(3); a path that does not resolve is
    /// returned normalised (Python resolves as far as it can; such a path
    /// never equals a mapped root).
    static func realpathC(_ path: String) -> String {
        guard let resolved = Darwin.realpath(path, nil) else { return normpath(path) }
        defer { free(resolved) }
        return String(cString: resolved)
    }
}

// MARK: - Times

enum ReaderTime {
    /// `datetime.fromisoformat(value.replace("Z", "+00:00"))` for the
    /// extended RFC 3339 forms transcripts use, as integer Unix seconds;
    /// nil without a time zone or outside 0...253402300799.
    static func parse(_ value: String) -> Int64? {
        guard value.utf8.count <= 64 else { return nil }
        let text = value.replacingOccurrences(of: "Z", with: "+00:00")
        // Python 3.12 accepts any single character between date and time.
        let pattern = #"^([0-9]{4})-([0-9]{2})-([0-9]{2}).([0-9]{2}):([0-9]{2})(?::([0-9]{2})(?:[.,]([0-9]{1,9}))?)?([+-])([0-9]{2}):?([0-9]{2})(?::?([0-9]{2}))?\z"#
        guard let regex = try? NSRegularExpression(pattern: pattern),
            let match = regex.firstMatch(in: text, range: NSRange(text.startIndex..., in: text))
        else { return nil }
        func group(_ index: Int) -> Int? {
            let range = match.range(at: index)
            guard range.location != NSNotFound, let swiftRange = Range(range, in: text) else { return nil }
            return Int(text[swiftRange])
        }
        guard let year = group(1), let month = group(2), let day = group(3), let hour = group(4),
            let minute = group(5), let tzHour = group(9), let tzMinute = group(10)
        else { return nil }
        let second = group(6) ?? 0
        let tzSecond = group(11) ?? 0
        guard (1...12).contains(month), hour <= 23, minute <= 59, second <= 59, tzHour <= 23, tzMinute <= 59
        else { return nil }
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        let components = DateComponents(year: year, month: month, day: day, hour: hour, minute: minute, second: second)
        guard let date = calendar.date(from: components),
            calendar.component(.day, from: date) == day  // no 31 February
        else { return nil }
        let sign: Int64 = Range(match.range(at: 8), in: text).map { text[$0] == "-" ? -1 : 1 } ?? 1
        let offset = sign * Int64(tzHour * 3600 + tzMinute * 60 + tzSecond)
        let seconds = Int64(date.timeIntervalSince1970) - offset
        return (0...253_402_300_799).contains(seconds) ? seconds : nil
    }
}

// MARK: - Transcript JSON (Python json.loads semantics)

indirect enum LooseJSON {
    case null
    case bool(Bool)
    /// The number's literal text (Python keeps ints exact, floats as float).
    case number(String)
    case string(String)
    case array([LooseJSON])
    case object([String: LooseJSON])

    subscript(key: String) -> LooseJSON? {
        if case .object(let fields) = self { return fields[key] }
        return nil
    }

    var string: String? {
        if case .string(let value) = self { return value }
        return nil
    }

    var object: [String: LooseJSON]? {
        if case .object(let value) = self { return value }
        return nil
    }

    var array: [LooseJSON]? {
        if case .array(let value) = self { return value }
        return nil
    }

    var isTrue: Bool {
        if case .bool(true) = self { return true }
        return false
    }

    /// Python `isinstance(value, int) and not isinstance(value, bool) and value >= 0`.
    var nonNegativeInt: Int64 {
        guard case .number(let text) = self, LooseJSON.isInteger(text), let value = Int64(text), value >= 0 else {
            return 0
        }
        return value
    }

    static func isInteger(_ text: String) -> Bool {
        !text.contains(where: { $0 == "." || $0 == "e" || $0 == "E" }) && !text.hasPrefix("N")
            && !text.contains("Infinity")
    }

    static func parse(_ data: Data) -> LooseJSON? {
        var parser = LooseParser(bytes: [UInt8](data))
        return try? parser.document()
    }
}

private struct LooseParser {
    let bytes: [UInt8]
    var index = 0
    var depth = 0

    struct Failure: Error {}

    mutating func document() throws -> LooseJSON {
        skip()
        let value = try parseValue()
        skip()
        guard index == bytes.count else { throw Failure() }
        return value
    }

    mutating func skip() {
        while index < bytes.count, [0x20, 0x09, 0x0A, 0x0D].contains(bytes[index]) { index += 1 }
    }

    mutating func literal(_ word: String) -> Bool {
        let expected = Array(word.utf8)
        guard index + expected.count <= bytes.count, Array(bytes[index..<index + expected.count]) == expected else {
            return false
        }
        index += expected.count
        return true
    }

    mutating func parseValue() throws -> LooseJSON {
        guard index < bytes.count else { throw Failure() }
        depth += 1
        defer { depth -= 1 }
        guard depth <= 900 else { throw Failure() }
        switch bytes[index] {
        case UInt8(ascii: "{"):
            index += 1
            var fields: [String: LooseJSON] = [:]
            skip()
            if index < bytes.count, bytes[index] == UInt8(ascii: "}") { index += 1; return .object(fields) }
            while true {
                skip()
                guard index < bytes.count, bytes[index] == UInt8(ascii: "\"") else { throw Failure() }
                let key = try parseString()
                skip()
                guard index < bytes.count, bytes[index] == UInt8(ascii: ":") else { throw Failure() }
                index += 1
                skip()
                fields[key] = try parseValue()  // a duplicate key: the last one wins
                skip()
                guard index < bytes.count else { throw Failure() }
                if bytes[index] == UInt8(ascii: ",") { index += 1; continue }
                if bytes[index] == UInt8(ascii: "}") { index += 1; return .object(fields) }
                throw Failure()
            }
        case UInt8(ascii: "["):
            index += 1
            var items: [LooseJSON] = []
            skip()
            if index < bytes.count, bytes[index] == UInt8(ascii: "]") { index += 1; return .array(items) }
            while true {
                skip()
                items.append(try parseValue())
                skip()
                guard index < bytes.count else { throw Failure() }
                if bytes[index] == UInt8(ascii: ",") { index += 1; continue }
                if bytes[index] == UInt8(ascii: "]") { index += 1; return .array(items) }
                throw Failure()
            }
        case UInt8(ascii: "\""): return .string(try parseString())
        case UInt8(ascii: "t"): if literal("true") { return .bool(true) }; throw Failure()
        case UInt8(ascii: "f"): if literal("false") { return .bool(false) }; throw Failure()
        case UInt8(ascii: "n"): if literal("null") { return .null }; throw Failure()
        case UInt8(ascii: "N"): if literal("NaN") { return .number("NaN") }; throw Failure()
        case UInt8(ascii: "I"): if literal("Infinity") { return .number("Infinity") }; throw Failure()
        default:
            if literal("-Infinity") { return .number("-Infinity") }
            return .number(try parseNumber())
        }
    }

    mutating func parseNumber() throws -> String {
        let start = index
        func digit() -> Bool { index < bytes.count && bytes[index] >= 0x30 && bytes[index] <= 0x39 }
        if index < bytes.count, bytes[index] == UInt8(ascii: "-") { index += 1 }
        guard digit() else { throw Failure() }
        if bytes[index] == UInt8(ascii: "0") { index += 1 } else { while digit() { index += 1 } }
        if index < bytes.count, bytes[index] == UInt8(ascii: ".") {
            index += 1
            guard digit() else { throw Failure() }
            while digit() { index += 1 }
        }
        if index < bytes.count, bytes[index] == UInt8(ascii: "e") || bytes[index] == UInt8(ascii: "E") {
            index += 1
            if index < bytes.count, bytes[index] == UInt8(ascii: "+") || bytes[index] == UInt8(ascii: "-") { index += 1 }
            guard digit() else { throw Failure() }
            while digit() { index += 1 }
        }
        return String(decoding: bytes[start..<index], as: UTF8.self)
    }

    mutating func parseString() throws -> String {
        // The strict parser's string rules (escapes, no raw control
        // characters, valid UTF-8); a lone surrogate fails the line.
        let start = index
        var cursor = index + 1
        while cursor < bytes.count {
            if bytes[cursor] == UInt8(ascii: "\\") { cursor += 2; continue }
            if bytes[cursor] == UInt8(ascii: "\"") { break }
            cursor += 1
        }
        guard cursor < bytes.count else { throw Failure() }
        let slice = Data(bytes[start...cursor])
        guard case .string(let value)? = try? JSON.parse(slice) else { throw Failure() }
        index = cursor + 1
        return value
    }
}

// MARK: - Files

enum ReaderFiles {
    /// The JSON objects of a JSONL file, bounded; malformed or oversized
    /// lines are skipped.
    static func lines(_ path: String) -> [LooseJSON] {
        guard let attributes = try? FileManager.default.attributesOfItem(atPath: path),
            let size = attributes[.size] as? Int, size <= ReaderLimits.maxFileBytes,
            let data = FileManager.default.contents(atPath: path)
        else { return [] }
        var out: [LooseJSON] = []
        var start = data.startIndex
        while start < data.endIndex {
            let end = data[start...].firstIndex(of: 0x0A).map { $0 + 1 } ?? data.endIndex
            let raw = data[start..<end]
            start = end
            guard raw.count <= ReaderLimits.maxLineBytes, let value = LooseJSON.parse(Data(raw)),
                case .object = value
            else { continue }
            out.append(value)
        }
        return out
    }

    private static func isDirectory(_ path: String) -> Bool {
        var directory: ObjCBool = false
        return FileManager.default.fileExists(atPath: path, isDirectory: &directory) && directory.boolValue
    }

    private static func isSymlink(_ path: String) -> Bool {
        (try? FileManager.default.attributesOfItem(atPath: path)[.type] as? FileAttributeType) == .typeSymbolicLink
    }

    private static func isRegularFile(_ path: String) -> Bool {
        (try? FileManager.default.attributesOfItem(atPath: path)[.type] as? FileAttributeType) == .typeRegular
    }

    private static func entries(_ directory: String) -> [String] {
        (try? FileManager.default.contentsOfDirectory(atPath: directory)) ?? []
    }

    /// `root.glob("*/*.jsonl")` (follows a symlinked directory) or
    /// `root.glob("**/*.jsonl")` (does not), as relative component lists.
    private static func glob(root: String, recursive: Bool) -> [[String]] {
        var out: [[String]] = []
        if recursive {
            func walk(_ relative: [String]) {
                let directory = ([root] + relative).joined(separator: "/")
                for name in entries(directory) {
                    let path = directory + "/" + name
                    if name.unicodeScalars.reversed().starts(with: ".jsonl".unicodeScalars.reversed()) {
                        out.append(relative + [name])
                    }
                    if isDirectory(path), !isSymlink(path) { walk(relative + [name]) }
                }
            }
            walk([])
        } else {
            for directory in entries(root) where isDirectory(root + "/" + directory) {
                for name in entries(root + "/" + directory)
                where name.unicodeScalars.reversed().starts(with: ".jsonl".unicodeScalars.reversed()) {
                    out.append([directory, name])
                }
            }
        }
        return out.sorted { lhs, rhs in
            for (a, b) in zip(lhs, rhs) where !Scalars.equal(a, b) {
                return Scalars.less(a, b)
            }
            return lhs.count < rhs.count
        }
    }

    /// The transcript files under `root`: regular files only, never through
    /// a symlinked file, realpath under the root, at most MAX_FILES.
    static func transcripts(root: String, recursive: Bool) -> [String] {
        guard isDirectory(root), !isSymlink(root) else { return [] }
        let realRoot = ReaderPaths.realpathC(root)
        var files: [String] = []
        for components in glob(root: root, recursive: recursive) {
            let path = ([root] + components).joined(separator: "/")
            if isSymlink(path) || !isRegularFile(path) { continue }
            guard Scalars.hasPrefix(ReaderPaths.realpathC(path), realRoot + "/") else { continue }
            files.append(path)
            if files.count >= ReaderLimits.maxFiles { break }
        }
        return files
    }
}

// MARK: - Money

enum ReaderMoney {
    /// `_usd_micros`: integer micro-USD of a cost, rounded half-even; 0 for
    /// anything negative, non-finite or not a number.
    static func micros(_ value: LooseJSON?) -> Int64 {
        let text: String
        switch value {
        case .number(let literal)?:
            if literal.hasSuffix("Infinity") || literal == "NaN" { return 0 }
            if LooseJSON.isInteger(literal) {
                text = literal
            } else {
                // Python: Decimal(str(float)): the shortest repr of the double.
                guard let double = Double(literal), double.isFinite else { return 0 }
                text = "\(double)"
            }
        case .string(let literal)?:
            let pattern = #"^\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\s*$"#
            guard literal.range(of: pattern, options: .regularExpression) != nil else { return 0 }
            text = literal.trimmingCharacters(in: .whitespaces)
        default:
            return 0
        }
        guard let amount = Decimal(string: text, locale: Locale(identifier: "en_US_POSIX")), !amount.isNaN,
            amount >= 0
        else { return 0 }
        var scaled = amount * Decimal(1_000_000)
        var rounded = Decimal()
        NSDecimalRound(&rounded, &scaled, 0, .bankers)
        return NSDecimalNumber(decimal: rounded).int64Value
    }
}
