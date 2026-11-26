// The two owner-turn readers (Phase 8B brief M1, D13, D17; runbook B4):
// - over the reference fixtures (backend/nodes/reference/fixtures/
//   owner_turns/, a transcript root laid out as a node home), output equal
//   to backend/nodes/conformance/vectors/owner_turns.json;
// - a synthetic home mirrored byte for byte from a Python generator, whose
//   reference output (readers.Readers on CPython 3.12) is pinned here: the
//   60 KiB budget, the 4 000-scalar cut, tied timestamps, sub-millisecond
//   times, Codex turn ids over byte offsets (multi-byte, malformed and
//   oversized lines counted);
// - the reference's time parser and formatter, its path normalisation and
//   the canonical-size cut (`_fit_owner_turns`), with measured values;
// - rule 2 (a row outside the prefixes is skipped before its turns are
//   parsed: a time that makes the reference raise is never reached);
// - the grants: `not_granted` until the directory grant, a revoke is
//   `not_granted` at once plus `grant.availability_changed`.
// No @testable import: NodeCoreTests also runs in Release (ReaderProbes.swift).

import CryptoKit
import Foundation
import NodeCore
import XCTest

final class OwnerTurnReaderTests: XCTestCase {
    static let claude = "claude.transcripts.owner_turns"
    static let codex = "codex.transcripts.owner_turns"
    /// The conformance clock (nodes.conformance.cases.NOW).
    static let caseNow: Int64 = 1_796_000_005

    // MARK: Homes

    static func temporaryHome() throws -> URL {
        let base = FileManager.default.temporaryDirectory.appendingPathComponent("friday-owner-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
        return URL(fileURLWithPath: FixtureHome.realpath(base.path))
    }

    static func write(_ text: String, to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data(text.utf8).write(to: url)
    }

    /// The owner-turn fixture root (`fixtures/owner_turns/{claude,codex}`)
    /// as a node home: `claude/projects` -> `.claude/projects`,
    /// `codex/sessions` -> `.codex/sessions`, bytes unchanged (the files
    /// carry no template markers).
    static func ownerFixtureHome() throws -> String {
        let home = try temporaryHome()
        let root = FixtureHome.fixtures.appendingPathComponent("owner_turns")
        for (source, target) in [("claude/projects", ".claude/projects"), ("codex/sessions", ".codex/sessions")] {
            try FileManager.default.createDirectory(
                at: home.appendingPathComponent(target).deletingLastPathComponent(), withIntermediateDirectories: true)
            try FileManager.default.copyItem(
                at: root.appendingPathComponent(source), to: home.appendingPathComponent(target))
        }
        return home.path
    }

    static func readers(_ home: String, claude: Bool = true, codex: Bool = true) -> NodeReaders {
        NodeReaders(
            home: home, repos: nil, grants: ReaderGrants(claude: claude, codex: codex), windowTitles: false,
            desktop: SystemDesktop(), usage: .unsupported)
    }

    static func args(since: String, prefixes: [String], sessions: Int64 = 50, turns: Int64 = 200) -> JSONValue {
        [
            "since": .string(since), "cwd_prefixes": .array(prefixes.map(JSONValue.string)),
            "max_sessions": .int(sessions), "max_turns": .int(turns),
        ]
    }

    static func output(_ outcome: ReadOutcome, file: StaticString = #filePath, line: UInt = #line) -> JSONValue? {
        guard outcome.ok, let output = outcome.output else {
            XCTFail("the read failed", file: file, line: line)
            return nil
        }
        return output
    }

    static func turns(_ output: JSONValue) -> [JSONValue] {
        (output["sessions"]?.arrayValue ?? []).flatMap { $0["turns"]?.arrayValue ?? [] }
    }

    // MARK: The reference fixtures and their vector

    func testTheReadersReproduceTheOwnerTurnVector() throws {
        let vector = try Repo.json("backend/nodes/conformance/vectors/owner_turns.json")
        let now = try XCTUnwrap(OwnerTurnProbe.parseInstant(try XCTUnwrap(vector["now"]?.stringValue)).micros)
        XCTAssertEqual(now, 1_795_219_200 * 1_000_000)
        let home = try Self.ownerFixtureHome()
        let readers = Self.readers(home)
        for capability in [Self.claude, Self.codex] {
            let outcome = readers.read(capability: capability, args: try XCTUnwrap(vector["args"]), now: now / 1_000_000)
            let expected = try XCTUnwrap(vector["results"]?[capability])
            XCTAssertEqual(outcome, .success(expected, truncated: false), capability)
            XCTAssertEqual(
                try JSON.canonical(try XCTUnwrap(Self.output(outcome))), try JSON.canonical(expected), capability)
        }
    }

    func testTheCapsTruncateLikeTheReference() throws {
        // max_turns 2 / max_sessions 1 over the vector's fixtures: the
        // first turn a cap cannot take ends the result.
        let home = try Self.ownerFixtureHome()
        let readers = Self.readers(home)
        let prefixes = ["/Users/owner/code", "/Users/owner/work/acme"]
        let byTurns = try XCTUnwrap(Self.output(readers.read(
            capability: Self.claude, args: Self.args(since: "2026-11-20T00:00:00.000Z", prefixes: prefixes, turns: 2),
            now: 1_795_219_200)))
        XCTAssertEqual(Self.turns(byTurns).compactMap { $0["turn_id"]?.stringValue }, ["u-001", "u-014"])
        XCTAssertEqual(byTurns["next_since"], "2026-11-20T09:04:00.000Z")
        XCTAssertEqual(byTurns["truncated"], true)
        let bySessions = try XCTUnwrap(Self.output(readers.read(
            capability: Self.codex,
            args: Self.args(since: "2026-11-20T00:00:00.000Z", prefixes: prefixes, sessions: 1), now: 1_795_219_200)))
        XCTAssertEqual(bySessions["sessions"]?.arrayValue?.count, 1)
        XCTAssertEqual(bySessions["next_since"], "2026-11-20T09:24:00.000Z")
        XCTAssertEqual(bySessions["truncated"], true)
    }

    func testArgsOutsideTheContractFailTheRead() throws {
        let home = try Self.ownerFixtureHome()
        let readers = Self.readers(home)
        let now: Int64 = 1_795_219_200  // 2026-11-21T00:00:00Z
        let good = Self.args(since: "2026-11-20T00:00:00.000Z", prefixes: ["/Users/owner"])
        guard readers.read(capability: Self.claude, args: good, now: now).ok else {
            return XCTFail("the good args must read")
        }
        var bad: [JSONValue] = []
        for since in ["2026-11-13T23:59:59.999Z", "2026-11-21T00:05:00.001Z", "2026-11-20", "2026-02-30T00:00:00Z"] {
            bad.append(Self.args(since: since, prefixes: ["/Users/owner"]))
        }
        bad.append(Self.args(since: "2026-11-20T00:00:00.000Z", prefixes: []))
        bad.append(Self.args(since: "2026-11-20T00:00:00.000Z", prefixes: ["relative"]))
        bad.append(Self.args(since: "2026-11-20T00:00:00.000Z", prefixes: ["/a", "/a"]))
        bad.append(Self.args(since: "2026-11-20T00:00:00.000Z", prefixes: Array(repeating: "/a", count: 21)))
        bad.append(Self.args(since: "2026-11-20T00:00:00.000Z", prefixes: ["/a"], sessions: 51))
        bad.append(Self.args(since: "2026-11-20T00:00:00.000Z", prefixes: ["/a"], turns: 0))
        var extra = good.objectValue!
        extra["limit"] = 5
        bad.append(.object(extra))
        var boolean = good.objectValue!
        boolean["max_turns"] = true
        bad.append(.object(boolean))
        for args in bad {
            XCTAssertEqual(readers.read(capability: Self.claude, args: args, now: now), .failure, "\(args)")
        }
        // Exactly 7 days old and 5 minutes ahead are inside the bounds.
        for since in ["2026-11-14T00:00:00.000Z", "2026-11-21T00:05:00.000Z"] {
            guard readers.read(
                capability: Self.codex, args: Self.args(since: since, prefixes: ["/Users/owner"]), now: now).ok
            else { return XCTFail(since) }
        }
    }

    // MARK: A synthetic home (generator mirrored from Python)

    static func syntheticClaude() -> String {
        var out = ""
        for i in 0..<40 {
            let (session, cwd) = i % 2 == 0 ? ("cc-a", "~/work/a") : ("cc-b", "~/work/b")
            var text = "Turn \(i): " + String(repeating: "ü", count: 1500) + " " + String(repeating: "字", count: 900)
            if i == 3 { text = String(repeating: "ab ", count: 2000) }
            var uuid = String(format: "u-%03d", i)
            var stamp = String(format: "2026-11-29T10:%02d:00.%06dZ", i, i * 1111)
            if i == 4 { uuid = "u-z" }
            if i == 5 { (uuid, stamp) = ("u-a", "2026-11-29T10:04:00.004444Z") }
            out += "{\"type\":\"user\",\"sessionId\":\"\(session)\",\"uuid\":\"\(uuid)\",\"cwd\":\"\(cwd)\","
                + "\"timestamp\":\"\(stamp)\",\"message\":{\"role\":\"user\",\"content\":\"\(text)\"}}\n"
            out += "{\"type\":\"assistant\",\"sessionId\":\"\(session)\",\"uuid\":\"" + String(format: "a-%03d", i)
                + "\",\"cwd\":\"\(cwd)\",\"timestamp\":\"\(stamp)\",\"message\":{\"role\":\"assistant\","
                + "\"content\":[{\"type\":\"text\",\"text\":\"ASSISTANT \(i)\"}]}}\n"
        }
        return out
    }

    static func syntheticCodex() -> String {
        var out = "{\"timestamp\":\"2026-11-29T09:00:00.000Z\",\"type\":\"session_meta\","
            + "\"payload\":{\"id\":\"cx-1\",\"cwd\":\"~/work/a\"}}\n"
        for i in 0..<20 {
            if i % 5 == 0 { out += "not json ü字\n" }
            if i == 7 { out += "{\"x\":\"" + String(repeating: "a", count: 1_048_576) + "\"}\n" }
            out += String(format: "{\"timestamp\":\"2026-11-29T09:%02d:00.000Z\",", i + 1)
                + "\"type\":\"response_item\",\"payload\":{\"type\":\"message\",\"role\":\"user\","
                + "\"content\":[{\"type\":\"input_text\",\"text\":\"Codex ü字 turn \(i)\"}]}}\n"
        }
        return out
    }

    static func sha256(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }

    func testASyntheticHomeMatchesTheReferenceByteForByte() throws {
        let claudeText = Self.syntheticClaude()
        let codexText = Self.syntheticCodex()
        // The generator is the Python one, byte for byte.
        XCTAssertEqual(
            Self.sha256(Data(claudeText.utf8)), "85f4956ae6d9973809e2d21c527a7809d1c0ac0675b0e123d682426b2d9c808f")
        XCTAssertEqual(
            Self.sha256(Data(codexText.utf8)), "eca32fa7ac5d4d9058f60b5b5f69ec9a55890a515121dd877b1a6e4db51eb6e4")
        let home = try Self.temporaryHome()
        try Self.write(claudeText, to: home.appendingPathComponent(".claude/projects/-work/cc-synth.jsonl"))
        try Self.write(codexText, to: home.appendingPathComponent(".codex/sessions/2026/11/29/rollout-synth.jsonl"))
        let readers = Self.readers(home.path)
        let args = Self.args(since: "2026-11-29T00:00:00.000Z", prefixes: ["~/work"])

        // Claude: the 60 KiB budget ends the result after ten turns.
        let claudeOutcome = readers.read(capability: Self.claude, args: args, now: Self.caseNow)
        guard claudeOutcome.ok, let claude = claudeOutcome.output else { return XCTFail("claude failed") }
        XCTAssertTrue(claudeOutcome.truncated)
        XCTAssertEqual(claude["truncated"], true)
        XCTAssertEqual(claude["next_since"], "2026-11-29T10:09:00.009Z")
        let claudeSessions = claude["sessions"]?.arrayValue ?? []
        XCTAssertEqual(claudeSessions.compactMap { $0["session_id"]?.stringValue }, ["cc-a", "cc-b"])
        XCTAssertEqual(claudeSessions.compactMap { $0["cwd"]?.stringValue }, ["~/work/a", "~/work/b"])
        XCTAssertEqual(claudeSessions.map { $0["turns"]?.arrayValue?.count ?? 0 }, [5, 5])
        let claudeTurns = Self.turns(claude)
        let claudeIDs: [String] = claudeTurns.compactMap { $0["turn_id"]?.stringValue }
        XCTAssertEqual(claudeIDs, ["u-000", "u-002", "u-z", "u-006", "u-008", "u-001", "u-003", "u-a", "u-007", "u-009"])
        let claudeAts: [String] = claudeTurns.compactMap { $0["at"]?.stringValue }
        XCTAssertEqual(
            claudeAts,
            [
                "2026-11-29T10:00:00.000Z", "2026-11-29T10:02:00.002Z", "2026-11-29T10:04:00.004Z",
                "2026-11-29T10:06:00.006Z", "2026-11-29T10:08:00.008Z", "2026-11-29T10:01:00.001Z",
                "2026-11-29T10:03:00.003Z", "2026-11-29T10:04:00.004Z", "2026-11-29T10:07:00.007Z",
                "2026-11-29T10:09:00.009Z",
            ])
        let claudeLengths: [Int] = claudeTurns.map { $0["text"]?.stringValue?.unicodeScalars.count ?? 0 }
        XCTAssertEqual(
            claudeLengths,
            [2409, 2409, 2409, 2409, 2409, 2409, 4000, 2409, 2409, 2409])
        let claudeCanonical = try JSON.canonical(claude)
        XCTAssertEqual(claudeCanonical.count, 56166)
        XCTAssertEqual(
            Self.sha256(claudeCanonical), "f4b508768dea3b64e9b77976086842b7fe6acaddf8d76b57ed93c62c4dd85e63")

        // Codex: ids over byte offsets (multi-byte, malformed and oversized
        // lines all counted).
        let codexOutcome = readers.read(capability: Self.codex, args: args, now: Self.caseNow)
        guard codexOutcome.ok, let codex = codexOutcome.output else { return XCTFail("codex failed") }
        XCTAssertFalse(codexOutcome.truncated)
        XCTAssertEqual(codex["next_since"], "2026-11-29T09:20:00.000Z")
        let codexIDs: [String] = Self.turns(codex).compactMap { $0["turn_id"]?.stringValue }
        XCTAssertEqual(
            codexIDs,
            [
                "8aa21ff1178b5f318812577f1a038be3", "d2f481660a7444dd603a17c3adb7c7f8",
                "4a09ebe7f874ca26741f52a082d4dd34", "10895151df8f087138b8cf45a18125d4",
                "fc374f425070a1fad4013ac3cdf31f65", "31cd771e82b83a794af5106f979e65b7",
                "3938dd64a59a1415a07d43707de69069", "74d117165f423ac2d72be0869578a077",
                "ae89fb788ed14a62e441b2654e7e92df", "726a5068d75e665b39dff9fdf2f61714",
                "b3bd8c8d811207e426a292019b70cb70", "503f207d06c62fc9a3ffc29efaf4e1f4",
                "5737e4568cc9ea5550b918375ed337a6", "e31792f85355b03cd1c591c7f5d2874f",
                "6a2ecaaecd1cb7150dbbaeb010d7b8ab", "0570f7f17e7cf868492d10ac0bb18ec2",
                "cd345771f308cf76ed21a6bae31a54b7", "0cfd7f0c0d9f33c75b6392d56e9a21c0",
                "262564505b23cbb3c7d03b50a1419e6e", "9a3b74ac7a59de205679691e151d4be7",
            ])
        let codexCanonical = try JSON.canonical(codex)
        XCTAssertEqual(codexCanonical.count, 2271)
        XCTAssertEqual(Self.sha256(codexCanonical), "738776f429bacb3ff4d168f2aef6098073dd496b9f8dd4e4d34a7d54c663f1e5")
    }

    func testTheCodexTurnIDIsTheReferenceHash() {
        // hashlib.sha256("codex/sessions/2026/11/20/x.jsonl:0".encode()).hexdigest()[:32]
        XCTAssertEqual(
            OwnerTurnProbe.codexTurnID(relative: "codex/sessions/2026/11/20/x.jsonl", offset: 0),
            "63a072f66ad78bf0319c72089d4bfdc6")
    }

    // MARK: Rule 2: outside the prefixes, the turns are never parsed

    func testARowOutsideThePrefixesIsSkippedBeforeItsTurnsAreParsed() throws {
        // A time the reference's datetime cannot hold makes its read raise
        // (OverflowError); outside the prefixes the reference never gets
        // there (measured on CPython 3.12: raises inside, ok outside).
        for inside in [true, false] {
            let home = try Self.temporaryHome()
            let cwd = inside ? "~/work/a" : "/elsewhere"
            try Self.write(
                "{\"type\":\"session_meta\",\"payload\":{\"id\":\"cx-1\",\"cwd\":\"\(cwd)\"}}\n"
                    + "{\"type\":\"event_msg\",\"timestamp\":\"9999-12-31T23:59:59-01:00\",\"payload\":{}}\n",
                to: home.appendingPathComponent(".codex/sessions/x/r.jsonl"))
            try Self.write(
                "{\"type\":\"user\",\"sessionId\":\"cc-1\",\"uuid\":\"u-1\",\"cwd\":\"\(cwd)\","
                    + "\"timestamp\":\"0001-01-01T00:30:00+01:00\",\"message\":{\"content\":\"hi\"}}\n",
                to: home.appendingPathComponent(".claude/projects/p/s.jsonl"))
            let readers = Self.readers(home.path)
            let args = Self.args(since: "2026-11-29T00:00:00.000Z", prefixes: ["~/work"])
            for capability in [Self.claude, Self.codex] {
                let outcome = readers.read(capability: capability, args: args, now: Self.caseNow)
                if inside {
                    XCTAssertEqual(outcome, .failure, capability)
                } else {
                    XCTAssertEqual(
                        outcome,
                        .success(
                            ["sessions": [], "next_since": "2026-11-29T00:00:00.000Z", "truncated": false],
                            truncated: false),
                        capability)
                }
            }
        }
    }

    // MARK: The reference's time and path helpers

    func testInstantsParseAndFormatLikeTheReference() {
        // owner_turns.parse_instant / format_instant on CPython 3.12.
        let cases: [(String, String?)] = [
            ("2026-11-20T10:00:00.5Z", "2026-11-20T10:00:00.500Z"),
            ("2026-11-20T24:00:00Z", nil),
            ("2026-11-20T23:59:60Z", nil),
            ("2026-11-20T10:00:00+24:00", nil),
            ("2026-11-20T10:00:00+23:59", "2026-11-19T10:01:00.000Z"),
            ("2026-11-20T10:00:00+05:60", "2026-11-20T04:00:00.000Z"),
            ("2026-11-20T10:00:00+05:99", "2026-11-20T03:21:00.000Z"),
            ("2026-11-20T10:00:00+23:60", nil),
            ("2026-11-20T10:00:00-23:59", "2026-11-21T09:59:00.000Z"),
            ("2026-02-29T10:00:00Z", nil),
            ("2024-02-29T10:00:00Z", "2024-02-29T10:00:00.000Z"),
            ("0000-01-01T00:00:00Z", nil),
            ("2026-11-20T10:00:00.123456-00:00", "2026-11-20T10:00:00.123Z"),
            ("2026-11-20T10:00:00.1234Z", "2026-11-20T10:00:00.123Z"),
            ("2026-11-20T10:00:00.12345+01:00", "2026-11-20T09:00:00.123Z"),
            ("2026-11-20T10:00:00.000001Z", "2026-11-20T10:00:00.000Z"),
            ("2026-13-01T00:00:00Z", nil),
            ("2026-11-20T10:00:00.1234567Z", nil),
            ("2026-11-20 10:00:00Z", nil),
            ("2026-11-20T10:00:00", nil),
            ("２026-11-20T10:00:00Z", nil),
            ("0001-01-01T01:00:00+01:00", "0001-01-01T00:00:00.000Z"),
        ]
        for (value, expected) in cases {
            let parsed = OwnerTurnProbe.parseInstant(value)
            XCTAssertFalse(parsed.overflow, value)
            XCTAssertEqual(parsed.micros.map(OwnerTurnProbe.formatInstant), expected, value)
        }
        XCTAssertEqual(OwnerTurnProbe.parseInstant("2026-11-20T10:00:00.1234Z").micros.map { $0 % 1_000_000 }, 123_400)
        // OverflowError in the reference.
        XCTAssertTrue(OwnerTurnProbe.parseInstant("9999-12-31T23:59:59-01:00").overflow)
        XCTAssertTrue(OwnerTurnProbe.parseInstant("0001-01-01T00:30:00+01:00").overflow)
    }

    func testPathsNormaliseLikeTheReference() {
        // owner_turns.normalise_path on CPython 3.12.
        let cases: [(String, String?)] = [
            ("  /a/b/  ", "/a/b"), ("\u{3000}/x\u{85}", "/x"), ("~", "~"), ("~/", "~"), ("~/../x", nil),
            ("//a//b/./c/..", "/a/b"), ("\u{200B}/x", nil), ("/" + String(repeating: "a", count: 499), "/" + String(repeating: "a", count: 499)),
            ("/" + String(repeating: "a", count: 500), nil), ("relative", nil), ("~user/x", nil), ("/a\u{0}b", nil),
        ]
        for (value, expected) in cases {
            XCTAssertEqual(OwnerTurnProbe.normalisePath(value), expected, value.debugDescription)
        }
    }

    // MARK: The canonical-size cut (readers.py `_fit_owner_turns`)

    static func interleaved() -> JSONValue {
        func turn(_ id: String, _ at: String) -> JSONValue {
            ["turn_id": .string(id), "at": .string(at), "text": .string(String(repeating: "x", count: 40))]
        }
        return [
            "sessions": [
                ["session_id": "cc-a", "cwd": "~/work/acme", "turns": [
                    turn("a-1", "2026-11-29T09:00:00.000Z"), turn("a-2", "2026-11-29T09:02:00.000Z"),
                ]],
                ["session_id": "cc-b", "cwd": "~/work/acme", "turns": [
                    turn("b-1", "2026-11-29T09:01:00.000Z"), turn("b-2", "2026-11-29T09:03:00.000Z"),
                ]],
            ],
            "next_since": "2026-11-29T09:03:00.000Z",
            "truncated": false,
        ]
    }

    func testTheCanonicalCutDropsTheNewestTurnsAndRecomputesTheCursor() throws {
        // Measured with readers._fit_owner_turns: (limit, canonical size,
        // next_since, turn ids per session).
        let cases: [(Int, Int, String, [[String]])] = [
            (150, 72, "2026-11-29T09:00:00.000Z", []),
            (200, 72, "2026-11-29T09:00:00.000Z", []),
            (300, 223, "2026-11-29T09:00:00.000Z", [["a-1"]]),
            (400, 375, "2026-11-29T09:01:00.000Z", [["a-1"], ["b-1"]]),
            (500, 475, "2026-11-29T09:02:00.000Z", [["a-1", "a-2"], ["b-1"]]),
        ]
        for (limit, size, nextSince, ids) in cases {
            let fitted = try OwnerTurnProbe.fit(Self.interleaved(), limit: limit)
            XCTAssertEqual(try JSON.canonical(fitted).count, size, "\(limit)")
            XCTAssertEqual(fitted["next_since"]?.stringValue, nextSince, "\(limit)")
            XCTAssertEqual(fitted["truncated"], true)
            XCTAssertEqual(
                fitted["sessions"]?.arrayValue?.map { ($0["turns"]?.arrayValue ?? []).compactMap { $0["turn_id"]?.stringValue } },
                ids, "\(limit)")
        }
    }

    // MARK: The debug-only preview (friday-node scrub-preview)

    #if DEBUG
    func testThePreviewCountsLineKindsAndMarkersAndNeverText() throws {
        let root = FixtureHome.fixtures.appendingPathComponent("owner_turns")
        let claude = Dictionary(uniqueKeysWithValues: OwnerTurnPreview.claude(
            directory: root.appendingPathComponent("claude/projects/-Users-owner-code-friday").path))
        XCTAssertEqual(claude["files"], 1)
        XCTAssertEqual(claude["lines"], 41)
        XCTAssertEqual(claude["accepted"], 4)  // the vector's four cc-sess-1 turns
        XCTAssertEqual(claude["kind.user"], 37)
        XCTAssertEqual(claude["kind.(other)"], nil)
        XCTAssertEqual(claude["accepted.starts_with_a_tag"], nil)
        for marker in OwnerTurnPreview.claudeMarkers {
            // The fixture's indented `<task-notification>` line counts twice.
            XCTAssertEqual(claude["user.excluded.marker.\(marker)"], marker == "<task-notification>" ? 2 : 1, marker)
        }
        XCTAssertEqual(claude["user.excluded.non_text_content.block.tool_result"], 2)
        let codex = Dictionary(uniqueKeysWithValues: OwnerTurnPreview.codex(
            directory: root.appendingPathComponent("codex/sessions").path))
        XCTAssertEqual(codex["files"], 3)
        XCTAssertEqual(codex["accepted"], 4)  // the vector's three plus /tmp/scratch's one
        for marker in OwnerTurnPreview.codexMarkers {
            XCTAssertEqual(codex["user.excluded.marker.\(marker)"], 1, marker)
        }
        // Counts only: no fixture text, identifier or file name in any key.
        let keys = (Array(claude.keys) + Array(codex.keys)).joined(separator: "\n")
        for phrase in [
            "PHRASE", "hetzner", "billing", "triage", "Acme", "cc-sess", "cx-thread", "u-0", "rollout", "/Users",
            "SECRET", "token", "reconciliation",
        ] {
            XCTAssertFalse(keys.contains(phrase), phrase)
        }
    }

    func testThePreviewEchoesOnlyFixedWords() {
        // A leading tag is named only when it is a known harness tag.
        XCTAssertEqual(OwnerTurnPreview.tagName(" <teammate-message teammate_id=\"x\">"), "<teammate-message>")
        XCTAssertEqual(OwnerTurnPreview.tagName("<ide_selection>a</ide_selection>"), "<ide_selection>")
        XCTAssertEqual(OwnerTurnPreview.tagName("<channel/>"), "<channel>")
        for text in ["<project-falcon>plan", "<b>bold</b>", "<task-notificationx>", "<a", "<"] {
            XCTAssertEqual(OwnerTurnPreview.tagName(text), "(other)", text)
        }
        // Line kinds, roles and block types come from fixed lists too.
        XCTAssertEqual(OwnerTurnPreview.echo("user", OwnerTurnPreview.claudeLineTypes), "user")
        XCTAssertEqual(OwnerTurnPreview.echo("my-secret-word", OwnerTurnPreview.claudeLineTypes), "(other)")
        XCTAssertEqual(OwnerTurnPreview.echo("tool_result", OwnerTurnPreview.claudeBlockTypes), "tool_result")
        XCTAssertEqual(OwnerTurnPreview.echo("user", OwnerTurnPreview.claudeBlockTypes), "(other)")
        XCTAssertEqual(OwnerTurnPreview.echo("developer", OwnerTurnPreview.codexRoles), "developer")
        XCTAssertEqual(OwnerTurnPreview.echo(nil, OwnerTurnPreview.codexRoles), "(other)")
        // Every marker tag is a known harness tag, so a regression shows by name.
        for marker in OwnerTurnPreview.claudeMarkers + OwnerTurnPreview.codexMarkers where marker.hasPrefix("<") {
            XCTAssertNotEqual(OwnerTurnPreview.tagName(marker + ">"), "(other)", marker)
        }
    }
    #endif

    // MARK: Grants

    func testTheReadsFollowTheDirectoryGrants() throws {
        let home = try Self.ownerFixtureHome()
        let args = Self.args(since: "2026-11-20T00:00:00.000Z", prefixes: ["/Users/owner"])
        let off = Self.readers(home, claude: false, codex: false)
        XCTAssertEqual(off.availability()[Self.claude], "not_granted")
        XCTAssertEqual(off.availability()[Self.codex], "not_granted")
        XCTAssertEqual(off.read(capability: Self.claude, args: args, now: 1_795_219_200), .failure)
        XCTAssertEqual(off.read(capability: Self.codex, args: args, now: 1_795_219_200), .failure)
        let claudeOnly = Self.readers(home, claude: true, codex: false)
        XCTAssertEqual(claudeOnly.availability()[Self.claude], "granted")
        XCTAssertEqual(claudeOnly.availability()[Self.codex], "not_granted")
        guard claudeOnly.read(capability: Self.claude, args: args, now: 1_795_219_200).ok else {
            return XCTFail("the granted read must run")
        }
        XCTAssertEqual(claudeOnly.read(capability: Self.codex, args: args, now: 1_795_219_200), .failure)
    }

    private func envelope(_ key: Curve25519.Signing.PrivateKey, capability: String, nonce: String) throws -> Data {
        let keyID = Signing.keyID(for: key.publicKey.rawRepresentation)
        let args = Self.args(since: "2026-11-29T00:00:00.000Z", prefixes: ["~/work"])
        var body: [String: JSONValue] = [
            "v": 1, "type": "invoke", "id": "4f5b027e-07eb-4be5-a70e-d157c4bb21de",
            "invocation_id": "d9426746-a50b-4e7f-8f39-150d02c0c8d7",
            "node_id": "6f1e2d3c-4b5a-4987-8a6b-5c4d3e2f1a0b",
            "workspace_id": "2a3b4c5d-6e7f-4a8b-9c0d-1e2f3a4b5c6d",
            "capability": .string(capability), "capability_version": 1, "class": "read",
            "args": args, "args_sha256": .string(try JSON.sha256Hex(args)), "nonce": .string(nonce),
            "issued_at": 1_796_000_000, "expires_at": 1_796_000_030, "trace_id": "owner-turn-test", "grant": nil,
            "key_id": .string(keyID),
        ]
        body["sig"] = .string(Base64URL.encode(try key.signature(for: JSON.canonical(.object(body)))))
        return try JSON.canonical(.object(body))
    }

    func testARevokeIsNotGrantedAtOnceAndAnnounced() throws {
        let home = try FixtureHome.build()
        let key = Curve25519.Signing.PrivateKey()
        let granted = Self.readers(home)
        let session = NodeSession(
            nodeID: "6f1e2d3c-4b5a-4987-8a6b-5c4d3e2f1a0b",
            pinned: [Signing.keyID(for: key.publicKey.rawRepresentation): key.publicKey.rawRepresentation],
            catalogue: .standard(availability: granted.availability()), readers: granted,
            clock: ManualClock(Self.caseNow), nodeVersion: "friday-node test", osVersion: "macOS test")
        _ = session.connect()
        let ack: JSONValue = [
            "v": 1, "type": "hello.ack", "id": "4f5b027e-07eb-4be5-a70e-d157c4bb21aa",
            "node_id": "6f1e2d3c-4b5a-4987-8a6b-5c4d3e2f1a0b", "heartbeat_seconds": 15, "rotate_after_days": 30,
            "server_time": 1_796_000_000, "accepted": [],
        ]
        _ = session.receive(try JSON.canonical(ack))

        // The owner turns off the Claude directory grant: a new reader set.
        let revoked = Self.readers(home, claude: false, codex: true)
        let frames = session.update(catalogue: .standard(availability: revoked.availability()), readers: revoked)
        XCTAssertEqual(frames.count, 1)
        XCTAssertEqual(frames.first?["kind"], "grant.availability_changed")
        let batch = frames.first?["batch"]?.arrayValue ?? []
        XCTAssertTrue(batch.contains(["name": .string(Self.claude), "version": 1, "availability": "not_granted"]))
        XCTAssertFalse(batch.contains { $0["name"]?.stringValue == Self.codex })
        XCTAssertNoThrow(try NodeProtocol.validate(frames[0], direction: .nodeToServer))

        let refused = session.receive(
            try envelope(key, capability: Self.claude, nonce: "fsCBbs_g-_AY6EWtzsd4OwliyKKYwIdwAe68dKMApFw"))
        XCTAssertEqual(refused.first?["refusal_code"], "not_granted")
        XCTAssertTrue(revoked.calls.isEmpty, "no reader may run on a refusal path")
        XCTAssertTrue(granted.calls.isEmpty)
    }
}
