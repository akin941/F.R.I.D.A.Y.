// Re-point while a credential rotation is in flight (the E1 review
// follow-up, `fix/p13-repoint-rotation-race`). The rotation POST went to the
// old host; the server may already have issued the new secret. Only an
// identity change (unpair, re-pair) may drop it, never a re-point.

import Foundation
import NodeCore
import XCTest

/// A rotation POST that holds until the test releases it.
final class GatedRotationPost: @unchecked Sendable {
    private let lock = NSLock()
    private var _urls: [URL] = []
    private var release: CheckedContinuation<Void, Never>?
    private var released = false
    let called = XCTestExpectation(description: "rotation POST sent")
    let secret: String
    let nodeID: String

    init(nodeID: String, secret: String) {
        self.nodeID = nodeID
        self.secret = secret
        called.assertForOverFulfill = false
    }

    var urls: [URL] { lock.withLock { _urls } }

    func open() {
        let continuation: CheckedContinuation<Void, Never>? = lock.withLock {
            released = true
            defer { release = nil }
            return release
        }
        continuation?.resume()
    }

    private func hold(_ continuation: CheckedContinuation<Void, Never>) {
        let resumeNow: Bool = lock.withLock {
            if released { return true }
            release = continuation
            return false
        }
        if resumeNow { continuation.resume() }
    }

    /// Records the call; returns its secret: `secret` for the first call,
    /// a different one for any later call (so a second rotation shows).
    private func record(_ url: URL) -> String {
        lock.withLock {
            _urls.append(url)
            return _urls.count == 1 ? secret : String(secret.dropLast()) + "\(_urls.count)"
        }
    }

    var post: Rotation.Post {
        { url, _, _, _ in
            let issued = self.record(url)
            self.called.fulfill()
            await withCheckedContinuation { self.hold($0) }
            return (200, ["node_id": .string(self.nodeID), "node_secret": .string(issued)])
        }
    }
}

final class RepointRotationRaceTests: XCTestCase {
    private let rotatedSecret = "jnd_" + String(repeating: "r", count: 43)

    /// A credential whose rotation is due, on a host where nothing listens.
    private func makeRunner(_ post: GatedRotationPost) throws -> (NodeRunner, MemoryCredentialStore, NodeCredential) {
        var credential = CredentialTests.sample()
        credential.serverURL = URL(string: "https://127.0.0.1:9")!
        let store = MemoryCredentialStore()
        try store.save(credential)
        let runner = NodeRunner(
            store: store, policyStore: MemoryLocalPolicyStore(), desktop: EmptyDesktop(),
            home: NSTemporaryDirectory(), clock: ManualClock(credential.secretIssuedAt + 31 * 86_400),
            rotationPost: post.post)
        return (runner, store, credential)
    }

    private func waitUntil(_ condition: () -> Bool) {
        let deadline = Date().addingTimeInterval(10)
        while !condition() && Date() < deadline { Thread.sleep(forTimeInterval: 0.02) }
    }

    /// Rotation POST in flight to the old host, the owner re-points, the
    /// response arrives: the stored credential has the new address **and**
    /// the rotated secret (it is not dropped as `superseded`).
    func testRepointDuringRotationKeepsTheRotatedSecretAndTheNewAddress() throws {
        let post = GatedRotationPost(nodeID: CredentialTests.sample().nodeID, secret: rotatedSecret)
        let (runner, store, original) = try makeRunner(post)
        runner.start()
        wait(for: [post.called], timeout: 10)
        XCTAssertEqual(post.urls.first?.port, 9)  // the old host
        let newURL = URL(string: "https://127.0.0.1:10")!
        _ = try runner.repoint(to: newURL, nodeID: original.nodeID)
        post.open()
        waitUntil { (try? store.load())?.secret != original.secret }
        Thread.sleep(forTimeInterval: 0.3)  // a second rotation, if any, would land here
        runner.stop()
        let stored = try XCTUnwrap(store.load())
        XCTAssertEqual(stored.secret, rotatedSecret)
        XCTAssertEqual(stored.serverURL, newURL)
        XCTAssertGreaterThan(stored.secretIssuedAt, original.secretIssuedAt)
        XCTAssertEqual(stored.tlsPinSHA256, original.tlsPinSHA256)
        XCTAssertEqual(stored.pinnedKeys, original.pinnedKeys)
        XCTAssertEqual(stored.nodeID, original.nodeID)
        XCTAssertEqual(runner.currentCredential(), stored)
        XCTAssertEqual(post.urls.count, 1)  // no second rotation with the old secret
    }

    /// Unpair while the rotation is in flight: the identity is gone, so the
    /// rotated secret is dropped and nothing is stored.
    func testUnpairDuringRotationDropsTheRotatedSecret() throws {
        let post = GatedRotationPost(nodeID: CredentialTests.sample().nodeID, secret: rotatedSecret)
        let (runner, store, _) = try makeRunner(post)
        runner.start()
        wait(for: [post.called], timeout: 10)
        try runner.unpair()
        post.open()
        Thread.sleep(forTimeInterval: 0.3)
        runner.stop()
        XCTAssertNil(try store.load())
        XCTAssertNil(runner.currentCredential())
    }

    /// Re-pair while the rotation is in flight: the new pairing wins, the old
    /// identity's rotated secret is dropped.
    func testRepairDuringRotationDropsTheRotatedSecret() throws {
        let post = GatedRotationPost(nodeID: CredentialTests.sample().nodeID, secret: rotatedSecret)
        let (runner, store, original) = try makeRunner(post)
        runner.start()
        wait(for: [post.called], timeout: 10)
        var fresh = original
        fresh.nodeID = "11111111-2222-4333-8444-555555555555"
        fresh.secret = "jnd_" + String(repeating: "p", count: 43)
        fresh.secretIssuedAt = original.secretIssuedAt + 31 * 86_400
        try runner.adopt(fresh)
        post.open()
        Thread.sleep(forTimeInterval: 0.3)
        runner.stop()
        XCTAssertEqual(try store.load(), fresh)
    }
}
