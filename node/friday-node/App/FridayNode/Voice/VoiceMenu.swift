// The "Ses" menu (Phase 11 Mac session M1; runbook B7): mic mode (off /
// push-to-talk / always-on; always-on asks for Touch ID or the password
// first), the software mute, the push-to-talk hotkey, and the wake model
// state (always-on stays push-to-talk until every wake model, the
// owner-trained `hey_friday.onnx` included, matches a non-empty pin: runbook
// A8). Every change goes through VoiceSettingsEditor; nothing here talks to
// the child or the server directly.

import AppKit
import NodeCore

final class VoiceMenu: NSObject {
    private let editor: VoiceSettingsEditor
    private let onFailure: (String) -> Void

    init(editor: VoiceSettingsEditor, onFailure: @escaping (String) -> Void) {
        self.editor = editor
        self.onFailure = onFailure
    }

    func build(into menu: NSMenu) {
        menu.removeAllItems()
        menu.autoenablesItems = false
        let settings: VoiceSettings
        do {
            settings = try editor.current()
        } catch {
            let item = NSMenuItem(title: "Ses ayarları okunamadı (mikrofon sessiz)", action: nil, keyEquivalent: "")
            item.isEnabled = false
            menu.addItem(item)
            return
        }
        let modes: [(String, String)] = [
            ("off", "Kapalı"), ("push_to_talk", "Bas-konuş"), ("always_on", "Sürekli dinleme (Touch ID ile)"),
        ]
        for (mode, title) in modes {
            let item = NSMenuItem(title: title, action: #selector(setMode(_:)), keyEquivalent: "")
            item.representedObject = mode
            item.state = settings.mode == mode ? .on : .off
            item.target = self
            menu.addItem(item)
        }
        let armable = WakeModels.armable()
        let wake = NSMenuItem(
            title: armable
                ? "Uyandırma modelleri doğrulandı"
                : "Uyandırma modeli hazır değil (hey_friday.onnx sabitlenmedi): yalnızca bas-konuş",
            action: nil, keyEquivalent: "")
        wake.isEnabled = false
        menu.addItem(wake)
        menu.addItem(.separator())
        let mute = NSMenuItem(title: "Sessize al", action: #selector(toggleMute), keyEquivalent: "")
        mute.state = settings.muted ? .on : .off
        mute.target = self
        menu.addItem(mute)
        menu.addItem(.separator())
        let header = NSMenuItem(title: "Bas-konuş tuşu", action: nil, keyEquivalent: "")
        header.isEnabled = false
        menu.addItem(header)
        for choice in VoiceHotkeys.choices {
            let item = NSMenuItem(title: choice.label, action: #selector(setHotkey(_:)), keyEquivalent: "")
            item.representedObject = choice.id
            item.state = settings.hotkey == choice.id ? .on : .off
            item.target = self
            menu.addItem(item)
        }
    }

    @objc private func setMode(_ sender: NSMenuItem) {
        guard let mode = sender.representedObject as? String else { return }
        Task {
            do {
                try await editor.setMode(mode)
            } catch PolicyChangeError.notAuthenticated {
                // Cancelled or failed authentication: nothing changed.
            } catch {
                await MainActor.run { self.onFailure("ses modu kaydedilemedi") }
            }
        }
    }

    @objc private func toggleMute() {
        do {
            let current = try editor.current()
            try editor.setMuted(!current.muted)
        } catch {
            onFailure("sessize alma kaydedilemedi")
        }
    }

    @objc private func setHotkey(_ sender: NSMenuItem) {
        guard let id = sender.representedObject as? String else { return }
        do {
            try editor.setHotkey(id)
        } catch {
            onFailure("tuş kaydedilemedi")
        }
    }
}
