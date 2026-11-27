// The app's executor (runbook B6): commands under `srt`, the recipes,
// AX press/type and open/focus, each behind its own availability.
//
// `availability(for:)` is what `hello` (and `grant.availability_changed`)
// advertises, so check 21 refuses before any window opens when it is not
// `granted`:
//
// | capability              | granted only when                                         |
// |-------------------------|-----------------------------------------------------------|
// | repo.command.run        | the configured srt location is usable (pinned 0.0.83, Node >= 22.12); else `unsupported` |
// | applescript.recipe.run  | every enabled recipe's target app has the Automation (TCC) grant; else `not_granted` |
// | ax.press, ax.type_text  | the Accessibility (TCC) grant (`AXIsProcessTrusted`); else `not_granted` |
// | apps.open, apps.focus   | always (NSWorkspace needs no TCC grant)                    |
//
// Which repos, kinds, recipes and bundle ids exist is the local policy
// (check 22); this executor only re-checks what it is handed.

import CoreServices
import Foundation

/// What the TCC checks answer (a seam: tests never ask the real TCC).
public protocol PermissionProbe: AnyObject {
    var accessibilityTrusted: Bool { get }
    /// Whether this app may send Apple Events to `bundleID` (asking the
    /// owner when `ask`), without launching it.
    func automation(_ bundleID: String, ask: Bool) -> AutomationStatus
}

public enum AutomationStatus: Equatable, Sendable {
    case granted, denied, undetermined
    /// The target is not running: TCC cannot be asked without launching it.
    case notRunning
}

/// The real probe. A target that is not running cannot be asked; its last
/// answer in this process is kept (a `granted` seen once stays until a
/// `denied` is seen), and a target never seen granted is not granted.
public final class SystemPermissionProbe: PermissionProbe, @unchecked Sendable {
    private let lock = NSLock()
    private var lastKnown: [String: AutomationStatus] = [:]

    public init() {}

    public var accessibilityTrusted: Bool { SystemAccessibility().trusted }

    public func automation(_ bundleID: String, ask: Bool) -> AutomationStatus {
        var target = AEAddressDesc()
        let bytes = Array(bundleID.utf8)
        let created = bytes.withUnsafeBufferPointer {
            AECreateDesc(DescType(typeApplicationBundleID), $0.baseAddress, $0.count, &target)
        }
        guard created == noErr else { return .denied }
        defer { AEDisposeDesc(&target) }
        let status = AEDeterminePermissionToAutomateTarget(&target, AEEventClass(typeWildCard),
                                                           AEEventID(typeWildCard), ask)
        let answer: AutomationStatus
        switch status {
        case noErr: answer = .granted
        case OSStatus(errAEEventNotPermitted): answer = .denied
        case OSStatus(errAEEventWouldRequireUserConsent): answer = .undetermined
        case OSStatus(procNotFound): answer = .notRunning
        default: answer = .denied
        }
        lock.lock()
        defer { lock.unlock() }
        if answer == .notRunning { return lastKnown[bundleID] ?? .notRunning }
        lastKnown[bundleID] = answer
        return answer
    }
}

public final class MacActionExecutor: ActionExecutor {
    public let srt: SrtExecutor
    public let bundleIDs: Set<String>
    public let recipesEnabled: Set<String>
    private let permissions: PermissionProbe
    private let accessibility: AccessibilityDriver
    private let workspace: WorkspaceDriver
    private let scripts: AppleScriptRunner
    private let interpolator: ScriptInterpolator

    public init(
        srt: SrtExecutor = SrtExecutor(), bundleIDs: Set<String> = [], recipesEnabled: Set<String> = [],
        permissions: PermissionProbe = SystemPermissionProbe(), accessibility: AccessibilityDriver = SystemAccessibility(),
        workspace: WorkspaceDriver = SystemWorkspace(), scripts: AppleScriptRunner = OsascriptRunner(),
        interpolator: ScriptInterpolator = AppleScriptLiteral()
    ) {
        self.srt = srt
        self.bundleIDs = bundleIDs
        self.recipesEnabled = recipesEnabled
        self.permissions = permissions
        self.accessibility = accessibility
        self.workspace = workspace
        self.scripts = scripts
        self.interpolator = interpolator
    }

    /// The Automation targets of the enabled recipes.
    public var automationTargets: [String] {
        Array(Set(recipesEnabled.compactMap { RecipeBook.recipe(named: $0)?.targetBundleID })).sorted()
    }

    public func availability(for capability: String) -> String {
        switch capability {
        case ActionContracts.commandCapability:
            return srt.available ? "granted" : "unsupported"
        case ActionContracts.recipeCapability:
            let allowed = automationTargets.allSatisfy { permissions.automation($0, ask: false) == .granted }
            return allowed ? "granted" : "not_granted"
        case "ax.press", "ax.type_text":
            return permissions.accessibilityTrusted ? "granted" : "not_granted"
        case "apps.open", "apps.focus":
            return "granted"
        default:
            return "unsupported"
        }
    }

    public func start(_ spec: CommandSpec) throws -> RunningCommand {
        try srt.start(spec)
    }

    public func perform(_ action: ActionRequest) -> ActionOutcome {
        do {
            switch action.capability {
            case "apps.open":
                try OpenFocus.open(bundleID: action.bundleID ?? "", allowed: bundleIDs, driver: workspace)
            case "apps.focus":
                try OpenFocus.focus(bundleID: action.bundleID ?? "", allowed: bundleIDs, driver: workspace)
            case "ax.press":
                guard bundleIDs.contains(action.bundleID ?? "") else { throw OpenFocusError.notOnList }
                try AXPress.perform(args: action.args, driver: accessibility)
            case "ax.type_text":
                guard bundleIDs.contains(action.bundleID ?? "") else { throw OpenFocusError.notOnList }
                try AXTypeText.perform(args: action.args, driver: accessibility)
            case ActionContracts.recipeCapability:
                guard let name = action.recipe, recipesEnabled.contains(name) else {
                    throw RecipeError("recipe not enabled")
                }
                let source = try RecipeBook.render(
                    name, params: action.args["params"] ?? [:], repoRoot: action.repoRoot, interpolate: interpolator)
                let timeout = Double(CapabilityContract.named(ActionContracts.recipeCapability)?.maxRuntimeSeconds ?? 30)
                guard scripts.run(source: source, timeout: timeout) else { throw RecipeError("the script failed") }
            default:
                return ActionOutcome(ok: false, error: "unsupported")
            }
            return ActionOutcome(ok: true)
        } catch let refusal as AXRefusal {
            return ActionOutcome(ok: false, error: refusal.rawValue)
        } catch let refusal as OpenFocusError {
            return ActionOutcome(ok: false, error: refusal.rawValue)
        } catch {
            return ActionOutcome(ok: false, error: "failed")
        }
    }
}
