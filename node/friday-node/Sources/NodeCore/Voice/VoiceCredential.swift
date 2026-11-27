// The voice child's credential (Phase 11 brief D3; Mac session M1 contract
// test 0; runbook B7).
//
// The host mints a short-lived `node_voice` token with the Keychain node
// secret, exactly as backend/accounts/tests/node_voice_helpers.py does:
//
//   POST /api/v1/auth/nodes/voice-token
//   Authorization: Bearer jnd_...           (the node secret; no body)
//   -> 201 {token, token_id, expires_at, previous_valid_until}
//      Cache-Control: no-store
//
// over the connection pinned at pairing (the leaf SPKI pin, ADR-013
// decision 2; never a redirect). It hands the token to the child in one
// `credential` frame {token, expires_at, server_url, ca_sha256} and keeps it
// in memory only: never written to disk, the Keychain, a log or argv.
// `ca_sha256` is the DER sha256 of the top certificate of the chain the
// server presented on that same pinned connection; the child refuses any
// TLS connection whose verified chain does not contain it (D6).
//
// Refresh: at `expires_at - refreshLeadSeconds` (75 s, so at least 60 s
// before expiry; the superseded token keeps the server's 30 s grace). A 401
// releases the mic and backs off (1 s -> 60 s) before the next mint; any
// other failure retries with the same backoff while the old token lasts.

import Foundation
import Security

public struct VoiceToken: Equatable, Sendable {
    public let token: String
    public let tokenID: String
    /// As the server wrote it (the `credential` frame carries it verbatim).
    public let expiresAt: String
    /// Unix seconds.
    public let expiresAtSeconds: Double
    public let previousValidUntil: String?
    public let serverURL: String
    public let caSHA256: String?

    public init(
        token: String, tokenID: String, expiresAt: String, expiresAtSeconds: Double, previousValidUntil: String?,
        serverURL: String, caSHA256: String?
    ) {
        self.token = token
        self.tokenID = tokenID
        self.expiresAt = expiresAt
        self.expiresAtSeconds = expiresAtSeconds
        self.previousValidUntil = previousValidUntil
        self.serverURL = serverURL
        self.caSHA256 = caSHA256
    }

    /// The `credential` frame's fields (no `type`).
    public var frame: [String: JSONValue] {
        [
            "token": .string(token), "expires_at": .string(expiresAt), "server_url": .string(serverURL),
            "ca_sha256": caSHA256.map { .string($0) } ?? .null,
        ]
    }
}

public enum VoiceCredentialError: Error, Equatable {
    /// 401: the node secret was refused (revoked, kill switch, unknown).
    case refused
    case status(Int)
    case badResponse
    case transport
}

public enum VoiceCredential {
    public static let path = "/api/v1/auth/nodes/voice-token"
    public static let refreshLeadSeconds = 75.0
    public static let minimumLeadSeconds = 60.0

    /// One HTTPS exchange: the request, the pin; the response, its body and
    /// the DER sha256 (hex) of the top certificate of the served chain.
    public typealias Transport = (URLRequest, Data) async throws -> (HTTPURLResponse, Data, String?)

    public static let pinnedTransport: Transport = { request, pin in
        let delegate = VoiceMintDelegate(pin: pin)
        let configuration = URLSessionConfiguration.ephemeral
        configuration.httpCookieStorage = nil
        configuration.urlCache = nil
        configuration.requestCachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        let session = URLSession(configuration: configuration, delegate: delegate, delegateQueue: nil)
        defer { session.finishTasksAndInvalidate() }
        do {
            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse else { throw VoiceCredentialError.transport }
            return (http, data, delegate.topCertificateSHA256)
        } catch let error as VoiceCredentialError {
            throw error
        } catch {
            throw VoiceCredentialError.transport
        }
    }

    /// `https://host[:port]`, the form the `credential` schema accepts.
    public static func origin(_ url: URL) -> String? {
        guard url.scheme == "https", let host = url.host, !host.isEmpty else { return nil }
        if let port = url.port { return "https://\(host):\(port)" }
        return "https://\(host)"
    }

    public static func request(for credential: NodeCredential) throws -> URLRequest {
        let url = try HTTP.endpoint(credential.serverURL, path)
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalAndRemoteCacheData, timeoutInterval: 20)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("friday-node/\(NodeInfo.version)", forHTTPHeaderField: "User-Agent")
        request.setValue("Bearer \(credential.secret)", forHTTPHeaderField: "Authorization")
        return request
    }

    /// Mint a token. Never logs or stores it.
    public static func mint(_ credential: NodeCredential, transport: Transport = pinnedTransport) async throws
        -> VoiceToken
    {
        guard let server = origin(credential.serverURL) else { throw VoiceCredentialError.badResponse }
        let (response, body, caSHA256) = try await transport(try request(for: credential), credential.tlsPinSHA256)
        if response.statusCode == 401 { throw VoiceCredentialError.refused }
        guard response.statusCode == 201 else { throw VoiceCredentialError.status(response.statusCode) }
        // The token must never sit in a cache.
        let cacheControl = (response.value(forHTTPHeaderField: "Cache-Control") ?? "").lowercased()
        guard cacheControl.split(separator: ",").contains(where: { $0.trimmingCharacters(in: .whitespaces) == "no-store" })
        else { throw VoiceCredentialError.badResponse }
        return try parse(body, serverURL: server, caSHA256: caSHA256)
    }

    public static func parse(_ body: Data, serverURL: String, caSHA256: String?) throws -> VoiceToken {
        guard let doc = try? JSON.parse(body), let fields = doc.objectValue,
            Set(fields.keys) == ["token", "token_id", "expires_at", "previous_valid_until"],
            let token = fields["token"]?.stringValue, let tokenID = fields["token_id"]?.stringValue,
            let expiresAt = fields["expires_at"]?.stringValue, let seconds = parseTime(expiresAt)
        else { throw VoiceCredentialError.badResponse }
        let previous = fields["previous_valid_until"]
        guard previous == .null || previous?.stringValue != nil else { throw VoiceCredentialError.badResponse }
        let minted = VoiceToken(
            token: token, tokenID: tokenID, expiresAt: expiresAt, expiresAtSeconds: seconds,
            previousValidUntil: previous?.stringValue, serverURL: serverURL, caSHA256: caSHA256)
        // What the child would refuse is refused here (token pattern, time
        // format, the URL form, the fingerprint).
        guard let schema = VoiceProtocol.schema(for: "credential") else { throw VoiceCredentialError.badResponse }
        var frame = minted.frame
        frame["type"] = "credential"
        guard Schema.isValid(.object(frame), against: schema) else { throw VoiceCredentialError.badResponse }
        return minted
    }

    /// RFC 3339 UTC with optional fraction (`Z` or `+00:00`), Unix seconds.
    public static func parseTime(_ text: String) -> Double? {
        guard Schema.fullMatch(
            text, "[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]{1,6})?(Z|\\+00:00)")
        else { return nil }
        var base = text
        if base.hasSuffix("+00:00") { base = String(base.dropLast(6)) } else { base = String(base.dropLast()) }
        var fraction = 0.0
        if let dot = base.firstIndex(of: ".") {
            fraction = Double("0" + base[dot...]) ?? 0
            base = String(base[..<dot])
        }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        guard let date = formatter.date(from: base + "Z") else { return nil }
        return date.timeIntervalSince1970 + fraction
    }

    /// When to mint the next token for one that expires at `expiresAt`
    /// (Unix seconds): `refreshLeadSeconds` before, never later than
    /// `minimumLeadSeconds` before, and not in the past.
    public static func refreshAt(expiresAt: Double, now: Double) -> Double {
        max(now, expiresAt - refreshLeadSeconds)
    }
}

/// Accepts the server only when its leaf SPKI matches the pin (the
/// PinningDelegate rule, no CA trust involved, no redirect) and records the
/// DER sha256 of the top certificate of the presented chain.
final class VoiceMintDelegate: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    private let pin: Data
    private let lock = NSLock()
    private var top: String?

    init(pin: Data) { self.pin = pin }

    var topCertificateSHA256: String? {
        lock.lock()
        defer { lock.unlock() }
        return top
    }

    func urlSession(
        _ session: URLSession, task: URLSessionTask, didReceive challenge: URLAuthenticationChallenge,
        completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
            let trust = challenge.protectionSpace.serverTrust, TLSPin.leafPin(trust) == pin,
            let chain = SecTrustCopyCertificateChain(trust) as? [SecCertificate], let last = chain.last
        else {
            completionHandler(.cancelAuthenticationChallenge, nil)
            return
        }
        let der = SecCertificateCopyData(last) as Data
        lock.lock()
        top = JSON.hexDigest(der)
        lock.unlock()
        completionHandler(.useCredential, URLCredential(trust: trust))
    }

    func urlSession(
        _ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void
    ) {
        completionHandler(nil)
    }
}
