// Offline voice conformance (Phase 11 Mac session M1 contract test 1;
// runbook B7): every *host* case in
// node/friday-voice/friday_voice/conformance/cases/host/ is played against
// `VoiceHostCore` on a manual clock with the reference verdicts, none
// skipped. A Swift port of friday_voice/conformance/runner.py
// `run_host_case` (step vocabulary: the `definitions.py` docstring).

import Foundation
import NodeCore
import XCTest

enum VoiceCases {
    static let directory = "node/friday-voice/friday_voice/conformance/cases/host"

    static func load() throws -> [JSONValue] {
        let url = Repo.url(directory)
        let names = try FileManager.default.contentsOfDirectory(atPath: url.path).filter { $0.hasSuffix(".json") }.sorted()
        return try names.map { try JSON.parse(try Data(contentsOf: url.appendingPathComponent($0))) }
    }

    /// definitions.CREDENTIAL without its `type`.
    static let credential: [String: JSONValue] = [
        "token": .string("jpat_" + String(repeating: "c0nf0rmanceT0ken", count: 2)),
        "expires_at": "2099-01-01T00:00:00Z",
        "server_url": "https://friday.example",
        "ca_sha256": .string(String(repeating: "ab", count: 32)),
    ]

    static func core(for testCase: JSONValue, clock: VoiceClock) -> VoiceHostCore {
        let host = testCase["host"]
        let muted: VoiceHostCore.Muted
        switch host?["muted"] {
        case .bool(true): muted = .yes
        case .string("unreadable"): muted = .unreadable
        default: muted = .no
        }
        return VoiceHostCore(
            clock: clock, credential: credential, mode: host?["mode"]?.stringValue ?? "push_to_talk", muted: muted,
            linkUp: host?["link_up"]?.boolValue ?? true, hostVersion: "0.1.0-reference")
    }

    static func local(_ core: VoiceHostCore, _ step: JSONValue) {
        let value = step["value"]
        switch step["event"]?.stringValue {
        case "link": core.setLink(value == "up")
        case "mute":
            switch value {
            case .bool(true): core.setMute(.yes)
            case .bool(false): core.setMute(.no)
            default: core.setMute(.unreadable)
            }
        case "mode": core.setMode(value?.stringValue ?? "off")
        case "ptt": core.ptt(value?.stringValue ?? "up")
        default: XCTFail("unknown host_local event")
        }
    }

    /// runner._check_host: every listed key against the host's state.
    static func check(_ step: JSONValue, _ core: VoiceHostCore, toChild: [String], index: Int) -> [String] {
        var observed: [String: JSONValue] = [
            "mic": core.micHeld ? "held" : "released",
            "link": core.linkOpen ? "open" : "dropped",
            "indicator": .string(core.indicator),
            "failure": core.lastFailure.map { .string($0) } ?? .null,
            "restart_in_ms": core.restartInMs.map { .int($0) } ?? .null,
            "dropped_play": .int(Int64(core.droppedPlay)),
        ]
        observed["to_child"] = .array(toChild.map { .string($0) })
        var failures: [String] = []
        for (key, want) in step.objectValue ?? [:] where key != "op" && key != "describe" {
            guard let got = observed[key] else {
                failures.append("step \(index): unknown expectation \(key)")
                continue
            }
            if got != want { failures.append("step \(index): \(key) is \(got), expected \(want)") }
        }
        return failures
    }
}

final class VoiceConformanceTests: XCTestCase {
    static let ops: Set<String> = [
        "child_send", "child_close_stdout", "child_exit", "host_local", "host_capture", "advance_ms", "expect",
    ]

    func run(_ testCase: JSONValue) -> [String] {
        let clock = ManualVoiceClock()
        let core = VoiceCases.core(for: testCase, clock: clock)
        core.startChild()
        var sent: [String] = []
        var failures: [String] = []
        func drain() { sent += core.takeOutbound().compactMap(VoiceFraming.type(of:)) }
        for (index, step) in (testCase["steps"]?.arrayValue ?? []).enumerated() {
            switch step["op"]?.stringValue {
            case "child_send":
                core.childBytes(Data(base64Encoded: step["frame_b64"]?.stringValue ?? "") ?? Data())
            case "child_close_stdout": core.childStreamClosed()
            case "child_exit": core.childExited(code: step["code"]?.intValue ?? 0)
            case "host_local": VoiceCases.local(core, step)
            case "host_capture":
                for _ in 0..<(step["frames"]?.intValue ?? 0) {
                    core.feedAudio(Data(count: VoiceHostCore.frameBytes))
                }
            case "advance_ms":
                clock.advance(step["ms"]?.intValue ?? 0)
                core.tick()
                if core.restartDue() { core.startChild() }
            case "expect":
                drain()
                failures += VoiceCases.check(step, core, toChild: sent, index: index)
                sent = []
                continue
            default:
                failures.append("step \(index): unknown op")
            }
            drain()
        }
        return failures
    }

    func testEveryHostCasePassesOffline() throws {
        let cases = try VoiceCases.load()
        let files = try FileManager.default.contentsOfDirectory(atPath: Repo.url(VoiceCases.directory).path)
            .filter { $0.hasSuffix(".json") }
        XCTAssertEqual(cases.count, files.count)
        XCTAssertGreaterThanOrEqual(cases.count, 28)
        var ran = 0
        for testCase in cases {
            let id = testCase["id"]?.stringValue ?? "?"
            XCTAssertEqual(testCase["family"], "host", id)
            XCTAssertEqual(testCase["protocol"], .string(VoiceProtocol.name), id)
            for step in testCase["steps"]?.arrayValue ?? [] {
                XCTAssertTrue(Self.ops.contains(step["op"]?.stringValue ?? ""), "\(id): unknown op")
            }
            let failures = run(testCase)
            XCTAssertEqual(failures, [], id)
            ran += 1
        }
        XCTAssertEqual(ran, cases.count, "no host case is skipped")
    }
}
