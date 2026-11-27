// `apps.open` and `apps.focus` (confirm class; Phase 10 brief D8, M2;
// runbook B6): NSWorkspace, for a bundle id on the node's local list only.
//
// - The bundle id must be on the local list (check 22 resolved it; checked
//   again here against the list the executor was built with).
// - `apps.open` asks LaunchServices for the app's URL and opens it
//   (activating it) only when that URL's realpath lies under
//   /Applications, /System/Applications or /System/Library/CoreServices and
//   the bundle's code signature is valid (B6 review finding 5): a look-alike
//   `.app` written into a repo or ~/Downloads that claims a listed bundle id
//   is never launched. An unknown bundle id is an error; nothing is searched
//   or downloaded. Apps installed elsewhere (~/Applications) are refused.
// - `apps.focus` activates a running instance only (it never launches), and
//   only one whose bundle lies under the same roots.
// No TCC grant is involved, so both are `granted` whenever the executor is.

import AppKit
import Foundation
import Security

public protocol WorkspaceDriver: AnyObject {
    func applicationURL(bundleID: String) -> URL?
    /// The bundle URLs of the running instances of `bundleID`.
    func runningApplicationURLs(bundleID: String) -> [URL]
    /// The bundle at `url` has a valid code signature.
    func hasValidSignature(_ url: URL) -> Bool
    /// Open the app at `url`, activating it; true when it opened.
    func openApplication(at url: URL, timeout: Double) -> Bool
    /// Activate the running instance of `bundleID` at `url`; false when none.
    func activateRunning(bundleID: String, at url: URL) -> Bool
}

public enum OpenFocusError: String, Error, Equatable {
    case notOnList = "not_on_list"
    case unknownApp = "unknown_app"
    case notRunning = "not_running"
    case untrustedLocation = "untrusted_location"
    case badSignature = "bad_signature"
    case failed
}

public enum OpenFocus {
    public static let trustedRoots = ["/Applications", "/System/Applications", "/System/Library/CoreServices"]

    /// The realpath of `url` lies strictly under a trusted root (a symlink
    /// into a repo does not count: the realpath is checked).
    public static func isTrustedLocation(_ url: URL) -> Bool {
        let real = ReaderPaths.realpathC(url.path)
        return trustedRoots.contains { SandboxPolicy.under(real, $0) && !Scalars.equal(real, $0) }
    }

    public static func open(bundleID: String, allowed: Set<String>, driver: WorkspaceDriver) throws {
        guard allowed.contains(where: { Scalars.equal($0, bundleID) }) else { throw OpenFocusError.notOnList }
        guard let url = driver.applicationURL(bundleID: bundleID) else { throw OpenFocusError.unknownApp }
        guard isTrustedLocation(url) else { throw OpenFocusError.untrustedLocation }
        guard driver.hasValidSignature(url) else { throw OpenFocusError.badSignature }
        guard driver.openApplication(at: url, timeout: 5) else { throw OpenFocusError.failed }
    }

    public static func focus(bundleID: String, allowed: Set<String>, driver: WorkspaceDriver) throws {
        guard allowed.contains(where: { Scalars.equal($0, bundleID) }) else { throw OpenFocusError.notOnList }
        let running = driver.runningApplicationURLs(bundleID: bundleID)
        guard !running.isEmpty else { throw OpenFocusError.notRunning }
        guard let url = running.first(where: isTrustedLocation) else { throw OpenFocusError.untrustedLocation }
        guard driver.activateRunning(bundleID: bundleID, at: url) else { throw OpenFocusError.notRunning }
    }
}

/// The real driver.
public final class SystemWorkspace: WorkspaceDriver {
    public init() {}

    public func applicationURL(bundleID: String) -> URL? {
        NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundleID)
    }

    public func runningApplicationURLs(bundleID: String) -> [URL] {
        NSRunningApplication.runningApplications(withBundleIdentifier: bundleID).compactMap(\.bundleURL)
    }

    public func hasValidSignature(_ url: URL) -> Bool {
        var code: SecStaticCode?
        guard SecStaticCodeCreateWithPath(url as CFURL, [], &code) == errSecSuccess, let code else { return false }
        let flags = SecCSFlags(rawValue: kSecCSCheckAllArchitectures | kSecCSStrictValidate)
        return SecStaticCodeCheckValidity(code, flags, nil) == errSecSuccess
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

    public func activateRunning(bundleID: String, at url: URL) -> Bool {
        guard let app = NSRunningApplication.runningApplications(withBundleIdentifier: bundleID)
            .first(where: { $0.bundleURL.map { Scalars.equal($0.path, url.path) } ?? false })
        else { return false }
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
