// `music.next_track`: no params; a fixed source.

public enum MusicNextTrack: Recipe {
    public static let name = "music.next_track"
    public static let targetBundleID: String? = "com.apple.Music"

    public static func source(params: JSONValue, repoRoot: String?, interpolate: ScriptInterpolator) throws -> String {
        """
        tell application id "com.apple.Music" to next track
        """
    }
}
