// Offline conformance (Phase 9 brief M1 contract test 1; Phase 10 brief M1
// contract test 1, runbook B5): every case file in
// backend/nodes/conformance/cases/v1/ and cases/v2/ is played against
// `NodeSession` with the reference node's expected frames and codes.
//
// A Swift port of backend/nodes/conformance/runner.py's `InProcessDriver`,
// `GatewayChecker` and `check`.
//
// Readers run over the reference fixture home (FixtureHome.swift, built like
// nodes/reference/fixtures/factory.py), with the case's local grants and
// window-titles toggle; `usage.ccusage` follows the reference rule. The v2
// cases add the case's scripted window owner and scripted executor
// (NodeCore `CaseSetup`, the same code the Conformance build's hook runs)
// and the action policy of backend/nodes/reference/fixtures/
// local_policy.json over the fixture home.
//
// DEBUG only: the scripted doubles do not exist in Release (the Release
// test build compiles NodeCoreTests to run ReleaseHygieneTests).

#if DEBUG

import Foundation
import NodeCore
import XCTest

/// The fake gateway's view of the node's frames (runner.py
/// `GatewayChecker`).
struct GatewayChecker {
    let version: Int64
    var inFlight: Set<String> = []
    var errors: [String] = []

    init(version: Int64 = 1) { self.version = version }

    mutating func serverSent(_ frame: JSONValue?) {
        if frame?["type"]?.stringValue == "invoke", let id = frame?["invocation_id"]?.stringValue {
            inFlight.insert(id)
        }
    }

    mutating func nodeSent(_ frame: JSONValue) {
        guard (try? NodeProtocol.validate(frame, direction: .nodeToServer, version: version)) != nil else {
            errors.append("schema")
            return
        }
        let kind = frame["type"]?.stringValue ?? ""
        let invocation = frame["invocation_id"]?.stringValue
        if kind == "output" || kind == "confirmation" {
            if !inFlight.contains(invocation ?? "") { errors.append("\(kind)_not_in_flight") }
        } else if kind == "result", let invocation {
            if inFlight.remove(invocation) == nil { errors.append("result_not_in_flight") }
        }
    }
}

final class ConformanceDriver {
    static let advanceStepSeconds: Int64 = 5
    let version: Int64
    let session: NodeSession
    let clock: ManualClock
    let reader: NodeReaders
    let executor: ScriptedExecutor
    let policyFingerprint: String
    var frames: [JSONValue] = []
    var checker: GatewayChecker
    var silent = false

    /// One fixture home for the whole run (the cases only read it; the
    /// worktree case adds and removes a worktree of `work/acme`).
    nonisolated(unsafe) static var sharedHome: String?
    static func home() throws -> String {
        if let home = sharedHome { return home }
        let home = try FixtureHome.build()
        sharedHome = home
        return home
    }

    static func actionPolicy(home: String) throws -> ActionPolicy {
        try ActionPolicy.load(try Repo.json("backend/nodes/reference/fixtures/local_policy.json"), home: home)
    }

    init(case document: JSONValue) throws {
        version = document["version"]?.intValue ?? 1
        checker = GatewayChecker(version: version)
        let setup = document["setup"] ?? [:]
        clock = ManualClock(setup["now"]?.intValue ?? 0)
        let home = try ConformanceDriver.home()
        var grants = ReaderGrants(claude: true, codex: true)
        if let claude = setup["reader_grants"]?["claude"]?.boolValue { grants.claude = claude }
        if let codex = setup["reader_grants"]?["codex"]?.boolValue { grants.codex = codex }
        reader = NodeReaders(
            home: home, repos: try FixtureHome.repos(home: home), grants: grants,
            windowTitles: setup["window_titles"]?.boolValue ?? false,
            desktop: try FixtureDesktop(fixturesDirectory: FixtureHome.fixtures), usage: .transcripts)
        let caseSetup = CaseSetup(setup: setup, clock: clock, readerAvailability: reader.availability())
        executor = caseSetup.executor
        let policy = try ConformanceDriver.actionPolicy(home: home)
        policyFingerprint = policy.fingerprint
        let keys = try Repo.json("backend/nodes/conformance/keys/conformance_keys.json")
        let publicKey = try XCTUnwrap(Signing.publicKey(base64: keys["signing"]?["public_key_b64"]?.stringValue ?? ""))
        session = NodeSession(
            nodeID: ConformanceCase.nodeID,
            pinned: [Signing.keyID(for: publicKey): publicKey],
            catalogue: caseSetup.catalogue,
            readers: reader, clock: clock,
            nodeVersion: "friday-node conformance", osVersion: "macOS test",
            readDelaySeconds: setup["read_delay"]?.intValue ?? 0,
            backoff: Backoff(random: { 0.5 }), policy: policy, executor: caseSetup.executor,
            window: caseSetup.window)
        silent = setup["silent"]?.boolValue ?? false
        handle(session.connect(version: version))
    }

    func handle(_ out: [JSONValue]) {
        for frame in out {
            checker.nodeSent(frame)
            frames.append(frame)
            if frame["type"]?.stringValue == "heartbeat", !silent, let seq = frame["seq"]?.intValue {
                let ack: JSONValue = [
                    "v": .int(version), "type": "heartbeat.ack",
                    "id": .string(String(format: "00000000-0000-4000-8000-%012lld", seq)),
                    "seq": .int(seq), "server_time": .int(clock.now()),
                ]
                handle(session.receive(try! JSON.canonical(ack)))
            }
        }
    }

    func play(_ document: JSONValue) throws {
        for step in document["steps"]?.arrayValue ?? [] {
            switch step["op"]?.stringValue {
            case "send":
                let frame = try XCTUnwrap(step["frame"])
                checker.serverSent(frame)
                handle(session.receive(try JSON.canonical(frame)))
                handle(session.tick())
            case "send_raw":
                let data = step["data"]?.stringValue ?? ""
                let repeated = String(repeating: data, count: Int(step["repeat"]?.intValue ?? 1))
                handle(session.receive(Data(repeated.utf8)))
                handle(session.tick())
            case "advance":
                var seconds = step["seconds"]?.intValue ?? 0
                while seconds > 0 {
                    let stepSeconds = min(seconds, Self.advanceStepSeconds)
                    clock.advance(stepSeconds)
                    seconds -= stepSeconds
                    handle(session.tick())
                }
            case "tick": handle(session.tick())
            case "disconnect": session.disconnect()
            case "kill_switch": handle(session.engageKillSwitch())
            case "voice_state": handle(session.relayVoiceState(step["state"] ?? .null))
            default: XCTFail("unknown step \(String(describing: step["op"]))")
            }
        }
    }

    /// The failures of one played case (empty when it passed).
    func failures(_ document: JSONValue) throws -> [String] {
        let expect = document["expect"] ?? [:]
        var failures: [String] = []
        let sessionFrames = frames.filter { !["hello", "heartbeat"].contains($0["type"]?.stringValue ?? "") }
        let matchers = expect["frames"]?.arrayValue ?? []
        if sessionFrames.count != matchers.count {
            let described = sessionFrames.map { frame in
                ["type", "state", "status", "refusal_code", "stream"].compactMap { frame[$0]?.stringValue }
                    .joined(separator: ":")
            }
            failures.append("expected \(matchers.count) frames, got \(sessionFrames.count): \(described)")
        }
        for (index, (matcher, frame)) in zip(matchers, sessionFrames).enumerated() {
            for (key, value) in matcher.objectValue ?? [:] where frame[key] != value {
                failures.append("frame \(index): \(key) != \(value), got \(String(describing: frame[key]))")
            }
        }
        let report: [String: JSONValue] = [
            "reader_calls": .int(Int64(reader.calls.count)),
            "executor_calls": .int(Int64(executor.calls.count)),
            "executor_terminated": .int(Int64(executor.terminatedCount)),
            "windows_shown": .int(Int64(session.window.shown.count)),
            "closed_reason": session.closedReason.map(JSONValue.string) ?? .null,
            "reconnect_allowed": .bool(session.reconnectAllowed),
            "voice_states_dropped": .int(Int64(session.voiceStatesDropped)),
        ]
        for (key, actual) in report.sorted(by: { $0.key < $1.key }) {
            if let expected = expect[key], expected != actual {
                failures.append("\(key): expected \(expected), got \(actual)")
            }
        }
        if session.policy.fingerprint != policyFingerprint {
            failures.append("the local policy changed during the session")
        }
        if let maximum = expect["backoff_max_seconds"]?.intValue {
            guard let delay = session.nextReconnectDelay, delay >= 0, delay <= Double(maximum) else {
                failures.append("the first reconnect backoff is outside 0..\(maximum)")
                return failures
            }
        }
        if let expected = expect["worktrees_after"]?.intValue {
            let count = try Worktree.list(try ConformanceDriver.home() + "/work/acme").count
            if Int64(count) != expected { failures.append("worktrees_after: expected \(expected), got \(count)") }
        }
        if !checker.errors.isEmpty { failures.append("gateway frame errors: \(checker.errors)") }
        if session.invalidOutgoingFrames > 0 { failures.append("the node built an invalid frame") }
        let text = frames.compactMap { try? JSON.canonicalString($0) }.joined(separator: "\n")
        for encoded in expect["absent_b64"]?.arrayValue ?? [] {
            if let data = Data(base64Encoded: encoded.stringValue ?? ""),
                let secret = String(data: data, encoding: .utf8), text.contains(secret)
            {
                failures.append("a raw secret reached a frame")
            }
        }
        return failures
    }
}

enum ConformanceCase {
    static let nodeID = "6f1e2d3c-4b5a-4987-8a6b-5c4d3e2f1a0b"

    static func all(_ version: Int = 1) throws -> [(name: String, document: JSONValue)] {
        let directory = Repo.url("backend/nodes/conformance/cases/v\(version)")
        let names = try FileManager.default.contentsOfDirectory(atPath: directory.path)
            .filter { $0.hasSuffix(".json") }.sorted()
        return try names.map { name in
            (String(name.dropLast(5)), try JSON.parse(try Data(contentsOf: directory.appendingPathComponent(name))))
        }
    }
}

final class ConformanceTests: XCTestCase {
    /// Every v1 case runs; none is pending since M2 (readers, grants,
    /// scrubber). Kept as an explicit, empty list so a future gap must be
    /// named here rather than skipped silently.
    static let pendingM2: Set<String> = []
    /// B4 owner-turn readers: none pending since runbook B4 (the Swift
    /// owner-turn readers serve every folded 8B case). Kept as an explicit,
    /// empty list like `pendingM2`.
    static let pendingB4: Set<String> = []
    /// Runbook B5: v2 cases that need a runbook B6 executor (srt, recipes,
    /// AX, open/focus) to pass. Empty: every v2 case passes against the
    /// scripted executor the reference node's own suites use, so none is
    /// pending; kept explicit so a gap must be named here.
    static let pendingB6: Set<String> = []
    static var pending: Set<String> { pendingM2.union(pendingB4).union(pendingB6) }

    private func play(version: Int, minimum: Int) throws {
        let cases = try ConformanceCase.all(version)
        XCTAssertGreaterThanOrEqual(cases.count, minimum, "the v\(version) case set shrank")
        var passed = 0
        var stillPending: [String] = []
        for (name, document) in cases {
            XCTAssertEqual(document["version"]?.intValue, Int64(version), name)
            let driver = try ConformanceDriver(case: document)
            try driver.play(document)
            let failures = try driver.failures(document)
            if Self.pending.contains(name) {
                if failures.isEmpty { XCTFail("\(name) passes now: remove it from its pending set") }
                stillPending.append(name)
                continue
            }
            if failures.isEmpty {
                passed += 1
            } else {
                XCTFail("\(name): \(failures.joined(separator: "; "))")
            }
        }
        XCTAssertTrue(
            Set(stillPending).isSubset(of: Self.pending), "a pending set names a case that does not exist")
        print("conformance v\(version): \(passed) passed, \(stillPending.count) pending, of \(cases.count)")
    }

    func testEveryV1CaseMatchesTheReferenceNode() throws {
        try play(version: 1, minimum: 50)
    }

    func testEveryV2CaseMatchesTheReferenceNode() throws {
        try play(version: 2, minimum: 90)
    }

    /// Every pending name is a real case of v1 or v2.
    func testPendingSetsNameRealCases() throws {
        let names = Set(try ConformanceCase.all(1).map(\.name) + ConformanceCase.all(2).map(\.name))
        XCTAssertTrue(Self.pending.isSubset(of: names), "\(Self.pending.subtracting(names))")
    }
}

#endif
