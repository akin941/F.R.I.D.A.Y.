// `apps.open` and `apps.focus` (confirm class; Phase 10 brief D8, M2;
// runbook B6): NSWorkspace, for a bundle id on the node's local list only.
//
// - The bundle id must be on the local list (check 22 resolved it; checked
//   again here against the list the executor was built with).
// - `apps.open` asks LaunchServices for the app's URL and opens it
//   (activating it); an unknown bundle id is an error, nothing is searched
//   or downloaded.
// - `apps.focus` activates a running instance only; it never launches.
// No TCC grant is involved, so both are `granted` whenever the executor is.

import AppKit
import Foundation

public protocol WorkspaceDriver: AnyObject {
    func applicationURL(bundleID: String) -> URL?
    /// Open the app at `url`, activating it; true when it opened.
    func openApplication(at url: URL, timeout: Double) -> Bool
    /// Activate a running instance of `bundleID`; false when none runs.
    func activateRunning(bundleID: String) -> Bool
}

public enum OpenFocusError: String, Error, Equatable {
    case notOnList = "not_on_list"
    case unknownApp = "unknown_app"
    case notRunning = "not_running"
    case failed
}

public enum OpenFocus {
    public static func open(bundleID: String, allowed: Set<String>, driver: WorkspaceDriver) throws {
        guard allowed.contains(where: { Scalars.equal($0, bundleID) }) else { throw OpenFocusError.notOnList }
        guard let url = driver.applicationURL(bundleID: bundleID) else { throw OpenFocusError.unknownApp }
        guard driver.openApplication(at: url, timeout: 5) else { throw OpenFocusError.failed }
    }

    public static func focus(bundleID: String, allowed: Set<String>, driver: WorkspaceDriver) throws {
        guard allowed.contains(where: { Scalars.equal($0, bundleID) }) else { throw OpenFocusError.notOnList }
        guard driver.activateRunning(bundleID: bundleID) else { throw OpenFocusError.notRunning }
    }
}

/// The real driver.
public final class SystemWorkspace: WorkspaceDriver {
    public init() {}

    public func applicationURL(bundleID: String) -> URL? {
        NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundleID)
    }

    public func openApplication(at url: URL, timeout: Double) -> Bool {
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = true
        configuration.addsToRecentItems = false
        let done = DispatchSemaphore(value: 0)
        let result = OpenResult()
        NSWorkspace.shared.openApplication(at: url, configuration: configuration) { app, error in
            result.set(app != nil && error == nil)
            done.signal()
        }
        guard done.wait(timeout: .now() + timeout) == .success else { return false }
        return result.value
    }

    public func activateRunning(bundleID: String) -> Bool {
        guard let app = NSRunningApplication.runningApplications(withBundleIdentifier: bundleID).first else {
            return false
        }
        return app.activate()
    }
}

private final class OpenResult: @unchecked Sendable {
    private let lock = NSLock()
    private var ok = false
    func set(_ value: Bool) {
        lock.lock()
        ok = value
        lock.unlock()
    }
    var value: Bool {
        lock.lock()
        defer { lock.unlock() }
        return ok
    }
}
