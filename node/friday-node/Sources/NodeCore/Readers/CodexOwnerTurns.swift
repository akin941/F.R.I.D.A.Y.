// `codex.transcripts.owner_turns` (Phase 8B brief D13, D17; runbook B4): a
// port of backend/nodes/reference/owner_turns.py `codex_line_text`,
// `codex_turn_id` and `_codex_turns` over the reader's vetted files
// (`codexFiles`, the same list `codex.threads.*` reads).
//
// The first JSON-object line must be `session_meta`; its payload names the
// thread (`id`, the `session_id`) and its `cwd`. A file whose `cwd` is
// outside the prefixes stops there, before any turn is parsed (rule 2). A
// turn is a `response_item` whose payload is a `message` with
// `role == "user"` and `input_text` parts only, not blank, not starting
// with a Codex marker, with a valid `timestamp`. Its `turn_id` is
// `sha256("<relative>:<offset>")[:32]` in hex, `relative` the file's path
// under the transcript root (`codex/sessions/...`) and `offset` the line's
// byte offset in the file (bytes, every line counted).

import CryptoKit
import Foundation

enum CodexOwnerTurn {
    enum Verdict {
        case turn(String)
        case notResponseItem
        case notMessage
        case notUserRole
        case nonTextContent
        case blank
        case marker(String)
    }

    /// `codex_line_text`, step by step.
    static func verdict(_ line: LooseJSON) -> Verdict {
        guard line["type"]?.string == "response_item" else { return .notResponseItem }
        guard let payload = line["payload"], payload.object != nil, payload["type"]?.string == "message" else {
            return .notMessage
        }
        guard payload["role"]?.string == "user" else { return .notUserRole }
        guard let content = payload["content"]?.array, !content.isEmpty else { return .nonTextContent }
        var parts: [String] = []
        for part in content {
            guard part.object != nil, part["type"]?.string == "input_text", let text = part["text"]?.string else {
                return .nonTextContent
            }
            parts.append(text)
        }
        let text = parts.joined(separator: "\n")
        if OwnerTurnText.isBlank(text) { return .blank }
        if let marker = OwnerTurnText.marker(text, TranscriptText.codexMarkers) { return .marker(marker) }
        return .turn(text)
    }

    /// `codex_turn_id(relative, offset)`.
    static func turnID(relative: String, offset: Int) -> String {
        let digest = SHA256.hash(data: Data("\(relative):\(offset)".utf8))
        return String(digest.map { String(format: "%02x", $0) }.joined().prefix(32))
    }

    /// The `session_meta` line's `(thread_id, cwd)`, or nil (the file is
    /// skipped).
    static func meta(_ line: LooseJSON) -> (String, String)? {
        guard line["type"]?.string == "session_meta", let payload = line["payload"], payload.object != nil,
            let threadID = OwnerTurnText.id(payload["id"]), let cwd = OwnerTurnText.normalisePath(payload["cwd"])
        else { return nil }
        return (threadID, cwd)
    }
}

extension NodeReaders {
    /// `relative` as under a transcript root (`codex/sessions/...`), so the
    /// turn ids equal the fixture form's.
    func codexRelative(_ path: String) -> String {
        "codex/sessions/" + Scalars.dropFirst(path, Scalars.count(codexRoot) + 1)
    }

    /// `_codex_turns` over `codexFiles`; a turn before `since` is dropped as
    /// it streams by (`collect_files`'s comprehension).
    func codexOwnerTurnRows(match: (String) -> String?, since: Int64) throws -> [OwnerTurnRow] {
        var rows: [OwnerTurnRow] = []
        var failure: Error?
        for path in codexFiles {
            let relative = codexRelative(path)
            var thread: (id: String, cwd: String)?
            ReaderFiles.forEachRawLine(path) { offset, value in
                guard let line = value else { return true }
                guard let current = thread else {
                    // The first JSON-object line: the thread, or the file is
                    // skipped before any of its turns is parsed.
                    guard let (threadID, cwd) = CodexOwnerTurn.meta(line), let shown = match(cwd) else { return false }
                    thread = (threadID, shown)
                    return true
                }
                var text: String?
                if case .turn(let value) = CodexOwnerTurn.verdict(line) { text = value }
                do {
                    // The reference parses every line's timestamp, turn or not.
                    guard let at = try OwnerInstant.parse(line["timestamp"]), let text, at >= since else { return true }
                    rows.append(OwnerTurnRow(
                        sessionID: current.id, cwd: current.cwd,
                        turnID: CodexOwnerTurn.turnID(relative: relative, offset: offset), at: at, text: text))
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
