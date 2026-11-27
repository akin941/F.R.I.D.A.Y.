// The installed voice child, on the owner's Mac only (Phase 11 M1; runbook
// B7): opt-in with FRIDAY_VOICE_INSTALLED_CHILD=1, because it needs
// `make friday-voice-install`. The host checks the installed uv.lock against
// the compiled-in hash, spawns `.venv/bin/friday-voice run` with the clean
// environment, and the real child answers the handshake: `ready`, then an
// armed push-to-talk `state`, so the core holds the (logical) mic. No audio
// engine is involved (no microphone is opened) and the credential points at
// a closed loopback port, so nothing leaves the Mac.

import Darwin
import Foundation
import NodeCore
import XCTest

final class VoiceInstalledChildTests: XCTestCase {
    func testTheInstalledChildPassesTheLockCheckAndTheHandshake() throws {
        guard ProcessInfo.processInfo.environment["FRIDAY_VOICE_INSTALLED_CHILD"] == "1" else {
            throw XCTSkip("set FRIDAY_VOICE_INSTALLED_CHILD=1 on the Mac after make friday-voice-install")
        }
        let install = VoiceChildHost.Install.installed()
        XCTAssertEqual(VoiceChildHost.checkLock(home: install.home), .ok, "installed uv.lock differs from the compiled-in hash")
        let credential: [String: JSONValue] = [
            "token": .string("jpat_" + String(repeating: "L0calHandshake", count: 2)),
            "expires_at": "2099-01-01T00:00:00Z", "server_url": "http://127.0.0.1:9", "ca_sha256": .null,
        ]
        let core = VoiceHostCore(clock: SystemVoiceClock(), credential: credential, pacedPlayback: true)
        let host = VoiceChildHost(core: core, install: install)
        host.start()
        let deadline = Date().addingTimeInterval(20)
        while Date() < deadline, !host.performSync({ $0.micHeld }) { usleep(20_000) }
        let (ready, state, held, failure) = host.performSync { ($0.childReady, $0.childState, $0.micHeld, $0.lastFailure) }
        XCTAssertTrue(ready)
        XCTAssertEqual(state, "armed_ptt")
        XCTAssertTrue(held)
        XCTAssertNil(failure)
        let pid = try XCTUnwrap(host.childPID)
        host.stop(wait: true)
        let gone = Date().addingTimeInterval(10)
        while Date() < gone, kill(pid, 0) == 0 { usleep(50_000) }
        XCTAssertNotEqual(kill(pid, 0), 0, "the child outlived stop()")
        XCTAssertNil(host.performSync { $0.lastFailure })
    }
}
