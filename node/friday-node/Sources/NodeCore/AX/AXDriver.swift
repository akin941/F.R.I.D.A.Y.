// The Accessibility seam of `ax.press` and `ax.type_text` (Phase 10 brief
// M2; runbook B6). `Press.swift` and `TypeText.swift` hold the rules and
// talk only to `AccessibilityDriver`; `SystemAccessibility` is the real
// one (ApplicationServices), and tests use a fake: no test ever drives the
// owner's real apps.

import AppKit
import ApplicationServices
import Carbon.HIToolbox
import Foundation

/// One Accessibility element (an `AXUIElement` for the system driver).
public final class AXHandle {
    public let element: AnyObject
    public init(_ element: AnyObject) { self.element = element }
}

public protocol AccessibilityDriver: AnyObject {
    /// The Accessibility TCC grant is present.
    var trusted: Bool { get }
    /// Secure event input is on somewhere (a password field has focus).
    var secureInputActive: Bool { get }
    /// The frontmost app's bundle id and pid.
    func frontmost() -> (bundleID: String, pid: pid_t)?
    /// The frontmost (focused) window of `pid`.
    func focusedWindow(pid: pid_t) -> AXHandle?
    /// The element that has keyboard focus in `pid`.
    func focusedElement(pid: pid_t) -> AXHandle?
    /// The window an element belongs to.
    func window(of element: AXHandle) -> AXHandle?
    func same(_ a: AXHandle, _ b: AXHandle) -> Bool
    func children(of element: AXHandle) -> [AXHandle]
    func role(of element: AXHandle) -> String?
    func subrole(of element: AXHandle) -> String?
    func title(of element: AXHandle) -> String?
    func press(_ element: AXHandle) -> Bool
    /// Insert `text` at the element's caret (its selected text).
    func insertText(_ text: String, into element: AXHandle) -> Bool
    /// Keystrokes carrying `text`, posted to `pid` only.
    func typeKeystrokes(_ text: String, pid: pid_t) -> Bool
    /// One Return key press posted to `pid` only.
    func pressReturn(pid: pid_t) -> Bool
}

public enum AXRefusal: String, Error, Equatable {
    case notTrusted = "not_trusted"
    case notFrontmost = "not_frontmost"
    case noWindow = "no_window"
    case secureField = "secure_field"
    case notFound = "not_found"
    case ambiguous = "ambiguous"
    case noFocus = "no_focus"
    case focusOutsideWindow = "focus_outside_window"
    case actionFailed = "action_failed"
    case badArgs = "bad_args"
}

enum AXRules {
    static let secureRole = "AXSecureTextField"

    /// The target app is frontmost and its frontmost window exists.
    static func frontWindow(_ driver: AccessibilityDriver, bundleID: String) throws -> (pid: pid_t, window: AXHandle) {
        guard driver.trusted else { throw AXRefusal.notTrusted }
        guard let front = driver.frontmost(), Scalars.equal(front.bundleID, bundleID) else {
            throw AXRefusal.notFrontmost
        }
        guard let window = driver.focusedWindow(pid: front.pid) else { throw AXRefusal.noWindow }
        return (front.pid, window)
    }

    /// No secure text field has focus (and secure input is off).
    static func noSecureFocus(_ driver: AccessibilityDriver, pid: pid_t) throws {
        if driver.secureInputActive { throw AXRefusal.secureField }
        if let focused = driver.focusedElement(pid: pid), isSecure(driver, focused) { throw AXRefusal.secureField }
    }

    static func isSecure(_ driver: AccessibilityDriver, _ element: AXHandle) -> Bool {
        driver.role(of: element) == secureRole || driver.subrole(of: element) == secureRole
    }
}

/// The real driver.
public final class SystemAccessibility: AccessibilityDriver {
    static let messagingTimeout: Float = 2

    public init() {}

    public var trusted: Bool { AXIsProcessTrusted() }

    public var secureInputActive: Bool { IsSecureEventInputEnabled() }

    public func frontmost() -> (bundleID: String, pid: pid_t)? {
        guard let app = NSWorkspace.shared.frontmostApplication, let bundle = app.bundleIdentifier else { return nil }
        return (bundle, app.processIdentifier)
    }

    private func application(_ pid: pid_t) -> AXUIElement {
        let app = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(app, Self.messagingTimeout)
        return app
    }

    private func element(_ handle: AXHandle) -> AXUIElement? {
        guard CFGetTypeID(handle.element) == AXUIElementGetTypeID() else { return nil }
        return (handle.element as! AXUIElement)  // swiftlint:disable:this force_cast
    }

    private func attribute(_ element: AXUIElement, _ name: String) -> AnyObject? {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success else { return nil }
        return value
    }

    private func handle(_ value: AnyObject?) -> AXHandle? {
        guard let value, CFGetTypeID(value) == AXUIElementGetTypeID() else { return nil }
        return AXHandle(value)
    }

    public func focusedWindow(pid: pid_t) -> AXHandle? {
        handle(attribute(application(pid), kAXFocusedWindowAttribute as String))
    }

    public func focusedElement(pid: pid_t) -> AXHandle? {
        handle(attribute(application(pid), kAXFocusedUIElementAttribute as String))
    }

    public func window(of element: AXHandle) -> AXHandle? {
        guard let element = self.element(element) else { return nil }
        return handle(attribute(element, kAXWindowAttribute as String))
    }

    public func same(_ a: AXHandle, _ b: AXHandle) -> Bool { CFEqual(a.element, b.element) }

    public func children(of element: AXHandle) -> [AXHandle] {
        guard let element = self.element(element),
            let children = attribute(element, kAXChildrenAttribute as String) as? [AnyObject]
        else { return [] }
        return children.compactMap(handle)
    }

    public func role(of element: AXHandle) -> String? {
        self.element(element).flatMap { attribute($0, kAXRoleAttribute as String) as? String }
    }

    public func subrole(of element: AXHandle) -> String? {
        self.element(element).flatMap { attribute($0, kAXSubroleAttribute as String) as? String }
    }

    public func title(of element: AXHandle) -> String? {
        self.element(element).flatMap { attribute($0, kAXTitleAttribute as String) as? String }
    }

    public func press(_ element: AXHandle) -> Bool {
        guard let element = self.element(element) else { return false }
        return AXUIElementPerformAction(element, kAXPressAction as CFString) == .success
    }

    public func insertText(_ text: String, into element: AXHandle) -> Bool {
        guard let element = self.element(element) else { return false }
        var settable: DarwinBoolean = false
        guard AXUIElementIsAttributeSettable(element, kAXSelectedTextAttribute as CFString, &settable) == .success,
            settable.boolValue
        else { return false }
        return AXUIElementSetAttributeValue(element, kAXSelectedTextAttribute as CFString, text as CFString) == .success
    }

    public func typeKeystrokes(_ text: String, pid: pid_t) -> Bool {
        let units = Array(text.utf16)
        var index = 0
        // CGEvent carries at most 20 UTF-16 units per event.
        while index < units.count {
            var end = min(index + 20, units.count)
            if end < units.count, UTF16.isLeadSurrogate(units[end - 1]) { end -= 1 }
            var chunk = Array(units[index..<end])
            guard let down = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: true),
                let up = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: false)
            else { return false }
            down.keyboardSetUnicodeString(stringLength: chunk.count, unicodeString: &chunk)
            up.keyboardSetUnicodeString(stringLength: chunk.count, unicodeString: &chunk)
            down.postToPid(pid)
            up.postToPid(pid)
            index = end
        }
        return true
    }

    public func pressReturn(pid: pid_t) -> Bool {
        guard let down = CGEvent(keyboardEventSource: nil, virtualKey: 36, keyDown: true),
            let up = CGEvent(keyboardEventSource: nil, virtualKey: 36, keyDown: false)
        else { return false }
        down.postToPid(pid)
        up.postToPid(pid)
        return true
    }
}
