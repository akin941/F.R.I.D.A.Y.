// `usage.ccusage` (Phase 9 brief M2): per-project spend.
//
// `.transcripts` is the reference rule (backend/nodes/reference/readers.py
// `_usage_ccusage`): the assistant `message.usage` and `costUSD` of the
// Claude transcripts, integer micro-USD rounded half-even. The conformance
// build and the tests use it, because no ccusage binary runs there.
//
// The release build would run a pinned `ccusage` binary (path and hash in
// the local policy, fixed argv `--json --offline`, a governance manifest
// entry). None is installed or vetted yet, so the release build advertises
// the capability `unsupported` (brief: "a missing binary makes availability
// unsupported"); see the Phase 9 brief follow-ups.

import Foundation

public enum UsageSource: Sendable {
    case transcripts
    case unsupported
}

extension NodeReaders {
    static let periodSeconds: [String: Int64] = ["week": 7 * 86_400, "month": 30 * 86_400]

    func usageCCUsage(_ args: JSONValue, now: Int64) throws -> JSONValue {
        guard usage == .transcripts else { throw ReaderError() }
        let period = args["period"]?.stringValue ?? ""
        let since: Int64
        if period == "today" {
            since = now - now % 86_400
        } else if let seconds = Self.periodSeconds[period] {
            since = now - seconds
        } else {
            throw ReaderError()
        }
        let prefixes = args["cwd_prefixes"]?.arrayValue?.compactMap(\.stringValue)
        var projects: [String: (Int64, Int64, Int64)] = [:]
        var order: [String] = []
        for path in claudeFiles {
            guard let transcript = claudeTranscript(path, prefixes: prefixes, wantUsage: true),
                let cwd = ident(transcript.cwd)
            else { continue }
            for entry in transcript.usage where entry.at >= since {
                if projects[cwd] == nil { order.append(cwd) }
                var row = projects[cwd] ?? (0, 0, 0)
                row.0 += entry.micros
                row.1 += entry.tokensIn
                row.2 += entry.tokensOut
                projects[cwd] = row
            }
        }
        let rows: [JSONValue] = order.sorted(by: Scalars.less).map { cwd in
            let row = projects[cwd]!
            return ["cwd": .string(cwd), "usd_micros": .int(row.0), "tokens_in": .int(row.1), "tokens_out": .int(row.2)]
        }
        return Self.usageTotals(rows)
    }

    static func usageTotals(_ rows: [JSONValue]) -> JSONValue {
        func sum(_ key: String) -> Int64 { rows.reduce(0) { $0 + ($1[key]?.intValue ?? 0) } }
        return [
            "total_usd_micros": .int(sum("usd_micros")),
            "tokens_in": .int(sum("tokens_in")),
            "tokens_out": .int(sum("tokens_out")),
            "by_project": .array(rows),
        ]
    }
}
