// The node-side verification order v2 (Phase 10 brief D5), checks 1-22. A
// port of backend/nodes/protocol/verify.py `verify_invoke` (version 2) and
// backend/nodes/reference/verifier_v2.py `verify`, with the same refusal
// codes at the same check numbers:
//
// |  # | check                                                   | code |
// |----|---------------------------------------------------------|------|
// | 1-4| size, strict JSON, known type, v2 schema                | too_large / malformed / unknown_type / schema |
// |  5 | envelope key pinned                                     | unknown_key |
// |  6 | envelope signature, domain invoke/v2                    | bad_signature |
// |  7 | node id                                                 | wrong_node |
// |  8 | issued_at not in the future (5 s skew)                  | not_yet_valid |
// |  9 | expires_at in the future                                | expired |
// | 10 | TTL <= read 30 / confirm 120 / gate 120                 | ttl_too_long |
// | 11 | nonce unseen (burnt even if a later check refuses)       | replay / nonce_cache_full |
// | 12 | capability + version in my own catalogue                | unknown_capability |
// | 13 | envelope class equals my entry's class                  | class_mismatch |
// | 14 | gate => grant present; read/confirm => grant null       | grant_missing / grant_unexpected |
// | 15 | grant key pinned                                        | unknown_key |
// | 16 | grant signature, domain grant/v2                        | bad_grant_signature |
// | 17 | grant fields equal envelope fields                      | grant_mismatch |
// | 18 | grant TTL <= 120 s                                      | ttl_too_long |
// | 19 | args hash                                               | args_hash_mismatch |
// | 20 | args valid against MY args_schema (+ recipe params)     | args_schema |
// | 21 | availability granted                                    | not_granted |
// | 22 | local policy resolves (confirm and gate)                | not_configured |
//
// Checks 23 (the confirmation window, `busy_confirming`, `owner_denied`,
// `confirm_timeout`) and 24 (after the click: `expired_after_confirm`, or a
// lost socket: nothing) need the window and the session: NodeSession.
// The first failing check wins and nothing after it runs; a refusal never
// echoes the payload.

import Foundation

/// An invoke that passed checks 1-22 (or a non-invoke frame that passed 1-4).
public struct VerifiedFrame {
    public let message: JSONValue
    /// The local mapping check 22 resolved (confirm and gate only).
    public let resolved: ResolvedIntent?
}

public enum VerifierV2 {
    public static let localPolicyCheck = 22
    private static let frameErrorChecks = ["too_large": 1, "malformed": 2, "unknown_type": 3]

    /// Checks 1-22 on one server -> node frame of a v2 session. A
    /// non-`invoke` frame passes checks 1-4 and is returned untouched.
    public static func verify(
        _ raw: Data, state: VerifierState, policy: ActionPolicy, now: Int64
    ) throws -> VerifiedFrame {
        let message: JSONValue
        do {
            message = try NodeProtocol.decodeFrame(raw, version: 2)
        } catch let error as ProtocolError {
            throw Refusal(code: error.code, check: frameErrorChecks[error.code] ?? 4)
        }
        guard message["type"]?.stringValue == "invoke" else { return VerifiedFrame(message: message, resolved: nil) }
        try verifyInvoke(message, state: state, now: now)
        guard let capabilityClass = message["class"]?.stringValue, capabilityClass != "read" else {
            return VerifiedFrame(message: message, resolved: nil)
        }
        do {
            let resolved = try policy.resolve(
                capability: message["capability"]?.stringValue ?? "", args: message["args"] ?? .null)
            return VerifiedFrame(message: message, resolved: resolved)
        } catch is NotConfigured {
            throw Refusal(code: "not_configured", check: localPolicyCheck)
        }
    }

    /// Checks 5-21 on an `invoke` that already passed checks 1-4 (every
    /// field below exists with its v2 schema type).
    public static func verifyInvoke(_ envelope: JSONValue, state: VerifierState, now: Int64) throws {
        do {
            try Signing.verify(envelope, pinned: state.pinned, version: 2)  // 5, 6
        } catch let error as ProtocolError {
            throw Refusal(code: error.code, check: error.code == "unknown_key" ? 5 : 6)
        }
        guard envelope["node_id"]?.stringValue == state.nodeID else {
            throw Refusal(code: "wrong_node", check: 7)
        }
        let capabilityClass = envelope["class"]?.stringValue ?? ""
        let issuedAt = envelope["issued_at"]?.intValue ?? 0
        let expiresAt = envelope["expires_at"]?.intValue ?? 0
        if issuedAt > now + NodeProtocol.clockSkewSeconds { throw Refusal(code: "not_yet_valid", check: 8) }
        if expiresAt <= now { throw Refusal(code: "expired", check: 9) }
        let ttl = expiresAt - issuedAt
        let maxTTL = NodeProtocol.maxInvokeTTLSecondsV2[capabilityClass] ?? 0
        if ttl <= 0 || ttl > maxTTL { throw Refusal(code: "ttl_too_long", check: 10) }
        // Check 11 before 12-21 on purpose (the D5 order, as in v1): a
        // refused envelope still burns its nonce; a server retry uses a
        // fresh one.
        try state.nonceCache.checkAndAdd(envelope["nonce"]?.stringValue ?? "", expiresAt: expiresAt, now: now)
        guard let capability = envelope["capability"]?.stringValue,
            let entry = state.catalogue.byName[capability],
            entry["version"] == envelope["capability_version"]
        else { throw Refusal(code: "unknown_capability", check: 12) }

        guard envelope["class"] == entry["class"] else { throw Refusal(code: "class_mismatch", check: 13) }
        let grant = envelope["grant"] ?? .null
        if capabilityClass == "gate" {
            if grant == .null { throw Refusal(code: "grant_missing", check: 14) }
            try GrantVerifier.verifySignature(grant, pinned: state.pinned)  // 15, 16
            try GrantVerifier.checkBinding(grant, envelope: envelope)  // 17
            try GrantVerifier.checkTTL(grant)  // 18
        } else if grant != .null {
            throw Refusal(code: "grant_unexpected", check: 14)
        }

        let args = envelope["args"] ?? .null
        let argsHash = try? JSON.sha256Hex(args)
        guard argsHash != nil, argsHash == envelope["args_sha256"]?.stringValue else {
            throw Refusal(code: "args_hash_mismatch", check: 19)
        }
        guard Schema.isValid(args, against: entry["args_schema"] ?? .object([:])),
            recipeParamsValid(capability: capability, args: args)
        else { throw Refusal(code: "args_schema", check: 20) }
        guard entry["availability"]?.stringValue == "granted" else {
            throw Refusal(code: "not_granted", check: 21)
        }
    }

    /// Check 20's extra check for `applescript.recipe.run`
    /// (verifier_v2.py `recipe_params_check`): `params` valid against the
    /// named recipe's own `params_schema` from the node's recipe catalogue.
    /// Every other capability has none.
    static func recipeParamsValid(
        capability: String, args: JSONValue, recipes: [String: RecipeContract] = RecipeContract.all
    ) -> Bool {
        guard capability == ActionContracts.recipeCapability else { return true }
        guard let name = args["recipe"]?.stringValue, let recipe = recipes[name] else { return false }
        return Schema.isValid(args["params"] ?? .null, against: recipe.paramsSchema)
    }
}
