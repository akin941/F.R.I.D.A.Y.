// The pairing window (Phase 9 brief D4, M1): the server's HTTPS address, a
// node pairing code from /devices ("Mac düğümü eşle") or `manage.py
// issue_pairing_code --node --strong`, and the node's display name. The code
// is used once and never stored; the credential is saved to the Keychain
// before the window reports success.

import AppKit
import NodeCore

final class PairingWindowController: NSWindowController {
    private let runner: NodeRunner
    private let serverField = NSTextField(string: "https://")
    private let codeField = NSSecureTextField(string: "")
    private let nameField = NSTextField(string: Host.current().localizedName ?? "Mac")
    private let message = NSTextField(labelWithString: "")
    private let pairButton = NSButton(title: "Eşleştir", target: nil, action: nil)

    init(runner: NodeRunner) {
        self.runner = runner
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 440, height: 210), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Friday Node — Eşleştir"
        super.init(window: window)
        let grid = NSGridView(views: [
            [NSTextField(labelWithString: "Sunucu"), serverField],
            [NSTextField(labelWithString: "Eşleştirme kodu"), codeField],
            [NSTextField(labelWithString: "Bu Mac'in adı"), nameField],
        ])
        grid.column(at: 1).width = 280
        pairButton.target = self
        pairButton.action = #selector(pair)
        pairButton.keyEquivalent = "\r"
        message.lineBreakMode = .byWordWrapping
        message.maximumNumberOfLines = 3
        let stack = NSStackView(views: [grid, message, pairButton])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.edgeInsets = NSEdgeInsets(top: 16, left: 16, bottom: 16, right: 16)
        window.contentView = stack
        window.center()
    }

    required init?(coder: NSCoder) { nil }

    @objc private func pair() {
        guard let server = URL(string: serverField.stringValue.trimmingCharacters(in: .whitespaces)),
            server.scheme == "https", server.host != nil
        else {
            message.stringValue = "Sunucu adresi https:// ile başlamalı."
            return
        }
        let request = PairingRequest(serverURL: server, code: codeField.stringValue, nodeName: nameField.stringValue)
        pairButton.isEnabled = false
        message.stringValue = "Eşleştiriliyor…"
        Task {
            let outcome: String
            do {
                let credential = try await Pairing.pair(request, now: Int64(Date().timeIntervalSince1970))
                try runner.adopt(credential)
                outcome = "Eşleşti. Pencereyi kapatabilirsiniz."
            } catch NodeHTTPError.status(let code, let reason) {
                outcome = "Sunucu reddetti (\(code)\(reason.map { ", \($0)" } ?? ""))."
            } catch NodeHTTPError.transport {
                outcome = "Sunucuya güvenli bağlantı kurulamadı (sertifika sistemce güvenilir olmalı)."
            } catch {
                outcome = "Eşleştirme başarısız: \(error)"
            }
            await MainActor.run {
                self.codeField.stringValue = ""
                self.message.stringValue = outcome
                self.pairButton.isEnabled = true
            }
        }
    }
}
