// The owner's command mappings (Phase 10 brief D4, D5 check 22, M2;
// ADR-013 decision 5; runbook B6): which repos exist (root, local name),
// which `command_kind` runs which argv with which PATH, environment names,
// network domains, runtime cap and sandbox mode; which recipes are
// enabled; which bundle ids may be opened, focused or driven; and where
// `srt` lives.
//
// - The `policy` part is exactly the `local_policy.json` shape that
//   `ActionPolicy.load` (check 22, the reference's `LocalPolicy`) reads, so
//   the session resolves intents against the very document the owner
//   saved.
// - Nothing in the protocol reaches it. It is changed only through
//   `CommandMappingsEditor`, after the owner passes LocalAuthentication
//   (`.deviceOwnerAuthentication`: Touch ID or the login password), and
//   stored as one item in the data protection keychain (service
//   `friday-node`, account `command-mappings`): the B2 pattern of
//   LocalPolicy / PolicyEditor, for the same reason (an unsandboxed app's
//   files are writable by any process of the user; the keychain item only
//   by an app signed with this app's access group).
// - On top of the reference's own checks, a mapping is refused when its
//   PATH has a relative entry or lacks `/bin` (srt finds `bash` with
//   `which` on the child's PATH, A6 finding 1), when a recipe is not in the
//   node's catalogue, or when a bundle id is not a well-formed one.

import Foundation
import Security

public struct CommandMappings: Equatable, Sendable {
    public struct Command: Equatable, Sendable {
        public var kind: String
        public var argv: [String]
        public var path: String
        public var envNames: [String]
        public var networkDomains: [String]
        public var maxRuntimeSeconds: Int64
        public var mode: String

        public init(
            kind: String, argv: [String], path: String = CommandMapping.defaultPath, envNames: [String] = [],
            networkDomains: [String] = [], maxRuntimeSeconds: Int64 = 900, mode: String = "in_place"
        ) {
            self.kind = kind
            self.argv = argv
            self.path = path
            self.envNames = envNames
            self.networkDomains = networkDomains
            self.maxRuntimeSeconds = maxRuntimeSeconds
            self.mode = mode
        }

        var mapping: CommandMapping {
            CommandMapping(
                kind: kind, argv: argv, path: path, envNames: envNames, networkDomains: networkDomains,
                maxRuntimeSeconds: maxRuntimeSeconds, mode: mode)
        }
    }

    public struct Repo: Equatable, Sendable {
        public var name: String
        /// As the owner chose it (`~/...` under the home, else absolute).
        public var root: String
        public var commands: [Command]

        public init(name: String, root: String, commands: [Command] = []) {
            self.name = name
            self.root = root
            self.commands = commands
        }
    }

    public var repos: [Repo]
    public var recipesEnabled: [String]
    public var bundleIDs: [String]
    public var srt: SrtLocation

    public init(repos: [Repo] = [], recipesEnabled: [String] = [], bundleIDs: [String] = [],
                srt: SrtLocation = SrtLocation()) {
        self.repos = repos
        self.recipesEnabled = recipesEnabled
        self.bundleIDs = bundleIDs
        self.srt = srt
    }

    // MARK: Changes keyed by repo root and command kind (B6 review finding
    // 10): the UI never applies an index it read before the Touch ID
    // prompt; a target that is gone by then refuses the change.

    public struct TargetGone: Error, Equatable, CustomStringConvertible {
        public let description: String
    }

    /// Change the repo whose root is `root`; refused when it is not mapped.
    public mutating func updateRepo(root: String, _ body: (inout Repo) throws -> Void) throws {
        guard let index = repos.firstIndex(where: { Scalars.equal($0.root, root) }) else {
            throw TargetGone(description: "the repo \(root) is no longer mapped")
        }
        try body(&repos[index])
    }

    public mutating func removeRepo(root: String) throws {
        try updateRepo(root: root) { _ in }
        repos.removeAll { Scalars.equal($0.root, root) }
    }

    public mutating func removeCommand(root: String, kind: String) throws {
        try updateRepo(root: root) { repo in
            guard repo.commands.contains(where: { $0.kind == kind }) else {
                throw TargetGone(description: "\(kind) is no longer mapped for \(root)")
            }
            repo.commands.removeAll { $0.kind == kind }
        }
    }

    /// Map (or replace) `command.kind` for the repo at `root`.
    public mutating func setCommand(root: String, _ command: Command) throws {
        try updateRepo(root: root) { repo in
            repo.commands.removeAll { $0.kind == command.kind }
            repo.commands.append(command)
        }
    }

    static let bundlePattern = "^[A-Za-z0-9-]{1,63}(?:\\.[A-Za-z0-9-]{1,63}){1,7}$"

    /// The `local_policy.json` document check 22 reads.
    public var policyDocument: JSONValue {
        [
            "repos": .array(repos.map { repo in
                [
                    "name": .string(repo.name),
                    "root": .string(repo.root),
                    "commands": .array(repo.commands.map { $0.mapping.document }),
                ]
            }),
            "recipes_enabled": .array(recipesEnabled.map(JSONValue.string)),
            "bundle_ids": .array(bundleIDs.map(JSONValue.string)),
        ]
    }

    /// What the keychain item holds.
    public var document: JSONValue {
        ["v": 1, "policy": policyDocument, "srt": srt.document]
    }

    /// The check-22 policy for `home`; throws when the mappings are unsafe
    /// or malformed (the reference's checks plus this file's).
    public func actionPolicy(home: String) throws -> ActionPolicy {
        try validate()
        return try ActionPolicy.load(policyDocument, home: home)
    }

    /// The checks beyond the reference's.
    public func validate() throws {
        for repo in repos {
            for command in repo.commands {
                let entries = command.path.split(separator: ":", omittingEmptySubsequences: false).map(String.init)
                guard entries.allSatisfy({ $0.hasPrefix("/") }) else {
                    throw ActionPolicyError("\(repo.name)/\(command.kind): PATH entries must be absolute")
                }
                guard entries.contains("/bin") else {
                    throw ActionPolicyError("\(repo.name)/\(command.kind): PATH must contain /bin (srt runs bash)")
                }
                // srt's own Node inherits the command's environment: no
                // loader or option variable (NODE_*, DYLD_*, OPENSSL_CONF*,
                // UV_*) may be mapped (hardening N3 and its review).
                if let name = command.envNames.first(where: SrtExecutor.isLoaderVariable) {
                    throw ActionPolicyError("\(repo.name)/\(command.kind): \(name) is refused (it would steer srt's Node)")
                }
            }
        }
        for recipe in recipesEnabled where RecipeContract.all[recipe] == nil || RecipeBook.recipe(named: recipe) == nil {
            throw ActionPolicyError("unknown recipe \(recipe)")
        }
        for bundle in bundleIDs where !Schema.fullMatch(bundle, Self.bundlePattern) {
            throw ActionPolicyError("bad bundle id")
        }
        if srt.node.isEmpty || !srt.node.hasPrefix("/") || srt.prefix.isEmpty || !srt.prefix.hasPrefix("/") {
            throw ActionPolicyError("srt paths must be absolute")
        }
    }

    public static func decoded(_ data: Data) throws -> CommandMappings {
        let doc = try JSON.parse(data)
        guard doc["v"] == 1, let policy = doc["policy"], let srt = SrtLocation.from(doc["srt"]) else {
            throw CredentialError.corrupt
        }
        func strings(_ value: JSONValue?) throws -> [String] {
            try (value?.arrayValue ?? []).map {
                guard let text = $0.stringValue else { throw CredentialError.corrupt }
                return text
            }
        }
        var repos: [Repo] = []
        for repo in policy["repos"]?.arrayValue ?? [] {
            guard let name = repo["name"]?.stringValue, let root = repo["root"]?.stringValue else {
                throw CredentialError.corrupt
            }
            var commands: [Command] = []
            for command in repo["commands"]?.arrayValue ?? [] {
                guard let kind = command["kind"]?.stringValue, let path = command["path"]?.stringValue,
                    let runtime = command["max_runtime_seconds"]?.intValue, let mode = command["mode"]?.stringValue
                else { throw CredentialError.corrupt }
                commands.append(
                    Command(
                        kind: kind, argv: try strings(command["argv"]), path: path,
                        envNames: try strings(command["env_names"]),
                        networkDomains: try strings(command["network_domains"]), maxRuntimeSeconds: runtime,
                        mode: mode))
            }
            repos.append(Repo(name: name, root: root, commands: commands))
        }
        return CommandMappings(
            repos: repos, recipesEnabled: try strings(policy["recipes_enabled"]),
            bundleIDs: try strings(policy["bundle_ids"]), srt: srt)
    }
}

public protocol CommandMappingsStore: AnyObject {
    /// The stored mappings, or the default (nothing mapped) when none are.
    func load() throws -> CommandMappings
    func save(_ mappings: CommandMappings) throws
}

public final class MemoryCommandMappingsStore: CommandMappingsStore {
    private var mappings: CommandMappings
    public init(_ mappings: CommandMappings = CommandMappings()) { self.mappings = mappings }
    public func load() throws -> CommandMappings { mappings }
    public func save(_ mappings: CommandMappings) throws { self.mappings = mappings }
}

public final class KeychainCommandMappingsStore: CommandMappingsStore {
    public static let account = "command-mappings"

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

    public func load() throws -> CommandMappings {
        var query = baseQuery()
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &out)
        if status == errSecItemNotFound { return CommandMappings() }
        guard status == errSecSuccess, let data = out as? Data else { throw CredentialError.keychain(status) }
        return try CommandMappings.decoded(data)
    }

    public func save(_ mappings: CommandMappings) throws {
        let data = try JSON.canonical(mappings.document)
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

/// Changes the mappings only after the owner authenticates (the B2
/// PolicyEditor pattern); a change that does not validate is not saved.
public final class CommandMappingsEditor: @unchecked Sendable {
    private let store: CommandMappingsStore
    private let authentication: OwnerAuthentication
    private let home: String
    private let lock = NSLock()
    /// Called after a change was saved (the runner rebuilds its policy and
    /// executor and tells the server what changed).
    public var onChange: ((CommandMappings) -> Void)?

    public init(store: CommandMappingsStore, authentication: OwnerAuthentication, home: String = NSHomeDirectory()) {
        self.store = store
        self.authentication = authentication
        self.home = home
    }

    public func current() throws -> CommandMappings { try store.load() }

    @discardableResult
    public func change(reason: String, _ change: (inout CommandMappings) throws -> Void) async throws -> CommandMappings {
        guard await authentication.authenticate(reason: reason) else { throw PolicyChangeError.notAuthenticated }
        let mappings = try apply(change)
        onChange?(mappings)
        return mappings
    }

    private func apply(_ change: (inout CommandMappings) throws -> Void) throws -> CommandMappings {
        lock.lock()
        defer { lock.unlock() }
        var mappings = try store.load()
        try change(&mappings)
        _ = try mappings.actionPolicy(home: home)  // refuses before saving
        try store.save(mappings)
        return mappings
    }
}
