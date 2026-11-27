// The node's credential and where it lives (Phase 9 brief M1, D4; ADR-013
// decisions 2 and 4; the 2026-11-25 d9 amendment and the A1 spike result).
//
// One Keychain item holds everything pairing established: the node id, the
// `jnd_` secret, the server's base URL, the pinned server signing keys and
// the TLS SPKI pin captured at pairing. It is a `kSecClassGenericPassword`,
// service `friday-node`, in the **data protection keychain**
// (`kSecUseDataProtectionKeychain`, which needs the Keychain Sharing
// entitlement A1 proved), accessible after first unlock on this device
// only, never synchronized to iCloud. Every query passes
// `kSecUseDataProtectionKeychain`, deletes included: on macOS a query
// without it can also match data-protection items (A1 finding).

import Foundation
import Security

public struct NodeCredential: Equatable, Sendable {
    public var nodeID: String
    public var secret: String
    /// The server origin, `https://host[:port]`; the gateway is
    /// `wss://host[:port]` + `gatewayPath`.
    public var serverURL: URL
    public var gatewayPath: String
    /// `[key_id: raw 32-byte Ed25519 public key]`, pinned at pairing.
    public var pinnedKeys: [String: Data]
    /// sha256 of the server certificate's SubjectPublicKeyInfo (DER).
    public var tlsPinSHA256: Data
    public var rotateAfterDays: Int64
    /// When the current secret was issued (node clock, Unix seconds).
    public var secretIssuedAt: Int64

    public init(
        nodeID: String, secret: String, serverURL: URL, gatewayPath: String, pinnedKeys: [String: Data],
        tlsPinSHA256: Data, rotateAfterDays: Int64, secretIssuedAt: Int64
    ) {
        self.nodeID = nodeID
        self.secret = secret
        self.serverURL = serverURL
        self.gatewayPath = gatewayPath
        self.pinnedKeys = pinnedKeys
        self.tlsPinSHA256 = tlsPinSHA256
        self.rotateAfterDays = rotateAfterDays
        self.secretIssuedAt = secretIssuedAt
    }

    /// `wss://` + host + gateway path.
    public var gatewayURL: URL? {
        guard var components = URLComponents(url: serverURL, resolvingAgainstBaseURL: false) else { return nil }
        components.scheme = "wss"
        components.path = gatewayPath
        components.query = nil
        return components.url
    }

    public func rotationDue(now: Int64) -> Bool {
        now - secretIssuedAt >= rotateAfterDays * 86_400
    }

    // MARK: Storage form (never logged)

    public func encoded() throws -> Data {
        var keys: [String: JSONValue] = [:]
        for (id, raw) in pinnedKeys { keys[id] = .string(raw.base64EncodedString()) }
        return try JSON.canonical([
            "v": 1,
            "node_id": .string(nodeID),
            "secret": .string(secret),
            "server_url": .string(serverURL.absoluteString),
            "gateway_path": .string(gatewayPath),
            "pinned_keys": .object(keys),
            "tls_pin_sha256": .string(tlsPinSHA256.base64EncodedString()),
            "rotate_after_days": .int(rotateAfterDays),
            "secret_issued_at": .int(secretIssuedAt),
        ])
    }

    public static func decoded(_ data: Data) throws -> NodeCredential {
        let doc = try JSON.parse(data)
        guard doc["v"] == 1, let nodeID = doc["node_id"]?.stringValue, let secret = doc["secret"]?.stringValue,
            let server = doc["server_url"]?.stringValue.flatMap(URL.init(string:)),
            let path = doc["gateway_path"]?.stringValue, let keys = doc["pinned_keys"]?.objectValue,
            let pin = doc["tls_pin_sha256"]?.stringValue.flatMap({ Data(base64Encoded: $0) }), pin.count == 32,
            let rotate = doc["rotate_after_days"]?.intValue, let issued = doc["secret_issued_at"]?.intValue
        else { throw CredentialError.corrupt }
        var pinned: [String: Data] = [:]
        for (id, value) in keys {
            guard let raw = value.stringValue.flatMap(Signing.publicKey(base64:)), Signing.keyID(for: raw) == id
            else { throw CredentialError.corrupt }
            pinned[id] = raw
        }
        guard !pinned.isEmpty else { throw CredentialError.corrupt }
        return NodeCredential(
            nodeID: nodeID, secret: secret, serverURL: server, gatewayPath: path, pinnedKeys: pinned,
            tlsPinSHA256: pin, rotateAfterDays: rotate, secretIssuedAt: issued)
    }
}

public enum CredentialError: Error, Equatable {
    case corrupt
    case keychain(OSStatus)
}

public protocol CredentialStore: AnyObject {
    func load() throws -> NodeCredential?
    func save(_ credential: NodeCredential) throws
    func delete() throws
}

/// Tests and the conformance build.
public final class MemoryCredentialStore: CredentialStore {
    private var stored: Data?
    public init() {}
    public func load() throws -> NodeCredential? { try stored.map(NodeCredential.decoded) }
    public func save(_ credential: NodeCredential) throws { stored = try credential.encoded() }
    public func delete() throws { stored = nil }
}

public final class KeychainCredentialStore: CredentialStore {
    public static let service = "friday-node"
    public static let account = "credential"

    public init() {}

    private func baseQuery() -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: Self.service,
            kSecAttrAccount as String: Self.account,
            kSecUseDataProtectionKeychain as String: true,
            kSecAttrSynchronizable as String: false,
        ]
    }

    public func load() throws -> NodeCredential? {
        var query = baseQuery()
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &out)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = out as? Data else { throw CredentialError.keychain(status) }
        return try NodeCredential.decoded(data)
    }

    /// Replace the item. Used for pairing and for rotation, where the new
    /// secret must be persisted before the rotation is considered done.
    public func save(_ credential: NodeCredential) throws {
        let data = try credential.encoded()
        let update = SecItemUpdate(baseQuery() as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        if update == errSecSuccess { return }
        guard update == errSecItemNotFound else { throw CredentialError.keychain(update) }
        var add = baseQuery()
        add[kSecValueData as String] = data
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let status = SecItemAdd(add as CFDictionary, nil)
        guard status == errSecSuccess else { throw CredentialError.keychain(status) }
    }

    public func delete() throws {
        let status = SecItemDelete(baseQuery() as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw CredentialError.keychain(status) }
    }
}
