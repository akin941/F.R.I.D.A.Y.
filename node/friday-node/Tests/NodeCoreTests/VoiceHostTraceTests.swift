// Barge-in and playback, the host's half (Phase 11 Mac session M1 contract
// test 5; brief D9; runbook B7).
//
// node/friday-node/fixtures/voice_host_traces.json is recorded by
// scripts/voice_host_traces.py from node/friday-voice/fixtures/barge_in.json
// (`friday-voice replay`'s scripts: the owner talking over a reply at AEC
// residual 0 / 0.3 / 1.0, and a reply left to play out with only its echo):
// every call the reference host (`HostCore(paced_playback=True)`) received
// from the real child and the replay clock, and every batch of frames it
// wrote back. Here the same calls go into `VoiceHostCore` on a manual clock
// set to the recorded times, and every batch must be the same frames, in
// the same order, with the same `at_ms`; `stopped_at`, the counters, the mic
// and the indicator must end the same. So paced playback (queue order,
// `started` when an utterance begins, `ended` when its audio has run,
// `stop_playback` for a queued or playing utterance, also after its last
// frame, `stopped` in the same tick as the frame: 0 ms, inside the 100 ms
// budget) is the reference's exactly. The child's side (VAD persistence,
// the AEC guard, pre-roll, cancellation) stays pinned by
// node/friday-voice/tests/test_barge_in.py and tests/test_session.py; the
// real-AEC hand check is an owner step (the brief's M1 record).

import Foundation
import NodeCore
import XCTest

final class VoiceHostTraceTests: XCTestCase {
    static let path = "node/friday-node/fixtures/voice_host_traces.json"

    static func muted(_ value: JSONValue?) -> VoiceHostCore.Muted {
        switch value {
        case .bool(true): return .yes
        case .bool(false): return .no
        default: return .unreadable
        }
    }

    func replay(_ trace: JSONValue) throws -> [String] {
        let id = trace["id"]?.stringValue ?? "?"
        let clock = ManualVoiceClock()
        let core = VoiceHostCore(
            clock: clock, credential: try XCTUnwrap(trace["credential"]?.objectValue, id),
            mode: trace["mode"]?.stringValue ?? "push_to_talk", hostVersion: "0.1.0-reference",
            pacedPlayback: trace["paced_playback"]?.boolValue ?? false)
        var failures: [String] = []
        var stopsAt: [Int64] = []
        var effects: [VoiceHostEffect] = []
        for (index, call) in (trace["calls"]?.arrayValue ?? []).enumerated() {
            let items = call.arrayValue ?? []
            let op = items.first?.stringValue ?? ""
            if op == "out" {
                let want = items[1].arrayValue ?? []
                let got = core.takeOutbound().map { (try? VoiceFraming.decode($0).header).map(JSONValue.object) ?? .null }
                if got != want { failures.append("\(id) call \(index): wrote \(got), expected \(want)") }
                continue
            }
            clock.monotonic = items[1].intValue ?? 0
            clock.wall = items[2].intValue ?? 0
            let args = Array(items.dropFirst(3))
            switch op {
            case "start_child": core.startChild()
            case "send_credential": core.sendCredential(args[0].objectValue ?? [:])
            case "child_bytes":
                var bytes = Data()
                for frame in args[0].arrayValue ?? [] {
                    var header = frame.arrayValue?[0].objectValue ?? [:]
                    let length = Int(frame.arrayValue?[1].intValue ?? 0)
                    if header["type"] == "stop_playback" { stopsAt.append(clock.monotonic) }
                    header.removeValue(forKey: "pcm_bytes")
                    bytes += try VoiceFraming.encode(header, payload: Data(count: length), direction: .childToHost)
                }
                core.childBytes(bytes)
            case "child_stream_closed": core.childStreamClosed()
            case "child_exited": core.childExited(code: args[0].intValue ?? 0)
            case "tick": core.tick()
            case "feed_audio":
                core.feedAudio(Data(count: Int(args[0].intValue ?? 0)), aec: args[1].stringValue ?? "on")
            case "set_link": core.setLink(args[0].boolValue ?? false)
            case "set_mute": core.setMute(Self.muted(args[0]))
            case "set_mode": core.setMode(args[0].stringValue ?? "off")
            case "ptt": core.ptt(args[0].stringValue ?? "up")
            case "shutdown": core.shutdown()
            default: failures.append("\(id) call \(index): unknown op \(op)")
            }
            effects += core.takeEffects()
        }
        let end = trace["end"]
        let stopped: JSONValue = .array(core.stoppedAt.map { .array([.string($0.0), .int($0.1)]) })
        if stopped != end?["stopped_at"] { failures.append("\(id): stopped_at \(stopped), expected \(end?["stopped_at"] ?? .null)") }
        let observed: [String: JSONValue] = [
            "mic_held": .bool(core.micHeld), "indicator": .string(core.indicator),
            "dropped_play": .int(Int64(core.droppedPlay)), "dropped_stop": .int(Int64(core.droppedStop)),
            "audio_sent": .int(Int64(core.audioSent)), "releases": .array(core.releases.map { .string($0) }),
            "earcons": .array(core.earcons.map { .string($0) }),
            "failures": .array(core.failures.map { .string($0.0) }),
        ]
        for (key, value) in observed where end?[key] != value {
            failures.append("\(id): \(key) is \(value), expected \(end?[key] ?? .null)")
        }
        // Every honoured stop happened in the very call that carried its
        // `stop_playback` (0 ms on the host clock; the budget is 100 ms).
        for (_, at) in core.stoppedAt where !stopsAt.contains(at) {
            failures.append("\(id): a stop at \(at) did not follow a stop_playback in the same call")
        }
        // The speakers got exactly what the protocol said: every utterance
        // that began is either stopped or played out, and no audio of a
        // stopped utterance is handed over after its stop.
        var silenced: Set<String> = []
        for effect in effects {
            switch effect {
            case .playbackStopped(let id): silenced.insert(id)
            case .playbackAudio(let id, _) where silenced.contains(id):
                failures.append("\(trace["id"]?.stringValue ?? "?"): audio of \(id) after its stop")
            default: break
            }
        }
        return failures
    }

    func testTheBargeInScriptsReplayExactlyAsTheReferenceHost() throws {
        let doc = try Repo.json(Self.path)
        let traces = try XCTUnwrap(doc["traces"]?.arrayValue)
        // barge_in.json carries float residuals: Foundation's parser here.
        let fixture = try JSONSerialization.jsonObject(with: try Repo.data("node/friday-voice/fixtures/barge_in.json"))
        let scenarios = ((fixture as? [String: Any])?["scenarios"] as? [[String: Any]]) ?? []
        XCTAssertEqual(
            traces.compactMap { $0["id"]?.stringValue }, scenarios.compactMap { $0["id"] as? String },
            "rerun scripts/voice_host_traces.py")
        var sawStopWhilePlaying = false
        var sawStopQueued = false
        var sawPlayOut = false
        for trace in traces {
            XCTAssertEqual(trace["paced_playback"], true)
            XCTAssertEqual(try replay(trace), [], trace["id"]?.stringValue ?? "?")
            let stops = trace["end"]?["stopped_at"]?.arrayValue ?? []
            if stops.count >= 2 { sawStopWhilePlaying = true; sawStopQueued = true }
            if trace["result"]?["stopped"] == false { sawPlayOut = true }
        }
        XCTAssertTrue(sawStopWhilePlaying && sawStopQueued && sawPlayOut, "the scripts cover stop and play-out")
    }

    /// Paced playback by hand: queue order, `ended` only after the audio ran,
    /// stop of a queued utterance after its last frame, stop of the playing
    /// one, and the speakers silenced at once.
    func testPacedPlaybackQueuesAndStops() throws {
        let clock = ManualVoiceClock()
        let core = VoiceHostCore(clock: clock, credential: VoiceCases.credential, pacedPlayback: true)
        core.startChild()
        _ = core.takeOutbound()
        func send(_ header: [String: JSONValue], _ payload: Data = Data()) throws {
            core.childBytes(try VoiceFraming.encode(header, payload: payload, direction: .childToHost))
        }
        try send(["type": "ready", "protocol": "friday-voice/v1", "child_version": "0.1.0"])
        let second = Data(count: 32_000)  // 1 s of audio
        try send(["type": "play", "utterance_id": "a", "seq": 0, "last": false], second)
        try send(["type": "play", "utterance_id": "a", "seq": 1, "last": true], second)
        try send(["type": "play", "utterance_id": "b", "seq": 0, "last": true], second)
        try send(["type": "play", "utterance_id": "c", "seq": 0, "last": true], second)
        func states() -> [String] {
            core.takeOutbound().compactMap { try? VoiceFraming.decode($0).header }
                .map { "\($0["utterance_id"]?.stringValue ?? ""):\($0["state"]?.stringValue ?? "")" }
        }
        XCTAssertEqual(states(), ["a:started"])
        XCTAssertEqual(
            core.takeEffects(),
            [.playbackBegan("a"), .playbackAudio("a", second), .playbackAudio("a", second)])
        clock.advance(1_999)
        core.tick()
        XCTAssertEqual(states(), [])
        clock.advance(1)
        core.tick()
        XCTAssertEqual(states(), ["a:ended", "b:started"])
        XCTAssertEqual(core.takeEffects(), [.playbackBegan("b"), .playbackAudio("b", second)])
        // c is queued with its last frame in: still stoppable.
        try send(["type": "stop_playback", "utterance_id": "c"])
        XCTAssertEqual(states(), ["c:stopped"])
        XCTAssertEqual(core.takeEffects(), [])
        // b plays: stopped now, silenced now.
        clock.advance(10)
        try send(["type": "stop_playback", "utterance_id": "b"])
        XCTAssertEqual(states(), ["b:stopped"])
        XCTAssertEqual(core.takeEffects(), [.playbackStopped("b")])
        XCTAssertEqual(core.stoppedAt.map(\.1), [2_000, 2_010])
        // A stop for nothing is counted, nothing else.
        try send(["type": "stop_playback", "utterance_id": "b"])
        XCTAssertEqual(core.droppedStop, 1)
        XCTAssertTrue(core.linkOpen)
    }
}
