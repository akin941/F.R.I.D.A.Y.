// The debug-only real-file check of the owner-turn rules (Phase 8B brief
// M1 "Real-file verification", runbook B4): `friday-node scrub-preview
// --owner-turns <one ~/.claude/projects/<project> directory>` and
// `--codex <a ~/.codex/sessions directory>` run the readers' own line rules
// (ClaudeOwnerTurns.swift, CodexOwnerTurns.swift) over real transcripts
// and return **counts only**: per line kind, per rule verdict and per
// excluded marker. No text, no identifier, no file name ever leaves this
// type: a line kind (a JSON `type` value) is shown only when it is a short
// format word, anything else is counted as `(other)`.
//
// Compiled only in debug builds (SwiftPM debug and the Xcode Debug
// configuration define DEBUG; Release does not), like the CLI command.

#if DEBUG
import Foundation

public enum OwnerTurnPreview {
    /// A line-kind word: `[A-Za-z0-9_.:-]{1,40}`; anything else is
    /// `(other)` (never echoed), a missing value `(none)`.
    static func word(_ value: LooseJSON?) -> String {
        guard let value else { return "(none)" }
        guard let text = value.string else { return "(other)" }
        let scalars = Array(text.unicodeScalars)
        guard (1...40).contains(scalars.count),
            scalars.allSatisfy({ $0.isASCII && (CharacterSet.alphanumerics.contains($0) || "_.:-".unicodeScalars.contains($0)) })
        else { return "(other)" }
        return text
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

    /// The leading tag's name when it is a markup word
    /// (`<[a-z][a-z0-9_-]{0,39}>`, as harness tags are), else `(other)`:
    /// the tag a marker would need, never the text after it.
    static func tagName(_ text: String) -> String {
        let scalars = Array(text.unicodeScalars.drop(while: PythonChars.isSpace).dropFirst().prefix(42))
        guard let close = scalars.firstIndex(of: ">"), close >= 1, close <= 40 else { return "(other)" }
        let name = scalars[0..<close]
        func lower(_ s: Unicode.Scalar) -> Bool { (0x61...0x7A).contains(s.value) }
        func digit(_ s: Unicode.Scalar) -> Bool { (0x30...0x39).contains(s.value) }
        guard lower(name[0]), name.allSatisfy({ lower($0) || digit($0) || $0 == "_" || $0 == "-" }) else {
            return "(other)"
        }
        return "<" + String(String.UnicodeScalarView(name)) + ">"
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
                counts["kind.\(word(line["type"]))", default: 0] += 1
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
                        counts["user.excluded.non_text_content.block.\(word(block["type"]))", default: 0] += 1
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
                counts["kind.\(word(line["type"]))/\(word(payload?["type"]))", default: 0] += 1
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
                    counts["message.role.\(word(payload?["role"]))", default: 0] += 1
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
                        counts["user.excluded.non_input_text_content.part.\(word(part["type"]))", default: 0] += 1
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
