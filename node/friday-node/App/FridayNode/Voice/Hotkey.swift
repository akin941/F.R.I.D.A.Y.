// The global push-to-talk hotkey (Phase 11 Mac session M1; runbook B7):
// Carbon `RegisterEventHotKey`, which delivers both the press and the
// release of a fixed key combination to this app while any app is in front,
// and needs no Accessibility grant (it never sees other keystrokes). The
// combination is one of `VoiceHotkeys.choices`, chosen in the node's "Ses"
// menu (VoiceSettings); press = `ptt: down`, release = `ptt: up`.

import Carbon.HIToolbox
import Foundation
import NodeCore

final class PushToTalkHotkey {
    /// true on press, false on release; on the main thread.
    var onChange: ((Bool) -> Void)?
    private var hotKey: EventHotKeyRef?
    private var handler: EventHandlerRef?
    private var pressed = false
    private static let signature: OSType = 0x4652_4459  // "FRDY"

    deinit {
        unregister()
        if let handler { RemoveEventHandler(handler) }
    }

    func register(_ choice: VoiceHotkeys.Choice) {
        unregister()
        installHandlerOnce()
        let id = EventHotKeyID(signature: Self.signature, id: 1)
        var ref: EventHotKeyRef?
        let status = RegisterEventHotKey(choice.keyCode, choice.modifiers, id, GetApplicationEventTarget(), 0, &ref)
        hotKey = status == noErr ? ref : nil
    }

    func unregister() {
        if let hotKey { UnregisterEventHotKey(hotKey) }
        hotKey = nil
        if pressed {
            pressed = false
            onChange?(false)
        }
    }

    var isRegistered: Bool { hotKey != nil }

    private func installHandlerOnce() {
        guard handler == nil else { return }
        var specs = [
            EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed)),
            EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyReleased)),
        ]
        let context = Unmanaged.passUnretained(self).toOpaque()
        InstallEventHandler(
            GetApplicationEventTarget(),
            { _, event, context in
                guard let event, let context else { return OSStatus(eventNotHandledErr) }
                let hotkey = Unmanaged<PushToTalkHotkey>.fromOpaque(context).takeUnretainedValue()
                let down = GetEventKind(event) == UInt32(kEventHotKeyPressed)
                DispatchQueue.main.async { hotkey.changed(down) }
                return noErr
            }, specs.count, &specs, context, &handler)
    }

    private func changed(_ down: Bool) {
        guard down != pressed else { return }
        pressed = down
        onChange?(down)
    }
}
