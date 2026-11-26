// A port of backend/memory/ingestion/scrubber.py's `scrub_text` with no
// workspace terms (Phase 9 brief D13 item 6, Mac session M2): the node
// scrubs every string it returns, and its output must be byte-identical to
// the server's (backend/nodes/conformance/vectors/scrub.json; recall alone
// is not enough, a different redaction shape would diverge from the
// server's second scrub).
//
// The Python patterns are translated to ICU so that they match exactly what
// Python 3's `re` matches on str patterns:
// - Python `\w` is `[\p{L}\p{N}_]`, `\d` is `\p{Nd}`, and `\b` is the
//   boundary of that `\w` (ICU's own `\w` and `\b` differ), so `\b` is
//   spelled as lookarounds over `[\p{L}\p{N}_]`;
// - Python `\s` is str.isspace(): it adds \x0B, \x1C-\x1F and \x85 to what
//   ICU's `\s` covers, so it is an explicit set;
// - `re.IGNORECASE` folds an ASCII letter to its two cases plus four
//   letters (U+0130 and U+0131 for i, U+017F for s, U+212A for k), and no
//   multi-character folds; ICU's case-insensitive mode does full folding
//   (ß ~ ss), so no ICU flag is used: case-insensitive pieces are explicit
//   classes;
// - Python `\Z` (true end) is ICU `\z`.
// Lengths and cuts count Unicode scalars, as Python's str does; regex
// spans are UTF-16 offsets throughout one pass, so slicing stays consistent.

import Foundation

public enum Scrubber {
    /// The hard cap on one input, before any rule runs (scrubber.py
    /// MAX_SCRUB_INPUT), in Unicode scalars.
    public static let maxInput = 64 * 1024
    static let cutWindow = 1024

    /// Scrub one string (scrubber.py `scrub_text(text).text`, no terms).
    public static func scrub(_ text: String) -> String {
        guard !text.isEmpty else { return "" }
        var value = boundInput(text)
        for rule in Rules.secrets { value = apply(value, rule) }
        value = apply(value, Rules.highEntropy)
        for rule in Rules.pii { value = apply(value, rule) }
        return value
    }

    /// The node's scrub as the readers use it: "" for an empty string.
    public static func scrubIdentifierUnchanged(_ text: String) -> Bool {
        !text.isEmpty && scrub(text) == text
    }

    // MARK: Bounding

    static func boundInput(_ text: String) -> String {
        let scalars = Array(text.unicodeScalars)
        guard scalars.count > maxInput else { return text }
        let head = scalars[0..<maxInput]
        var index = maxInput - 1
        let stop = max(maxInput - cutWindow, 0) - 1
        while index > stop {
            if PythonChars.isSpace(head[index]) { return String(String.UnicodeScalarView(head[0..<index])) }
            index -= 1
        }
        return String(String.UnicodeScalarView(head))
    }

    // MARK: The engine (scrubber.py `_Scrubber._apply`)

    struct Rule {
        let kind: String
        let name: String
        let regex: NSRegularExpression
        /// Capture groups to replace instead of the whole match; with
        /// several, the first that took part in the match.
        let groups: [Int]
        let validate: ((String) -> String?)?

        init(_ kind: String, _ name: String, _ pattern: String, groups: [Int] = [],
             validate: ((String) -> String?)? = nil) {
            self.kind = kind
            self.name = name
            // A pattern here is a constant of this file: failing to compile
            // is a programming error caught by every test.
            self.regex = try! NSRegularExpression(pattern: pattern)
            self.groups = groups
            self.validate = validate
        }
    }

    static func apply(_ text: String, _ rule: Rule) -> String {
        let ns = text as NSString
        let whole = NSRange(location: 0, length: ns.length)
        let spans = Rules.placeholder.matches(in: text, range: whole).map(\.range)
        var out = ""
        var last = 0
        for match in rule.regex.matches(in: text, range: whole) {
            guard let span = span(of: match, groups: rule.groups) else { continue }
            let start = span.location
            var end = span.location + span.length
            if start < last || isProtected(spans, start, end) { continue }
            let value = ns.substring(with: span)
            if Rules.isPlaceholder(value) { continue }
            if let validate = rule.validate {
                guard let trimmed = validate(value) else { continue }
                end = start + (trimmed as NSString).length
            }
            out += ns.substring(with: NSRange(location: last, length: start - last))
            out += "[\(rule.kind):\(rule.name)]"
            last = end
        }
        out += ns.substring(from: last)
        return out
    }

    private static func span(of match: NSTextCheckingResult, groups: [Int]) -> NSRange? {
        if groups.isEmpty { return match.range }
        for group in groups {
            let range = match.range(at: group)
            if range.location != NSNotFound { return range }
        }
        return nil
    }

    /// [start, end) overlaps an existing placeholder.
    private static func isProtected(_ spans: [NSRange], _ start: Int, _ end: Int) -> Bool {
        for span in spans {
            if span.location >= end { return false }
            if start < span.location + span.length { return true }
        }
        return false
    }
}

// MARK: - Python character classes

enum PythonChars {
    /// str.isspace() (measured on CPython 3.12, Unicode 15.0).
    static func isSpace(_ scalar: Unicode.Scalar) -> Bool {
        switch scalar.value {
        case 0x09...0x0D, 0x1C...0x20, 0x85, 0xA0, 0x1680, 0x2000...0x200A, 0x2028, 0x2029, 0x202F, 0x205F,
            0x3000:
            return true
        default:
            return false
        }
    }

    /// The decimal value of a `\d` (Unicode Nd) scalar, as Python's int(c).
    static func digitValue(_ scalar: Unicode.Scalar) -> Int? {
        guard scalar.properties.numericType == .decimal, let value = scalar.properties.numericValue else {
            return nil
        }
        return Int(value)
    }
}

// MARK: - The rules (scrubber.py, translated)

enum Rules {
    /// Python `\w`.
    static let w = "[\\p{L}\\p{N}_]"
    /// Python `\b`.
    static let b = "(?:(?<=\(w))(?!\(w))|(?<!\(w))(?=\(w)))"
    /// Python `\s`, for use inside a character class.
    static let sInner =
        "\\t\\n\\x{0B}\\f\\r\\x{1C}-\\x{20}\\x{85}\\x{A0}\\x{1680}\\x{2000}-\\x{200A}\\x{2028}\\x{2029}\\x{202F}\\x{205F}\\x{3000}"
    static let s = "[\(sInner)]"
    /// `a-z` / `A-Z` under re.IGNORECASE, for use inside a character class.
    static let lettersCI = "a-zA-Z\\x{130}\\x{131}\\x{17F}\\x{212A}"

    /// A literal ASCII word under re.IGNORECASE.
    static func word(_ text: String) -> String {
        text.map { char -> String in
            let lower = char.lowercased()
            guard char.isLetter, char.isASCII else { return NSRegularExpression.escapedPattern(for: String(char)) }
            var set = lower + char.uppercased()
            switch lower {
            case "i": set += "\\x{130}\\x{131}"
            case "s": set += "\\x{17F}"
            case "k": set += "\\x{212A}"
            default: break
            }
            return "[\(set)]"
        }.joined()
    }

    static let placeholder = try! NSRegularExpression(pattern: "\\[(?:SECRET|PII|TERM):[a-z0-9_]+\\]")
    private static let placeholderWhole = try! NSRegularExpression(
        pattern: "^(?:\\[(?:SECRET|PII|TERM):[a-z0-9_]+\\])\\z")

    static func isPlaceholder(_ value: String) -> Bool {
        placeholderWhole.firstMatch(in: value, range: NSRange(location: 0, length: (value as NSString).length)) != nil
    }

    static let secretWords: String = {
        let words = [
            word("secret"), word("token"), word("password"), word("passwd"), word("pwd"),
            word("api") + "[_-]?" + word("key"), word("private") + "[_-]?" + word("key"),
            word("access") + "[_-]?" + word("key"), word("credential"),
        ]
        return "(?:" + words.joined(separator: "|") + ")"
    }()

    static let secrets: [Scrubber.Rule] = {
        let keyChars = "[\(lettersCI)0-9_.\\-]"
        return [
            .init("SECRET", "pem_private_key",
                  "-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----"
                    + "(?:[\\s\\S]*?-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----|[\\s\\S]*\\z)"),
            .init("SECRET", "pem_block",
                  "-----BEGIN [A-Z0-9 ]{1,64}-----(?:(?!-----BEGIN )[\\s\\S]){0,16384}?"
                    + "-----END [A-Z0-9 ]{1,64}-----"),
            .init("SECRET", "anthropic_key", b + "sk-ant-[A-Za-z0-9_\\-]{16,}"),
            .init("SECRET", "openai_key", b + "sk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\\-]{20,}"),
            .init("SECRET", "github_token", b + "gh[pousr]_[A-Za-z0-9]{20,}"),
            .init("SECRET", "github_pat", b + "github_pat_[A-Za-z0-9_]{20,}"),
            .init("SECRET", "slack_token", b + "xox[baprs]-[A-Za-z0-9\\-]{10,}"),
            .init("SECRET", "aws_key", b + "(?:AKIA|ASIA|AGPA|AIDA|AROA)[0-9A-Z]{16}" + b),
            .init("SECRET", "google_api_key", b + "AIza[0-9A-Za-z_\\-]{30,}"),
            .init("SECRET", "stripe_key", b + "(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}"),
            .init("SECRET", "jwt",
                  b + "eyJ[A-Za-z0-9_\\-]{8,}\\.eyJ[A-Za-z0-9_\\-]{8,}\\.[A-Za-z0-9_\\-]{8,}"),
            .init("SECRET", "url_credentials",
                  b + "[\(lettersCI)][\(lettersCI)0-9+.\\-]{0,31}://"
                    + "([^\(sInner)/:@\\[\\]]+:[^\(sInner)/@\\[\\]]+)@",
                  groups: [1]),
            .init("SECRET", "bearer_token",
                  b + "(?:" + word("bearer") + "|" + word("basic") + "|" + word("token") + ")"
                    + s + "+([\(lettersCI)0-9._~+/\\-]{16,}=*)",
                  groups: [1]),
            .init("SECRET", "assignment",
                  "(?<!\(keyChars))\(keyChars){0,128}" + secretWords + "\(keyChars){0,64}"
                    + "[\"']?[ \\t]{0,16}(?:=|:)[ \\t]{0,16}"
                    + "(?:\"([^\"\\n]{1,1024})\"|'([^'\\n]{1,1024})'|[\"']?([^\(sInner)\"',;]+))",
                  groups: [1, 2, 3]),
        ]
    }()

    static let highEntropy = Scrubber.Rule(
        "SECRET", "high_entropy", "[A-Za-z0-9+/_\\-]{32,}={0,2}",
        validate: { looksLikeSecretRun($0) ? $0 : nil })

    static let pii: [Scrubber.Rule] = [
        .init("PII", "iban", b + "[A-Z]{2}\\p{Nd}{2}(?:[ ]?[A-Z0-9]){11,30}" + b, validate: longestValidIBAN),
        .init("PII", "card", "(?<![\\p{Nd}\\-])\\p{Nd}(?:[ \\-]?\\p{Nd}){12,18}(?![\\p{Nd}])",
              validate: { luhnIsValid($0) ? $0 : nil }),
        .init("PII", "tckn", "(?<!\\p{Nd})[1-9]\\p{Nd}{10}(?!\\p{Nd})", validate: { tcknIsValid($0) ? $0 : nil }),
        .init("PII", "email",
              b + "[\(lettersCI)0-9._%+\\-]{1,64}@[\(lettersCI)0-9\\-]{1,63}(?:\\.[\(lettersCI)0-9\\-]{1,63}){0,8}"
                + "\\.[\(lettersCI)]{2,24}" + b),
        .init("PII", "phone",
              "(?<![\\p{L}\\p{N}_+])(?:(?:\\+|00)90[\(sInner).\\-]?|0)?\\(?[2-5]\\p{Nd}{2}\\)?"
                + "[\(sInner).\\-]?\\p{Nd}{3}[\(sInner).\\-]?\\p{Nd}{2}[\(sInner).\\-]?\\p{Nd}{2}(?!\\p{Nd})"),
        .init("PII", "phone", "(?<![\\p{L}\\p{N}_+])\\+[1-9](?:[\(sInner).\\-]?\\p{Nd}){7,14}(?!\\p{Nd})"),
    ]

    // MARK: Validators (scrubber.py)

    static func shannonEntropy(_ value: String) -> Double {
        let scalars = Array(value.unicodeScalars)
        guard !scalars.isEmpty else { return 0 }
        // Counter order: first occurrence (the float sum's order matters at
        // the thresholds).
        var order: [Unicode.Scalar] = []
        var counts: [Unicode.Scalar: Int] = [:]
        for scalar in scalars {
            if counts[scalar] == nil { order.append(scalar) }
            counts[scalar, default: 0] += 1
        }
        let length = Double(scalars.count)
        var total = 0.0
        for scalar in order {
            let p = Double(counts[scalar]!) / length
            total += p * log2(p)
        }
        return -total
    }

    static func looksLikeSecretRun(_ run: String) -> Bool {
        let chars = Array(run.unicodeScalars)
        let isHex = !chars.isEmpty && chars.allSatisfy { ("0"..."9").contains($0) || ("a"..."f").contains($0) || ("A"..."F").contains($0) }
        if isHex {
            if [40, 64].contains(chars.count) && run == run.lowercased() { return false }
            return shannonEntropy(run) >= 3.0
        }
        let upper = chars.filter { ("A"..."Z").contains($0) }.count
        let lower = chars.filter { ("a"..."z").contains($0) }.count
        let digits = chars.filter { ("0"..."9").contains($0) }.count
        if digits < 2 || upper < 2 || lower < 2 { return false }
        return shannonEntropy(run) >= 4.0
    }

    static func ibanIsValid(_ candidate: String) -> Bool {
        let compact = Array(candidate.replacingOccurrences(of: " ", with: "").uppercased().unicodeScalars)
        guard (15...34).contains(compact.count) else { return false }
        func isLetter(_ s: Unicode.Scalar) -> Bool { ("A"..."Z").contains(s) }
        guard compact.count >= 5, isLetter(compact[0]), isLetter(compact[1]),
            PythonChars.digitValue(compact[2]) != nil, PythonChars.digitValue(compact[3]) != nil,
            compact[4...].allSatisfy({ isLetter($0) || ("0"..."9").contains($0) })
        else { return false }
        let rearranged = Array(compact[4...]) + Array(compact[0..<4])
        var remainder = 0
        for scalar in rearranged {
            let value = PythonChars.digitValue(scalar) ?? Int(scalar.value) - 55
            for digit in String(value) { remainder = (remainder * 10 + Int(String(digit))!) % 97 }
        }
        return remainder == 1
    }

    static func longestValidIBAN(_ value: String) -> String? {
        var candidate = value
        while candidate.replacingOccurrences(of: " ", with: "").unicodeScalars.count >= 15 {
            if ibanIsValid(candidate) { return candidate }
            if let space = candidate.range(of: " ", options: .backwards) {
                candidate = String(candidate[..<space.lowerBound])
            } else {
                var scalars = candidate.unicodeScalars
                scalars.removeLast()
                candidate = String(scalars)
            }
        }
        return nil
    }

    static func luhnIsValid(_ candidate: String) -> Bool {
        let digits = candidate.unicodeScalars.compactMap(PythonChars.digitValue)
        guard (13...19).contains(digits.count), Set(digits).count != 1 else { return false }
        var total = 0
        for (index, digit) in digits.reversed().enumerated() {
            var value = digit
            if index % 2 == 1 {
                value *= 2
                if value > 9 { value -= 9 }
            }
            total += value
        }
        return total % 10 == 0
    }

    static func tcknIsValid(_ candidate: String) -> Bool {
        let scalars = Array(candidate.unicodeScalars)
        guard scalars.count == 11, ("1"..."9").contains(scalars[0]) else { return false }
        let d = scalars.compactMap(PythonChars.digitValue)
        guard d.count == 11 else { return false }
        let tenth = (((d[0] + d[2] + d[4] + d[6] + d[8]) * 7 - (d[1] + d[3] + d[5] + d[7])) % 10 + 10) % 10
        guard tenth == d[9] else { return false }
        return d[0..<10].reduce(0, +) % 10 == d[10]
    }
}
