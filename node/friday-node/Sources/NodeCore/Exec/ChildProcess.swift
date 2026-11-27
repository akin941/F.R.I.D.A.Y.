// Every child process the node starts (git for the throwaway worktree, the
// `srt` sandbox, `osascript` for the recipes) goes through here (runbook B6;
// the B5 review's findings 1 and 4).
//
// - `posix_spawn` with an argv (never a shell), an explicit environment and
//   explicit stdio: stdin is /dev/null, stdout and stderr are pipes the node
//   reads (or /dev/null). `POSIX_SPAWN_CLOEXEC_DEFAULT` keeps every other
//   descriptor of the app (the WebSocket, the hook socket, the keychain) out
//   of the child; the parent's pipe ends are close-on-exec as well, so a
//   concurrent Foundation spawn elsewhere cannot inherit them either.
// - Signal dispositions and the mask are reset to the defaults, and the
//   working directory is set by the spawn itself (`addchdir_np`).
// - Optionally in a new process group (the sandboxed command), so a stop
//   reaches the whole tree.
// - Reaping happens on a dedicated thread with `waitid(WNOWAIT)` first: the
//   exit is recorded under the lock before the zombie is reaped, so a
//   signal sent under the same lock can never hit a recycled pid.
// - `ChildRegistry` remembers the blocking children (git, osascript) the
//   session queue is waiting on, so the kill switch can stop them from any
//   thread instead of waiting behind them (finding 1).

import Darwin
import Foundation

public struct SpawnError: Error, Equatable, CustomStringConvertible {
    public let description: String
    init(_ description: String) { self.description = description }
}

/// One spawned child: its pid, its output pipes, its exit status.
public final class ChildProcess: @unchecked Sendable {
    public enum Output: Sendable {
        case devNull
        case pipe
    }

    public let pid: pid_t
    public let processGroup: Bool
    private let lock = NSLock()
    private var exitStatus: Int32?
    private let exited = DispatchSemaphore(value: 0)
    private var exitHandlers: [() -> Void] = []
    /// The parent's read ends (nil for /dev/null), close-on-exec.
    public let stdoutFD: Int32?
    public let stderrFD: Int32?

    private init(pid: pid_t, processGroup: Bool, stdoutFD: Int32?, stderrFD: Int32?) {
        self.pid = pid
        self.processGroup = processGroup
        self.stdoutFD = stdoutFD
        self.stderrFD = stderrFD
    }

    /// Spawn `argv` (argv[0] is the program name the child sees) from the
    /// absolute `executable`. Nothing of the node's own environment reaches
    /// the child: `environment` is all it gets.
    public static func spawn(
        executable: String, argv: [String], environment: [String: String], cwd: String? = nil,
        stdout: Output = .pipe, stderr: Output = .pipe, newProcessGroup: Bool = false, startSuspended: Bool = false
    ) throws -> ChildProcess {
        guard executable.hasPrefix("/"), !argv.isEmpty else { throw SpawnError("bad executable or argv") }
        for part in argv + [executable] + environment.keys + environment.values + [cwd ?? ""]
        where part.utf8.contains(0) {
            throw SpawnError("NUL in argv, environment or cwd")
        }
        var outPipe: [Int32] = [-1, -1]
        var errPipe: [Int32] = [-1, -1]
        func closeAll() {
            for fd in outPipe + errPipe where fd >= 0 { close(fd) }
        }
        if stdout == .pipe {
            guard pipe(&outPipe) == 0 else { throw SpawnError("pipe failed") }
        }
        if stderr == .pipe {
            guard pipe(&errPipe) == 0 else {
                closeAll()
                throw SpawnError("pipe failed")
            }
        }
        for fd in outPipe + errPipe where fd >= 0 { _ = fcntl(fd, F_SETFD, FD_CLOEXEC) }

        var actions: posix_spawn_file_actions_t?
        posix_spawn_file_actions_init(&actions)
        defer { posix_spawn_file_actions_destroy(&actions) }
        posix_spawn_file_actions_addopen(&actions, 0, "/dev/null", O_RDONLY, 0)
        if stdout == .pipe {
            posix_spawn_file_actions_adddup2(&actions, outPipe[1], 1)
        } else {
            posix_spawn_file_actions_addopen(&actions, 1, "/dev/null", O_WRONLY, 0)
        }
        if stderr == .pipe {
            posix_spawn_file_actions_adddup2(&actions, errPipe[1], 2)
        } else {
            posix_spawn_file_actions_addopen(&actions, 2, "/dev/null", O_WRONLY, 0)
        }
        if let cwd {
            guard posix_spawn_file_actions_addchdir_np(&actions, cwd) == 0 else {
                closeAll()
                throw SpawnError("chdir action failed")
            }
        }

        var attributes: posix_spawnattr_t?
        posix_spawnattr_init(&attributes)
        defer { posix_spawnattr_destroy(&attributes) }
        var flags = Int32(POSIX_SPAWN_CLOEXEC_DEFAULT) | Int32(POSIX_SPAWN_SETSIGDEF) | Int32(POSIX_SPAWN_SETSIGMASK)
        // Suspended: the child exists (its pid, identity and process group
        // can be read and watched) but runs no instruction until `resume()`.
        if startSuspended { flags |= Int32(POSIX_SPAWN_START_SUSPENDED) }
        if newProcessGroup {
            flags |= Int32(POSIX_SPAWN_SETPGROUP)
            posix_spawnattr_setpgroup(&attributes, 0)
        }
        posix_spawnattr_setflags(&attributes, Int16(truncatingIfNeeded: flags))
        var defaults = sigset_t()
        sigemptyset(&defaults)
        for signal in [SIGPIPE, SIGINT, SIGTERM, SIGHUP, SIGQUIT, SIGCHLD, SIGUSR1, SIGUSR2, SIGALRM, SIGXCPU] {
            sigaddset(&defaults, signal)
        }
        posix_spawnattr_setsigdefault(&attributes, &defaults)
        var mask = sigset_t()
        sigemptyset(&mask)
        posix_spawnattr_setsigmask(&attributes, &mask)

        var cArgv: [UnsafeMutablePointer<CChar>?] = argv.map { strdup($0) } + [nil]
        var cEnv: [UnsafeMutablePointer<CChar>?] =
            environment.sorted(by: { $0.key < $1.key }).map { strdup("\($0.key)=\($0.value)") } + [nil]
        defer {
            for pointer in cArgv { free(pointer) }
            for pointer in cEnv { free(pointer) }
        }
        var pid: pid_t = 0
        let status = posix_spawn(&pid, executable, &actions, &attributes, &cArgv, &cEnv)
        // The child's ends belong to the child now.
        if outPipe[1] >= 0 { close(outPipe[1]) }
        if errPipe[1] >= 0 { close(errPipe[1]) }
        guard status == 0 else {
            if outPipe[0] >= 0 { close(outPipe[0]) }
            if errPipe[0] >= 0 { close(errPipe[0]) }
            throw SpawnError("posix_spawn failed (\(status))")
        }
        let child = ChildProcess(
            pid: pid, processGroup: newProcessGroup, stdoutFD: outPipe[0] >= 0 ? outPipe[0] : nil,
            stderrFD: errPipe[0] >= 0 ? errPipe[0] : nil)
        child.startReaper()
        return child
    }

    private func startReaper() {
        let thread = Thread { [self] in
            // Wait without reaping: the pid stays ours until the exit is
            // recorded under the lock. macOS's waitid also reports a child
            // that is merely stopped (a `startSuspended` spawn), so only an
            // exit, a kill or a dump ends the wait; the reap itself never
            // blocks under the lock.
            while true {
                var info = siginfo_t()
                let result = waitid(P_PID, id_t(pid), &info, WEXITED | WNOWAIT)
                if result < 0 {
                    if errno == EINTR { continue }
                    break  // ECHILD: nothing left to wait for
                }
                if [CLD_EXITED, CLD_KILLED, CLD_DUMPED].contains(info.si_code) { break }
                usleep(1_000)  // stopped or continued: not an exit yet
            }
            lock.lock()
            var status: Int32 = 0
            while waitpid(pid, &status, 0) < 0 && errno == EINTR {}
            exitStatus = status
            let handlers = exitHandlers
            exitHandlers = []
            lock.unlock()
            exited.signal()
            for handler in handlers { handler() }
        }
        thread.name = "friday-node.reaper"
        thread.start()
    }

    /// The raw wait status once the child was reaped.
    public var status: Int32? {
        lock.lock()
        defer { lock.unlock() }
        return exitStatus
    }

    public var hasExited: Bool { status != nil }

    /// The exit code (0...255), or the negative signal number; nil while it
    /// runs.
    public var exitCode: Int64? {
        guard let status else { return nil }
        if status & 0x7f == 0 { return Int64((status >> 8) & 0xff) }
        return -Int64(status & 0x7f)
    }

    /// Wait up to `timeout` seconds (nil: forever); true when it exited.
    @discardableResult
    public func wait(timeout: Double?) -> Bool {
        if hasExited { return true }
        guard let timeout else {
            exited.wait()
            exited.signal()
            return true
        }
        guard exited.wait(timeout: .now() + timeout) == .success else { return false }
        exited.signal()
        return true
    }

    /// Run `handler` once the child was reaped (at once when it already
    /// was), on the reaper's thread.
    public func onExit(_ handler: @escaping () -> Void) {
        lock.lock()
        if exitStatus != nil {
            lock.unlock()
            handler()
            return
        }
        exitHandlers.append(handler)
        lock.unlock()
    }

    /// Signal the child (its whole group when it leads one), only while it
    /// is not reaped: until then its pid, and so its group id, cannot name
    /// another process. After the reap nothing is sent here (a sandboxed
    /// run's leftovers are TreeMonitor's, by verified identity).
    /// Let a child spawned with `startSuspended` run.
    public func resume() {
        lock.lock()
        defer { lock.unlock() }
        guard exitStatus == nil else { return }
        _ = kill(pid, SIGCONT)
    }

    public func signal(_ signal: Int32) {
        lock.lock()
        defer { lock.unlock() }
        guard exitStatus == nil else { return }
        if processGroup { _ = killpg(pid, signal) } else { _ = kill(pid, signal) }
    }

    /// Read one pipe to EOF on the calling thread, handing each block over.
    public static func drain(_ fd: Int32, _ handle: (Data) -> Void) {
        var buffer = [UInt8](repeating: 0, count: 65536)
        while true {
            let count = read(fd, &buffer, buffer.count)
            if count > 0 {
                handle(Data(buffer[0..<count]))
            } else if count < 0 && errno == EINTR {
                continue
            } else {
                break
            }
        }
        close(fd)
    }
}

/// The blocking children the session queue waits on (git, osascript): the
/// kill switch stops them from any thread (finding 1 of the B5 review).
public final class ChildRegistry: @unchecked Sendable {
    public static let shared = ChildRegistry()
    public init() {}
    private let lock = NSLock()
    private var children: [ObjectIdentifier: ChildProcess] = [:]
    private var cancelled = 0

    public func add(_ child: ChildProcess) {
        lock.lock()
        children[ObjectIdentifier(child)] = child
        lock.unlock()
    }

    public func remove(_ child: ChildProcess) {
        lock.lock()
        children.removeValue(forKey: ObjectIdentifier(child))
        lock.unlock()
    }

    /// SIGKILL every registered child (the kill switch); returns how many.
    @discardableResult
    public func killAll() -> Int {
        lock.lock()
        let all = Array(children.values)
        cancelled += all.count
        lock.unlock()
        for child in all { child.signal(SIGKILL) }
        return all.count
    }

    public var count: Int {
        lock.lock()
        defer { lock.unlock() }
        return children.count
    }
}

/// Run a short-lived helper (git, osascript) to completion with a timeout:
/// stdout collected, stderr discarded, registered with `ChildRegistry` while
/// it runs. On timeout it is killed (SIGKILL) and the call fails.
public enum BoundedRun {
    public struct Outcome: Equatable {
        public let exitCode: Int64?
        public let stdout: Data
        public let timedOut: Bool
    }

    public static func run(
        executable: String, argv: [String], environment: [String: String], cwd: String? = nil,
        timeout: Double, maxOutput: Int = 4 * 1024 * 1024, registry: ChildRegistry = .shared
    ) throws -> Outcome {
        let child = try ChildProcess.spawn(
            executable: executable, argv: argv, environment: environment, cwd: cwd, stdout: .pipe, stderr: .devNull)
        registry.add(child)
        // Known to the process monitors as a helper of the node (git,
        // osascript): no run's orphan sweep touches it or what it leaves.
        let helperID = ProcessTree.identity(of: child.pid)?.uniqueID
        if let helperID { MonitorRegistry.shared.helperStarted(helperID) }
        defer {
            registry.remove(child)
            if let helperID { MonitorRegistry.shared.helperEnded(helperID) }
        }
        let collected = LockedData(limit: maxOutput)
        let drained = DispatchSemaphore(value: 0)
        if let fd = child.stdoutFD {
            DispatchQueue.global(qos: .utility).async {
                ChildProcess.drain(fd) { collected.append($0) }
                drained.signal()
            }
        } else {
            drained.signal()
        }
        let finished = child.wait(timeout: timeout)
        if !finished {
            child.signal(SIGKILL)
            child.wait(timeout: 5)
        }
        // A grandchild holding the pipe open must not hang the caller.
        _ = drained.wait(timeout: .now() + 1)
        return Outcome(exitCode: child.exitCode, stdout: collected.data, timedOut: !finished)
    }
}

final class LockedData: @unchecked Sendable {
    private let lock = NSLock()
    private var buffer = Data()
    private let limit: Int

    init(limit: Int) { self.limit = limit }

    func append(_ data: Data) {
        lock.lock()
        if buffer.count < limit { buffer.append(data.prefix(limit - buffer.count)) }
        lock.unlock()
    }

    var data: Data {
        lock.lock()
        defer { lock.unlock() }
        return buffer
    }
}
