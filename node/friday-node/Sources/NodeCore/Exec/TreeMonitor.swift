// Learns one sandboxed run's members and enforces `max_processes` and
// `cpu_seconds` (runbook B6; B6 review findings 2-3; hardening N1, N4). The
// membership and identity rules are ProcessTree.swift's.
//
// - Fail closed (N4): the monitor is built while the root is still
//   suspended (`POSIX_SPAWN_START_SUSPENDED`); when the root's identity
//   cannot be read, the unique-id self-test failed, or the kqueue or the
//   root's fork/exit watch cannot be set up, it throws, the caller kills the
//   suspended root's group, and the run is an `error`: nothing ever runs
//   unmonitored.
// - Learning (N1): the root's `NOTE_FORK`/`NOTE_EXIT` watch is registered
//   before it runs a single instruction. A fork event on a member reads that
//   member's children at once (on the event thread) and asks for a full
//   sweep; an exit event asks for a sweep too. A sweep reads every process
//   and adds, to a fixed point, every one whose recorded parent unique id is
//   a member's, also of members that have exited (their unique ids are never
//   forgotten).
// - Scans: every 5 ms for the first 2 s of a run (when a daemonising command
//   forks), then every 50 ms, and every 200 ms once no fork event came for
//   2 s.

import Darwin
import Foundation

public struct MonitorUnavailable: Error, Equatable, CustomStringConvertible {
    public let description: String
}

/// The last-resort orphan sweep (hardening N1). An intermediate process that
/// forks and exits before the monitor learns it leaves a child whose
/// recorded parent is unknown. Such a child is still the run's when ALL of
/// these hold: it was re-parented to launchd (ppid 1), it runs as this user,
/// it was created after the run's root (unique id and start time), its
/// recorded parent is not a live process, and macOS's responsibility
/// tracking names the same responsible process as for this app. The last
/// test is what makes it safe, and it holds only when this app IS its own
/// responsible process (the installed app, launched by launchd): then every
/// process the app's children ever create answers to the app, and no
/// unrelated process does. Under a test runner or a shell (where the
/// responsible process is the terminal or IDE, shared with unrelated
/// processes) the sweep is off. The responsibility call is the system's
/// `responsibility_get_pid_responsible_for_pid` (looked up at run time; when
/// it is absent the sweep is off).
enum OrphanSweep {
    private typealias ResponsibleFunction = @convention(c) (pid_t) -> pid_t

    private static let function: ResponsibleFunction? = {
        guard let symbol = dlsym(UnsafeMutableRawPointer(bitPattern: -2), "responsibility_get_pid_responsible_for_pid")
        else { return nil }
        return unsafeBitCast(symbol, to: ResponsibleFunction.self)
    }()

    static func responsible(for pid: pid_t) -> pid_t? {
        guard let function else { return nil }
        let answer = function(pid)
        return answer > 0 ? answer : nil
    }

    /// The responsible process every orphan must answer to, nil when the
    /// sweep is off.
    static var anchor: pid_t? {
        #if DEBUG
        if let forced = forcedFilter.value { _ = forced; return responsible(for: getpid()) }
        #endif
        guard let mine = responsible(for: getpid()), mine == getpid() else { return nil }
        return mine
    }

    #if DEBUG
    /// Tests only: turn the sweep on under a test runner, restricted to the
    /// candidates `filter` accepts (so nothing of the developer's session can
    /// be touched).
    static let forcedFilter = LockedBox<((pid_t) -> Bool)?>(nil)
    #endif

    static func accepts(_ pid: pid_t) -> Bool {
        #if DEBUG
        if let filter = forcedFilter.value { return filter(pid) }
        #endif
        return true
    }
}

final class LockedBox<Value>: @unchecked Sendable {
    private let lock = NSLock()
    private var stored: Value
    init(_ value: Value) { stored = value }
    var value: Value {
        get {
            lock.lock()
            defer { lock.unlock() }
            return stored
        }
        set {
            lock.lock()
            stored = newValue
            lock.unlock()
        }
    }
}

#if DEBUG
/// Tests only: force the orphan sweep on for candidates `filter` accepts.
public enum OrphanSweepTesting {
    public static func force(_ filter: ((pid_t) -> Bool)?) { OrphanSweep.forcedFilter.value = filter }
    public static var active: Bool { OrphanSweep.anchor != nil }
}

/// Tests only (DEBUG builds): make the next monitor fail to set up, to
/// exercise the fail-closed path.
public enum MonitorFailureInjection {
    private static let lock = NSLock()
    nonisolated(unsafe) private static var armed = false

    public static func failNext() {
        lock.lock()
        armed = true
        lock.unlock()
    }

    static func consume() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        let was = armed
        armed = false
        return was
    }
}
#endif

final class TreeMonitor: @unchecked Sendable {
    static let fastInterval: Double = 0.005
    static let fastPeriod: Double = 2
    static let interval: Double = 0.05
    static let quietInterval: Double = 0.2
    static let quietAfter: Double = 2

    private let root: ProcessIdentity
    private let group: pid_t
    private let maxProcesses: Int
    private let cpuSeconds: Double
    private let lock = NSLock()
    private var cpuByProcess: [ProcessIdentity: Double] = [:]
    private var violation: String?
    private var rootExited = false
    private var stopped = false
    private var sweepPending = false
    private let startedAt = Date()
    private var lastForkAt = Date()
    /// Every member ever learnt, by unique id (never reused within a boot;
    /// kept after the member exits, so its orphans still trace to it).
    private var memberIDs: Set<UInt64> = []
    private(set) var peakProcesses = 0
    private let queue = DispatchQueue(label: "friday-node.tree-monitor", qos: .userInitiated)
    private let kq: Int32

    /// Build the monitor for a root that is still suspended; throws when the
    /// run could not be monitored.
    init(root pid: pid_t, maxProcesses: Int, cpuSeconds: Double) throws {
        #if DEBUG
        if MonitorFailureInjection.consume() { throw MonitorUnavailable(description: "forced by a test") }
        #endif
        guard ProcessTree.selfTestPassed else { throw MonitorUnavailable(description: "process self-test failed") }
        guard let root = ProcessTree.identity(of: pid) else {
            throw MonitorUnavailable(description: "the root's identity cannot be read")
        }
        let kq = kqueue()
        guard kq >= 0 else { throw MonitorUnavailable(description: "kqueue failed") }
        self.root = root
        self.kq = kq
        group = pid
        self.maxProcesses = maxProcesses
        self.cpuSeconds = cpuSeconds
        memberIDs.insert(root.uniqueID)
        guard watch(pid) else {
            close(kq)
            throw MonitorUnavailable(description: "the root's fork/exit watch failed")
        }
    }

    deinit { close(kq) }

    /// Start the event thread and the scans (before the root is resumed).
    func start() {
        let thread = Thread { [weak self] in self?.forkEvents() }
        thread.name = "friday-node.fork-events"
        thread.qualityOfService = .userInitiated
        thread.start()
        scheduleSample(after: 0)
    }

    func stop() {
        lock.lock()
        stopped = true
        lock.unlock()
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
        sweepSoon()
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

    @discardableResult
    private func watch(_ pid: pid_t) -> Bool {
        var change = KernelEvent()
        change.ident = UInt(pid)
        change.filter = Int16(EVFILT_PROC)
        change.flags = UInt16(EV_ADD | EV_ENABLE)
        change.fflags = UInt32(NOTE_FORK) | UInt32(NOTE_EXIT)
        return keventRegister(kq, &change) == 0
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
        var events = [KernelEvent](repeating: KernelEvent(), count: 32)
        var timeout = timespec(tv_sec: 0, tv_nsec: 100_000_000)
        while true {
            lock.lock()
            let done = stopped
            lock.unlock()
            if done { return }
            let count = keventWait(kq, &events, &timeout)
            guard count > 0 else { continue }
            var forked = false
            for event in events.prefix(Int(count)) where event.fflags & UInt32(NOTE_FORK) != 0 {
                learnChildren(of: pid_t(event.ident))
                forked = true
            }
            if forked {
                lock.lock()
                lastForkAt = Date()
                lock.unlock()
            }
            sweepSoon()
        }
    }

    // MARK: Scans

    private func sweepSoon() {
        lock.lock()
        let already = sweepPending
        sweepPending = true
        lock.unlock()
        guard !already else { return }
        queue.async {
            self.lock.lock()
            self.sweepPending = false
            self.lock.unlock()
            self.sample()
        }
    }

    private func nextInterval() -> Double {
        lock.lock()
        defer { lock.unlock() }
        let now = Date()
        if now.timeIntervalSince(startedAt) < Self.fastPeriod { return Self.fastInterval }
        if now.timeIntervalSince(lastForkAt) > Self.quietAfter { return Self.quietInterval }
        return Self.interval
    }

    private func scheduleSample(after delay: Double) {
        queue.asyncAfter(deadline: .now() + delay) { [weak self] in
            guard let self else { return }
            self.lock.lock()
            let done = self.stopped
            self.lock.unlock()
            guard !done else { return }
            self.sample()
            self.scheduleSample(after: self.nextInterval())
        }
    }

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
        let rootAlive = !exited && rows.contains { $0.identity == root }
        let liveIDs = Set(rows.map(\.identity.uniqueID))
        let anchor = OrphanSweep.anchor
        let uid = getuid()
        var members: Set<ProcessIdentity> = []
        var changed = true
        while changed {
            changed = false
            for row in rows where !members.contains(row.identity) {
                let byLineage = known.contains(row.identity.uniqueID) || known.contains(row.parentUniqueID)
                let byGroup = rootAlive && row.pgid == group
                var byOrphanSweep = false
                if !byLineage && !byGroup, let anchor, row.ppid == 1, row.uid == uid,
                    row.identity.uniqueID > root.uniqueID, row.parentUniqueID > root.uniqueID,
                    !liveIDs.contains(row.parentUniqueID), row.identity.start >= root.start,
                    OrphanSweep.responsible(for: row.identity.pid) == anchor, OrphanSweep.accepts(row.identity.pid)
                {
                    byOrphanSweep = true
                }
                guard byLineage || byGroup || byOrphanSweep else { continue }
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
        // Stop only after the tree has stayed empty for a while: a process
        // still being created when the last member died is caught too.
        lock.lock()
        if exited && members.isEmpty {
            emptySince = emptySince ?? Date()
        } else {
            emptySince = nil
        }
        let quiet = emptySince.map { Date().timeIntervalSince($0) >= Self.emptyGrace } ?? false
        lock.unlock()
        if quiet { stop() }
    }

    static let emptyGrace: Double = 1.5
    private var emptySince: Date?
}
