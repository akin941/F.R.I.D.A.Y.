// Protocol constants, the frozen message registry and frame validation, v1
// and v2 (Phase 9 brief D5; Phase 10 brief D1; mirrors
// backend/nodes/protocol/messages.py).
//
// The schemas and capability contracts are the vendored, byte-identical
// copies of backend/nodes (Generated/VendoredProtocol.swift). Every frame
// the node receives runs D5 checks 1-4 here; every frame it sends is
// validated against the same schemas before it leaves.
//
// v2 adds exactly two node -> server types (`confirmation`, `output`); the
// server -> node set is the same five types in both versions (D1: no
// server-side cancel, no policy-mutation type). A session negotiates its
// version once (the WebSocket subprotocol) and every frame carries it.

import Foundation

public enum NodeProtocol {
    public static let version: Int64 = 1
    public static let latestVersion: Int64 = 2
    public static let versions: [Int64] = [1, 2]
    public static let subprotocol = "friday-node.v1"
    public static let subprotocols: [Int64: String] = [1: "friday-node.v1", 2: "friday-node.v2"]
    /// What the node offers, highest first (D1): the gateway picks the
    /// highest version it supports.
    public static let offeredSubprotocols = ["friday-node.v2", "friday-node.v1"]
    public static let gatewayPath = "/ws/node/v1"
    public static let maxFrameBytes = 256 * 1024
    public static let maxResultBytes = 64 * 1024
    public static let heartbeatSeconds: Int64 = 15
    public static let clockSkewSeconds: Int64 = 5
    public static let maxReadInvokeTTLSeconds: Int64 = 30
    public static let nonceCacheSize = 10_000

    // v2 bounds (Phase 10 D1, D2, D5).
    public static let maxGrantTTLSeconds: Int64 = 120
    public static let maxConfirmInvokeTTLSeconds: Int64 = 120
    /// D5 check 10 in v2: the TTL ceiling depends on the envelope's class.
    public static let maxInvokeTTLSecondsV2: [String: Int64] = [
        "read": maxReadInvokeTTLSeconds, "confirm": maxConfirmInvokeTTLSeconds, "gate": maxGrantTTLSeconds,
    ]
    public static let confirmWindowSeconds: Int64 = 60
    public static let maxTailBytes = 4096
    public static let maxOutputChunkBytes = 8192
    public static let maxOutputSeq: Int64 = 65_535
    public static let outputStreams = ["stdout", "stderr"]

    public static let nodeToServer: Set<String> = ["hello", "heartbeat", "result", "event"]
    /// Exactly the five server -> node types: there is no policy-mutation
    /// message type (ADR-013 decision 5).
    public static let serverToNode: Set<String> = ["hello.ack", "heartbeat.ack", "invoke", "event.ack", "bye"]
    /// v2 adds exactly two node -> server types; the server -> node set is
    /// v1's, unchanged (D1).
    public static let nodeToServerV2: Set<String> = nodeToServer.union(["confirmation", "output"])
    public static let serverToNodeV2: Set<String> = serverToNode

    public static func types(_ direction: Direction, version: Int64) -> Set<String> {
        switch (direction, version) {
        case (.nodeToServer, 2): return nodeToServerV2
        case (.serverToNode, 2): return serverToNodeV2
        case (.nodeToServer, _): return nodeToServer
        case (.serverToNode, _): return serverToNode
        }
    }

    public static let refusalCodes = [
        "too_large", "malformed", "unknown_type", "schema", "unknown_key", "bad_signature",
        "wrong_node", "not_yet_valid", "expired", "ttl_too_long", "replay", "nonce_cache_full",
        "unknown_capability", "class_not_allowed", "args_hash_mismatch", "args_schema", "not_granted",
    ]

    /// The v2 refusal codes in D5 order (checks 1-12 as v1, then 13-24).
    public static let refusalCodesV2 = [
        "too_large", "malformed", "unknown_type", "schema", "unknown_key", "bad_signature",
        "wrong_node", "not_yet_valid", "expired", "ttl_too_long", "replay", "nonce_cache_full",
        "unknown_capability", "class_mismatch", "grant_unexpected", "grant_missing", "bad_grant_signature",
        "grant_mismatch", "args_hash_mismatch", "args_schema", "not_granted", "not_configured",
        "busy_confirming", "owner_denied", "confirm_timeout", "expired_after_confirm",
    ]

    public static let byeReasons: Set<String> = [
        "revoked", "kill_switch", "credential_expired", "replaced", "protocol_error", "server_shutdown",
    ]
    /// `bye` reasons after which the node does not reconnect until the owner
    /// acts locally (brief D13 item 2).
    public static let noReconnectReasons: Set<String> = ["revoked", "kill_switch", "credential_expired"]

    public enum Direction { case nodeToServer, serverToNode }

    /// The version a negotiated subprotocol names, or nil.
    public static func version(ofSubprotocol name: String?) -> Int64? {
        subprotocols.first { $0.value == name }?.key
    }

    // MARK: Vendored files

    /// The bytes of one vendored file, by its path under backend/nodes.
    public static func vendoredFile(_ path: String) -> Data? {
        VendoredProtocol.files[path].flatMap { Data(base64Encoded: $0) }
    }

    public static var vendoredPaths: [String] { VendoredProtocol.files.keys.sorted() }

    private static let schemas: [Int64: [String: JSONValue]] = {
        var loaded: [Int64: [String: JSONValue]] = [:]
        for version in versions {
            var byType: [String: JSONValue] = [:]
            let names = types(.nodeToServer, version: version).union(types(.serverToNode, version: version))
            // v2's one sub-schema: the grant object `invoke.grant` embeds.
            for type in version == 2 ? names.union(["grant"]) : names {
                if let data = vendoredFile("protocol/schemas/v\(version)/\(type).schema.json"),
                    let schema = try? JSON.parse(data)
                {
                    byType[type] = schema
                }
            }
            loaded[version] = byType
        }
        return loaded
    }()

    public static func schema(for type: String, version: Int64 = 1) -> JSONValue? { schemas[version]?[type] }

    // MARK: Validation

    /// Validate one parsed message for `direction` in protocol `version`.
    /// Throws `unknown_type` (D5 check 3) or `schema` (check 4).
    public static func validate(_ message: JSONValue, direction: Direction, version: Int64 = 1) throws {
        guard case .object = message else { throw ProtocolError("schema") }
        guard let type = message["type"]?.stringValue, types(direction, version: version).contains(type) else {
            throw ProtocolError("unknown_type")
        }
        guard let schema = schemas[version]?[type], Schema.isValid(message, against: schema) else {
            throw ProtocolError("schema")
        }
    }

    /// D5 checks 1-4 for one received frame: size (`too_large`), strict
    /// JSON (`malformed`), known type (`unknown_type`), schema (`schema`).
    public static func decodeFrame(_ raw: Data, version: Int64 = 1) throws -> JSONValue {
        guard raw.count <= maxFrameBytes else { throw ProtocolError("too_large") }
        let message: JSONValue
        do {
            message = try JSON.parse(raw)
        } catch {
            throw ProtocolError("malformed")
        }
        try validate(message, direction: .serverToNode, version: version)
        return message
    }

    /// `hello.catalogue_sha256`: sha256 of the canonical JSON of the
    /// catalogue array, in the order the node sends it.
    public static func catalogueSHA256(_ catalogue: [JSONValue]) throws -> String {
        try JSON.sha256Hex(.array(catalogue))
    }
}

public struct ProtocolError: Error, Equatable, CustomStringConvertible {
    public let code: String
    public init(_ code: String) { self.code = code }
    public var description: String { code }
}

// MARK: - Capability contracts and the node's catalogue

/// One recipe of `applescript.recipe.run` (backend/nodes/capabilities/v2/
/// recipes/*.json): its own params schema and confirm template. The
/// AppleScript text never leaves the node and is not part of the contract.
public struct RecipeContract: Equatable, Sendable {
    public let name: String
    public let version: Int64
    public let label: String
    public let paramsSchema: JSONValue
    public let confirmTemplate: String

    /// A repo recipe (its params carry `repo_path`) follows the repo's
    /// workspace (D3).
    public var isRepoRecipe: Bool { paramsSchema["properties"]?["repo_path"] != nil }

    /// The node's own recipe catalogue, by name.
    public static let all: [String: RecipeContract] = {
        var recipes: [String: RecipeContract] = [:]
        for path in NodeProtocol.vendoredPaths where path.hasPrefix("capabilities/v2/recipes/") {
            guard let data = NodeProtocol.vendoredFile(path), let doc = try? JSON.parse(data),
                let name = doc["name"]?.stringValue, let version = doc["version"]?.intValue,
                let label = doc["label"]?.stringValue, let params = doc["params_schema"],
                let template = doc["confirm_template"]?.stringValue
            else { continue }
            recipes[name] = RecipeContract(
                name: name, version: version, label: label, paramsSchema: params, confirmTemplate: template)
        }
        return recipes
    }()
}

/// One server-known contract: a v1 read (backend/nodes/capabilities/v1/) or
/// a v2 action (confirm or gate class, capabilities/v2/).
public struct CapabilityContract: Equatable, Sendable {
    public let name: String
    public let version: Int64
    public let capabilityClass: String
    public let argsSchema: JSONValue
    public let resultSchema: JSONValue
    public let maxResultBytes: Int64
    // Phase 10 action contracts; reads keep the defaults.
    public let label: String
    public let maxRuntimeSeconds: Int64?
    public let streamsOutput: Bool
    public let confirmTemplate: String?

    public init(
        name: String, version: Int64, capabilityClass: String, argsSchema: JSONValue, resultSchema: JSONValue,
        maxResultBytes: Int64, label: String = "", maxRuntimeSeconds: Int64? = nil, streamsOutput: Bool = false,
        confirmTemplate: String? = nil
    ) {
        self.name = name
        self.version = version
        self.capabilityClass = capabilityClass
        self.argsSchema = argsSchema
        self.resultSchema = resultSchema
        self.maxResultBytes = maxResultBytes
        self.label = label
        self.maxRuntimeSeconds = maxRuntimeSeconds
        self.streamsOutput = streamsOutput
        self.confirmTemplate = confirmTemplate
    }

    public var isAction: Bool { capabilityClass == "confirm" || capabilityClass == "gate" }

    /// The `hello` catalogue entry a conforming node advertises.
    public func catalogueEntry(availability: String) -> JSONValue {
        [
            "name": .string(name),
            "version": .int(version),
            "class": .string(capabilityClass),
            "args_schema": argsSchema,
            "result_schema": resultSchema,
            "availability": .string(availability),
        ]
    }

    /// The recipe valid `args` name (`applescript.recipe.run` only).
    public func recipe(for args: JSONValue) -> RecipeContract? {
        guard name == ActionContracts.recipeCapability, let recipe = args["recipe"]?.stringValue else { return nil }
        return RecipeContract.all[recipe]
    }

    private static func load(prefix: String) -> [CapabilityContract] {
        NodeProtocol.vendoredPaths
            .filter { $0.hasPrefix(prefix) && !$0.dropFirst(prefix.count).contains("/") }
            .compactMap { path -> CapabilityContract? in
                guard let data = NodeProtocol.vendoredFile(path), let doc = try? JSON.parse(data),
                    let name = doc["name"]?.stringValue, let version = doc["version"]?.intValue,
                    let cls = doc["class"]?.stringValue, let args = doc["args_schema"],
                    let result = doc["result_schema"]
                else { return nil }
                return CapabilityContract(
                    name: name, version: version, capabilityClass: cls, argsSchema: args,
                    resultSchema: result,
                    maxResultBytes: doc["max_result_bytes"]?.intValue ?? Int64(NodeProtocol.maxResultBytes),
                    label: doc["label"]?.stringValue ?? "", maxRuntimeSeconds: doc["max_runtime_seconds"]?.intValue,
                    streamsOutput: doc["streams_output"]?.boolValue ?? false,
                    confirmTemplate: doc["confirm_template"]?.stringValue)
            }
            .sorted { $0.name < $1.name }
    }

    /// Every vendored v1 read contract, sorted by name.
    public static let all: [CapabilityContract] = load(prefix: "capabilities/v1/")

    /// Every vendored v2 action contract (confirm and gate), sorted by name.
    public static let actions: [CapabilityContract] = load(prefix: "capabilities/v2/")

    /// A read or action contract by name.
    public static func named(_ name: String) -> CapabilityContract? {
        all.first { $0.name == name } ?? actions.first { $0.name == name }
    }
}

public enum ActionContracts {
    public static let recipeCapability = "applescript.recipe.run"
    public static let commandCapability = "repo.command.run"
    /// The protocol ceiling of a command's runtime (D4).
    public static let maxRuntimeSeconds: Int64 = 900
}

/// The node's own catalogue: its entries as sent in `hello`, and the map the
/// verifier checks invokes against (checks 12, 15, 16 in v1; 12-21 in v2).
public struct Catalogue: Sendable {
    public let entries: [JSONValue]
    public let byName: [String: JSONValue]

    public init(entries: [JSONValue]) {
        self.entries = entries
        var map: [String: JSONValue] = [:]
        for entry in entries {
            if let name = entry["name"]?.stringValue { map[name] = entry }
        }
        byName = map
    }

    /// The server's own contracts as the node's entries: the v1 reads, then
    /// (with `actions`) the v2 action contracts; `availability` maps a name
    /// to a non-`granted` value. A v1 `hello` carries the reads only.
    public static func standard(availability: [String: String] = [:], actions: Bool = false) -> Catalogue {
        let contracts = CapabilityContract.all + (actions ? CapabilityContract.actions : [])
        return Catalogue(entries: contracts.map {
            $0.catalogueEntry(availability: availability[$0.name] ?? "granted")
        })
    }
}
