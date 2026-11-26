// The shared half of the two owner-turn reads (Phase 8B brief D13, D17;
// runbook B4): a port of backend/nodes/reference/owner_turns.py (the
// executable spec) as the reference node's `readers.Readers` feeds it (its
// vetted files, rule 4, and its home-aware prefix match, rule 2).
//
// - Times are the reference's own: `parse_instant` (the contract's RFC 3339
//   pattern, then `datetime.fromisoformat`, kept to the microsecond) and
//   `format_instant` (UTC, milliseconds cut, `Z`), not Unix seconds.
// - `validate_args`: the contract's args plus `since >= now - 7 days` and
//   `since <= now + 5 minutes`; any refusal is a reader error.
// - `collect_files`: candidates with `at >= since` whose identifiers the
//   scrub leaves unchanged, ordered by `(at, turn_id)` (stable: file order
//   breaks ties), then taken oldest first until a cap (`max_turns`,
//   `max_sessions`, the 60 KiB budget measured as compact JSON) ends the
//   result with `truncated: true`; `next_since` is the `at` of the last
//   turn taken (`since` when none).
// - Every text is scrubbed, then cut to 4 000 scalars.
// - `fitOwnerTurns` is `readers._fit_owner_turns`: a canonical-size cut
//   drops the newest turns first and recomputes `next_since`, so the cursor
//   never skips a dropped turn.
// Strings compare by code point (`Scalars`), never by canonical equivalence.

import Foundation

enum OwnerTurnLimits {
    static let maxTextChars = 4000
    static let maxSinceAgeMicros: Int64 = 7 * 86_400 * 1_000_000
    static let maxSinceAheadMicros: Int64 = 5 * 60 * 1_000_000
    static let maxSessions: Int64 = 50
    static let maxTurns: Int64 = 200
    static let maxPrefixes = 20
    static let maxPathChars = 500
    static let maxResultBytes = 60 * 1024
    static let argKeys = ["since", "cwd_prefixes", "max_sessions", "max_turns"]
}

/// A time the reference's `datetime` could not hold (`astimezone` raises
/// OverflowError there, which no reader catches): the whole read fails.
struct OwnerInstantOverflow: Error {}

// MARK: - Instants (owner_turns.py parse_instant / format_instant)

enum OwnerInstant {
    static let microsPerSecond: Int64 = 1_000_000
    static let microsPerDay: Int64 = 86_400 * microsPerSecond
    /// datetime.min / datetime.max in UTC, in microseconds since the epoch.
    static let minMicros: Int64 = daysFromCivil(1, 1, 1) * microsPerDay
    static let maxMicros: Int64 = (daysFromCivil(9999, 12, 31) + 1) * microsPerDay - 1

    /// Days since 1970-01-01 of a proleptic Gregorian date.
    static func daysFromCivil(_ year: Int64, _ month: Int64, _ day: Int64) -> Int64 {
        let y = month <= 2 ? year - 1 : year
        let era = (y >= 0 ? y : y - 399) / 400
        let yoe = y - era * 400
        let mp = (month + 9) % 12
        let doy = (153 * mp + 2) / 5 + day - 1
        let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy
        return era * 146_097 + doe - 719_468
    }

    static func civilFromDays(_ days: Int64) -> (Int64, Int64, Int64) {
        let z = days + 719_468
        let era = (z >= 0 ? z : z - 146_096) / 146_097
        let doe = z - era * 146_097
        let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365
        let doy = doe - (365 * yoe + yoe / 4 - yoe / 100)
        let mp = (5 * doy + 2) / 153
        let day = doy - (153 * mp + 2) / 5 + 1
        let month = mp < 10 ? mp + 3 : mp - 9
        return (month <= 2 ? yoe + era * 400 + 1 : yoe + era * 400, month, day)
    }

    static func daysInMonth(_ year: Int64, _ month: Int64) -> Int64 {
        switch month {
        case 2: return (year % 4 == 0 && year % 100 != 0) || year % 400 == 0 ? 29 : 28
        case 4, 6, 9, 11: return 30
        default: return 31
        }
    }

    /// `parse_instant`: microseconds since the epoch (UTC), nil for anything
    /// but a string matching the contract's pattern that `fromisoformat`
    /// accepts; throws where the reference's `astimezone` overflows.
    static func parse(_ value: LooseJSON?) throws -> Int64? {
        guard let text = value?.string else { return nil }
        return try parse(text)
    }

    static func parse(_ text: String) throws -> Int64? {
        // \A[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})\Z
        let s = Array(text.unicodeScalars)
        var index = 0
        func digits(_ count: Int) -> Int64? {
            guard index + count <= s.count else { return nil }
            var value: Int64 = 0
            for scalar in s[index..<index + count] {
                guard scalar.value >= 0x30, scalar.value <= 0x39 else { return nil }
                value = value * 10 + Int64(scalar.value - 0x30)
            }
            index += count
            return value
        }
        func literal(_ character: Unicode.Scalar) -> Bool {
            guard index < s.count, s[index] == character else { return false }
            index += 1
            return true
        }
        guard let year = digits(4), literal("-"), let month = digits(2), literal("-"), let day = digits(2),
            literal("T"), let hour = digits(2), literal(":"), let minute = digits(2), literal(":"),
            let second = digits(2)
        else { return nil }
        var fraction: Int64 = 0
        if literal(".") {
            var count = 0
            while count < 6, index < s.count, s[index].value >= 0x30, s[index].value <= 0x39 {
                fraction = fraction * 10 + Int64(s[index].value - 0x30)
                index += 1
                count += 1
            }
            guard count >= 1 else { return nil }
            for _ in count..<6 { fraction *= 10 }
        }
        var offset: Int64 = 0
        if literal("Z") {
            offset = 0
        } else {
            guard index < s.count, s[index] == "+" || s[index] == "-" else { return nil }
            let sign: Int64 = s[index] == "-" ? -1 : 1
            index += 1
            guard let tzHour = digits(2), literal(":"), let tzMinute = digits(2) else { return nil }
            // fromisoformat: the minutes are not range-checked, the whole
            // offset must be strictly within a day.
            let seconds = tzHour * 3600 + tzMinute * 60
            guard seconds < 86_400 else { return nil }
            offset = sign * seconds
        }
        guard index == s.count else { return nil }
        guard year >= 1, (1...12).contains(month), day >= 1, day <= daysInMonth(year, month), hour <= 23,
            minute <= 59, second <= 59
        else { return nil }
        let local = daysFromCivil(year, month, day) * microsPerDay
            + (hour * 3600 + minute * 60 + second) * microsPerSecond + fraction
        let utc = local - offset * microsPerSecond
        guard utc >= minMicros, utc <= maxMicros else { throw OwnerInstantOverflow() }
        return utc
    }

    /// `format_instant`: `YYYY-MM-DDTHH:MM:SS.mmmZ` (microseconds cut).
    static func format(_ micros: Int64) -> String {
        let days = micros >= 0 ? micros / microsPerDay : -((-micros + microsPerDay - 1) / microsPerDay)
        let rest = micros - days * microsPerDay
        let (year, month, day) = civilFromDays(days)
        let seconds = rest / microsPerSecond
        let millis = (rest % microsPerSecond) / 1000
        return String(
            format: "%04lld-%02lld-%02lldT%02lld:%02lld:%02lld.%03lldZ", year, month, day, seconds / 3600,
            (seconds / 60) % 60, seconds % 60, millis)
    }
}

// MARK: - Paths and identifiers

enum OwnerTurnText {
    /// `_ID`: `\A[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z` (ASCII only).
    static func isID(_ value: String) -> Bool {
        let scalars = Array(value.unicodeScalars)
        guard (1...128).contains(scalars.count) else { return false }
        func alnum(_ s: Unicode.Scalar) -> Bool {
            (0x30...0x39).contains(s.value) || (0x41...0x5A).contains(s.value) || (0x61...0x7A).contains(s.value)
        }
        guard alnum(scalars[0]) else { return false }
        return scalars.dropFirst().allSatisfy { alnum($0) || $0 == "." || $0 == "_" || $0 == ":" || $0 == "-" }
    }

    static func id(_ value: LooseJSON?) -> String? {
        guard let text = value?.string, isID(text) else { return nil }
        return text
    }

    /// `str.strip()` with Python's whitespace.
    static func strip(_ text: String) -> String {
        var scalars = Substring(text).unicodeScalars[...]
        while let first = scalars.first, PythonChars.isSpace(first) { scalars = scalars.dropFirst() }
        while let last = scalars.last, PythonChars.isSpace(last) { scalars = scalars.dropLast() }
        return String(String.UnicodeScalarView(scalars))
    }

    /// `not text.strip()`.
    static func isBlank(_ text: String) -> Bool { text.unicodeScalars.allSatisfy(PythonChars.isSpace) }

    /// owner_turns.py `normalise_path`: stripped, at most 500 scalars, then
    /// the `~`-preserving normalisation; nil where the server refuses.
    static func normalisePath(_ value: LooseJSON?) -> String? {
        guard let text = value?.string else { return nil }
        return normalisePath(text)
    }

    static func normalisePath(_ text: String) -> String? {
        let path = strip(text)
        guard !path.isEmpty, !path.unicodeScalars.contains("\u{0}"), Scalars.count(path) <= OwnerTurnLimits.maxPathChars
        else { return nil }
        return ReaderPaths.normalise(path)
    }

    /// `text.lstrip().startswith(markers)`: the marker it starts with.
    static func marker(_ text: String, _ markers: [String]) -> String? {
        let stripped = String(String.UnicodeScalarView(text.unicodeScalars.drop(while: PythonChars.isSpace)))
        return markers.first { Scalars.hasPrefix(stripped, $0) }
    }
}

// MARK: - Arguments (owner_turns.py validate_args)

struct OwnerTurnArgs {
    var since: Int64
    var prefixes: [String]
    var maxSessions: Int
    var maxTurns: Int

    /// nil = `Refused("args_contract")`; throws where the reference's time
    /// parsing raises.
    static func validate(_ args: JSONValue, nowSeconds: Int64) throws -> OwnerTurnArgs? {
        guard let fields = args.objectValue, fields.count == OwnerTurnLimits.argKeys.count,
            fields.keys.allSatisfy({ key in OwnerTurnLimits.argKeys.contains { Scalars.equal($0, key) } })
        else { return nil }
        guard let sinceText = fields["since"]?.stringValue, let since = try OwnerInstant.parse(sinceText) else {
            return nil
        }
        let now = nowSeconds * OwnerInstant.microsPerSecond
        guard since >= now - OwnerTurnLimits.maxSinceAgeMicros, since <= now + OwnerTurnLimits.maxSinceAheadMicros
        else { return nil }
        guard let raw = fields["cwd_prefixes"]?.arrayValue, (1...OwnerTurnLimits.maxPrefixes).contains(raw.count)
        else { return nil }
        var seen = Set<[UInt32]>()
        var prefixes: [String] = []
        for item in raw {
            // str(item) of a non-string never normalises: refused either way.
            guard let text = item.stringValue, seen.insert(text.unicodeScalars.map(\.value)).inserted,
                let normalised = OwnerTurnText.normalisePath(text)
            else { return nil }
            prefixes.append(normalised)
        }
        guard let sessions = fields["max_sessions"]?.intValue, (1...OwnerTurnLimits.maxSessions).contains(sessions),
            let turns = fields["max_turns"]?.intValue, (1...OwnerTurnLimits.maxTurns).contains(turns)
        else { return nil }
        return OwnerTurnArgs(since: since, prefixes: prefixes, maxSessions: Int(sessions), maxTurns: Int(turns))
    }
}

/// One owner turn as a reader yields it (`(session_id, cwd, turn_id, at,
/// raw_text)`; `cwd` in the form the prefix match reports).
struct OwnerTurnRow {
    var sessionID: String
    var cwd: String
    var turnID: String
    var at: Int64
    var text: String
}

// MARK: - Collect (owner_turns.py collect_files) and the canonical cut

extension NodeReaders {
    /// `_owner_turn_match`: a row's `cwd` matches when its absolute or `~/`
    /// form lies under a prefix; it is reported in the form of the prefix
    /// that matched (absolute first).
    func ownerTurnMatch(_ prefixes: [String]) -> (String) -> String? {
        let absolutePrefixes = prefixes.filter { Scalars.hasPrefix($0, "/") }
        let homePrefixes = prefixes.filter { !Scalars.hasPrefix($0, "/") }
        let home = self.home
        return { cwd in
            guard let forms = ReaderPaths.pathForms(cwd, home: home) else { return nil }
            let absolute = forms.forms[0]
            if absolutePrefixes.contains(where: { ReaderPaths.pathMatches($0, absolute) }) { return absolute }
            if !Scalars.equal(forms.display, absolute),
                homePrefixes.contains(where: { ReaderPaths.pathMatches($0, forms.display) })
            {
                return forms.display
            }
            return nil
        }
    }

    /// One owner-turn read: args, then the capability's rows, then collect.
    func ownerTurns(_ capability: String, _ args: JSONValue, now: Int64) throws -> JSONValue {
        guard let parsed = try OwnerTurnArgs.validate(args, nowSeconds: now) else { throw ReaderError() }
        let match = ownerTurnMatch(parsed.prefixes)
        let rows: [OwnerTurnRow]
        let hashedIDs: Bool
        if capability == "claude.transcripts.owner_turns" {
            rows = try claudeOwnerTurnRows(match: match)
            hashedIDs = false
        } else {
            rows = try codexOwnerTurnRows(match: match)
            hashedIDs = true
        }
        return Self.collectOwnerTurns(rows, args: parsed, hashedIDs: hashedIDs)
    }

    /// The compact-JSON size the reference's `_size` measures (the
    /// canonical form has the same bytes, only the key order differs).
    static func compactSize(_ value: JSONValue) -> Int { (try? JSON.canonical(value).count) ?? Int.max }

    /// `collect_files` over rows already matched. `hashedIDs`: a Codex
    /// `turn_id` is the node's own hash, exempt from the identifier check.
    static func collectOwnerTurns(_ rows: [OwnerTurnRow], args: OwnerTurnArgs, hashedIDs: Bool) -> JSONValue {
        func kept(_ value: String) -> Bool { Scalars.equal(Scrubber.scrub(value), value) }
        let candidates = rows.enumerated().filter { _, row in
            row.at >= args.since && kept(row.sessionID) && kept(row.cwd) && (hashedIDs || kept(row.turnID))
        }.sorted { lhs, rhs in
            if lhs.element.at != rhs.element.at { return lhs.element.at < rhs.element.at }
            if !Scalars.equal(lhs.element.turnID, rhs.element.turnID) {
                return Scalars.less(lhs.element.turnID, rhs.element.turnID)
            }
            return lhs.offset < rhs.offset  // Python's sort is stable
        }.map(\.element)

        struct Key: Hashable {
            let session: [UInt32]
            let cwd: [UInt32]
        }
        var sessions: [Key: (sessionID: String, cwd: String, turns: [JSONValue])] = [:]
        var order: [Key] = []
        var taken = 0
        var size = compactSize([
            "sessions": [], "next_since": .string(OwnerInstant.format(args.since)), "truncated": true,
        ])
        var truncated = false
        var lastAt = args.since
        for row in candidates {
            let key = Key(session: row.sessionID.unicodeScalars.map(\.value), cwd: row.cwd.unicodeScalars.map(\.value))
            let turn: JSONValue = [
                "turn_id": .string(row.turnID), "at": .string(OwnerInstant.format(row.at)),
                "text": .string(Scalars.prefix(Scrubber.scrub(row.text), OwnerTurnLimits.maxTextChars)),
            ]
            var added = compactSize(turn) + 1
            let isNew = sessions[key] == nil
            if isNew {
                added += compactSize(["session_id": .string(row.sessionID), "cwd": .string(row.cwd), "turns": []]) + 1
            }
            if taken >= args.maxTurns || (isNew && order.count >= args.maxSessions)
                || size + added > OwnerTurnLimits.maxResultBytes
            {
                truncated = true
                break
            }
            if isNew {
                sessions[key] = (row.sessionID, row.cwd, [])
                order.append(key)
            }
            sessions[key]!.turns.append(turn)
            taken += 1
            size += added
            lastAt = row.at
        }
        let out: [JSONValue] = order.map { key in
            let session = sessions[key]!
            return ["session_id": .string(session.sessionID), "cwd": .string(session.cwd), "turns": .array(session.turns)]
        }
        return ["sessions": .array(out), "next_since": .string(OwnerInstant.format(lastAt)), "truncated": .bool(truncated)]
    }

    /// readers.py `_fit_owner_turns`: cut to `limit` canonical bytes, the
    /// newest turns first in `(at, turn_id)` order (sessions left without a
    /// turn go too); `next_since` is the `at` of the last surviving turn
    /// (the earliest dropped one when none survives), so the cursor never
    /// skips a dropped turn.
    static func fitOwnerTurns(_ output: JSONValue, limit: Int) throws -> JSONValue {
        guard var fields = output.objectValue, let sessions = output["sessions"]?.arrayValue else {
            throw ReaderError()
        }
        var order: [(at: String, turnID: String, session: Int, position: Int)] = []
        for (i, session) in sessions.enumerated() {
            for (j, turn) in (session["turns"]?.arrayValue ?? []).enumerated() {
                order.append((turn["at"]?.stringValue ?? "", turn["turn_id"]?.stringValue ?? "", i, j))
            }
        }
        order.sort { lhs, rhs in
            if !Scalars.equal(lhs.at, rhs.at) { return Scalars.less(lhs.at, rhs.at) }
            if !Scalars.equal(lhs.turnID, rhs.turnID) { return Scalars.less(lhs.turnID, rhs.turnID) }
            if lhs.session != rhs.session { return lhs.session < rhs.session }
            return lhs.position < rhs.position
        }
        var keep = order.count
        while true {
            keep = max(keep - 1, 0)
            struct Spot: Hashable {
                let session: Int
                let position: Int
            }
            let kept = Set(order.prefix(keep).map { Spot(session: $0.session, position: $0.position) })
            var rows: [JSONValue] = []
            for (i, session) in sessions.enumerated() {
                let turns = (session["turns"]?.arrayValue ?? []).enumerated()
                    .filter { kept.contains(Spot(session: i, position: $0.offset)) }.map(\.element)
                if !turns.isEmpty, var row = session.objectValue {
                    row["turns"] = .array(turns)
                    rows.append(.object(row))
                }
            }
            let nextSince: JSONValue
            if keep > 0 {
                nextSince = .string(order[keep - 1].at)
            } else {
                nextSince = order.first.map { .string($0.at) } ?? (output["next_since"] ?? .null)
            }
            fields["sessions"] = .array(rows)
            fields["next_since"] = nextSince
            fields["truncated"] = true
            let candidate = JSONValue.object(fields)
            if keep == 0 { return candidate }
            if try JSON.canonical(candidate).count <= limit { return candidate }
        }
    }
}
