// The confirmation window (Phase 10 brief D5 check 23, Mac session M1;
// ADR-014 decision 2; runbook B5): a non-activating floating `NSPanel`.
//
// It draws `ConfirmationPanelView` (NodeCore) and nothing else: the lines are
// rendered by the node from the capability's (or recipe's)
// `confirm_template` and the node's OWN validated args, plus the approval
// id's first 8 characters (gate class) and, for `repo.command.run`, the
// repo's local name, the exact argv and the network setting. No server text
// ever reaches this panel, and its labels are plain text fields (no HTML, no
// attributed or rich text).
//
// - "Çalıştır" / "Reddet" forward the owner's click to the session (which
//   applies it on the runner queue: check 23, then check 24); closing the
//   panel is a `close` decision.
// - A 60 s countdown; the session times the window out on its own clock.
// - It closes on socket loss, the kill switch or the timeout (the session
//   tells it through `ConfirmationObserver`); one window at a time.
// - While the approved command runs it shows "Durdur", which stops it.
//
// The decision logic lives in NodeCore (ConfirmationWindowModel,
// ConfirmationPanelView, NodeSession) and is unit-tested there; this layer
// only draws and forwards.

import AppKit
import NodeCore

final class ConfirmationWindowController: NSObject, ConfirmationObserver, NSWindowDelegate {
    private let runner: NodeRunner
    private let panel: NSPanel
    private let textStack = NSStackView()
    private let countdown = NSTextField(labelWithString: "")
    private let approveButton = NSButton(title: ConfirmationPanelView.approveTitle, target: nil, action: nil)
    private let denyButton = NSButton(title: ConfirmationPanelView.denyTitle, target: nil, action: nil)
    private let stopButton = NSButton(title: ConfirmationPanelView.stopTitle, target: nil, action: nil)
    private var timer: Timer?
    private var current: ConfirmationPanelView = .hidden

    init(runner: NodeRunner) {
        self.runner = runner
        panel = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: 460, height: 200),
            styleMask: [.titled, .closable, .nonactivatingPanel, .utilityWindow], backing: .buffered, defer: true)
        super.init()
        panel.title = ConfirmationPanelView.windowTitle
        panel.isFloatingPanel = true
        panel.level = .floating
        panel.hidesOnDeactivate = false
        panel.becomesKeyOnlyIfNeeded = true
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.isReleasedWhenClosed = false
        panel.delegate = self

        textStack.orientation = .vertical
        textStack.alignment = .leading
        textStack.spacing = 4
        countdown.textColor = .secondaryLabelColor
        for (button, action) in [
            (approveButton, #selector(approve)), (denyButton, #selector(deny)), (stopButton, #selector(stop)),
        ] {
            button.target = self
            button.action = action
            button.bezelStyle = .rounded
        }
        // Never a default (Return) button: a stray keypress must not approve.
        approveButton.keyEquivalent = ""
        denyButton.keyEquivalent = "\u{1b}"
        let buttons = NSStackView(views: [countdown, NSView(), stopButton, denyButton, approveButton])
        buttons.orientation = .horizontal
        let stack = NSStackView(views: [textStack, buttons])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 12
        stack.edgeInsets = NSEdgeInsets(top: 16, left: 16, bottom: 16, right: 16)
        buttons.widthAnchor.constraint(equalTo: stack.widthAnchor, constant: -32).isActive = true
        panel.contentView = stack
    }

    // MARK: ConfirmationObserver (called on the runner queue)

    func confirmationShown(_ prompt: ConfirmationPrompt) { refreshSoon() }
    func confirmationClosed(invocationID: String, reason: String) { refreshSoon() }
    func commandStarted(invocationID: String) { refreshSoon() }
    func commandEnded(invocationID: String) { refreshSoon() }

    private func refreshSoon() {
        DispatchQueue.main.async { self.refresh() }
    }

    // MARK: Drawing

    /// Redraw from the session's state now (main thread).
    func refresh() {
        let view = runner.panelView()
        defer { current = view }
        guard view.mode != .hidden else {
            stopTimer()
            panel.orderOut(nil)
            return
        }
        if view.lines != current.lines || view.invocationID != current.invocationID {
            for subview in textStack.arrangedSubviews {
                textStack.removeArrangedSubview(subview)
                subview.removeFromSuperview()
            }
            for line in view.lines {
                let label = NSTextField(wrappingLabelWithString: line)
                label.isSelectable = false
                label.allowsEditingTextAttributes = false
                label.preferredMaxLayoutWidth = 420
                textStack.addArrangedSubview(label)
            }
        }
        countdown.stringValue = view.countdown
        approveButton.isHidden = !view.buttons.contains(ConfirmationPanelView.approveTitle)
        denyButton.isHidden = !view.buttons.contains(ConfirmationPanelView.denyTitle)
        stopButton.isHidden = !view.buttons.contains(ConfirmationPanelView.stopTitle)
        if !panel.isVisible {
            panel.center()
            // Non-activating: shown above other windows without taking focus
            // from the app the owner is using.
            panel.orderFrontRegardless()
        }
        startTimer()
    }

    private func startTimer() {
        guard timer == nil else { return }
        let timer = Timer(timeInterval: 1, repeats: true) { [weak self] _ in self?.refresh() }
        RunLoop.main.add(timer, forMode: .common)
        self.timer = timer
    }

    private func stopTimer() {
        timer?.invalidate()
        timer = nil
    }

    // MARK: The owner's clicks

    @objc private func approve() {
        guard current.mode == .asking, let id = current.invocationID else { return }
        runner.decide(.approve, invocationID: id)
    }

    @objc private func deny() {
        guard current.mode == .asking, let id = current.invocationID else { return }
        runner.decide(.deny, invocationID: id)
    }

    @objc private func stop() {
        guard current.mode == .running, let id = current.invocationID else { return }
        runner.stop(invocationID: id)
    }

    /// The close button: a `close` decision while asking. While a command
    /// runs the panel stays (it is stopped with "Durdur"); the session closes
    /// the panel itself once the decision or the result is in.
    func windowShouldClose(_ sender: NSWindow) -> Bool {
        if current.mode == .asking, let id = current.invocationID {
            runner.decide(.close, invocationID: id)
        }
        return false
    }
}
