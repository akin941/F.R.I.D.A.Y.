// Reconnect backoff, the parser's depth bound on a small stack and rotation
// persistence (the feat/p9-node-app review findings).

import CryptoKit
import Foundation
import NodeCore
import XCTest

final class ReconnectBackoffTests: XCTestCase {
    final class Reader: ReadProvider {
        func read(capability: String, args: JSONValue, now: Int64) -> ReadOutcome { .failure }
    }

    func makeSession() -> NodeSession {
        NodeSession(
            nodeID: "6f1e2d3c-4b5a-4987-8a6b-5c4d3e2f1a0b", pinned: [:], catalogue: NodeCatalogue.current(),
            readers: Reader(), clock: ManualClock(1_796_000_000), nodeVersion: "friday-node test",
            osVersion: "macOS test", backoff: Backoff(random: { 0.999_999 }))
    }

    private func helloAck() throws -> Data {
        try JSON.canonical([
            "v": 1, "type": "hello.ack", "id": "4f5b027e-07eb-4be5-a70e-d157c4bb21de",
            "node_id": "6f1e2d3c-4b5a-4987-8a6b-5c4d3e2f1a0b", "heartbeat_seconds": 15,
            "rotate_after_days": 30, "server_time": 1_796_000_000, "accepted": [],
        ])
    }

    /// Server down / pin mismatch: the handshake never opens, and every
    /// failed attempt grows the delay (1, 2, 4, ... 60 s), never stuck at 1 s.
    func testFailedHandshakesGrowTheBackoff() {
        let session = makeSession()
        var ceilings: [Double] = []
        for _ in 0..<9 {
            session.connectionFailed()
            ceilings.append((session.nextReconnectDelay ?? -1).rounded(.up))
        }
        XCTAssertEqual(ceilings, [1, 2, 4, 8, 16, 32, 60, 60, 60])
        XCTAssertTrue(session.reconnectAllowed)
        XCTAssertEqual(session.closedReason, "connect_failed")
    }

    /// A server that accepts the socket and closes it before `hello.ack`
    /// (4408, 4429) keeps growing the delay; only an accepted session resets.
    func testBackoffResetsOnlyOnHelloAck() throws {
        let session = makeSession()
        _ = session.connect()
        session.disconnect()
        XCTAssertEqual(session.nextReconnectDelay?.rounded(.up), 1)
        _ = session.connect()
        session.disconnect()
        XCTAssertEqual(session.nextReconnectDelay?.rounded(.up), 2)
        _ = session.connect()
        XCTAssertTrue(session.receive(try helloAck()).isEmpty)
        XCTAssertTrue(session.sessionReady)
        session.disconnect()
        XCTAssertEqual(session.nextReconnectDelay?.rounded(.up), 1)
    }

    /// A stopping bye after failed attempts still stops: no delay is drawn.
    func testNoDelayAfterAStoppingBye() throws {
        let session = makeSession()
        _ = session.connect()
        let bye: JSONValue = ["v": 1, "type": "bye", "id": "4f5b027e-07eb-4be5-a70e-d157c4bb21df", "reason": "revoked"]
        _ = session.receive(try JSON.canonical(bye))
        XCTAssertFalse(session.reconnectAllowed)
        XCTAssertNil(session.nextReconnectDelay)
        session.connectionFailed()
        XCTAssertNil(session.nextReconnectDelay)
    }
}

final class ParserStackTests: XCTestCase {
    /// The transport parses on a dispatch thread (512 KiB stack). The depth
    /// bound must hold there in an unoptimized build too.
    func testDepthBoundOnASmallStack() {
        var results: [Bool] = []
        let done = expectation(description: "parsed")
        let thread = Thread {
            for depth in [JSON.maxDepth, JSON.maxDepth + 1, 100_000] {
                let text = String(repeating: "[", count: depth) + String(repeating: "]", count: depth)
                results.append((try? JSON.parse(text)) != nil)
            }
            let objects = String(repeating: "{\"a\":", count: JSON.maxDepth) + "1"
                + String(repeating: "}", count: JSON.maxDepth)
            results.append((try? JSON.parse(objects)) != nil)
            done.fulfill()
        }
        thread.stackSize = 512 * 1024
        thread.start()
        wait(for: [done], timeout: 10)
        XCTAssertEqual(results, [true, false, false, true])
    }
}

final class RotationTests: XCTestCase {
    struct StoreFailure: Error {}

    private func response(_ secret: String) -> JSONValue {
        [
            "node_id": "6f1e2d3c-4b5a-4987-8a6b-5c4d3e2f1a0b", "node_secret": .string(secret),
            "rotate_after_days": 30,
        ]
    }

    /// The new secret is persisted before `rotate` returns; a failed write
    /// fails the rotation (the node must never use a secret it did not keep).
    func testRotationPersistsBeforeReportingSuccess() async throws {
        let current = CredentialTests.sample()
        let newSecret = "jnd_" + String(repeating: "N", count: 43)
        var persisted: [NodeCredential] = []
        let post: Rotation.Post = { url, _, bearer, pin in
            XCTAssertEqual(url.path, "/api/v1/auth/nodes/credential/rotate")
            XCTAssertEqual(bearer, current.secret)
            XCTAssertEqual(pin, current.tlsPinSHA256)
            return (200, self.response(newSecret))
        }
        let rotated = try await Rotation.rotate(current, now: 1_797_000_000, persist: { persisted.append($0) }, post: post)
        XCTAssertEqual(persisted, [rotated])
        XCTAssertEqual(rotated.secret, newSecret)
        XCTAssertEqual(rotated.secretIssuedAt, 1_797_000_000)

        do {
            _ = try await Rotation.rotate(current, now: 1_797_000_000, persist: { _ in throw StoreFailure() }, post: post)
            XCTFail("a rotation whose secret was not kept must fail")
        } catch is StoreFailure {}
    }

    func testRotationRefusals() async {
        let current = CredentialTests.sample()
        let cases: [(Int, JSONValue?)] = [
            (401, ["code": "not_authenticated"]),
            (200, response(current.secret)),  // the same secret again
            (200, response("not-a-node-secret")),
            (200, ["node_id": "00000000-0000-4000-8000-000000000000", "node_secret": .string("jnd_" + String(repeating: "Z", count: 43))]),
        ]
        for (status, body) in cases {
            var persisted = 0
            do {
                _ = try await Rotation.rotate(current, now: 1, persist: { _ in persisted += 1 }, post: { _, _, _, _ in (status, body) })
                XCTFail("accepted \(status) \(String(describing: body))")
            } catch {}
            XCTAssertEqual(persisted, 0)
        }
    }
}
