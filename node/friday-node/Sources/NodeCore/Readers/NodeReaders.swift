// The node's readers for the eight v1 read capabilities (Phase 9 brief M2,
// D7, D13 items 5-6): a port of backend/nodes/reference/readers.py
// `Readers`, whose module docstring states the normative rules:
//
// 1. never tool results or file contents (counts only);
// 2. `cwd_prefixes` first (segment boundaries, absolute and `~/` forms);
// 3. every returned string scrubbed (Scrubber.swift), free text scrubbed
//    then cut (2 000 / 512 scalars), an identifier the scrub would change
//    not returned (its row dropped, a `model` nulled);
// 4. bounded (files, bytes per file and line, rows not matching the
//    result schema dropped, output cut to `max_result_bytes`, oldest turns
//    or last rows first, marked `truncated`);
// 5. `cwd` in `~/` form under the home, absolute elsewhere; Unix seconds;
// 6. nothing cached, copied or logged: every call re-reads.
//
// Directory grants are local and off by default; a family without its
// grant is advertised `not_granted` (check 16 refuses before any reader
// runs) and the reader refuses too (defence in depth).

import Foundation

public struct ReaderGrants: Equatable, Sendable {
    public var claude: Bool
    public var codex: Bool

    public init(claude: Bool = false, codex: Bool = false) {
        self.claude = claude
        self.codex = codex
    }
}

public final class NodeReaders: ReadProvider, @unchecked Sendable {
    static let family: [String: String] = [
        "claude.sessions.list": "claude", "claude.sessions.read": "claude", "usage.ccusage": "claude",
        "codex.threads.list": "codex", "codex.threads.read": "codex", "git.status": "git",
        "apps.frontmost": "desktop", "windows.list": "desktop",
    ]

    let home: String
    let claudeRoot: String
    let codexRoot: String
    let repos: [LocalRepo]?
    let grants: ReaderGrants
    let windowTitles: Bool
    let desktop: DesktopSource
    let usage: UsageSource
    /// Capabilities this node runs, in call order (tests and the
    /// conformance report count them; no arguments are kept).
    public private(set) var calls: [String] = []
    private let lock = NSLock()

    /// `home` is resolved (realpath); `repos` nil = no local repo list.
    public init(
        home: String, repos: [LocalRepo]?, grants: ReaderGrants, windowTitles: Bool, desktop: DesktopSource,
        usage: UsageSource
    ) {
        self.home = ReaderPaths.realpathC(home)
        claudeRoot = self.home + "/.claude/projects"
        codexRoot = self.home + "/.codex/sessions"
        self.repos = repos
        self.grants = grants
        self.windowTitles = windowTitles
        self.desktop = desktop
        self.usage = usage
    }

    /// `{capability: availability}` for the `hello` catalogue.
    public func availability() -> [String: String] {
        var out: [String: String] = [:]
        for (name, family) in Self.family {
            switch family {
            case "claude": out[name] = grants.claude ? "granted" : "not_granted"
            case "codex": out[name] = grants.codex ? "granted" : "not_granted"
            case "git": out[name] = (repos?.isEmpty == false) ? "granted" : "not_granted"
            default: out[name] = "granted"
            }
        }
        if usage == .unsupported { out["usage.ccusage"] = "unsupported" }
        return out
    }

    public func read(capability: String, args: JSONValue, now: Int64) -> ReadOutcome {
        lock.lock()
        calls.append(capability)
        lock.unlock()
        guard availability()[capability] == "granted" else { return .failure }
        let output: JSONValue
        do {
            switch capability {
            case "claude.sessions.list": output = try claudeSessionsList(args, now: now)
            case "claude.sessions.read": output = try claudeSessionsRead(args, now: now)
            case "usage.ccusage": output = try usageCCUsage(args, now: now)
            case "codex.threads.list": output = try codexThreadsList(args, now: now)
            case "codex.threads.read": output = try codexThreadsRead(args, now: now)
            case "git.status": output = try gitStatus(args, now: now)
            case "apps.frontmost": output = try appsFrontmost(args, now: now)
            case "windows.list": output = try windowsList(args, now: now)
            default: return .failure
            }
        } catch {
            return .failure
        }
        guard let (fitted, truncated) = try? fit(capability, output),
            let contract = CapabilityContract.all.first(where: { $0.name == capability }),
            Schema.isValid(fitted, against: contract.resultSchema)
        else { return .failure }
        return .success(fitted, truncated: truncated)
    }

    // MARK: Scrubbing helpers

    /// An identifier the scrub leaves unchanged, else nil.
    func ident(_ value: String) -> String? {
        guard !value.isEmpty, Scalars.equal(Scrubber.scrub(value), value) else { return nil }
        return value
    }

    /// Free text: scrubbed, then cut to `limit` scalars.
    func scrubbedText(_ value: String, limit: Int) -> String {
        value.isEmpty ? "" : Scalars.prefix(Scrubber.scrub(value), limit)
    }

    func rowOK(_ capability: String, _ listKey: String, _ row: JSONValue) -> Bool {
        guard let contract = CapabilityContract.all.first(where: { $0.name == capability }),
            let items = contract.resultSchema["properties"]?[listKey]?["items"]
        else { return false }
        return Schema.isValid(row, against: items)
    }

    // MARK: Bounding

    /// Cut `output` to the contract's `max_result_bytes`: turns from the
    /// oldest, other rows from the end (usage totals recomputed).
    func fit(_ capability: String, _ output: JSONValue) throws -> (JSONValue, Bool) {
        guard let contract = CapabilityContract.all.first(where: { $0.name == capability }) else { throw ReaderError() }
        let limit = Int(contract.maxResultBytes)
        if try JSON.canonical(output).count <= limit { return (output, false) }
        guard let key = ["turns", "sessions", "threads", "by_project", "windows"].first(where: { output[$0] != nil }),
            var rows = output[key]?.arrayValue, var fields = output.objectValue
        else { throw ReaderError() }
        while !rows.isEmpty {
            if key == "turns" { rows.removeFirst() } else { rows.removeLast() }
            fields[key] = .array(rows)
            let candidate: JSONValue = key == "by_project" ? Self.usageTotals(rows) : .object(fields)
            if try JSON.canonical(candidate).count <= limit { return (candidate, true) }
        }
        fields[key] = .array([])
        return (key == "by_project" ? Self.usageTotals([]) : .object(fields), true)
    }
}
