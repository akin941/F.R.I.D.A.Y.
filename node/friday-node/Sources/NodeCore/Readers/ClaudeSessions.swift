// `claude.sessions.list` / `claude.sessions.read`, and the transcript walk
// `usage.ccusage` shares (backend/nodes/reference/readers.py
// `_claude_transcript`, `_list_rows`, `_claude_sessions_*`).
//
// Claude Code JSONL under `~/.claude/projects/*/<session_id>.jsonl`:
// user/assistant text only; tool calls and results counted, never
// returned; meta, sidechain and compact-summary lines and harness marker
// text skipped; a transcript outside `cwd_prefixes` is skipped as soon as
// its `cwd` is known.

import Foundation

struct Transcript {
    var ident: String
    var cwd: String
    var forms: [String]
    var startedAt: Int64?
    var lastActivityAt: Int64?
    var messageCount: Int64 = 0
    var model: String?
    var turns: [[String: JSONValue]] = []
    var usage: [(at: Int64, tokensIn: Int64, tokensOut: Int64, micros: Int64)] = []
}

enum TranscriptText {
    static let claudeMarkers = [
        "<command-name>", "<command-message>", "<command-args>", "<local-command-stdout>",
        "<local-command-stderr>", "<system-reminder>", "<user-prompt-submit-hook>",
        "Caveat: The messages below were generated", "[Request interrupted",
    ]
    static let codexMarkers = ["<environment_context>", "<user_instructions>", "<user_shell_command>"]

    /// `text.lstrip().startswith(markers)` with Python's whitespace.
    static func startsWithMarker(_ text: String, _ markers: [String]) -> Bool {
        let stripped = String(String.UnicodeScalarView(text.unicodeScalars.drop(while: PythonChars.isSpace)))
        return markers.contains { Scalars.hasPrefix(stripped, $0) }
    }

    /// `(text, tool_calls, tool_results)` of a Claude message's content.
    static func claudeContent(_ content: LooseJSON?) -> (String, Int64, Int64) {
        if case .string(let text)? = content { return (text, 0, 0) }
        guard let blocks = content?.array else { return ("", 0, 0) }
        var texts: [String] = []
        var calls: Int64 = 0
        var results: Int64 = 0
        for block in blocks {
            guard block.object != nil else { continue }
            switch block["type"]?.string {
            case "text": if let text = block["text"]?.string { texts.append(text) }
            case "tool_use": calls += 1
            case "tool_result": results += 1
            default: break
            }
        }
        return (texts.filter { !$0.isEmpty }.joined(separator: "\n"), calls, results)
    }

    static func codexText(_ content: LooseJSON?, role: String) -> String {
        guard let parts = content?.array else { return "" }
        let wanted = role == "user" ? "input_text" : "output_text"
        return parts.compactMap { part -> String? in
            guard part.object != nil, part["type"]?.string == wanted else { return nil }
            return part["text"]?.string
        }.joined(separator: "\n")
    }
}

extension NodeReaders {
    var claudeFiles: [String] { ReaderFiles.transcripts(root: claudeRoot, recursive: false) }

    /// pathlib's `stem` of a `*.jsonl` name (a bare ".jsonl" has no suffix).
    static func stem(_ path: String) -> String {
        let name = (path as NSString).lastPathComponent
        if Scalars.equal(name, ".jsonl") { return name }
        return Scalars.prefix(name, Scalars.count(name) - Scalars.count(".jsonl"))
    }

    func claudeTranscript(_ path: String, prefixes: [String]?, wantTurns: Bool = false, wantUsage: Bool = false)
        -> Transcript?
    {
        guard let sessionID = ident(Self.stem(path)) else { return nil }
        var transcript: Transcript?
        var pending: (Int64, Int64) = (0, 0)
        var outsidePrefixes = false
        ReaderFiles.forEachLine(path) { line in
            if transcript == nil {
                guard let cwd = line["cwd"]?.string else { return true }
                guard let forms = ReaderPaths.pathForms(cwd, home: home),
                    ReaderPaths.matchesPrefixes(prefixes, forms.forms)
                else { outsidePrefixes = true; return false }  // rule 2: skipped before its turns are parsed
                transcript = Transcript(ident: sessionID, cwd: forms.display, forms: forms.forms)
            }
            guard let kind = line["type"]?.string, kind == "user" || kind == "assistant" else { return true }
            if line["isMeta"]?.isTrue == true || line["isSidechain"]?.isTrue == true { return true }
            if line["isCompactSummary"]?.isTrue == true { return true }
            guard let at = line["timestamp"]?.string.flatMap(ReaderTime.parse) else { return true }
            guard let message = line["message"], message.object != nil else { return true }
            let (text, calls, results) = TranscriptText.claudeContent(message["content"])
            if !text.isEmpty && TranscriptText.startsWithMarker(text, TranscriptText.claudeMarkers) { return true }
            if transcript!.startedAt == nil { transcript!.startedAt = at }
            transcript!.lastActivityAt = max(transcript!.lastActivityAt ?? at, at)
            if kind == "assistant" {
                if let model = message["model"]?.string { transcript!.model = model }
                if wantUsage, let usage = message["usage"], usage.object != nil {
                    transcript!.usage.append(
                        (at, usage["input_tokens"]?.nonNegativeInt ?? 0, usage["output_tokens"]?.nonNegativeInt ?? 0,
                         ReaderMoney.micros(line["costUSD"])))
                }
            }
            if text.isEmpty {
                // A tool-only line: its counts belong to the turn before it.
                if !transcript!.turns.isEmpty {
                    let last = transcript!.turns.count - 1
                    transcript!.turns[last]["tool_calls"] = .int((transcript!.turns[last]["tool_calls"]?.intValue ?? 0) + calls)
                    transcript!.turns[last]["tool_results"] = .int((transcript!.turns[last]["tool_results"]?.intValue ?? 0) + results)
                } else {
                    pending.0 += calls
                    pending.1 += results
                }
                return true
            }
            transcript!.messageCount += 1
            if wantTurns {
                transcript!.turns.append([
                    "role": .string(kind),
                    "at": .int(at),
                    "text": .string(scrubbedText(text, limit: ReaderLimits.maxTurnChars)),
                    "tool_calls": .int(calls + pending.0),
                    "tool_results": .int(results + pending.1),
                ])
                pending = (0, 0)
            }
            return true
        }
        if outsidePrefixes { return nil }
        return transcript
    }

    func listRows(_ transcripts: [Transcript?], args: JSONValue, now: Int64, idKey: String) -> [JSONValue] {
        let since = now - (args["since_hours"]?.intValue ?? 0) * 3600
        var rows: [(last: Int64, id: String, row: JSONValue)] = []
        for case let transcript? in transcripts {
            guard let last = transcript.lastActivityAt, last >= since else { continue }
            let active = now - last <= ReaderLimits.activeSeconds
            var model: JSONValue = .null
            if let name = transcript.model, ident(name) != nil { model = .string(name) }
            guard let cwd = ident(transcript.cwd) else { continue }
            rows.append((last, transcript.ident, [
                idKey: .string(transcript.ident),
                "cwd": .string(cwd),
                "started_at": transcript.startedAt.map(JSONValue.int) ?? .null,
                "last_activity_at": .int(last),
                "message_count": .int(transcript.messageCount),
                "model": model,
                "status_hint": active ? "active" : "idle",
            ]))
        }
        rows.sort { $0.last != $1.last ? $0.last > $1.last : Scalars.less($0.id, $1.id) }
        return rows.map(\.row)
    }

    func claudeSessionsList(_ args: JSONValue, now: Int64) throws -> JSONValue {
        let prefixes = args["cwd_prefixes"]?.arrayValue?.compactMap(\.stringValue)
        let transcripts = claudeFiles.map { claudeTranscript($0, prefixes: prefixes) }
        let rows = listRows(transcripts, args: args, now: now, idKey: "session_id")
            .filter { rowOK("claude.sessions.list", "sessions", $0) }
        return ["sessions": .array(Array(rows.prefix(Int(args["limit"]?.intValue ?? 0))))]
    }

    func claudeSessionsRead(_ args: JSONValue, now: Int64) throws -> JSONValue {
        let wanted = args["session_id"]?.stringValue ?? ""
        for path in claudeFiles where Scalars.equal(Self.stem(path), wanted) {
            guard let transcript = claudeTranscript(path, prefixes: nil, wantTurns: true) else { break }
            let last = Int(args["last_turns"]?.intValue ?? 0)
            return [
                "session_id": .string(transcript.ident),
                "turns": .array(transcript.turns.suffix(last).map(JSONValue.object)),
            ]
        }
        throw ReaderError()
    }
}
