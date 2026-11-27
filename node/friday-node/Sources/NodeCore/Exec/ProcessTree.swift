// The process tree of one sandboxed run, and the limits macOS cannot put on
// it any other way (runbook B6; A6 finding 2; B6 review findings 2 and 3).
//
// `RLIMIT_NPROC` on macOS counts every process of the user, not the
// command's: with ~290 owner processes, the reference executor's cap of 256
// makes every fork fail, and a cap relative to the current count would race
// every other app of the owner. So the node never sets it.
//
// Identity. A process is (pid, unique id, start time): the unique id
// (`proc_uniqidentifierinfo.p_uniqueid`) is never reused within a boot, and
// the start time comes from `proc_bsdinfo.pbi_start_tvsec/tvusec`. Every
// signal goes to one pid only after re-reading both and finding them
// unchanged, so a recycled pid is never signalled; the monitor never sends
// `killpg` (a recycled group id could name an unrelated group).
//
// Membership. A process belongs to the run when its PARENT'S unique id
// (`p_puniqueid`, which the kernel keeps when the process is re-parented to
// launchd) is the unique id of a member, starting from the root. So a child
// that calls `setsid` and whose parent exits at once is still found: its
// recorded parent is a member even though its current parent is launchd.
// Members are learnt three ways:
// - kqueue `EVFILT_PROC NOTE_FORK` on every member: the moment a member
//   forks, its children (`proc_listchildpids`) are read and watched in turn,
//   so an intermediate process is learnt while it lives;
// - a full scan every 50 ms (`proc_listallpids`), adding every process whose
//   recorded parent is a member (to a fixed point);
// - the root's process group while the root is not reaped.
// (A per-run environment marker was tried first: macOS does not show other
// processes' environments, so it cannot be read back.)
//
// Limits: more than `max_processes` members alive -> every member is
// SIGKILLed and the run ends as an error (`max_processes`); the sampled CPU
// time of every member above `cpu_seconds` -> the same (`cpu_seconds`); once
// the root has exited, every member left is killed, so its pipes close and
// nothing outlives the run.
//
// Residual limits (node README, the M2 record, carried into M3): an
// intermediate process that forks and exits before the kernel delivers the
// fork event to the monitor's thread and before a scan (microseconds) is
// never learnt, and so neither are its children; a fork burst can overshoot
// the count for up to one sample (`kern.maxprocperuid` is the backstop); CPU
// time of a process that lived less than a sample is not counted; between
// the identity check and `kill(2)` there is a window of microseconds (macOS
// has no pidfd).

import Darwin
import Foundation

/// A process as (pid, unique id, start time): a recycled pid is a different
/// identity.
public struct ProcessIdentity: Hashable, Sendable {
    public let pid: pid_t
    public let uniqueID: UInt64
    public let startSeconds: UInt64
    public let startMicroseconds: UInt64

    public init(pid: pid_t, uniqueID: UInt64, startSeconds: UInt64, startMicroseconds: UInt64) {
        self.pid = pid
        self.uniqueID = uniqueID
        self.startSeconds = startSeconds
        self.startMicroseconds = startMicroseconds
    }
}

/// `struct kevent` (the name is also the function's).
typealias KernelEvent = Darwin.kevent

/// kevent(2) with one change and no event list.
private func keventRegister(_ kq: Int32, _ change: UnsafePointer<KernelEvent>) -> Int32 {
    kevent(kq, change, 1, nil, 0, nil)
}

/// kevent(2) waiting for up to `events.count` events.
private func keventWait(_ kq: Int32, _ events: inout [KernelEvent], _ timeout: UnsafePointer<timespec>) -> Int32 {
    let capacity = Int32(events.count)
    return events.withUnsafeMutableBufferPointer { kevent(kq, nil, 0, $0.baseAddress, capacity, timeout) }
}

struct ProcessRow {
    let identity: ProcessIdentity
    /// The unique id of the process that forked it (kept across re-parenting).
    let parentUniqueID: UInt64
    let pgid: pid_t
}

/// sys/proc_info.h `struct proc_uniqidentifierinfo` (PROC_PIDUNIQIDENTIFIERINFO),
/// whose Swift import the SDK does not provide.
private struct UniqueIdentifierInfo {
    var uuid: (UInt64, UInt64) = (0, 0)
    var uniqueID: UInt64 = 0
    var parentUniqueID: UInt64 = 0
    var idVersion: Int32 = 0
    var reserve2: UInt32 = 0
    var reserve3: UInt64 = 0
    var reserve4: UInt64 = 0
}

public enum ProcessTree {
    static let uniqueIdentifierFlavor: Int32 = 17  // PROC_PIDUNIQIDENTIFIERINFO

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

    /// The current children of `pid` (`proc_listchildpids`).
    static func childPIDs(_ pid: pid_t) -> [pid_t] {
        var pids = [pid_t](repeating: 0, count: 256)
        let count = pids.withUnsafeMutableBufferPointer { buffer -> Int32 in
            proc_listchildpids(pid, buffer.baseAddress, Int32(buffer.count * MemoryLayout<pid_t>.size))
        }
        guard count > 0 else { return [] }
        return Array(pids.prefix(Int(count))).filter { $0 > 0 }
    }

    static func row(_ pid: pid_t) -> ProcessRow? {
        var bsd = proc_bsdinfo()
        let bsdSize = Int32(MemoryLayout<proc_bsdinfo>.size)
        guard proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &bsd, bsdSize) == bsdSize else { return nil }
        var unique = UniqueIdentifierInfo()
        let uniqueSize = Int32(MemoryLayout<UniqueIdentifierInfo>.size)
        guard proc_pidinfo(pid, uniqueIdentifierFlavor, 0, &unique, uniqueSize) == uniqueSize else { return nil }
        return ProcessRow(
            identity: ProcessIdentity(
                pid: pid, uniqueID: unique.uniqueID, startSeconds: bsd.pbi_start_tvsec,
                startMicroseconds: bsd.pbi_start_tvusec),
            parentUniqueID: unique.parentUniqueID, pgid: pid_t(bsd.pbi_pgid))
    }

    /// The identity of a live `pid`, nil when it is gone.
    public static func identity(of pid: pid_t) -> ProcessIdentity? { row(pid)?.identity }

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

    /// Signal `identity` only if its pid still names that same process.
    @discardableResult
    public static func signal(_ identity: ProcessIdentity, _ signal: Int32) -> Bool {
        guard identity.pid > 1, identity.pid != getpid(), Self.identity(of: identity.pid) == identity else {
            return false
        }
        return Darwin.kill(identity.pid, signal) == 0
    }
}

/// Learns one run's members and enforces `max_processes` and `cpu_seconds`.
final class TreeMonitor: @unchecked Sendable {
    static let interval: Double = 0.05

    private let root: ProcessIdentity?
    private let group: pid_t
    private let maxProcesses: Int
    private let cpuSeconds: Double
    private let lock = NSLock()
    private var timer: DispatchSourceTimer?
    private var cpuByProcess: [ProcessIdentity: Double] = [:]
    private var violation: String?
    private var rootExited = false
    private var stopped = false
    /// Every member ever learnt, by unique id (never reused within a boot).
    private var memberIDs: Set<UInt64> = []
    private(set) var peakProcesses = 0
    private let queue = DispatchQueue(label: "friday-node.tree-monitor", qos: .utility)
    private let kq: Int32

    init(root pid: pid_t, maxProcesses: Int, cpuSeconds: Double) {
        root = ProcessTree.identity(of: pid)
        group = pid
        self.maxProcesses = maxProcesses
        self.cpuSeconds = cpuSeconds
        kq = kqueue()
        if let root { memberIDs.insert(root.uniqueID) }
    }

    deinit {
        if kq >= 0 { close(kq) }
    }

    func start() {
        if let root { watch(root.pid) }
        if kq >= 0 {
            let thread = Thread { [weak self] in self?.forkEvents() }
            thread.name = "friday-node.fork-events"
            thread.start()
        }
        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(deadline: .now(), repeating: Self.interval, leeway: .milliseconds(5))
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
        stopped = true
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

    /// Kill every member now (terminate's SIGKILL step); each pid is checked
    /// against its identity first.
    func killTree() {
        queue.sync { kill(currentMembers()) }
    }

    private func kill(_ members: Set<ProcessIdentity>) {
        for member in members { ProcessTree.signal(member, SIGKILL) }
    }

    // MARK: Fork events

    private func watch(_ pid: pid_t) {
        guard kq >= 0 else { return }
        var change = KernelEvent()
        change.ident = UInt(pid)
        change.filter = Int16(EVFILT_PROC)
        change.flags = UInt16(EV_ADD | EV_ENABLE)
        change.fflags = UInt32(NOTE_FORK) | UInt32(NOTE_EXIT)
        _ = keventRegister(kq, &change)
    }

    /// Learn the children of `pid` (a member) at once, and watch them.
    private func learnChildren(of pid: pid_t) {
        guard let parent = ProcessTree.row(pid) else { return }
        lock.lock()
        let isMember = memberIDs.contains(parent.identity.uniqueID)
        lock.unlock()
        guard isMember else { return }
        for child in ProcessTree.childPIDs(pid) {
            guard let row = ProcessTree.row(child), row.parentUniqueID == parent.identity.uniqueID else { continue }
            lock.lock()
            let new = memberIDs.insert(row.identity.uniqueID).inserted
            lock.unlock()
            if new {
                watch(child)
                learnChildren(of: child)
            }
        }
    }

    private func forkEvents() {
        var events = [KernelEvent](repeating: KernelEvent(), count: 16)
        var timeout = timespec(tv_sec: 0, tv_nsec: 200_000_000)
        while true {
            lock.lock()
            let done = stopped
            lock.unlock()
            if done { return }
            let count = keventWait(kq, &events, &timeout)
            guard count > 0 else { continue }
            for event in events.prefix(Int(count)) where event.fflags & UInt32(NOTE_FORK) != 0 {
                learnChildren(of: pid_t(event.ident))
            }
        }
    }

    // MARK: Scans

    /// The live members now (on `queue`): every process whose recorded
    /// parent is a member, to a fixed point; plus the root's group while it
    /// is not reaped.
    private func currentMembers() -> Set<ProcessIdentity> {
        lock.lock()
        let exited = rootExited
        var known = memberIDs
        lock.unlock()
        var rows: [ProcessRow] = []
        for pid in ProcessTree.allPIDs() {
            if let row = ProcessTree.row(pid) { rows.append(row) }
        }
        let rootAlive = !exited && root.map { root in rows.contains { $0.identity == root } } ?? false
        var members: Set<ProcessIdentity> = []
        var changed = true
        while changed {
            changed = false
            for row in rows where !members.contains(row.identity) {
                let byLineage = known.contains(row.identity.uniqueID) || known.contains(row.parentUniqueID)
                let byGroup = rootAlive && row.pgid == group
                guard byLineage || byGroup else { continue }
                members.insert(row.identity)
                if known.insert(row.identity.uniqueID).inserted { changed = true }
            }
        }
        lock.lock()
        let fresh = known.subtracting(memberIDs)
        memberIDs.formUnion(known)
        lock.unlock()
        for member in members where fresh.contains(member.uniqueID) { watch(member.pid) }
        return members
    }

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
                for member in members {
                    if let cpu = ProcessTree.cpuSeconds(member.pid), ProcessTree.identity(of: member.pid) == member {
                        cpuByProcess[member] = max(cpuByProcess[member] ?? 0, cpu)
                    }
                }
                if cpuByProcess.values.reduce(0, +) > cpuSeconds { broken = "cpu_seconds" }
            }
            if let broken { violation = broken }
        }
        lock.unlock()
        if broken != nil || (exited && !members.isEmpty) { kill(members) }
        if exited && members.isEmpty { stop() }
    }
}
