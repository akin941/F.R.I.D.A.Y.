// The command-mapping part of the "İzinler" menu (Phase 10 brief M2, D4;
// runbook B6; owner step 5).
//
// What the owner can change here, each change only after Touch ID or the
// login password (CommandMappingsEditor, `.deviceOwnerAuthentication`), and
// each refused before saving when it does not validate (CommandMappings:
// the reference's checks plus "PATH contains /bin"):
// - repos: root (a folder chooser), local name;
// - per repo: `command_kind` -> argv (one argument per line, never a shell
//   line), PATH, environment names, network domains (none = network off,
//   shown as such in the confirmation window), runtime cap, sandbox mode;
// - recipes: enable / disable each of the node's catalogue;
// - bundle ids: the apps open/focus and AX may target (an app chooser);
// - the srt location (Node binary and npm prefix).
// Looking needs no authentication; the TCC requests (Accessibility, the
// recipes' Automation targets) are system prompts the owner answers.

import AppKit
import ApplicationServices
import NodeCore
import UniformTypeIdentifiers

final class CommandMappingsMenu: NSObject {
    private let editor: CommandMappingsEditor
    private let permissions: PermissionProbe
    private let onFailure: (String) -> Void
    private var mappings = CommandMappings()

    init(editor: CommandMappingsEditor, permissions: PermissionProbe, onFailure: @escaping (String) -> Void) {
        self.editor = editor
        self.permissions = permissions
        self.onFailure = onFailure
    }

    /// Append the section to `menu`.
    func build(into menu: NSMenu) {
        mappings = (try? editor.current()) ?? CommandMappings()
        menu.addItem(.separator())
        header("Komut eşlemeleri (repo.command.run)", into: menu)
        for repo in mappings.repos {
            let item = NSMenuItem(title: "\(repo.name) — \(repo.root)", action: nil, keyEquivalent: "")
            let sub = NSMenu(title: repo.name)
            sub.autoenablesItems = false
            for command in repo.commands {
                let network = command.networkDomains.isEmpty ? "ağ kapalı" : "ağ: " + command.networkDomains.joined(separator: ", ")
                let line = NSMenuItem(
                    title: "\(command.kind): \(command.argv.joined(separator: " ")) (\(command.mode), \(command.maxRuntimeSeconds) sn, \(network))",
                    action: nil, keyEquivalent: "")
                line.isEnabled = false
                sub.addItem(line)
                sub.addItem(action("  Kaldır: \(command.kind)", #selector(removeCommand(_:)),
                                   represented: [repo.root, command.kind]))
            }
            sub.addItem(.separator())
            sub.addItem(action("Komut ekle / değiştir…", #selector(editCommand(_:)), represented: [repo.root]))
            sub.addItem(action("Repoyu kaldır", #selector(removeRepo(_:)), represented: [repo.root]))
            item.submenu = sub
            menu.addItem(item)
        }
        menu.addItem(action("Komut deposu ekle…", #selector(addRepo)))

        menu.addItem(.separator())
        header("Tarifler (applescript.recipe.run)", into: menu)
        for recipe in RecipeContract.all.values.sorted(by: { $0.name < $1.name }) {
            let item = action("\(recipe.label) (\(recipe.name))", #selector(toggleRecipe(_:)), represented: recipe.name)
            item.state = mappings.recipesEnabled.contains(recipe.name) ? .on : .off
            menu.addItem(item)
        }
        menu.addItem(action("Otomasyon izinlerini iste", #selector(askAutomation)))

        menu.addItem(.separator())
        header("Uygulamalar (apps.open, apps.focus, ax.*)", into: menu)
        for bundle in mappings.bundleIDs {
            menu.addItem(action("Kaldır: \(bundle)", #selector(removeBundle(_:)), represented: bundle))
        }
        menu.addItem(action("Uygulama ekle…", #selector(addBundle)))
        let trusted = permissions.accessibilityTrusted
        let ax = action(trusted ? "Erişilebilirlik izni: verildi" : "Erişilebilirlik iznini iste", #selector(askAccessibility))
        ax.isEnabled = !trusted
        menu.addItem(ax)

        menu.addItem(.separator())
        let problem = mappings.srt.problem()
        header("srt: " + (problem ?? "hazır (\(SrtLocation.pinnedVersion), dosyalar onaylı)"), into: menu)
        // Setting the location stores the sha256 of node, node_modules and
        // the lockfile; any later change of those files makes srt
        // `unsupported` until the owner re-confirms them with Touch ID.
        menu.addItem(action("srt konumu… (Touch ID ile onaylanır)", #selector(editSrt)))
        menu.addItem(action("srt dosyalarını yeniden onayla (Touch ID)", #selector(confirmSrt)))
    }

    private func header(_ title: String, into menu: NSMenu) {
        let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        item.isEnabled = false
        menu.addItem(item)
    }

    private func action(_ title: String, _ selector: Selector, represented: Any? = nil) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: selector, keyEquivalent: "")
        item.target = self
        item.representedObject = represented
        return item
    }

    // MARK: Changes (each behind LocalAuthentication)

    /// Every change names its target by repo root (and command kind), never
    /// by a menu index read before the Touch ID prompt; a target gone by then
    /// refuses the change (CommandMappings.TargetGone).
    private func change(_ reason: String, _ change: @escaping (inout CommandMappings) throws -> Void) {
        Task {
            do {
                _ = try await editor.change(reason: reason, change)
            } catch PolicyChangeError.notAuthenticated {
                // Cancelled or failed authentication: nothing changed.
            } catch {
                let text = (error as? CustomStringConvertible)?.description ?? String(describing: error)
                await MainActor.run { self.onFailure(text) }
            }
        }
    }

    @objc private func addRepo() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = "Seç"
        NSApp.activate(ignoringOtherApps: true)
        guard panel.runModal() == .OK, let url = panel.url else { return }
        guard let name = ask("Yerel ad", "Bu repo onay penceresinde bu adla görünür.", default: url.lastPathComponent),
            !name.isEmpty
        else { return }
        let home = NSHomeDirectory()
        var root = url.path
        if root.hasPrefix(home + "/") { root = "~/" + root.dropFirst(home.count + 1) }
        change("Friday Node'a komut deposu ekle: \(name)") { mappings in
            guard !mappings.repos.contains(where: { $0.root == root }) else { return }
            mappings.repos.append(CommandMappings.Repo(name: name, root: root))
        }
    }

    @objc private func removeRepo(_ sender: NSMenuItem) {
        guard let root = (sender.representedObject as? [String])?.first else { return }
        change("Friday Node'dan komut deposunu kaldır: \(root)") { try $0.removeRepo(root: root) }
    }

    @objc private func removeCommand(_ sender: NSMenuItem) {
        guard let key = sender.representedObject as? [String], key.count == 2 else { return }
        let (root, kind) = (key[0], key[1])
        change("Friday Node'dan \(root) için '\(kind)' komutunu kaldır") { try $0.removeCommand(root: root, kind: kind) }
    }

    @objc private func editCommand(_ sender: NSMenuItem) {
        guard let root = (sender.representedObject as? [String])?.first,
            let repo = mappings.repos.first(where: { $0.root == root }), let command = CommandDialog.run(repo: repo)
        else { return }
        change("Friday Node'da \(repo.name) için '\(command.kind)' komutunu eşle") {
            try $0.setCommand(root: root, command)
        }
    }

    @objc private func toggleRecipe(_ sender: NSMenuItem) {
        guard let name = sender.representedObject as? String else { return }
        change("Friday Node'da tarif iznini değiştir: \(name)") { mappings in
            if let found = mappings.recipesEnabled.firstIndex(of: name) {
                mappings.recipesEnabled.remove(at: found)
            } else {
                mappings.recipesEnabled.append(name)
            }
        }
    }

    @objc private func addBundle() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowedContentTypes = [.application]
        panel.directoryURL = URL(fileURLWithPath: "/Applications")
        panel.prompt = "Ekle"
        NSApp.activate(ignoringOtherApps: true)
        guard panel.runModal() == .OK, let url = panel.url, let bundle = Bundle(url: url)?.bundleIdentifier else { return }
        change("Friday Node'un uygulama listesine ekle: \(bundle)") { mappings in
            if !mappings.bundleIDs.contains(bundle) { mappings.bundleIDs.append(bundle) }
        }
    }

    @objc private func removeBundle(_ sender: NSMenuItem) {
        guard let bundle = sender.representedObject as? String else { return }
        change("Friday Node'un uygulama listesinden kaldır: \(bundle)") { mappings in
            mappings.bundleIDs.removeAll { $0 == bundle }
        }
    }

    @objc private func editSrt() {
        let current = mappings.srt
        guard let node = ask("Node yolu", "srt'yi çalıştıran Node (>= 22.12), mutlak yol.", default: current.node),
            let prefix = ask("srt öneki", "npm --prefix dizini (@anthropic-ai/sandbox-runtime \(SrtLocation.pinnedVersion)).",
                             default: current.prefix)
        else { return }
        // The digests are taken after Touch ID, inside the change.
        change("Friday Node'un srt konumunu ayarla ve dosyalarını onayla") { mappings in
            mappings.srt = try SrtLocation(node: node, prefix: prefix).confirmed()
        }
    }

    /// The Touch ID prompt names what changed since the last confirmation;
    /// the confirmation itself is refused unless the files are the pinned
    /// npm packages (SrtPin).
    @objc private func confirmSrt() {
        let changed = mappings.srt.digests.isEmpty ? [] : mappings.srt.changedDigests()
        let what = changed.isEmpty ? "" : " (değişen: \(changed.joined(separator: ", ")))"
        change("Friday Node'un srt dosyalarını yeniden onayla\(what)") { mappings in
            mappings.srt = try mappings.srt.confirmed()
        }
    }

    // MARK: TCC (system prompts the owner answers)

    @objc private func askAccessibility() {
        let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        _ = AXIsProcessTrustedWithOptions(options)
    }

    @objc private func askAutomation() {
        let targets = Set(mappings.recipesEnabled.compactMap { RecipeBook.recipe(named: $0)?.targetBundleID })
        let permissions = self.permissions
        DispatchQueue.global(qos: .userInitiated).async {
            // Asking blocks while the system prompt is up; a target that is
            // not running cannot be asked (open it first).
            for target in targets.sorted() { _ = permissions.automation(target, ask: true) }
        }
    }

    // MARK: Dialogs

    private func ask(_ title: String, _ text: String, default value: String) -> String? {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = text
        alert.addButton(withTitle: "Tamam")
        alert.addButton(withTitle: "Vazgeç")
        let field = NSTextField(string: value)
        field.frame = NSRect(x: 0, y: 0, width: 360, height: 24)
        alert.accessoryView = field
        NSApp.activate(ignoringOtherApps: true)
        guard alert.runModal() == .alertFirstButtonReturn else { return nil }
        return field.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

/// The "Komut ekle / değiştir" dialog: one mapping, typed fields only.
enum CommandDialog {
    static func run(repo: CommandMappings.Repo) -> CommandMappings.Command? {
        let alert = NSAlert()
        alert.messageText = "\(repo.name): komut eşle"
        alert.informativeText =
            "argv: her satıra bir argüman (kabuk yok). PATH /bin içermeli. Ağ alan adları boşsa ağ kapalı."
        alert.addButton(withTitle: "Kaydet")
        alert.addButton(withTitle: "Vazgeç")

        let kind = NSPopUpButton()
        kind.addItems(withTitles: CommandMapping.kinds)
        let argv = NSTextView(frame: NSRect(x: 0, y: 0, width: 360, height: 80))
        argv.isRichText = false
        argv.isAutomaticQuoteSubstitutionEnabled = false
        argv.isAutomaticDashSubstitutionEnabled = false
        argv.isAutomaticTextReplacementEnabled = false
        argv.font = .monospacedSystemFont(ofSize: 12, weight: .regular)
        let argvScroll = NSScrollView(frame: NSRect(x: 0, y: 0, width: 360, height: 80))
        argvScroll.documentView = argv
        argvScroll.hasVerticalScroller = true
        argvScroll.borderType = .bezelBorder
        let path = NSTextField(string: CommandMapping.defaultPath)
        let env = NSTextField(string: "")
        env.placeholderString = "ör. PYTHONHASHSEED (virgülle)"
        let domains = NSTextField(string: "")
        domains.placeholderString = "boş = ağ kapalı; ör. pypi.org, files.pythonhosted.org"
        let runtime = NSTextField(string: "900")
        let mode = NSPopUpButton()
        mode.addItems(withTitles: CommandMapping.modes)

        let grid = NSGridView(views: [
            [NSTextField(labelWithString: "Tür"), kind],
            [NSTextField(labelWithString: "argv"), argvScroll],
            [NSTextField(labelWithString: "PATH"), path],
            [NSTextField(labelWithString: "Ortam adları"), env],
            [NSTextField(labelWithString: "Ağ alan adları"), domains],
            [NSTextField(labelWithString: "Süre (sn, ≤ 900)"), runtime],
            [NSTextField(labelWithString: "Kip"), mode],
        ])
        grid.column(at: 1).width = 360
        grid.frame = NSRect(x: 0, y: 0, width: 480, height: 260)
        alert.accessoryView = grid
        NSApp.activate(ignoringOtherApps: true)
        guard alert.runModal() == .alertFirstButtonReturn else { return nil }

        func list(_ text: String) -> [String] {
            text.split(whereSeparator: { $0 == "," || $0 == " " }).map(String.init).filter { !$0.isEmpty }
        }
        let parts = argv.string.split(separator: "\n", omittingEmptySubsequences: true).map(String.init)
        return CommandMappings.Command(
            kind: kind.titleOfSelectedItem ?? "test", argv: parts,
            path: path.stringValue.trimmingCharacters(in: .whitespaces), envNames: list(env.stringValue),
            networkDomains: list(domains.stringValue), maxRuntimeSeconds: Int64(runtime.stringValue) ?? 0,
            mode: mode.titleOfSelectedItem ?? "in_place")
    }
}
