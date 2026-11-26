// The node's version strings for `hello` (schema: node_version and
// os_version are short, printable identifiers).

import Foundation

public enum NodeInfo {
    public static let version = "0.1.0"

    /// `hello.node_version`.
    public static var nodeVersion: String { "friday-node \(version)" }

    /// `hello.os_version`, for example "macOS 27.2".
    public static var osVersion: String {
        let os = ProcessInfo.processInfo.operatingSystemVersion
        return "macOS \(os.majorVersion).\(os.minorVersion)"
    }
}
