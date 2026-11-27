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

    /// The ICU sets equal to CPython 3.12's `\\w` and `\\d` (tests pin them).
    public static var pythonWordSet: String { Rules.w }
    public static var pythonDigitSet: String { Rules.d }

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
        guard scalar.properties.numericType == .decimal, let value = scalar.properties.numericValue,
            isPythonDecimal(scalar)
        else { return nil }
        return Int(value)
    }

    private static let decimalSet = try! NSRegularExpression(pattern: "^" + Rules.d + "\\z")

    /// Python's `\d` (CPython 3.12), not ICU's newer Nd.
    static func isPythonDecimal(_ scalar: Unicode.Scalar) -> Bool {
        let text = String(scalar)
        return decimalSet.firstMatch(in: text, range: NSRange(location: 0, length: (text as NSString).length)) != nil
    }
}

// MARK: - The rules (scrubber.py, translated)

enum Rules {
    // CPython 3.12 uses Unicode 15.0; this Mac's ICU is newer and also counts
    // some Apple private-use letters. These are the exact code points ICU
    // puts in `[\p{L}\p{N}_]` / `\p{Nd}` that Python's `\w` / `\d` do not
    // (measured over U+0000..U+10FFFF; nothing is missing the other way).
    // ScrubberUnicodeTests fingerprints both sets against CPython 3.12, so an
    // ICU update that moves them fails a test instead of drifting silently.
    static let wOnlyICU =
        "\\x{88F}\\x{C5C}\\x{CDC}\\x{1C89}-\\x{1C8A}\\x{A7CB}-\\x{A7CF}\\x{A7D2}\\x{A7D4}\\x{A7DA}-\\x{A7DC}\\x{A7F1}\\x{F882}\\x{F89A}-\\x{F89E}\\x{F8A1}-\\x{F8A7}\\x{F8B8}\\x{F8C1}-\\x{F8D6}\\x{105C0}-\\x{105F3}\\x{10940}-\\x{10959}\\x{10D40}-\\x{10D65}\\x{10D6F}-\\x{10D85}\\x{10EC2}-\\x{10EC7}\\x{11380}-\\x{11389}\\x{1138B}\\x{1138E}\\x{11390}-\\x{113B5}\\x{113B7}\\x{113D1}\\x{113D3}\\x{116D0}-\\x{116E3}\\x{11BC0}-\\x{11BE0}\\x{11BF0}-\\x{11BF9}\\x{11DB0}-\\x{11DDB}\\x{11DE0}-\\x{11DE9}\\x{13460}-\\x{143FA}\\x{16100}-\\x{1611D}\\x{16130}-\\x{16139}\\x{16D40}-\\x{16D6C}\\x{16D70}-\\x{16D79}\\x{16EA0}-\\x{16EB8}\\x{16EBB}-\\x{16ED3}\\x{16FF2}-\\x{16FF6}\\x{187F8}-\\x{187FF}\\x{18CFF}\\x{18D09}-\\x{18D1E}\\x{18D80}-\\x{18DF2}\\x{1CCF0}-\\x{1CCF9}\\x{1E5D0}-\\x{1E5ED}\\x{1E5F0}-\\x{1E5FA}\\x{1E6C0}-\\x{1E6DE}\\x{1E6E0}-\\x{1E6E2}\\x{1E6E4}-\\x{1E6E5}\\x{1E6E7}-\\x{1E6ED}\\x{1E6F0}-\\x{1E6F4}\\x{1E6FE}-\\x{1E6FF}\\x{2B73A}-\\x{2B73F}\\x{2CEA2}-\\x{2CEAD}\\x{2EBF0}-\\x{2EE5D}\\x{323B0}-\\x{33479}"
    static let dOnlyICU =
        "\\x{10D40}-\\x{10D49}\\x{116D0}-\\x{116E3}\\x{11BF0}-\\x{11BF9}\\x{11DE0}-\\x{11DE9}\\x{16130}-\\x{16139}\\x{16D70}-\\x{16D79}\\x{1CCF0}-\\x{1CCF9}\\x{1E5F1}-\\x{1E5FA}"
    /// Python `\w`, a set usable alone or nested in another class.
    static let w = "[[\\p{L}\\p{N}_]--[\(wOnlyICU)]]"
    /// Python `\d`.
    static let d = "[[\\p{Nd}]--[\(dOnlyICU)]]"
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
        .init("PII", "iban", b + "[A-Z]{2}\(d){2}(?:[ ]?[A-Z0-9]){11,30}" + b, validate: longestValidIBAN),
        .init("PII", "card", "(?<![\\-\(d)])\(d)(?:[ \\-]?\(d)){12,18}(?!\(d))",
              validate: { luhnIsValid($0) ? $0 : nil }),
        .init("PII", "tckn", "(?<!\(d))[1-9]\(d){10}(?!\(d))", validate: { tcknIsValid($0) ? $0 : nil }),
        .init("PII", "email",
              b + "[\(lettersCI)0-9._%+\\-]{1,64}@[\(lettersCI)0-9\\-]{1,63}(?:\\.[\(lettersCI)0-9\\-]{1,63}){0,8}"
                + "\\.[\(lettersCI)]{2,24}" + b),
        .init("PII", "phone",
              "(?<![+\(w)])(?:(?:\\+|00)90[\(sInner).\\-]?|0)?\\(?[2-5]\(d){2}\\)?"
                + "[\(sInner).\\-]?\(d){3}[\(sInner).\\-]?\(d){2}[\(sInner).\\-]?\(d){2}(?!\(d))"),
        .init("PII", "phone", "(?<![+\(w)])\\+[1-9](?:[\(sInner).\\-]?\(d)){7,14}(?!\(d))"),
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
