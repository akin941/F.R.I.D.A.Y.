// Credential rotation (Phase 9 brief D1, M1): `POST
// /api/v1/auth/nodes/credential/rotate`, authenticated by the current secret
// over the pinned connection. The new secret is persisted **before** the
// rotation is reported done (ADR-013 residual): a crash after the server
// issued it but before the Keychain write would strand the node once the
// previous secret's grace period ends.

import Foundation

public enum Rotation {
    /// One HTTPS POST: `(url, body, bearer, pin) -> (status, body)`. Tests
    /// inject a fake; the app uses the pinned URLSession.
    public typealias Post = (URL, JSONValue, String, Data) async throws -> (status: Int, body: JSONValue?)

    public static let pinnedPost: Post = { url, body, bearer, pin in
        let (status, response, _) = try await HTTP.post(url, body: body, bearer: bearer, pin: pin)
        return (status, response)
    }

    /// Rotate the secret; on success the new credential is already saved in
    /// `store` when this returns.
    public static func rotate(_ current: NodeCredential, store: CredentialStore, now: Int64) async throws
        -> NodeCredential
    {
        try await rotate(current, now: now, persist: { try store.save($0) })
    }

    /// Rotate the secret. `persist` runs before this returns; if it throws,
    /// the rotation fails (the caller must not use a secret it did not keep).
    public static func rotate(
        _ current: NodeCredential, now: Int64, persist: (NodeCredential) throws -> Void,
        post: Post = pinnedPost
    ) async throws -> NodeCredential {
        let url = try HTTP.endpoint(current.serverURL, "/api/v1/auth/nodes/credential/rotate")
        let (status, response) = try await post(url, [:], current.secret, current.tlsPinSHA256)
        guard status == 200 else { throw NodeHTTPError.status(status, code: response?["code"]?.stringValue) }
        guard let response, response["node_id"]?.stringValue == current.nodeID,
            let secret = response["node_secret"]?.stringValue, HTTP.isNodeSecret(secret), secret != current.secret
        else { throw NodeHTTPError.badResponse }
        var next = current
        next.secret = secret
        next.secretIssuedAt = now
        if let days = response["rotate_after_days"]?.intValue, (1...365).contains(days) { next.rotateAfterDays = days }
        try persist(next)  // persisted before the rotation counts as done
        return next
    }
}
