// The M2 local policy and grants (Phase 9 brief M2 "Must satisfy"): grants
// off by default; a change only after owner authentication; a revoked grant
// is `not_granted` at once (check 16 refuses, no reader runs) and the node
// sends `grant.availability_changed` with items exactly
// `{name, version, availability}`.

import CryptoKit
import Foundation
import NodeCore
import XCTest

final class PolicyTests: XCTestCase {
    struct FakeAuth: OwnerAuthentication {
        let answer: Bool
        func authenticate(reason: String) async -> Bool { answer }
    }

    final class CountingReader: ReadProvider {
        var calls = 0
        func read(capability: String, args: JSONValue, now: Int64) -> ReadOutcome {
            calls += 1
            return .success(["sessions": []])
        }
    }

    func testDefaultsAreOffAndTheDocumentRoundTrips() throws {
        let policy = LocalPolicy()
        XCTAssertEqual(policy.grants, ReaderGrants(claude: false, codex: false))
        XCTAssertFalse(policy.windowTitles)
        XCTAssertTrue(policy.repos.isEmpty)
        let full = LocalPolicy(
            grants: ReaderGrants(claude: true, codex: false), windowTitles: true,
            repos: [.init(name: "acme", root: "~/work/acme")])
        XCTAssertEqual(try LocalPolicy.decoded(try JSON.canonical(full.document)), full)
        XCTAssertNotEqual(full.fingerprint, policy.fingerprint)
    }

    func testAChangeNeedsTheOwner() async throws {
        let store = MemoryLocalPolicyStore()
        var changed: [LocalPolicy] = []
        let refused = PolicyEditor(store: store, authentication: FakeAuth(answer: false))
        refused.onChange = { changed.append($0) }
        do {
            try await refused.change(reason: "test") { $0.grants.claude = true }
            XCTFail("changed without authentication")
        } catch PolicyChangeError.notAuthenticated {}
        XCTAssertEqual(try store.load(), LocalPolicy())
        XCTAssertTrue(changed.isEmpty)

        let editor = PolicyEditor(store: store, authentication: FakeAuth(answer: true))
        editor.onChange = { changed.append($0) }
        try await editor.change(reason: "test") { $0.grants.claude = true }
        XCTAssertTrue(try store.load().grants.claude)
        XCTAssertEqual(changed.count, 1)
    }

    private func envelope(_ key: Curve25519.Signing.PrivateKey, nonce: String) throws -> Data {
        let keyID = Signing.keyID(for: key.publicKey.rawRepresentation)
        let args: JSONValue = ["since_hours": 24, "limit": 10]
        var body: [String: JSONValue] = [
            "v": 1, "type": "invoke", "id": "4f5b027e-07eb-4be5-a70e-d157c4bb21de",
            "invocation_id": "d9426746-a50b-4e7f-8f39-150d02c0c8d7",
            "node_id": "6f1e2d3c-4b5a-4987-8a6b-5c4d3e2f1a0b",
            "workspace_id": "2a3b4c5d-6e7f-4a8b-9c0d-1e2f3a4b5c6d",
            "capability": "claude.sessions.list", "capability_version": 1, "class": "read",
            "args": args, "args_sha256": .string(try JSON.sha256Hex(args)), "nonce": .string(nonce),
            "issued_at": 1_796_000_000, "expires_at": 1_796_000_030, "trace_id": "policy-test", "grant": nil,
            "key_id": .string(keyID),
        ]
        body["sig"] = .string(Base64URL.encode(try key.signature(for: JSON.canonical(.object(body)))))
        return try JSON.canonical(.object(body))
    }

    func testARevokedGrantRefusesAtOnceAndIsAnnounced() throws {
        let key = Curve25519.Signing.PrivateKey()
        let granted = Catalogue.standard(availability: ["claude.sessions.list": "granted"])
        let reader = CountingReader()
        let session = NodeSession(
            nodeID: "6f1e2d3c-4b5a-4987-8a6b-5c4d3e2f1a0b",
            pinned: [Signing.keyID(for: key.publicKey.rawRepresentation): key.publicKey.rawRepresentation],
            catalogue: granted, readers: reader, clock: ManualClock(1_796_000_005),
            nodeVersion: "friday-node test", osVersion: "macOS test")
        _ = session.connect()

        // Before hello.ack an availability change is not sent (the next
        // hello carries the catalogue).
        let revoked = Catalogue.standard(availability: ["claude.sessions.list": "not_granted"])
        XCTAssertTrue(session.update(catalogue: revoked, readers: reader).isEmpty)
        XCTAssertTrue(session.update(catalogue: granted, readers: reader).isEmpty)

        let ack: JSONValue = [
            "v": 1, "type": "hello.ack", "id": "4f5b027e-07eb-4be5-a70e-d157c4bb21aa",
            "node_id": "6f1e2d3c-4b5a-4987-8a6b-5c4d3e2f1a0b", "heartbeat_seconds": 15, "rotate_after_days": 30,
            "server_time": 1_796_000_000, "accepted": [],
        ]
        _ = session.receive(try JSON.canonical(ack))
        let frames = session.update(catalogue: revoked, readers: reader)
        XCTAssertEqual(frames.count, 1)
        XCTAssertEqual(frames[0]["kind"], "grant.availability_changed")
        XCTAssertEqual(frames[0]["spooled"], false)
        XCTAssertEqual(frames[0]["batch"], [["name": "claude.sessions.list", "version": 1, "availability": "not_granted"]])
        XCTAssertNoThrow(try NodeProtocol.validate(frames[0], direction: .nodeToServer))

        let refused = session.receive(try envelope(key, nonce: "fsCBbs_g-_AY6EWtzsd4OwliyKKYwIdwAe68dKMApFw"))
        XCTAssertEqual(refused.first?["refusal_code"], "not_granted")
        XCTAssertEqual(reader.calls, 0, "no reader may run on a refusal path")

        // Nothing changed: nothing announced.
        XCTAssertTrue(session.update(catalogue: revoked, readers: reader).isEmpty)
    }
}
