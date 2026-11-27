// `music.play_pause`: no params; a fixed source.

public enum MusicPlayPause: Recipe {
    public static let name = "music.play_pause"
    public static let targetBundleID: String? = "com.apple.Music"

    public static func source(params: JSONValue, repoRoot: String?, interpolate: ScriptInterpolator) throws -> String {
        """
        tell application id "com.apple.Music" to playpause
        """
    }
}
