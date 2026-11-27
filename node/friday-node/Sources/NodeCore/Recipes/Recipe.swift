// The AppleScript recipes (Phase 10 brief D3, M2; runbook B6). The server
// sends a recipe name and typed params (`applescript.recipe.run`); the
// AppleScript text lives only here, one file per recipe of
// backend/nodes/capabilities/v2/recipes/, each a fixed source.
//
// - Params are validated again against the recipe's own `params_schema`
//   (the vendored contract) before anything is rendered: a refused value
//   never reaches interpolation.
// - A value enters a source only as an AppleScript string literal
//   (`AppleScriptLiteral`): `\` and `"` escaped, and any control character
//   (C0, DEL, C1, U+2028, U+2029) refused outright, so no value can end the
//   literal, continue the statement or start a new line. A number is a
//   literal too, coerced inside the script (`("50" as integer)`).
// - The source runs through `/usr/bin/osascript -e <source>` (an argv, no
//   shell; ChildProcess with a timeout, registered so the kill switch can
//   stop it). Not NSAppleScript in-process: it must run on the main thread,
//   and the session queue never waits on the main thread.
// - Automation (TCC) prompts come per target app on the first run, or when
//   the owner asks for them in the policy UI; `availability` is `granted`
//   only when every enabled recipe's target app is already allowed.

import Foundation

/// Turns one value into an AppleScript string literal.
public protocol ScriptInterpolator {
    func literal(_ value: String) throws -> String
}

public struct RecipeError: Error, Equatable, CustomStringConvertible {
    public let description: String
    init(_ description: String) { self.description = description }
}

/// `"..."` with `\` and `"` escaped; control characters refused.
public struct AppleScriptLiteral: ScriptInterpolator {
    public init() {}

    public static func isForbidden(_ scalar: Unicode.Scalar) -> Bool {
        let value = scalar.value
        return value < 0x20 || (0x7F...0x9F).contains(value) || value == 0x2028 || value == 0x2029
    }

    public func literal(_ value: String) throws -> String {
        var out = "\""
        for scalar in value.unicodeScalars {
            if Self.isForbidden(scalar) { throw RecipeError("a control character in a recipe value") }
            switch scalar {
            case "\\": out += "\\\\"
            case "\"": out += "\\\""
            default: out.unicodeScalars.append(scalar)
            }
        }
        return out + "\""
    }
}

/// One recipe: its name (the contract's) and its fixed source.
public protocol Recipe {
    static var name: String { get }
    /// The app the script sends Apple Events to (its Automation grant), nil
    /// for a recipe that needs none.
    static var targetBundleID: String? { get }
    /// The fixed source with the validated params as literals. `repoRoot` is
    /// what check 22 resolved a repo recipe's `repo_path` to.
    static func source(params: JSONValue, repoRoot: String?, interpolate: ScriptInterpolator) throws -> String
}

public enum RecipeBook {
    public static let recipes: [Recipe.Type] = [
        FinderRevealRepo.self, MusicNextTrack.self, MusicPlayPause.self, SystemVolumeSet.self,
        TerminalOpenRepo.self,
    ]

    public static func recipe(named name: String) -> Recipe.Type? {
        recipes.first { $0.name == name }
    }

    /// The rendered source of `name` for `params`: the params are checked
    /// against the vendored `params_schema` first, so a refused value never
    /// reaches `interpolate`.
    public static func render(
        _ name: String, params: JSONValue, repoRoot: String?, interpolate: ScriptInterpolator = AppleScriptLiteral()
    ) throws -> String {
        guard let recipe = recipe(named: name), let contract = RecipeContract.all[name] else {
            throw RecipeError("unknown recipe")
        }
        guard Schema.isValid(params, against: contract.paramsSchema) else { throw RecipeError("params refused") }
        if contract.isRepoRecipe && repoRoot == nil { throw RecipeError("repo recipe without a resolved repo") }
        return try recipe.source(params: params, repoRoot: repoRoot, interpolate: interpolate)
    }
}

/// Runs a rendered AppleScript source.
public protocol AppleScriptRunner: AnyObject {
    /// True when the script ran to completion.
    func run(source: String, timeout: Double) -> Bool
}

/// `/usr/bin/osascript -e <source>`, empty environment, a timeout.
public final class OsascriptRunner: AppleScriptRunner {
    public static let executable = "/usr/bin/osascript"

    public init() {}

    public func run(source: String, timeout: Double) -> Bool {
        guard let outcome = try? BoundedRun.run(
            executable: Self.executable, argv: ["osascript", "-e", source], environment: ["LANG": "en_US.UTF-8"],
            timeout: timeout)
        else { return false }
        return !outcome.timedOut && outcome.exitCode == 0
    }
}
