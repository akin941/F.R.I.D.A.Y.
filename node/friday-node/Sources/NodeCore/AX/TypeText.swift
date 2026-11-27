// `ax.type_text` (gate class; Phase 10 brief M2; runbook B6): types bounded
// text (the schema's 1...500 characters) into the focused field of the
// frontmost window of `bundle_id`, and presses Return when `submit`.
//
// - Refused when the Accessibility grant is missing, when `bundle_id` is not
//   frontmost, when it has no frontmost window, when nothing has focus or
//   the focus is outside that window, and when the focused element is a
//   secure text field (or secure input is on): the node never types into a
//   password field.
// - The text is inserted at the caret through the element's AXSelectedText;
//   an element that does not take it gets keystrokes posted to that app's
//   pid only (never the system-wide event tap).
// - Before Return the frontmost app and the secure-field rule are checked
//   again.

import Foundation

public enum AXTypeText {
    public static func perform(args: JSONValue, driver: AccessibilityDriver) throws {
        guard let bundleID = args["bundle_id"]?.stringValue, let text = args["text"]?.stringValue, !text.isEmpty,
            text.unicodeScalars.count <= 500, let submit = args["submit"]?.boolValue
        else { throw AXRefusal.badArgs }
        let (pid, window) = try AXRules.frontWindow(driver, bundleID: bundleID)
        try AXRules.noSecureFocus(driver, pid: pid)
        guard let focused = driver.focusedElement(pid: pid) else { throw AXRefusal.noFocus }
        guard let owner = driver.window(of: focused), driver.same(owner, window) else {
            throw AXRefusal.focusOutsideWindow
        }
        if !driver.insertText(text, into: focused) {
            guard driver.typeKeystrokes(text, pid: pid) else { throw AXRefusal.actionFailed }
        }
        guard submit else { return }
        guard let front = driver.frontmost(), Scalars.equal(front.bundleID, bundleID), front.pid == pid else {
            throw AXRefusal.notFrontmost
        }
        try AXRules.noSecureFocus(driver, pid: pid)
        guard driver.pressReturn(pid: pid) else { throw AXRefusal.actionFailed }
    }
}
