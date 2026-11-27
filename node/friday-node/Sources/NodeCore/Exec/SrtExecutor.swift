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
//
// The version string is not trusted alone (B6 review finding 6): the prefix
// is user-writable. Setting or re-confirming the location (Touch ID, in the
// policy UI) stores in the keychain item the sha256 of the Node binary, of
// the whole `<prefix>/node_modules` tree (cli.js, dist and every dependency)
// and of `<prefix>/package-lock.json`; `problem()` recomputes them (cached
// per file by inode, size, mtime and ctime) before every start and every
// availability answer, and any difference is `unsupported` until the owner
// re-confirms.

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
    /// The sha256 of what runs (B6 review finding 6), taken when the owner
    /// set or re-confirmed the location with Touch ID: `node` (the binary),
    /// `node_modules` (the whole installed tree, `dist/cli.js` and every
    /// dependency included), `package-lock.json` (the prefix's lockfile).
    /// Empty: not confirmed, so `unsupported`.
    public var digests: [String: String]

    public static let digestNames = ["node", "node_modules", "package-lock.json"]

    public init(
        node: String = SrtLocation.defaultNode, prefix: String = SrtLocation.defaultPrefix, digests: [String: String] = [:]
    ) {
        self.node = node
        self.prefix = prefix
        self.digests = digests
    }

    public var cliPath: String { prefix + "/" + Self.packagePath + "/dist/cli.js" }
    var packageJSON: String { prefix + "/" + Self.packagePath + "/package.json" }

    public var document: JSONValue {
        ["node": .string(node), "prefix": .string(prefix), "sha256": .object(digests.mapValues(JSONValue.string))]
    }

    public static func from(_ value: JSONValue?) -> SrtLocation? {
        guard let node = value?["node"]?.stringValue, let prefix = value?["prefix"]?.stringValue else { return nil }
        let digests = (value?["sha256"]?.objectValue ?? [:]).compactMapValues(\.stringValue)
        return SrtLocation(node: node, prefix: prefix, digests: digests)
    }

    /// The digests of the files as they are now (throws when one cannot be
    /// read). Unchanged files are not re-read (FileDigests caches by inode,
    /// size, mtime and ctime).
    public func currentDigests() throws -> [String: String] {
        [
            "node": try FileDigests.shared.file(node),
            "node_modules": try FileDigests.shared.tree(prefix + "/node_modules"),
            "package-lock.json": try FileDigests.shared.file(prefix + "/package-lock.json"),
        ]
    }

    /// This location with the digests of what is installed now (the owner
    /// confirms it with Touch ID in the policy UI). Refused unless the
    /// prefix holds exactly the pinned npm packages (SrtPin: lockfile
    /// versions and integrities, and the installed files equal to the
    /// cached, integrity-checked tarballs).
    public func confirmed(npmCache: String = SrtPin.defaultCache) throws -> SrtLocation {
        if let problem = SrtPin.problem(prefix: prefix, cache: npmCache) {
            throw ExecutorError("srt is not the pinned package: \(problem)")
        }
        return SrtLocation(node: node, prefix: prefix, digests: try currentDigests())
    }

    /// The digests that differ from the confirmed ones (what the re-confirm
    /// prompt names), empty when none does.
    public func changedDigests() -> [String] {
        guard let now = try? currentDigests() else { return Self.digestNames }
        return Self.digestNames.filter { now[$0] != digests[$0] }
    }

    /// Why this location cannot run commands, or nil when it can: absolute
    /// paths, an executable Node >= 22.12, the CLI present, the package at
    /// the pinned version, and every file unchanged since the owner
    /// confirmed the location.
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
        guard Set(digests.keys) == Set(Self.digestNames) else { return "srt not confirmed (Touch ID)" }
        guard let now = try? currentDigests() else { return "srt files unreadable" }
        guard now == digests else {
            let changed = Self.digestNames.filter { now[$0] != digests[$0] }
            return "srt files changed since confirmation: " + changed.joined(separator: ", ")
        }
        guard let nodeVersion = NodeVersionCache.shared.version(of: node, digest: now["node"] ?? "") else {
            return "node --version failed"
        }
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

/// `node --version` per binary content: asked again whenever the binary's
/// digest changes (spawned with an empty environment and a 10 s timeout).
final class NodeVersionCache: @unchecked Sendable {
    static let shared = NodeVersionCache()
    private let lock = NSLock()
    private var versions: [String: String] = [:]

    func version(of node: String, digest: String) -> String? {
        let key = node + "\u{0}" + digest
        lock.lock()
        if let known = versions[key] {
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
        versions[key] = version
        lock.unlock()
        return version
    }
}

/// sha256 of files and directory trees, cached per file by (device, inode,
/// size, mtime, ctime): a file that is written changes its ctime, which no
/// unprivileged process can set back, so an unchanged signature means
/// unchanged content.
public final class FileDigests: @unchecked Sendable {
    public static let shared = FileDigests()
    private let lock = NSLock()
    private var cache: [String: (signature: [Int64], digest: String)] = [:]

    public init() {}

    static func signature(_ path: String) -> [Int64]? {
        var info = stat()
        guard lstat(path, &info) == 0 else { return nil }
        return [
            Int64(info.st_dev), Int64(bitPattern: info.st_ino), Int64(info.st_size), Int64(info.st_mode),
            Int64(info.st_mtimespec.tv_sec), Int64(info.st_mtimespec.tv_nsec),
            Int64(info.st_ctimespec.tv_sec), Int64(info.st_ctimespec.tv_nsec),
        ]
    }

    /// One regular file (a symlink is resolved and its target hashed).
    public func file(_ path: String) throws -> String {
        let real = ReaderPaths.realpathC(path)
        guard let signature = Self.signature(real) else { throw ExecutorError("unreadable: \(path)") }
        lock.lock()
        if let cached = cache[real], cached.signature == signature {
            lock.unlock()
            return cached.digest
        }
        lock.unlock()
        guard let data = FileManager.default.contents(atPath: real) else { throw ExecutorError("unreadable: \(path)") }
        let digest = JSON.hexDigest(data)
        lock.lock()
        cache[real] = (signature, digest)
        lock.unlock()
        return digest
    }

    /// A directory tree: every entry's relative path, kind and (for a file)
    /// digest or (for a symlink) target, sorted, hashed together.
    public func tree(_ root: String) throws -> String {
        guard let enumerator = FileManager.default.enumerator(atPath: root) else {
            throw ExecutorError("unreadable: \(root)")
        }
        var lines: [String] = []
        for case let relative as String in enumerator {
            let path = root + "/" + relative
            var info = stat()
            guard lstat(path, &info) == 0 else { throw ExecutorError("unreadable: \(path)") }
            switch info.st_mode & S_IFMT {
            case S_IFDIR: lines.append("d \(relative)")
            case S_IFLNK:
                let target = (try? FileManager.default.destinationOfSymbolicLink(atPath: path)) ?? ""
                lines.append("l \(relative) -> \(target)")
            case S_IFREG: lines.append("f \(relative) \(try file(path)) \(info.st_mode & 0o7777)")
            default: lines.append("o \(relative)")
            }
        }
        lines.sort(by: Scalars.less)
        return JSON.hexDigest(Data(lines.joined(separator: "\n").utf8))
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

    /// srt is usable AND the process monitor works on this system (its
    /// unique-id self-test passed): never `granted` for an unmonitorable run.
    public var available: Bool { ProcessTree.selfTestPassed && location.problem() == nil }

    /// The exact argv srt is started with (tests, the README).
    public func argv(settingsPath: String, command: [String]) -> [String] {
        [location.node, location.cliPath, "--settings", settingsPath, "--"] + command
    }

    /// Start `spec` under srt; throws (nothing runs) when srt is not usable,
    /// the spec carries no sandbox, or the working directory no longer
    /// resolves to the write root.
    public func start(_ spec: CommandSpec) throws -> RunningCommand {
        guard let sandbox = spec.sandbox else { throw ExecutorError("no sandbox document") }
        guard ProcessTree.selfTestPassed else { throw ExecutorError("process monitor self-test failed") }
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

    private init(child: ChildProcess, monitor: TreeMonitor, limits: RunLimits, cleanupDirectory: String?) {
        self.child = child
        self.monitor = monitor
        self.limits = limits
        self.cleanupDirectory = cleanupDirectory
    }

    /// Spawn `argv` suspended in a new process group, set up the monitor
    /// (identity, fork/exit watch) and the pumps, then let it run
    /// (TreeMonitor follows the tree by unique-id lineage, so a descendant
    /// that left the group and lost its parent is still the run's). When
    /// the monitor cannot be set up, the suspended root's group is killed
    /// and nothing runs (hardening N4).
    public static func start(
        executable: String, argv: [String], environment: [String: String], cwd: String, limits: RunLimits,
        cleanupDirectory: String? = nil
    ) throws -> SandboxRun {
        let child = try ChildProcess.spawn(
            executable: executable, argv: argv, environment: environment, cwd: cwd, stdout: .pipe, stderr: .pipe,
            newProcessGroup: true, startSuspended: true)
        let monitor: TreeMonitor
        do {
            monitor = try TreeMonitor(
                root: child.pid, maxProcesses: limits.maxProcesses, cpuSeconds: limits.cpuSeconds)
        } catch {
            child.signal(SIGKILL)  // still suspended and unreaped: its group is this run only
            child.wait(timeout: 5)
            for fd in [child.stdoutFD, child.stderrFD].compactMap({ $0 }) { close(fd) }
            throw ExecutorError("the run cannot be monitored: \(error)")
        }
        let run = SandboxRun(child: child, monitor: monitor, limits: limits, cleanupDirectory: cleanupDirectory)
        run.begin()
        child.resume()
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
        // Whatever of the tree is left (a child that left the group) dies
        // now; the monitor keeps sampling until no member is left.
        monitor.killTree()
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
        // While the root is not reaped its group id names this run only.
        child.signal(SIGTERM)
        DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + limits.killGraceSeconds) { [self] in
            // A run that finished meanwhile is not signalled again (its
            // leftovers are the monitor's); otherwise only verified
            // identities and an unreaped root are signalled.
            guard !self.hasExited else { return }
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
