// "Sunucu adresini değiştir" (Phase 13 M1, runbook E1; ADR-017 decision 7):
// the owner types the new base URL; ServerAddressEditor (NodeCore,
// ServerAddress.swift) checks it, asks for Touch ID or the login password,
// probes the new server against the pins taken at pairing and only then
// saves it in the Keychain credential, keeping every pin. Nothing here
// talks to the server beyond that TLS handshake; no server frame can open
// or fill this window.

import AppKit
import NodeCore

final class ServerAddressWindowController: NSWindowController {
    private let editor: ServerAddressEditor
    private let onChanged: () -> Void
    private let serverField = NSTextField(string: "")
    private let message = NSTextField(labelWithString: "")
    private let changeButton = NSButton(title: "Değiştir", target: nil, action: nil)

    init(editor: ServerAddressEditor, onChanged: @escaping () -> Void) {
        self.editor = editor
        self.onChanged = onChanged
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 480, height: 200), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Friday Node — Sunucu adresi"
        super.init(window: window)
        serverField.stringValue = editor.current()?.absoluteString ?? "https://"
        serverField.placeholderString = "https://friday-home.local"
        let explanation = NSTextField(
            wrappingLabelWithString:
                "Yeni adres yerel olarak kaydedilir. Eşleştirmedeki sertifika ve imza anahtarı pinleri korunur; "
                + "pinleri uymayan bir sunucu reddedilir.")
        let grid = NSGridView(views: [[NSTextField(labelWithString: "Sunucu"), serverField]])
        grid.column(at: 1).width = 300
        changeButton.target = self
        changeButton.action = #selector(change)
        changeButton.keyEquivalent = "\r"
        message.lineBreakMode = .byWordWrapping
        message.maximumNumberOfLines = 3
        let stack = NSStackView(views: [explanation, grid, message, changeButton])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.edgeInsets = NSEdgeInsets(top: 16, left: 16, bottom: 16, right: 16)
        window.contentView = stack
        window.center()
    }

    required init?(coder: NSCoder) { nil }

    @objc private func change() {
        let text = serverField.stringValue
        changeButton.isEnabled = false
        message.stringValue = "Denetleniyor…"
        Task {
            let outcome: String
            var changed = false
            do {
                let saved = try await editor.change(to: text)
                outcome = "Sunucu artık \(ServerAddress.displayHost(of: saved) ?? "?") (pinler korundu)."
                changed = true
            } catch ServerAddressError.invalid(let why) {
                outcome = why
            } catch ServerAddressError.notAuthenticated {
                outcome = "Kimlik doğrulanmadı; değişiklik yapılmadı."
            } catch ServerAddressError.identityChanged {
                outcome = "\(ServerAddress.identityChangedMessage). Değişiklik yapılmadı."
            } catch ServerAddressError.unreachable {
                outcome = "Yeni sunucuya ulaşılamadı; değişiklik yapılmadı."
            } catch ServerAddressError.notPaired {
                outcome = "Bu Mac eşleşmemiş; önce eşleştirin."
            } catch {
                outcome = "Adres kaydedilemedi; değişiklik yapılmadı."
            }
            await MainActor.run {
                self.message.stringValue = outcome
                self.changeButton.isEnabled = true
                if changed { self.onChanged() }
            }
        }
    }
}
