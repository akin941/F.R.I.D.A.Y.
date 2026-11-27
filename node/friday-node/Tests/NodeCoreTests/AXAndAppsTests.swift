// AX press/type and open/focus (runbook B6; Phase 10 brief M2): the rules,
// against fakes only. No test drives the owner's real apps, asks the real
// TCC, or posts a real event.

import Foundation
import NodeCore
import XCTest

/// A TCC probe with scripted answers.
final class GrantedProbe: PermissionProbe {
    var trusted = true
    var automationAnswers: [String: AutomationStatus] = [:]
    private(set) var asked: [String] = []

    var accessibilityTrusted: Bool { trusted }

    func automation(_ bundleID: String, ask: Bool) -> AutomationStatus {
        asked.append(bundleID)
        return automationAnswers[bundleID] ?? .granted
    }
}

/// A fake Accessibility tree.
final class FakeAccessibility: AccessibilityDriver {
    final class Node {
        let role: String
        let subrole: String?
        let title: String?
        var children: [Node]
        weak var window: Node?
        init(_ role: String, title: String? = nil, subrole: String? = nil, children: [Node] = []) {
            self.role = role
            self.title = title
            self.subrole = subrole
            self.children = children
        }
    }

    var trusted = true
    var secureInputActive = false
    var front: (bundleID: String, pid: pid_t)? = ("com.apple.Notes", 42)
    /// The front after the first press/insert (an app switch mid-action).
    var frontAfterAction: (bundleID: String, pid: pid_t)??
    var window: Node?
    var focused: Node?
    var acceptsInsert = true
    private(set) var pressed: [String] = []
    private(set) var inserted: [String] = []
    private(set) var keystrokes: [String] = []
    private(set) var returns = 0
    private var acted = false

    func frontmost() -> (bundleID: String, pid: pid_t)? {
        if acted, let after = frontAfterAction { return after }
        return front
    }
    func focusedWindow(pid: pid_t) -> AXHandle? { window.map { AXHandle($0) } }
    func focusedElement(pid: pid_t) -> AXHandle? { focused.map { AXHandle($0) } }
    func window(of element: AXHandle) -> AXHandle? { (element.element as? Node)?.window.map { AXHandle($0) } }
    func same(_ a: AXHandle, _ b: AXHandle) -> Bool { a.element === b.element }
    func children(of element: AXHandle) -> [AXHandle] { ((element.element as? Node)?.children ?? []).map { AXHandle($0) } }
    func role(of element: AXHandle) -> String? { (element.element as? Node)?.role }
    func subrole(of element: AXHandle) -> String? { (element.element as? Node)?.subrole }
    func title(of element: AXHandle) -> String? { (element.element as? Node)?.title }
    func press(_ element: AXHandle) -> Bool {
        pressed.append((element.element as? Node)?.title ?? "")
        acted = true
        return true
    }
    /// Runs when an insert is attempted (a focus change between the check
    /// and the keystrokes).
    var onInsertAttempt: (() -> Void)?

    func insertText(_ text: String, into element: AXHandle) -> Bool {
        onInsertAttempt?()
        guard acceptsInsert else { return false }
        inserted.append(text)
        acted = true
        return true
    }
    func typeKeystrokes(_ text: String, pid: pid_t) -> Bool {
        keystrokes.append(text)
        acted = true
        return true
    }
    func pressReturn(pid: pid_t) -> Bool {
        returns += 1
        return true
    }
}

final class FakeWorkspace: WorkspaceDriver {
    var installed: [String: URL] = ["com.apple.Terminal": URL(fileURLWithPath: "/System/Applications/Utilities/Terminal.app")]
    /// Running instances per bundle id (their bundle URLs).
    var running: [String: [URL]] = [:]
    var signed = true
    private(set) var opened: [URL] = []
    private(set) var activated: [String] = []

    func applicationURL(bundleID: String) -> URL? { installed[bundleID] }
    func runningApplicationURLs(bundleID: String) -> [URL] { running[bundleID] ?? [] }
    func hasValidSignature(_ url: URL) -> Bool { signed }
    func openApplication(at url: URL, timeout: Double) -> Bool {
        opened.append(url)
        return true
    }
    func activateRunning(bundleID: String, at url: URL) -> Bool {
        guard running[bundleID]?.contains(url) ?? false else { return false }
        activated.append(url.path)
        return true
    }
}

final class AXTests: XCTestCase {
    private func notes(buttons: [String] = ["Kaydet", "Sil"], field: FakeAccessibility.Node? = nil) -> FakeAccessibility {
        let ax = FakeAccessibility()
        let text = field ?? FakeAccessibility.Node("AXTextArea")
        let group = FakeAccessibility.Node("AXGroup", children: buttons.map { FakeAccessibility.Node("AXButton", title: $0) })
        let window = FakeAccessibility.Node("AXWindow", title: "Notlar", children: [group, text])
        text.window = window
        ax.window = window
        ax.focused = text
        return ax
    }

    private func press(_ ax: FakeAccessibility, title: String, role: String = "button", bundle: String = "com.apple.Notes")
        -> AXRefusal?
    {
        do {
            try AXPress.perform(args: ["bundle_id": .string(bundle), "role": .string(role), "title": .string(title)], driver: ax)
            return nil
        } catch let refusal as AXRefusal {
            return refusal
        } catch {
            return .actionFailed
        }
    }

    func testPressFindsTheExactTitleInTheFrontmostWindowOnly() {
        let ax = notes()
        XCTAssertNil(press(ax, title: "Kaydet"))
        XCTAssertEqual(ax.pressed, ["Kaydet"])
        XCTAssertEqual(press(ax, title: "kaydet"), .notFound, "exact match, case included")
        XCTAssertEqual(press(ax, title: "Kayde"), .notFound, "no prefix match")
        XCTAssertEqual(press(ax, title: "Kaydet", role: "checkbox"), .notFound, "the role must match")
        XCTAssertEqual(press(notes(buttons: ["Tamam", "Tamam"]), title: "Tamam"), .ambiguous)
        // Composed vs decomposed: a different scalar sequence is a different title.
        XCTAssertEqual(press(notes(buttons: ["Sil\u{0065}\u{0301}"]), title: "Sil\u{00e9}"), .notFound)
    }

    func testPressRefusesWithoutTheGrantWhenNotFrontmostOrOverASecureField() {
        let untrusted = notes()
        untrusted.trusted = false
        XCTAssertEqual(press(untrusted, title: "Kaydet"), .notTrusted)
        XCTAssertEqual(press(notes(), title: "Kaydet", bundle: "com.apple.Safari"), .notFrontmost)
        let noWindow = notes()
        noWindow.window = nil
        XCTAssertEqual(press(noWindow, title: "Kaydet"), .noWindow)
        let secure = notes(field: FakeAccessibility.Node("AXTextField", subrole: "AXSecureTextField"))
        XCTAssertEqual(press(secure, title: "Kaydet"), .secureField)
        let secureInput = notes()
        secureInput.secureInputActive = true
        XCTAssertEqual(press(secureInput, title: "Kaydet"), .secureField)
        for ax in [untrusted, noWindow, secure, secureInput] { XCTAssertEqual(ax.pressed, []) }
    }

    private func type(_ ax: FakeAccessibility, _ text: String, submit: Bool = false, bundle: String = "com.apple.Notes")
        -> AXRefusal?
    {
        do {
            try AXTypeText.perform(
                args: ["bundle_id": .string(bundle), "text": .string(text), "submit": .bool(submit)], driver: ax)
            return nil
        } catch let refusal as AXRefusal {
            return refusal
        } catch {
            return .actionFailed
        }
    }

    func testTypeTextGoesIntoTheFocusedFieldOfTheFrontmostWindow() {
        let ax = notes()
        XCTAssertNil(type(ax, "merhaba\ndünya", submit: true))
        XCTAssertEqual(ax.inserted, ["merhaba\ndünya"])
        XCTAssertEqual(ax.returns, 1)
        let fallback = notes()
        fallback.acceptsInsert = false
        XCTAssertNil(type(fallback, "selam"))
        XCTAssertEqual(fallback.keystrokes, ["selam"])
        XCTAssertEqual(fallback.returns, 0)
    }

    func testTypeTextRefusesSecureFieldsAndForeignFocus() {
        let secure = notes(field: FakeAccessibility.Node("AXSecureTextField"))
        XCTAssertEqual(type(secure, "parola"), .secureField)
        let other = notes()
        other.focused = FakeAccessibility.Node("AXTextField")  // in no window of ours
        XCTAssertEqual(type(other, "x"), .focusOutsideWindow)
        let none = notes()
        none.focused = nil
        XCTAssertEqual(type(none, "x"), .noFocus)
        XCTAssertEqual(type(notes(), "x", bundle: "com.apple.Safari"), .notFrontmost)
        let untrusted = notes()
        untrusted.trusted = false
        XCTAssertEqual(type(untrusted, "x"), .notTrusted)
        XCTAssertEqual(type(notes(), String(repeating: "a", count: 501)), .badArgs)
        for ax in [secure, other, none, untrusted] {
            XCTAssertEqual(ax.inserted, [])
            XCTAssertEqual(ax.keystrokes, [])
        }
        // The app changed between typing and Return: no Return.
        let switched = notes()
        switched.frontAfterAction = .some(("com.apple.Terminal", 7))
        XCTAssertEqual(type(switched, "ls", submit: true), .notFrontmost)
        XCTAssertEqual(switched.returns, 0)
    }

    /// The keystroke fallback is re-checked right before the post: focus
    /// that moved to a secure field, to another element, or another app
    /// in front refuses (B6 review finding 4).
    func testTheKeystrokeFallbackRechecksFocusRightBeforePosting() {
        let toSecure = notes()
        toSecure.acceptsInsert = false
        toSecure.onInsertAttempt = {
            let secure = FakeAccessibility.Node("AXTextField", subrole: "AXSecureTextField")
            secure.window = toSecure.window
            toSecure.focused = secure
        }
        XCTAssertEqual(type(toSecure, "parola"), .secureField)
        let secureInput = notes()
        secureInput.acceptsInsert = false
        secureInput.onInsertAttempt = { secureInput.secureInputActive = true }
        XCTAssertEqual(type(secureInput, "x"), .secureField)
        let moved = notes()
        moved.acceptsInsert = false
        moved.onInsertAttempt = {
            let other = FakeAccessibility.Node("AXTextField")
            other.window = moved.window
            moved.focused = other
        }
        XCTAssertEqual(type(moved, "x"), .focusMoved)
        let switched = notes()
        switched.acceptsInsert = false
        switched.onInsertAttempt = { switched.front = ("com.apple.Terminal", 7) }
        XCTAssertEqual(type(switched, "x"), .notFrontmost)
        for ax in [toSecure, secureInput, moved, switched] { XCTAssertEqual(ax.keystrokes, []) }
    }

    func testAXAvailabilityIsTheAccessibilityGrant() {
        let probe = GrantedProbe()
        let executor = MacActionExecutor(
            srt: SrtExecutor(location: SrtLocation(node: "/nonexistent/node", prefix: "/nonexistent")),
            permissions: probe, accessibility: FakeAccessibility(), workspace: FakeWorkspace(), scripts: SpyScriptRunner())
        XCTAssertEqual(executor.availability(for: "ax.press"), "granted")
        probe.trusted = false
        XCTAssertEqual(executor.availability(for: "ax.press"), "not_granted")
        XCTAssertEqual(executor.availability(for: "ax.type_text"), "not_granted")
        XCTAssertEqual(executor.availability(for: "apps.open"), "granted")
        XCTAssertEqual(executor.availability(for: "repo.command.run"), "unsupported", "no usable srt")
    }

    func testTheExecutorChecksTheBundleListAgain() {
        let ax = notes()
        let executor = MacActionExecutor(
            srt: SrtExecutor(location: SrtLocation(node: "/nonexistent/node", prefix: "/nonexistent")),
            bundleIDs: ["com.apple.Terminal"], permissions: GrantedProbe(), accessibility: ax, workspace: FakeWorkspace(),
            scripts: SpyScriptRunner())
        let outcome = executor.perform(ActionRequest(
            capability: "ax.press", args: ["bundle_id": "com.apple.Notes", "role": "button", "title": "Kaydet"],
            bundleID: "com.apple.Notes"))
        XCTAssertEqual(outcome, ActionOutcome(ok: false, error: "not_on_list"))
        XCTAssertEqual(ax.pressed, [])
    }
}

final class OpenFocusTests: XCTestCase {
    func testOpenUsesTheLocalListAndLaunchServicesOnly() throws {
        let workspace = FakeWorkspace()
        try OpenFocus.open(bundleID: "com.apple.Terminal", allowed: ["com.apple.Terminal"], driver: workspace)
        XCTAssertEqual(workspace.opened.count, 1)
        XCTAssertThrowsError(try OpenFocus.open(bundleID: "com.apple.Terminal", allowed: [], driver: workspace)) {
            XCTAssertEqual($0 as? OpenFocusError, .notOnList)
        }
        XCTAssertThrowsError(try OpenFocus.open(bundleID: "com.example.None", allowed: ["com.example.None"], driver: workspace)) {
            XCTAssertEqual($0 as? OpenFocusError, .unknownApp)
        }
        XCTAssertEqual(workspace.opened.count, 1)
    }

    func testFocusNeverLaunches() throws {
        let workspace = FakeWorkspace()
        XCTAssertThrowsError(try OpenFocus.focus(bundleID: "com.apple.Terminal", allowed: ["com.apple.Terminal"], driver: workspace)) {
            XCTAssertEqual($0 as? OpenFocusError, .notRunning)
        }
        XCTAssertEqual(workspace.opened, [])
        let terminal = URL(fileURLWithPath: "/System/Applications/Utilities/Terminal.app")
        workspace.running = ["com.apple.Terminal": [terminal]]
        try OpenFocus.focus(bundleID: "com.apple.Terminal", allowed: ["com.apple.Terminal"], driver: workspace)
        XCTAssertEqual(workspace.activated, [terminal.path])
    }

    /// A look-alike bundle outside the trusted roots (in a repo, in the temp
    /// dir, behind a symlink from /Applications-like names) is never opened
    /// or focused; a bad signature is refused too (B6 review finding 5).
    func testOnlyTrustedLocationsWithAValidSignatureOpen() throws {
        let base = resolvedPath(try makeTempDirectory())
        defer { try? FileManager.default.removeItem(atPath: base) }
        let fake = base + "/work/acme/Terminal.app"
        try FileManager.default.createDirectory(atPath: fake, withIntermediateDirectories: true)
        let workspace = FakeWorkspace()
        workspace.installed["com.apple.Terminal"] = URL(fileURLWithPath: fake)
        XCTAssertThrowsError(try OpenFocus.open(bundleID: "com.apple.Terminal", allowed: ["com.apple.Terminal"], driver: workspace)) {
            XCTAssertEqual($0 as? OpenFocusError, .untrustedLocation)
        }
        // A symlink whose path looks trusted but resolves into the repo.
        let link = base + "/Applications"
        try FileManager.default.createSymbolicLink(atPath: link, withDestinationPath: base + "/work/acme")
        workspace.installed["com.apple.Terminal"] = URL(fileURLWithPath: link + "/Terminal.app")
        XCTAssertThrowsError(try OpenFocus.open(bundleID: "com.apple.Terminal", allowed: ["com.apple.Terminal"], driver: workspace))
        workspace.running = ["com.apple.Terminal": [URL(fileURLWithPath: fake)]]
        XCTAssertThrowsError(try OpenFocus.focus(bundleID: "com.apple.Terminal", allowed: ["com.apple.Terminal"], driver: workspace)) {
            XCTAssertEqual($0 as? OpenFocusError, .untrustedLocation)
        }
        XCTAssertEqual(workspace.opened, [])
        XCTAssertEqual(workspace.activated, [])
        // The real path, but the signature does not verify.
        workspace.installed["com.apple.Terminal"] = URL(fileURLWithPath: "/System/Applications/Utilities/Terminal.app")
        workspace.signed = false
        XCTAssertThrowsError(try OpenFocus.open(bundleID: "com.apple.Terminal", allowed: ["com.apple.Terminal"], driver: workspace)) {
            XCTAssertEqual($0 as? OpenFocusError, .badSignature)
        }
        XCTAssertFalse(OpenFocus.isTrustedLocation(URL(fileURLWithPath: "/Applications")))
        XCTAssertFalse(OpenFocus.isTrustedLocation(URL(fileURLWithPath: "/ApplicationsX/Evil.app")))
    }

    /// The real signature check, read-only, on an Apple app.
    func testTheSystemSignatureCheckAcceptsAnAppleApp() throws {
        let terminal = URL(fileURLWithPath: "/System/Applications/Utilities/Terminal.app")
        guard FileManager.default.fileExists(atPath: terminal.path) else { throw XCTSkip("no Terminal.app") }
        XCTAssertTrue(OpenFocus.isTrustedLocation(terminal))
        XCTAssertTrue(SystemWorkspace().hasValidSignature(terminal))
        let base = resolvedPath(try makeTempDirectory())
        defer { try? FileManager.default.removeItem(atPath: base) }
        try FileManager.default.createDirectory(atPath: base + "/Fake.app", withIntermediateDirectories: true)
        XCTAssertFalse(SystemWorkspace().hasValidSignature(URL(fileURLWithPath: base + "/Fake.app")))
    }
}
