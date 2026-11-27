// `ax.press` (gate class; Phase 10 brief M2; runbook B6): presses one
// element of the frontmost window of `bundle_id`.
//
// - Refused when the Accessibility grant is missing, when `bundle_id` is not
//   the frontmost app, when it has no frontmost window, or when a secure text
//   field has focus (or secure input is on).
// - Only the frontmost window's subtree is searched (bounded: depth 40, 4000
//   elements); the element's role must be the mapped AX role (`button` ->
//   AXButton, `menu_item` -> AXMenuItem, `checkbox` -> AXCheckBox) and its
//   AXTitle must equal `title` exactly (scalar for scalar). None -> refused;
//   more than one -> refused (never a guess).
// - The frontmost app is checked again right before the press.

import Foundation

public enum AXPress {
    public static let roles = ["button": "AXButton", "menu_item": "AXMenuItem", "checkbox": "AXCheckBox"]
    static let maxDepth = 40
    static let maxElements = 4000

    public static func perform(args: JSONValue, driver: AccessibilityDriver) throws {
        guard let bundleID = args["bundle_id"]?.stringValue, let role = args["role"]?.stringValue,
            let axRole = roles[role], let title = args["title"]?.stringValue, !title.isEmpty
        else { throw AXRefusal.badArgs }
        let (pid, window) = try AXRules.frontWindow(driver, bundleID: bundleID)
        try AXRules.noSecureFocus(driver, pid: pid)
        let matches = find(in: window, role: axRole, title: title, driver: driver)
        guard !matches.isEmpty else { throw AXRefusal.notFound }
        guard matches.count == 1 else { throw AXRefusal.ambiguous }
        try AXRules.stillFront(driver, bundleID: bundleID, pid: pid)
        try AXRules.noSecureFocus(driver, pid: pid)
        guard driver.press(matches[0]) else { throw AXRefusal.actionFailed }
    }

    /// Up to two exact matches in `window`'s subtree (two means ambiguous).
    static func find(in window: AXHandle, role: String, title: String, driver: AccessibilityDriver) -> [AXHandle] {
        var matches: [AXHandle] = []
        var queue: [(AXHandle, Int)] = [(window, 0)]
        var visited = 0
        while !queue.isEmpty, visited < maxElements, matches.count < 2 {
            let (element, depth) = queue.removeFirst()
            visited += 1
            if driver.role(of: element) == role, let candidate = driver.title(of: element),
                Scalars.equal(candidate, title)
            {
                matches.append(element)
            }
            if depth < maxDepth { queue += driver.children(of: element).map { ($0, depth + 1) } }
        }
        return matches
    }
}
