// Changing the local policy (Phase 9 brief M2, D13 item 5): only after the
// owner passes LocalAuthentication (`.deviceOwnerAuthentication`: Touch ID,
// or the login password), and the change reaches the server only as the
// node's own availability: a revoked grant is `not_granted` at once and
// the node sends `grant.availability_changed` with items exactly
// `{name, version, availability}`.

import Foundation
import LocalAuthentication

public protocol OwnerAuthentication: Sendable {
    /// True when the owner proved presence for `reason`.
    func authenticate(reason: String) async -> Bool
}

/// Touch ID, or the login password.
public struct DeviceOwnerAuthentication: OwnerAuthentication {
    public init() {}

    public func authenticate(reason: String) async -> Bool {
        let context = LAContext()
        var error: NSError?
        guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &error) else { return false }
        return (try? await context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: reason)) ?? false
    }
}

public enum PolicyChangeError: Error, Equatable {
    case notAuthenticated
}

public final class PolicyEditor: @unchecked Sendable {
    private let store: LocalPolicyStore
    private let authentication: OwnerAuthentication
    /// Serialises load-modify-save, so two changes never lose one another.
    private let lock = NSLock()
    /// Called after a change was saved (the runner rebuilds its readers and
    /// tells the server what changed).
    public var onChange: ((LocalPolicy) -> Void)?

    public init(store: LocalPolicyStore, authentication: OwnerAuthentication) {
        self.store = store
        self.authentication = authentication
    }

    public func current() throws -> LocalPolicy { try store.load() }

    /// Apply `change` after the owner authenticates; nothing changes otherwise.
    @discardableResult
    public func change(reason: String, _ change: (inout LocalPolicy) -> Void) async throws -> LocalPolicy {
        guard await authentication.authenticate(reason: reason) else { throw PolicyChangeError.notAuthenticated }
        let policy = try apply(change)
        onChange?(policy)
        return policy
    }

    private func apply(_ change: (inout LocalPolicy) -> Void) throws -> LocalPolicy {
        lock.lock()
        defer { lock.unlock() }
        var policy = try store.load()
        change(&policy)
        try store.save(policy)
        return policy
    }
}

public enum AvailabilityChange {
    /// The `grant.availability_changed` batch items between two catalogues:
    /// `{name, version, availability}` for every entry whose availability
    /// differs, in catalogue order.
    public static func items(from old: Catalogue, to new: Catalogue) -> [JSONValue] {
        new.entries.compactMap { entry -> JSONValue? in
            guard let name = entry["name"]?.stringValue, let version = entry["version"],
                let availability = entry["availability"],
                old.byName[name]?["availability"] != availability
            else { return nil }
            return ["name": .string(name), "version": version, "availability": availability]
        }
    }
}
