// The shared protocol vectors (backend/nodes/protocol/vectors/), loaded from
// the repo path, reproduced byte for byte (Phase 9 brief M1 "Must satisfy").

import CryptoKit
import Foundation
import NodeCore
import XCTest

enum Repo {
    /// The repository root, from this file's location.
    static let root: URL = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent().deletingLastPathComponent()

    static func url(_ path: String) -> URL { root.appendingPathComponent(path) }

    static func data(_ path: String) throws -> Data { try Data(contentsOf: url(path)) }

    static func json(_ path: String) throws -> JSONValue { try JSON.parse(try data(path)) }
}

final class CanonicalVectorTests: XCTestCase {
    func testValidVectorsCanonicalizeByteForByte() throws {
        let doc = try Repo.json("backend/nodes/protocol/vectors/canonical.json")
        let valid = try XCTUnwrap(doc["valid"]?.arrayValue)
        XCTAssertFalse(valid.isEmpty)
        for vector in valid {
            let name = vector["name"]?.stringValue ?? "?"
            let input = try XCTUnwrap(vector["input"], name)
            let expected = try XCTUnwrap(vector["canonical"]?.stringValue, name)
            XCTAssertEqual(try JSON.canonicalString(input), expected, name)
            XCTAssertEqual(try JSON.sha256Hex(input), vector["sha256"]?.stringValue, name)
            // The canonical text parses back to the same value.
            XCTAssertEqual(try JSON.parse(expected), input, name)
        }
    }

    func testInvalidVectorsAreRefused() throws {
        let doc = try Repo.json("backend/nodes/protocol/vectors/canonical.json")
        let invalid = try XCTUnwrap(doc["invalid"]?.arrayValue)
        XCTAssertFalse(invalid.isEmpty)
        for vector in invalid {
            let name = vector["name"]?.stringValue ?? "?"
            let data: Data
            if let text = vector["json"]?.stringValue {
                data = Data(text.utf8)
            } else {
                let hex = try XCTUnwrap(vector["hex"]?.stringValue, name)
                data = Data(stride(from: 0, to: hex.count, by: 2).map {
                    let start = hex.index(hex.startIndex, offsetBy: $0)
                    return UInt8(hex[start..<hex.index(start, offsetBy: 2)], radix: 16)!
                })
            }
            let refused: Bool
            if let value = try? JSON.parse(data) {
                refused = (try? JSON.canonical(value)) == nil
            } else {
                refused = true
            }
            XCTAssertTrue(refused, "accepted invalid vector \(name)")
        }
    }

    func testStrictParserRefusals() {
        for text in [
            "{\"a\":1,\"a\":2}", "[1,]", "{\"a\":NaN}", "{\"a\":Infinity}", "01", "1.0", "1e3",
            "\"\u{01}\"", "\"\\ud800\"", "9007199254740992", "{} {}", "\u{FEFF}{}",
        ] {
            XCTAssertThrowsError(try JSON.parse(text), text)
        }
        XCTAssertEqual(try JSON.parse(" {\"a\" : [ -0 , true , null ] } "), ["a": [0, true, nil]])
        XCTAssertEqual(try JSON.parse("\"\\ud83d\\ude00\""), .string("\u{1F600}"))
    }

    func testDepthBound() {
        let deep = String(repeating: "[", count: 5000) + String(repeating: "]", count: 5000)
        XCTAssertThrowsError(try JSON.parse(deep))
    }
}

final class SigningVectorTests: XCTestCase {
    func testSigningVectors() throws {
        let doc = try Repo.json("backend/nodes/protocol/vectors/signing.json")
        let publicKey = try XCTUnwrap(Signing.publicKey(base64: try XCTUnwrap(doc["public_key_b64"]?.stringValue)))
        let keyID = try XCTUnwrap(doc["key_id"]?.stringValue)
        XCTAssertEqual(Signing.keyID(for: publicKey), keyID)

        let envelope = try XCTUnwrap(doc["envelope"])
        var body = try XCTUnwrap(envelope.objectValue)
        body.removeValue(forKey: "sig")
        XCTAssertEqual(try JSON.canonicalString(.object(body)), doc["signed_body_canonical"]?.stringValue)
        XCTAssertEqual(try JSON.canonicalString(try XCTUnwrap(envelope["args"])), doc["args_canonical"]?.stringValue)
        XCTAssertEqual(try JSON.sha256Hex(try XCTUnwrap(envelope["args"])), envelope["args_sha256"]?.stringValue)

        XCTAssertNoThrow(try Signing.verify(envelope, pinned: [keyID: publicKey]))
        XCTAssertThrowsError(try Signing.verify(envelope, pinned: [:])) {
            XCTAssertEqual($0 as? ProtocolError, ProtocolError("unknown_key"))
        }
        var tampered = try XCTUnwrap(envelope.objectValue)
        tampered["trace_id"] = "trace-0002"
        XCTAssertThrowsError(try Signing.verify(.object(tampered), pinned: [keyID: publicKey])) {
            XCTAssertEqual($0 as? ProtocolError, ProtocolError("bad_signature"))
        }

        // Ed25519 is deterministic: signing the body with the vector's
        // private key reproduces `sig` exactly.
        let privateRaw = try XCTUnwrap(Data(base64Encoded: try XCTUnwrap(doc["private_key_b64"]?.stringValue)))
        let key = try Curve25519.Signing.PrivateKey(rawRepresentation: privateRaw)
        XCTAssertEqual(key.publicKey.rawRepresentation, publicKey)
        let signature = try key.signature(for: JSON.canonical(.object(body)))
        XCTAssertNoThrow(try Signing.verify(.object(body.merging(["sig": .string(Base64URL.encode(signature))]) { $1 }),
                                            pinned: [keyID: publicKey]))
    }

    func testStrictBase64URL() {
        XCTAssertEqual(Base64URL.decode("AAECAw"), Data([0, 1, 2, 3]))
        XCTAssertNil(Base64URL.decode("AAECAw=="))  // padding
        XCTAssertNil(Base64URL.decode("AAECAx"))  // stray trailing bits
        XCTAssertNil(Base64URL.decode("AA+/"))  // standard alphabet
        XCTAssertNil(Base64URL.decode("A"))
    }
}

final class VendoredProtocolTests: XCTestCase {
    /// The vendored copies equal the repo files byte for byte, and every
    /// repo file of the vendored set is vendored.
    func testVendoredFilesMatchTheRepo() throws {
        let fileManager = FileManager.default
        var repoPaths: [String] = []
        for directory in ["protocol/schemas/v1", "capabilities/v1"] {
            let url = Repo.url("backend/nodes/\(directory)")
            for name in try fileManager.contentsOfDirectory(atPath: url.path) where name.hasSuffix(".json") {
                repoPaths.append("\(directory)/\(name)")
            }
        }
        XCTAssertEqual(NodeProtocol.vendoredPaths, repoPaths.sorted(),
                       "rerun node/friday-node/scripts/vendor_protocol.py")
        for path in repoPaths {
            XCTAssertEqual(NodeProtocol.vendoredFile(path), try Repo.data("backend/nodes/\(path)"),
                           "\(path) drifted: rerun node/friday-node/scripts/vendor_protocol.py")
        }
    }

    func testEveryMessageTypeHasASchemaAndEveryContractLoads() {
        for type in NodeProtocol.nodeToServer.union(NodeProtocol.serverToNode) {
            XCTAssertNotNil(NodeProtocol.schema(for: type), type)
        }
        XCTAssertEqual(CapabilityContract.all.count, 10)
        XCTAssertTrue(CapabilityContract.all.allSatisfy { $0.capabilityClass == "read" && $0.version == 1 })
    }
}

final class PolicyGuardTests: XCTestCase {
    /// ADR-013 decision 5 made mechanical, node side: the server -> node set
    /// is exactly five types and nothing the node accepts looks like policy.
    func testServerToNodeSetIsFrozen() {
        XCTAssertEqual(NodeProtocol.serverToNode, ["hello.ack", "heartbeat.ack", "invoke", "event.ack", "bye"])
        XCTAssertEqual(NodeProtocol.nodeToServer, ["hello", "heartbeat", "result", "event"])
    }

    func testNoPolicyShapedNames() throws {
        let policy = try NSRegularExpression(
            pattern: "(?i)policy|allowlist|grant(?!_id)|catalogue[._]set|config|exec_policy|settings")
        func names(_ schema: JSONValue) -> [String] {
            var out: [String] = []
            for (key, value) in schema["properties"]?.objectValue ?? [:] {
                out.append(key)
                out += names(value)
            }
            return out
        }
        for type in NodeProtocol.serverToNode {
            var all = [type]
            all += names(try XCTUnwrap(NodeProtocol.schema(for: type)))
            for name in all where !(type == "invoke" && name == "grant") {
                let range = NSRange(name.startIndex..., in: name)
                XCTAssertNil(policy.firstMatch(in: name, range: range), "\(type).\(name)")
            }
        }
    }
}
