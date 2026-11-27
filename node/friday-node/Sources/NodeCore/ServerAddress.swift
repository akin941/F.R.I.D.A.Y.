// Re-pointing the node at another server address (Phase 13 M1, runbook E1;
// ADR-017 decision 7; ADR-013 decision 5). The Swift twin of
// `friday-node-linux config set server-url` (`cli.cmd_set_server_url`,
// `credential.check_server_url`).
//
// - The address is the `serverURL` of the Keychain credential pairing
//   stored (Credential.swift). Every consumer reads it from there: the node
//   WebSocket (`NodeCredential.gatewayURL`), rotation, the B7 voice token
//   mint and the B8 selftest mint (both through
//   `NodeRunner.credentialForVoice()`). Nothing else holds an address.
// - Only the owner changes it, locally: "Sunucu adresini değiştir" in the
//   menu, after LocalAuthentication (`.deviceOwnerAuthentication`: Touch ID
//   or the login password). No server frame reaches this code: `NodeSession`
//   has no handle on the credential, and a `config.update`-shaped frame is
//   `unknown_type` (the policy-mutation conformance family).
// - Same order as Linux: the URL must be `https://host[:port]` (no path,
//   query, fragment or credentials; Python's port rules), then the new
//   server must pass the pins taken at pairing (a TLS handshake against the
//   stored SPKI pin, nothing sent: no HTTP request, never the Bearer
//   secret), and only then is the credential saved. The node id, the
//   secret, the gateway path, the signing-key pins and the TLS pin are all
//   **kept**. A refusal changes nothing: the node stays on the address it
//   had (Linux: "no change made").
// - A later server whose pin differs is refused by the TLS handshake before
//   the WebSocket request, so before `hello` (`PinningDelegate`); the runner
//   shows "Sunucu kimliği değişti — yeniden eşleştirin" and keeps retrying
//   the same address with backoff, exactly as the Linux daemon does; it
//   never falls back to an earlier address. The signing-key pins stay too,
//   so a server with other keys cannot get a frame verified.

import Foundation
import Network

public enum ServerAddressError: Error, Equatable {
    /// The node is not paired: there is no address to change.
    case notPaired
    /// The text is not `https://host[:port]`; the message says why.
    case invalid(String)
    /// The owner cancelled or failed LocalAuthentication.
    case notAuthenticated
    /// The new server's certificate does not match the pin.
    case identityChanged
    /// No TLS handshake with the new server (DNS, refused, timeout).
    case unreachable
    /// Saving the credential failed.
    case storage
}

public enum ServerAddress {
    /// The owner-facing refusal for a server whose pins differ.
    public static let identityChangedMessage = "Sunucu kimliği değişti — yeniden eşleştirin"

    /// `check_server_url`: an `https://host[:port]` URL without path, query,
    /// fragment or credentials, returned as `https://<netloc>` (the host as
    /// typed). Python's `urlsplit` rules: the scheme is case-insensitive; an
    /// empty `?` or `#` is ignored; the path may be empty or `/`; the port,
    /// when present, is ASCII digits in 0...65535 (an empty port after `:`
    /// is the default port); a bracketed host must be an IPv6 address.
    /// Stricter than Python only where Python would accept a URL nobody can
    /// mean: any `@` (Python passes an empty user, `https://@host`), spaces,
    /// control or non-ASCII characters, and characters outside the URL host
    /// grammar.
    public static func check(_ text: String) throws -> URL {
        let value = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard value.unicodeScalars.allSatisfy({ $0.isASCII && $0.value > 0x20 && $0.value != 0x7F }) else {
            throw ServerAddressError.invalid("Adres boşluk, denetim karakteri ya da ASCII dışı karakter içeremez.")
        }
        guard let colon = value.firstIndex(of: ":"), value[..<colon].lowercased() == "https" else {
            throw ServerAddressError.invalid("Adres https://<sunucu>[:<port>] biçiminde olmalı.")
        }
        let afterScheme = value[value.index(after: colon)...]
        guard afterScheme.hasPrefix("//") else {
            throw ServerAddressError.invalid("Adres https://<sunucu>[:<port>] biçiminde olmalı.")
        }
        let rest = afterScheme.dropFirst(2)
        let netlocEnd = rest.firstIndex(where: { "/?#".contains($0) }) ?? rest.endIndex
        let netloc = String(rest[..<netlocEnd])
        var tail = rest[netlocEnd...]
        var fragment = Substring("")
        if let hash = tail.firstIndex(of: "#") {
            fragment = tail[tail.index(after: hash)...]
            tail = tail[..<hash]
        }
        var query = Substring("")
        if let mark = tail.firstIndex(of: "?") {
            query = tail[tail.index(after: mark)...]
            tail = tail[..<mark]
        }
        let path = tail
        if netloc.contains("@") || !query.isEmpty || !fragment.isEmpty {
            throw ServerAddressError.invalid("Adreste kullanıcı bilgisi, sorgu ya da parça (#) olamaz.")
        }
        guard path.isEmpty || path == "/" else {
            throw ServerAddressError.invalid("Adreste yol olamaz (yalnızca https://<sunucu>[:<port>]).")
        }
        let (host, port) = try hostAndPort(netloc)
        guard !host.isEmpty else {
            throw ServerAddressError.invalid("Adres https://<sunucu>[:<port>] biçiminde olmalı.")
        }
        guard let url = URL(string: "https://" + netloc), url.host != nil,
            url.port == port || (port == nil && url.port == nil)
        else { throw ServerAddressError.invalid("Adres çözümlenemedi.") }
        return url
    }

    /// Python's `_hostinfo` and `port` over the netloc (no `@` left).
    private static func hostAndPort(_ netloc: String) throws -> (host: String, port: Int?) {
        let host: String
        var portText: Substring?
        if netloc.hasPrefix("[") {
            guard let close = netloc.firstIndex(of: "]") else {
                throw ServerAddressError.invalid("IPv6 adresi köşeli parantezle kapatılmalı.")
            }
            host = String(netloc[netloc.index(after: netloc.startIndex)..<close])
            let after = netloc[netloc.index(after: close)...]
            if !after.isEmpty {
                guard after.hasPrefix(":") else { throw ServerAddressError.invalid("Port geçersiz.") }
                portText = after.dropFirst()
            }
            var address = in6_addr()
            guard !host.contains("%"), inet_pton(AF_INET6, host, &address) == 1 else {
                throw ServerAddressError.invalid("Köşeli parantez içinde yalnızca bir IPv6 adresi olabilir.")
            }
        } else {
            guard !netloc.contains("]") else {
                throw ServerAddressError.invalid("IPv6 adresi köşeli parantezle açılmalı.")
            }
            if let colon = netloc.firstIndex(of: ":") {
                host = String(netloc[..<colon])
                portText = netloc[netloc.index(after: colon)...]
            } else {
                host = netloc
            }
            let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~")
            guard host.unicodeScalars.allSatisfy(allowed.contains) else {
                throw ServerAddressError.invalid("Sunucu adı geçersiz karakter içeriyor.")
            }
        }
        guard let portText, !portText.isEmpty else { return (host, nil) }
        guard portText.utf8.allSatisfy({ $0 >= 0x30 && $0 <= 0x39 }), let port = Int(portText), port <= 65_535
        else { throw ServerAddressError.invalid("Port 0-65535 arasında bir sayı olmalı.") }
        return (host, port)
    }

    /// The host the menu shows ("Sunucu: friday-home.local"), from the
    /// stored credential only.
    public static func displayHost(of credential: NodeCredential?) -> String? {
        guard let url = credential?.serverURL, let host = url.host else { return nil }
        if let port = url.port, port != 443 { return host.contains(":") ? "[\(host)]:\(port)" : "\(host):\(port)" }
        return host
    }
}

// MARK: - The pin probe (Linux: `credential.pins.connect(host, port)`)

public enum PinProbeResult: Equatable, Sendable {
    case match
    case mismatch
    case unreachable
}

public protocol PinProbe: Sendable {
    /// A TLS handshake with `host:port` that is accepted only if the leaf
    /// SPKI hashes to `pin`; nothing is sent after it.
    func probe(host: String, port: Int, pin: Data) async -> PinProbeResult
}

/// Network.framework: TCP + TLS with a verify block that compares the leaf
/// pin (no CA evaluation, as on every pinned connection), cancelled as soon
/// as the handshake completes. No application byte is ever written.
public struct TLSPinProbe: PinProbe {
    public var timeoutSeconds: Double

    public init(timeoutSeconds: Double = 10) {
        self.timeoutSeconds = timeoutSeconds
    }

    public func probe(host: String, port: Int, pin: Data) async -> PinProbeResult {
        guard let nwPort = NWEndpoint.Port(rawValue: UInt16(clamping: port)), (0...65_535).contains(port) else {
            return .unreachable
        }
        let queue = DispatchQueue(label: "friday-node.pin-probe")
        let state = ProbeState()
        let tls = NWProtocolTLS.Options()
        sec_protocol_options_set_verify_block(
            tls.securityProtocolOptions,
            { _, trust, complete in
                let leaf = TLSPin.leafPin(sec_trust_copy_ref(trust).takeRetainedValue())
                let ok = leaf == pin
                if !ok { state.markMismatch() }
                complete(ok)
            }, queue)
        let parameters = NWParameters(tls: tls, tcp: NWProtocolTCP.Options())
        let connection = NWConnection(host: NWEndpoint.Host(host), port: nwPort, using: parameters)
        return await withCheckedContinuation { continuation in
            state.continuation = continuation
            connection.stateUpdateHandler = { update in
                switch update {
                case .ready:
                    state.finish(.match)
                    connection.cancel()
                case .failed, .waiting:
                    state.finish(state.mismatch ? .mismatch : .unreachable)
                    connection.cancel()
                case .cancelled:
                    state.finish(state.mismatch ? .mismatch : .unreachable)
                default:
                    break
                }
            }
            queue.asyncAfter(deadline: .now() + timeoutSeconds) {
                state.finish(state.mismatch ? .mismatch : .unreachable)
                connection.cancel()
            }
            connection.start(queue: queue)
        }
    }

    /// Touched on the probe's queue only.
    private final class ProbeState: @unchecked Sendable {
        var continuation: CheckedContinuation<PinProbeResult, Never>?
        private(set) var mismatch = false
        func markMismatch() { mismatch = true }
        func finish(_ result: PinProbeResult) {
            continuation?.resume(returning: result)
            continuation = nil
        }
    }
}

// MARK: - The editor

/// Where the address lives: the runner (which saves it on its own queue,
/// so a rotation in flight cannot write the old address back) or, in
/// tests, a plain store.
public protocol ServerAddressTarget: AnyObject {
    /// The stored credential now (local only).
    func currentCredential() -> NodeCredential?
    /// Save the credential with `serverURL` replaced, everything else kept,
    /// and reconnect to it; returns what was saved.
    func repoint(to serverURL: URL, nodeID: String) throws -> NodeCredential
}

public final class ServerAddressEditor: @unchecked Sendable {
    public static let reason = "Friday Node'un sunucu adresini değiştir"

    private let target: ServerAddressTarget
    private let authentication: OwnerAuthentication
    private let probe: PinProbe

    public init(target: ServerAddressTarget, authentication: OwnerAuthentication, probe: PinProbe = TLSPinProbe()) {
        self.target = target
        self.authentication = authentication
        self.probe = probe
    }

    /// The address shown in the editor (local credential only).
    public func current() -> URL? { target.currentCredential()?.serverURL }

    /// Re-point the node (see the file header for the order). Throws, and
    /// changes nothing, on every refusal.
    @discardableResult
    public func change(to text: String) async throws -> NodeCredential {
        guard let credential = target.currentCredential() else { throw ServerAddressError.notPaired }
        let url = try ServerAddress.check(text)
        guard await authentication.authenticate(reason: Self.reason) else { throw ServerAddressError.notAuthenticated }
        guard let host = url.host else { throw ServerAddressError.invalid("Adres çözümlenemedi.") }
        switch await probe.probe(host: host, port: url.port ?? 443, pin: credential.tlsPinSHA256) {
        case .match: break
        case .mismatch: throw ServerAddressError.identityChanged
        case .unreachable: throw ServerAddressError.unreachable
        }
        do {
            return try target.repoint(to: url, nodeID: credential.nodeID)
        } catch let error as ServerAddressError {
            throw error
        } catch {
            throw ServerAddressError.storage
        }
    }
}

/// A store-backed target (tests; no runner).
public final class StoreServerAddressTarget: ServerAddressTarget {
    private let store: CredentialStore

    public init(store: CredentialStore) {
        self.store = store
    }

    public func currentCredential() -> NodeCredential? { try? store.load() }

    public func repoint(to serverURL: URL, nodeID: String) throws -> NodeCredential {
        guard var next = try store.load(), next.nodeID == nodeID else { throw ServerAddressError.notPaired }
        next.serverURL = serverURL
        try store.save(next)
        return next
    }
}
