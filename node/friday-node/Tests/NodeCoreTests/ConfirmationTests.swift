// The confirmation window's decision logic (Phase 10 brief D5 checks 23-24,
// D3 `confirm_template`; ADR-013 decision 6; runbook B5): the rendered text
// equals the reference node's `render_confirmation` byte for byte, the
// window model's rules (one at a time, 60 s, first decision before the
// deadline), the panel's pure view, and the session flows the AppKit panel
// drives (a real click, "Durdur", the local kill switch, socket loss).

import CryptoKit
import Foundation
import NodeCore
import XCTest

/// Signed v2 invokes for unit tests, with the published grant-vector key.
enum V2Frames {
    static let nodeID = "7d0f2b8e-1c4a-4f5e-9b6d-2a3c4d5e6f70"
    static let workspaceID = "0b1c2d3e-4f50-4617-8a9b-0c1d2e3f4a5b"
    static let approvalID = "3c2b1a09-8f7e-4d6c-9b5a-4f3e2d1c0b0a"

    static func key() throws -> Curve25519.Signing.PrivateKey {
        let doc = try Repo.json("backend/nodes/protocol/vectors/grants.json")
        let raw = try XCTUnwrap(Data(base64Encoded: doc["private_key_b64"]?.stringValue ?? ""))
        return try Curve25519.Signing.PrivateKey(rawRepresentation: raw)
    }

    static func pinned() throws -> [String: Data] {
        let publicKey = try key().publicKey.rawRepresentation
        return [Signing.keyID(for: publicKey): publicKey]
    }

    static func uuid(_ n: Int) -> String { String(format: "00000000-0000-4000-8000-%012d", n) }

    static func nonce(_ n: Int) -> String {
        Base64URL.encode(Data(SHA256.hash(data: Data("nonce-\(n)".utf8))).prefix(32))
    }

    static func sign(_ body: [String: JSONValue], domain: Data, key: Curve25519.Signing.PrivateKey) throws -> JSONValue {
        var signed = body
        signed["key_id"] = .string(Signing.keyID(for: key.publicKey.rawRepresentation))
        let signature = try key.signature(for: domain + JSON.canonical(.object(signed)))
        signed["sig"] = .string(Base64URL.encode(signature))
        return .object(signed)
    }

    /// A signed v2 invoke (with its grant for gate class).
    static func invoke(
        _ capability: String, _ cls: String, args: JSONValue, n: Int, issuedAt: Int64, ttl: Int64 = 90
    ) throws -> Data {
        let key = try key()
        var envelope: [String: JSONValue] = [
            "v": 2, "type": "invoke", "id": .string(uuid(n)), "invocation_id": .string(uuid(1000 + n)),
            "node_id": .string(nodeID), "workspace_id": .string(workspaceID), "capability": .string(capability),
            "capability_version": 1, "class": .string(cls), "args": args, "args_sha256": .string(try JSON.sha256Hex(args)),
            "nonce": .string(nonce(n)), "issued_at": .int(issuedAt), "expires_at": .int(issuedAt + ttl),
            "trace_id": .string("trace-\(n)"), "grant": nil,
        ]
        if cls == "gate" {
            var grant: [String: JSONValue] = [
                "v": 2, "grant_id": .string(uuid(2000 + n)), "approval_id": .string(approvalID), "class": "gate",
            ]
            for name in GrantVerifier.sharedFields { grant[name] = envelope[name] }
            envelope["grant"] = try sign(grant, domain: Signing.grantDomainV2, key: key)
        }
        return try JSON.canonical(try sign(envelope, domain: Signing.invokeDomainV2, key: key))
    }

    static func helloAck() throws -> Data {
        try JSON.canonical([
            "v": 2, "type": "hello.ack", "id": .string(uuid(9999)), "node_id": .string(nodeID), "server_time": 0,
            "heartbeat_seconds": 15, "rotate_after_days": 30, "accepted": [],
        ])
    }
}

final class ConfirmationRenderTests: XCTestCase {
    private func policy() throws -> ActionPolicy {
        try ActionPolicy.load(try Repo.json("backend/nodes/reference/fixtures/local_policy.json"), home: "/nonexistent/home")
    }

    private func text(_ capability: String, _ cls: String, _ args: JSONValue, approval: String? = nil) throws -> String {
        var grant: JSONValue = nil
        if let approval { grant = ["approval_id": .string(approval)] }
        let envelope: JSONValue = [
            "capability": .string(capability), "class": .string(cls), "args": args, "grant": grant,
            "invocation_id": .string(V2Frames.uuid(1)),
        ]
        let resolved = try? policy().resolve(capability: capability, args: args)
        return ConfirmationRenderer.prompt(envelope: envelope, resolved: resolved, shownAt: 0).text
    }

    /// Expected texts printed by the reference node's `render_confirmation`
    /// over the same fixture policy (backend/nodes/reference/confirm.py).
    func testTheTextIsTheReferenceText() throws {
        XCTAssertEqual(
            try text("repo.command.run", "gate", ["repo_path": "~/work/acme", "command_kind": "test"], approval: V2Frames.approvalID),
            "~/work/acme reposunda 'test' komutu çalıştırılsın mı?\nRepo: acme\nKomut: python3 -c print('acme tests ok')\nAğ: kapalı\nOnay: 3c2b1a09")
        XCTAssertEqual(
            try text("repo.command.run", "gate", ["repo_path": "~/code/friday", "command_kind": "test"], approval: V2Frames.approvalID),
            "~/code/friday reposunda 'test' komutu çalıştırılsın mı?\nRepo: friday\nKomut: uv run pytest -q\nAğ: pypi.org, files.pythonhosted.org\nOnay: 3c2b1a09")
        XCTAssertEqual(
            try text("applescript.recipe.run", "gate", ["recipe": "system.volume_set", "params": ["level": 50]],
                     approval: "aaaaaaaa-8f7e-4d6c-9b5a-4f3e2d1c0b0a"),
            "Ses düzeyi 50 yapılsın mı?\nTarif: Ses düzeyi\nOnay: aaaaaaaa")
        XCTAssertEqual(try text("apps.open", "confirm", ["bundle_id": "com.apple.Terminal"]),
                       "com.apple.Terminal uygulaması açılsın mı?")
        // A slot-shaped value is data: it is never filled again.
        XCTAssertEqual(
            try text("ax.type_text", "gate", ["bundle_id": "com.apple.Notes", "text": "{bundle_id} merhaba", "submit": false],
                     approval: "bbbbbbbb-0000-4000-8000-000000000000"),
            "com.apple.Notes uygulamasına şu metin yazılsın mı: {bundle_id} merhaba\nOnay: bbbbbbbb")
    }

    func testTemplateSlots() {
        XCTAssertEqual(ConfirmationRenderer.slots("{repo_path} reposunda '{command_kind}'"), ["repo_path", "command_kind"])
        XCTAssertNil(ConfirmationRenderer.slots("{Bad}"))
        XCTAssertNil(ConfirmationRenderer.slots("a { b"))
        XCTAssertNil(ConfirmationRenderer.slots(""))
        for contract in CapabilityContract.actions {
            XCTAssertNotNil(ConfirmationRenderer.slots(contract.confirmTemplate ?? ""), contract.name)
        }
        for recipe in RecipeContract.all.values {
            XCTAssertNotNil(ConfirmationRenderer.slots(recipe.confirmTemplate), recipe.name)
        }
    }
}

final class ConfirmationWindowModelTests: XCTestCase {
    private func prompt(_ id: Int, shownAt: Int64) -> ConfirmationPrompt {
        ConfirmationRenderer.prompt(
            envelope: ["capability": "apps.open", "class": "confirm", "args": ["bundle_id": "com.apple.Terminal"],
                       "invocation_id": .string(V2Frames.uuid(id)), "grant": nil],
            resolved: nil, shownAt: shownAt)
    }

    func testOneWindowAtATimeAndTheSixtySecondTimeout() {
        let window = ConfirmationWindowModel()
        XCTAssertNoThrow(try window.open(prompt(1, shownAt: 100)))
        XCTAssertThrowsError(try window.open(prompt(2, shownAt: 101))) { XCTAssertEqual($0 as? WindowBusy, WindowBusy()) }
        XCTAssertNil(window.poll(now: 159))
        XCTAssertEqual(window.poll(now: 160), "timed_out")
        XCTAssertFalse(window.isOpen)
        XCTAssertEqual(window.shown.count, 1)
    }

    func testTheFirstClickBeforeTheDeadlineCounts() {
        let window = ConfirmationWindowModel()
        try? window.open(prompt(1, shownAt: 100))
        XCTAssertFalse(window.decide(.approve, invocationID: V2Frames.uuid(2), now: 101), "another invocation")
        XCTAssertTrue(window.decide(.deny, invocationID: V2Frames.uuid(1), now: 102))
        XCTAssertFalse(window.decide(.approve, invocationID: V2Frames.uuid(1), now: 103), "only the first click")
        XCTAssertEqual(window.poll(now: 103), "denied")
        try? window.open(prompt(3, shownAt: 200))
        XCTAssertFalse(window.decide(.approve, invocationID: V2Frames.uuid(3), now: 260), "at the deadline")
        XCTAssertEqual(window.poll(now: 260), "timed_out")
    }

    func testAForcedCloseHasNoDecision() {
        let window = ConfirmationWindowModel()
        try? window.open(prompt(1, shownAt: 0))
        window.forceClose("kill_switch")
        XCTAssertFalse(window.isOpen)
        XCTAssertNil(window.poll(now: 1000))
        XCTAssertEqual(window.closedBy, ["kill_switch"])
    }

    func testThePanelViewIsDerivedFromTheNodesOwnPrompt() {
        let asking = ConfirmationPanelView.make(prompt: prompt(1, shownAt: 100), running: nil, now: 115)
        XCTAssertEqual(asking.mode, .asking)
        XCTAssertEqual(asking.buttons, ["Çalıştır", "Reddet"])
        XCTAssertEqual(asking.secondsLeft, 45)
        XCTAssertEqual(asking.countdown, "45 sn")
        XCTAssertEqual(asking.lines, ["com.apple.Terminal uygulaması açılsın mı?"])
        XCTAssertEqual(ConfirmationPanelView.make(prompt: prompt(1, shownAt: 100), running: nil, now: 200).secondsLeft, 0)
        let running = ConfirmationPanelView.make(prompt: nil, running: prompt(2, shownAt: 100), now: 120)
        XCTAssertEqual(running.mode, .running)
        XCTAssertEqual(running.buttons, ["Durdur"])
        XCTAssertEqual(running.invocationID, V2Frames.uuid(2))
        XCTAssertEqual(ConfirmationPanelView.make(prompt: nil, running: nil, now: 0), .hidden)
        // One window at a time: an open window wins over a running command.
        XCTAssertEqual(ConfirmationPanelView.make(prompt: prompt(3, shownAt: 100), running: prompt(2, shownAt: 90), now: 100).mode, .asking)
    }
}

#if DEBUG

/// The session flows the AppKit panel drives.
final class ConfirmationSessionTests: XCTestCase {
    final class NoReads: ReadProvider {
        func read(capability: String, args: JSONValue, now: Int64) -> ReadOutcome { .failure }
    }

    final class Recorder: ConfirmationObserver {
        var events: [String] = []
        func confirmationShown(_ prompt: ConfirmationPrompt) { events.append("shown") }
        func confirmationClosed(invocationID: String, reason: String) { events.append("closed:\(reason)") }
        func commandStarted(invocationID: String) { events.append("started") }
        func commandEnded(invocationID: String) { events.append("ended") }
    }

    let start: Int64 = 1_796_000_000
    var clock: ManualClock!
    var executor: ScriptedExecutor!
    var recorder: Recorder!

    private func session(scripts: [CommandScript] = [], executor custom: ActionExecutor? = nil) throws -> NodeSession {
        clock = ManualClock(start + 5)
        executor = ScriptedExecutor(clock: clock, scripts: scripts)
        recorder = Recorder()
        let home = try ConformanceDriver.home()
        let chosen = custom ?? executor!
        var availability: [String: String] = [:]
        for contract in CapabilityContract.actions { availability[contract.name] = chosen.availability(for: contract.name) }
        let session = NodeSession(
            nodeID: V2Frames.nodeID, pinned: try V2Frames.pinned(), catalogue: .standard(availability: availability, actions: true),
            readers: NoReads(), clock: clock, nodeVersion: "test", osVersion: "test",
            policy: try ConformanceDriver.actionPolicy(home: home), executor: chosen)
        session.confirmationObserver = recorder
        _ = session.connect(version: 2)
        _ = session.receive(try V2Frames.helloAck())
        return session
    }

    private func command(_ n: Int) throws -> Data {
        try V2Frames.invoke("repo.command.run", "gate", args: ["repo_path": "~/work/acme", "command_kind": "test"], n: n, issuedAt: start)
    }

    private func kinds(_ frames: [JSONValue]) -> [String] {
        frames.filter { $0["type"] != "heartbeat" }.map { frame in
            ["type", "state", "status", "refusal_code"].compactMap { frame[$0]?.stringValue }.joined(separator: ":")
        }
    }

    func testAV2HelloCarriesTheActionEntriesAndAV1HelloDoesNot() throws {
        let session = try session()
        let v2 = try XCTUnwrap(session.hello()["catalogue"]?.arrayValue)
        XCTAssertEqual(v2.count, 16)
        _ = session.connect(version: 1)
        let v1 = try XCTUnwrap(session.hello()["catalogue"]?.arrayValue)
        XCTAssertEqual(v1.count, 10)
        XCTAssertTrue(v1.allSatisfy { $0["class"] == "read" })
        XCTAssertEqual(session.hello()["v"], 1)
    }

    func testAClickRunsTheCommandAndStreamsItsScrubbedOutput() throws {
        let session = try session(scripts: [CommandScript(chunks: [("stdout", Data("ok 1\n".utf8), 0)], exitCode: 0, duration: 2)])
        var frames = session.receive(try command(1))
        XCTAssertEqual(kinds(frames), ["confirmation:shown"])
        XCTAssertEqual(session.openPrompt?.approvalPrefix, "3c2b1a09")
        XCTAssertEqual(session.openPrompt?.repoName, "acme")
        clock.advance(3)
        XCTAssertTrue(session.decide(.approve, invocationID: V2Frames.uuid(1001)))
        frames = session.tick()
        XCTAssertEqual(kinds(frames), ["confirmation:approved", "output"])
        XCTAssertEqual(session.runningPrompt?.invocationID, V2Frames.uuid(1001))
        clock.advance(2)
        frames = session.tick()
        XCTAssertEqual(kinds(frames), ["result:ok"])
        XCTAssertEqual(frames.last?["confirm_state"], "approved")
        XCTAssertEqual(frames.last?["exit_code"], 0)
        XCTAssertEqual(frames.last?["tail"], "ok 1\n")
        XCTAssertEqual(recorder.events, ["shown", "closed:approved", "started", "ended"])
        XCTAssertEqual(executor.calls.first?.argv, ["python3", "-c", "print('acme tests ok')"])
        XCTAssertEqual(executor.calls.first?.spec?.wallSeconds, 300)
        XCTAssertNil(session.runningPrompt)
    }

    func testDurdurStopsTheRunningCommand() throws {
        let session = try session(scripts: [CommandScript(chunks: [("stdout", Data("started\n".utf8), 0)], hang: true)])
        _ = session.receive(try command(1))
        session.decide(.approve, invocationID: V2Frames.uuid(1001))
        XCTAssertEqual(kinds(session.tick()), ["confirmation:approved", "output"])
        XCTAssertEqual(kinds(session.stop(invocationID: V2Frames.uuid(9))), [], "another invocation")
        let frames = session.stop(invocationID: V2Frames.uuid(1001))
        XCTAssertEqual(kinds(frames), ["result:error"])
        XCTAssertEqual(frames.last?["exit_code"], .null)
        XCTAssertEqual(executor.terminatedCount, 1)
        XCTAssertEqual(recorder.events.last, "ended")
    }

    func testAClickAfterTheDeadlineIsATimeout() throws {
        let session = try session()
        _ = session.receive(try command(1))
        clock.advance(60)
        XCTAssertFalse(session.decide(.approve, invocationID: V2Frames.uuid(1001)))
        XCTAssertEqual(kinds(session.tick()), ["confirmation:timed_out", "result:refused:confirm_timeout"])
        XCTAssertTrue(executor.calls.isEmpty)
    }

    func testTheLocalKillSwitchClosesTheWindowAndRefusesLaterClicks() throws {
        let session = try session()
        _ = session.receive(try command(1))
        XCTAssertEqual(kinds(session.engageKillSwitch()), ["confirmation:closed", "result:refused:owner_denied"])
        XCTAssertNil(session.openPrompt)
        XCTAssertEqual(recorder.events, ["shown", "closed:kill_switch"])
        // A later window still opens, but no click runs anything while engaged.
        _ = session.receive(try command(2))
        session.decide(.approve, invocationID: V2Frames.uuid(1002))
        XCTAssertEqual(kinds(session.tick()), ["confirmation:approved", "result:refused:expired_after_confirm"])
        session.releaseKillSwitch()
        _ = session.receive(try command(3))
        session.decide(.approve, invocationID: V2Frames.uuid(1003))
        XCTAssertEqual(kinds(session.tick()).prefix(2), ["confirmation:approved", "output"])
        XCTAssertEqual(executor.calls.count, 1)
    }

    func testSocketLossClosesTheWindowAndSendsNothingLate() throws {
        let session = try session()
        _ = session.receive(try command(1))
        session.disconnect()
        XCTAssertEqual(recorder.events, ["shown", "closed:disconnect"])
        XCTAssertFalse(session.decide(.approve, invocationID: V2Frames.uuid(1001)))
        XCTAssertEqual(session.tick(), [])
        XCTAssertTrue(executor.calls.isEmpty)
    }

    /// The release app until runbook B6: every action is `unsupported`, so
    /// check 21 refuses before any window opens.
    func testWithoutAnExecutorNothingIsShown() throws {
        let session = try session(executor: UnavailableExecutor())
        let frames = session.receive(try command(1))
        XCTAssertEqual(kinds(frames), ["result:refused:not_granted"])
        XCTAssertTrue(recorder.events.isEmpty)
        XCTAssertTrue(session.window.shown.isEmpty)
    }

    /// The worktree mode: the command runs in a throwaway worktree outside
    /// the repo, which is gone afterwards.
    func testWorktreeModeRunsOutsideTheRepoAndCleansUp() throws {
        let session = try session(scripts: [CommandScript(chunks: [("stdout", Data("lint ok\n".utf8), 0)])])
        let repo = try ConformanceDriver.home() + "/work/acme"
        let before = try Worktree.list(repo).count
        _ = session.receive(try V2Frames.invoke(
            "repo.command.run", "gate", args: ["repo_path": "~/work/acme", "command_kind": "lint"], n: 1, issuedAt: start))
        session.decide(.approve, invocationID: V2Frames.uuid(1001))
        XCTAssertEqual(kinds(session.tick()), ["confirmation:approved", "output", "result:ok"])
        let spec = try XCTUnwrap(executor.calls.first?.spec)
        XCTAssertEqual(spec.mode, "worktree")
        XCTAssertNotEqual(spec.cwd, spec.repoRoot)
        XCTAssertFalse(spec.cwd.hasPrefix(spec.repoRoot + "/"))
        XCTAssertFalse(FileManager.default.fileExists(atPath: spec.cwd))
        XCTAssertFalse(FileManager.default.fileExists(atPath: spec.tempDir))
        XCTAssertEqual(try Worktree.list(repo).count, before)
    }

    /// The checker can fail: a changed expectation is reported.
    func testTheOfflineCheckerReportsAMismatch() throws {
        let path = "backend/nodes/conformance/cases/v2/v2_owner_denies.json"
        var document = try XCTUnwrap(try Repo.json(path).objectValue)
        var expect = try XCTUnwrap(document["expect"]?.objectValue)
        var frames = try XCTUnwrap(expect["frames"]?.arrayValue)
        var last = try XCTUnwrap(frames.last?.objectValue)
        last["refusal_code"] = "confirm_timeout"
        frames[frames.count - 1] = .object(last)
        expect["frames"] = .array(frames)
        document["expect"] = .object(expect)
        let driver = try ConformanceDriver(case: .object(document))
        try driver.play(.object(document))
        XCTAssertFalse(try driver.failures(.object(document)).isEmpty)
    }
}

#endif
