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
    /// Optional: the server certificate's SPKI pin (base64 or hex), for a
    /// server this Mac does not trust, such as the loopback proxy
    /// (`make local-tls` prints it). Empty = the system must trust it.
    private let pinField = NSTextField(string: "")
    private let message = NSTextField(labelWithString: "")
    private let pairButton = NSButton(title: "Eşleştir", target: nil, action: nil)

    init(runner: NodeRunner) {
        self.runner = runner
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 480, height: 240), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Friday Node — Eşleştir"
        super.init(window: window)
        let grid = NSGridView(views: [
            [NSTextField(labelWithString: "Sunucu"), serverField],
            [NSTextField(labelWithString: "Eşleştirme kodu"), codeField],
            [NSTextField(labelWithString: "Bu Mac'in adı"), nameField],
            [NSTextField(labelWithString: "Sertifika pini"), pinField],
        ])
        pinField.placeholderString = "isteğe bağlı (make local-tls çıktısı)"
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
        // The same address rules as "Sunucu adresini değiştir" and
        // friday-node-linux `pair` (`check_server_url`).
        let server: URL
        do {
            server = try ServerAddress.check(serverField.stringValue)
        } catch ServerAddressError.invalid(let why) {
            message.stringValue = why
            return
        } catch {
            message.stringValue = "Sunucu adresi https:// ile başlamalı."
            return
        }
        let pinText = pinField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        var pin: Data?
        if !pinText.isEmpty {
            guard let parsed = TLSPin.parse(pinText) else {
                message.stringValue = "Sertifika pini geçersiz (base64 ya da hex, 32 bayt)."
                return
            }
            pin = parsed
        }
        let request = PairingRequest(
            serverURL: server, code: codeField.stringValue, nodeName: nameField.stringValue, expectedPin: pin)
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
                outcome = pin == nil
                    ? "Sunucuya güvenli bağlantı kurulamadı (sertifika sistemce güvenilir olmalı ya da pini girilmeli)."
                    : "Sunucuya bağlanılamadı ya da sertifikası girilen pine uymuyor."
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
