// Offline conformance (Phase 9 brief M1, contract test 1): every case file
// in backend/nodes/conformance/cases/v1/ is played against `NodeSession`
// with the reference node's expected frames and codes.
//
// A Swift port of backend/nodes/conformance/runner.py's `InProcessDriver`,
// `GatewayChecker` and `check`.
//
// The read cases that need the M2 readers (Claude Code / Codex transcripts,
// git, usage, window titles and the scrubber) cannot pass in M1. They are
// listed in `pendingM2` and the test fails both when a case outside the list
// does not pass and when a listed case starts passing (so M2 must empty the
// list; nothing is skipped silently).

import Foundation
import NodeCore
import XCTest

/// The M1 test reader: `apps.frontmost` over the reference fixture desktop
/// (backend/nodes/reference/fixtures/desktop.json). Every other capability
/// is an M2 reader and fails here.
final class FixtureDesktopReader: ReadProvider {
    private(set) var calls: [String] = []
    let desktop: JSONValue

    init() throws {
        desktop = try Repo.json("backend/nodes/reference/fixtures/desktop.json")
    }

    func read(capability: String, args: JSONValue, now: Int64) -> ReadOutcome {
        calls.append(capability)
        guard capability == "apps.frontmost" else { return .failure }
        let frontmost = desktop["frontmost"] ?? [:]
        return .success([
            "bundle_id": frontmost["bundle_id"] ?? .null,
            "app_name": frontmost["app_name"] ?? .null,
        ])
    }
}

/// The fake gateway's view of the node's frames.
struct GatewayChecker {
    var inFlight: Set<String> = []
    var errors: [String] = []

    mutating func serverSent(_ frame: JSONValue?) {
        if frame?["type"]?.stringValue == "invoke", let id = frame?["invocation_id"]?.stringValue {
            inFlight.insert(id)
        }
    }

    mutating func nodeSent(_ frame: JSONValue) {
        guard (try? NodeProtocol.validate(frame, direction: .nodeToServer)) != nil else {
            errors.append("schema")
            return
        }
        if frame["type"]?.stringValue == "result", let id = frame["invocation_id"]?.stringValue {
            if inFlight.remove(id) == nil { errors.append("result_not_in_flight") }
        }
    }
}

final class ConformanceDriver {
    static let advanceStepSeconds: Int64 = 5
    let session: NodeSession
    let clock: ManualClock
    let reader: FixtureDesktopReader
    var frames: [JSONValue] = []
    var checker = GatewayChecker()
    var silent = false

    init(case document: JSONValue) throws {
        let setup = document["setup"] ?? [:]
        clock = ManualClock(setup["now"]?.intValue ?? 0)
        reader = try FixtureDesktopReader()
        var availability: [String: String] = [:]
        for (name, value) in setup["availability"]?.objectValue ?? [:] {
            availability[name] = value.stringValue
        }
        let keys = try Repo.json("backend/nodes/conformance/keys/conformance_keys.json")
        let publicKey = try XCTUnwrap(Signing.publicKey(base64: keys["signing"]?["public_key_b64"]?.stringValue ?? ""))
        session = NodeSession(
            nodeID: ConformanceCase.nodeID,
            pinned: [Signing.keyID(for: publicKey): publicKey],
            catalogue: .standard(availability: availability),
            readers: reader, clock: clock,
            nodeVersion: "friday-node conformance", osVersion: "macOS test",
            readDelaySeconds: setup["read_delay"]?.intValue ?? 0,
            backoff: Backoff(random: { 0.5 }))
        silent = setup["silent"]?.boolValue ?? false
        handle(session.connect())
    }

    func handle(_ out: [JSONValue]) {
        for frame in out {
            checker.nodeSent(frame)
            frames.append(frame)
            if frame["type"]?.stringValue == "heartbeat", !silent, let seq = frame["seq"]?.intValue {
                let ack: JSONValue = [
                    "v": 1, "type": "heartbeat.ack",
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
            case "kill_switch": break  // v1: no window or executor to stop
            case "voice_state": handle(session.relayVoiceState(step["state"] ?? .null))
            default: XCTFail("unknown step \(String(describing: step["op"]))")
            }
        }
    }

    /// The failures of one played case (empty when it passed).
    func failures(_ document: JSONValue) -> [String] {
        let expect = document["expect"] ?? [:]
        var failures: [String] = []
        let sessionFrames = frames.filter { !["hello", "heartbeat"].contains($0["type"]?.stringValue ?? "") }
        let matchers = expect["frames"]?.arrayValue ?? []
        if sessionFrames.count != matchers.count {
            failures.append("expected \(matchers.count) frames, got \(sessionFrames.count)")
        }
        for (index, (matcher, frame)) in zip(matchers, sessionFrames).enumerated() {
            for (key, value) in matcher.objectValue ?? [:] where frame[key] != value {
                failures.append("frame \(index): \(key) != \(value), got \(String(describing: frame[key]))")
            }
        }
        if let calls = expect["reader_calls"]?.intValue, calls != Int64(reader.calls.count) {
            failures.append("reader_calls: expected \(calls), got \(reader.calls.count)")
        }
        if let reason = expect["closed_reason"] {
            let actual: JSONValue = session.closedReason.map(JSONValue.string) ?? .null
            if actual != reason { failures.append("closed_reason: expected \(reason), got \(actual)") }
        }
        if let allowed = expect["reconnect_allowed"]?.boolValue, allowed != session.reconnectAllowed {
            failures.append("reconnect_allowed: expected \(allowed)")
        }
        if let dropped = expect["voice_states_dropped"]?.intValue, dropped != Int64(session.voiceStatesDropped) {
            failures.append("voice_states_dropped: expected \(dropped), got \(session.voiceStatesDropped)")
        }
        for key in ["executor_calls", "executor_terminated", "windows_shown"] {
            if let count = expect[key]?.intValue, count != 0 { failures.append("\(key): v1 runs no \(key)") }
        }
        if let maximum = expect["backoff_max_seconds"]?.intValue {
            guard let delay = session.nextReconnectDelay, delay >= 0, delay <= Double(maximum) else {
                failures.append("the first reconnect backoff is outside 0..\(maximum)")
                return failures
            }
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

    static func all() throws -> [(name: String, document: JSONValue)] {
        let directory = Repo.url("backend/nodes/conformance/cases/v1")
        let names = try FileManager.default.contentsOfDirectory(atPath: directory.path)
            .filter { $0.hasSuffix(".json") }.sorted()
        return try names.map { name in
            (String(name.dropLast(5)), try JSON.parse(try Data(contentsOf: directory.appendingPathComponent(name))))
        }
    }
}

final class ConformanceTests: XCTestCase {
    /// Read cases that need the M2 readers and scrubber (Phase 9 brief M2,
    /// runbook B2). M2 empties this list.
    static let pendingM2: Set<String> = [
        "v1_read_codex_thread_detail_scrubbed",
        "v1_read_codex_threads_list_cwd_prefixes",
        "v1_read_directory_not_granted",
        "v1_read_git_status",
        "v1_read_session_detail_last_turns",
        "v1_read_session_detail_scrubbed",
        "v1_read_sessions_list_cwd_prefixes",
        "v1_read_sessions_list_since_hours",
        "v1_read_usage_cwd_prefixes",
        "v1_read_windows_titles_off",
        "v1_read_windows_titles_on_scrubbed",
    ]

    func testEveryV1CaseMatchesTheReferenceNode() throws {
        let cases = try ConformanceCase.all()
        XCTAssertGreaterThanOrEqual(cases.count, 50, "the v1 case set shrank")
        var passed = 0
        var stillPending: [String] = []
        for (name, document) in cases {
            XCTAssertEqual(document["version"]?.intValue, 1, name)
            let driver = try ConformanceDriver(case: document)
            try driver.play(document)
            let failures = driver.failures(document)
            if Self.pendingM2.contains(name) {
                if failures.isEmpty { XCTFail("\(name) passes now: remove it from pendingM2") }
                stillPending.append(name)
                continue
            }
            if failures.isEmpty {
                passed += 1
            } else {
                XCTFail("\(name): \(failures.joined(separator: "; "))")
            }
        }
        XCTAssertEqual(Set(stillPending), Self.pendingM2, "pendingM2 names a case that does not exist")
        print("conformance v1: \(passed) passed, \(stillPending.count) pending M2 readers, of \(cases.count)")
    }
}
