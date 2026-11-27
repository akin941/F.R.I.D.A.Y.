// TLS public-key pinning (ADR-013 decision 2; Phase 9 brief D4).
//
// The pin is sha256 of the server leaf certificate's SubjectPublicKeyInfo
// in DER, the same value `openssl x509 -pubkey | openssl pkey -pubin
// -outform der | openssl dgst -sha256` gives (docs/runbooks/friday-node.md
// "Pinning the served certificate"), so the owner can compare it with
// NODE_TLS_PIN_SHA256. Security.framework exports a key's raw form only;
// the SPKI is rebuilt by prefixing the fixed ASN.1 header of its algorithm.
//
// The pin is captured at pairing from the connection the node paired over
// (which must pass the system trust evaluation) and enforced on every later
// connection in place of CA validation. A key type without a known header
// is refused: fail closed.

import CryptoKit
import Foundation
import Security

public enum TLSPin {
    /// Hex (ASCII digits only) to bytes; nil on anything else.
    private static func hexBytes(_ text: String) -> Data? {
        let bytes = Array(text.utf8)
        guard bytes.count % 2 == 0 else { return nil }
        var out = Data()
        for index in stride(from: 0, to: bytes.count, by: 2) {
            guard let pair = String(bytes: bytes[index...index + 1], encoding: .ascii),
                let byte = UInt8(pair, radix: 16)
            else { return nil }
            out.append(byte)
        }
        return out
    }

    private static func hex(_ text: String) -> Data { hexBytes(text)! }  // constant headers only

    private static let ecP256Header = hex("3059301306072a8648ce3d020106082a8648ce3d030107034200")
    private static let ecP384Header = hex("3076301006072a8648ce3d020106052b81040022036200")
    private static let rsa2048Header = hex("30820122300d06092a864886f70d01010105000382010f00")
    private static let rsa3072Header = hex("308201a2300d06092a864886f70d01010105000382018f00")
    private static let rsa4096Header = hex("30820222300d06092a864886f70d01010105000382020f00")

    /// The DER SubjectPublicKeyInfo of a certificate's public key.
    public static func spki(of certificate: SecCertificate) -> Data? {
        guard let key = SecCertificateCopyKey(certificate),
            let raw = SecKeyCopyExternalRepresentation(key, nil) as Data?,
            let attributes = SecKeyCopyAttributes(key) as? [String: Any],
            let type = attributes[kSecAttrKeyType as String] as? String,
            let bits = attributes[kSecAttrKeySizeInBits as String] as? Int
        else { return nil }
        let isEC = type == (kSecAttrKeyTypeECSECPrimeRandom as String)
        let isRSA = type == (kSecAttrKeyTypeRSA as String)
        let header: Data?
        switch bits {
        case 256 where isEC: header = ecP256Header
        case 384 where isEC: header = ecP384Header
        case 2048 where isRSA: header = rsa2048Header
        case 3072 where isRSA: header = rsa3072Header
        case 4096 where isRSA: header = rsa4096Header
        default: header = nil
        }
        return header.map { $0 + raw }
    }

    public static func sha256(of certificate: SecCertificate) -> Data? {
        spki(of: certificate).map { Data(SHA256.hash(data: $0)) }
    }

    /// The pin of a trust object's leaf certificate.
    public static func leafPin(_ trust: SecTrust) -> Data? {
        guard let chain = SecTrustCopyCertificateChain(trust) as? [SecCertificate], let leaf = chain.first else {
            return nil
        }
        return sha256(of: leaf)
    }

    /// Parse a pin as the server states it: base64 or hex, optionally with a
    /// `sha256/` prefix (NODE_TLS_PIN_SHA256's accepted forms).
    public static func parse(_ text: String) -> Data? {
        var value = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if value.lowercased().hasPrefix("sha256/") { value = String(value.dropFirst(7)) }
        if value.utf8.count == 64, let raw = hexBytes(value) { return raw }
        if let data = Data(base64Encoded: value), data.count == 32 { return data }
        return nil
    }
}

/// A URLSession delegate that accepts a server only when its leaf SPKI
/// matches the pin (and, with no pin, only when the system trusts it; then
/// it records the leaf pin, for pairing).
public final class PinningDelegate: NSObject, URLSessionDelegate, URLSessionTaskDelegate,
    @unchecked Sendable
{
    private let expectedPin: Data?
    private let lock = NSLock()
    private var _observedPin: Data?
    private var _refusedPin = false

    /// `pin == nil`: pairing mode (system trust required, pin recorded).
    public init(pin: Data?) {
        expectedPin = pin
    }

    /// True once a server's leaf did not match the expected pin (Phase 13
    /// M1: the runner shows "Sunucu kimliği değişti — yeniden eşleştirin").
    public var refusedPin: Bool {
        lock.lock()
        defer { lock.unlock() }
        return _refusedPin
    }

    /// The leaf pin of the last accepted server (pairing mode).
    public var observedPin: Data? {
        lock.lock()
        defer { lock.unlock() }
        return _observedPin
    }

    public func urlSession(
        _ session: URLSession, didReceive challenge: URLAuthenticationChallenge,
        completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        handle(challenge, completionHandler)
    }

    public func urlSession(
        _ session: URLSession, task: URLSessionTask, didReceive challenge: URLAuthenticationChallenge,
        completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        handle(challenge, completionHandler)
    }

    /// Never follow a redirect: the pin (and, for rotation, the bearer)
    /// belongs to the server the node paired with.
    public func urlSession(
        _ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void
    ) {
        completionHandler(nil)
    }

    private func handle(
        _ challenge: URLAuthenticationChallenge,
        _ completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
            let trust = challenge.protectionSpace.serverTrust, let leaf = TLSPin.leafPin(trust)
        else {
            completionHandler(.cancelAuthenticationChallenge, nil)
            return
        }
        if let expected = expectedPin {
            guard leaf == expected else {
                lock.lock()
                _refusedPin = true
                lock.unlock()
                completionHandler(.cancelAuthenticationChallenge, nil)
                return
            }
            completionHandler(.useCredential, URLCredential(trust: trust))
            return
        }
        var error: CFError?
        guard SecTrustEvaluateWithError(trust, &error) else {
            completionHandler(.cancelAuthenticationChallenge, nil)
            return
        }
        lock.lock()
        _observedPin = leaf
        lock.unlock()
        completionHandler(.useCredential, URLCredential(trust: trust))
    }
}
