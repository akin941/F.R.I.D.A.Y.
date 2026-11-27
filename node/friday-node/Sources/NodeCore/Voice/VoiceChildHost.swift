// The voice child's process host (Phase 11 brief D6, D12, D16; Mac session
// M1; runbook B7). Binds `VoiceHostCore` to a real child process:
//
//   ~/Library/Application Support/Friday/voice/.venv/bin/friday-voice run
//
// - Spawned through `ChildProcess` (posix_spawn, an argv, never a shell,
//   `POSIX_SPAWN_CLOEXEC_DEFAULT`, signals reset, its own process group),
//   stdin/stdout pipes (the protocol) and stderr (the child's scrubbed log:
//   only its codes reach the node's os_log), and a clean environment: `PATH`,
//   `HOME` and `LANG` only, nothing inherited from the app.
// - Before every spawn the installed `uv.lock` must hash to the sha256
//   compiled into the host (`VoiceProtocol.lockSHA256`, generated from this
//   checkout's node/friday-voice/uv.lock); a mismatch or a missing install
//   is a failed start (`lock_mismatch`, `not_installed`) and is retried with
//   the same backoff. The hash gates the lock FILE only: it proves which
//   dependency set `make friday-voice-install` was asked to install, not
//   what is in `.venv` now (nothing re-hashes the installed packages or the
//   child's own sources; a process of the owner's user can change them, as
//   it can change anything in the home).
// - The core decides; this type moves bytes and time: a 20 ms tick runs the
//   watchdog (10 s without `state`), paced playback and the restart backoff
//   (1 s -> 60 s); a failed child has its stdin closed, then SIGTERM to its
//   group after 2 s and SIGKILL to its verified tree after 2 s more. The
//   next child is spawned only once every earlier one has exited (or was
//   SIGKILLed): two children never overlap.
// - `stop()` (WS loss, mode off) sends `shutdown`, closes stdin and kills
//   what is left the same way; `stop(wait: true)` (quit) runs that ladder
//   synchronously, bounded (`quitGraceSeconds` per step), so nothing
//   outlives the app; `kill()` (the kill switch) SIGKILLs the tree at once,
//   and every child still in its stop grace with it. None restarts.
// - The child is registered as a node helper (MonitorRegistry), so no
//   sandboxed run's orphan sweep attributes its processes to a run.
//
// Writes to the child never block the host queue: frames go to a bounded
// buffer flushed by a write source on a non-blocking pipe; a child that
// stops reading (more than 1 MiB pending) is failed as unresponsive
// (`watchdog`).

import Darwin
import Foundation
import os

public protocol VoiceChildHostDelegate: AnyObject {
    /// On the host queue, in order.
    func voiceHost(_ host: VoiceChildHost, effects: [VoiceHostEffect])
    /// The mic is now held / released (capture starts / stops).
    func voiceHost(_ host: VoiceChildHost, micHeld: Bool)
    /// The indicator changed (menu-bar icon).
    func voiceHost(_ host: VoiceChildHost, indicator: String)
}

public final class VoiceChildHost: @unchecked Sendable {
    public struct Install: Sendable {
        public let home: String
        public let executable: String
        public let argv: [String]
        public let cwd: String?
        /// Check `<home>/uv.lock` against the compiled-in sha256 before each
        /// spawn (off only for the Conformance build's hostile child, which
        /// runs from the checkout).
        public let checkLock: Bool

        public static let defaultHome = NSHomeDirectory() + "/Library/Application Support/Friday/voice"

        public static func installed(home: String = defaultHome) -> Install {
            Install(
                home: home, executable: home + "/.venv/bin/friday-voice", argv: ["friday-voice", "run"], cwd: home,
                checkLock: true)
        }

        public init(home: String, executable: String, argv: [String], cwd: String?, checkLock: Bool) {
            self.home = home
            self.executable = executable
            self.argv = argv
            self.cwd = cwd
            self.checkLock = checkLock
        }
    }

    /// The child's whole environment (D16: nothing inherited).
    public static func childEnvironment(home: String = NSHomeDirectory()) -> [String: String] {
        ["PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": home, "LANG": "en_US.UTF-8"]
    }

    public enum LockCheck: Equatable {
        case ok
        case notInstalled
        case mismatch
    }

    /// The installed lock against the compiled-in hash.
    public static func checkLock(home: String, expected: String = VoiceProtocol.lockSHA256) -> LockCheck {
        guard let data = FileManager.default.contents(atPath: home + "/uv.lock") else { return .notInstalled }
        return JSON.hexDigest(data) == expected ? .ok : .mismatch
    }

    static let log = Logger(subsystem: "dev.friday.friday-node", category: "voice")
    public static let tickMs = 20
    public static let maxPendingWrite = 1024 * 1024
    public static let stopGraceSeconds = 2.0
    /// Quit: how long each step of the synchronous ladder waits (stdin EOF,
    /// then SIGTERM, then SIGKILL).
    public static let quitGraceSeconds = 0.5

    public let queue = DispatchQueue(label: "friday-node.voice", qos: .userInteractive)
    public let core: VoiceHostCore
    public let install: Install
    public weak var delegate: VoiceChildHostDelegate?
    /// Each (re)start's argv (the Conformance build gives every restart its
    /// own hostile-child segment); default: `install.argv`.
    public var argvForStart: ((Int) -> [String])?
    /// Every stderr line of the child (the Conformance build reads the
    /// verdict lines); the app logs codes only.
    public var onStderrLine: ((String) -> Void)?
    /// Every batch of frames handed to the child, in order (the live
    /// runner checks `to_child` with it); on the host queue.
    public var onFramesWritten: (([Data]) -> Void)?
    public var environment: [String: String]

    private var child: ChildProcess?
    private var childID: Int = 0
    private var pending = Data()
    private var writeSource: DispatchSourceWrite?
    private var timer: DispatchSourceTimer?
    private var lastMic = false
    private var lastIndicator = "off"
    private var running = false  // the owner wants a child (start() .. stop())
    private var helperID: UInt64?
    /// Children being stopped: killed after the grace unless gone.
    private var stopping: [Int: ChildProcess] = [:]

    public init(core: VoiceHostCore, install: Install = .installed(), environment: [String: String]? = nil) {
        self.core = core
        self.install = install
        self.environment = environment ?? Self.childEnvironment()
    }

    deinit { timer?.cancel() }

    // MARK: Control (any thread)

    /// Start the child (and the tick); a no-op when it already runs.
    public func start(autoTick: Bool = true) {
        queue.async {
            self.running = true
            if autoTick { self.startTimer() }
            // A child in its restart backoff waits for the tick.
            self.spawnIfDue()
        }
    }

    /// Spawn when wanted and no earlier child is still around (no overlap):
    /// a restart once its backoff ran out, or a first start.
    private func spawnIfDue() {
        guard running, child == nil, stopping.isEmpty, !core.childRunning else { return }
        if core.restartAt == nil || core.restartDue() { spawn() }
    }

    /// Stop on purpose (quit, WS loss, mode off): `shutdown`, then the kill
    /// ladder. No restart until `start()`.
    /// `grace`: each step of the synchronous ladder when `wait` (quit).
    public func stop(wait: Bool = false, grace: Double = VoiceChildHost.quitGraceSeconds) {
        let work = {
            self.running = false
            self.core.shutdown()
            self.flush()  // the `shutdown` frame
            self.core.childExited()  // expected: no restart, no watchdog
            self.publish()
            self.retire(graceful: true)
            if wait { self.finishStopping(grace: grace) }
        }
        if wait { queue.sync(execute: work) } else { queue.async(execute: work) }
    }

    /// Quit: every child still stopping gets the ladder now, synchronously
    /// (stdin is closed already): a bounded wait for its exit, SIGTERM to its
    /// group, a bounded wait, SIGKILL to its tree, a bounded wait for the
    /// reap. Blocks the host queue for at most 3 x `quitGraceSeconds`.
    private func finishStopping(grace: Double) {
        let processes = Array(stopping.values)
        stopping.removeAll()
        for process in processes where !process.wait(timeout: grace) {
            process.signal(SIGTERM)
            if !process.wait(timeout: grace) {
                Self.killTree(process)
                process.wait(timeout: grace)
            } else {
                Self.killTree(process)  // its group's leftovers
            }
        }
    }

    /// The kill switch: SIGKILL the tree now. No restart until `start()`.
    public func kill() {
        queue.async {
            self.running = false
            self.core.shutdown()
            self.core.childExited()
            self.core.dropCredential()
            self.publish()
            self.retire(graceful: false)
            // A child still in a graceful stop's grace dies now too.
            for process in self.stopping.values { Self.killTree(process) }
            self.stopping.removeAll()
        }
    }

    /// Run `body` on the host queue with the core, then flush its frames and
    /// publish mic / indicator / effects.
    public func perform(_ body: @escaping (VoiceHostCore) -> Void) {
        queue.async {
            body(self.core)
            self.afterCoreChange()
        }
    }

    public func performSync<T>(_ body: (VoiceHostCore) -> T) -> T {
        queue.sync {
            let value = body(core)
            afterCoreChange()
            return value
        }
    }

    /// One tick, on the host queue (the timer's, or a runner's manual step).
    public func tick() {
        dispatchPrecondition(condition: .onQueue(queue))
        core.tick()
        spawnIfDue()
        afterCoreChange()
    }

    // MARK: Internals (host queue)

    private func startTimer() {
        guard timer == nil else { return }
        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(deadline: .now(), repeating: .milliseconds(Self.tickMs), leeway: .milliseconds(2))
        timer.setEventHandler { [weak self] in self?.tick() }
        self.timer = timer
        timer.resume()
    }

    private func afterCoreChange() {
        flush()
        if !core.linkOpen, child != nil { retire(graceful: true) }
        publish()
    }

    private func publish() {
        let effects = core.takeEffects()
        if !effects.isEmpty { delegate?.voiceHost(self, effects: effects) }
        if core.micHeld != lastMic {
            lastMic = core.micHeld
            delegate?.voiceHost(self, micHeld: lastMic)
        }
        if core.indicator != lastIndicator {
            lastIndicator = core.indicator
            delegate?.voiceHost(self, indicator: lastIndicator)
        }
    }

    private func spawn() {
        guard child == nil else { return }
        if install.checkLock {
            switch Self.checkLock(home: install.home) {
            case .ok: break
            case .notInstalled:
                Self.log.error("voice child: not installed")
                core.spawnFailed("not_installed")
                publish()
                return
            case .mismatch:
                Self.log.error("voice child: installed uv.lock differs from the compiled-in hash; not started")
                core.spawnFailed("lock_mismatch")
                publish()
                return
            }
        }
        let argv = argvForStart?(core.starts + 1) ?? install.argv
        let process: ChildProcess
        do {
            process = try ChildProcess.spawn(
                executable: install.executable, argv: argv, environment: environment, cwd: install.cwd,
                stdin: .pipe, stdout: .pipe, stderr: .pipe, newProcessGroup: true)
        } catch {
            Self.log.error("voice child: spawn failed")
            core.spawnFailed("spawn_failed")
            publish()
            return
        }
        childID += 1
        let id = childID
        child = process
        if let unique = ProcessTree.identity(of: process.pid)?.uniqueID {
            helperID = unique
            MonitorRegistry.shared.helperStarted(unique)
        }
        if let fd = process.stdinFD {
            _ = fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK)
        }
        pending = Data()
        core.startChild()
        let stdoutDone = DispatchSemaphore(value: 0)
        if let fd = process.stdoutFD {
            let thread = Thread { [weak self] in
                ChildProcess.drain(fd) { data in
                    self?.queue.async { self?.received(data, from: id) }
                }
                stdoutDone.signal()
                self?.queue.async { self?.stdoutClosed(from: id) }
            }
            thread.name = "friday-node.voice-stdout"
            thread.start()
        } else {
            stdoutDone.signal()
        }
        if let fd = process.stderrFD {
            let thread = Thread { [weak self] in
                var line = Data()
                ChildProcess.drain(fd) { data in
                    for byte in data {
                        if byte == 0x0A {
                            let text = String(decoding: line.prefix(4096), as: UTF8.self)
                            line.removeAll(keepingCapacity: true)
                            self?.stderrLine(text)
                        } else if line.count < 8192 {
                            line.append(byte)
                        }
                    }
                }
                if !line.isEmpty { self?.stderrLine(String(decoding: line.prefix(4096), as: UTF8.self)) }
            }
            thread.name = "friday-node.voice-stderr"
            thread.start()
        }
        process.onExit { [weak self] in
            // Let the stdout reader hand over what the child wrote last (a
            // partial frame is a short read, not a plain exit).
            _ = stdoutDone.wait(timeout: .now() + 1)
            self?.queue.async { self?.exited(id: id, process: process) }
        }
        afterCoreChange()
    }

    private func stderrLine(_ text: String) {
        if let onStderrLine {
            onStderrLine(text)
            return
        }
        // The child's log lines are already scrubbed (D16); only the code
        // reaches the node's log.
        if let doc = try? JSON.parse(text), let code = doc["code"]?.stringValue, code.utf8.count <= 64 {
            Self.log.info("voice child: \(code, privacy: .public)")
        }
    }

    private func received(_ data: Data, from id: Int) {
        guard id == childID, child != nil else { return }
        core.childBytes(data)
        afterCoreChange()
    }

    private func stdoutClosed(from id: Int) {
        guard id == childID, child != nil else { return }
        core.childStreamClosed()
        afterCoreChange()
    }

    private func exited(id: Int, process: ChildProcess) {
        if stopping.removeValue(forKey: id) != nil {
            // The last earlier child is gone: a due restart may go now.
            spawnIfDue()
            afterCoreChange()
            return
        }
        if let helperID, id == childID {
            MonitorRegistry.shared.helperEnded(helperID)
            self.helperID = nil
        }
        guard id == childID, child === process else { return }
        core.childExited(code: process.exitCode ?? -1)
        if child === process {
            closeStdin()
            child = nil
        }
        afterCoreChange()
    }

    private func flush() {
        let frames = core.takeOutbound()
        guard let process = child, let fd = process.stdinFD else { return }
        if !frames.isEmpty { onFramesWritten?(frames) }
        for frame in frames { pending.append(frame) }
        if pending.count > Self.maxPendingWrite {
            pending = Data()
            Self.log.error("voice child: not reading its stdin")
            core.childExitedUnresponsive()
            return
        }
        writePending(fd)
    }

    private func writePending(_ fd: Int32) {
        while !pending.isEmpty {
            let written = pending.withUnsafeBytes { buffer in Darwin.write(fd, buffer.baseAddress, buffer.count) }
            if written > 0 {
                pending.removeFirst(written)
                continue
            }
            if written < 0 && errno == EINTR { continue }
            if written < 0 && errno == EAGAIN {
                armWriteSource(fd)
                return
            }
            pending = Data()  // EPIPE: the child is gone; its exit is on its way
            return
        }
        writeSource?.cancel()
        writeSource = nil
    }

    private func armWriteSource(_ fd: Int32) {
        guard writeSource == nil else { return }
        let source = DispatchSource.makeWriteSource(fileDescriptor: fd, queue: queue)
        source.setEventHandler { [weak self] in
            guard let self else { return }
            self.writeSource?.cancel()
            self.writeSource = nil
            self.writePending(fd)
        }
        writeSource = source
        source.resume()
    }

    private func closeStdin() {
        writeSource?.cancel()
        writeSource = nil
        if let fd = child?.stdinFD { close(fd) }
    }

    /// The link to the current child is gone (failed or stopped): close its
    /// stdin, then the kill ladder (graceful: SIGTERM after the grace, SIGKILL
    /// after another; otherwise SIGKILL at once).
    private func retire(graceful: Bool) {
        guard let process = child else { return }
        flushRemaining(process)
        closeStdin()
        child = nil
        let id = childID
        if let helperID {
            MonitorRegistry.shared.helperEnded(helperID)
            self.helperID = nil
        }
        guard graceful else {
            Self.killTree(process)
            return
        }
        stopping[id] = process
        queue.asyncAfter(deadline: .now() + Self.stopGraceSeconds) { [weak self] in
            guard let self, self.stopping[id] != nil else { return }
            process.signal(SIGTERM)
            self.queue.asyncAfter(deadline: .now() + Self.stopGraceSeconds) { [weak self] in
                // Kept in `stopping` until its exit is seen: no restart overlaps it.
                guard let self, self.stopping[id] != nil else { return }
                Self.killTree(process)
            }
        }
    }

    /// A last best-effort write of what is pending (the `shutdown` frame).
    private func flushRemaining(_ process: ChildProcess) {
        guard let fd = process.stdinFD, !pending.isEmpty else { return }
        _ = pending.withUnsafeBytes { buffer in Darwin.write(fd, buffer.baseAddress, buffer.count) }
        pending = Data()
    }

    /// SIGKILL the child's group (while it is not reaped) and every verified
    /// descendant outside it (by unique-id lineage; ProcessTree.signal checks
    /// each identity first).
    static func killTree(_ process: ChildProcess) {
        let descendants = Self.descendants(of: process.pid)
        process.signal(SIGKILL)
        for identity in descendants { ProcessTree.signal(identity, SIGKILL) }
    }

    /// The live descendants of `pid`, by recorded-parent unique ids (a fixed
    /// point over one scan).
    static func descendants(of pid: pid_t) -> [ProcessIdentity] {
        guard let root = ProcessTree.row(pid) else { return [] }
        var known: Set<UInt64> = [root.identity.uniqueID]
        var found: [ProcessIdentity] = []
        let rows = ProcessTree.allPIDs().compactMap(ProcessTree.row)
        var changed = true
        while changed {
            changed = false
            for row in rows where !known.contains(row.identity.uniqueID) && known.contains(row.parentUniqueID) {
                known.insert(row.identity.uniqueID)
                found.append(row.identity)
                changed = true
            }
        }
        return found
    }

    // MARK: Observation (tests, the runner)

    /// The pids `killTree` would reach beyond the group (diagnostics).
    public static func descendantPIDs(of pid: pid_t) -> [pid_t] { descendants(of: pid).map(\.pid) }

    public var childPID: pid_t? { queue.sync { child?.pid } }

    /// Children in a stop's grace (tests).
    public var stoppingPIDs: [pid_t] { queue.sync { stopping.values.map(\.pid) } }
}

extension VoiceHostCore {
    /// The child stopped reading its stdin: failed closed as unresponsive
    /// (the watchdog's code: it no longer takes part in the protocol).
    func childExitedUnresponsive() { failClosed("watchdog") }
}
