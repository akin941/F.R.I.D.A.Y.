// `terminal.open_repo`: opens a mapped repo in a new Terminal window. The
// path (the realpath check 22 resolved `repo_path` to) is an AppleScript
// string literal, and reaches Terminal's shell only through AppleScript's
// own `quoted form of` (single-quoted for the shell), never spliced into
// the command text.

public enum TerminalOpenRepo: Recipe {
    public static let name = "terminal.open_repo"
    public static let targetBundleID: String? = "com.apple.Terminal"

    public static func source(params: JSONValue, repoRoot: String?, interpolate: ScriptInterpolator) throws -> String {
        guard let repoRoot else { throw RecipeError("no repo") }
        let path = try interpolate.literal(repoRoot)
        return """
            set repoPath to \(path)
            tell application id "com.apple.Terminal"
            \tdo script "cd " & quoted form of repoPath
            \tactivate
            end tell
            """
    }
}
