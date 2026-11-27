// `system.volume_set`: `level` 0...100 (the schema's bounds, checked again
// here) enters the source only as a string literal coerced in the script.
// `set volume` is a Standard Additions command: no Apple Event to another
// app, so no Automation grant.

public enum SystemVolumeSet: Recipe {
    public static let name = "system.volume_set"
    public static let targetBundleID: String? = nil

    public static func source(params: JSONValue, repoRoot: String?, interpolate: ScriptInterpolator) throws -> String {
        guard case .int(let level)? = params["level"], (0...100).contains(level) else {
            throw RecipeError("level outside 0...100")
        }
        let literal = try interpolate.literal(String(level))
        return """
            set volume output volume (\(literal) as integer)
            """
    }
}
