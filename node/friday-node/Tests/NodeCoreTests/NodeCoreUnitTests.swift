// Unit tests for the M1 pieces the conformance cases do not reach: the TLS
// pin, the credential's storage form, the pairing response checks, the
// backoff and the session's owner/heartbeat behaviour.

import CryptoKit
import Foundation
import NodeCore
import Security
import XCTest

final class TLSPinTests: XCTestCase {
    private func openssl(_ arguments: [String], input: Data? = nil) throws -> Data {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/openssl")
        process.arguments = arguments
        let output = Pipe()
        process.standardOutput = output
        process.standardError = Pipe()
        let stdin = Pipe()
        process.standardInput = stdin
        try process.run()
        if let input { stdin.fileHandleForWriting.write(input) }
        try stdin.fileHandleForWriting.close()
        let data = output.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        XCTAssertEqual(process.terminationStatus, 0, arguments.joined(separator: " "))
        return data
    }

    /// The pin equals the runbook's openssl pipeline for every supported key
    /// type (so the owner can compare it with NODE_TLS_PIN_SHA256).
    func testPinMatchesOpenSSLForEveryKeyType() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        // EC keys as real servers use them: named curves (LibreSSL's
        // `-newkey ec` writes explicit parameters, which no TLS stack sends).
        let keySpecs = ["prime256v1", "secp384r1", "rsa:2048", "rsa:3072", "rsa:4096"]
        for (index, spec) in keySpecs.enumerated() {
            let key = directory.appendingPathComponent("k\(index).pem").path
            let cert = directory.appendingPathComponent("c\(index).der").path
            if spec.hasPrefix("rsa:") {
                _ = try openssl(["genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:\(spec.dropFirst(4))",
                                 "-out", key])
            } else {
                _ = try openssl(["ecparam", "-name", spec, "-genkey", "-param_enc", "named_curve", "-noout", "-out", key])
            }
            _ = try openssl(["req", "-new", "-x509", "-nodes", "-subj", "/CN=pin-test", "-days", "1", "-key", key,
                             "-outform", "DER", "-out", cert])
            let keyType = [spec]
            let der = try Data(contentsOf: URL(fileURLWithPath: cert))
            let spki = try openssl(["x509", "-inform", "DER", "-in", cert, "-pubkey", "-noout"])
            let spkiDER = try openssl(["pkey", "-pubin", "-outform", "DER"], input: spki)
            let expected = Data(SHA256.hash(data: spkiDER))
            let certificate = try XCTUnwrap(SecCertificateCreateWithData(nil, der as CFData))
            XCTAssertEqual(TLSPin.sha256(of: certificate), expected, keyType.joined(separator: " "))
        }
    }

    func testPinParsing() {
        let raw = Data((0..<32).map { UInt8($0) })
        XCTAssertEqual(TLSPin.parse(raw.base64EncodedString()), raw)
        XCTAssertEqual(TLSPin.parse("sha256/" + raw.base64EncodedString()), raw)
        XCTAssertEqual(TLSPin.parse(raw.map { String(format: "%02X", $0) }.joined()), raw)
        XCTAssertNil(TLSPin.parse("abc"))
    }
}

final class CredentialTests: XCTestCase {
    static let key = Curve25519.Signing.PrivateKey().publicKey.rawRepresentation

    static func sample() -> NodeCredential {
        NodeCredential(
            nodeID: "6f1e2d3c-4b5a-4987-8a6b-5c4d3e2f1a0b", secret: "jnd_" + String(repeating: "x", count: 43),
            serverURL: URL(string: "https://friday.example.net")!, gatewayPath: "/ws/node/v1",
            pinnedKeys: [Signing.keyID(for: key): key], tlsPinSHA256: Data(repeating: 7, count: 32),
            rotateAfterDays: 30, secretIssuedAt: 1_796_000_000)
    }

    func testStorageRoundTrip() throws {
        let store = MemoryCredentialStore()
        XCTAssertNil(try store.load())
        try store.save(Self.sample())
        XCTAssertEqual(try store.load(), Self.sample())
        try store.delete()
        XCTAssertNil(try store.load())
    }

    func testCorruptStorageIsRefused() throws {
        var doc = try JSON.parse(try Self.sample().encoded()).objectValue!
        doc["pinned_keys"] = ["0000000000000000": .string(Self.key.base64EncodedString())]  // id mismatch
        XCTAssertThrowsError(try NodeCredential.decoded(try JSON.canonical(.object(doc))))
    }

    func testGatewayURLAndRotationDue() {
        let credential = Self.sample()
        XCTAssertEqual(credential.gatewayURL?.absoluteString, "wss://friday.example.net/ws/node/v1")
        XCTAssertFalse(credential.rotationDue(now: 1_796_000_000 + 29 * 86_400))
        XCTAssertTrue(credential.rotationDue(now: 1_796_000_000 + 30 * 86_400))
    }

    func testPairingResponseIsCheckedFieldByField() throws {
        let key = Self.key
        let good: [String: JSONValue] = [
            "node_id": "6f1e2d3c-4b5a-4987-8a6b-5c4d3e2f1a0b",
            "node_secret": .string("jnd_" + String(repeating: "A", count: 43)),
            "server_signing_keys": [[
                "key_id": .string(Signing.keyID(for: key)), "alg": "Ed25519",
                "public_key_b64": .string(key.base64EncodedString()),
            ]],
            "protocol_version": 1, "gateway_path": "/ws/node/v1", "rotate_after_days": 30,
        ]
        let server = URL(string: "https://friday.example.net")!
        let pin = Data(repeating: 1, count: 32)
        let credential = try Pairing.credential(from: .object(good), serverURL: server, tlsPin: pin, now: 5)
        XCTAssertEqual(credential.pinnedKeys, [Signing.keyID(for: key): key])
        XCTAssertEqual(credential.tlsPinSHA256, pin)

        let broken: [(String, JSONValue)] = [
            ("node_id", "not-a-uuid"), ("node_secret", "secret"), ("protocol_version", 2),
            ("gateway_path", "/ws/other"), ("rotate_after_days", 0), ("server_signing_keys", []),
            ("server_signing_keys", [["key_id": "0000000000000000", "alg": "Ed25519",
                                      "public_key_b64": .string(key.base64EncodedString())]]),
        ]
        for (field, value) in broken {
            var doc = good
            doc[field] = value
            XCTAssertThrowsError(try Pairing.credential(from: .object(doc), serverURL: server, tlsPin: pin, now: 5),
                                 field)
        }
    }
}

final class BackoffTests: XCTestCase {
    func testFullJitterCeilings() {
        var backoff = Backoff(random: { 0.999_999 })
        let delays = (0..<10).map { _ in backoff.nextDelay() }
        let ceilings: [Double] = [1, 2, 4, 8, 16, 32, 60, 60, 60, 60]
        for (delay, ceiling) in zip(delays, ceilings) {
            XCTAssertLessThanOrEqual(delay, ceiling)
            XCTAssertGreaterThan(delay, ceiling * 0.99)
        }
        backoff.reset()
        XCTAssertLessThanOrEqual(backoff.nextDelay(), 1)
    }
}

final class NodeSessionTests: XCTestCase {
    final class Reader: ReadProvider {
        func read(capability: String, args: JSONValue, now: Int64) -> ReadOutcome { .failure }
    }

    func makeSession(_ clock: ManualClock) -> NodeSession {
        NodeSession(
            nodeID: "6f1e2d3c-4b5a-4987-8a6b-5c4d3e2f1a0b", pinned: [:], catalogue: NodeCatalogue.defaultPolicy(),
            readers: Reader(), clock: clock, nodeVersion: NodeInfo.nodeVersion, osVersion: NodeInfo.osVersion)
    }

    func testHelloIsValidAndCarriesTheReadCatalogue() throws {
        let session = makeSession(ManualClock(1_796_000_000))
        let frames = session.connect()
        XCTAssertEqual(frames.count, 1)
        XCTAssertEqual(frames[0]["type"], "hello")
        XCTAssertEqual(frames[0]["catalogue"]?.arrayValue?.count, 10)
        XCTAssertNoThrow(try NodeProtocol.validate(frames[0], direction: .nodeToServer))
        XCTAssertEqual(session.invalidOutgoingFrames, 0)
    }

    func testHeartbeatEveryFifteenSecondsAndSilenceClose() {
        let clock = ManualClock(1_796_000_000)
        let session = makeSession(clock)
        _ = session.connect()
        clock.advance(14)
        XCTAssertTrue(session.tick().isEmpty)
        clock.advance(1)
        let frames = session.tick()
        XCTAssertEqual(frames.map { $0["type"] }, ["heartbeat"])
        clock.advance(31)  // 46 s without a server frame
        _ = session.tick()
        XCTAssertFalse(session.connected)
        XCTAssertEqual(session.closedReason, "server_silent")
        XCTAssertTrue(session.reconnectAllowed)
        XCTAssertNotNil(session.nextReconnectDelay)
    }

    func testHelloAckIsRecordedAndByeKillSwitchNeedsTheOwner() throws {
        let clock = ManualClock(1_796_000_000)
        let session = makeSession(clock)
        _ = session.connect()
        let ack: JSONValue = [
            "v": 1, "type": "hello.ack", "id": "4f5b027e-07eb-4be5-a70e-d157c4bb21de",
            "node_id": "6f1e2d3c-4b5a-4987-8a6b-5c4d3e2f1a0b", "heartbeat_seconds": 15,
            "rotate_after_days": 30, "server_time": 1_796_000_000, "accepted": ["apps.frontmost"],
        ]
        XCTAssertTrue(session.receive(try JSON.canonical(ack)).isEmpty)
        XCTAssertTrue(session.sessionReady)
        XCTAssertEqual(session.helloAck?.rotateAfterDays, 30)
        XCTAssertEqual(session.helloAck?.accepted, ["apps.frontmost"])

        let bye: JSONValue = ["v": 1, "type": "bye", "id": "4f5b027e-07eb-4be5-a70e-d157c4bb21df", "reason": "kill_switch"]
        XCTAssertTrue(session.receive(try JSON.canonical(bye)).isEmpty)
        XCTAssertFalse(session.connected)
        XCTAssertFalse(session.reconnectAllowed)
        XCTAssertEqual(session.closedReason, "bye:kill_switch")
        session.ownerResume()
        XCTAssertTrue(session.reconnectAllowed)
        XCTAssertEqual(session.connect().first?["type"], "hello")
    }

    func testNothingIsSentAfterDisconnect() {
        let clock = ManualClock(1_796_000_000)
        let session = makeSession(clock)
        _ = session.connect()
        session.disconnect()
        clock.advance(60)
        XCTAssertTrue(session.tick().isEmpty)
        XCTAssertTrue(session.receive(Data("{}".utf8)).isEmpty)
    }

    func testTheDefaultPolicyGrantsNoOwnerData() {
        var availability: [String: String] = [:]
        for entry in NodeCatalogue.defaultPolicy().entries {
            availability[entry["name"]!.stringValue!] = entry["availability"]!.stringValue!
        }
        XCTAssertEqual(availability, [
            "claude.sessions.list": "not_granted", "claude.sessions.read": "not_granted",
            "codex.threads.list": "not_granted", "codex.threads.read": "not_granted",
            "git.status": "not_granted", "usage.ccusage": "unsupported",
            "apps.frontmost": "granted", "windows.list": "granted",
            // Behind the directory grants, off by default (runbook B4).
            "claude.transcripts.owner_turns": "not_granted", "codex.transcripts.owner_turns": "not_granted",
        ])
    }

    func testTheOwnerTurnReadsFollowTheDirectoryGrants() {
        let granted = NodeReaders(
            home: NSTemporaryDirectory(), repos: nil, grants: ReaderGrants(claude: true, codex: true),
            windowTitles: false, desktop: SystemDesktop(), usage: .unsupported)
        XCTAssertEqual(granted.availability()["claude.transcripts.owner_turns"], "granted")
        XCTAssertEqual(granted.availability()["codex.transcripts.owner_turns"], "granted")
        let off = NodeReaders(
            home: NSTemporaryDirectory(), repos: nil, grants: ReaderGrants(claude: false, codex: false),
            windowTitles: false, desktop: SystemDesktop(), usage: .unsupported)
        XCTAssertEqual(off.availability()["claude.transcripts.owner_turns"], "not_granted")
        XCTAssertEqual(off.availability()["codex.transcripts.owner_turns"], "not_granted")
        let args: JSONValue = .object([
            "since": "2026-11-29T00:00:00.000Z", "cwd_prefixes": .array(["~/work/acme"]),
            "max_sessions": 50, "max_turns": 200,
        ])
        XCTAssertEqual(
            off.read(capability: "claude.transcripts.owner_turns", args: args, now: 1_796_000_005), .failure,
            "an owner-turn read without its directory grant must fail")
    }
}
