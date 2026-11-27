// The node-local action policy, read side (Phase 10 brief D4, D5 check 22;
// ADR-013 decision 5). A port of backend/nodes/reference/local_policy.py.
//
// Which repos and commands exist, which recipes are enabled, which bundle
// ids may be opened, and each command's network domains: all node-local.
// The server sends typed intents (`command_kind=test`, `recipe=...`,
// `bundle_id=...`); `resolve` maps one onto the owner's own mapping or
// refuses `not_configured`:
//
// - `repo_path` (`~/...` or absolute) is expanded against the node's home
//   and realpath-resolved; it must EQUAL a registered repo root's realpath,
//   so a symlink planted in a mapped directory cannot redirect it, and `..`
//   never reaches here (the schema refuses it);
// - `command_kind` must be mapped for that repo;
// - a recipe must be enabled locally (and a repo recipe's `repo_path` must
//   be a registered repo);
// - `bundle_id` must be on the local list.
//
// Nothing in the protocol reaches it: the type has no setter and the
// session only calls `resolve`. The owner edits it in the app's "İzinler"
// menu behind LocalAuthentication (runbook B6, `Policy/CommandMappings.swift`,
// which stores this document in the keychain); with nothing mapped every
// confirm- and gate-class intent is `not_configured`.

import Foundation

public struct ActionPolicyError: Error, Equatable, CustomStringConvertible {
    public let description: String
    init(_ description: String) { self.description = description }
}

/// D5 check 22: the intent has no local mapping. `reason` is for local logs
/// and tests only; the node answers the bare code `not_configured`.
public struct NotConfigured: Error, Equatable {
    public let reason: String
}

public struct CommandMapping: Equatable, Sendable {
    public static let kinds = ["test", "lint", "typecheck", "build", "format_check"]
    public static let modes = ["in_place", "worktree"]
    public static let defaultPath = "/usr/bin:/bin:/usr/sbin:/sbin"

    public let kind: String
    /// The exact argv: no shell, ever.
    public let argv: [String]
    public let path: String
    public let envNames: [String]
    public let networkDomains: [String]
    public let maxRuntimeSeconds: Int64
    public let mode: String

    public var networkEnabled: Bool { !networkDomains.isEmpty }

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

    /// The `local_policy.json` shape of one mapping.
    public var document: JSONValue {
        [
            "kind": .string(kind),
            "argv": .array(argv.map(JSONValue.string)),
            "path": .string(path),
            "env_names": .array(envNames.map(JSONValue.string)),
            "network_domains": .array(networkDomains.map(JSONValue.string)),
            "max_runtime_seconds": .int(maxRuntimeSeconds),
            "mode": .string(mode),
        ]
    }
}

public struct RepoMapping: Equatable, Sendable {
    public let name: String
    /// As written (`~/...` or absolute).
    public let root: String
    /// realpath at load.
    public let realRoot: String
    public let commands: [String: CommandMapping]
}

/// What check 22 resolved an intent to (never sent to the server).
public struct ResolvedIntent: Equatable, Sendable {
    public let capability: String
    public var repo: RepoMapping?
    public var command: CommandMapping?
    public var recipe: String?
    public var bundleID: String?
}

public struct ActionPolicy: Sendable {
    public let home: String
    public let repos: [RepoMapping]
    public let recipesEnabled: Set<String>
    public let bundleIDs: Set<String>
    /// The document it was loaded from (the fingerprint covers it).
    public let document: JSONValue

    /// No repo, no recipe, no bundle id: every action intent is
    /// `not_configured`.
    public static func empty(home: String) -> ActionPolicy {
        ActionPolicy(home: home, repos: [], recipesEnabled: [], bundleIDs: [], document: [:])
    }

    private init(home: String, repos: [RepoMapping], recipesEnabled: Set<String>, bundleIDs: Set<String>,
                 document: JSONValue) {
        self.home = home
        self.repos = repos
        self.recipesEnabled = recipesEnabled
        self.bundleIDs = bundleIDs
        self.document = document
    }

    /// sha256 of the canonical `{home, document}` (local_policy.py
    /// `fingerprint`): the conformance suite asserts no session changes it.
    public var fingerprint: String {
        (try? JSON.sha256Hex(["home": .string(home), "document": document])) ?? ""
    }

    // MARK: Loading (local_policy.py `from_document`)

    private static let secretName = try! NSRegularExpression(
        pattern: "token|secret|key|password|credential|auth", options: [.caseInsensitive])
    private static let domainPattern = "^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\\.)+[a-z]{2,63}$"
    private static let envNamePattern = "^[A-Z_][A-Z0-9_]{0,63}$"

    static func looksSecret(_ name: String) -> Bool {
        secretName.firstMatch(in: name, range: NSRange(name.startIndex..<name.endIndex, in: name)) != nil
    }

    public static func load(_ document: JSONValue, home: String) throws -> ActionPolicy {
        guard let body = document.objectValue,
            Set(body.keys).isSubset(of: ["repos", "recipes_enabled", "bundle_ids"])
        else { throw ActionPolicyError("unexpected policy keys") }
        var repos: [RepoMapping] = []
        var seen: Set<String> = []
        for repo in body["repos"]?.arrayValue ?? [] {
            guard let name = repo["name"]?.stringValue, !name.isEmpty, name.unicodeScalars.count <= 64 else {
                throw ActionPolicyError("a repo needs a short local name")
            }
            guard let fields = repo.objectValue, Set(fields.keys).isSubset(of: ["name", "root", "commands"]) else {
                throw ActionPolicyError("\(name): unexpected keys")
            }
            guard let root = repo["root"]?.stringValue, root.hasPrefix("/") || root.hasPrefix("~/") else {
                throw ActionPolicyError("\(name): root must be absolute or ~/")
            }
            let realRoot = ReaderPaths.realpathC(expand(root, home: home))
            guard !seen.contains(realRoot) else { throw ActionPolicyError("\(name): root mapped twice") }
            seen.insert(realRoot)
            var commands: [String: CommandMapping] = [:]
            for mapping in repo["commands"]?.arrayValue ?? [] {
                let command = try checkMapping(name, mapping)
                guard commands[command.kind] == nil else {
                    throw ActionPolicyError("\(name): command kind mapped twice")
                }
                commands[command.kind] = command
            }
            repos.append(RepoMapping(name: name, root: root, realRoot: realRoot, commands: commands))
        }
        let recipes = (body["recipes_enabled"]?.arrayValue ?? []).compactMap(\.stringValue)
        let bundles = (body["bundle_ids"]?.arrayValue ?? []).compactMap(\.stringValue)
        return ActionPolicy(
            home: home, repos: repos, recipesEnabled: Set(recipes), bundleIDs: Set(bundles), document: document)
    }

    private static func checkMapping(_ repo: String, _ body: JSONValue) throws -> CommandMapping {
        guard let kind = body["kind"]?.stringValue, CommandMapping.kinds.contains(kind) else {
            throw ActionPolicyError("\(repo): unknown command kind")
        }
        guard let parts = body["argv"]?.arrayValue, !parts.isEmpty else {
            throw ActionPolicyError("\(repo)/\(kind): argv must be a non-empty list of strings")
        }
        let argv = try parts.map { part -> String in
            guard let text = part.stringValue, !text.isEmpty, !text.unicodeScalars.contains("\u{0}") else {
                throw ActionPolicyError("\(repo)/\(kind): argv must be a non-empty list of strings")
            }
            return text
        }
        let envNames = try (body["env_names"]?.arrayValue ?? []).map { value -> String in
            guard let name = value.stringValue, Schema.fullMatch(name, envNamePattern) else {
                throw ActionPolicyError("\(repo)/\(kind): bad environment name")
            }
            guard !looksSecret(name) else {
                throw ActionPolicyError("\(repo)/\(kind): a secret-shaped environment name is refused")
            }
            return name
        }
        let domains = try (body["network_domains"]?.arrayValue ?? []).map { value -> String in
            guard let domain = value.stringValue, Schema.fullMatch(domain, domainPattern) else {
                throw ActionPolicyError("\(repo)/\(kind): bad network domain")
            }
            return domain
        }
        let runtime: Int64
        switch body["max_runtime_seconds"] {
        case nil: runtime = 900
        case .int(let value) where (1...900).contains(value): runtime = value
        default: throw ActionPolicyError("\(repo)/\(kind): max_runtime_seconds outside 1..900")
        }
        let mode = body["mode"].map { $0.stringValue ?? "" } ?? "in_place"
        guard CommandMapping.modes.contains(mode) else {
            throw ActionPolicyError("\(repo)/\(kind): unknown sandbox mode")
        }
        let path = body["path"].map { $0.stringValue ?? "" } ?? CommandMapping.defaultPath
        guard !path.isEmpty, !path.unicodeScalars.contains("\u{0}") else {
            throw ActionPolicyError("\(repo)/\(kind): bad PATH")
        }
        let allowed: Set<String> = ["kind", "argv", "path", "env_names", "network_domains", "max_runtime_seconds", "mode"]
        guard Set((body.objectValue ?? [:]).keys).isSubset(of: allowed) else {
            throw ActionPolicyError("\(repo)/\(kind): unexpected keys")
        }
        return CommandMapping(
            kind: kind, argv: argv, path: path, envNames: envNames, networkDomains: domains,
            maxRuntimeSeconds: runtime, mode: mode)
    }

    static func expand(_ path: String, home: String) -> String {
        if path == "~" { return home }
        if path.hasPrefix("~/") { return (home as NSString).appendingPathComponent(String(path.dropFirst(2))) }
        return path
    }

    // MARK: Check 22

    public func repo(forPath repoPath: JSONValue?) throws -> RepoMapping {
        guard let path = repoPath?.stringValue else { throw NotConfigured(reason: "repo_path is not a string") }
        let real = ReaderPaths.realpathC(Self.expand(path, home: home))
        guard let repo = repos.first(where: { $0.realRoot == real }) else {
            throw NotConfigured(reason: "repo not mapped")
        }
        return repo
    }

    /// D5 check 22 for already schema-valid `args`.
    public func resolve(capability: String, args: JSONValue) throws -> ResolvedIntent {
        switch capability {
        case ActionContracts.commandCapability:
            let repo = try repo(forPath: args["repo_path"])
            guard let kind = args["command_kind"]?.stringValue, let command = repo.commands[kind] else {
                throw NotConfigured(reason: "command kind not mapped")
            }
            return ResolvedIntent(capability: capability, repo: repo, command: command)
        case ActionContracts.recipeCapability:
            guard let recipe = args["recipe"]?.stringValue, recipesEnabled.contains(recipe) else {
                throw NotConfigured(reason: "recipe not enabled")
            }
            var resolved = ResolvedIntent(capability: capability, recipe: recipe)
            if let params = args["params"], params["repo_path"] != nil {
                resolved.repo = try repo(forPath: params["repo_path"])
            }
            return resolved
        case "apps.open", "apps.focus", "ax.press", "ax.type_text":
            guard let bundle = args["bundle_id"]?.stringValue, bundleIDs.contains(bundle) else {
                throw NotConfigured(reason: "bundle id not on the local list")
            }
            return ResolvedIntent(capability: capability, bundleID: bundle)
        default:
            throw NotConfigured(reason: "no local mapping for this capability")
        }
    }
}
