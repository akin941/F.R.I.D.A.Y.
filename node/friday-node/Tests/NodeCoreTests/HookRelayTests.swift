// The hook relay and spool (Phase 9 brief M3): backend/nodes/tests/
// test_reference_spool.py and test_reference_hook_relay.py re-expressed
// against the Swift Spool, HookSocket, HookClient and NodeSession.

import Foundation
import NodeCore
import XCTest

private func event(_ n: Int, pad: Int = 0) -> JSONValue {
    var fields: [String: JSONValue] = [
        "hook_event_name": "SessionStart", "session_id": .string("cc-\(n)"), "cwd": "/Users/owner/work/acme",
        "occurred_at": "2026-11-26T19:00:00+00:00", "event_id": .string(String(format: "%032x", n)),
    ]
    if pad > 0 { fields["source"] = .string(String(repeating: "p", count: pad)) }
    return .object(fields)
}

private func shortDirectory() -> String {
    let path = "/tmp/fnh-" + String(UUID().uuidString.prefix(8)).lowercased()
    try? FileManager.default.createDirectory(atPath: path, withIntermediateDirectories: true)
    return path
}

private func mode(_ path: String) -> Int {
    var info = stat()
    lstat(path, &info)
    return Int(info.st_mode & 0o777)
}

final class SpoolTests: XCTestCase {
    func testTheNormativeLimits() {
        XCTAssertEqual(Spool.maxBytes, 10 * 1024 * 1024)
        XCTAssertEqual(Spool.maxAgeSeconds, 24 * 3600)
        XCTAssertEqual(Spool.maxBatchEvents, 50)
    }

    func testFilesAre0600InA0700Directory() throws {
        let base = shortDirectory()
        defer { try? FileManager.default.removeItem(atPath: base) }
        let spool = try Spool(directory: base + "/spool", clock: ManualClock(1_796_000_000))
        let id = try XCTUnwrap(spool.append([event(1)]))
        XCTAssertEqual(mode(base + "/spool"), 0o700)
        XCTAssertEqual(mode(base + "/spool/\(id).json"), 0o600)
    }

    func testALooseDirectoryIsTightenedAndASymlinkOrFileRefused() throws {
        let base = shortDirectory()
        defer { try? FileManager.default.removeItem(atPath: base) }
        try FileManager.default.createDirectory(atPath: base + "/loose", withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o755])
        _ = try Spool(directory: base + "/loose", clock: ManualClock(0))
        XCTAssertEqual(mode(base + "/loose"), 0o700)
        try FileManager.default.createSymbolicLink(atPath: base + "/link", withDestinationPath: base + "/loose")
        XCTAssertThrowsError(try Spool(directory: base + "/link", clock: ManualClock(0)))
        try Data("x".utf8).write(to: URL(fileURLWithPath: base + "/file"))
        XCTAssertThrowsError(try Spool(directory: base + "/file", clock: ManualClock(0)))
    }

    func testOldestBatchesAreDroppedFirstOverTheSizeCap() throws {
        let base = shortDirectory()
        defer { try? FileManager.default.removeItem(atPath: base) }
        let spool = try Spool(directory: base + "/spool", clock: ManualClock(0), maxBytes: 1000)
        let ids = (0..<6).compactMap { spool.append([event($0, pad: 200)]) }
        let remaining = spool.pending().map(\.batchID)
        XCTAssertEqual(remaining, Array(ids.suffix(remaining.count)))
        XCTAssertFalse(remaining.contains(ids[0]))
        let depth = spool.depth()
        XCTAssertEqual(depth.count, remaining.count)
        XCTAssertLessThanOrEqual(depth.bytes, 1000)
        XCTAssertEqual(spool.dropped, ids.count - remaining.count)
    }

    func testABatchLargerThanTheWholeBudgetIsRefused() throws {
        let base = shortDirectory()
        defer { try? FileManager.default.removeItem(atPath: base) }
        let spool = try Spool(directory: base + "/spool", clock: ManualClock(0), maxBytes: 300)
        XCTAssertNil(spool.append([event(1, pad: 500)]))
        XCTAssertEqual(spool.depth().count, 0)
        XCTAssertEqual(spool.depth().bytes, 0)
    }

    func testBatchesOlderThan24HoursAreDropped() throws {
        let base = shortDirectory()
        defer { try? FileManager.default.removeItem(atPath: base) }
        let clock = ManualClock(1_796_000_000)
        let spool = try Spool(directory: base + "/spool", clock: clock)
        let old = spool.append([event(1)])
        clock.advance(23 * 3600)
        let young = spool.append([event(2)])
        clock.advance(3601)
        XCTAssertEqual(spool.pending().map(\.batchID), [young!])
        XCTAssertFalse(spool.pending().map(\.batchID).contains(old!))
    }

    func testEventsOnly() throws {
        let base = shortDirectory()
        defer { try? FileManager.default.removeItem(atPath: base) }
        let spool = try Spool(directory: base + "/spool", clock: ManualClock(0))
        XCTAssertNil(spool.append([]))
        XCTAssertNil(spool.append(["not an event"]))
        XCTAssertNil(spool.append((0..<51).map { event($0) }))
        XCTAssertNotNil(spool.append((0..<50).map { event($0) }))
    }

    func testABatchIsDeletedOnlyAfterItsAckAndSurvivesARestart() throws {
        let base = shortDirectory()
        defer { try? FileManager.default.removeItem(atPath: base) }
        let clock = ManualClock(0)
        let spool = try Spool(directory: base + "/spool", clock: clock)
        let first = try XCTUnwrap(spool.append([event(1)]))
        let second = try XCTUnwrap(spool.append([event(2)]))
        XCTAssertEqual(spool.first()?.batchID, first)
        XCTAssertEqual(spool.first()?.batchID, first)  // reading never deletes
        let restarted = try Spool(directory: base + "/spool", clock: clock)
        XCTAssertEqual(restarted.pending().map(\.batchID), [first, second])
        XCTAssertTrue(restarted.ack(first))
        XCTAssertFalse(restarted.ack(first))
        XCTAssertFalse(restarted.ack("../../etc/passwd"))
        let third = try XCTUnwrap(restarted.append([event(3)]))
        XCTAssertGreaterThan(third, second)  // ids keep increasing across restarts
        XCTAssertEqual(restarted.pending().map { $0.events[0]["session_id"] }, ["cc-2", "cc-3"])
    }

    func testACorruptBatchFileIsDropped() throws {
        let base = shortDirectory()
        defer { try? FileManager.default.removeItem(atPath: base) }
        let spool = try Spool(directory: base + "/spool", clock: ManualClock(0))
        let good = spool.append([event(1)])
        try Data("{nope".utf8).write(to: URL(fileURLWithPath: base + "/spool/000000000099.json"))
        XCTAssertEqual(spool.pending().map(\.batchID), [good!])
    }
}

final class SessionEventTests: XCTestCase {
    var base = ""
    var session: NodeSession!

    override func setUpWithError() throws {
        base = shortDirectory()
        let clock = ManualClock(1_796_000_005)
        session = NodeSession(
            nodeID: "6f1e2d3c-4b5a-4987-8a6b-5c4d3e2f1a0b", pinned: [:], catalogue: NodeCatalogue.defaultPolicy(),
            readers: PolicyTests.CountingReader(), clock: clock, nodeVersion: "friday-node test", osVersion: "macOS test")
        session.spool = try Spool(directory: base + "/spool", clock: clock)
    }

    override func tearDown() {
        try? FileManager.default.removeItem(atPath: base)
    }

    private func helloAck() -> Data {
        try! JSON.canonical([
            "v": 1, "type": "hello.ack", "id": "4f5b027e-07eb-4be5-a70e-d157c4bb21aa",
            "node_id": "6f1e2d3c-4b5a-4987-8a6b-5c4d3e2f1a0b", "heartbeat_seconds": 15, "rotate_after_days": 30,
            "server_time": 1_796_000_000, "accepted": ["apps.frontmost"],
        ])
    }

    private func eventAck(_ id: JSONValue?) -> Data {
        try! JSON.canonical([
            "v": 1, "type": "event.ack", "id": .string(UUID().uuidString.lowercased()),
            "event_id": id ?? .string(UUID().uuidString.lowercased()), "stored": 1, "duplicate": 0, "dropped": 0,
        ])
    }

    func testALiveEventIsSentAfterHelloAckAndNeverAnswered() throws {
        _ = session.connect()
        XCTAssertEqual(session.relayHookEvent(event(1)), [])  // before hello.ack: spooled
        XCTAssertEqual(session.spool?.depth().count, 1)
        let upload = session.receive(helloAck())
        XCTAssertEqual(upload.count, 1)
        XCTAssertEqual(upload[0]["type"], "event")
        XCTAssertEqual(upload[0]["spooled"], true)
        XCTAssertEqual(upload[0]["kind"], "claude_code.hook")
        XCTAssertEqual(upload[0]["batch"], [event(1)])
        XCTAssertEqual(session.receive(eventAck(upload[0]["id"])), [])
        XCTAssertEqual(session.spool?.depth().count, 0)
        let live = session.relayHookEvent(event(2))
        XCTAssertEqual(live.count, 1)
        XCTAssertEqual(live[0]["spooled"], false)
        XCTAssertEqual(live[0]["batch"], [event(2)])
        XCTAssertNoThrow(try NodeProtocol.validate(live[0], direction: .nodeToServer))
        XCTAssertEqual(session.receive(eventAck(live[0]["id"])), [])
    }

    func testSpooledBatchesUploadInOrderOneInFlight() {
        for n in 0..<3 { _ = session.relayHookEvent(event(n)) }  // offline: spooled
        XCTAssertEqual(session.spool?.depth().count, 3)
        _ = session.connect()
        let first = session.receive(helloAck())
        XCTAssertEqual(first.first?["batch"]?.arrayValue?.first?["session_id"], "cc-0")
        XCTAssertEqual(session.relayHookEvent(event(9)), [])  // one in flight: spooled behind
        let second = session.receive(eventAck(first.first?["id"]))
        XCTAssertEqual(second.first?["batch"]?.arrayValue?.first?["session_id"], "cc-1")
        let third = session.receive(eventAck(second.first?["id"]))
        let fourth = session.receive(eventAck(third.first?["id"]))
        XCTAssertEqual(fourth.first?["batch"]?.arrayValue?.first?["session_id"], "cc-9")
        XCTAssertEqual(session.receive(eventAck(fourth.first?["id"])), [])
        XCTAssertEqual(session.spool?.depth().count, 0)
    }

    func testAStrayAckDeletesNothing() {
        _ = session.relayHookEvent(event(1))
        _ = session.connect()
        _ = session.receive(helloAck())
        XCTAssertEqual(session.receive(eventAck(nil)), [])
        XCTAssertEqual(session.strayEventAcks, 1)
        XCTAssertEqual(session.spool?.depth().count, 1)
    }

    func testAnUnackedUploadStaysSpooledAndIsResentAfterReconnect() {
        _ = session.relayHookEvent(event(1))
        _ = session.connect()
        let upload = session.receive(helloAck())
        session.disconnect()
        XCTAssertEqual(session.spool?.depth().count, 1)
        _ = session.connect()
        let again = session.receive(helloAck())
        XCTAssertEqual(again.first?["batch"], upload.first?["batch"])
        XCTAssertNotEqual(again.first?["id"], upload.first?["id"])
    }

    func testALiveEventNotAckedBeforeTheSocketDropsIsSpooled() {
        _ = session.connect()
        _ = session.receive(helloAck())
        let live = session.relayHookEvent(event(1))
        XCTAssertEqual(live.count, 1)
        session.disconnect()
        XCTAssertEqual(session.spool?.pending().first?.events, [event(1)])
    }

    func testWithoutASpoolOfflineEventsAreDroppedAndCounted() {
        session.spool = nil
        XCTAssertEqual(session.relayHookEvent(event(1)), [])
        XCTAssertEqual(session.eventsDropped, 1)
    }

    func testHeartbeatsCarryTheSpoolDepth() {
        _ = session.relayHookEvent(event(1))
        _ = session.connect()
        (session.clock as! ManualClock).advance(15)
        let heartbeat = session.tick().first { $0["type"] == "heartbeat" }
        XCTAssertEqual(heartbeat?["spool_count"], 1)
        XCTAssertGreaterThan(heartbeat?["spool_bytes"]?.intValue ?? 0, 0)
    }
}

final class HookRelayTests: XCTestCase {
    var base = ""
    var delivered: [JSONValue] = []
    let lock = NSLock()

    override func setUp() {
        base = shortDirectory()
        delivered = []
    }

    override func tearDown() {
        try? FileManager.default.removeItem(atPath: base)
    }

    private func relay(peerUID: @escaping (Int32) -> uid_t? = HookSocket.getpeereidUID) throws -> HookSocket {
        let socket = HookSocket(
            path: base + "/sock/node.sock", clock: ManualClock(1_796_000_000),
            deliver: { [weak self] event in
                self?.lock.lock()
                self?.delivered.append(event)
                self?.lock.unlock()
            },
            peerUID: peerUID, makeID: { "0123456789abcdef0123456789abcdef" })
        try socket.start()
        return socket
    }

    private func waitFor(_ socket: HookSocket, _ outcome: HookOutcome, count: Int = 1) {
        let deadline = Date().addingTimeInterval(5)
        while socket.count(of: outcome) < count && Date() < deadline { usleep(10_000) }
    }

    func testTheSocketIs0600InA0700Directory() throws {
        let socket = try relay()
        defer { socket.stop() }
        XCTAssertEqual(mode(base + "/sock"), 0o700)
        XCTAssertEqual(mode(base + "/sock/node.sock"), 0o600)
    }

    func testAHookEventIsRelayedAllowListedAndScrubbed() throws {
        let socket = try relay()
        defer { socket.stop() }
        let payload: JSONValue = [
            "hook_event_name": "Notification", "session_id": "cc-1", "cwd": "/Users/owner/work/acme",
            "notification_type": "idle", "message": "token: abcdefghijklmnopqrstuvwxyz0123 waiting",
            "transcript_path": "/Users/owner/.claude/secret.jsonl", "extra": 1,
        ]
        HookClient.relay(try JSON.canonical(payload), to: socket.path)
        waitFor(socket, .relayed)
        lock.lock()
        let got = delivered
        lock.unlock()
        XCTAssertEqual(got.count, 1)
        let fields = try XCTUnwrap(got.first?.objectValue)
        XCTAssertEqual(Set(fields.keys), ["hook_event_name", "session_id", "cwd", "notification_type", "message",
                                         "occurred_at", "event_id"])
        // Exactly what backend/nodes/reference/hook_relay.py build_hook_event
        // returns for this payload and clock.
        XCTAssertEqual(fields["message"], .string("token: [SECRET:assignment] waiting"))
        XCTAssertEqual(fields["occurred_at"], "2026-11-30T00:53:20+00:00")
        XCTAssertEqual(fields["event_id"], "0123456789abcdef0123456789abcdef")
    }

    func testTheEventShapeMatchesFridayHook() throws {
        // Python's friday_hook.build_event on the same payload and clock
        // (values computed with backend/integrations/cli/friday_hook.py).
        let long = String(repeating: "x", count: 1500)
        let payload = try XCTUnwrap(HookEvent.parse(try JSON.canonical([
            "hook_event_name": "Stop", "session_id": .string(long), "reason": "", "message": .string(String(repeating: "m", count: 300)),
        ])))
        let built = try XCTUnwrap(HookEvent.build(payload, now: 1_796_000_000, eventID: "e"))
        XCTAssertEqual(built["session_id"]?.stringValue?.count, 1000)
        XCTAssertNil(built["reason"])  // empty strings are left out
        XCTAssertEqual(built["message"]?.stringValue?.count, 200)
        XCTAssertNil(HookEvent.build(try XCTUnwrap(HookEvent.parse(Data(#"{"hook_event_name":"PreToolUse"}"#.utf8))), now: 0, eventID: "e"))
        XCTAssertNil(HookEvent.build(try XCTUnwrap(HookEvent.parse(Data("[1]".utf8))), now: 0, eventID: "e"))
    }

    func testAConnectionFromAnotherUIDIsRefusedBeforeReading() throws {
        let socket = try relay(peerUID: { _ in getuid() + 1 })
        defer { socket.stop() }
        HookClient.relay(Data(#"{"hook_event_name":"Stop"}"#.utf8), to: socket.path)
        waitFor(socket, .peerRefused)
        XCTAssertEqual(socket.count(of: .peerRefused), 1)
        lock.lock()
        XCTAssertTrue(delivered.isEmpty)
        lock.unlock()
    }

    func testAFailingPeerCredentialCheckFailsClosed() throws {
        let socket = try relay(peerUID: { _ in nil })
        defer { socket.stop() }
        HookClient.relay(Data(#"{"hook_event_name":"Stop"}"#.utf8), to: socket.path)
        waitFor(socket, .peerRefused)
        XCTAssertEqual(socket.count(of: .peerRefused), 1)
    }

    func testGetpeereidReadsTheRealPeerUID() throws {
        let socket = try relay()
        defer { socket.stop() }
        HookClient.relay(Data(#"{"hook_event_name":"Stop"}"#.utf8), to: socket.path)
        waitFor(socket, .relayed)
        XCTAssertEqual(socket.count(of: .relayed), 1)
    }

    func testOversizedInputIsDroppedWhole() throws {
        let socket = try relay()
        defer { socket.stop() }
        var big = Data(#"{"hook_event_name":"Stop","pad":""#.utf8)
        big.append(Data(repeating: UInt8(ascii: "a"), count: HookEvent.maxInputBytes))
        big.append(Data(#""}"#.utf8))
        HookClient.relay(big, to: socket.path)
        waitFor(socket, .oversized)
        XCTAssertEqual(socket.count(of: .oversized), 1)
        XCTAssertTrue(delivered.isEmpty)
    }

    func testMalformedAndUnknownEventsAreDropped() throws {
        let socket = try relay()
        defer { socket.stop() }
        HookClient.relay(Data("{nope".utf8), to: socket.path)
        waitFor(socket, .malformed)
        HookClient.relay(Data(#"{"hook_event_name":"PreToolUse"}"#.utf8), to: socket.path)
        waitFor(socket, .ignored)
        XCTAssertEqual(socket.count(of: .malformed), 1)
        XCTAssertEqual(socket.count(of: .ignored), 1)
        XCTAssertTrue(delivered.isEmpty)
    }

    func testAnExistingNonSocketPathIsRefused() throws {
        try FileManager.default.createDirectory(atPath: base + "/sock", withIntermediateDirectories: true)
        try Data("x".utf8).write(to: URL(fileURLWithPath: base + "/sock/node.sock"))
        XCTAssertThrowsError(try relay())
    }

    func testASymlinkedSocketDirectoryIsRefused() throws {
        try FileManager.default.createDirectory(atPath: base + "/real", withIntermediateDirectories: true)
        try FileManager.default.createSymbolicLink(atPath: base + "/sock", withDestinationPath: base + "/real")
        XCTAssertThrowsError(try relay())
    }

    /// The CLI exits 0 quickly when the node is down (no socket), and when
    /// stdin never closes (the 3 s alarm).
    func testTheCLIExitsZeroQuicklyWhenTheNodeIsDown() throws {
        let cli = Repo.url("node/friday-node/.build/debug/friday-node")
        guard FileManager.default.fileExists(atPath: cli.path) else { throw XCTSkip("build the friday-node product first") }
        let process = Process()
        process.executableURL = cli
        process.arguments = ["hook"]
        process.environment = ["HOME": base]  // no socket there
        let stdin = Pipe()
        process.standardInput = stdin
        let started = Date()
        try process.run()
        stdin.fileHandleForWriting.write(Data(#"{"hook_event_name":"Stop"}"#.utf8))
        try stdin.fileHandleForWriting.close()
        process.waitUntilExit()
        XCTAssertEqual(process.terminationStatus, 0)
        XCTAssertLessThan(Date().timeIntervalSince(started), 3.5)

        let stuck = Process()
        stuck.executableURL = cli
        stuck.arguments = ["hook"]
        stuck.environment = ["HOME": base]
        let open = Pipe()  // never closed: the alarm must end it
        stuck.standardInput = open
        let startedStuck = Date()
        try stuck.run()
        stuck.waitUntilExit()
        XCTAssertEqual(stuck.terminationStatus, 0)
        XCTAssertLessThan(Date().timeIntervalSince(startedStuck), 4.0)
        _ = open
    }
}
