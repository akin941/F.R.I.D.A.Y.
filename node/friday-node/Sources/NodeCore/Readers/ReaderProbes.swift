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

/// The owner-turn helpers (OwnerTurns.swift, CodexOwnerTurns.swift).
public enum OwnerTurnProbe {
    /// `parse_instant`: microseconds since the epoch, nil when refused;
    /// `overflow` where the reference's `astimezone` raises.
    public static func parseInstant(_ value: String) -> (micros: Int64?, overflow: Bool) {
        do { return (try OwnerInstant.parse(value), false) } catch { return (nil, true) }
    }

    public static func formatInstant(_ micros: Int64) -> String { OwnerInstant.format(micros) }

    public static func codexTurnID(relative: String, offset: Int) -> String {
        CodexOwnerTurn.turnID(relative: relative, offset: offset)
    }

    /// readers.py `_fit_owner_turns`.
    public static func fit(_ output: JSONValue, limit: Int) throws -> JSONValue {
        try NodeReaders.fitOwnerTurns(output, limit: limit)
    }

    public static func normalisePath(_ path: String) -> String? { OwnerTurnText.normalisePath(path) }
}
