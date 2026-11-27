// `finder.reveal_repo`: shows a mapped repo in Finder. The path is the
// realpath check 22 resolved `repo_path` to (it equals a mapped root), as an
// AppleScript string literal.

public enum FinderRevealRepo: Recipe {
    public static let name = "finder.reveal_repo"
    public static let targetBundleID: String? = "com.apple.finder"

    public static func source(params: JSONValue, repoRoot: String?, interpolate: ScriptInterpolator) throws -> String {
        guard let repoRoot else { throw RecipeError("no repo") }
        let path = try interpolate.literal(repoRoot)
        return """
            tell application id "com.apple.finder"
            \treveal (POSIX file \(path) as alias)
            \tactivate
            end tell
            """
    }
}
