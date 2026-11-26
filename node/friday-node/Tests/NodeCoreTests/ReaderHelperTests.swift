// The reader helpers against the reference (backend/nodes/reference/
// readers.py) behaviour, beyond what the conformance cases reach, and the
// rule-2 streaming: a transcript outside `cwd_prefixes` stops at its first
// `cwd` line, so a large file is never read whole or parsed.

import Foundation
import NodeCore
import XCTest

final class ReaderHelperTests: XCTestCase {
    // MARK: Paths (normalise / path_forms / matches_prefixes)

    func testPathRulesMatchTheReference() {
        let home = "/Users/owner"
        // Expected values computed with the reference functions on CPython 3.12.
        let cases: [(String, String?)] = [
            ("~/work//acme/./src/..", "~/work/acme"), ("~", "~"), ("~/../x", nil), ("relative", nil),
            ("//srv//a", "/srv/a"), ("/a/b/../../..", "/"), ("", nil), ("~/a\u{0}", nil),
        ]
        for (input, expected) in cases {
            XCTAssertEqual(ReaderPathsProbe.normalise(input), expected, input)
        }
        let forms = ReaderPathsProbe.forms("/Users/owner/work/acme/", home: home)
        XCTAssertEqual(forms?.display, "~/work/acme")
        XCTAssertEqual(Set(forms?.forms ?? []), ["/Users/owner/work/acme", "~/work/acme"])
        XCTAssertTrue(ReaderPathsProbe.matches(["~/work/acme"], forms!.forms))
        XCTAssertTrue(ReaderPathsProbe.matches(["/Users/owner/work"], forms!.forms))
        XCTAssertFalse(ReaderPathsProbe.matches(["~/work/ac"], forms!.forms))  // segment boundary
        let secret = ReaderPathsProbe.forms("/Users/owner/work/acme-secret", home: home)!
        XCTAssertFalse(ReaderPathsProbe.matches(["~/work/acme"], secret.forms))
        XCTAssertTrue(ReaderPathsProbe.matches(["~"], secret.forms))
        XCTAssertTrue(ReaderPathsProbe.matches(nil, secret.forms))
        XCTAssertFalse(ReaderPathsProbe.matches([], secret.forms))
    }

    // MARK: Times and money

    func testTimestamps() {
        XCTAssertEqual(ReaderTimeProbe.parse("2026-11-19T10:00:00Z"), 1_795_082_400)
        XCTAssertEqual(ReaderTimeProbe.parse("2026-11-19T10:00:00.987654Z"), 1_795_082_400)
        XCTAssertEqual(ReaderTimeProbe.parse("2026-11-19T13:00:00+03:00"), 1_795_082_400)
        XCTAssertEqual(ReaderTimeProbe.parse("2026-11-19 10:00:00+0000"), 1_795_082_400)
        XCTAssertNil(ReaderTimeProbe.parse("2026-11-19T10:00:00"))  // naive: no zone
        XCTAssertNil(ReaderTimeProbe.parse("2026-02-30T10:00:00Z"))
        XCTAssertNil(ReaderTimeProbe.parse("2026-11-19T10:00:00Z\n"))
    }

    func testMicrosAreHalfEvenOverPythonFloatRepr() {
        // Decimal(str(float)) * 1e6, ROUND_HALF_EVEN (CPython 3.12).
        let cases: [(String, Int64)] = [
            ("0.0123", 12_300), ("1e-05", 10), ("0.0000005", 0), ("0.0000015", 2), ("0.0000025", 2),
            ("3", 3_000_000), ("-1", 0), ("NaN", 0), ("Infinity", 0), ("0.1", 100_000),
        ]
        for (literal, expected) in cases {
            XCTAssertEqual(ReaderMoneyProbe.micros(number: literal), expected, literal)
        }
        XCTAssertEqual(ReaderMoneyProbe.micros(string: " 0.5 "), 500_000)
        XCTAssertEqual(ReaderMoneyProbe.micros(string: "abc"), 0)
    }

    // MARK: Files (pathlib semantics) and streaming

    private func tempDirectory() throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("friday-reader-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return URL(fileURLWithPath: FixtureHome.realpath(url.path))
    }

    func testFileListingFollowsPathlib() throws {
        let base = try tempDirectory()
        defer { try? FileManager.default.removeItem(at: base) }
        let root = base.appendingPathComponent("root")
        let fm = FileManager.default
        for dir in ["a/b", ".hid", "a-x"] { try fm.createDirectory(at: root.appendingPathComponent(dir), withIntermediateDirectories: true) }
        for file in ["a/b/x.jsonl", ".hid/h.jsonl", "a/.dot.jsonl", "a-x/c.jsonl", "a/c.jsonl"] {
            try Data("{}\n".utf8).write(to: root.appendingPathComponent(file))
        }
        let other = base.appendingPathComponent("other")
        try fm.createDirectory(at: other, withIntermediateDirectories: true)
        try Data("{}\n".utf8).write(to: other.appendingPathComponent("o.jsonl"))
        try fm.createSymbolicLink(at: root.appendingPathComponent("lnk"), withDestinationURL: other)
        try fm.createSymbolicLink(at: root.appendingPathComponent("inlnk"), withDestinationURL: root.appendingPathComponent("a"))
        func relative(_ paths: [String]) -> [String] { paths.map { String($0.dropFirst(root.path.count + 1)) } }
        // CPython 3.12: `*/` follows a symlinked directory (then realpath
        // drops what leaves the root), `**/` does not; dot files included;
        // pathlib order (component by component).
        XCTAssertEqual(relative(ReaderFilesProbe.transcripts(root.path, recursive: false)),
                       [".hid/h.jsonl", "a/.dot.jsonl", "a/c.jsonl", "a-x/c.jsonl", "inlnk/.dot.jsonl", "inlnk/c.jsonl"])
        XCTAssertEqual(relative(ReaderFilesProbe.transcripts(root.path, recursive: true)),
                       [".hid/h.jsonl", "a/.dot.jsonl", "a/b/x.jsonl", "a/c.jsonl", "a-x/c.jsonl"])
    }

    func testATranscriptOutsideThePrefixesIsNotReadWhole() throws {
        let home = try tempDirectory()
        defer { try? FileManager.default.removeItem(at: home) }
        let project = home.appendingPathComponent(".claude/projects/-elsewhere")
        try FileManager.default.createDirectory(at: project, withIntermediateDirectories: true)
        // ~30 MB: one `cwd` line outside the prefix, then many valid lines.
        let first = #"{"type":"user","cwd":"/elsewhere","timestamp":"2026-11-19T10:00:00Z","message":{"content":"x"}}"# + "\n"
        let filler = #"{"type":"assistant","timestamp":"2026-11-19T10:00:01Z","message":{"content":""# + String(repeating: "y", count: 3000) + "\"}}\n"
        var data = Data(first.utf8)
        let line = Data(filler.utf8)
        while data.count < 30 * 1024 * 1024 { data.append(line) }
        try data.write(to: project.appendingPathComponent("s1.jsonl"))
        let readers = NodeReaders(
            home: home.path, repos: nil, grants: ReaderGrants(claude: true), windowTitles: false,
            desktop: try FixtureDesktop(fixturesDirectory: FixtureHome.fixtures), usage: .transcripts)
        let started = Date()
        let outcome = readers.read(
            capability: "claude.sessions.list",
            args: ["since_hours": 1_000_000, "limit": 50, "cwd_prefixes": ["~/work"]], now: 1_796_000_000)
        XCTAssertLessThan(Date().timeIntervalSince(started), 1.0, "rule 2: stop at the first cwd line")
        XCTAssertEqual(outcome.output, ["sessions": []])
        // Without the prefix the same file is read (and its session listed).
        let all = readers.read(
            capability: "claude.sessions.list", args: ["since_hours": 1_000_000, "limit": 50], now: 1_796_000_000)
        XCTAssertEqual(all.output?["sessions"]?.arrayValue?.count, 1)
    }

    func testAnOversizedLineIsSkippedNotHeld() throws {
        let home = try tempDirectory()
        defer { try? FileManager.default.removeItem(at: home) }
        let project = home.appendingPathComponent(".claude/projects/-p")
        try FileManager.default.createDirectory(at: project, withIntermediateDirectories: true)
        let huge = #"{"type":"user","cwd":"/x","pad":""# + String(repeating: "z", count: 1_100_000) + "\"}\n"
        let ok = #"{"type":"user","cwd":"/p","timestamp":"2026-11-19T10:00:00Z","message":{"content":"hello"}}"# + "\n"
        try Data((huge + ok).utf8).write(to: project.appendingPathComponent("s2.jsonl"))
        let readers = NodeReaders(
            home: home.path, repos: nil, grants: ReaderGrants(claude: true), windowTitles: false,
            desktop: try FixtureDesktop(fixturesDirectory: FixtureHome.fixtures), usage: .transcripts)
        let out = readers.read(
            capability: "claude.sessions.list", args: ["since_hours": 1_000_000, "limit": 50], now: 1_796_000_000)
        // The oversized first line (with its cwd) is skipped; the next line's
        // cwd is the transcript's.
        XCTAssertEqual(out.output?["sessions"]?.arrayValue?.first?["cwd"], "/p")
    }

    // MARK: A policy change before hello.ack

    func testAChangeBeforeHelloAckIsSentWithTheAck() throws {
        let granted = Catalogue.standard(availability: ["claude.sessions.list": "granted"])
        let session = NodeSession(
            nodeID: "6f1e2d3c-4b5a-4987-8a6b-5c4d3e2f1a0b", pinned: [:], catalogue: granted,
            readers: PolicyTests.CountingReader(), clock: ManualClock(1_796_000_005),
            nodeVersion: "friday-node test", osVersion: "macOS test")
        _ = session.connect()
        let revoked = Catalogue.standard(availability: ["claude.sessions.list": "not_granted"])
        XCTAssertTrue(session.update(catalogue: revoked, readers: PolicyTests.CountingReader()).isEmpty)
        let ack: JSONValue = [
            "v": 1, "type": "hello.ack", "id": "4f5b027e-07eb-4be5-a70e-d157c4bb21aa",
            "node_id": "6f1e2d3c-4b5a-4987-8a6b-5c4d3e2f1a0b", "heartbeat_seconds": 15, "rotate_after_days": 30,
            "server_time": 1_796_000_000, "accepted": [],
        ]
        let frames = session.receive(try JSON.canonical(ack))
        XCTAssertEqual(frames.count, 1)
        XCTAssertEqual(frames.first?["kind"], "grant.availability_changed")
        XCTAssertEqual(frames.first?["batch"], [["name": "claude.sessions.list", "version": 1, "availability": "not_granted"]])
    }
}
