// The confirmation window's rules (Phase 10 brief D5 check 23, D3; ADR-013
// decision 6; ADR-014 decision 2). A port of
// backend/nodes/reference/confirm.py, kept in NodeCore so every decision is
// unit-testable; the app's `ConfirmationWindow` (an `NSPanel`) only draws
// `ConfirmationPanelView` and forwards the two clicks.
//
// - One invocation at a time: a second confirm or gate invoke while one is
//   open is refused `busy_confirming`.
// - The text is rendered by the node from the capability's (or recipe's)
//   `confirm_template`, `{arg}` slots filled from the node's own validated
//   args, never from server text; a gate-class window also shows the
//   approval id's first 8 characters, and `repo.command.run` the repo's
//   local name, the exact argv and whether network is enabled.
// - The owner approves ("Çalıştır"), denies ("Reddet") or closes the
//   window; with no decision it times out after 60 s on the node clock.
// - It closes, with no decision, on socket loss or the kill switch.
//
// The scripted owner (DEBUG builds: `swift test`, the Conformance build's
// hook) is a queue of `(action, after)` decisions, each taking effect
// `after` seconds after the window opened, exactly as the reference double.

import Foundation

public enum ConfirmationAction: String, Sendable {
    case approve, deny, close

    /// The `confirmation.state` this action reports.
    public var state: String {
        switch self {
        case .approve: return "approved"
        case .deny: return "denied"
        case .close: return "closed"
        }
    }
}

/// A scripted decision (confirm.py `Decision`).
public struct ConfirmationDecision: Equatable, Sendable {
    public let action: ConfirmationAction
    public let after: Int64

    public init(_ action: ConfirmationAction, after: Int64 = 0) {
        self.action = action
        self.after = max(after, 0)
    }

    /// `setup.window[i]` of a case document.
    public static func from(_ body: JSONValue) -> ConfirmationDecision? {
        guard let name = body["action"]?.stringValue, let action = ConfirmationAction(rawValue: name) else {
            return nil
        }
        return ConfirmationDecision(action, after: body["after"]?.intValue ?? 0)
    }
}

/// What the window shows: built only from the node's own contract and
/// validated args (and, for gate class, the grant's approval id).
public struct ConfirmationPrompt: Equatable, Sendable {
    public let invocationID: String
    public let capability: String
    public let capabilityClass: String
    /// The filled `confirm_template` sentence.
    public let sentence: String
    public let recipeLabel: String?
    /// The repo's local name (never the server's text).
    public let repoName: String?
    /// The exact argv the mapping runs, and its network setting.
    public let argv: [String]?
    public let networkDomains: [String]?
    /// The approval id's first 8 characters (gate class).
    public let approvalPrefix: String?
    /// The mapping's sandbox mode (`repo.command.run`): the panel shows it,
    /// with a warning for `in_place` (writes stay in the repo). Not part of
    /// `lines`/`text`, which stay the reference's `render_confirmation`.
    public var sandboxMode: String? = nil

    /// The panel's extra line for the sandbox mode, if any.
    public var modeLine: String? {
        switch sandboxMode {
        case "in_place"?: return "Kip: in_place — ⚠︎ komut doğrudan repoda çalışır, yazdıkları kalır"
        case "worktree"?: return "Kip: worktree — geçici çalışma ağacında, sonra silinir"
        case let other?: return "Kip: " + ConfirmationRenderer.displaySafe(other)
        case nil: return nil
        }
    }
    public let shownAt: Int64
    public let deadline: Int64

    /// The lines in the reference order (confirm.py `render_confirmation`).
    public var lines: [String] {
        var lines = [sentence]
        if let recipeLabel { lines.append("Tarif: \(recipeLabel)") }
        if let repoName { lines.append("Repo: \(repoName)") }
        if let argv {
            lines.append("Komut: " + argv.joined(separator: " "))
            let domains = networkDomains ?? []
            lines.append("Ağ: " + (domains.isEmpty ? "kapalı" : domains.joined(separator: ", ")))
        }
        if let approvalPrefix { lines.append("Onay: \(approvalPrefix)") }
        return lines
    }

    public var text: String { lines.joined(separator: "\n") }
}

public enum ConfirmationRenderer {
    private static let slot = try! NSRegularExpression(pattern: "\\{([a-z][a-z0-9_]{0,31})\\}")

    /// The `{arg}` slot names of a template, in order (capabilities
    /// `template_slots`). Nil for a malformed template (a stray brace).
    public static func slots(_ template: String) -> [String]? {
        guard !template.isEmpty, template.unicodeScalars.count <= 300 else { return nil }
        let whole = NSRange(template.startIndex..<template.endIndex, in: template)
        let stripped = slot.stringByReplacingMatches(in: template, range: whole, withTemplate: "")
        guard !stripped.contains("{"), !stripped.contains("}") else { return nil }
        return slot.matches(in: template, range: whole).compactMap { match in
            Range(match.range(at: 1), in: template).map { String(template[$0]) }
        }
    }

    /// Python's `str(value)` for the scalars a template can name.
    static func pythonString(_ value: JSONValue?) -> String {
        switch value {
        case nil: return ""
        case .string(let text)?: return text
        case .int(let number)?: return String(number)
        case .bool(let flag)?: return flag ? "True" : "False"
        case .null?: return "None"
        case let other?: return (try? JSON.canonicalString(other)) ?? ""
        }
    }

    /// The window for one verified confirm or gate invoke (checks 1-22
    /// passed, so `args` is valid against the node's own schema).
    public static func prompt(
        envelope: JSONValue, resolved: ResolvedIntent?, shownAt: Int64,
        timeout: Int64 = NodeProtocol.confirmWindowSeconds
    ) -> ConfirmationPrompt {
        let capability = envelope["capability"]?.stringValue ?? ""
        let args = envelope["args"] ?? [:]
        let contract = CapabilityContract.named(capability)
        let recipe = contract?.recipe(for: args)
        let template = recipe?.confirmTemplate ?? contract?.confirmTemplate ?? ""
        let values = recipe != nil ? (args["params"] ?? [:]) : args
        var sentence = template
        for name in slots(template) ?? [] {
            sentence = sentence.replacingOccurrences(of: "{\(name)}", with: displaySafe(pythonString(values[name])))
        }
        let approvalID = envelope["grant"]?["approval_id"]?.stringValue
        return ConfirmationPrompt(
            invocationID: envelope["invocation_id"]?.stringValue ?? "", capability: capability,
            capabilityClass: envelope["class"]?.stringValue ?? "", sentence: sentence,
            recipeLabel: recipe.map { displaySafe($0.label) }, repoName: resolved?.repo.map { displaySafe($0.name) },
            argv: resolved?.command?.argv.map(displaySafe),
            networkDomains: resolved?.command.map { $0.networkDomains.map(displaySafe) },
            approvalPrefix: approvalID.flatMap { $0.isEmpty ? nil : displaySafe(String($0.prefix(8))) },
            sandboxMode: resolved?.command?.mode, shownAt: shownAt, deadline: shownAt + timeout)
    }

    /// Shown after a value whose control characters were made visible.
    public static let alteredMark = "⟨kontrol karakteri gösterildi⟩"

    /// A character that would break the window's lines or reorder its text:
    /// C0 and C1 controls (newline included), DEL, the line and paragraph
    /// separators, and the bidi controls.
    static func isUnsafeForDisplay(_ scalar: Unicode.Scalar) -> Bool {
        let value = scalar.value
        return value < 0x20 || (0x7F...0x9F).contains(value) || value == 0x2028 || value == 0x2029
            || (0x202A...0x202E).contains(value) || (0x2066...0x2069).contains(value) || value == 0x200E
            || value == 0x200F || value == 0x061C
    }

    /// One value as the window shows it (B5 review finding 5): every unsafe
    /// character is replaced by a visible escape (`\n`, `\r`, `\t`,
    /// `\u{202E}`), so a value can never forge another label line ("Onay:
    /// ...") or flip the text around, and an altered value ends with
    /// `alteredMark`. A value without such a character is unchanged.
    public static func displaySafe(_ text: String) -> String {
        var out = ""
        var altered = false
        for scalar in text.unicodeScalars {
            guard isUnsafeForDisplay(scalar) else {
                out.unicodeScalars.append(scalar)
                continue
            }
            altered = true
            switch scalar {
            case "\n": out += "\\n"
            case "\r": out += "\\r"
            case "\t": out += "\\t"
            default: out += "\\u{" + String(scalar.value, radix: 16, uppercase: true) + "}"
            }
        }
        return altered ? out + " " + alteredMark : out
    }
}

public struct WindowBusy: Error, Equatable {
    public let code = "busy_confirming"
    public init() {}
}

/// One window at a time; the owner's decision or the 60 s timeout, on the
/// node clock (confirm.py `ConfirmationWindowDouble`, with the scripted
/// owner replaced by `decide` for the real panel).
public final class ConfirmationWindowModel {
    public let timeout: Int64
    public private(set) var current: ConfirmationPrompt?
    private var decision: ConfirmationAction?
    private var decidedAt: Int64?
    /// Every text shown, in order (tests, the conformance report).
    public private(set) var shown: [String] = []
    /// "disconnect" / "kill_switch" per forced close.
    public private(set) var closedBy: [String] = []

    #if DEBUG
    /// The scripted owner: one decision per opened window, in order.
    public var script: [ConfirmationDecision] = []
    #endif

    public init(timeout: Int64 = NodeProtocol.confirmWindowSeconds) {
        self.timeout = timeout
    }

    #if DEBUG
    public convenience init(script: [ConfirmationDecision], timeout: Int64 = NodeProtocol.confirmWindowSeconds) {
        self.init(timeout: timeout)
        self.script = script
    }
    #endif

    public var isOpen: Bool { current != nil }

    public func open(_ prompt: ConfirmationPrompt) throws {
        guard current == nil else { throw WindowBusy() }
        current = prompt
        decision = nil
        decidedAt = nil
        #if DEBUG
        if !script.isEmpty {
            let scripted = script.removeFirst()
            decision = scripted.action
            decidedAt = prompt.shownAt + scripted.after
        }
        #endif
        shown.append(prompt.text)
    }

    /// The owner clicked (the panel's "Çalıştır" / "Reddet", or closed it)
    /// at `now`. Only the open window's first decision before its deadline
    /// counts; returns whether this one did.
    @discardableResult
    public func decide(_ action: ConfirmationAction, invocationID: String, now: Int64) -> Bool {
        guard let current, current.invocationID == invocationID, decision == nil, now < current.deadline else {
            return false
        }
        decision = action
        decidedAt = now
        return true
    }

    /// The window's outcome once due (`approved`, `denied`, `closed`,
    /// `timed_out`), closing it; nil while it is open and undecided.
    public func poll(now: Int64) -> String? {
        guard let window = current else { return nil }
        if let decidedAt, let decision, decidedAt < window.deadline {
            guard now >= decidedAt else { return nil }
            close()
            return decision.state
        }
        if now >= window.deadline {
            close()
            return "timed_out"
        }
        return nil
    }

    /// Close with no decision (socket loss, kill switch).
    public func forceClose(_ reason: String) {
        guard current != nil else { return }
        close()
        closedBy.append(reason)
    }

    private func close() {
        current = nil
        decision = nil
        decidedAt = nil
    }
}

/// What the session tells the app's panel (on the session's queue).
public protocol ConfirmationObserver: AnyObject {
    /// A window opened (`confirmation{shown}` was just produced).
    func confirmationShown(_ prompt: ConfirmationPrompt)
    /// The open window closed: a decision, the timeout, socket loss or the
    /// kill switch (`reason`: the confirmation state, "disconnect" or
    /// "kill_switch").
    func confirmationClosed(invocationID: String, reason: String)
    /// The approved command started ("Durdur" applies to it).
    func commandStarted(invocationID: String)
    /// The command ended (result sent, stopped, or the socket was lost).
    func commandEnded(invocationID: String)
}

/// The panel's content at one instant: pure, so the AppKit layer only
/// draws it.
public struct ConfirmationPanelView: Equatable, Sendable {
    public enum Mode: Equatable, Sendable { case hidden, asking, running }

    public static let approveTitle = "Çalıştır"
    public static let denyTitle = "Reddet"
    public static let stopTitle = "Durdur"
    public static let windowTitle = "Friday Node — onay"

    public let mode: Mode
    public let invocationID: String?
    public let lines: [String]
    /// Whole seconds left before the window times out (asking only).
    public let secondsLeft: Int64
    public let countdown: String
    /// The buttons to show, in order.
    public let buttons: [String]
    /// What "Durdur" stops: the running command's invocation, also while
    /// a second window asks (B5 review NIT: the stop must stay reachable).
    public let stopInvocationID: String?

    public static let hidden = ConfirmationPanelView(
        mode: .hidden, invocationID: nil, lines: [], secondsLeft: 0, countdown: "", buttons: [],
        stopInvocationID: nil)

    /// The line a window shows while another approved command still runs.
    public static let stillRunningLine = "Önceki onaylı komut hâlâ çalışıyor (Durdur ile durdurulur)."

    /// `prompt`: the open window, if any; `running`: the prompt whose
    /// command runs, if any. An open window wins (one window at a time), and
    /// keeps "Durdur" for the command that runs.
    public static func make(prompt: ConfirmationPrompt?, running: ConfirmationPrompt?, now: Int64)
        -> ConfirmationPanelView
    {
        if let prompt {
            let left = max(prompt.deadline - now, 0)
            return ConfirmationPanelView(
                mode: .asking, invocationID: prompt.invocationID,
                lines: prompt.lines + [prompt.modeLine].compactMap { $0 } + (running == nil ? [] : [stillRunningLine]),
                secondsLeft: left,
                countdown: "\(left) sn", buttons: running == nil ? [approveTitle, denyTitle] : [stopTitle, denyTitle, approveTitle],
                stopInvocationID: running?.invocationID)
        }
        if let running {
            return ConfirmationPanelView(
                mode: .running, invocationID: running.invocationID,
                lines: running.lines + [running.modeLine].compactMap { $0 }, secondsLeft: 0,
                countdown: "Çalışıyor…", buttons: [stopTitle], stopInvocationID: running.invocationID)
        }
        return .hidden
    }
}
