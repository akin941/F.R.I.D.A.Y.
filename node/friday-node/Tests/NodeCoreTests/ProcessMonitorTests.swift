// The process monitor's lineage, orphan sweep and lifecycle (hardening N1,
// N4 and their review). Deterministic tests run always; the statistical
// retry probes are opt-in (`FRIDAY_PROBE=1`, optional `FRIDAY_PROBE_RUNS`),
// with documented bounds, and print a full record of every survivor.
//
// DEBUG only: they use the monitor's test seams (forced orphan sweep with a
// candidate filter, failure injection, registry introspection).

#if DEBUG

import Darwin
import Foundation
import NodeCore
import XCTest

enum MonitorTestSupport {
    static let limits = RunLimits(maxProcesses: 64, cpuSeconds: 60, maxOutputBytes: 1 << 20, killGraceSeconds: 1)

    static func sh(_ script: String) throws -> SandboxRun {
        try SandboxRun.start(
            executable: "/bin/sh", argv: ["sh", "-c", script], environment: ["PATH": "/usr/bin:/bin"],
            cwd: NSTemporaryDirectory(), limits: limits)
    }

    /// Alive and not a zombie.
    static func isRunning(_ pid: pid_t) -> Bool {
        guard kill(pid, 0) == 0 else { return false }
        var info = proc_bsdinfo()
        let size = Int32(MemoryLayout<proc_bsdinfo>.size)
        guard proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &info, size) == size else { return false }
        return info.pbi_status != 5  // SZOMB
    }

    /// Wait until no other run is being monitored (helpers that ran before a
    /// run's root do not matter to its sweep).
    static func waitForQuiet() {
        _ = Waiting.until(15) { MonitorRegistryTesting.registeredMonitors == 0 }
    }

    /// Plant an orphan outside every run: perl forks a child that calls
    /// setsid and execs sleep, then exits (the child is re-parented to
    /// launchd, its recorded parent gone). Returns the orphan's pid.
    static func plantOrphan(seconds: Int = 30) throws -> pid_t {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/perl")
        task.arguments = [
            "-MPOSIX", "-e",
            "$| = 1; exit if fork(); POSIX::setsid(); print \"$$\\n\"; close STDOUT; exec \"/bin/sleep\", \"\(seconds)\"",
        ]
        let pipe = Pipe()
        task.standardOutput = pipe
        try task.run()
        task.waitUntilExit()
        let text = String(decoding: pipe.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
        guard let pid = pid_t(text.trimmingCharacters(in: .whitespacesAndNewlines)) else {
            throw ExecutorError("no orphan pid")
        }
        return pid
    }

    static func describe(_ pid: pid_t) -> String {
        var info = proc_bsdinfo()
        _ = proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &info, Int32(MemoryLayout<proc_bsdinfo>.size))
        var path = [CChar](repeating: 0, count: 4096)
        _ = proc_pidpath(pid, &path, UInt32(path.count))
        typealias Responsible = @convention(c) (pid_t) -> pid_t
        let symbol = dlsym(UnsafeMutableRawPointer(bitPattern: -2), "responsibility_get_pid_responsible_for_pid")
        let responsible = symbol.map { unsafeBitCast($0, to: Responsible.self) }
        let identity = ProcessTree.identity(of: pid)
        var chain = "?"
        if let identity {
            let parent = ProcessTree.parentUniqueID(of: pid) ?? 0
            chain = "uid=\(identity.uniqueID) parent-uid=\(parent) "
                + "parent-known-to-a-monitor=\(MonitorRegistryTesting.anyMonitorKnows(parent)) "
                + "parent-alive=\(ProcessTree.identity(of: info.pbi_ppid == 1 ? 0 : pid_t(info.pbi_ppid)) != nil)"
        }
        return "pid=\(pid) ppid=\(info.pbi_ppid) status=\(info.pbi_status) path=\(String(cString: path)) "
            + "start=\(info.pbi_start_tvsec).\(info.pbi_start_tvusec) responsible=\(responsible?(pid) ?? -9) "
            + "self-responsible=\(responsible?(getpid()) ?? -9) \(chain) "
            + "known-to-a-monitor=\(identity.map { MonitorRegistryTesting.anyMonitorKnows($0.uniqueID) } ?? false)"
    }
}

final class ProcessMonitorTests: XCTestCase {
    override func tearDown() {
        OrphanSweepTesting.force(nil)
        super.tearDown()
    }

    /// Lineage, deterministically: the intermediate lives long enough to be
    /// learnt from its fork event, then forks a setsid child and exits; the
    /// child (re-parented to launchd, outside the group) is killed.
    func testASetsidChildOfALearntIntermediateIsKilled() throws {
        guard FileManager.default.isExecutableFile(atPath: "/usr/bin/perl") else { throw XCTSkip("no perl") }
        var printed = 0
        for attempt in 0..<5 {
            let run = try MonitorTestSupport.sh("""
                /usr/bin/perl -MPOSIX -e '$| = 1; exit if fork(); POSIX::setsid(); select(undef, undef, undef, 0.3); \
                exit if fork(); print "$$\\n"; close STDOUT; close STDERR; exec "/bin/sleep", "60"'; sleep 1
                """)
            let (output, outcome) = Waiting.finish(run, timeout: 15)
            XCTAssertNotNil(outcome)
            guard let pid = pid_t(output.trimmingCharacters(in: .whitespacesAndNewlines)), pid > 1 else { continue }
            printed += 1
            XCTAssertTrue(Waiting.until(5) { !MonitorTestSupport.isRunning(pid) }, "attempt \(attempt): \(MonitorTestSupport.describe(pid))")
            if MonitorTestSupport.isRunning(pid) { kill(pid, SIGKILL) }
        }
        // Not vacuous: at least one attempt got as far as printing its pid.
        XCTAssertGreaterThan(printed, 0, "no attempt produced a setsid child")
    }

    /// Always-run smoke test of the review's retry probe: 3 runs of 15
    /// intermediates that fork a setsid child and exit at once, bounded
    /// loosely (20 %); the opt-in probes below measure it properly.
    func testRetryProbeSmoke() throws {
        guard FileManager.default.isExecutableFile(atPath: "/usr/bin/perl") else { throw XCTSkip("no perl") }
        let result = try retryProbe(runs: 3)
        FileHandle.standardError.write(Data("retry probe (smoke): \(result.survivors) of \(result.children) survived\n".utf8))
        XCTAssertGreaterThan(result.children, 0)
        XCTAssertLessThanOrEqual(Double(result.survivors), Double(result.children) * 0.2)
    }

    /// The orphan sweep, deterministically: with the sweep on (forced, and
    /// limited to the one planted pid), a launchd-parented orphan created
    /// after the root, whose recorded parent is gone, is attributed to the
    /// run while it is the only live one, and dies with it.
    func testTheOrphanSweepKillsAPlantedOrphan() throws {
        guard FileManager.default.isExecutableFile(atPath: "/usr/bin/perl") else { throw XCTSkip("no perl") }
        MonitorTestSupport.waitForQuiet()
        let target = LockedPid()
        OrphanSweepTesting.force { $0 == target.value }
        let run = try MonitorTestSupport.sh("sleep 1.5")
        Thread.sleep(forTimeInterval: 0.2)
        let orphan = try MonitorTestSupport.plantOrphan()
        target.value = orphan
        let identity = try XCTUnwrap(ProcessTree.identity(of: orphan))
        XCTAssertTrue(Waiting.until(2) { MonitorRegistryTesting.anyMonitorKnows(identity.uniqueID) }, "not attributed")
        _ = Waiting.finish(run, timeout: 10)
        XCTAssertTrue(Waiting.until(2) { !MonitorTestSupport.isRunning(orphan) }, MonitorTestSupport.describe(orphan))
        if MonitorTestSupport.isRunning(orphan) { kill(orphan, SIGKILL) }
    }

    /// The control: with the sweep off, the same planted orphan is not the
    /// run's and survives it.
    func testWithoutTheSweepAPlantedOrphanIsNotTheRuns() throws {
        guard FileManager.default.isExecutableFile(atPath: "/usr/bin/perl") else { throw XCTSkip("no perl") }
        XCTAssertFalse(OrphanSweepTesting.active)
        let run = try MonitorTestSupport.sh("sleep 1")
        Thread.sleep(forTimeInterval: 0.2)
        let orphan = try MonitorTestSupport.plantOrphan()
        _ = Waiting.finish(run, timeout: 10)
        Thread.sleep(forTimeInterval: 0.5)
        XCTAssertTrue(MonitorTestSupport.isRunning(orphan))
        kill(orphan, SIGKILL)
    }

    /// Two concurrent runs (review finding 1): run 2 makes a real orphan
    /// candidate (an intermediate lives 0.5 s, forks the daemon and exits;
    /// the daemon calls setsid, so it is launchd-parented with its recorded
    /// parent kept and gone, newer than run 1's root) while run 1's forced orphan
    /// sweep is on. Run 1's root exits during run 2's life, so a wrong claim
    /// by run 1 would kill the daemon then. It must survive until run 2
    /// ends, and be gone after (run 2 learnt it).
    func testAnotherRunsDaemonisedChildSurvivesUntilThatRunEnds() throws {
        guard FileManager.default.isExecutableFile(atPath: "/usr/bin/perl") else { throw XCTSkip("no perl") }
        MonitorTestSupport.waitForQuiet()
        OrphanSweepTesting.force { pid in
            var path = [CChar](repeating: 0, count: 4096)
            guard proc_pidpath(pid, &path, UInt32(path.count)) > 0 else { return false }
            return String(cString: path) == "/bin/sleep"
        }
        let run1 = try MonitorTestSupport.sh("sleep 1.5")
        Thread.sleep(forTimeInterval: 0.2)
        let run2 = try MonitorTestSupport.sh("""
            /usr/bin/perl -MPOSIX -e '$| = 1; exit if fork(); my $d = fork(); \
            if ($d) { select(undef, undef, undef, 0.5); exit } POSIX::setsid(); print "$$\\n"; \
            close STDOUT; close STDERR; exec "/bin/sleep", "30"'; sleep 4
            """)
        var output = ""
        let deadline = Date().addingTimeInterval(3)
        var child: pid_t?
        while child == nil, Date() < deadline {
            for chunk in run2.read() { output += String(decoding: chunk.data, as: UTF8.self) }
            child = pid_t(output.trimmingCharacters(in: .whitespacesAndNewlines))
            Thread.sleep(forTimeInterval: 0.02)
        }
        let daemon = try XCTUnwrap(child, "run 2 printed no pid")
        // Once its parent (which lived 0.5 s after forking it) is gone, the
        // daemon is a real candidate: launchd-parented, recorded parent kept.
        XCTAssertTrue(Waiting.until(2) { ProcessTree.parentUniqueID(of: daemon).map { $0 > 1 } ?? false
            && MonitorTestSupport.describe(daemon).contains("ppid=1 ") })
        XCTAssertNotEqual(ProcessTree.parentUniqueID(of: daemon), 1, "the daemon's recorded parent was reset")
        // While run 2 lives (run 1's root exits at 1.5 s), the daemon lives.
        var checks = 0
        while !run2.hasExited, run2.poll() == nil, checks < 30 {
            XCTAssertTrue(MonitorTestSupport.isRunning(daemon), "run 1 killed run 2's daemon: \(MonitorTestSupport.describe(daemon))")
            if !MonitorTestSupport.isRunning(daemon) { break }
            Thread.sleep(forTimeInterval: 0.15)
            checks += 1
        }
        XCTAssertGreaterThan(checks, 15, "the window in which run 1's root exits was not observed")
        _ = Waiting.finish(run2, timeout: 10)
        // After run 2 ended, nothing of it outlives it.
        XCTAssertTrue(Waiting.until(4) { !MonitorTestSupport.isRunning(daemon) }, MonitorTestSupport.describe(daemon))
        if MonitorTestSupport.isRunning(daemon) { kill(daemon, SIGKILL) }
        _ = Waiting.finish(run1, timeout: 10)
    }

    /// Helpers that ran before a run's root (the git calls that create a
    /// worktree) do not turn its sweep off (review finding 3).
    func testHelpersBeforeTheRootLeaveTheSweepOn() throws {
        guard FileManager.default.isExecutableFile(atPath: "/usr/bin/perl") else { throw XCTSkip("no perl") }
        MonitorTestSupport.waitForQuiet()
        for _ in 0..<3 {
            _ = try BoundedRun.run(executable: "/usr/bin/git", argv: ["git", "--version"], environment: [:], timeout: 10)
        }
        XCTAssertFalse(MonitorRegistryTesting.helpersQuiet, "the helpers are recent")
        let target = LockedPid()
        OrphanSweepTesting.force { $0 == target.value }
        let run = try MonitorTestSupport.sh("sleep 1.5")
        Thread.sleep(forTimeInterval: 0.2)
        let orphan = try MonitorTestSupport.plantOrphan()
        target.value = orphan
        let identity = try XCTUnwrap(ProcessTree.identity(of: orphan))
        XCTAssertTrue(Waiting.until(2) { MonitorRegistryTesting.anyMonitorKnows(identity.uniqueID) }, "the sweep was off")
        _ = Waiting.finish(run, timeout: 10)
        XCTAssertTrue(Waiting.until(2) { !MonitorTestSupport.isRunning(orphan) })
        if MonitorTestSupport.isRunning(orphan) { kill(orphan, SIGKILL) }
    }

    /// A helper that starts after the root does turn the sweep off.
    func testAHelperAfterTheRootTurnsTheSweepOff() throws {
        guard FileManager.default.isExecutableFile(atPath: "/usr/bin/perl") else { throw XCTSkip("no perl") }
        MonitorTestSupport.waitForQuiet()
        let target = LockedPid()
        OrphanSweepTesting.force { $0 == target.value }
        let run = try MonitorTestSupport.sh("sleep 1.5")
        Thread.sleep(forTimeInterval: 0.1)
        _ = try BoundedRun.run(executable: "/usr/bin/git", argv: ["git", "--version"], environment: [:], timeout: 10)
        let orphan = try MonitorTestSupport.plantOrphan()
        target.value = orphan
        let identity = try XCTUnwrap(ProcessTree.identity(of: orphan))
        Thread.sleep(forTimeInterval: 1)
        XCTAssertFalse(MonitorRegistryTesting.anyMonitorKnows(identity.uniqueID), "attributed despite a newer helper")
        _ = Waiting.finish(run, timeout: 10)
        Thread.sleep(forTimeInterval: 0.3)
        XCTAssertTrue(MonitorTestSupport.isRunning(orphan))
        kill(orphan, SIGKILL)
    }

    /// Review N5 NIT: a watch failure after `kqueue()` throws and frees the
    /// monitor. This only checks that the monitor is freed and that a
    /// descriptor opened afterwards stays open; it cannot by itself detect a
    /// double close inside the failing init (nothing can reuse the number
    /// between two closes there). The code closes the kqueue in one place,
    /// `closeQueue()`, guarded by a flag under the lock.
    func testAWatchFailureAfterKqueueFreesTheMonitor() throws {
        let path = NSTemporaryDirectory() + "friday-fd-probe-\(UUID().uuidString)"
        FileManager.default.createFile(atPath: path, contents: Data("x".utf8))
        defer { try? FileManager.default.removeItem(atPath: path) }
        let baseline = MonitorFailureInjection.liveMonitors
        for _ in 0..<20 {
            MonitorFailureInjection.failNextWatch()
            XCTAssertThrowsError(try MonitorTestSupport.sh("true"))
            // A descriptor opened right after must stay open (a double close
            // of the freed kqueue number would close it).
            let fd = open(path, O_RDONLY)
            XCTAssertGreaterThanOrEqual(fd, 0)
            _ = Waiting.until(0.05) { false }
            var info = stat()
            XCTAssertEqual(fstat(fd, &info), 0, "a descriptor was closed behind our back")
            close(fd)
        }
        XCTAssertTrue(Waiting.until(2) { MonitorFailureInjection.liveMonitors <= baseline }, "a failed monitor was not freed")
    }

    /// Review N4: a finished run's monitor stops and is freed.
    func testAFinishedRunsMonitorIsFreed() throws {
        let baseline = MonitorFailureInjection.liveMonitors
        do {
            let run = try MonitorTestSupport.sh("echo hi")
            _ = Waiting.finish(run, timeout: 10)
        }
        XCTAssertTrue(Waiting.until(5) { MonitorFailureInjection.liveMonitors <= baseline })
    }

    // MARK: Opt-in statistical probes (FRIDAY_PROBE=1)

    /// The review's retry probe: each run forks 15 intermediates, each of
    /// which forks a setsid child and exits at once; children alive 0.7 s
    /// after the run are survivors (killed, and described).
    private func retryProbe(runs: Int) throws -> (survivors: Int, children: Int) {
        let script = """
            /usr/bin/perl -MPOSIX -e '$| = 1; for (1..15) { my $i = fork(); next if $i; \
            my $c = fork(); exit if $c; POSIX::setsid(); print "$$\\n"; close STDOUT; close STDERR; \
            exec "/bin/sleep", "60" }'
            """
        var children = 0
        var survivors = 0
        for _ in 0..<runs {
            let run = try MonitorTestSupport.sh(script)
            let rootID = ProcessTree.identity(of: run.pid)?.uniqueID ?? 0
            let (output, _) = Waiting.finish(run, timeout: 20)
            let pids = output.split(separator: "\n").compactMap { pid_t($0.trimmingCharacters(in: .whitespaces)) }
            children += pids.count
            let allIDs = pids.compactMap { ProcessTree.identity(of: $0)?.uniqueID }.sorted()
            Thread.sleep(forTimeInterval: 0.7)
            for pid in pids where MonitorTestSupport.isRunning(pid) {
                survivors += 1
                FileHandle.standardError.write(Data(
                    ("retry probe survivor: \(MonitorTestSupport.describe(pid)) run-root-uid=\(rootID) "
                        + "this-run's-child-uids=\(allIDs)\n").utf8))
                kill(pid, SIGKILL)
            }
            MonitorTestSupport.waitForQuiet()
        }
        return (survivors, children)
    }

    private func probeRuns() throws -> Int {
        guard ProcessInfo.processInfo.environment["FRIDAY_PROBE"] == "1" else {
            throw XCTSkip("statistical probe: set FRIDAY_PROBE=1 (and FRIDAY_PROBE_RUNS, default 40)")
        }
        guard FileManager.default.isExecutableFile(atPath: "/usr/bin/perl") else { throw XCTSkip("no perl") }
        return Int(ProcessInfo.processInfo.environment["FRIDAY_PROBE_RUNS"] ?? "") ?? 40
    }

    /// Bound: at most 0.5 % survivors with the sweep on (restricted to the
    /// probe's perl and sleep).
    func testRetryProbeWithTheOrphanSweep() throws {
        let runs = try probeRuns()
        OrphanSweepTesting.force { pid in
            var path = [CChar](repeating: 0, count: 4096)
            guard proc_pidpath(pid, &path, UInt32(path.count)) > 0 else { return false }
            return ["/bin/sleep", "/usr/bin/perl"].contains(String(cString: path))
        }
        let result = try retryProbe(runs: runs)
        FileHandle.standardError.write(Data(
            "retry probe (orphan sweep): \(result.survivors) of \(result.children) survived (\(runs) runs)\n".utf8))
        XCTAssertLessThanOrEqual(Double(result.survivors), Double(result.children) * 0.005)
    }

    /// Bound: at most 2 % survivors with lineage and events only (catches a
    /// regression from the measured ~0.5 % to ~4 %).
    func testRetryProbeWithLineageOnly() throws {
        let runs = try probeRuns()
        let result = try retryProbe(runs: runs)
        FileHandle.standardError.write(Data(
            "retry probe (lineage only): \(result.survivors) of \(result.children) survived (\(runs) runs)\n".utf8))
        XCTAssertLessThanOrEqual(Double(result.survivors), Double(result.children) * 0.02)
    }
}

final class LockedPid: @unchecked Sendable {
    private let lock = NSLock()
    private var pid: pid_t = -1
    var value: pid_t {
        get {
            lock.lock()
            defer { lock.unlock() }
            return pid
        }
        set {
            lock.lock()
            pid = newValue
            lock.unlock()
        }
    }
}

#endif
