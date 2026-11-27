// Runs one mapped command under `srt` (Anthropic sandbox-runtime, Seatbelt
// on macOS) with the settings document of SandboxPolicy (Phase 10 brief D4,
// D5 check 24; runbook B6; docs/mcp/dependencies/node-gateway.md `srt`).
//
// The invocation, by absolute paths only (srt is not on any PATH, and its
// `#!/usr/bin/env node` shebang would search the stripped PATH):
//
//     <node> <prefix>/node_modules/@anthropic-ai/sandbox-runtime/dist/cli.js \
//         --settings <private dir>/settings.json -- <mapped argv...>
//
// - `--settings` is always given (srt refuses to fall back to its built-in
//   defaults when the file named is missing, empty or invalid).
// - The settings file is the canonical document, written 0600 into a fresh
//   0700 directory outside every writable root, and removed after the run.
// - The environment is the document's stripped allowlist, plus
//   `CLAUDE_CODE_TMPDIR` = the run's private temp dir: srt hands the
//   sandboxed command `TMPDIR=/tmp/claude` unless told otherwise, and the
//   command's TMPDIR must be its own private dir (D4).
// - srt runs the argv through `bash`, found with `which` on the child's
//   PATH, quoting it itself; CommandMappings refuses a mapped PATH without
//   `/bin`.
// - Network goes through srt's own proxy: with no allowed domain an HTTPS
//   CONNECT is answered 403.
// - The process is spawned in its own group (ChildProcess: argv only,
//   POSIX_SPAWN_CLOEXEC_DEFAULT, stdin /dev/null, stdout/stderr pipes).
//   `terminate()` sends SIGTERM to the group and SIGKILL to the whole tree
//   after the grace (5 s), without blocking the caller; TreeMonitor enforces
//   `max_processes` and `cpu_seconds` (not RLIMIT_NPROC: see
//   ProcessTree.swift).
// - Output past the 1 MiB cap is counted, not kept (`droppedBytes`).
//
// The srt location is the owner's choice in the policy UI (`SrtLocation`,
// stored with the command mappings); the defaults are what runbook A6
// installed and measured: `/usr/local/bin/node` (Node >= 22.12) and the
// private prefix `~/Library/Application Support/Friday/srt` holding
// `@anthropic-ai/sandbox-runtime` 0.0.83, the pinned version. A location
// whose package is another version is `unsupported`.

import Darwin
import Foundation

/// Where `srt` lives: the Node binary and the npm prefix.
public struct SrtLocation: Equatable, Sendable {
    public static let pinnedVersion = "0.0.83"
    public static let minimumNode = (major: 22, minor: 12)
    public static let defaultNode = "/usr/local/bin/node"
    public static var defaultPrefix: String { NSHomeDirectory() + "/Library/Application Support/Friday/srt" }
    static let packagePath = "node_modules/@anthropic-ai/sandbox-runtime"

    public var node: String
    public var prefix: String

    public init(node: String = SrtLocation.defaultNode, prefix: String = SrtLocation.defaultPrefix) {
        self.node = node
        self.prefix = prefix
    }

    public var cliPath: String { prefix + "/" + Self.packagePath + "/dist/cli.js" }
    var packageJSON: String { prefix + "/" + Self.packagePath + "/package.json" }

    public var document: JSONValue { ["node": .string(node), "prefix": .string(prefix)] }

    public static func from(_ value: JSONValue?) -> SrtLocation? {
        guard let node = value?["node"]?.stringValue, let prefix = value?["prefix"]?.stringValue else { return nil }
        return SrtLocation(node: node, prefix: prefix)
    }

    /// Why this location cannot run commands, or nil when it can: absolute
    /// paths, an executable Node >= 22.12, the CLI present, the package at
    /// the pinned version.
    public func problem() -> String? {
        guard node.hasPrefix("/"), prefix.hasPrefix("/"), !node.contains("\0"), !prefix.contains("\0") else {
            return "srt paths must be absolute"
        }
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: node, isDirectory: &isDirectory), !isDirectory.boolValue,
            FileManager.default.isExecutableFile(atPath: node)
        else { return "node not found" }
        guard FileManager.default.fileExists(atPath: cliPath) else { return "srt cli.js not found" }
        guard let data = FileManager.default.contents(atPath: packageJSON),
            let version = (try? JSON.parse(data))?["version"]?.stringValue
        else { return "srt package.json unreadable" }
        guard version == Self.pinnedVersion else { return "srt \(version) is not the pinned \(Self.pinnedVersion)" }
        guard let nodeVersion = NodeVersionCache.shared.version(of: node) else { return "node --version failed" }
        guard Self.nodeIsRecentEnough(nodeVersion) else { return "node \(nodeVersion) is older than 22.12" }
        return nil
    }

    /// "v24.14.1" >= 22.12.
    public static func nodeIsRecentEnough(_ version: String) -> Bool {
        let parts = version.trimmingCharacters(in: .whitespacesAndNewlines).drop(while: { $0 == "v" })
            .split(separator: ".").compactMap { Int($0) }
        guard parts.count >= 2 else { return false }
        return (parts[0], parts[1]) >= (minimumNode.major, minimumNode.minor)
    }
}

/// `node --version` per binary, asked once (it is spawned with an empty
/// environment and a 10 s timeout).
final class NodeVersionCache: @unchecked Sendable {
    static let shared = NodeVersionCache()
    private let lock = NSLock()
    private var versions: [String: String] = [:]

    func version(of node: String) -> String? {
        lock.lock()
        if let known = versions[node] {
            lock.unlock()
            return known
        }
        lock.unlock()
        guard let outcome = try? BoundedRun.run(
            executable: node, argv: [node, "--version"], environment: [:], timeout: 10),
            outcome.exitCode == 0
        else { return nil }
        let version = String(decoding: outcome.stdout, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
        lock.lock()
        versions[node] = version
        lock.unlock()
        return version
    }
}

/// The limits one run enforces itself (the document's `limits`).
public struct RunLimits: Equatable, Sendable {
    public let maxProcesses: Int
    public let cpuSeconds: Double
    public let maxOutputBytes: Int
    public let killGraceSeconds: Double

    public init(maxProcesses: Int, cpuSeconds: Double, maxOutputBytes: Int, killGraceSeconds: Double) {
        self.maxProcesses = maxProcesses
        self.cpuSeconds = cpuSeconds
        self.maxOutputBytes = maxOutputBytes
        self.killGraceSeconds = killGraceSeconds
    }

    public init(_ sandbox: SandboxDocument) {
        self.init(
            maxProcesses: Int(sandbox.maxProcesses), cpuSeconds: Double(sandbox.cpuSeconds),
            maxOutputBytes: Int(sandbox.maxOutputBytes), killGraceSeconds: Double(sandbox.killGraceSeconds))
    }
}

public final class SrtExecutor: @unchecked Sendable {
    public let location: SrtLocation

    public init(location: SrtLocation = SrtLocation()) {
        self.location = location
    }

    public var available: Bool { location.problem() == nil }

    /// The exact argv srt is started with (tests, the README).
    public func argv(settingsPath: String, command: [String]) -> [String] {
        [location.node, location.cliPath, "--settings", settingsPath, "--"] + command
    }

    /// Start `spec` under srt; throws (nothing runs) when srt is not usable,
    /// the spec carries no sandbox, or the working directory no longer
    /// resolves to the write root.
    public func start(_ spec: CommandSpec) throws -> RunningCommand {
        guard let sandbox = spec.sandbox else { throw ExecutorError("no sandbox document") }
        if let problem = location.problem() { throw ExecutorError(problem) }
        // The write root was resolved before the window; it must still be
        // the same directory now (no symlink swapped in meanwhile).
        guard ReaderPaths.realpathC(sandbox.cwd) == sandbox.writeRoot, sandbox.cwd == sandbox.writeRoot else {
            throw ExecutorError("the working directory moved")
        }
        let settingsDir = try TempDirectory.make(prefix: "friday-srt-")
        let settingsPath = settingsDir + "/settings.json"
        do {
            let bytes = try sandbox.settingsBytes()
            guard FileManager.default.createFile(
                atPath: settingsPath, contents: bytes, attributes: [.posixPermissions: 0o600])
            else { throw ExecutorError("settings file not written") }
            var environment = sandbox.environment
            environment["CLAUDE_CODE_TMPDIR"] = sandbox.tempDir
            return try SandboxRun.start(
                executable: location.node, argv: argv(settingsPath: settingsPath, command: sandbox.argv),
                environment: environment, cwd: sandbox.cwd, limits: RunLimits(sandbox), cleanupDirectory: settingsDir)
        } catch {
            TempDirectory.remove(settingsDir)
            throw error
        }
    }
}

/// One running process tree: output pumped off two pipes, the limits
/// enforced, a non-blocking stop.
public final class SandboxRun: RunningCommand, @unchecked Sendable {
    /// After the root exited, how long the pipes may stay open (a child
    /// re-parented out of reach) before the run counts as finished anyway.
    static let drainGraceSeconds: Double = 3

    private let child: ChildProcess
    private let monitor: TreeMonitor
    private let limits: RunLimits
    private let cleanupDirectory: String?
    private let lock = NSLock()
    private var pending: [OutputChunk] = []
    private var keptBytes = 0
    private var openPipes = 0
    private var rootExited = false
    private var finished = false
    private var finishHandlers: [() -> Void] = []
    private var terminating = false

    private init(child: ChildProcess, limits: RunLimits, cleanupDirectory: String?) {
        self.child = child
        self.limits = limits
        self.cleanupDirectory = cleanupDirectory
        monitor = TreeMonitor(
            root: child.pid, group: child.pid, maxProcesses: limits.maxProcesses, cpuSeconds: limits.cpuSeconds)
    }

    /// Spawn `argv` in a new process group and start pumping and watching.
    public static func start(
        executable: String, argv: [String], environment: [String: String], cwd: String, limits: RunLimits,
        cleanupDirectory: String? = nil
    ) throws -> SandboxRun {
        let child = try ChildProcess.spawn(
            executable: executable, argv: argv, environment: environment, cwd: cwd, stdout: .pipe, stderr: .pipe,
            newProcessGroup: true)
        let run = SandboxRun(child: child, limits: limits, cleanupDirectory: cleanupDirectory)
        run.begin()
        return run
    }

    public var pid: pid_t { child.pid }

    private func begin() {
        monitor.start()
        let pipes = [("stdout", child.stdoutFD), ("stderr", child.stderrFD)].compactMap { name, fd in
            fd.map { (name, $0) }
        }
        lock.lock()
        openPipes = pipes.count
        lock.unlock()
        for (stream, fd) in pipes {
            let thread = Thread { [self] in
                ChildProcess.drain(fd) { self.received(stream, $0) }
                self.pipeClosed()
            }
            thread.name = "friday-node.output"
            thread.start()
        }
        child.onExit { [weak self] in
            guard let self else { return }
            self.monitor.rootDidExit()
            self.lock.lock()
            self.rootExited = true
            self.lock.unlock()
            self.checkFinished()
            DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + Self.drainGraceSeconds) {
                self.checkFinished(force: true)
            }
        }
    }

    private func received(_ stream: String, _ data: Data) {
        lock.lock()
        defer { lock.unlock() }
        let room = max(limits.maxOutputBytes - keptBytes, 0)
        let kept = data.prefix(room)
        keptBytes += kept.count
        pending.append(OutputChunk(stream: stream, data: Data(kept), droppedBytes: Int64(data.count - kept.count)))
    }

    private func pipeClosed() {
        lock.lock()
        openPipes -= 1
        lock.unlock()
        checkFinished()
    }

    private func checkFinished(force: Bool = false) {
        lock.lock()
        guard !finished, rootExited, openPipes == 0 || force else {
            lock.unlock()
            return
        }
        finished = true
        let handlers = finishHandlers
        finishHandlers = []
        lock.unlock()
        monitor.stop()
        TempDirectory.remove(cleanupDirectory)
        for handler in handlers { DispatchQueue.global(qos: .utility).async(execute: handler) }
    }

    public func read() -> [OutputChunk] {
        lock.lock()
        defer { lock.unlock() }
        let chunks = pending
        pending = []
        return chunks
    }

    public func poll() -> CommandOutcome? {
        lock.lock()
        let done = finished
        lock.unlock()
        guard done else { return nil }
        if let broken = monitor.brokenLimit { return CommandOutcome(exitCode: child.exitCode, error: broken) }
        return CommandOutcome(exitCode: child.exitCode)
    }

    /// The limit the run broke (`max_processes`, `cpu_seconds`), if any.
    public var brokenLimit: String? { monitor.brokenLimit }

    public func terminate() {
        lock.lock()
        guard !terminating, !finished else {
            lock.unlock()
            return
        }
        terminating = true
        lock.unlock()
        child.signal(SIGTERM)
        DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + limits.killGraceSeconds) { [self] in
            self.monitor.killTree()
            self.child.signal(SIGKILL)
        }
    }

    public var hasExited: Bool {
        lock.lock()
        defer { lock.unlock() }
        return finished
    }

    /// `work` runs on a utility queue once the tree is gone (never on the
    /// caller's queue, so removing a worktree never blocks the session).
    public func afterExit(_ work: @escaping () -> Void) {
        lock.lock()
        if finished {
            lock.unlock()
            DispatchQueue.global(qos: .utility).async(execute: work)
            return
        }
        finishHandlers.append(work)
        lock.unlock()
    }
}
