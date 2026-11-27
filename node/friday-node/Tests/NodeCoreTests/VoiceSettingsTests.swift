// Voice settings, the indicator, the install checks and the child process
// rules (Phase 11 Mac session M1; ADR-013 decisions 1, 5, 6; ADR-015
// decisions 2, 3; runbook B7). No microphone, no Keychain, no real install:
// memory stores, temporary directories and fake devices.

import Darwin
import Foundation
import NodeCore
import XCTest

final class FailingMute: InputMuteSource {
    func deviceMuted() throws -> Bool? { throw InputMuteUnreadable() }
}

final class FixedMute: InputMuteSource {
    let value: Bool?
    init(_ value: Bool?) { self.value = value }
    func deviceMuted() throws -> Bool? { value }
}

final class VoiceSettingsTests: XCTestCase {
    func testAlwaysOnNeedsTheOwnerNarrowingDoesNot() async throws {
        let store = MemoryVoiceSettingsStore(VoiceSettings(mode: "push_to_talk"))
        let refused = VoiceSettingsEditor(store: store, authentication: AlwaysNo())
        do {
            try await refused.setMode("always_on")
            XCTFail("widened without LocalAuthentication")
        } catch {
            XCTAssertEqual(error as? PolicyChangeError, .notAuthenticated)
        }
        XCTAssertEqual(try store.load().mode, "push_to_talk")
        try await refused.setMode("off")
        XCTAssertEqual(try store.load().mode, "off")
        let owner = VoiceSettingsEditor(store: store, authentication: AlwaysYes())
        var seen: [VoiceSettings] = []
        owner.onChange = { seen.append($0) }
        try await owner.setMode("always_on")
        XCTAssertEqual(try store.load().mode, "always_on")
        XCTAssertEqual(seen.map(\.mode), ["always_on"])
        try refused.setMuted(true)  // muting never waits for authentication
        XCTAssertTrue(try store.load().muted)
        XCTAssertThrowsError(try refused.setHotkey("caps_lock"))
        try refused.setHotkey("f5")
        XCTAssertEqual(try store.load().hotkey, "f5")
    }

    func testMuteFailsClosed() {
        let store = MemoryVoiceSettingsStore(VoiceSettings(mode: "push_to_talk"))
        XCTAssertEqual(VoiceMute.read(store: store, device: NoInputMute()), .no)
        XCTAssertEqual(VoiceMute.read(store: store, device: FixedMute(true)), .yes)
        XCTAssertEqual(VoiceMute.read(store: store, device: FixedMute(nil)), .no, "no mute property: not consulted")
        XCTAssertEqual(VoiceMute.read(store: store, device: FailingMute()), .unreadable)
        store.failLoad = true
        XCTAssertEqual(VoiceMute.read(store: store, device: NoInputMute()), .unreadable)
        // The host core sends `mute: unreadable` and shows `muted`.
        let core = VoiceHostCore(clock: ManualVoiceClock(), credential: VoiceCases.credential)
        core.startChild()
        _ = core.takeOutbound()
        core.setMute(.unreadable)
        let frame = core.takeOutbound().first.flatMap { try? VoiceFraming.decode($0).header }
        XCTAssertEqual(frame?["muted"], "unreadable")
        XCTAssertEqual(core.indicator, "muted")
    }

    func testTheStoredDocumentIsStrict() throws {
        let settings = VoiceSettings(mode: "always_on", muted: true, hotkey: "f5")
        XCTAssertEqual(try VoiceSettings.decoded(try JSON.canonical(settings.document)), settings)
        XCTAssertThrowsError(try VoiceSettings.decoded(Data(#"{"v":1,"mode":"loud","muted":false,"hotkey":"f5"}"#.utf8)))
        XCTAssertThrowsError(try VoiceSettings.decoded(Data(#"{"v":1,"mode":"off","muted":"no","hotkey":"f5"}"#.utf8)))
        XCTAssertEqual(VoiceSettings().mode, "off", "voice is off until the owner turns it on")
    }

    func testEveryStateHasAnIndicatorAndOnlyHeldStatesShowALiveMic() {
        for state in VoiceProtocol.states {
            let indicator = MicIndicator.presentation(for: state)
            XCTAssertEqual(indicator.state, state)
            XCTAssertFalse(indicator.label.isEmpty)
            XCTAssertEqual(indicator.micLive, VoiceHostCore.acquireStates.contains(state), state)
        }
        XCTAssertEqual(MicIndicator.presentation(for: "unknown").state, "off")
        XCTAssertEqual(
            Set(VoiceProtocol.states.map { MicIndicator.presentation(for: $0).symbol }).count, VoiceProtocol.states.count)
    }

    func testEarconsAreAFixedBoundedSet() {
        for name in Earcons.names {
            let pcm = Earcons.pcm(name)
            XCTAssertNotNil(pcm, name)
            XCTAssertEqual((pcm?.count ?? 1) % 2, 0)
            XCTAssertLessThan(pcm?.count ?? 0, 16_000 * 2)  // under a second
        }
        XCTAssertNil(Earcons.pcm("siren"))
    }

    func testTheFramerCutsExactTwentyMillisecondFrames() {
        var framer = PCMFramer()
        XCTAssertEqual(framer.append(Data(count: 600)), [])
        let frames = framer.append(Data(count: 1000))
        XCTAssertEqual(frames.map(\.count), [640, 640])
        XCTAssertEqual(framer.buffered, 320)
    }

    // MARK: Wake models (the A8 keyword model is not pinned on this Mac)

    func makeInstall(keywordPin: String, keyword: Data?) throws -> URL {
        let home = FileManager.default.temporaryDirectory.appendingPathComponent("voice-home-\(UUID().uuidString)")
        let models = home.appendingPathComponent("models")
        try FileManager.default.createDirectory(at: models, withIntermediateDirectories: true)
        let mel = Data("mel".utf8)
        let emb = Data("emb".utf8)
        try mel.write(to: models.appendingPathComponent("melspectrogram.onnx"))
        try emb.write(to: models.appendingPathComponent("embedding_model.onnx"))
        if let keyword { try keyword.write(to: models.appendingPathComponent("hey_friday.onnx")) }
        let manifest: JSONValue = [
            "modelDir": .string(models.path),
            "models": [
                ["role": "melspectrogram", "file": "melspectrogram.onnx", "sha256": .string(JSON.hexDigest(mel))],
                ["role": "embedding", "file": "embedding_model.onnx", "sha256": .string(JSON.hexDigest(emb))],
                ["role": "keyword", "file": "hey_friday.onnx", "sha256": .string(keywordPin)],
            ],
        ]
        try JSON.canonical(manifest).write(to: home.appendingPathComponent("models.json"))
        return home
    }

    func testWakeIsNotArmableWithoutAPinnedKeywordModel() throws {
        let unpinned = try makeInstall(keywordPin: "", keyword: nil)
        defer { try? FileManager.default.removeItem(at: unpinned) }
        XCTAssertEqual(WakeModels.status(home: unpinned.path)?["keyword"], "unpinned")
        XCTAssertFalse(WakeModels.armable(home: unpinned.path))
        let keyword = Data("hey".utf8)
        let pinned = try makeInstall(keywordPin: JSON.hexDigest(keyword), keyword: keyword)
        defer { try? FileManager.default.removeItem(at: pinned) }
        XCTAssertTrue(WakeModels.armable(home: pinned.path))
        let swapped = try makeInstall(keywordPin: JSON.hexDigest(keyword), keyword: Data("other".utf8))
        defer { try? FileManager.default.removeItem(at: swapped) }
        XCTAssertEqual(WakeModels.status(home: swapped.path)?["keyword"], "mismatch")
        XCTAssertFalse(WakeModels.armable(home: "/nonexistent"))
    }

    func testTheHotkeysAreDistinct() {
        XCTAssertEqual(Set(VoiceHotkeys.choices.map(\.id)).count, VoiceHotkeys.choices.count)
        XCTAssertNotNil(VoiceHotkeys.byID[VoiceSettings.defaultHotkey])
    }
}

final class VoiceChildHostTests: XCTestCase {
    func testTheLockHashGatesTheInstall() throws {
        let home = FileManager.default.temporaryDirectory.appendingPathComponent("voice-lock-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: home, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: home) }
        XCTAssertEqual(VoiceChildHost.checkLock(home: home.path), .notInstalled)
        try Repo.data("node/friday-voice/uv.lock").write(to: home.appendingPathComponent("uv.lock"))
        XCTAssertEqual(VoiceChildHost.checkLock(home: home.path), .ok)
        try Data("tampered".utf8).write(to: home.appendingPathComponent("uv.lock"))
        XCTAssertEqual(VoiceChildHost.checkLock(home: home.path), .mismatch)
        // A mismatched install never starts: a failed start, retried with
        // the backoff.
        let core = VoiceHostCore(clock: ManualVoiceClock(), credential: VoiceCases.credential)
        let host = VoiceChildHost(core: core, install: .installed(home: home.path))
        host.start(autoTick: false)
        let failure = host.performSync { ($0.lastFailure, $0.restartInMs, $0.linkOpen) }
        XCTAssertEqual(failure.0, "lock_mismatch")
        XCTAssertEqual(failure.1, 1_000)
        XCTAssertFalse(failure.2)
        XCTAssertNil(host.childPID)
    }

    func testTheInstalledPathsAreTheRunbooks() {
        let install = VoiceChildHost.Install.installed(home: "/H")
        XCTAssertEqual(install.executable, "/H/.venv/bin/friday-voice")
        XCTAssertEqual(install.argv, ["friday-voice", "run"])
        XCTAssertTrue(install.checkLock)
        XCTAssertTrue(VoiceChildHost.Install.defaultHome.hasSuffix("/Library/Application Support/Friday/voice"))
    }

    /// A child that dies is restarted with backoff; one that forks keeps
    /// nothing alive after stop() (the tree is killed); the child's
    /// descendants are found by lineage.
    func testExitRestartsWithBackoffAndStopKillsTheTree() throws {
        let clock = ManualVoiceClock()
        let core = VoiceHostCore(clock: clock, credential: VoiceCases.credential)
        let install = VoiceChildHost.Install(
            home: "/tmp", executable: "/bin/sh", argv: ["sh", "-c", "sleep 30 & sleep 30 & wait"], cwd: "/",
            checkLock: false)
        let host = VoiceChildHost(core: core, install: install)
        host.start(autoTick: false)
        var pid: pid_t?
        let deadline = Date().addingTimeInterval(5)
        while pid == nil, Date() < deadline {
            pid = host.childPID
            usleep(10_000)
        }
        let root = try XCTUnwrap(pid)
        var descendants: [pid_t] = []
        for _ in 0..<100 where descendants.count < 2 {
            descendants = VoiceChildHost.descendantPIDs(of: root)
            usleep(10_000)
        }
        XCTAssertEqual(descendants.count, 2, "the two sleeps")
        host.stop(wait: true)
        let gone = Date().addingTimeInterval(10)
        while Date() < gone, ([root] + descendants).contains(where: { kill($0, 0) == 0 }) { usleep(50_000) }
        XCTAssertFalse(([root] + descendants).contains(where: { kill($0, 0) == 0 }), "the tree outlived stop()")
        XCTAssertEqual(host.performSync { $0.lastFailure }, nil, "a deliberate stop is no failure")

        // An exiting child: failed closed, restarted only when the backoff ran out.
        let quick = VoiceHostCore(clock: clock, credential: VoiceCases.credential)
        let exiting = VoiceChildHost(
            core: quick,
            install: VoiceChildHost.Install(home: "/tmp", executable: "/usr/bin/true", argv: ["true"], cwd: "/", checkLock: false))
        exiting.start(autoTick: false)
        let failed = Date().addingTimeInterval(5)
        while Date() < failed, exiting.performSync({ $0.lastFailure }) == nil { usleep(10_000) }
        XCTAssertEqual(exiting.performSync { $0.lastFailure }, "child_exit")
        XCTAssertEqual(exiting.performSync { $0.restartInMs }, 1_000)
        clock.advance(999)
        exiting.queue.sync { exiting.tick() }
        XCTAssertEqual(exiting.performSync { $0.starts }, 1)
        clock.advance(1)
        exiting.queue.sync { exiting.tick() }
        XCTAssertEqual(exiting.performSync { $0.starts }, 2)
        exiting.stop(wait: true)
    }
}
