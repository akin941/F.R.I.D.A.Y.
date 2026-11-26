// The node-local policy of M2 (Phase 9 brief M2, D13 item 5; ADR-013
// decision 5): which transcript directories may be read, whether window
// titles are returned, and which git repos `git.status` may report on.
//
// Nothing in the protocol reaches it: no frame changes it; the node only
// reads it. It is edited in the app's menu, only after the owner passes
// LocalAuthentication (Grants.swift), and stored as one item in the data
// protection keychain (service `friday-node`, account `local-policy`) rather
// than a file in Application Support: the app is not sandboxed, so any
// process of the same user could rewrite a file there, while the keychain
// item is writable only by an app signed with this app's keychain access
// group.

import Foundation
import Security

public struct LocalPolicy: Equatable, Sendable {
    public struct Repo: Equatable, Sendable {
        public var name: String
        /// As the owner chose it (`~/...` under the home, else absolute).
        public var root: String

        public init(name: String, root: String) {
            self.name = name
            self.root = root
        }
    }

    public var grants: ReaderGrants
    public var windowTitles: Bool
    public var repos: [Repo]

    /// Grants off by default, titles off, no repos.
    public init(grants: ReaderGrants = ReaderGrants(), windowTitles: Bool = false, repos: [Repo] = []) {
        self.grants = grants
        self.windowTitles = windowTitles
        self.repos = repos
    }

    public var document: JSONValue {
        [
            "v": 1,
            "grants": ["claude": .bool(grants.claude), "codex": .bool(grants.codex)],
            "window_titles": .bool(windowTitles),
            "repos": .array(repos.map { ["name": .string($0.name), "root": .string($0.root)] }),
        ]
    }

    /// sha256 of the canonical document: the conformance report's policy
    /// fingerprint (unchanged by any session).
    public var fingerprint: String { (try? JSON.sha256Hex(document)) ?? "" }

    public static func decoded(_ data: Data) throws -> LocalPolicy {
        let doc = try JSON.parse(data)
        guard doc["v"] == 1, let claude = doc["grants"]?["claude"]?.boolValue,
            let codex = doc["grants"]?["codex"]?.boolValue, let titles = doc["window_titles"]?.boolValue,
            let repos = doc["repos"]?.arrayValue
        else { throw CredentialError.corrupt }
        return LocalPolicy(
            grants: ReaderGrants(claude: claude, codex: codex), windowTitles: titles,
            repos: try repos.map {
                guard let name = $0["name"]?.stringValue, let root = $0["root"]?.stringValue else {
                    throw CredentialError.corrupt
                }
                return Repo(name: name, root: root)
            })
    }

    /// The readers this policy allows, for the node's home.
    public func readers(home: String, desktop: DesktopSource, usage: UsageSource) -> NodeReaders {
        NodeReaders(
            home: home, repos: repos.map { LocalRepo(name: $0.name, root: $0.root, home: home) }, grants: grants,
            windowTitles: windowTitles, desktop: desktop, usage: usage)
    }
}

public protocol LocalPolicyStore: AnyObject {
    /// The stored policy, or the default (everything off) when none is.
    func load() throws -> LocalPolicy
    func save(_ policy: LocalPolicy) throws
}

public final class MemoryLocalPolicyStore: LocalPolicyStore {
    private var policy: LocalPolicy
    public init(_ policy: LocalPolicy = LocalPolicy()) { self.policy = policy }
    public func load() throws -> LocalPolicy { policy }
    public func save(_ policy: LocalPolicy) throws { self.policy = policy }
}

public final class KeychainLocalPolicyStore: LocalPolicyStore {
    public static let account = "local-policy"

    public init() {}

    private func baseQuery() -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: KeychainCredentialStore.service,
            kSecAttrAccount as String: Self.account,
            kSecUseDataProtectionKeychain as String: true,
            kSecAttrSynchronizable as String: false,
        ]
    }

    public func load() throws -> LocalPolicy {
        var query = baseQuery()
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &out)
        if status == errSecItemNotFound { return LocalPolicy() }
        guard status == errSecSuccess, let data = out as? Data else { throw CredentialError.keychain(status) }
        return try LocalPolicy.decoded(data)
    }

    public func save(_ policy: LocalPolicy) throws {
        let data = try JSON.canonical(policy.document)
        let update = SecItemUpdate(baseQuery() as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        if update == errSecSuccess { return }
        guard update == errSecItemNotFound else { throw CredentialError.keychain(update) }
        var add = baseQuery()
        add[kSecValueData as String] = data
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let status = SecItemAdd(add as CFDictionary, nil)
        guard status == errSecSuccess else { throw CredentialError.keychain(status) }
    }
}
