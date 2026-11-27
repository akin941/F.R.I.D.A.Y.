// The voice settings in the node's local UI (Phase 11 brief D6, D11, D12;
// ADR-013 decision 5; ADR-015 decision 3; Mac session M1).
//
// - Mic mode: off / push-to-talk / always-on; the push-to-talk hotkey; the
//   software mute. Stored in one Keychain item (account `voice-settings`,
//   the data protection keychain like the credential): no user default, no
//   environment variable and no protocol message can change them, and no
//   child message names them (D6). The server can only narrow the mode
//   (the always-on gate), never widen it.
// - Changing the mode TO always-on asks for LocalAuthentication first
//   (ADR-013 decision 5); narrowing (off, push-to-talk) and muting never
//   wait for it.
// - Mute fails closed (ADR-015 decision 3): when the stored settings cannot
//   be read, or the default input device reports a mute property it then
//   fails to read, the host sends `mute: unreadable` (the child releases and
//   the indicator reads `muted`). A device that has no mute property
//   (most built-in microphones) is not consulted.
// - Wake mode needs every pinned wake model (melspectrogram, embedding and
//   the owner-trained keyword `hey_friday.onnx`) present and matching a
//   non-empty pin in the installed `models.json` (the child's own rule,
//   friday_voice/models.py `wake_armable`); `WakeModels.status` shows it in
//   the menu. Until the keyword model is pinned (runbook A8) always-on runs
//   as push-to-talk: the child refuses to arm.

import CoreAudio
import Foundation
import Security

public struct VoiceSettings: Equatable, Sendable {
    public var mode: String
    public var muted: Bool
    public var hotkey: String

    public static let modes = VoiceProtocol.modes
    public static let defaultHotkey = "option_command_space"

    public init(mode: String = "off", muted: Bool = false, hotkey: String = VoiceSettings.defaultHotkey) {
        self.mode = mode
        self.muted = muted
        self.hotkey = hotkey
    }

    public var document: JSONValue {
        ["v": 1, "mode": .string(mode), "muted": .bool(muted), "hotkey": .string(hotkey)]
    }

    public static func decoded(_ data: Data) throws -> VoiceSettings {
        let doc = try JSON.parse(data)
        guard doc["v"] == 1, let mode = doc["mode"]?.stringValue, modes.contains(mode),
            let muted = doc["muted"]?.boolValue, let hotkey = doc["hotkey"]?.stringValue,
            VoiceHotkeys.byID[hotkey] != nil
        else { throw CredentialError.corrupt }
        return VoiceSettings(mode: mode, muted: muted, hotkey: hotkey)
    }
}

/// The push-to-talk hotkeys the node offers (Carbon virtual key codes and
/// modifier masks; the app registers the chosen one with
/// RegisterEventHotKey, which needs no Accessibility grant).
public enum VoiceHotkeys {
    public struct Choice: Equatable, Sendable {
        public let id: String
        public let label: String
        public let keyCode: UInt32
        /// Carbon modifiers: cmdKey 0x100, shiftKey 0x200, optionKey 0x800,
        /// controlKey 0x1000.
        public let modifiers: UInt32
    }

    public static let choices: [Choice] = [
        Choice(id: "option_command_space", label: "⌥⌘ Boşluk", keyCode: 49, modifiers: 0x800 | 0x100),
        Choice(id: "control_option_space", label: "⌃⌥ Boşluk", keyCode: 49, modifiers: 0x1000 | 0x800),
        Choice(id: "f5", label: "F5", keyCode: 96, modifiers: 0),
        Choice(id: "control_option_f", label: "⌃⌥ F", keyCode: 3, modifiers: 0x1000 | 0x800),
    ]

    public static let byID: [String: Choice] = Dictionary(uniqueKeysWithValues: choices.map { ($0.id, $0) })
}

public protocol VoiceSettingsStore: AnyObject {
    /// The stored settings, or the default (mode off) when none are.
    func load() throws -> VoiceSettings
    func save(_ settings: VoiceSettings) throws
}

public final class MemoryVoiceSettingsStore: VoiceSettingsStore {
    private var settings: VoiceSettings
    public var failLoad = false
    public init(_ settings: VoiceSettings = VoiceSettings()) { self.settings = settings }
    public func load() throws -> VoiceSettings {
        if failLoad { throw CredentialError.keychain(-1) }
        return settings
    }
    public func save(_ settings: VoiceSettings) throws { self.settings = settings }
}

public final class KeychainVoiceSettingsStore: VoiceSettingsStore {
    public static let account = "voice-settings"

    public init() {}

    private func baseQuery() -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: KeychainCredentialStore.service,
            kSecAttrAccount as String: Self.account,
            kSecUseDataProtectionKeychain as String: true,
            kSecAttrSynchronizable as String: false,
        ]
    }

    public func load() throws -> VoiceSettings {
        var query = baseQuery()
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &out)
        if status == errSecItemNotFound { return VoiceSettings() }
        guard status == errSecSuccess, let data = out as? Data else { throw CredentialError.keychain(status) }
        return try VoiceSettings.decoded(data)
    }

    public func save(_ settings: VoiceSettings) throws {
        let data = try JSON.canonical(settings.document)
        let update = SecItemUpdate(baseQuery() as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        if update == errSecSuccess { return }
        guard update == errSecItemNotFound else { throw CredentialError.keychain(update) }
        var add = baseQuery()
        add[kSecValueData as String] = data
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let status = SecItemAdd(add as CFDictionary, nil)
        guard status == errSecSuccess else { throw CredentialError.keychain(status) }
    }
}

/// The input device's own mute switch, where it has one.
public protocol InputMuteSource: AnyObject {
    /// nil: the device has no mute property (not consulted); throws when it
    /// has one that cannot be read.
    func deviceMuted() throws -> Bool?
}

public struct InputMuteUnreadable: Error {
    public init() {}
}

public final class NoInputMute: InputMuteSource {
    public init() {}
    public func deviceMuted() throws -> Bool? { nil }
}

/// CoreAudio: the default input device's `kAudioDevicePropertyMute` on the
/// input scope (read only; nothing is changed).
public final class CoreAudioInputMute: InputMuteSource {
    public init() {}

    public func deviceMuted() throws -> Bool? {
        var device = AudioObjectID(kAudioObjectUnknown)
        var size = UInt32(MemoryLayout<AudioObjectID>.size)
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyDefaultInputDevice, mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain)
        guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &device)
            == noErr
        else { throw InputMuteUnreadable() }
        if device == kAudioObjectUnknown { return nil }  // no input device: nothing to read
        var mute = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyMute, mScope: kAudioDevicePropertyScopeInput,
            mElement: kAudioObjectPropertyElementMain)
        guard AudioObjectHasProperty(device, &mute) else { return nil }
        var value: UInt32 = 0
        size = UInt32(MemoryLayout<UInt32>.size)
        guard AudioObjectGetPropertyData(device, &mute, 0, nil, &size, &value) == noErr else {
            throw InputMuteUnreadable()
        }
        return value != 0
    }
}

public enum VoiceMute {
    /// What the host sends as `mute`: the stored software mute and the
    /// device's switch; any read failure is `unreadable` (fail closed).
    public static func read(store: VoiceSettingsStore, device: InputMuteSource) -> VoiceHostCore.Muted {
        guard let settings = try? store.load() else { return .unreadable }
        if settings.muted { return .yes }
        do {
            return try device.deviceMuted() == true ? .yes : .no
        } catch {
            return .unreadable
        }
    }
}

/// Changes the settings; widening to always-on only after the owner
/// authenticates (the PolicyEditor pattern).
public final class VoiceSettingsEditor: @unchecked Sendable {
    private let store: VoiceSettingsStore
    private let authentication: OwnerAuthentication
    private let lock = NSLock()
    public var onChange: ((VoiceSettings) -> Void)?

    public init(store: VoiceSettingsStore, authentication: OwnerAuthentication) {
        self.store = store
        self.authentication = authentication
    }

    public func current() throws -> VoiceSettings { try store.load() }

    @discardableResult
    public func setMode(_ mode: String) async throws -> VoiceSettings {
        guard VoiceSettings.modes.contains(mode) else { throw CredentialError.corrupt }
        if mode == "always_on" {
            guard await authentication.authenticate(reason: "Friday Node'un mikrofonunu sürekli dinlemeye aç")
            else { throw PolicyChangeError.notAuthenticated }
        }
        return try apply { $0.mode = mode }
    }

    @discardableResult
    public func setMuted(_ muted: Bool) throws -> VoiceSettings { try apply { $0.muted = muted } }

    @discardableResult
    public func setHotkey(_ id: String) throws -> VoiceSettings {
        guard VoiceHotkeys.byID[id] != nil else { throw CredentialError.corrupt }
        return try apply { $0.hotkey = id }
    }

    private func apply(_ change: (inout VoiceSettings) -> Void) throws -> VoiceSettings {
        lock.lock()
        var settings = try store.load()
        change(&settings)
        do {
            try store.save(settings)
        } catch {
            lock.unlock()
            throw error
        }
        lock.unlock()
        onChange?(settings)
        return settings
    }
}

/// friday_voice/models.py `wake_armable` over the installed manifest.
public enum WakeModels {
    public static let wakeRoles = ["melspectrogram", "embedding", "keyword"]

    /// {role: ok | missing | mismatch | unpinned}; nil when the manifest is
    /// missing or unreadable.
    public static func status(home: String = VoiceChildHost.Install.defaultHome) -> [String: String]? {
        guard let data = FileManager.default.contents(atPath: home + "/models.json"),
            let manifest = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let models = manifest["models"] as? [[String: Any]], let dirText = manifest["modelDir"] as? String
        else { return nil }
        let directory = (dirText as NSString).expandingTildeInPath
        var result: [String: String] = [:]
        for role in wakeRoles {
            guard let entry = models.first(where: { $0["role"] as? String == role }),
                let file = entry["file"] as? String, !file.contains("/")
            else {
                result[role] = "missing"
                continue
            }
            let pin = entry["sha256"] as? String ?? ""
            let path = (directory as NSString).appendingPathComponent(file)
            if pin.isEmpty {
                result[role] = "unpinned"
            } else if let bytes = FileManager.default.contents(atPath: path) {
                result[role] = JSON.hexDigest(bytes) == pin ? "ok" : "mismatch"
            } else {
                result[role] = "missing"
            }
        }
        return result
    }

    public static func armable(home: String = VoiceChildHost.Install.defaultHome) -> Bool {
        guard let status = status(home: home) else { return false }
        return wakeRoles.allSatisfy { status[$0] == "ok" }
    }
}
