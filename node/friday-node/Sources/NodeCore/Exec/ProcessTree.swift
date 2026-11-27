// The process tree of one sandboxed run, and the limits macOS cannot put on
// it any other way (runbook B6; A6 finding 2).
//
// `RLIMIT_NPROC` on macOS counts every process of the user, not the
// command's: with ~290 owner processes, the reference executor's cap of 256
// makes every fork fail, and a cap relative to the current count would race
// every other app of the owner. So the node never sets it. Instead a monitor
// samples the run's tree every 100 ms:
//
// - the tree is the run's process group plus every descendant of its root
//   (by parent pid), so a child that leaves the group (`setsid`) is still
//   counted while its parent lives;
// - more than `max_processes` processes alive at once -> the whole tree is
//   killed (SIGKILL) and the run ends as an error (`max_processes`);
// - the CPU time of every process seen in the tree (user + system, the
//   largest value sampled per pid) above `cpu_seconds` -> the same, as
//   `cpu_seconds` (posix_spawn cannot set RLIMIT_CPU on the child, and
//   setting it on the app would cap the app);
// - once the root has exited, whatever is left of the tree (a background
//   child) is killed, so its pipes close and nothing outlives the run.
//
// Limits of the method (documented in the node README and the dependency
// manifest): a sampled count can overshoot between two samples (a fork bomb
// gets up to 100 ms; the kernel's per-user `kern.maxprocperuid` is the
// backstop), CPU time of a process that lived less than a sample interval is
// not seen, and a descendant that was re-parented to launchd after its
// parent died is no longer linked to the tree unless it stayed in the group.

import Darwin
import Foundation

public enum ProcessTree {
    /// Every pid on the system (`proc_listallpids`).
    static func allPIDs() -> [pid_t] {
        let estimate = proc_listallpids(nil, 0)
        guard estimate > 0 else { return [] }
        var pids = [pid_t](repeating: 0, count: Int(estimate) + 64)
        let count = pids.withUnsafeMutableBufferPointer { buffer -> Int32 in
            proc_listallpids(buffer.baseAddress, Int32(buffer.count * MemoryLayout<pid_t>.size))
        }
        guard count > 0 else { return [] }
        return Array(pids.prefix(Int(count))).filter { $0 > 0 }
    }

    /// (parent pid, process group) of `pid`, nil when it is gone.
    static func parentAndGroup(_ pid: pid_t) -> (ppid: pid_t, pgid: pid_t)? {
        var info = proc_bsdinfo()
        let size = Int32(MemoryLayout<proc_bsdinfo>.size)
        guard proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &info, size) == size else { return nil }
        return (pid_t(info.pbi_ppid), pid_t(info.pbi_pgid))
    }

    /// The live members of a tree: the live `roots`, the members of `group`
    /// (only those in `known` when given) and every descendant of either.
    public static func members(roots: Set<pid_t>, group: pid_t?, known: Set<pid_t>? = nil) -> Set<pid_t> {
        var children: [pid_t: [pid_t]] = [:]
        var alive: Set<pid_t> = []
        var members: Set<pid_t> = []
        for pid in allPIDs() {
            guard let (ppid, pgid) = parentAndGroup(pid) else { continue }
            alive.insert(pid)
            children[ppid, default: []].append(pid)
            if let group, pgid == group, known?.contains(pid) ?? true { members.insert(pid) }
        }
        members.formUnion(roots.intersection(alive))
        var queue = Array(members)
        while let next = queue.popLast() {
            for child in children[next] ?? [] where !members.contains(child) {
                members.insert(child)
                queue.append(child)
            }
        }
        return members
    }

    private static let timebase: (numer: UInt64, denom: UInt64) = {
        var info = mach_timebase_info_data_t()
        mach_timebase_info(&info)
        return (UInt64(max(info.numer, 1)), UInt64(max(info.denom, 1)))
    }()

    /// User + system CPU time of `pid` in seconds, nil when it is gone.
    static func cpuSeconds(_ pid: pid_t) -> Double? {
        var usage = rusage_info_v0()
        let status = withUnsafeMutablePointer(to: &usage) { pointer -> Int32 in
            pointer.withMemoryRebound(to: rusage_info_t?.self, capacity: 1) {
                proc_pid_rusage(pid, RUSAGE_INFO_V0, $0)
            }
        }
        guard status == 0 else { return nil }
        // rusage_info times are Mach absolute time units.
        let ticks = usage.ri_user_time &+ usage.ri_system_time
        let nanoseconds = Double(ticks) * Double(timebase.numer) / Double(timebase.denom)
        return nanoseconds / 1_000_000_000
    }

    /// SIGKILL the group and every live member.
    static func kill(root: pid_t, group: pid_t?, members: Set<pid_t>) {
        guard !members.isEmpty else { return }
        if let group { _ = killpg(group, SIGKILL) }
        for pid in members where pid > 1 && pid != getpid() { _ = Darwin.kill(pid, SIGKILL) }
    }
}

/// Samples one run's tree and enforces `max_processes` and `cpu_seconds`.
final class TreeMonitor: @unchecked Sendable {
    static let interval: Double = 0.1

    private let root: pid_t
    private let group: pid_t?
    private let maxProcesses: Int
    private let cpuSeconds: Double
    private let lock = NSLock()
    private var timer: DispatchSourceTimer?
    private var cpuByPID: [pid_t: Double] = [:]
    private var violation: String?
    private var rootExited = false
    public private(set) var peakProcesses = 0
    private let queue = DispatchQueue(label: "friday-node.tree-monitor", qos: .utility)

    init(root: pid_t, group: pid_t?, maxProcesses: Int, cpuSeconds: Double) {
        self.root = root
        self.group = group
        self.maxProcesses = maxProcesses
        self.cpuSeconds = cpuSeconds
    }

    func start() {
        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(deadline: .now(), repeating: Self.interval, leeway: .milliseconds(10))
        timer.setEventHandler { [weak self] in self?.sample() }
        lock.lock()
        self.timer = timer
        lock.unlock()
        timer.resume()
    }

    func stop() {
        lock.lock()
        let timer = self.timer
        self.timer = nil
        lock.unlock()
        timer?.cancel()
    }

    /// The limit the run broke, if any (`max_processes`, `cpu_seconds`).
    var brokenLimit: String? {
        lock.lock()
        defer { lock.unlock() }
        return violation
    }

    /// The root was reaped: kill what is left of the tree from now on.
    func rootDidExit() {
        lock.lock()
        rootExited = true
        lock.unlock()
        queue.async { self.sample() }
    }

    /// Kill the whole tree now (terminate's SIGKILL step).
    func killTree() {
        ProcessTree.kill(root: root, group: group, members: currentMembers())
    }

    /// While the root lives: its group and descendants (remembered). After
    /// it was reaped its pid may be recycled as a process, so the root is no
    /// longer a root; the group still counts (a group id is not reused while
    /// any member lives), and so do the processes seen in the tree before
    /// and their descendants.
    private func currentMembers() -> Set<pid_t> {
        lock.lock()
        let exited = rootExited
        let known = seen
        lock.unlock()
        let members = exited
            ? ProcessTree.members(roots: known.subtracting([root]), group: group)
            : ProcessTree.members(roots: [root], group: group)
        lock.lock()
        seen.formUnion(members)
        lock.unlock()
        return members
    }

    private var seen: Set<pid_t> = []

    private func sample() {
        let members = currentMembers()
        lock.lock()
        let exited = rootExited
        peakProcesses = max(peakProcesses, members.count)
        var broken: String?
        if !exited && violation == nil {
            if members.count > maxProcesses {
                broken = "max_processes"
            } else {
                for pid in members {
                    if let cpu = ProcessTree.cpuSeconds(pid) { cpuByPID[pid] = max(cpuByPID[pid] ?? 0, cpu) }
                }
                if cpuByPID.values.reduce(0, +) > cpuSeconds { broken = "cpu_seconds" }
            }
            if let broken { violation = broken }
        }
        lock.unlock()
        if broken != nil || (exited && !members.isEmpty) {
            ProcessTree.kill(root: root, group: group, members: members)
        }
        if exited && members.isEmpty { stop() }
    }
}
