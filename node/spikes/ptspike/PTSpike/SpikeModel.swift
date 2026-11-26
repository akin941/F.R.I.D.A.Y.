import AVFoundation
import AppKit
import ApplicationServices
import CoreGraphics
import Foundation
import Security

struct CheckResult: Identifiable {
    let id: String
    let name: String
    let state: String
    let ok: Bool
}

struct CheckAction {
    let title: String
    let run: () -> Void
}

/// Runs the A1 checks. Nothing here prompts on its own: every request is a button.
/// The launch pass only reads state (and reads the Keychain items back — a prompt
/// at that point is itself the A1.4 failure signal).
@MainActor
final class SpikeModel: ObservableObject {
    @Published private(set) var results: [CheckResult] = []

    let buildVersion = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "?"
    let bundleID = Bundle.main.bundleIdentifier ?? "?"
    let spikeDir = NSHomeDirectory() + "/FridaySpike"
    var statusPath: String { spikeDir + "/status-\(buildVersion).txt" }

    private var lastWrite: [String: String] = [:]

    init() {
        Task { @MainActor in
            await Self.ensureSystemEventsRunning()
            self.refresh()
        }
    }

    // MARK: - Refresh and status file

    func refresh() {
        results = [
            micCheck(),
            axCheck(),
            screenCheck(),
            fdaCheck(),
            automationCheck(),
            keychainCheck(dataProtection: true),
            keychainCheck(dataProtection: false),
        ]
        writeStatusFile()
    }

    private func writeStatusFile() {
        try? FileManager.default.createDirectory(
            atPath: spikeDir, withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700])
        var lines = [
            "PTSpike build=\(buildVersion) marker=\(buildMarker) bundle=\(bundleID)",
            "time=\(ISO8601DateFormatter().string(from: Date()))",
        ]
        lines += results.map { "\($0.ok ? "PASS" : "----") \($0.name): \($0.state)" }
        lines += lastWrite.sorted { $0.key < $1.key }.map { "write \($0.key): \($0.value)" }
        try? (lines.joined(separator: "\n") + "\n")
            .write(toFile: statusPath, atomically: true, encoding: .utf8)
    }

    func actions(for id: String) -> [CheckAction] {
        switch id {
        case "mic":
            return [CheckAction(title: "Request") { [weak self] in
                AVCaptureDevice.requestAccess(for: .audio) { _ in
                    Task { @MainActor in self?.refresh() }
                }
            }]
        case "ax":
            return [
                CheckAction(title: "Prompt") { [weak self] in
                    _ = AXIsProcessTrustedWithOptions(["AXTrustedCheckOptionPrompt": true] as CFDictionary)
                    self?.refresh()
                },
                settingsAction("Privacy_Accessibility"),
            ]
        case "screen":
            return [
                CheckAction(title: "Request") { [weak self] in
                    _ = CGRequestScreenCaptureAccess()
                    self?.refresh()
                },
                settingsAction("Privacy_ScreenCapture"),
            ]
        case "fda":
            return [settingsAction("Privacy_AllFiles")]
        case "automation":
            return [CheckAction(title: "Request") { [weak self] in
                DispatchQueue.global().async {
                    _ = Self.automationStatus(ask: true)
                    Task { @MainActor in self?.refresh() }
                }
            }]
        case "kc-dp":
            return [CheckAction(title: "Write") { [weak self] in self?.writeKeychain(dataProtection: true) }]
        case "kc-file":
            return [CheckAction(title: "Write") { [weak self] in self?.writeKeychain(dataProtection: false) }]
        default:
            return []
        }
    }

    private func settingsAction(_ anchor: String) -> CheckAction {
        CheckAction(title: "Settings") {
            if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?\(anchor)") {
                NSWorkspace.shared.open(url)
            }
        }
    }

    // MARK: - TCC checks (read only)

    private func micCheck() -> CheckResult {
        let status = AVCaptureDevice.authorizationStatus(for: .audio)
        let name: String
        switch status {
        case .authorized: name = "granted"
        case .denied: name = "denied"
        case .restricted: name = "restricted"
        case .notDetermined: name = "not determined"
        @unknown default: name = "unknown (\(status.rawValue))"
        }
        return CheckResult(id: "mic", name: "Microphone", state: name, ok: status == .authorized)
    }

    private func axCheck() -> CheckResult {
        let trusted = AXIsProcessTrusted()
        return CheckResult(id: "ax", name: "Accessibility", state: trusted ? "granted" : "not trusted", ok: trusted)
    }

    private func screenCheck() -> CheckResult {
        let ok = CGPreflightScreenCaptureAccess()
        return CheckResult(id: "screen", name: "Screen Recording", state: ok ? "granted" : "not granted", ok: ok)
    }

    private func fdaCheck() -> CheckResult {
        let home = NSHomeDirectory()
        for rel in ["Library/Safari", "Library/Mail"] {
            let path = home + "/" + rel
            guard FileManager.default.fileExists(atPath: path) else { continue }
            do {
                _ = try FileManager.default.contentsOfDirectory(atPath: path)
                return CheckResult(id: "fda", name: "Full Disk Access", state: "granted (listed ~/\(rel))", ok: true)
            } catch {
                let code = (error as NSError).code
                return CheckResult(id: "fda", name: "Full Disk Access",
                                   state: "denied (~/\(rel), NSError \(code))", ok: false)
            }
        }
        return CheckResult(id: "fda", name: "Full Disk Access", state: "no protected directory to probe", ok: false)
    }

    private func automationCheck() -> CheckResult {
        let status = Self.automationStatus(ask: false)
        let name: String
        switch status {
        case noErr: name = "granted"
        case OSStatus(errAEEventNotPermitted): name = "denied (-1743)"
        case -1744: name = "not determined (-1744, would ask)"
        case OSStatus(procNotFound): name = "System Events not running (-600)"
        default: name = "OSStatus \(status)"
        }
        return CheckResult(id: "automation", name: "Automation (System Events)", state: name, ok: status == noErr)
    }

    nonisolated static func automationStatus(ask: Bool) -> OSStatus {
        let target = NSAppleEventDescriptor(bundleIdentifier: "com.apple.systemevents")
        guard let desc = target.aeDesc else { return OSStatus(paramErr) }
        return AEDeterminePermissionToAutomateTarget(desc, typeWildCard, typeWildCard, ask)
    }

    private static func ensureSystemEventsRunning() async {
        if !NSRunningApplication.runningApplications(withBundleIdentifier: "com.apple.systemevents").isEmpty {
            return
        }
        let config = NSWorkspace.OpenConfiguration()
        config.activates = false
        let url = URL(fileURLWithPath: "/System/Library/CoreServices/System Events.app")
        _ = try? await NSWorkspace.shared.openApplication(at: url, configuration: config)
        try? await Task.sleep(nanoseconds: 500_000_000)
    }

    // MARK: - Keychain (A1.4)

    private static let service = "friday-node-spike"
    private static let account = "spike"

    private static func baseQuery(dataProtection: Bool) -> [String: Any] {
        var query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        if dataProtection {
            query[kSecUseDataProtectionKeychain as String] = true
        }
        return query
    }

    private static func label(_ dataProtection: Bool) -> String {
        dataProtection ? "data-protection" : "file-based"
    }

    private static func describe(_ status: OSStatus) -> String {
        let message = SecCopyErrorMessageString(status, nil) as String? ?? ""
        return "OSStatus \(status) \(message)"
    }

    private func keychainCheck(dataProtection: Bool) -> CheckResult {
        var query = Self.baseQuery(dataProtection: dataProtection)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &out)
        let id = dataProtection ? "kc-dp" : "kc-file"
        let name = "Keychain (\(Self.label(dataProtection)))"
        if status == errSecSuccess, let data = out as? Data {
            let value = String(decoding: data, as: UTF8.self)
            return CheckResult(id: id, name: name, state: "read OK: \(value)", ok: true)
        }
        if status == errSecItemNotFound {
            return CheckResult(id: id, name: name, state: "no item yet (press Write)", ok: false)
        }
        return CheckResult(id: id, name: name, state: "read failed: \(Self.describe(status))", ok: false)
    }

    func writeKeychain(dataProtection: Bool) {
        SecItemDelete(Self.baseQuery(dataProtection: dataProtection) as CFDictionary)
        var query = Self.baseQuery(dataProtection: dataProtection)
        query[kSecValueData as String] = Data("written-by-build-\(buildVersion)".utf8)
        query[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        query[kSecAttrSynchronizable as String] = false
        let status = SecItemAdd(query as CFDictionary, nil)
        lastWrite[Self.label(dataProtection)] = "build \(buildVersion): \(Self.describe(status))"
        refresh()
    }

    func deleteKeychainItems() {
        for dp in [true, false] {
            let status = SecItemDelete(Self.baseQuery(dataProtection: dp) as CFDictionary)
            lastWrite[Self.label(dp)] = "deleted by build \(buildVersion): \(Self.describe(status))"
        }
        refresh()
    }
}
