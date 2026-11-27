// The v2 protocol vectors (Phase 10 brief M1 "Must satisfy"; runbook B5
// contract test 1): backend/nodes/protocol/vectors/signing_v2.json and
// grants.json, loaded from the repo path. Every `grants.json` refusal gives
// the same code at the same check as the reference verifier.

import CryptoKit
import Foundation
import NodeCore
import XCTest

final class SigningV2VectorTests: XCTestCase {
    private func load() throws -> (JSONValue, [String: Data]) {
        let doc = try Repo.json("backend/nodes/protocol/vectors/signing_v2.json")
        let key = try XCTUnwrap(Signing.publicKey(base64: doc["public_key_b64"]?.stringValue ?? ""))
        XCTAssertEqual(Signing.keyID(for: key), doc["key_id"]?.stringValue)
        return (doc, [Signing.keyID(for: key): key])
    }

    func testTheDomainsAreTheVectorBytes() throws {
        let (doc, _) = try load()
        XCTAssertEqual(Signing.invokeDomainV2, Data(try XCTUnwrap(doc["invoke_domain"]?.stringValue).utf8))
        XCTAssertEqual(Signing.grantDomainV2, Data(try XCTUnwrap(doc["grant_domain"]?.stringValue).utf8))
    }

    func testReadAndConfirmEnvelopesVerifyOverTheExactMessage() throws {
        let (doc, pinned) = try load()
        for name in ["read_envelope", "confirm_envelope"] {
            let envelope = try XCTUnwrap(doc[name])
            XCTAssertNoThrow(try Signing.verify(envelope, pinned: pinned, version: 2), name)
            var body = try XCTUnwrap(envelope.objectValue)
            body.removeValue(forKey: "sig")
            let message = Signing.invokeDomainV2 + (try JSON.canonical(.object(body)))
            XCTAssertEqual(String(decoding: message, as: UTF8.self), doc["\(name)_signed_message"]?.stringValue, name)
            // A v2 envelope never verifies on a v1 session.
            XCTAssertThrowsError(try Signing.verify(envelope, pinned: pinned, version: 1), name)
            // And it passes the v2 schema (checks 1-4).
            XCTAssertNoThrow(try NodeProtocol.decodeFrame(try JSON.canonical(envelope), version: 2), name)
        }
    }

    func testOtherDomainsNeverVerify() throws {
        let (doc, pinned) = try load()
        let cases = try XCTUnwrap(doc["must_not_verify"]?.arrayValue)
        XCTAssertEqual(cases.count, 3)
        for item in cases {
            let name = item["name"]?.stringValue ?? "?"
            XCTAssertThrowsError(try Signing.verify(try XCTUnwrap(item["envelope"]), pinned: pinned, version: 2), name) {
                XCTAssertEqual($0 as? ProtocolError, ProtocolError("bad_signature"), name)
            }
        }
    }
}

final class GrantVectorTests: XCTestCase {
    private struct Vectors {
        let doc: JSONValue
        let pinned: [String: Data]
        let state: () -> VerifierState
        let now: Int64
    }

    private func load() throws -> Vectors {
        let doc = try Repo.json("backend/nodes/protocol/vectors/grants.json")
        let key = try XCTUnwrap(Signing.publicKey(base64: doc["public_key_b64"]?.stringValue ?? ""))
        XCTAssertEqual(Signing.keyID(for: key), doc["key_id"]?.stringValue)
        let pinned = [Signing.keyID(for: key): key]
        let nodeState = try XCTUnwrap(doc["node_state"])
        XCTAssertEqual(nodeState["pinned_key_ids"], [.string(Signing.keyID(for: key))])
        let nodeID = try XCTUnwrap(nodeState["node_id"]?.stringValue)
        // "the server's own contracts (nodes/capabilities/v1 and v2), all granted"
        return Vectors(
            doc: doc, pinned: pinned,
            state: {
                VerifierState(
                    nodeID: nodeID, pinned: pinned, catalogue: .standard(actions: true), nonceCache: NonceCache())
            }, now: try XCTUnwrap(nodeState["now"]?.intValue))
    }

    func testTheSharedFieldsAndTheCeilingAreTheVectors() throws {
        let vectors = try load()
        XCTAssertEqual(vectors.doc["shared_fields"], .array(GrantVerifier.sharedFields.map(JSONValue.string)))
        XCTAssertEqual(vectors.doc["max_grant_ttl_seconds"]?.intValue, NodeProtocol.maxGrantTTLSeconds)
    }

    func testTheGatedEnvelopePassesEveryProtocolCheck() throws {
        let vectors = try load()
        let envelope = try XCTUnwrap(vectors.doc["gated_envelope"])
        let decoded = try NodeProtocol.decodeFrame(try JSON.canonical(envelope), version: 2)
        XCTAssertNoThrow(try VerifierV2.verifyInvoke(decoded, state: vectors.state(), now: vectors.now))
        // The exact bytes both signatures cover.
        let grant = try XCTUnwrap(envelope["grant"]?.objectValue)
        var grantBody = grant
        grantBody.removeValue(forKey: "sig")
        XCTAssertEqual(
            String(decoding: Signing.grantDomainV2 + (try JSON.canonical(.object(grantBody))), as: UTF8.self),
            vectors.doc["grant_signed_message"]?.stringValue)
        var envelopeBody = try XCTUnwrap(envelope.objectValue)
        envelopeBody.removeValue(forKey: "sig")
        XCTAssertEqual(
            String(decoding: Signing.invokeDomainV2 + (try JSON.canonical(.object(envelopeBody))), as: UTF8.self),
            vectors.doc["envelope_signed_message"]?.stringValue)
        // The unsigned grant plus key_id is what was signed. (CryptoKit's
        // Ed25519 signatures are randomized, so a re-signature differs from
        // `sig` byte for byte but verifies the same.)
        let privateRaw = try XCTUnwrap(Data(base64Encoded: vectors.doc["private_key_b64"]?.stringValue ?? ""))
        let key = try Curve25519.Signing.PrivateKey(rawRepresentation: privateRaw)
        var unsigned = try XCTUnwrap(vectors.doc["unsigned_grant"]?.objectValue)
        unsigned["key_id"] = .string(Signing.keyID(for: key.publicKey.rawRepresentation))
        XCTAssertEqual(unsigned, grantBody)
        XCTAssertNoThrow(try GrantVerifier.verifySignature(.object(grant), pinned: vectors.pinned))
        let signature = try key.signature(for: Signing.grantDomainV2 + JSON.canonical(.object(unsigned)))
        var resigned = unsigned
        resigned["sig"] = .string(Base64URL.encode(signature))
        XCTAssertNoThrow(try GrantVerifier.verifySignature(.object(resigned), pinned: vectors.pinned))
        // The same signature in the invoke domain, or with none, is refused.
        for domain in [Signing.invokeDomainV2, Data()] {
            var other = unsigned
            other["sig"] = .string(Base64URL.encode(try key.signature(for: domain + JSON.canonical(.object(unsigned)))))
            XCTAssertThrowsError(try GrantVerifier.verifySignature(.object(other), pinned: vectors.pinned)) {
                XCTAssertEqual($0 as? Refusal, Refusal(code: "bad_grant_signature", check: 16))
            }
        }
    }

    /// Every refusal: the same code at the same check (checks 1-12 pass).
    func testEveryRefusalHasTheReferenceCodeAndCheck() throws {
        let vectors = try load()
        let refusals = try XCTUnwrap(vectors.doc["refusals"]?.arrayValue)
        XCTAssertEqual(refusals.count, 29)
        for item in refusals {
            let name = item["name"]?.stringValue ?? "?"
            let envelope = try XCTUnwrap(item["envelope"], name)
            let expected = Refusal(
                code: try XCTUnwrap(item["code"]?.stringValue, name), check: Int(try XCTUnwrap(item["check"]?.intValue)))
            let decoded = try NodeProtocol.decodeFrame(try JSON.canonical(envelope), version: 2)
            XCTAssertThrowsError(try VerifierV2.verifyInvoke(decoded, state: vectors.state(), now: vectors.now), name) {
                XCTAssertEqual($0 as? Refusal, expected, name)
            }
        }
    }

    func testBindingIsTypeStrictAndTTLIsBounded() throws {
        let vectors = try load()
        let envelope = try XCTUnwrap(vectors.doc["gated_envelope"])
        let grant = try XCTUnwrap(envelope["grant"])
        XCTAssertNoThrow(try GrantVerifier.checkBinding(grant, envelope: envelope))
        XCTAssertNoThrow(try GrantVerifier.checkTTL(grant))
        for (field, value) in [("capability_version", JSONValue.bool(true)), ("v", .int(1)), ("class", "confirm")] {
            var changed = try XCTUnwrap(grant.objectValue)
            changed[field] = value
            XCTAssertThrowsError(try GrantVerifier.checkBinding(.object(changed), envelope: envelope), field) {
                XCTAssertEqual($0 as? Refusal, Refusal(code: "grant_mismatch", check: 17))
            }
        }
        var long = try XCTUnwrap(grant.objectValue)
        long["expires_at"] = .int((grant["issued_at"]?.intValue ?? 0) + 121)
        XCTAssertThrowsError(try GrantVerifier.checkTTL(.object(long))) {
            XCTAssertEqual($0 as? Refusal, Refusal(code: "ttl_too_long", check: 18))
        }
        long["expires_at"] = grant["issued_at"]
        XCTAssertThrowsError(try GrantVerifier.checkTTL(.object(long)))
    }

    /// The v2 refusal codes are exactly the `result.refusal_code` enum.
    func testRefusalCodesMatchTheResultSchema() throws {
        let schema = try XCTUnwrap(NodeProtocol.schema(for: "result", version: 2))
        let codes = try XCTUnwrap(schema["properties"]?["refusal_code"]?["enum"]?.arrayValue)
        XCTAssertEqual(codes.compactMap(\.stringValue), NodeProtocol.refusalCodesV2)
    }
}
