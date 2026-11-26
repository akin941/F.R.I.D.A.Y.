// `claude.transcripts.owner_turns` (Phase 8B brief D13, D17; runbook B4): a
// port of backend/nodes/reference/owner_turns.py `claude_line_turn` and
// `_claude_turns` over the reader's vetted files (`claudeFiles`, the same
// list `claude.sessions.*` reads).
//
// A line is a turn only if `type == "user"`, `isMeta`, `isSidechain` and
// `isCompactSummary` are not true, `message.role` is absent or "user",
// `message.content` is a string or text blocks only (any other block skips
// the whole line), the text is not blank and does not start with a harness
// marker, and `sessionId`, `uuid` (the `turn_id`), `timestamp` and `cwd` are
// valid. The prefix check runs on each line's own `cwd` before anything
// else of the line is parsed (rule 2).

import Foundation

enum ClaudeOwnerTurn {
    /// Why a line is or is not an owner turn (the debug preview counts
    /// these; the reader only needs `.turn`).
    enum Verdict {
        case turn(sessionID: String, turnID: String, at: Int64, text: String)
        case notUser
        case meta
        case sidechain
        case compactSummary
        case notUserRole
        case nonTextContent
        case blank
        case marker(String)
        case badIdentifier
        case badTimestamp
        case badCwd
    }

    /// `_claude_text`: a string, or text blocks only joined by "\n".
    static func text(_ content: LooseJSON?) -> String? {
        if case .string(let text)? = content { return text }
        guard let blocks = content?.array, !blocks.isEmpty else { return nil }
        var parts: [String] = []
        for block in blocks {
            guard block.object != nil, block["type"]?.string == "text", let text = block["text"]?.string else {
                return nil  // tool_result, image or anything else: skip the line
            }
            parts.append(text)
        }
        return parts.joined(separator: "\n")
    }

    /// `claude_line_turn`, step by step; throws where the reference's time
    /// parsing raises.
    static func verdict(_ line: LooseJSON) throws -> Verdict {
        guard line["type"]?.string == "user" else { return .notUser }
        if line["isMeta"]?.isTrue == true { return .meta }
        if line["isSidechain"]?.isTrue == true { return .sidechain }
        if line["isCompactSummary"]?.isTrue == true { return .compactSummary }
        guard let message = line["message"], message.object != nil else { return .notUserRole }
        if let role = message["role"], role.string != "user" { return .notUserRole }
        guard let text = text(message["content"]) else { return .nonTextContent }
        if OwnerTurnText.isBlank(text) { return .blank }
        if let marker = OwnerTurnText.marker(text, TranscriptText.claudeMarkers) { return .marker(marker) }
        let sessionID = OwnerTurnText.id(line["sessionId"])
        let turnID = OwnerTurnText.id(line["uuid"])
        let at = try OwnerInstant.parse(line["timestamp"])
        let cwd = OwnerTurnText.normalisePath(line["cwd"])
        guard let sessionID, let turnID else { return .badIdentifier }
        guard let at else { return .badTimestamp }
        guard cwd != nil else { return .badCwd }
        return .turn(sessionID: sessionID, turnID: turnID, at: at, text: text)
    }
}

extension NodeReaders {
    /// `_claude_turns` over `claudeFiles`; `match(cwd)` is the cwd to report
    /// or nil outside the prefixes.
    func claudeOwnerTurnRows(match: (String) -> String?) throws -> [OwnerTurnRow] {
        var rows: [OwnerTurnRow] = []
        var failure: Error?
        for path in claudeFiles {
            ReaderFiles.forEachLine(path) { line in
                // Rule 2: the row's cwd first, before anything else of the line.
                guard let cwd = OwnerTurnText.normalisePath(line["cwd"]), let shown = match(cwd) else { return true }
                do {
                    if case .turn(let sessionID, let turnID, let at, let text) = try ClaudeOwnerTurn.verdict(line) {
                        rows.append(OwnerTurnRow(sessionID: sessionID, cwd: shown, turnID: turnID, at: at, text: text))
                    }
                } catch {
                    failure = error
                    return false
                }
                return true
            }
            if let failure { throw failure }
        }
        return rows
    }
}
