// The invocation grant of a gate-class invoke, node side (Phase 10 brief
// D2, D5 checks 15-18; ADR-013 decision 4). A port of
// backend/nodes/protocol/grants.py `verify_grant_signature`,
// `check_grant_binding` and `check_grant_ttl`.
//
// - Signature: Ed25519 with the same pinned server key as the envelope, over
//   `b"friday-node/grant/v2\n" + canonical(grant without sig)`. Domain
//   separation keeps grants and envelopes apart: a grant signature never
//   verifies as an envelope (or without a domain), and the reverse.
// - Binding: every shared field equal between grant and envelope, type
//   strict (`1` never equals `true`), the grant's `v` 2 and class `gate`,
//   and the envelope's class `gate`.
// - Lifetime: `0 < expires_at - issued_at <= 120`.
//
// Each check throws `Refusal` with the D5 code at the D5 check number; the
// order they run in is VerifierV2's. Nothing here echoes grant content.

import Foundation

public enum GrantVerifier {
    public static let grantVersion: Int64 = 2
    /// The fields a grant and its envelope must carry identically (D2).
    public static let sharedFields = [
        "node_id", "workspace_id", "capability", "capability_version", "args_sha256", "nonce", "issued_at",
        "expires_at",
    ]

    /// D5 checks 15-16: the grant's `key_id` is pinned (`unknown_key`, 15)
    /// and its signature verifies in the grant/v2 domain
    /// (`bad_grant_signature`, 16).
    public static func verifySignature(_ grant: JSONValue, pinned: [String: Data]) throws {
        guard grant.objectValue != nil else { throw Refusal(code: "bad_grant_signature", check: 16) }
        do {
            try Signing.verify(
                grant, pinned: pinned, domain: Signing.grantDomainV2, unknownKey: "unknown_key",
                badSignature: "bad_grant_signature")
        } catch let error as ProtocolError {
            throw Refusal(code: error.code, check: error.code == "unknown_key" ? 15 : 16)
        }
    }

    /// D5 check 17: every shared field equal, `v` 2, class `gate` on both.
    public static func checkBinding(_ grant: JSONValue, envelope: JSONValue) throws {
        let mismatch = Refusal(code: "grant_mismatch", check: 17)
        guard grant["v"] == .int(grantVersion), grant["class"] == .string("gate"),
            envelope["class"] == .string("gate")
        else { throw mismatch }
        for name in sharedFields {
            // JSONValue equality is type-strict: .int(1) != .bool(true).
            guard let left = grant[name], let right = envelope[name], left == right else { throw mismatch }
        }
    }

    /// D5 check 18: `0 < expires_at - issued_at <= MAX_GRANT_TTL_SECONDS`.
    public static func checkTTL(_ grant: JSONValue, maxTTL: Int64 = NodeProtocol.maxGrantTTLSeconds) throws {
        guard let issued = grant["issued_at"]?.intValue, let expires = grant["expires_at"]?.intValue else {
            throw Refusal(code: "ttl_too_long", check: 18)
        }
        let ttl = expires - issued
        if ttl <= 0 || ttl > min(maxTTL, NodeProtocol.maxGrantTTLSeconds) {
            throw Refusal(code: "ttl_too_long", check: 18)
        }
    }
}
