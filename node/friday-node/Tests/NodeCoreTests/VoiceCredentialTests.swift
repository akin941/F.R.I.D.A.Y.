// The voice credential (Phase 11 Mac session M1 contract test 0; brief D3;
// runbook B7): the host mints exactly as
// backend/accounts/tests/node_voice_helpers.py `mint` does, against a fake
// server that answers like accounts.views.NodeVoiceTokenMintView.

import Foundation
import NodeCore
import XCTest

/// The fake server: records each request, answers with the scripted
/// status, headers and body.
final class FakeMintServer: @unchecked Sendable {
    struct Answer {
        var status: Int
        var headers: [String: String] = ["Cache-Control": "no-store", "Content-Type": "application/json"]
        var body: JSONValue?
    }

    private let lock = NSLock()
    var answers: [Answer]
    private(set) var requests: [URLRequest] = []
    private(set) var pins: [Data] = []
    var caSHA256: String? = String(repeating: "cd", count: 32)

    init(_ answers: [Answer]) { self.answers = answers }

    static func minted(token: String = "jpat_" + String(repeating: "V0iceT0ken", count: 4),
                       expiresAt: String = "2026-11-27T10:15:00.123456Z", previous: JSONValue = .null) -> Answer {
        Answer(status: 201, body: [
            "token": .string(token), "token_id": "6f1e2d3c-4b5a-4987-8a6b-5c4d3e2f1a0b", "expires_at": .string(expiresAt),
            "previous_valid_until": previous,
        ])
    }

    private func take(_ request: URLRequest, _ pin: Data) -> (Answer, String?) {
        lock.lock()
        defer { lock.unlock() }
        requests.append(request)
        pins.append(pin)
        return (answers.isEmpty ? Answer(status: 500) : answers.removeFirst(), caSHA256)
    }

    var transport: VoiceCredential.Transport {
        { request, pin in
            let (answer, ca) = self.take(request, pin)
            let response = HTTPURLResponse(
                url: request.url!, statusCode: answer.status, httpVersion: "HTTP/1.1", headerFields: answer.headers)!
            let body = try answer.body.map { try JSON.canonical($0) } ?? Data()
            return (response, body, ca)
        }
    }
}

enum VoiceFixtures {
    static let secret = "jnd_" + String(repeating: "s3cret", count: 6)

    static func nodeCredential(server: String = "https://friday.example:8443") -> NodeCredential {
        NodeCredential(
            nodeID: "6f1e2d3c-4b5a-4987-8a6b-5c4d3e2f1a0b", secret: secret, serverURL: URL(string: server)!,
            gatewayPath: "/api/v1/nodes/ws", pinnedKeys: ["k": Data(count: 32)], tlsPinSHA256: Data(repeating: 7, count: 32),
            rotateAfterDays: 30, secretIssuedAt: 0)
    }
}

final class VoiceCredentialTests: XCTestCase {
    func testTheMintIsTheHelpersExchange() async throws {
        let server = FakeMintServer([FakeMintServer.minted()])
        let token = try await VoiceCredential.mint(VoiceFixtures.nodeCredential(), transport: server.transport)
        let request = try XCTUnwrap(server.requests.first)
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(request.url?.absoluteString, "https://friday.example:8443/api/v1/auth/nodes/voice-token")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer \(VoiceFixtures.secret)")
        XCTAssertNil(request.httpBody, "the helper posts no body")
        XCTAssertEqual(request.cachePolicy, .reloadIgnoringLocalAndRemoteCacheData)
        XCTAssertEqual(server.pins, [Data(repeating: 7, count: 32)], "over the pairing pin")
        XCTAssertEqual(token.expiresAt, "2026-11-27T10:15:00.123456Z")
        XCTAssertEqual(token.tokenID, "6f1e2d3c-4b5a-4987-8a6b-5c4d3e2f1a0b")
        XCTAssertEqual(token.expiresAtSeconds, 1_795_774_500.123456, accuracy: 0.000_01)
        // The credential frame the child gets is valid against its schema.
        var frame = token.frame
        frame["type"] = "credential"
        XCTAssertNoThrow(try VoiceFraming.encode(frame, direction: .hostToChild))
        XCTAssertEqual(frame["server_url"], "https://friday.example:8443")
        XCTAssertEqual(frame["ca_sha256"], .string(String(repeating: "cd", count: 32)))
        XCTAssertEqual(Set(frame.keys), ["type", "token", "expires_at", "server_url", "ca_sha256"])
    }

    func testA401IsRefused() async {
        let server = FakeMintServer([FakeMintServer.Answer(status: 401, body: ["code": "not_authenticated"])])
        do {
            _ = try await VoiceCredential.mint(VoiceFixtures.nodeCredential(), transport: server.transport)
            XCTFail("minted")
        } catch {
            XCTAssertEqual(error as? VoiceCredentialError, .refused)
        }
    }

    func testAnythingButA201WithNoStoreAndTheFourFieldsIsRefused() async {
        let bad: [FakeMintServer.Answer] = [
            FakeMintServer.Answer(status: 200, body: FakeMintServer.minted().body),
            FakeMintServer.Answer(status: 429, body: [:]),
            { var a = FakeMintServer.minted(); a.headers = ["Content-Type": "application/json"]; return a }(),
            { var a = FakeMintServer.minted(); a.body = ["token": "jpat_x"]; return a }(),
            FakeMintServer.minted(token: "not-a-token"),
            FakeMintServer.minted(expiresAt: "2026-11-27 10:15:00"),
            FakeMintServer.minted(previous: 3),
        ]
        for answer in bad {
            let server = FakeMintServer([answer])
            do {
                _ = try await VoiceCredential.mint(VoiceFixtures.nodeCredential(), transport: server.transport)
                XCTFail("minted from \(answer.status)")
            } catch {
                XCTAssertNotEqual(error as? VoiceCredentialError, .refused)
            }
        }
    }

    func testThePreviousGraceAndThePlusZeroForm() async throws {
        let server = FakeMintServer([
            FakeMintServer.minted(expiresAt: "2026-11-27T10:15:00+00:00", previous: "2026-11-27T10:00:30Z")
        ])
        let token = try await VoiceCredential.mint(VoiceFixtures.nodeCredential(), transport: server.transport)
        XCTAssertEqual(token.previousValidUntil, "2026-11-27T10:00:30Z")
        XCTAssertEqual(token.expiresAtSeconds, 1_795_774_500, accuracy: 0.001)
    }

    func testRefreshIsAtLeastSixtySecondsBeforeExpiry() {
        let expires = 10_000.0
        let at = VoiceCredential.refreshAt(expiresAt: expires, now: 1_000)
        XCTAssertLessThanOrEqual(at, expires - VoiceCredential.minimumLeadSeconds)
        XCTAssertGreaterThan(at, 1_000)
        XCTAssertEqual(VoiceCredential.refreshAt(expiresAt: 1_030, now: 1_000), 1_000, "already due: now")
    }

    func testOnlyAnHttpsOriginIsAServerURL() {
        XCTAssertEqual(VoiceCredential.origin(URL(string: "https://h.example/")!), "https://h.example")
        XCTAssertEqual(VoiceCredential.origin(URL(string: "https://127.0.0.1:8443")!), "https://127.0.0.1:8443")
        XCTAssertNil(VoiceCredential.origin(URL(string: "http://h.example")!))
    }

    /// The token appears in no file the host writes, and the minter has no
    /// store at all: the type holds it in memory only.
    func testTheTokenIsNeverPersisted() throws {
        let source = String(decoding: try Repo.data("node/friday-node/Sources/NodeCore/Voice/VoiceCredential.swift"), as: UTF8.self)
        for forbidden in ["SecItemAdd", "write(to", "UserDefaults", "FileManager", "Logger(", "print("] {
            XCTAssertFalse(source.contains(forbidden), forbidden)
        }
    }
}
