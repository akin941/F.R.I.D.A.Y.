// The debug-only real-file check of the owner-turn rules (Phase 8B brief
// M1 "Real-file verification", runbook B4): `friday-node scrub-preview
// --owner-turns <one ~/.claude/projects/<project> directory>` and
// `--codex <a ~/.codex/sessions directory>` run the readers' own line rules
// (ClaudeOwnerTurns.swift, CodexOwnerTurns.swift) over real transcripts
// and return **counts only**: per line kind, per rule verdict and per
// excluded marker. No text, no identifier, no file name ever leaves this
// type: a line kind, role, block or part type, or leading tag name is shown
// only when it is on one of the fixed lists below; anything else is counted
// as `(other)`.
//
// Compiled only in debug builds (SwiftPM debug and the Xcode Debug
// configuration define DEBUG; Release does not), like the CLI command.

#if DEBUG
import Foundation

public enum OwnerTurnPreview {
    // Every word the preview may echo comes from one of these fixed lists
    // (known format words); anything else is counted as `(other)`, so no
    // owner-typed text can reach a key, however it is shaped.
    public static let claudeLineTypes: Set<String> = [
        "user", "assistant", "attachment", "system", "progress", "summary", "custom-title", "ended-by-model",
        "ai-title", "last-prompt", "tag", "relocated", "agent-name", "agent-color", "agent-setting", "pr-link",
        "frame-link", "bridge-session", "file-history-snapshot", "attribution-snapshot", "mode", "permission-mode",
        "isolation-latch", "worktree-state", "queue-operation", "content-replacement", "fork-context-ref",
        "marble-origami-commit", "marble-origami-snapshot", "marble-origami-reset",
    ]
    public static let claudeBlockTypes: Set<String> = [
        "text", "tool_result", "tool_use", "image", "document", "thinking", "redacted_thinking", "server_tool_use",
        "web_search_tool_result", "search_result",
    ]
    public static let codexLineTypes: Set<String> = [
        "session_meta", "response_item", "event_msg", "turn_context", "compacted", "inter_agent_communication",
    ]
    public static let codexPayloadTypes: Set<String> = [
        // response_item payloads
        "message", "reasoning", "function_call", "function_call_output", "custom_tool_call",
        "custom_tool_call_output", "local_shell_call", "local_shell_call_output", "web_search_call",
        "tool_search_call", "image_generation_call", "ghost_snapshot", "compaction",
        // event_msg payloads (the persisted variants of Codex's event enum)
        "user_message", "agent_message", "agent_reasoning", "agent_reasoning_raw_content",
        "agent_reasoning_section_break", "token_count", "task_started", "task_complete", "turn_aborted",
        "context_compacted", "thread_rolled_back", "session_configured", "exec_command_begin", "exec_command_end",
        "exec_approval_request", "apply_patch_approval_request", "patch_apply_begin", "patch_apply_end",
        "mcp_tool_call_begin", "mcp_tool_call_end", "web_search_begin", "web_search_end",
        "image_generation_begin", "image_generation_end", "view_image_tool_call", "turn_diff", "plan_update",
        "stream_error", "error", "warning", "deprecation_notice", "entered_review_mode", "exited_review_mode",
        "item_started", "item_completed",
    ]
    public static let codexRoles: Set<String> = ["user", "assistant", "developer", "system"]
    public static let codexPartTypes: Set<String> = ["input_text", "input_image", "output_text", "input_file"]
    /// Known harness tag names (Claude Code and Codex): the marker tags
    /// themselves, so a reader regression shows by name, plus known tags no
    /// marker covers (IDE context, persisted tool output, remote review).
    public static let harnessTags: Set<String> = [
        "command-name", "command-message", "command-args", "local-command-stdout", "local-command-stderr",
        "local-command-caveat", "system-reminder", "user-prompt-submit-hook", "bash-input", "bash-stdout",
        "bash-stderr", "bash-exit-code", "task-notification", "tick", "user-memory-input", "fork-boilerplate",
        "teammate-message", "cross-session-message", "agent-message", "channel", "mcp-resource-update",
        "mcp-polling-update", "remote-review", "remote-review-progress", "persisted-output", "ide_opened_file",
        "ide_selection", "ide_diagnostics", "environment_context", "user_instructions", "user_shell_command",
        "skill", "turn_aborted", "subagent_notification", "user_action",
    ]

    /// The readers' marker lists (for the preview's tests).
    public static var claudeMarkers: [String] { TranscriptText.claudeMarkers }
    public static var codexMarkers: [String] { TranscriptText.codexMarkers }

    /// `text` when it is on `allowed`, else `(other)` (never echoed).
    public static func echo(_ text: String?, _ allowed: Set<String>) -> String {
        guard let text, allowed.contains(text) else { return "(other)" }
        return text
    }

    /// A JSON value's word: `echo` of a string, `(none)` for a missing
    /// value, `(other)` for anything else.
    static func word(_ value: LooseJSON?, _ allowed: Set<String>) -> String {
        guard let value else { return "(none)" }
        return echo(value.string, allowed)
    }

    /// Counts for an accepted turn's text (never the text itself).
    static func countAccepted(_ text: String, into counts: inout [String: Int]) {
        counts["accepted", default: 0] += 1
        let scrubbed = Scrubber.scrub(text)
        if !Scalars.equal(scrubbed, text) { counts["accepted.text_changed_by_scrub", default: 0] += 1 }
        if Scalars.count(scrubbed) > OwnerTurnLimits.maxTextChars { counts["accepted.text_cut_at_4000", default: 0] += 1 }
        // A leading tag no marker covers: a candidate new harness format.
        let stripped = Array(text.unicodeScalars.drop(while: PythonChars.isSpace).prefix(2))
        if stripped.count == 2, stripped[0] == "<", stripped[1].properties.isAlphabetic || stripped[1] == "/" {
            counts["accepted.starts_with_a_tag", default: 0] += 1
            counts["accepted.starts_with_a_tag.\(tagName(text))", default: 0] += 1
        }
    }

    /// The leading tag's name (up to `>`, `/` or whitespace) as `<name>`
    /// when it is on `harnessTags`, else `(other)`: never owner text.
    public static func tagName(_ text: String) -> String {
        let scalars = text.unicodeScalars.drop(while: PythonChars.isSpace).dropFirst().prefix(64)
        let name = String(String.UnicodeScalarView(scalars.prefix { $0 != ">" && $0 != "/" && !PythonChars.isSpace($0) }))
        return harnessTags.contains(name) ? "<" + name + ">" : "(other)"
    }

    /// The `*.jsonl` files directly in `directory` (a Claude project
    /// directory, as the reader's `projects/*/*.jsonl`): regular files, no
    /// symlink, code-point order.
    static func directFiles(_ directory: String) -> [String] {
        let names = (try? FileManager.default.contentsOfDirectory(atPath: directory)) ?? []
        return names.filter { name in
            guard name.unicodeScalars.reversed().starts(with: ".jsonl".unicodeScalars.reversed()) else { return false }
            let type = (try? FileManager.default.attributesOfItem(atPath: directory + "/" + name)[.type]) as? FileAttributeType
            return type == .typeRegular
        }.sorted(by: Scalars.less).map { directory + "/" + $0 }
    }

    /// Claude Code JSONL of one project directory.
    public static func claude(directory: String) -> [(String, Int)] {
        var counts: [String: Int] = [:]
        for path in directFiles(directory) {
            counts["files", default: 0] += 1
            ReaderFiles.forEachRawLine(path) { _, value in
                counts["lines", default: 0] += 1
                guard let line = value else {
                    counts["lines.unparseable_or_oversized", default: 0] += 1
                    return true
                }
                counts["kind.\(word(line["type"], claudeLineTypes))", default: 0] += 1
                guard line["type"]?.string == "user" else { return true }
                let verdict: ClaudeOwnerTurn.Verdict
                do { verdict = try ClaudeOwnerTurn.verdict(line) } catch {
                    counts["user.timestamp_overflow", default: 0] += 1
                    return true
                }
                switch verdict {
                case .turn(let sessionID, let turnID, _, let text):
                    let cwd = OwnerTurnText.normalisePath(line["cwd"]) ?? ""
                    if [sessionID, turnID, cwd].contains(where: { !Scalars.equal(Scrubber.scrub($0), $0) }) {
                        counts["user.identifier_changed_by_scrub", default: 0] += 1
                    } else {
                        countAccepted(text, into: &counts)
                    }
                case .notUser: break
                case .meta: counts["user.excluded.is_meta", default: 0] += 1
                case .sidechain: counts["user.excluded.is_sidechain", default: 0] += 1
                case .compactSummary: counts["user.excluded.is_compact_summary", default: 0] += 1
                case .notUserRole: counts["user.excluded.no_message_or_role_not_user", default: 0] += 1
                case .nonTextContent:
                    counts["user.excluded.non_text_content", default: 0] += 1
                    for block in line["message"]?["content"]?.array ?? [] {
                        counts["user.excluded.non_text_content.block.\(word(block["type"], claudeBlockTypes))", default: 0] += 1
                    }
                case .blank: counts["user.excluded.blank", default: 0] += 1
                case .marker(let marker): counts["user.excluded.marker.\(marker)", default: 0] += 1
                case .badIdentifier: counts["user.excluded.bad_session_id_or_uuid", default: 0] += 1
                case .badTimestamp: counts["user.excluded.bad_timestamp", default: 0] += 1
                case .badCwd: counts["user.excluded.bad_cwd", default: 0] += 1
                }
                return true
            }
        }
        return counts.sorted { Scalars.less($0.key, $1.key) }.map { ($0.key, $0.value) }
    }

    /// Codex rollout JSONL under `directory` (recursive, as the reader's
    /// `sessions/**/*.jsonl`).
    public static func codex(directory: String) -> [(String, Int)] {
        var counts: [String: Int] = [:]
        for path in ReaderFiles.transcripts(root: ReaderPaths.realpathC(directory), recursive: true) {
            counts["files", default: 0] += 1
            var sawMeta = false
            var skipped = false
            ReaderFiles.forEachRawLine(path) { _, value in
                counts["lines", default: 0] += 1
                guard let line = value else {
                    counts["lines.unparseable_or_oversized", default: 0] += 1
                    return true
                }
                let payload = line["payload"]
                counts["kind.\(word(line["type"], codexLineTypes))/\(word(payload?["type"], codexPayloadTypes))", default: 0] += 1
                if !sawMeta {
                    sawMeta = true
                    if CodexOwnerTurn.meta(line) == nil {
                        counts["files.skipped.first_line_not_a_valid_session_meta", default: 0] += 1
                        skipped = true
                    }
                    return true
                }
                if skipped { return true }  // the reader stops at the first line
                if line["type"]?.string == "response_item", payload?["type"]?.string == "message" {
                    counts["message.role.\(word(payload?["role"], codexRoles))", default: 0] += 1
                }
                let timestamp: Int64?
                do { timestamp = try OwnerInstant.parse(line["timestamp"]) } catch {
                    counts["lines.timestamp_overflow", default: 0] += 1
                    return true
                }
                switch CodexOwnerTurn.verdict(line) {
                case .turn(let text):
                    if timestamp == nil {
                        counts["user.excluded.bad_timestamp", default: 0] += 1
                    } else {
                        countAccepted(text, into: &counts)
                    }
                case .notResponseItem, .notMessage, .notUserRole: break
                case .nonTextContent:
                    counts["user.excluded.non_input_text_content", default: 0] += 1
                    for part in payload?["content"]?.array ?? [] {
                        counts["user.excluded.non_input_text_content.part.\(word(part["type"], codexPartTypes))", default: 0] += 1
                    }
                case .blank: counts["user.excluded.blank", default: 0] += 1
                case .marker(let marker): counts["user.excluded.marker.\(marker)", default: 0] += 1
                }
                return true
            }
        }
        return counts.sorted { Scalars.less($0.key, $1.key) }.map { ($0.key, $0.value) }
    }
}
#endif
