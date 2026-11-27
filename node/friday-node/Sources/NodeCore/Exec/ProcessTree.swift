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
// (`p_puniqueid`) is the unique id of a member, starting from the root.
// The kernel does NOT always keep that id when the process is re-parented
// to launchd (measured, B6 hardening review): it was kept for a `setsid`
// child and for a child whose parent lived about 0.5 s after forking it, and
// reset to 1 for a non-setsid child whose parent exits at once and for
// `( cmd & )`, and even a setsid child's id was occasionally reset. Where it
// is kept, a child whose parent is gone is still found by its recorded
// parent. Where it is reset, the child is found only if it was learnt while
// its parent lived (fork events, the process group, scans); the orphan
// sweep's `parentUniqueID > root` condition skips such a child. In the
// review's probe, non-setsid daemons were still contained 0 of 60 times that
// way.
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

    /// The start time in seconds since the epoch.
    var start: Double { Double(startSeconds) + Double(startMicroseconds) / 1_000_000 }
}

/// `struct kevent` (the name is also the function's).
typealias KernelEvent = Darwin.kevent

/// kevent(2) with one change and no event list.
func keventRegister(_ kq: Int32, _ change: UnsafePointer<KernelEvent>) -> Int32 {
    kevent(kq, change, 1, nil, 0, nil)
}

/// kevent(2) waiting for up to `events.count` events.
func keventWait(_ kq: Int32, _ events: inout [KernelEvent], _ timeout: UnsafePointer<timespec>) -> Int32 {
    let capacity = Int32(events.count)
    return events.withUnsafeMutableBufferPointer { kevent(kq, nil, 0, $0.baseAddress, capacity, timeout) }
}

struct ProcessRow {
    let identity: ProcessIdentity
    /// The unique id of the process that forked it (often, not always, kept
    /// after re-parenting to launchd; 1 when the kernel reset it).
    let parentUniqueID: UInt64
    let pgid: pid_t
    let ppid: pid_t
    let uid: uid_t
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
            parentUniqueID: unique.parentUniqueID, pgid: pid_t(bsd.pbi_pgid), ppid: pid_t(bsd.pbi_ppid),
            uid: bsd.pbi_uid)
    }

    /// The identity of a live `pid`, nil when it is gone.
    public static func identity(of pid: pid_t) -> ProcessIdentity? { row(pid)?.identity }

    /// The unique id of the process that forked `pid` (diagnostics).
    public static func parentUniqueID(of pid: pid_t) -> UInt64? { row(pid)?.parentUniqueID }

    /// The monitor's kernel interfaces work on this system (hardening N4):
    /// the unique-id flavor answers for this process with plausible ids (its
    /// own after its parent's), and a kqueue can be made. Asked once; when
    /// it fails, `repo.command.run` is `unsupported` (never run unmonitored).
    public static let selfTestPassed: Bool = {
        guard let me = row(getpid()), me.identity.uniqueID != 0, me.parentUniqueID != 0,
            me.parentUniqueID < me.identity.uniqueID
        else { return false }
        // Launched by launchd (the app): its row may not be readable.
        if getppid() != 1 {
            guard let parent = row(getppid()), parent.identity.uniqueID == me.parentUniqueID else { return false }
        }
        let probe = kqueue()
        guard probe >= 0 else { return false }
        close(probe)
        return true
    }()

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

