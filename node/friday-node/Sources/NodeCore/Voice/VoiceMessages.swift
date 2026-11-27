// `friday-voice/v1` message sets, schemas and refusal codes (Phase 11 brief
// D6; Mac session M1). A port of
// node/friday-voice/friday_voice/protocol/messages.py.
//
// The two direction sets are frozen: adding a type is a protocol version
// bump. Nothing the child can send changes the host's mode, hotkey, mute,
// device, credential or node policy, opens a URL, runs anything or sends
// anything on the node WebSocket other than the `voice.state` event the
// host derives from `state` (ADR-013 decision 5).
//
// The schemas are byte copies of
// node/friday-voice/friday_voice/protocol/schemas/v1/<direction>/<type>.schema.json
// (Generated/VendoredVoiceProtocol.swift, written by
// scripts/vendor_protocol.py; `--check` in CI and VoiceProtocolTests fail on
// drift). Validation is the strict subset of Schema.swift: `pattern` is a
// full match, numbers are integers only, unknown properties are refused,
// and a refusal never echoes the offending value.

import Foundation

public struct VoiceProtocolError: Error, Equatable, CustomStringConvertible {
    public let code: String
    public init(_ code: String) {
        precondition(VoiceProtocol.errorCodes.contains(code), "unknown voice protocol error code")
        self.code = code
    }
    public var description: String { code }
}

public enum VoiceProtocol {
    public static let name = "friday-voice/v1"
    public static let sampleRate = 16_000

    public enum Direction: String, Sendable {
        case hostToChild = "host_to_child"
        case childToHost = "child_to_host"
    }

    public static let hostToChildTypes: Set<String> = [
        "hello", "credential", "audio", "playback", "mode", "mute", "ptt", "link", "shutdown",
    ]
    public static let childToHostTypes: Set<String> = [
        "ready", "state", "play", "stop_playback", "earcon", "release_mic", "log",
    ]

    public static func types(_ direction: Direction) -> Set<String> {
        direction == .hostToChild ? hostToChildTypes : childToHostTypes
    }

    /// Frames that carry a PCM payload after the header line.
    public static let audioTypes: [String: Direction] = ["audio": .hostToChild, "play": .childToHost]

    public static let modes = ["off", "push_to_talk", "always_on"]
    public static let states = [
        "off", "armed_ptt", "armed_wake", "listening", "thinking", "speaking", "muted", "storm_muted", "error",
    ]
    public static let releaseReasons = [
        "link_down", "muted", "mute_unreadable", "mode_off", "credential_missing", "credential_expired",
        "credential_refused", "protocol_error", "stdin_closed", "storm", "shutdown", "error",
    ]
    public static let earcons = ["wake", "end", "error"]

    // Byte limits (D6).
    public static let maxFrameBytes = 1024 * 1024
    public static let maxPCMBytes = 64 * 1024
    public static let maxHeaderBytes = 16 * 1024

    public static let framingErrors: Set<String> = [
        "frame_empty", "frame_too_large", "short_read", "header_too_large", "header_unterminated",
        "invalid_utf8", "invalid_json", "duplicate_key", "number_not_integer", "header_not_object",
        "payload_length_mismatch", "payload_unexpected", "pcm_odd_length", "pcm_too_large",
    ]
    public static let schemaErrors: Set<String> = ["unknown_type", "schema_invalid"]
    public static let sessionErrors: Set<String> = [
        "hello_expected", "ready_expected", "duplicate_hello", "duplicate_ready", "version_unsupported",
    ]
    public static var errorCodes: Set<String> { framingErrors.union(schemaErrors).union(sessionErrors) }

    /// Copied verbatim from backend/nodes/protocol/messages.py (as the
    /// Python package does); VoiceProtocolTests asserts no type or property
    /// name of the vendored schemas matches it (no reserved exception).
    public static let policyShaped = "(?i)policy|allowlist|grant(?!_id)|catalogue[._]set|config|exec_policy|settings"

    // MARK: Schemas

    /// The vendored schema file of one message type, by its path under
    /// friday_voice/protocol/.
    public static func vendoredFile(_ path: String) -> Data? {
        VendoredVoiceProtocol.files[path].flatMap { Data(base64Encoded: $0) }
    }

    public static var vendoredPaths: [String] { VendoredVoiceProtocol.files.keys.sorted() }

    /// sha256 (hex) of node/friday-voice/uv.lock at build time: the host
    /// refuses an installed child whose lock differs.
    public static var lockSHA256: String { VendoredVoiceProtocol.uvLockSHA256 }

    private static let schemas: [String: JSONValue] = {
        var loaded: [String: JSONValue] = [:]
        for direction in [Direction.hostToChild, .childToHost] {
            for type in types(direction) {
                if let data = vendoredFile("schemas/v1/\(direction.rawValue)/\(type).schema.json"),
                    let schema = try? JSON.parse(data)
                {
                    loaded[type] = schema
                }
            }
        }
        return loaded
    }()

    public static func schema(for type: String) -> JSONValue? { schemas[type] }

    /// Validate one decoded header for the direction it travelled in
    /// (messages.validate): unknown type, then the protocol version of
    /// `hello`/`ready`, then the schema. (`_has_float` has no counterpart:
    /// VoiceJSON never yields a float, it refuses one while parsing.)
    public static func validate(_ header: JSONValue, _ direction: Direction) throws {
        guard case .object = header else { throw VoiceProtocolError("header_not_object") }
        guard let type = header["type"]?.stringValue, types(direction).contains(type) else {
            throw VoiceProtocolError("unknown_type")
        }
        if type == "hello" || type == "ready", header["protocol"] != .string(name) {
            throw VoiceProtocolError("version_unsupported")
        }
        guard let schema = schema(for: type), Schema.isValid(header, against: schema) else {
            throw VoiceProtocolError("schema_invalid")
        }
    }

    public static func direction(of type: String) -> Direction? {
        if hostToChildTypes.contains(type) { return .hostToChild }
        if childToHostTypes.contains(type) { return .childToHost }
        return nil
    }
}
