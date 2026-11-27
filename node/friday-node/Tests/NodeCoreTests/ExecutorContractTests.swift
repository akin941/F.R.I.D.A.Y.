// Runbook B6 contract test 2 at the session level, and the session flows B6
// changed (B5 review findings 3, 4 and the cleanup NIT).
//
// - Every `cases/v2/args_*` case, played against the real `MacActionExecutor`
//   wired to spies (AppleScript runner, interpolator, AX, workspace): the
//   same refusals as the reference node, and not one value reaches
//   interpolation, not one script, press, keystroke, open or command runs.
// - The kill switch is the runner's: a new session (a re-pair) sees it.
// - A repo whose path was swapped for a symlink after the window opened runs
//   nothing.
// - A stopped command's worktree and temp dir are removed only once its
//   process is gone.
// - The spec carries the generated sandbox (stripped environment).
//
// DEBUG only (the conformance driver and the scripted doubles).

#if DEBUG

import Foundation
import NodeCore
import XCTest

/// Counts what reaches the executor.
final class SpyingExecutor: ActionExecutor {
    let inner: ActionExecutor
    private(set) var starts = 0
    private(set) var performs = 0
    init(_ inner: ActionExecutor) { self.inner = inner }
    func availability(for capability: String) -> String { inner.availability(for: capability) }
    func start(_ spec: CommandSpec) throws -> RunningCommand {
        starts += 1
        return try inner.start(spec)
    }
    func perform(_ action: ActionRequest) -> ActionOutcome {
        performs += 1
        return inner.perform(action)
    }
}

final class ExecutorContractTests: XCTestCase {
    struct Spies {
        let interpolator = SpyInterpolator()
        let scripts = SpyScriptRunner()
        let accessibility = FakeAccessibility()
        let workspace = FakeWorkspace()
        let executor: SpyingExecutor

        init() {
            let real = MacActionExecutor(
                srt: SrtExecutor(location: SrtLocation(node: "/nonexistent/node", prefix: "/nonexistent")),
                bundleIDs: ["com.apple.Terminal", "com.apple.Safari", "com.apple.Notes"],
                recipesEnabled: Set(RecipeContract.all.keys), permissions: GrantedProbe(), accessibility: accessibility,
                workspace: workspace, scripts: scripts, interpolator: interpolator)
            executor = SpyingExecutor(real)
        }

        var nothingReached: Bool {
            executor.starts == 0 && executor.performs == 0 && interpolator.values.isEmpty && scripts.sources.isEmpty
                && accessibility.pressed.isEmpty && accessibility.inserted.isEmpty && accessibility.keystrokes.isEmpty
                && workspace.opened.isEmpty && workspace.activated.isEmpty
        }
    }

    func testTheArgsFuzzCasesNeverReachAnExecutor() throws {
        let cases = try ConformanceCase.all(2).filter { $0.name.hasPrefix("v2_args_") }
        XCTAssertGreaterThanOrEqual(cases.count, 12)
        for (name, document) in cases {
            let spies = Spies()
            let driver = try ConformanceDriver(case: document, actionExecutor: spies.executor)
            try driver.play(document)
            XCTAssertEqual(try driver.failures(document), [], name)
            XCTAssertTrue(spies.nothingReached, "\(name) reached an executor")
            // Every one is refused at check 19 or 20 (or as a frame error).
            let results = driver.frames.filter { $0["type"] == "result" }
            XCTAssertFalse(results.isEmpty, name)
            XCTAssertTrue(results.allSatisfy { $0["status"] != "ok" }, name)
        }
    }

    /// Positive control: the same wiring does reach interpolation for an
    /// approved recipe, so the spies above can see a call.
    func testTheSpiesSeeAnApprovedRecipe() throws {
        let document = try XCTUnwrap(try ConformanceCase.all(2).first { $0.name == "v2_gate_recipe_after_click" }?.document)
        let spies = Spies()
        let driver = try ConformanceDriver(case: document, actionExecutor: spies.executor)
        try driver.play(document)
        XCTAssertEqual(spies.executor.performs, 1)
        XCTAssertEqual(spies.scripts.sources.count, 1)
    }

    // MARK: Session flows

    final class NoReads: ReadProvider {
        func read(capability: String, args: JSONValue, now: Int64) -> ReadOutcome { .failure }
    }

    let start: Int64 = 1_796_000_000

    private func session(
        policy: ActionPolicy, executor: ActionExecutor, killSwitch: KillSwitch = KillSwitch(),
        environment: [String: String] = [:]
    ) throws -> (NodeSession, ManualClock) {
        let clock = ManualClock(start + 5)
        var availability: [String: String] = [:]
        for contract in CapabilityContract.actions { availability[contract.name] = "granted" }
        let session = NodeSession(
            nodeID: V2Frames.nodeID, pinned: try V2Frames.pinned(),
            catalogue: .standard(availability: availability, actions: true), readers: NoReads(), clock: clock,
            nodeVersion: "test", osVersion: "test", policy: policy, executor: executor, killSwitch: killSwitch,
            nodeEnvironment: environment)
        _ = session.connect(version: 2)
        _ = session.receive(try V2Frames.helloAck())
        return (session, clock)
    }

    private func statuses(_ frames: [JSONValue]) -> [String] {
        frames.filter { $0["type"] == "result" || $0["type"] == "confirmation" }.map { frame in
            ["type", "state", "status", "refusal_code"].compactMap { frame[$0]?.stringValue }.joined(separator: ":")
        }
    }

    func testTheKillSwitchOutlivesTheSession() throws {
        let home = try ConformanceDriver.home()
        let shared = KillSwitch()
        let scripted = ScriptedExecutor(clock: ManualClock(0))
        let (first, _) = try session(policy: try ConformanceDriver.actionPolicy(home: home), executor: scripted, killSwitch: shared)
        _ = first.engageKillSwitch()
        first.disconnect()
        // A re-pair: a new session with the runner's kill switch.
        let (second, _) = try session(policy: try ConformanceDriver.actionPolicy(home: home), executor: scripted, killSwitch: shared)
        XCTAssertTrue(second.killSwitchEngaged)
        _ = second.receive(try V2Frames.invoke(
            "repo.command.run", "gate", args: ["repo_path": "~/work/acme", "command_kind": "test"], n: 1, issuedAt: start))
        second.decide(.approve, invocationID: V2Frames.uuid(1001))
        XCTAssertEqual(statuses(second.tick()), ["confirmation:approved", "result:refused:expired_after_confirm"])
        XCTAssertTrue(scripted.calls.isEmpty)
        shared.release()
        XCTAssertFalse(second.killSwitchEngaged)
    }

    /// Check 22 resolved the repo before the window; a symlink swapped in
    /// meanwhile is caught at execute time (B5 review finding 4).
    func testARepoSwappedForASymlinkAfterTheWindowRunsNothing() throws {
        let base = resolvedPath(try makeTempDirectory())
        defer { try? FileManager.default.removeItem(atPath: base) }
        let repo = base + "/proj"
        let elsewhere = base + "/elsewhere"
        for directory in [repo, elsewhere] {
            try FileManager.default.createDirectory(atPath: directory, withIntermediateDirectories: true)
        }
        let policy = try ActionPolicy.load(
            ["repos": [["name": "proj", "root": .string(repo), "commands": [["kind": "test", "argv": ["true"]]]]]],
            home: base)
        let scripted = ScriptedExecutor(clock: ManualClock(0))
        let (session, _) = try session(policy: policy, executor: scripted)
        _ = session.receive(try V2Frames.invoke(
            "repo.command.run", "gate", args: ["repo_path": .string(repo), "command_kind": "test"], n: 1, issuedAt: start))
        XCTAssertNotNil(session.openPrompt)
        try FileManager.default.moveItem(atPath: repo, toPath: base + "/moved")
        try FileManager.default.createSymbolicLink(atPath: repo, withDestinationPath: elsewhere)
        session.decide(.approve, invocationID: V2Frames.uuid(1001))
        XCTAssertEqual(statuses(session.tick()), ["confirmation:approved", "result:error"])
        XCTAssertTrue(scripted.calls.isEmpty, "nothing ran in the swapped directory")
    }

    /// A command that is told to stop keeps its files until it is gone.
    final class SlowExit: ActionExecutor {
        final class Run: RunningCommand {
            var exited = false
            var pending: [() -> Void] = []
            func read() -> [OutputChunk] { [] }
            func poll() -> CommandOutcome? { exited ? CommandOutcome(exitCode: -15) : nil }
            func terminate() {}
            var hasExited: Bool { exited }
            func afterExit(_ work: @escaping () -> Void) {
                if exited { work() } else { pending.append(work) }
            }
            func exit() {
                exited = true
                for work in pending { work() }
                pending = []
            }
        }
        var run = Run()
        var spec: CommandSpec?
        func availability(for capability: String) -> String { "granted" }
        func start(_ spec: CommandSpec) throws -> RunningCommand {
            self.spec = spec
            return run
        }
        func perform(_ action: ActionRequest) -> ActionOutcome { ActionOutcome(ok: true) }
    }

    func testCleanupWaitsUntilTheStoppedCommandExited() throws {
        let home = try ConformanceDriver.home()
        let slow = SlowExit()
        let (session, _) = try session(policy: try ConformanceDriver.actionPolicy(home: home), executor: slow)
        _ = session.receive(try V2Frames.invoke(
            "repo.command.run", "gate", args: ["repo_path": "~/work/acme", "command_kind": "test"], n: 1, issuedAt: start))
        session.decide(.approve, invocationID: V2Frames.uuid(1001))
        _ = session.tick()
        let temp = try XCTUnwrap(slow.spec?.tempDir)
        XCTAssertEqual(statuses(session.stop(invocationID: V2Frames.uuid(1001))), ["result:error"])
        XCTAssertTrue(FileManager.default.fileExists(atPath: temp), "removed under a live process")
        slow.run.exit()
        XCTAssertFalse(FileManager.default.fileExists(atPath: temp))
    }

    /// The spec carries the sandbox SandboxPolicy generates: no secret name
    /// of the node's environment reaches it, the private temp dir is TMPDIR.
    func testTheSpecCarriesTheGeneratedSandbox() throws {
        let home = try ConformanceDriver.home()
        let scripted = ScriptedExecutor(clock: ManualClock(0))
        let (session, _) = try session(
            policy: try ConformanceDriver.actionPolicy(home: home), executor: scripted,
            environment: ["GITHUB_TOKEN": "ghp_canary", "PYTHONHASHSEED": "0", "AWS_SECRET_ACCESS_KEY": "x"])
        _ = session.receive(try V2Frames.invoke(
            "repo.command.run", "gate", args: ["repo_path": "~/work/acme", "command_kind": "test"], n: 1, issuedAt: start))
        session.decide(.approve, invocationID: V2Frames.uuid(1001))
        _ = session.tick()
        let spec = try XCTUnwrap(scripted.calls.first?.spec)
        let sandbox = try XCTUnwrap(spec.sandbox)
        XCTAssertEqual(sandbox.environment["TMPDIR"], spec.tempDir)
        XCTAssertNil(sandbox.environment["GITHUB_TOKEN"])
        XCTAssertNil(sandbox.environment["AWS_SECRET_ACCESS_KEY"])
        XCTAssertEqual(sandbox.argv, spec.argv)
        XCTAssertEqual(sandbox.settings["filesystem"]?["allowWrite"], [.string(spec.writeRoot), .string(spec.tempDir)])
        XCTAssertEqual(sandbox.wallSeconds, spec.wallSeconds)
    }
}

#endif
