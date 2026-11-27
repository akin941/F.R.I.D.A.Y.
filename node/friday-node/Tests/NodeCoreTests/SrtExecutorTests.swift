// The process side of the executors (runbook B6; B5 review findings 1, 4
// and the cleanup NIT): spawning, output, the non-blocking stop, the
// process-tree limits, CLOEXEC, the bounded helper runs the kill switch can
// stop, git without the repo's executables, and (when srt is installed at
// the configured location) real `srt` runs in a temp dir: a write inside
// succeeds; a write outside, a denied read and the network are refused.

import Foundation
import NodeCore
import XCTest

enum Waiting {
    /// Pump `run` until it finishes (or `timeout`), returning its output.
    static func finish(_ run: RunningCommand, timeout: Double = 30) -> (String, CommandOutcome?) {
        var output = Data()
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            for chunk in run.read() { output.append(chunk.data) }
            if let outcome = run.poll() {
                for chunk in run.read() { output.append(chunk.data) }
                return (String(decoding: output, as: UTF8.self), outcome)
            }
            Thread.sleep(forTimeInterval: 0.02)
        }
        return (String(decoding: output, as: UTF8.self), nil)
    }

    static func until(_ timeout: Double, _ condition: () -> Bool) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if condition() { return true }
            Thread.sleep(forTimeInterval: 0.02)
        }
        return condition()
    }
}

final class SandboxRunTests: XCTestCase {
    private let limits = RunLimits(maxProcesses: 32, cpuSeconds: 30, maxOutputBytes: 1 << 20, killGraceSeconds: 1)

    private func sh(_ script: String, limits: RunLimits? = nil, env: [String: String] = ["PATH": "/usr/bin:/bin"])
        throws -> SandboxRun
    {
        try SandboxRun.start(
            executable: "/bin/sh", argv: ["sh", "-c", script], environment: env, cwd: NSTemporaryDirectory(),
            limits: limits ?? self.limits)
    }

    func testOutputExitCodeAndTheExactEnvironment() throws {
        let run = try sh("echo out; echo err >&2; env | sort; exit 3", env: ["PATH": "/usr/bin:/bin", "ONLY": "this"])
        let (output, outcome) = Waiting.finish(run)
        XCTAssertEqual(outcome?.exitCode, 3)
        XCTAssertTrue(output.contains("out\n"))
        XCTAssertTrue(output.contains("err\n"))
        XCTAssertTrue(output.contains("ONLY=this"))
        XCTAssertFalse(output.contains("HOME="), "nothing of the node's environment reaches the child")
        XCTAssertTrue(run.hasExited)
    }

    /// POSIX_SPAWN_CLOEXEC_DEFAULT: an inheritable descriptor of the node
    /// does not reach the child; stdin is /dev/null.
    func testNoDescriptorOfTheNodeLeaksIntoTheChild() throws {
        let path = NSTemporaryDirectory() + "friday-cloexec-\(UUID().uuidString)"
        FileManager.default.createFile(atPath: path, contents: Data("x".utf8))
        defer { try? FileManager.default.removeItem(atPath: path) }
        let fd = open(path, O_RDONLY)  // inheritable on purpose
        defer { close(fd) }
        XCTAssertGreaterThan(fd, 2)
        let run = try sh("for n in 3 4 5 6 7 8 9 \(fd); do if [ -e /dev/fd/$n ] && : <&$n 2>/dev/null; then echo open:$n; fi; done; cat; echo end")
        let (output, outcome) = Waiting.finish(run)
        XCTAssertEqual(outcome?.exitCode, 0)
        XCTAssertFalse(output.contains("open:\(fd)"), output)
        XCTAssertTrue(output.hasSuffix("end\n"), "stdin is /dev/null: cat returns at once")
    }

    /// The stop never blocks: SIGTERM at once, SIGKILL to the tree after the
    /// grace, also for a child that ignores SIGTERM.
    func testTerminateIsNonBlockingAndEndsWithSIGKILL() throws {
        let run = try sh("trap '' TERM; echo ready; while :; do sleep 1; done")
        XCTAssertTrue(Waiting.until(5) { !run.read().isEmpty })
        let started = Date()
        run.terminate()
        XCTAssertLessThan(Date().timeIntervalSince(started), 0.2)
        XCTAssertFalse(run.hasExited, "still alive during the grace")
        let (_, outcome) = Waiting.finish(run, timeout: 10)
        XCTAssertNotNil(outcome)
        XCTAssertTrue(run.hasExited)
    }

    /// Cleanup waits for the tree to be gone (B5 NIT): `afterExit` runs only
    /// after the stopped process exited.
    func testAfterExitWaitsForTheProcess() throws {
        let run = try sh("trap '' TERM; echo ready; while :; do sleep 1; done")
        XCTAssertTrue(Waiting.until(5) { !run.read().isEmpty }, "the trap is set")
        let ran = ExpectationFlag()
        run.afterExit { ran.set() }
        run.terminate()
        Thread.sleep(forTimeInterval: 0.3)
        XCTAssertFalse(ran.value, "the process is still in its grace")
        XCTAssertTrue(Waiting.until(10) { ran.value })
        XCTAssertTrue(run.hasExited)
    }

    /// `max_processes` is a tree count (not RLIMIT_NPROC): a run that keeps
    /// more processes alive than allowed is killed and reported.
    func testTheProcessLimitKillsTheTree() throws {
        let tight = RunLimits(maxProcesses: 5, cpuSeconds: 30, maxOutputBytes: 1 << 20, killGraceSeconds: 1)
        let run = try sh("for i in 1 2 3 4 5 6 7 8 9 10; do sleep 30 & done; wait", limits: tight)
        let (_, outcome) = Waiting.finish(run, timeout: 15)
        XCTAssertEqual(outcome?.error, "max_processes")
        XCTAssertEqual(run.brokenLimit, "max_processes")
        // A normal run under the same limit is untouched (sh + one child).
        let fine = try sh("sleep 0.3; echo ok", limits: tight)
        let (output, ok) = Waiting.finish(fine)
        XCTAssertNil(ok?.error)
        XCTAssertEqual(output, "ok\n")
    }

    func testTheCPULimitKillsTheTree() throws {
        let tight = RunLimits(maxProcesses: 8, cpuSeconds: 1, maxOutputBytes: 1 << 20, killGraceSeconds: 1)
        let run = try sh("while :; do :; done", limits: tight)
        let (_, outcome) = Waiting.finish(run, timeout: 20)
        XCTAssertEqual(outcome?.error, "cpu_seconds")
    }

    /// A background child left behind is killed once the root exits, so
    /// the pipes close and nothing outlives the run.
    func testNothingOutlivesTheRun() throws {
        let run = try SandboxRun.start(
            executable: "/bin/sh", argv: ["sh", "-c", "sleep 60 & echo $!"], environment: ["PATH": "/usr/bin:/bin"],
            cwd: NSTemporaryDirectory(), limits: limits)
        let (output, outcome) = Waiting.finish(run, timeout: 10)
        XCTAssertEqual(outcome?.exitCode, 0)
        let pid = pid_t(output.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 0
        XCTAssertGreaterThan(pid, 0)
        XCTAssertTrue(Waiting.until(5) { kill(pid, 0) != 0 }, "the background child is gone")
    }

    /// Output past the cap is counted, not kept.
    func testOutputPastTheCapIsCountedNotKept() throws {
        let small = RunLimits(maxProcesses: 8, cpuSeconds: 30, maxOutputBytes: 1000, killGraceSeconds: 1)
        let run = try sh("head -c 50000 /dev/zero | tr '\\0' y", limits: small)
        var kept = 0
        var dropped: Int64 = 0
        let deadline = Date().addingTimeInterval(10)
        while Date() < deadline {
            for chunk in run.read() {
                kept += chunk.data.count
                dropped += chunk.droppedBytes
            }
            if run.poll() != nil { break }
            Thread.sleep(forTimeInterval: 0.02)
        }
        for chunk in run.read() {
            kept += chunk.data.count
            dropped += chunk.droppedBytes
        }
        XCTAssertEqual(kept, 1000)
        XCTAssertEqual(Int64(kept) + dropped, 50000)
        let streamer = OutputStreamer()
        _ = streamer.feed("stdout", Data(repeating: 0x79, count: 10))
        _ = streamer.countDropped(5)
        XCTAssertEqual(streamer.outputBytes, 15)
        XCTAssertTrue(streamer.truncated)
    }
}

final class ExpectationFlag: @unchecked Sendable {
    private let lock = NSLock()
    private var flag = false
    func set() {
        lock.lock()
        flag = true
        lock.unlock()
    }
    var value: Bool {
        lock.lock()
        defer { lock.unlock() }
        return flag
    }
}

final class BoundedRunTests: XCTestCase {
    func testATimeoutKillsTheHelper() throws {
        let started = Date()
        let outcome = try BoundedRun.run(
            executable: "/bin/sleep", argv: ["sleep", "30"], environment: [:], timeout: 0.5)
        XCTAssertTrue(outcome.timedOut)
        XCTAssertLessThan(Date().timeIntervalSince(started), 5)
    }

    /// The kill switch stops a helper the session queue waits on, from
    /// another thread (B5 review finding 1).
    func testTheRegistryStopsARunningHelperFromAnotherThread() throws {
        let registry = ChildRegistry()
        DispatchQueue.global().asyncAfter(deadline: .now() + 0.3) {
            XCTAssertEqual(registry.killAll(), 1)
        }
        let started = Date()
        let outcome = try BoundedRun.run(
            executable: "/bin/sleep", argv: ["sleep", "30"], environment: [:], timeout: 60, registry: registry)
        XCTAssertLessThan(Date().timeIntervalSince(started), 5)
        XCTAssertEqual(outcome.exitCode, -Int64(SIGKILL))
        XCTAssertEqual(registry.count, 0)
    }
}

/// Git for the throwaway worktree: no hook, no fsmonitor, no filter driver
/// of the repo's config runs (B5 review finding 2).
final class WorktreeSafetyTests: XCTestCase {
    private func git(_ cwd: String, _ args: [String]) throws {
        let outcome = try BoundedRun.run(
            executable: "/usr/bin/git", argv: ["git", "-C", cwd] + args,
            environment: ["PATH": "/usr/bin:/bin", "HOME": "/var/empty", "GIT_CONFIG_GLOBAL": "/dev/null",
                          "GIT_CONFIG_NOSYSTEM": "1"],
            timeout: 30)
        XCTAssertEqual(outcome.exitCode, 0, args.joined(separator: " "))
    }

    func testNoRepoExecutableRunsDuringSetupAndTeardown() throws {
        guard FileManager.default.isExecutableFile(atPath: "/usr/bin/git") else { throw XCTSkip("no git") }
        let base = resolvedPath(try makeTempDirectory())
        defer { try? FileManager.default.removeItem(atPath: base) }
        let repo = base + "/repo"
        try FileManager.default.createDirectory(atPath: repo, withIntermediateDirectories: true)
        try git(repo, ["init", "-q"])
        try git(repo, ["config", "user.email", "t@example.invalid"])
        try git(repo, ["config", "user.name", "t"])
        try Data("*.txt filter=evil\n".utf8).write(to: URL(fileURLWithPath: repo + "/.gitattributes"))
        try Data("hello\n".utf8).write(to: URL(fileURLWithPath: repo + "/a.txt"))
        try git(repo, ["add", "."])
        try git(repo, ["commit", "-qm", "init"])
        let marker = base + "/RAN"
        try git(repo, ["config", "filter.evil.smudge", "sh -c 'touch \(marker); cat'"])
        try git(repo, ["config", "filter.evil.required", "true"])
        try git(repo, ["config", "core.fsmonitor", "sh -c 'touch \(marker)'"])
        try FileManager.default.createDirectory(atPath: repo + "/.git/hooks", withIntermediateDirectories: true)
        let hook = repo + "/.git/hooks/post-checkout"
        try Data("#!/bin/sh\ntouch \(marker)\n".utf8).write(to: URL(fileURLWithPath: hook))
        chmod(hook, 0o755)
        let worktreeBase = base + "/wts"
        try FileManager.default.createDirectory(atPath: worktreeBase, withIntermediateDirectories: true)
        let before = try Worktree.list(repo)
        let worktree = try Worktree.create(repo, base: worktreeBase)
        XCTAssertEqual(try String(contentsOfFile: worktree + "/a.txt", encoding: .utf8), "hello\n")
        Worktree.remove(repo, worktree)
        XCTAssertFalse(FileManager.default.fileExists(atPath: marker), "a repo executable ran")
        XCTAssertFalse(FileManager.default.fileExists(atPath: worktree))
        XCTAssertEqual(try Worktree.list(repo), before)
    }
}

func makeTempDirectory() throws -> String {
    var template = Array((NSTemporaryDirectory() + "friday-b6-XXXXXX").utf8CString)
    guard let made = mkdtemp(&template) else { throw ExecutorError("mkdtemp") }
    return String(cString: made)
}

func resolvedPath(_ path: String) -> String {
    guard let resolved = realpath(path, nil) else { return path }
    defer { free(resolved) }
    return String(cString: resolved)
}

/// Real `srt` (skipped when it is not installed at the configured
/// location): only a temp dir is written; nothing of the owner's is read.
final class RealSrtTests: XCTestCase {
    private func executor() throws -> SrtExecutor {
        let executor = SrtExecutor()
        if let problem = executor.location.problem() { throw XCTSkip("srt not usable: \(problem)") }
        return executor
    }

    private func run(_ script: String, _ executor: SrtExecutor, base: String, home: String, network: [String] = [])
        throws -> (String, CommandOutcome?)
    {
        let write = base + "/write"
        let temp = base + "/tmp"
        let mapping = CommandMapping(
            kind: "test", argv: ["/bin/sh", "-c", script], path: "/usr/bin:/bin", networkDomains: network,
            maxRuntimeSeconds: 60, mode: "in_place")
        let sandbox = try SandboxPolicy.build(
            command: mapping, home: home, repoRoot: write, writeRoot: write, tempDir: temp, nodeEnvironment: [:])
        let spec = CommandSpec(
            mode: "in_place", argv: mapping.argv, cwd: write, repoRoot: write, writeRoot: write, tempDir: temp,
            path: mapping.path, networkDomains: network, wallSeconds: 60, sandbox: sandbox)
        return Waiting.finish(try executor.start(spec), timeout: 60)
    }

    func testARealSrtRunWritesOnlyItsRootsAndReachesNothing() throws {
        let executor = try executor()
        let base = resolvedPath(try makeTempDirectory())
        defer { try? FileManager.default.removeItem(atPath: base) }
        let home = base + "/home"
        for directory in [base + "/write", base + "/tmp", home + "/.ssh"] {
            try FileManager.default.createDirectory(atPath: directory, withIntermediateDirectories: true)
        }
        chmod(base + "/tmp", 0o700)
        try Data("SECRET-CANARY\n".utf8).write(to: URL(fileURLWithPath: home + "/.ssh/id_test"))

        let (inside, okInside) = try run("echo hi > inside.txt && cat inside.txt && echo T=$TMPDIR", executor, base: base, home: home)
        XCTAssertEqual(okInside?.exitCode, 0, inside)
        XCTAssertTrue(inside.contains("hi\n"))
        XCTAssertTrue(inside.contains("T=\(base)/tmp"), "TMPDIR is the private temp dir, not /tmp/claude: \(inside)")

        let (_, outside) = try run("echo x > \(base)/outside.txt", executor, base: base, home: home)
        XCTAssertNotEqual(outside?.exitCode, 0)
        XCTAssertFalse(FileManager.default.fileExists(atPath: base + "/outside.txt"))

        let (_, gitDir) = try run("mkdir -p .git && echo x > .git/hook", executor, base: base, home: home)
        XCTAssertNotEqual(gitDir?.exitCode, 0, "the write root's .git is not writable")

        let (read, denied) = try run("cat \(home)/.ssh/id_test", executor, base: base, home: home)
        XCTAssertNotEqual(denied?.exitCode, 0)
        XCTAssertFalse(read.contains("SECRET-CANARY"))

        let (_, tmp) = try run("echo x > /tmp/friday-b6-escape-$$", executor, base: base, home: home)
        XCTAssertNotEqual(tmp?.exitCode, 0, "/tmp itself is not writable")

        let (net, network) = try run(
            "/usr/bin/curl -sS -m 10 -o /dev/null https://example.com", executor, base: base, home: home)
        XCTAssertNotEqual(network?.exitCode, 0, net)

        // No ripgrep on the child's PATH, and srt still runs (macOS needs none).
        let (rg, noRipgrep) = try run("command -v rg || echo no-rg", executor, base: base, home: home)
        XCTAssertEqual(noRipgrep?.exitCode, 0)
        XCTAssertTrue(rg.contains("no-rg"), rg)
    }

    func testAMissingSandboxDocumentOrAMovedRootIsRefused() throws {
        let executor = try executor()
        let spec = CommandSpec(
            mode: "in_place", argv: ["true"], cwd: "/x", repoRoot: "/x", writeRoot: "/x", tempDir: "/y", path: "/bin",
            networkDomains: [], wallSeconds: 10)
        XCTAssertThrowsError(try executor.start(spec))
    }

    func testTheInvocationIsAbsolutePathsAndAlwaysSettings() {
        let executor = SrtExecutor(location: SrtLocation(node: "/opt/node", prefix: "/opt/srt"))
        XCTAssertEqual(
            executor.argv(settingsPath: "/p/settings.json", command: ["uv", "run", "pytest"]),
            ["/opt/node", "/opt/srt/node_modules/@anthropic-ai/sandbox-runtime/dist/cli.js", "--settings",
             "/p/settings.json", "--", "uv", "run", "pytest"])
        XCTAssertTrue(SrtLocation.nodeIsRecentEnough("v24.14.1"))
        XCTAssertTrue(SrtLocation.nodeIsRecentEnough("v22.12.0"))
        XCTAssertFalse(SrtLocation.nodeIsRecentEnough("v22.11.9"))
        XCTAssertFalse(SrtLocation.nodeIsRecentEnough("garbage"))
        XCTAssertNotNil(SrtLocation(node: "relative/node", prefix: "/x").problem())
        XCTAssertNotNil(SrtLocation(node: "/nonexistent/node", prefix: "/x").problem())
    }
}
