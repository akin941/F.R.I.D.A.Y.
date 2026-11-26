// Pairing (and the HTTPS helper rotation shares) (Phase 9 brief D1, D4, M1).
//
// - `Pairing.pair`: `POST /api/v1/nodes/pair` with a one-time node pairing
//   code. The connection must pass the system trust evaluation; the leaf's
//   SPKI pin is captured from it. The response is checked field by field
//   (node id, `jnd_` secret, Ed25519 keys whose ids match, gateway path,
//   protocol version) before anything is stored.
// - `Rotation.rotate`: `POST /api/v1/auth/nodes/credential/rotate`
//   authenticated by the current secret over the pinned connection. The new
//   secret is persisted **before** the call is reported done (ADR-013
//   residual): a crash after the server issued it but before the Keychain
//   write would otherwise strand the node once the grace period ends.
//
// Bodies are never logged.

import Foundation

public enum NodeHTTPError: Error, Equatable {
    case invalidServerURL
    case transport
    case tlsPinMissing
    case status(Int, code: String?)
    case badResponse
}

public struct PairingRequest: Sendable {
    public var serverURL: URL
    public var code: String
    public var nodeName: String

    public init(serverURL: URL, code: String, nodeName: String) {
        self.serverURL = serverURL
        self.code = code
        self.nodeName = nodeName
    }
}

enum HTTP {
    static func post(
        _ url: URL, body: JSONValue, bearer: String?, pin: Data?
    ) async throws -> (status: Int, body: JSONValue?, observedPin: Data?) {
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalAndRemoteCacheData, timeoutInterval: 20)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("friday-node/\(NodeInfo.version)", forHTTPHeaderField: "User-Agent")
        if let bearer { request.setValue("Bearer \(bearer)", forHTTPHeaderField: "Authorization") }
        request.httpBody = try JSON.canonical(body)
        let delegate = PinningDelegate(pin: pin)
        let configuration = URLSessionConfiguration.ephemeral
        configuration.httpCookieStorage = nil
        configuration.urlCache = nil
        let session = URLSession(configuration: configuration, delegate: delegate, delegateQueue: nil)
        defer { session.finishTasksAndInvalidate() }
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw NodeHTTPError.transport
        }
        guard let http = response as? HTTPURLResponse else { throw NodeHTTPError.transport }
        return (http.statusCode, try? JSON.parse(data), delegate.observedPin)
    }

    static func endpoint(_ base: URL, _ path: String) throws -> URL {
        guard base.scheme == "https", var components = URLComponents(url: base, resolvingAgainstBaseURL: false)
        else { throw NodeHTTPError.invalidServerURL }
        components.path = path
        components.query = nil
        components.fragment = nil
        guard let url = components.url else { throw NodeHTTPError.invalidServerURL }
        return url
    }

    static let uuid = try! NSRegularExpression(
        pattern: "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

    static func isUUID(_ text: String) -> Bool {
        uuid.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)) != nil
    }

    static func isNodeSecret(_ text: String) -> Bool {
        text.hasPrefix("jnd_") && text.count >= 24 && text.count <= 256
            && text.unicodeScalars.allSatisfy { $0.isASCII && $0.value > 0x20 && $0.value < 0x7F }
    }
}

public enum Pairing {
    /// Pair with the server; returns the credential to store. Nothing is
    /// stored here: the caller saves it (and only then shows success).
    public static func pair(_ request: PairingRequest, now: Int64) async throws -> NodeCredential {
        let url = try HTTP.endpoint(request.serverURL, "/api/v1/nodes/pair")
        let body: JSONValue = [
            "code": .string(request.code.trimmingCharacters(in: .whitespacesAndNewlines)),
            "node_name": .string(String(request.nodeName.prefix(100))),
            "protocol_versions": [.int(NodeProtocol.version)],
            "platform": "macos",
        ]
        let (status, response, observedPin) = try await HTTP.post(url, body: body, bearer: nil, pin: nil)
        guard status == 200 || status == 201 else {
            throw NodeHTTPError.status(status, code: response?["code"]?.stringValue)
        }
        guard let pin = observedPin else { throw NodeHTTPError.tlsPinMissing }
        return try credential(from: response, serverURL: request.serverURL, tlsPin: pin, now: now)
    }

    public static func credential(from response: JSONValue?, serverURL: URL, tlsPin: Data, now: Int64) throws
        -> NodeCredential
    {
        guard let response, let nodeID = response["node_id"]?.stringValue, HTTP.isUUID(nodeID),
            let secret = response["node_secret"]?.stringValue, HTTP.isNodeSecret(secret),
            response["protocol_version"] == .int(NodeProtocol.version),
            response["gateway_path"]?.stringValue == NodeProtocol.gatewayPath,
            let rotate = response["rotate_after_days"]?.intValue, (1...365).contains(rotate),
            let keys = response["server_signing_keys"]?.arrayValue, !keys.isEmpty, keys.count <= 8
        else { throw NodeHTTPError.badResponse }
        var pinned: [String: Data] = [:]
        for entry in keys {
            guard entry["alg"]?.stringValue == "Ed25519", let id = entry["key_id"]?.stringValue,
                let raw = entry["public_key_b64"]?.stringValue.flatMap(Signing.publicKey(base64:)),
                Signing.keyID(for: raw) == id
            else { throw NodeHTTPError.badResponse }
            pinned[id] = raw
        }
        return NodeCredential(
            nodeID: nodeID, secret: secret, serverURL: serverURL, gatewayPath: NodeProtocol.gatewayPath,
            pinnedKeys: pinned, tlsPinSHA256: tlsPin, rotateAfterDays: rotate, secretIssuedAt: now)
    }
}
