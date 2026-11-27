// The menu bar (Phase 9 brief M1, D13 items 2 and 9).
//
// - A status line: not paired / connecting / connected / waiting / stopped.
// - The mic line reserves the ADR-015 decision 2 state and always reads
//   "off" in Phase 9: the node has no microphone entitlement until Phase 11.
// - "Resume" is the owner's local action after a `bye` that stops
//   reconnecting (revoked, kill switch, credential expired).
// - "Pair..." opens the pairing window; "Unpair" forgets the credential.
// - "İzinler" (M2): the local grants for ~/.claude/projects and
//   ~/.codex/sessions, the window-titles toggle and the git repo list. Every
//   change asks for Touch ID or the password first (PolicyEditor); the node
//   then advertises the new availability at once.
// - "Acil durdur" (Phase 10 M1): the node's local kill switch; the
//   confirmation window (ConfirmationWindow.swift) closes and later clicks
//   are refused until the owner releases it after LocalAuthentication. The
//   flag lives on the runner (B6), so the item is right after a re-pair.
// - "İzinler" also holds (runbook B6, CommandMappingsMenu.swift) the command
//   mappings, recipes, bundle-id list and srt location, every change behind
//   LocalAuthentication, plus the Accessibility / Automation TCC requests.

import AppKit
import CoreGraphics
import NodeCore

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private let statusLine = NSMenuItem(title: "Friday Node: başlatılıyor…", action: nil, keyEquivalent: "")
    private let micLine = NSMenuItem(title: "Mikrofon: kapalı", action: nil, keyEquivalent: "")
    private var resumeItem: NSMenuItem!
    private var pairItem: NSMenuItem!
    private var unpairItem: NSMenuItem!
    private var pairingWindow: PairingWindowController?
    private let permissionsMenu = NSMenu(title: "İzinler")
    let policyStore = KeychainLocalPolicyStore()
    /// Runbook B6: the command mappings (keychain), the TCC probe shared by
    /// the executor and the menu, and the real executors built from them.
    let mappingsStore = KeychainCommandMappingsStore()
    let permissions = SystemPermissionProbe()
    lazy var runner: NodeRunner = {
        let permissions = self.permissions
        return NodeRunner(
            store: KeychainCredentialStore(), policyStore: policyStore, desktop: SystemDesktop(),
            home: NSHomeDirectory(), usage: .unsupported, mappingsStore: mappingsStore,
            makeExecutor: { mappings in
                MacActionExecutor(
                    srt: SrtExecutor(location: mappings.srt), bundleIDs: Set(mappings.bundleIDs),
                    recipesEnabled: Set(mappings.recipesEnabled), permissions: permissions)
            })
    }()
    lazy var editor = PolicyEditor(store: policyStore, authentication: DeviceOwnerAuthentication())
    lazy var mappingsEditor = CommandMappingsEditor(store: mappingsStore, authentication: DeviceOwnerAuthentication())
    private lazy var mappingsMenu = CommandMappingsMenu(
        editor: mappingsEditor, permissions: permissions,
        onFailure: { [weak self] text in self?.statusLine.title = "Friday Node: eşleme reddedildi (\(text))" })
    /// The confirmation window (Phase 10 M1); the runner holds it weakly.
    private lazy var confirmationWindow = ConfirmationWindowController(runner: runner)
    private var killSwitchItem: NSMenuItem!

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
        // The local kill switch (Phase 10 D5 check 24): closes an open
        // confirmation window, stops a running command, and refuses every
        // later click until released (release needs Touch ID or the password).
        killSwitchItem = NSMenuItem(title: "Acil durdur", action: #selector(toggleKillSwitch), keyEquivalent: "")
        for item in [resumeItem!, pairItem!, unpairItem!, killSwitchItem!] {
            item.target = self
            menu.addItem(item)
        }
        let permissions = NSMenuItem(title: "İzinler", action: nil, keyEquivalent: "")
        permissions.submenu = permissionsMenu
        permissionsMenu.autoenablesItems = false
        menu.addItem(.separator())
        menu.addItem(permissions)
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Çık", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        menu.autoenablesItems = false
        menu.delegate = self
        statusItem.menu = menu

        editor.onChange = { [weak self] policy in
            self?.runner.policyChanged(policy)
            DispatchQueue.main.async { self?.rebuildPermissions() }
        }
        mappingsEditor.onChange = { [weak self] mappings in
            self?.runner.mappingsChanged(mappings)
            DispatchQueue.main.async { self?.rebuildPermissions() }
        }
        rebuildPermissions()
        runner.confirmationObserver = confirmationWindow
        runner.onStatus = { [weak self] status in
            self?.show(status)
            // A lost or closed socket closes the confirmation window at once.
            self?.confirmationWindow.refresh()
        }
        show(.connecting)
        // The hook relay (M3): Claude Code's `friday-node hook` writes here.
        do {
            try runner.startHookRelay()
        } catch {
            statusLine.title = "Friday Node: hook soketi açılamadı"
        }
        runner.start()
    }

    func applicationWillTerminate(_ notification: Notification) {
        runner.stopHookRelay()
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

    // MARK: Permissions (local policy)

    private func rebuildPermissions() {
        permissionsMenu.removeAllItems()
        let policy = (try? editor.current()) ?? LocalPolicy()
        func toggle(_ title: String, _ on: Bool, _ action: Selector) {
            let item = NSMenuItem(title: title, action: action, keyEquivalent: "")
            item.state = on ? .on : .off
            item.target = self
            permissionsMenu.addItem(item)
        }
        toggle("Claude Code oturumları (~/.claude/projects)", policy.grants.claude, #selector(toggleClaude))
        toggle("Codex oturumları (~/.codex/sessions)", policy.grants.codex, #selector(toggleCodex))
        toggle("Pencere başlıkları", policy.windowTitles, #selector(toggleTitles))
        permissionsMenu.addItem(.separator())
        let header = NSMenuItem(title: "Git depoları (yalnızca sayılar)", action: nil, keyEquivalent: "")
        header.isEnabled = false
        permissionsMenu.addItem(header)
        for (index, repo) in policy.repos.enumerated() {
            let item = NSMenuItem(title: "Kaldır: \(repo.root)", action: #selector(removeRepo(_:)), keyEquivalent: "")
            item.tag = index
            item.target = self
            permissionsMenu.addItem(item)
        }
        let add = NSMenuItem(title: "Depo ekle…", action: #selector(addRepo), keyEquivalent: "")
        add.target = self
        permissionsMenu.addItem(add)
        // Runbook B6: command mappings, recipes, the bundle-id list, srt.
        mappingsMenu.build(into: permissionsMenu)
    }

    /// The kill switch item follows the runner's flag (also after a
    /// re-pair); the TCC lines are re-read each time the menu opens.
    private func syncKillSwitchItem() {
        let engaged = runner.killSwitchEngaged
        killSwitchItem.state = engaged ? .on : .off
        killSwitchItem.title = engaged ? "Acil durdurmayı kaldır" : "Acil durdur"
    }

    private func changePolicy(
        _ reason: String, then: ((LocalPolicy) -> Void)? = nil, _ change: @escaping (inout LocalPolicy) -> Void
    ) {
        Task {
            do {
                let policy = try await editor.change(reason: reason, change)
                await MainActor.run { then?(policy) }
            } catch PolicyChangeError.notAuthenticated {
                // The owner cancelled or failed authentication: nothing changed.
            } catch {
                await MainActor.run { self.statusLine.title = "Friday Node: izinler kaydedilemedi" }
            }
        }
    }

    @objc private func toggleClaude() {
        changePolicy("Friday Node'un Claude Code oturum iznini değiştir") { $0.grants.claude.toggle() }
    }

    @objc private func toggleCodex() {
        changePolicy("Friday Node'un Codex oturum iznini değiştir") { $0.grants.codex.toggle() }
    }

    @objc private func toggleTitles() {
        // The Screen Recording prompt comes only after the owner authenticated
        // and the toggle is on.
        changePolicy(
            "Friday Node'un pencere başlıklarını okuma iznini değiştir",
            then: { policy in
                if policy.windowTitles && !CGPreflightScreenCaptureAccess() { _ = CGRequestScreenCaptureAccess() }
            }
        ) { $0.windowTitles.toggle() }
    }

    @objc private func addRepo() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = "Ekle"
        NSApp.activate(ignoringOtherApps: true)
        guard panel.runModal() == .OK, let url = panel.url else { return }
        let home = NSHomeDirectory()
        var root = url.path
        if root == home { root = "~" } else if root.hasPrefix(home + "/") { root = "~/" + root.dropFirst(home.count + 1) }
        let name = url.lastPathComponent
        changePolicy("Friday Node'a git deposu ekle: \(name)") { policy in
            guard !policy.repos.contains(where: { $0.root == root }) else { return }
            policy.repos.append(LocalPolicy.Repo(name: name, root: root))
        }
    }

    @objc private func removeRepo(_ sender: NSMenuItem) {
        let index = sender.tag
        changePolicy("Friday Node'dan git deposunu kaldır") { policy in
            guard policy.repos.indices.contains(index) else { return }
            policy.repos.remove(at: index)
        }
    }

    @objc private func resume() {
        runner.ownerResume()
    }

    @objc private func toggleKillSwitch() {
        guard runner.killSwitchEngaged else {
            runner.engageKillSwitch()
            syncKillSwitchItem()
            return
        }
        Task {
            guard await DeviceOwnerAuthentication().authenticate(reason: "Friday Node'un acil durdurmasını kaldır")
            else { return }
            runner.releaseKillSwitch()
            await MainActor.run { self.syncKillSwitchItem() }
        }
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

extension AppDelegate: NSMenuDelegate {
    /// The kill switch item, the permissions (TCC lines included) and the
    /// advertised availability are refreshed whenever the menu opens.
    func menuWillOpen(_ menu: NSMenu) {
        syncKillSwitchItem()
        rebuildPermissions()
        runner.refreshAvailability()
    }
}
