// The voice host as the app wires it (Phase 11 brief D3, D6, D12, D15;
// Mac session M1; runbook B7), with a fake audio engine (no microphone is
// ever opened), a fake node link and a scripted mint. The "child" is a real
// process that records every frame the host writes to it (`/bin/sh -c
// 'cat > file'`, a clean environment) and writes nothing back; the child's
// frames are injected into the host core as if it had written them.

import Darwin
import Foundation
import NodeCore
import XCTest

final class FakeAudio: VoiceAudioIO, @unchecked Sendable {
    private let lock = NSLock()
    var onCapture: ((Data, String) -> Void)?
    private(set) var calls: [String] = []
    var failStart = false

    private func note(_ call: String) {
        lock.lock()
        calls.append(call)
        lock.unlock()
    }

    var recorded: [String] {
        lock.lock()
        defer { lock.unlock() }
        return calls
    }

    func startCapture() throws {
        note("startCapture")
        if failStart { throw VoiceAudioError.noInput }
    }
    func stopCapture() { note("stopCapture") }
    func beginPlayback(_ utteranceID: String) { note("begin:\(utteranceID)") }
    func enqueue(_ utteranceID: String, pcm: Data) { note("enqueue:\(utteranceID):\(pcm.count)") }
    func stopPlayback(_ utteranceID: String) { note("stop:\(utteranceID)") }
    func playEarcon(_ name: String) { note("earcon:\(name)") }
    func shutdown() { note("shutdown") }
}

final class FakeLink: VoiceNodeLink, @unchecked Sendable {
    private let lock = NSLock()
    private var states: [JSONValue] = []
    var credential: NodeCredential? = VoiceFixtures.nodeCredential()

    func relayVoiceState(_ state: JSONValue) {
        lock.lock()
        states.append(state)
        lock.unlock()
    }

    func credentialForVoice() -> NodeCredential? { credential }

    var relayed: [JSONValue] {
        lock.lock()
        defer { lock.unlock() }
        return states
    }
}

/// A scripted mint: each call takes the next outcome.
final class ScriptedMint: @unchecked Sendable {
    private let lock = NSLock()
    var outcomes: [Result<VoiceToken, VoiceCredentialError>]
    private(set) var count = 0

    init(_ outcomes: [Result<VoiceToken, VoiceCredentialError>]) { self.outcomes = outcomes }

    static func token(_ suffix: String = "A", expiresIn: Double = 900) -> VoiceToken {
        VoiceToken(
            token: "jpat_" + String(repeating: suffix, count: 20), tokenID: "t", expiresAt: "2099-01-01T00:00:00Z",
            expiresAtSeconds: Date().timeIntervalSince1970 + expiresIn, previousValidUntil: nil,
            serverURL: "https://friday.example", caSHA256: String(repeating: "ab", count: 32))
    }

    private func next() -> Result<VoiceToken, VoiceCredentialError> {
        lock.lock()
        defer { lock.unlock() }
        count += 1
        return outcomes.isEmpty ? .failure(.transport) : outcomes.removeFirst()
    }

    var mint: VoiceService.Mint {
        { _ in try self.next().get() }
    }

    var calls: Int {
        lock.lock()
        defer { lock.unlock() }
        return count
    }
}

final class VoiceServiceTests: XCTestCase {
    var directory: URL!
    /// The service holds its link weakly (the app's NodeRunner outlives it).
    var links: [FakeLink] = []
    var record: URL { directory.appendingPathComponent("frames.bin") }

    override func setUpWithError() throws {
        directory = FileManager.default.temporaryDirectory.appendingPathComponent("voice-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: directory)
    }

    func makeService(
        mint: ScriptedMint, settings: MemoryVoiceSettingsStore = MemoryVoiceSettingsStore(VoiceSettings(mode: "push_to_talk")),
        audio: FakeAudio = FakeAudio(), link: FakeLink = FakeLink()
    ) -> (VoiceService, FakeAudio, FakeLink) {
        let core = VoiceHostCore(clock: SystemVoiceClock(), mode: "off", muted: .unreadable, linkUp: false, pacedPlayback: true)
        let install = VoiceChildHost.Install(
            home: directory.path, executable: "/bin/sh", argv: ["sh", "-c", "cat > \"$0\"", record.path],
            cwd: directory.path, checkLock: false)
        let host = VoiceChildHost(core: core, install: install)
        let service = VoiceService(host: host, audio: audio, settingsStore: settings, link: link, mint: mint.mint)
        links.append(link)
        return (service, audio, link)
    }

    func wait(_ what: String, timeout: Double = 5, _ condition: () -> Bool) {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if condition() { return }
            usleep(10_000)
        }
        XCTFail("timed out: \(what)")
    }

    func written() -> [String] {
        guard let data = try? Data(contentsOf: record) else { return [] }
        let decoder = VoiceFrameDecoder(direction: .hostToChild)
        return decoder.feed(data).frames.compactMap { $0.header["type"]?.stringValue }
    }

    func inject(_ service: VoiceService, _ headers: [[String: JSONValue]]) throws {
        var bytes = Data()
        for header in headers { bytes += try VoiceFraming.encode(header, direction: .childToHost) }
        service.host.perform { $0.childBytes(bytes) }
        _ = service.host.performSync { _ in 0 }
    }

    static let ready: [String: JSONValue] = ["type": "ready", "protocol": "friday-voice/v1", "child_version": "0.1.0"]

    static func state(_ name: String) -> [String: JSONValue] {
        [
            "type": "state", "state": .string(name), "mode": "push_to_talk", "session_open": false,
            "wake_accepts_today": 0, "wake_discards_today": 0, "storm_mutes_today": .int(name == "storm_muted" ? 1 : 0),
            "at_ms": 1_800_000_000_000,
        ]
    }

    func core(_ service: VoiceService) -> (mic: Bool, indicator: String, running: Bool, muted: VoiceHostCore.Muted) {
        service.host.performSync { ($0.micHeld, $0.indicator, $0.childRunning, $0.muted) }
    }

    /// Up: the child starts with hello + credential; its states reach the
    /// link as `voice.state` items; the mic follows the core.
    func testTheChildStartsOnTheLinkAndItsStatesAreRelayed() throws {
        let mint = ScriptedMint([.success(ScriptedMint.token())])
        let (service, audio, link) = makeService(mint: mint)
        service.start(autoPoll: false)
        service.linkChanged(true)
        wait("credential") { written().contains("credential") }
        XCTAssertEqual(Array(written().prefix(1)), ["hello"])
        try inject(service, [Self.ready, Self.state("armed_ptt")])
        wait("mic held") { core(service).mic }
        wait("capture") { audio.recorded.contains("startCapture") }
        XCTAssertEqual(core(service).indicator, "armed_ptt")
        let item = try XCTUnwrap(link.relayed.last)
        XCTAssertEqual(Set(item.objectValue?.keys ?? [:].keys), [
            "state", "mode", "session_open", "wake_accepts_today", "wake_discards_today", "storm_mutes_today",
        ])
        XCTAssertEqual(item["state"], "armed_ptt")
        // Push-to-talk reaches the child.
        service.ptt(true)
        service.ptt(false)
        wait("ptt") { written().filter { $0 == "ptt" }.count == 2 }
        // Capture frames reach the child only while the mic is held.
        audio.onCapture?(Data(count: 640), "on")
        wait("audio") { written().contains("audio") }
        service.shutdown()
        XCTAssertTrue(audio.recorded.contains("shutdown"))
        wait("shutdown frame") { written().last == "shutdown" }
    }

    /// release_mic(storm) releases; `storm_muted` shows until the child
    /// re-arms, which it does only after a mute on -> off cycle on the Mac
    /// (the host relays both mute changes).
    func testAStormReleaseShowsStormMutedUntilAMuteCycle() throws {
        let settings = MemoryVoiceSettingsStore(VoiceSettings(mode: "push_to_talk"))
        let (service, audio, _) = makeService(mint: ScriptedMint([.success(ScriptedMint.token())]), settings: settings)
        let editor = VoiceSettingsEditor(store: settings, authentication: AlwaysNo())
        editor.onChange = { service.settingsChanged($0) }
        service.start(autoPoll: false)
        service.linkChanged(true)
        wait("credential") { written().contains("credential") }
        try inject(service, [Self.ready, Self.state("armed_ptt")])
        wait("held") { core(service).mic }
        try inject(service, [["type": "release_mic", "reason": "storm"], Self.state("storm_muted")])
        wait("released") { !core(service).mic }
        XCTAssertEqual(core(service).indicator, "storm_muted")
        wait("capture stopped") { audio.recorded.contains("stopCapture") }
        XCTAssertEqual(MicIndicator.presentation(for: "storm_muted").micLive, false)
        try editor.setMuted(true)
        wait("muted") { core(service).muted == .yes }
        XCTAssertEqual(core(service).indicator, "muted")
        try editor.setMuted(false)
        wait("unmuted") { core(service).muted == .no }
        wait("two mute frames") { written().filter { $0 == "mute" }.count >= 2 }
        try inject(service, [Self.state("armed_ptt")])
        wait("held again") { core(service).mic }
        service.shutdown()
    }

    /// A 401 on the mint releases the mic (indicator `error`) and keeps it
    /// released whatever the child says, until a fresh token is sent.
    func testA401ReleasesTheMicAndBacksOff() throws {
        let mint = ScriptedMint([.failure(.refused)])
        let (service, _, _) = makeService(mint: mint)
        service.start(autoPoll: false)
        service.linkChanged(true)
        wait("mint") { mint.calls == 1 }
        wait("refused") { core(service).indicator == "error" }
        try inject(service, [Self.ready, Self.state("armed_ptt")])
        XCTAssertFalse(core(service).mic)
        XCTAssertEqual(core(service).indicator, "error")
        // The backoff: no second mint at once.
        usleep(300_000)
        XCTAssertEqual(mint.calls, 1)
        // A fresh token (the backoff ran out): sent, and the mic may be held.
        mint.outcomes = [.success(ScriptedMint.token("B"))]
        usleep(1_000_000)
        // The app's one-second poll re-mints once the backoff ran out; any
        // reconcile does too (driven here by an unchanged setting).
        service.settingsChanged(VoiceSettings(mode: "push_to_talk"))
        wait("second mint") { mint.calls == 2 }
        wait("credential re-sent") { written().filter { $0 == "credential" }.count >= 1 }
        try inject(service, [Self.state("armed_ptt")])
        wait("held") { core(service).mic }
        service.shutdown()
    }

    /// The kill switch kills the child at once and nothing restarts it until
    /// it is released; WS loss sends `link: down`, then stops the child.
    func testKillSwitchAndLinkLossStopTheChild() throws {
        let mint = ScriptedMint([.success(ScriptedMint.token()), .success(ScriptedMint.token("C"))])
        let (service, _, _) = makeService(mint: mint)
        service.start(autoPoll: false)
        service.linkChanged(true)
        wait("started") { service.host.childPID != nil }
        let first = try XCTUnwrap(service.host.childPID)
        service.killSwitchChanged(engaged: true)
        wait("killed") { kill(first, 0) != 0 }
        usleep(1_500_000)
        XCTAssertNil(service.host.childPID, "no restart under the kill switch")
        XCTAssertFalse(core(service).mic)
        service.killSwitchChanged(engaged: false)
        wait("restarted") { service.host.childPID != nil }
        let second = try XCTUnwrap(service.host.childPID)
        XCTAssertNotEqual(first, second)
        // The second child's file (truncated at its spawn): hello, then the
        // freshly minted credential.
        wait("second child greeted") { written() == ["hello", "credential"] }
        let before = written().count
        service.linkChanged(false)
        wait("stopped") { kill(second, 0) != 0 }
        XCTAssertEqual(Array(written().dropFirst(before)), ["link", "shutdown"],
                       "link: down reaches the child before it is stopped")
        XCTAssertEqual(core(service).indicator, "off")
        service.shutdown()
    }

    /// Mode off stops the child; the stored settings unreadable means
    /// `mute: unreadable` (fail closed).
    /// B7 review 7: a microphone that cannot be opened (TCC denied) is not
    /// retried on every child state; the mic stays released, indicator
    /// `error`, until the backoff runs out.
    func testADeniedMicrophoneBacksOff() throws {
        let audio = FakeAudio()
        audio.failStart = true
        let (service, _, _) = makeService(mint: ScriptedMint([.success(ScriptedMint.token())]), audio: audio)
        service.start(autoPoll: false)
        service.linkChanged(true)
        wait("credential") { written().contains("credential") }
        try inject(service, [Self.ready, Self.state("armed_ptt")])
        wait("one try") { audio.recorded.filter { $0 == "startCapture" }.count == 1 }
        wait("blocked") { core(service).indicator == "error" }
        for _ in 0..<5 { try inject(service, [Self.state("armed_ptt")]) }
        XCTAssertFalse(core(service).mic)
        XCTAssertEqual(core(service).indicator, "error")
        XCTAssertEqual(audio.recorded.filter { $0 == "startCapture" }.count, 1, "no retry inside the backoff")
        audio.failStart = false
        wait("retried after the backoff") { audio.recorded.filter { $0 == "startCapture" }.count == 2 }
        wait("held") { core(service).mic }
        service.shutdown()
    }

    func testModeOffAndUnreadableSettings() throws {
        let settings = MemoryVoiceSettingsStore(VoiceSettings(mode: "push_to_talk"))
        let (service, _, _) = makeService(mint: ScriptedMint([.success(ScriptedMint.token())]), settings: settings)
        service.start(autoPoll: false)
        service.linkChanged(true)
        wait("started") { service.host.childPID != nil }
        let pid = try XCTUnwrap(service.host.childPID)
        service.settingsChanged(VoiceSettings(mode: "off"))
        wait("stopped") { kill(pid, 0) != 0 }
        settings.failLoad = true
        XCTAssertEqual(VoiceMute.read(store: settings, device: NoInputMute()), .unreadable)
        service.shutdown()
    }

    /// The child's environment is PATH, HOME and LANG only.
    func testTheChildEnvironmentIsClean() throws {
        let environment = VoiceChildHost.childEnvironment(home: "/Users/o")
        XCTAssertEqual(Set(environment.keys), ["PATH", "HOME", "LANG"])
        let core = VoiceHostCore(clock: SystemVoiceClock(), credential: VoiceCases.credential)
        let install = VoiceChildHost.Install(
            home: directory.path, executable: "/usr/bin/env", argv: ["env"], cwd: directory.path, checkLock: false)
        let host = VoiceChildHost(core: core, install: install)
        // What the host would hand the child, read back by env itself.
        let probe = try ChildProcess.spawn(
            executable: "/usr/bin/env", argv: ["env"], environment: host.environment, cwd: directory.path,
            stdin: .pipe, stdout: .pipe, stderr: .devNull)
        var data = Data()
        if let fd = probe.stdoutFD { ChildProcess.drain(fd) { data.append($0) } }
        probe.wait(timeout: 5)
        let names = Set(String(decoding: data, as: UTF8.self).split(separator: "\n").compactMap { $0.split(separator: "=").first.map(String.init) })
        XCTAssertEqual(names, ["PATH", "HOME", "LANG"])
        if let fd = probe.stdinFD { close(fd) }
        // As the voice child, env's output is protocol garbage: the host
        // fails it closed (released, restart scheduled).
        host.start()
        wait("failed closed") { host.performSync { $0.failures.count } >= 1 }
        XCTAssertEqual(host.performSync { $0.micHeld }, false)
        host.stop(wait: true)
    }

    /// Runbook B8: the nightly selftest's hold stops the child and releases
    /// the mic (one voice at a time; the selftest's token supersedes the
    /// child's), and giving it back starts the child with a fresh mint.
    func testTheSelftestHoldStopsTheChildAndGivesItBackWithAFreshToken() throws {
        let mint = ScriptedMint([.success(ScriptedMint.token()), .success(ScriptedMint.token("B"))])
        let (service, audio, _) = makeService(mint: mint)
        service.start(autoPoll: false)
        service.linkChanged(true)
        wait("credential") { written().contains("credential") }
        try inject(service, [Self.ready, Self.state("armed_ptt")])
        wait("capture") { service.micInUse }
        let first = try XCTUnwrap(service.host.childPID)
        XCTAssertTrue(service.setSelftestHold(true, timeout: 3), "the mic is free within the timeout")
        XCTAssertFalse(service.micInUse)
        XCTAssertTrue(audio.recorded.contains("stopCapture"))
        wait("child stopped") { kill(first, 0) != 0 }
        usleep(500_000)
        XCTAssertNil(service.host.childPID, "no restart while the selftest holds the voice")
        XCTAssertEqual(mint.calls, 1)
        service.setSelftestHold(false, timeout: 0)
        wait("restarted") { service.host.childPID != nil }
        wait("fresh mint") { mint.calls == 2 }
        service.shutdown()
    }
}

/// An owner who never authenticates.
struct AlwaysNo: OwnerAuthentication {
    func authenticate(reason: String) async -> Bool { false }
}

struct AlwaysYes: OwnerAuthentication {
    func authenticate(reason: String) async -> Bool { true }
}
