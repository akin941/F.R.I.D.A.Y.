// `codex.threads.list` / `codex.threads.read` (backend/nodes/reference/
// readers.py `_codex_transcript`, `_codex_threads_*`): Codex rollout JSONL
// under `~/.codex/sessions/**/*.jsonl`; the `session_meta` line names the
// thread and its `cwd`. Tool calls and outputs are counted, never returned;
// reasoning items and the harness-injected user context are skipped.

import Foundation

extension NodeReaders {
    var codexFiles: [String] { ReaderFiles.transcripts(root: codexRoot, recursive: true) }

    func codexTranscript(_ path: String, prefixes: [String]?, wantTurns: Bool = false) -> Transcript? {
        var transcript: Transcript?
        var pending: (Int64, Int64) = (0, 0)
        var outsidePrefixes = false
        ReaderFiles.forEachLine(path) { line in
            let kind = line["type"]?.string
            guard let payload = line["payload"], payload.object != nil else { return true }
            if transcript == nil {
                guard kind == "session_meta" else { return true }
                guard let threadID = payload["id"]?.string.flatMap(ident), let cwd = payload["cwd"]?.string else {
                    outsidePrefixes = true; return false
                }
                guard let forms = ReaderPaths.pathForms(cwd, home: home),
                    ReaderPaths.matchesPrefixes(prefixes, forms.forms)
                else { outsidePrefixes = true; return false }  // rule 2
                transcript = Transcript(ident: threadID, cwd: forms.display, forms: forms.forms)
                // Python `a or b`: a 0 timestamp falls through to the next.
                let fromPayload = payload["timestamp"]?.string.flatMap(ReaderTime.parse)
                transcript!.startedAt = (fromPayload ?? 0) != 0
                    ? fromPayload : line["timestamp"]?.string.flatMap(ReaderTime.parse)
                return true
            }
            let at = line["timestamp"]?.string.flatMap(ReaderTime.parse)
            if kind == "turn_context" {
                if let model = payload["model"]?.string { transcript!.model = model }
                return true
            }
            guard kind == "response_item", let at else { return true }
            let item = payload["type"]?.string
            if ["function_call", "local_shell_call", "custom_tool_call"].contains(item) {
                pending.0 += 1
                return true
            }
            if ["function_call_output", "local_shell_call_output", "custom_tool_call_output"].contains(item) {
                pending.1 += 1
                return true
            }
            guard item == "message", let role = payload["role"]?.string, role == "user" || role == "assistant" else {
                return true
            }
            let text = TranscriptText.codexText(payload["content"], role: role)
            if text.isEmpty || (role == "user" && TranscriptText.startsWithMarker(text, TranscriptText.codexMarkers)) {
                return true
            }
            if transcript!.startedAt == nil { transcript!.startedAt = at }
            transcript!.lastActivityAt = max(transcript!.lastActivityAt ?? at, at)
            transcript!.messageCount += 1
            if wantTurns {
                let (calls, results) = role == "assistant" ? pending : (0, 0)
                transcript!.turns.append([
                    "role": .string(role),
                    "at": .int(at),
                    "text": .string(scrubbedText(text, limit: ReaderLimits.maxTurnChars)),
                    "tool_calls": .int(calls),
                    "tool_results": .int(results),
                ])
                if role == "assistant" { pending = (0, 0) }
            }
            return true
        }
        if outsidePrefixes { return nil }
        if var found = transcript, wantTurns, !found.turns.isEmpty, pending.0 != 0 || pending.1 != 0 {
            let last = found.turns.count - 1
            found.turns[last]["tool_calls"] = .int((found.turns[last]["tool_calls"]?.intValue ?? 0) + pending.0)
            found.turns[last]["tool_results"] = .int((found.turns[last]["tool_results"]?.intValue ?? 0) + pending.1)
            transcript = found
        }
        return transcript
    }

    func codexThreadsList(_ args: JSONValue, now: Int64) throws -> JSONValue {
        let prefixes = args["cwd_prefixes"]?.arrayValue?.compactMap(\.stringValue)
        let transcripts = codexFiles.map { codexTranscript($0, prefixes: prefixes) }
        let rows = listRows(transcripts, args: args, now: now, idKey: "thread_id")
            .filter { rowOK("codex.threads.list", "threads", $0) }
        return ["threads": .array(Array(rows.prefix(Int(args["limit"]?.intValue ?? 0))))]
    }

    func codexThreadsRead(_ args: JSONValue, now: Int64) throws -> JSONValue {
        let wanted = args["thread_id"]?.stringValue ?? ""
        for path in codexFiles {
            if let transcript = codexTranscript(path, prefixes: nil, wantTurns: true),
                Scalars.equal(transcript.ident, wanted)
            {
                let last = Int(args["last_turns"]?.intValue ?? 0)
                return [
                    "thread_id": .string(transcript.ident),
                    "turns": .array(transcript.turns.suffix(last).map(JSONValue.object)),
                ]
            }
        }
        throw ReaderError()
    }
}
