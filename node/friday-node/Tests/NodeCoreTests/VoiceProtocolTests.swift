// `friday-voice/v1` on the Swift side (Phase 11 brief D6; Mac session M1
// "Must satisfy"; runbook B7): the vendored schemas and the uv.lock hash
// equal the repo's, the frozen sets equal the schema directories, no name is
// policy-shaped, and every vector of
// node/friday-voice/friday_voice/protocol/vectors/frames.json decodes,
// re-encodes byte for byte (valid) or is refused with its code (invalid).

import Foundation
import NodeCore
import XCTest

enum VoiceRepo {
    static let protocolDir = "node/friday-voice/friday_voice/protocol"

    static func json(_ path: String) throws -> JSONValue { try Repo.json(path) }

    static func b64(_ value: JSONValue?) throws -> Data {
        try XCTUnwrap(value?.stringValue.flatMap { Data(base64Encoded: $0) })
    }
}

final class VoiceProtocolTests: XCTestCase {
    func testTheVendoredSchemasAreTheRepoFilesByteForByte() throws {
        let root = Repo.url(VoiceRepo.protocolDir)
        var repoPaths: [String] = []
        for direction in ["host_to_child", "child_to_host"] {
            let dir = root.appendingPathComponent("schemas/v1/\(direction)")
            for name in try FileManager.default.contentsOfDirectory(atPath: dir.path) where name.hasSuffix(".schema.json") {
                repoPaths.append("schemas/v1/\(direction)/\(name)")
            }
        }
        XCTAssertEqual(VoiceProtocol.vendoredPaths, repoPaths.sorted(), "rerun scripts/vendor_protocol.py")
        for path in repoPaths {
            XCTAssertEqual(
                VoiceProtocol.vendoredFile(path), try Repo.data("\(VoiceRepo.protocolDir)/\(path)"),
                "\(path) drifted: rerun node/friday-node/scripts/vendor_protocol.py")
        }
    }

    func testTheCompiledInLockHashIsTheCheckoutsUvLock() throws {
        let lock = try Repo.data("node/friday-voice/uv.lock")
        XCTAssertEqual(VoiceProtocol.lockSHA256, JSON.hexDigest(lock))
        XCTAssertEqual(VoiceProtocol.lockSHA256.count, 64)
    }

    func testTheFrozenSetsAreTheSchemaDirectories() {
        let host = Set(VoiceProtocol.vendoredPaths.filter { $0.contains("/host_to_child/") }.map(Self.typeName))
        let child = Set(VoiceProtocol.vendoredPaths.filter { $0.contains("/child_to_host/") }.map(Self.typeName))
        XCTAssertEqual(host, VoiceProtocol.hostToChildTypes)
        XCTAssertEqual(child, VoiceProtocol.childToHostTypes)
        XCTAssertEqual(
            VoiceProtocol.hostToChildTypes,
            ["hello", "credential", "audio", "playback", "mode", "mute", "ptt", "link", "shutdown"])
        XCTAssertEqual(
            VoiceProtocol.childToHostTypes, ["ready", "state", "play", "stop_playback", "earcon", "release_mic", "log"])
        XCTAssertTrue(VoiceProtocol.hostToChildTypes.isDisjoint(with: VoiceProtocol.childToHostTypes))
    }

    static func typeName(_ path: String) -> String {
        String(path.split(separator: "/").last!.dropLast(".schema.json".count))
    }

    /// The node protocol's POLICY_SHAPED regex matches no type or property
    /// name (D6: no reserved exception), and the literal is the backend's.
    func testNoNameIsPolicyShaped() throws {
        let backend = String(decoding: try Repo.data("backend/nodes/protocol/messages.py"), as: UTF8.self)
        XCTAssertTrue(backend.contains(VoiceProtocol.policyShaped), "the POLICY_SHAPED literal drifted")
        let regex = try NSRegularExpression(pattern: VoiceProtocol.policyShaped)
        func shaped(_ name: String) -> Bool {
            regex.firstMatch(in: name, range: NSRange(name.startIndex..., in: name)) != nil
        }
        for type in VoiceProtocol.hostToChildTypes.union(VoiceProtocol.childToHostTypes) {
            XCTAssertFalse(shaped(type), type)
            let schema = try XCTUnwrap(VoiceProtocol.schema(for: type), type)
            for property in (schema["properties"]?.objectValue ?? [:]).keys {
                XCTAssertFalse(shaped(property), "\(type).\(property)")
            }
            XCTAssertEqual(schema["additionalProperties"], .bool(false), type)
        }
        XCTAssertTrue(shaped("policy.set") && shaped("config"))
    }

    func testEveryValidVectorDecodesAndReencodesByteForByte() throws {
        let doc = try VoiceRepo.json("\(VoiceRepo.protocolDir)/vectors/frames.json")
        XCTAssertEqual(doc["max_frame_bytes"]?.intValue, Int64(VoiceProtocol.maxFrameBytes))
        XCTAssertEqual(doc["max_pcm_bytes"]?.intValue, Int64(VoiceProtocol.maxPCMBytes))
        let valid = try XCTUnwrap(doc["valid"]?.arrayValue)
        XCTAssertFalse(valid.isEmpty)
        for vector in valid {
            let name = vector["name"]?.stringValue ?? "?"
            let direction = try XCTUnwrap(vector["direction"]?.stringValue.flatMap(VoiceProtocol.Direction.init), name)
            let frame = try VoiceRepo.b64(vector["frame_b64"])
            let payload = try VoiceRepo.b64(vector["payload_b64"])
            let decoded = try VoiceFraming.decode(frame, direction: direction)
            var expected = try XCTUnwrap(vector["header"]?.objectValue, name)
            if let type = expected["type"]?.stringValue, VoiceProtocol.audioTypes[type] != nil {
                expected["pcm_bytes"] = .int(Int64(payload.count))
            }
            XCTAssertEqual(decoded.header, expected, name)
            XCTAssertEqual(decoded.payload, payload, name)
            let reencoded = try VoiceFraming.encode(
                try XCTUnwrap(vector["header"]?.objectValue), payload: payload, direction: direction)
            XCTAssertEqual(reencoded, frame, name)
            // The incremental decoder, fed one byte at a time.
            let decoder = VoiceFrameDecoder(direction: direction)
            var got: [[String: JSONValue]] = []
            for byte in frame {
                let (frames, error) = decoder.feed(Data([byte]))
                XCTAssertNil(error, name)
                got += frames.map(\.header)
            }
            XCTAssertEqual(got, [expected], name)
            XCTAssertNil(decoder.close(), name)
        }
    }

    func testEveryInvalidVectorIsRefusedWithItsCode() throws {
        let doc = try VoiceRepo.json("\(VoiceRepo.protocolDir)/vectors/frames.json")
        let invalid = try XCTUnwrap(doc["invalid"]?.arrayValue)
        XCTAssertFalse(invalid.isEmpty)
        for vector in invalid {
            let name = vector["name"]?.stringValue ?? "?"
            let direction = try XCTUnwrap(vector["direction"]?.stringValue.flatMap(VoiceProtocol.Direction.init), name)
            let frame = try VoiceRepo.b64(vector["frame_b64"])
            let code = try XCTUnwrap(vector["error"]?.stringValue, name)
            XCTAssertTrue(VoiceProtocol.errorCodes.contains(code), name)
            XCTAssertThrowsError(try VoiceFraming.decode(frame, direction: direction), name) { error in
                XCTAssertEqual((error as? VoiceProtocolError)?.code, code, name)
            }
            // The stream decoder plus the direction check: the same code.
            let decoder = VoiceFrameDecoder()
            let (frames, error) = decoder.feed(frame)
            var streamed = error ?? decoder.close()
            if streamed == nil {
                for (header, _) in frames {
                    do {
                        try VoiceProtocol.validate(.object(header), direction)
                    } catch let refusal as VoiceProtocolError {
                        streamed = refusal.code
                    }
                }
            }
            XCTAssertEqual(streamed, code, name)
        }
    }

    /// The JSON corner cases, with the codes `json.loads` and its hooks give.
    func testStrictJSONCodesMirrorThePythonDecoder() throws {
        func code(_ header: String, payload: Data = Data()) -> String? {
            var body = Data(header.utf8)
            body.append(0x0A)
            body.append(payload)
            do {
                _ = try VoiceFraming.decodeBody(body, direction: .childToHost)
                return nil
            } catch {
                return (error as? VoiceProtocolError)?.code
            }
        }
        XCTAssertEqual(code(#"{"type":"log","code":"a","detail":"x","detail":"y"}"#), "duplicate_key")
        XCTAssertEqual(code(#"{"a":1.5,"a":2}"#), "number_not_integer")  // the float comes first
        XCTAssertEqual(code(#"{"a":1,"a":1e3}"#), "number_not_integer")  // ... and here too
        XCTAssertEqual(code(#"{"a":NaN}"#), "invalid_json")
        XCTAssertEqual(code(#"{"a":-Infinity}"#), "invalid_json")
        XCTAssertEqual(code(#"{"a":01}"#), "invalid_json")
        XCTAssertEqual(code(#"{"a":1.}"#), "invalid_json")
        XCTAssertEqual(code("[]"), "header_not_object")
        XCTAssertEqual(code("\"x\""), "header_not_object")
        XCTAssertEqual(code(""), "invalid_json")
        XCTAssertEqual(code("{\"a\":\"\u{01}\"}"), "invalid_json")
        XCTAssertEqual(code(#"{"type":"state","at_ms":99999999999999999999999}"#), "schema_invalid")
        XCTAssertEqual(code(#"{"type":"open_url"}"#), "unknown_type")
        XCTAssertEqual(code(#"{"type":"ready","protocol":"friday-voice/v2","child_version":"1"}"#), "version_unsupported")
        XCTAssertEqual(code(#"{"type":"stop_playback","utterance_id":"u1"}"#, payload: Data([0])), "payload_unexpected")
        XCTAssertEqual(code(#"{"type":"play","utterance_id":"u","seq":0,"last":true,"pcm_bytes":true}"#), "payload_length_mismatch")
        XCTAssertNil(code(#" { "type" : "stop_playback" , "utterance_id" : "u1" } "#))
        XCTAssertNil(code(#"{"type":"log","code":"a","detail":"😀 çağrı"}"#))
        let deep = String(repeating: "[", count: 600) + String(repeating: "]", count: 600)
        XCTAssertEqual(code(deep), "invalid_json")
    }

    func testTheHostNeverEncodesAnInvalidHeader() {
        XCTAssertThrowsError(try VoiceFraming.encode(["type": "mode", "mode": "loud"], direction: .hostToChild))
        XCTAssertThrowsError(try VoiceFraming.encode(["type": "ready", "protocol": "friday-voice/v1", "child_version": "1"],
                                                    direction: .hostToChild))
        XCTAssertNoThrow(try VoiceFraming.encode(["type": "ptt", "key": "down"], direction: .hostToChild))
    }
}
