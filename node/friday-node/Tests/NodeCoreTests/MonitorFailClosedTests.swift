// Hardening N4: a run that cannot be monitored never runs. The monitor is
// set up while the root is still suspended; when that fails (forced here),
// the suspended root's group is killed, `start` throws (the session reports
// the run as an `error`), and the command never executed one instruction.
// The kernel interfaces the monitor needs pass their self-test on this Mac.

import Foundation
import NodeCore
import XCTest

final class MonitorFailClosedTests: XCTestCase {
    func testTheUniqueIDSelfTestPassesHere() {
        XCTAssertTrue(ProcessTree.selfTestPassed)
        let me = ProcessTree.identity(of: getpid())
        XCTAssertNotNil(me)
        XCTAssertGreaterThan(me?.uniqueID ?? 0, 0)
    }

    #if DEBUG
    func testAnUnmonitorableRunNeverRuns() throws {
        let base = resolvedPath(try makeTempDirectory())
        defer { try? FileManager.default.removeItem(atPath: base) }
        let marker = base + "/RAN"
        MonitorFailureInjection.failNext()
        XCTAssertThrowsError(
            try SandboxRun.start(
                executable: "/usr/bin/touch", argv: ["touch", marker], environment: [:], cwd: base,
                limits: RunLimits(maxProcesses: 8, cpuSeconds: 5, maxOutputBytes: 1024, killGraceSeconds: 1)))
        Thread.sleep(forTimeInterval: 0.3)
        XCTAssertFalse(FileManager.default.fileExists(atPath: marker), "the command ran unmonitored")
        // The next run, monitored, does run.
        let run = try SandboxRun.start(
            executable: "/usr/bin/touch", argv: ["touch", marker], environment: [:], cwd: base,
            limits: RunLimits(maxProcesses: 8, cpuSeconds: 5, maxOutputBytes: 1024, killGraceSeconds: 1))
        let (_, outcome) = Waiting.finish(run, timeout: 10)
        XCTAssertEqual(outcome?.exitCode, 0)
        XCTAssertTrue(FileManager.default.fileExists(atPath: marker))
    }
    #endif
}
