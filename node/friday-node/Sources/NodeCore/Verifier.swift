// Signed envelopes and the node-side verification order v1 (Phase 9 brief
// D4, D5; mirrors backend/nodes/protocol/signing.py and verify.py).
//
// `sig` is Ed25519 over the canonical JSON of the envelope without `sig`
// (v1 has no signature domain); `key_id` is the first 16 hex characters of
// sha256 of the raw 32-byte public key; signatures and nonces are base64url
// without padding. The first failing check wins and nothing after it runs.
// A refusal never echoes the payload.

import CryptoKit
import Foundation

public enum Base64URL {
    private static let alphabet = Set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_".utf8)

    public static func encode(_ data: Data) -> String {
        data.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }

    /// Strict: only the URL-safe alphabet, no padding, and the canonical
    /// encoding of the decoded bytes (no stray trailing bits).
    public static func decode(_ text: String) -> Data? {
        guard text.utf8.allSatisfy({ alphabet.contains($0) }) else { return nil }
        var standard = text.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        let remainder = standard.count % 4
        if remainder == 1 { return nil }
        if remainder > 0 { standard += String(repeating: "=", count: 4 - remainder) }
        guard let data = Data(base64Encoded: standard), encode(data) == text else { return nil }
        return data
    }
}

public enum Signing {
    public static let keyBytes = 32
    public static let signatureBytes = 64

    /// The key id: first 16 hex chars of sha256 of the raw public key.
    public static func keyID(for publicKey: Data) -> String {
        String(JSON.hexDigest(publicKey).prefix(16))
    }

    /// A raw 32-byte Ed25519 public key from standard base64 (the pairing
    /// response's `server_signing_keys[].public_key_b64`).
    public static func publicKey(base64 text: String) -> Data? {
        guard let raw = Data(base64Encoded: text.trimmingCharacters(in: .whitespaces)),
            raw.count == keyBytes, (try? Curve25519.Signing.PublicKey(rawRepresentation: raw)) != nil
        else { return nil }
        return raw
    }

    /// The v2 signature domains (Phase 10 D1): an envelope is signed over
    /// `invokeDomainV2 + canonical(envelope - sig)`, a grant over
    /// `grantDomainV2 + canonical(grant - sig)`; v1 envelopes have none.
    public static let invokeDomainV2 = Data("friday-node/invoke/v2\n".utf8)
    public static let grantDomainV2 = Data("friday-node/grant/v2\n".utf8)

    /// The signature domain of an envelope of protocol `version`; nil for an
    /// unknown version (which never verifies).
    public static func envelopeDomain(_ version: JSONValue?) -> Data? {
        switch version {
        case .int(1): return Data()
        case .int(2): return invokeDomainV2
        default: return nil
        }
    }

    /// D5 checks 5 and 6 for an envelope against the pinned keys
    /// (`[key_id: raw public key]`). Throws `unknown_key` / `bad_signature`.
    /// `version` is the session's negotiated version: an envelope of another
    /// `v` is `bad_signature`; the envelope's own `v` picks the domain
    /// (signing.py `verify_envelope`).
    public static func verify(_ envelope: JSONValue, pinned: [String: Data], version: Int64 = 1) throws {
        guard let keyID = envelope["key_id"]?.stringValue, pinned[keyID] != nil else {
            throw ProtocolError("unknown_key")
        }
        guard envelope["v"] == .int(version), let domain = envelopeDomain(envelope["v"]) else {
            throw ProtocolError("bad_signature")
        }
        try verify(envelope, pinned: pinned, domain: domain)
    }

    /// `signed["sig"]` over `domain + canonical(signed without sig)` against
    /// the pinned keys (signing.py `verify_with_domain`). Throws
    /// `unknownKey` / `badSignature`.
    public static func verify(
        _ signed: JSONValue, pinned: [String: Data], domain: Data, unknownKey: String = "unknown_key",
        badSignature: String = "bad_signature"
    ) throws {
        guard let fields = signed.objectValue, let keyID = fields["key_id"]?.stringValue,
            let raw = pinned[keyID]
        else { throw ProtocolError(unknownKey) }
        guard let sigText = fields["sig"]?.stringValue, let signature = Base64URL.decode(sigText),
            signature.count == signatureBytes
        else { throw ProtocolError(badSignature) }
        var body = fields
        body.removeValue(forKey: "sig")
        guard let canonical = try? JSON.canonical(.object(body)),
            let key = try? Curve25519.Signing.PublicKey(rawRepresentation: raw),
            key.isValidSignature(signature, for: domain + canonical)
        else { throw ProtocolError(badSignature) }
    }
}

/// D5 check 11: nonces kept until `expires_at + skew`, bounded; a full
/// cache refuses rather than evicting a live nonce. It survives reconnects
/// (a replay across sockets is a replay).
public final class NonceCache {
    public let capacity: Int
    public let skew: Int64
    private var keepUntil: [String: Int64] = [:]
    private var order: [String] = []

    public init(capacity: Int = NodeProtocol.nonceCacheSize, skew: Int64 = NodeProtocol.clockSkewSeconds) {
        self.capacity = capacity
        self.skew = skew
    }

    public var count: Int { keepUntil.count }

    public func purge(now: Int64) {
        order.removeAll { nonce in
            guard let until = keepUntil[nonce], until > now else {
                keepUntil.removeValue(forKey: nonce)
                return true
            }
            return false
        }
    }

    public func checkAndAdd(_ nonce: String, expiresAt: Int64, now: Int64) throws {
        purge(now: now)
        if keepUntil[nonce] != nil { throw Refusal(code: "replay", check: 11) }
        if keepUntil.count >= capacity { throw Refusal(code: "nonce_cache_full", check: 11) }
        keepUntil[nonce] = expiresAt + skew
        order.append(nonce)
    }
}

/// One invoke refused at `check` with D5 refusal `code`.
public struct Refusal: Error, Equatable {
    public let code: String
    public let check: Int

    public init(code: String, check: Int) {
        self.code = code
        self.check = check
    }

    /// Checks 1-4 are frame errors (an `error` result, not `refused`).
    public var isFrameError: Bool { check <= 4 }
}

/// What the node knows locally: its id, the keys pinned at pairing, its
/// **own** catalogue and its nonce cache.
public struct VerifierState {
    public let nodeID: String
    public let pinned: [String: Data]
    public let catalogue: Catalogue
    public let nonceCache: NonceCache

    public init(nodeID: String, pinned: [String: Data], catalogue: Catalogue, nonceCache: NonceCache) {
        self.nodeID = nodeID
        self.pinned = pinned
        self.catalogue = catalogue
        self.nonceCache = nonceCache
    }
}

public enum Verifier {
    private static let frameErrorChecks = ["too_large": 1, "malformed": 2, "unknown_type": 3]

    /// Run checks 1-16 on one server -> node frame. Returns the parsed
    /// message; a non-`invoke` frame passes checks 1-4 and is returned
    /// untouched. Throws `Refusal`.
    public static func verify(_ raw: Data, state: VerifierState, now: Int64) throws -> JSONValue {
        let message: JSONValue
        do {
            message = try NodeProtocol.decodeFrame(raw)
        } catch let error as ProtocolError {
            throw Refusal(code: error.code, check: frameErrorChecks[error.code] ?? 4)
        }
        guard message["type"]?.stringValue == "invoke" else { return message }
        try verifyInvoke(message, state: state, now: now)
        return message
    }

    /// Checks 5-16 on an `invoke` that already passed checks 1-4 (so every
    /// field below exists with its schema type).
    static func verifyInvoke(_ envelope: JSONValue, state: VerifierState, now: Int64) throws {
        do {
            try Signing.verify(envelope, pinned: state.pinned)  // 5, 6
        } catch let error as ProtocolError {
            throw Refusal(code: error.code, check: error.code == "unknown_key" ? 5 : 6)
        }
        guard envelope["node_id"]?.stringValue == state.nodeID else {
            throw Refusal(code: "wrong_node", check: 7)
        }
        let issuedAt = envelope["issued_at"]?.intValue ?? 0
        let expiresAt = envelope["expires_at"]?.intValue ?? 0
        if issuedAt > now + NodeProtocol.clockSkewSeconds { throw Refusal(code: "not_yet_valid", check: 8) }
        if expiresAt <= now { throw Refusal(code: "expired", check: 9) }
        let ttl = expiresAt - issuedAt
        if ttl <= 0 || ttl > NodeProtocol.maxReadInvokeTTLSeconds { throw Refusal(code: "ttl_too_long", check: 10) }
        // Check 11 before 12-16 on purpose: a refused envelope still burns
        // its nonce; a server retry uses a fresh one.
        try state.nonceCache.checkAndAdd(envelope["nonce"]?.stringValue ?? "", expiresAt: expiresAt, now: now)
        guard let capability = envelope["capability"]?.stringValue,
            let entry = state.catalogue.byName[capability],
            entry["version"] == envelope["capability_version"]
        else { throw Refusal(code: "unknown_capability", check: 12) }
        guard envelope["class"]?.stringValue == "read", envelope["grant"] == .null else {
            throw Refusal(code: "class_not_allowed", check: 13)
        }
        let args = envelope["args"] ?? .null
        let argsHash = try? JSON.sha256Hex(args)
        guard argsHash != nil, argsHash == envelope["args_sha256"]?.stringValue else {
            throw Refusal(code: "args_hash_mismatch", check: 14)
        }
        guard Schema.isValid(args, against: entry["args_schema"] ?? .object([:])) else {
            throw Refusal(code: "args_schema", check: 15)
        }
        guard entry["availability"]?.stringValue == "granted" else {
            throw Refusal(code: "not_granted", check: 16)
        }
    }
}
