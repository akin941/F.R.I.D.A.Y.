// Protocol v1 constants, the frozen message registry and frame validation
// (Phase 9 brief D5; mirrors backend/nodes/protocol/messages.py).
//
// The schemas and capability contracts are the vendored, byte-identical
// copies of backend/nodes (Generated/VendoredProtocol.swift). Every frame
// the node receives runs D5 checks 1-4 here; every frame it sends is
// validated against the same schemas before it leaves.

import Foundation

public enum NodeProtocol {
    public static let version: Int64 = 1
    public static let subprotocol = "friday-node.v1"
    public static let gatewayPath = "/ws/node/v1"
    public static let maxFrameBytes = 256 * 1024
    public static let maxResultBytes = 64 * 1024
    public static let heartbeatSeconds: Int64 = 15
    public static let clockSkewSeconds: Int64 = 5
    public static let maxReadInvokeTTLSeconds: Int64 = 30
    public static let nonceCacheSize = 10_000

    public static let nodeToServer: Set<String> = ["hello", "heartbeat", "result", "event"]
    /// Exactly the five server -> node types: there is no policy-mutation
    /// message type (ADR-013 decision 5).
    public static let serverToNode: Set<String> = ["hello.ack", "heartbeat.ack", "invoke", "event.ack", "bye"]

    public static let refusalCodes = [
        "too_large", "malformed", "unknown_type", "schema", "unknown_key", "bad_signature",
        "wrong_node", "not_yet_valid", "expired", "ttl_too_long", "replay", "nonce_cache_full",
        "unknown_capability", "class_not_allowed", "args_hash_mismatch", "args_schema", "not_granted",
    ]

    public static let byeReasons: Set<String> = [
        "revoked", "kill_switch", "credential_expired", "replaced", "protocol_error", "server_shutdown",
    ]
    /// `bye` reasons after which the node does not reconnect until the owner
    /// acts locally (brief D13 item 2).
    public static let noReconnectReasons: Set<String> = ["revoked", "kill_switch", "credential_expired"]

    public enum Direction { case nodeToServer, serverToNode }

    // MARK: Vendored files

    /// The bytes of one vendored file, by its path under backend/nodes.
    public static func vendoredFile(_ path: String) -> Data? {
        VendoredProtocol.files[path].flatMap { Data(base64Encoded: $0) }
    }

    public static var vendoredPaths: [String] { VendoredProtocol.files.keys.sorted() }

    private static let schemas: [String: JSONValue] = {
        var loaded: [String: JSONValue] = [:]
        for type in nodeToServer.union(serverToNode) {
            if let data = vendoredFile("protocol/schemas/v1/\(type).schema.json"),
                let schema = try? JSON.parse(data)
            {
                loaded[type] = schema
            }
        }
        return loaded
    }()

    public static func schema(for type: String) -> JSONValue? { schemas[type] }

    // MARK: Validation

    /// Validate one parsed message for `direction`. Throws `unknown_type`
    /// (D5 check 3) or `schema` (check 4).
    public static func validate(_ message: JSONValue, direction: Direction) throws {
        guard case .object = message else { throw ProtocolError("schema") }
        let allowed = direction == .nodeToServer ? nodeToServer : serverToNode
        guard let type = message["type"]?.stringValue, allowed.contains(type) else {
            throw ProtocolError("unknown_type")
        }
        guard let schema = schemas[type], Schema.isValid(message, against: schema) else {
            throw ProtocolError("schema")
        }
    }

    /// D5 checks 1-4 for one received frame: size (`too_large`), strict
    /// JSON (`malformed`), known type (`unknown_type`), schema (`schema`).
    public static func decodeFrame(_ raw: Data) throws -> JSONValue {
        guard raw.count <= maxFrameBytes else { throw ProtocolError("too_large") }
        let message: JSONValue
        do {
            message = try JSON.parse(raw)
        } catch {
            throw ProtocolError("malformed")
        }
        try validate(message, direction: .serverToNode)
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

/// One server-known read contract (backend/nodes/capabilities/v1/*.json).
public struct CapabilityContract: Equatable, Sendable {
    public let name: String
    public let version: Int64
    public let capabilityClass: String
    public let argsSchema: JSONValue
    public let resultSchema: JSONValue
    public let maxResultBytes: Int64

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

    /// Every vendored v1 read contract, sorted by name.
    public static let all: [CapabilityContract] = {
        NodeProtocol.vendoredPaths
            .filter { $0.hasPrefix("capabilities/v1/") }
            .compactMap { path -> CapabilityContract? in
                guard let data = NodeProtocol.vendoredFile(path), let doc = try? JSON.parse(data),
                    let name = doc["name"]?.stringValue, let version = doc["version"]?.intValue,
                    let cls = doc["class"]?.stringValue, let args = doc["args_schema"],
                    let result = doc["result_schema"]
                else { return nil }
                return CapabilityContract(
                    name: name, version: version, capabilityClass: cls, argsSchema: args,
                    resultSchema: result,
                    maxResultBytes: doc["max_result_bytes"]?.intValue ?? Int64(NodeProtocol.maxResultBytes))
            }
            .sorted { $0.name < $1.name }
    }()
}

/// The node's own catalogue: its entries as sent in `hello`, and the map the
/// verifier checks invokes against (checks 12, 15, 16).
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

    /// The server's own v1 read contracts as the node's entries;
    /// `availability` maps a name to a non-`granted` value.
    public static func standard(availability: [String: String] = [:]) -> Catalogue {
        Catalogue(entries: CapabilityContract.all.map {
            $0.catalogueEntry(availability: availability[$0.name] ?? "granted")
        })
    }
}
