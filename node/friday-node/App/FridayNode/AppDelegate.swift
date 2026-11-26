// The menu bar (Phase 9 brief M1, D13 items 2 and 9).
//
// - A status line: not paired / connecting / connected / waiting / stopped.
// - The mic line reserves the ADR-015 decision 2 state and always reads
//   "off" in Phase 9: the node has no microphone entitlement until Phase 11.
// - "Resume" is the owner's local action after a `bye` that stops
//   reconnecting (revoked, kill switch, credential expired).
// - "Pair..." opens the pairing window; "Unpair" forgets the credential.

import AppKit
import NodeCore

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private let statusLine = NSMenuItem(title: "Friday Node: başlatılıyor…", action: nil, keyEquivalent: "")
    private let micLine = NSMenuItem(title: "Mikrofon: kapalı", action: nil, keyEquivalent: "")
    private var resumeItem: NSMenuItem!
    private var pairItem: NSMenuItem!
    private var unpairItem: NSMenuItem!
    private var pairingWindow: PairingWindowController?
    let runner = NodeRunner(store: KeychainCredentialStore(), readers: NoReaders())

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.image = NSImage(systemSymbolName: "point.3.connected.trianglepath.dotted",
                                           accessibilityDescription: "Friday Node")
        let menu = NSMenu()
        statusLine.isEnabled = false
        micLine.isEnabled = false
        menu.addItem(statusLine)
        menu.addItem(micLine)
        menu.addItem(.separator())
        resumeItem = NSMenuItem(title: "Devam et", action: #selector(resume), keyEquivalent: "")
        pairItem = NSMenuItem(title: "Eşleştir…", action: #selector(pair), keyEquivalent: "")
        unpairItem = NSMenuItem(title: "Eşleştirmeyi kaldır", action: #selector(unpair), keyEquivalent: "")
        for item in [resumeItem!, pairItem!, unpairItem!] {
            item.target = self
            menu.addItem(item)
        }
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Çık", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        menu.autoenablesItems = false
        statusItem.menu = menu

        runner.onStatus = { [weak self] status in self?.show(status) }
        show(.connecting)
        runner.start()
    }

    func applicationWillTerminate(_ notification: Notification) {
        runner.stop()
    }

    private func show(_ status: NodeRunner.Status) {
        let text: String
        switch status {
        case .notPaired: text = "Eşleşmedi"
        case .connecting: text = "Bağlanıyor…"
        case .connected: text = "Bağlı"
        case .waiting(let seconds): text = "Yeniden bağlanacak (\(seconds) sn)"
        case .stopped(let reason): text = "Durduruldu (\(Self.describe(reason)))"
        case .needsPairing: text = "Yeniden eşleştirme gerekli"
        case .error(let what): text = "Hata: \(what)"
        }
        statusLine.title = "Friday Node: \(text)"
        if case .stopped = status { resumeItem.isEnabled = true } else { resumeItem.isEnabled = false }
        let paired: Bool
        switch status {
        case .notPaired, .needsPairing: paired = false
        default: paired = true
        }
        unpairItem.isEnabled = paired
        micLine.title = "Mikrofon: kapalı"
    }

    private static func describe(_ reason: String) -> String {
        switch reason {
        case "bye:revoked": return "iptal edildi"
        case "bye:kill_switch": return "acil durdurma"
        case "bye:credential_expired": return "kimlik süresi doldu"
        default: return reason
        }
    }

    @objc private func resume() {
        runner.ownerResume()
    }

    @objc private func pair() {
        let controller = PairingWindowController(runner: runner)
        pairingWindow = controller
        controller.showWindow(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc private func unpair() {
        try? runner.unpair()
    }
}
