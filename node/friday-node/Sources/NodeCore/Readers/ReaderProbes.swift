// Read-only entry points to the reader helpers for NodeCoreTests, which
// also run in the Release configuration (no @testable import there).
// They expose pure functions only: no state, no file content beyond what a
// reader already returns.

import Foundation

public enum ReaderPathsProbe {
    public static func normalise(_ path: String) -> String? { ReaderPaths.normalise(path) }

    public static func forms(_ path: String, home: String) -> (display: String, forms: [String])? {
        ReaderPaths.pathForms(path, home: home)
    }

    public static func matches(_ prefixes: [String]?, _ forms: [String]) -> Bool {
        ReaderPaths.matchesPrefixes(prefixes, forms)
    }
}

public enum ReaderTimeProbe {
    public static func parse(_ value: String) -> Int64? { ReaderTime.parse(value) }
}

public enum ReaderMoneyProbe {
    public static func micros(number literal: String) -> Int64 { ReaderMoney.micros(.number(literal)) }
    public static func micros(string literal: String) -> Int64 { ReaderMoney.micros(.string(literal)) }
}

public enum ReaderFilesProbe {
    public static func transcripts(_ root: String, recursive: Bool) -> [String] {
        ReaderFiles.transcripts(root: root, recursive: recursive)
    }
}
