// Credential rotation (Phase 9 brief D1, M1): `POST
// /api/v1/auth/nodes/credential/rotate`, authenticated by the current secret
// over the pinned connection. The new secret is persisted **before** the
// rotation is reported done (ADR-013 residual): a crash after the server
// issued it but before the Keychain write would strand the node once the
// previous secret's grace period ends.

import Foundation

public enum Rotation {
    /// Rotate the secret. On success the new credential is already saved in
    /// `store` when this returns.
    public static func rotate(_ current: NodeCredential, store: CredentialStore, now: Int64) async throws
        -> NodeCredential
    {
        let url = try HTTP.endpoint(current.serverURL, "/api/v1/auth/nodes/credential/rotate")
        let (status, response, _) = try await HTTP.post(
            url, body: [:], bearer: current.secret, pin: current.tlsPinSHA256)
        guard status == 200 else { throw NodeHTTPError.status(status, code: response?["code"]?.stringValue) }
        guard let response, response["node_id"]?.stringValue == current.nodeID,
            let secret = response["node_secret"]?.stringValue, HTTP.isNodeSecret(secret), secret != current.secret
        else { throw NodeHTTPError.badResponse }
        var next = current
        next.secret = secret
        next.secretIssuedAt = now
        if let days = response["rotate_after_days"]?.intValue, (1...365).contains(days) { next.rotateAfterDays = days }
        try store.save(next)  // persisted before the rotation counts as done
        return next
    }
}
