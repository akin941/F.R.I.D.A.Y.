// The node's bounded hook-event spool (Phase 9 brief M3, D10, D13 item 7;
// a port of backend/nodes/reference/spool.py).
//
// Hook events that cannot be sent (the socket is down, or an upload is in
// flight) are written here and uploaded after reconnect, in order, one batch
// in flight, each deleted only after its `event.ack`. Normative limits:
// - events only: a batch is a list of 1-50 hook events (JSON objects);
// - <= 10 MiB in total: after a write the oldest batches are dropped first
//   until the spool fits; one batch larger than the whole budget is refused;
// - <= 24 h: an older batch is dropped;
// - the directory is 0700 (owned by this user, never a symlink), every batch
//   file 0600, created with O_EXCL | O_NOFOLLOW;
// - a batch is deleted only by `ack(batchID)` (or by the caps): an upload
//   never acked stays and is sent again after the next reconnect (the server
//   dedupes on `event_id`).
// Batches survive a restart: files `<seq:012d>.json` holding
// `{"created_at": <unix seconds>, "events": [...]}`.

import Foundation

public struct SpoolBatch: Equatable {
    public let batchID: String
    public let createdAt: Int64
    public let events: [JSONValue]
    public let size: Int
}

public enum SpoolError: Error, Equatable {
    case unsafeDirectory
}

public final class Spool {
    public static let maxBytes = 10 * 1024 * 1024
    public static let maxAgeSeconds: Int64 = 24 * 3600
    public static let maxBatchEvents = 50
    static let nameDigits = 12

    public let directory: String
    let clock: NodeClock
    let maxBytes: Int
    let maxAge: Int64
    public private(set) var dropped = 0
    private var nextSeq: Int

    public init(directory: String, clock: NodeClock, maxBytes: Int = Spool.maxBytes, maxAge: Int64 = Spool.maxAgeSeconds)
        throws
    {
        self.directory = directory
        self.clock = clock
        self.maxBytes = maxBytes
        self.maxAge = maxAge
        try Self.prepareDirectory(directory)
        nextSeq = 1
        nextSeq = (paths().compactMap { Int(($0 as NSString).lastPathComponent.prefix(Self.nameDigits)) }.max() ?? 0) + 1
    }

    /// mkdir 0700; refuse a symlink, a non-directory or another owner; then
    /// tighten an existing loose mode to 0700.
    static func prepareDirectory(_ path: String) throws {
        try? FileManager.default.createDirectory(
            atPath: path, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        var info = stat()
        guard lstat(path, &info) == 0, (info.st_mode & S_IFMT) == S_IFDIR, info.st_uid == getuid() else {
            throw SpoolError.unsafeDirectory
        }
        guard chmod(path, 0o700) == 0 else { throw SpoolError.unsafeDirectory }
    }

    static func isValid(_ events: [JSONValue]) -> Bool {
        (1...maxBatchEvents).contains(events.count) && events.allSatisfy { $0.objectValue != nil }
    }

    // MARK: Files

    private func paths() -> [String] {
        let names = (try? FileManager.default.contentsOfDirectory(atPath: directory)) ?? []
        return names.filter { name in
            guard name.hasSuffix(".json") else { return false }
            let stem = name.dropLast(5)
            guard stem.count == Self.nameDigits, stem.allSatisfy({ $0.isASCII && $0.isNumber }) else { return false }
            var info = stat()
            let path = directory + "/" + name
            return lstat(path, &info) == 0 && (info.st_mode & S_IFMT) == S_IFREG
        }.sorted().map { directory + "/" + $0 }
    }

    private func load(_ path: String) -> SpoolBatch? {
        guard let data = FileManager.default.contents(atPath: path), let doc = try? JSON.parse(data),
            let createdAt = doc["created_at"]?.intValue, let events = doc["events"]?.arrayValue, Self.isValid(events)
        else { return nil }
        let stem = String((path as NSString).lastPathComponent.dropLast(5))
        return SpoolBatch(batchID: stem, createdAt: createdAt, events: events, size: data.count)
    }

    private func remove(_ path: String) {
        if unlink(path) == 0 { dropped += 1 }
    }

    // MARK: Writing

    /// Spool one batch; its id, or nil when refused (not 1-50 objects, or
    /// larger than the whole budget).
    @discardableResult
    public func append(_ events: [JSONValue]) -> String? {
        guard Self.isValid(events),
            let body = try? JSON.canonical(["created_at": .int(clock.now()), "events": .array(events)]),
            body.count <= maxBytes
        else {
            dropped += 1
            return nil
        }
        let batchID = String(format: "%012d", nextSeq)
        nextSeq += 1
        let path = directory + "/" + batchID + ".json"
        let fd = open(path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
        guard fd >= 0 else {
            dropped += 1
            return nil
        }
        defer { close(fd) }
        fchmod(fd, 0o600)
        let written = body.withUnsafeBytes { write(fd, $0.baseAddress, $0.count) }
        guard written == body.count else {
            unlink(path)
            dropped += 1
            return nil
        }
        enforce()
        return batchID
    }

    /// Apply the age and size caps (oldest dropped first).
    public func enforce() {
        let now = clock.now()
        var batches: [(String, SpoolBatch)] = []
        for path in paths() {
            guard let batch = load(path), now - batch.createdAt <= maxAge else {
                remove(path)
                continue
            }
            batches.append((path, batch))
        }
        var total = batches.reduce(0) { $0 + $1.1.size }
        while !batches.isEmpty && total > maxBytes {
            let (path, batch) = batches.removeFirst()
            remove(path)
            total -= batch.size
        }
    }

    // MARK: Reading and acking

    /// The spooled batches, oldest first (after the caps).
    public func pending() -> [SpoolBatch] {
        enforce()
        return paths().compactMap(load)
    }

    public func first() -> SpoolBatch? { pending().first }

    /// Delete a batch after the server acked it.
    @discardableResult
    public func ack(_ batchID: String) -> Bool {
        guard batchID.count == Self.nameDigits, batchID.allSatisfy({ $0.isASCII && $0.isNumber }) else { return false }
        return unlink(directory + "/" + batchID + ".json") == 0
    }

    /// `(count, bytes)` for the heartbeat.
    public func depth() -> (count: Int, bytes: Int) {
        let paths = paths()
        let bytes = paths.reduce(0) { total, path in
            total + ((try? FileManager.default.attributesOfItem(atPath: path)[.size] as? Int) ?? 0)
        }
        return (paths.count, bytes)
    }
}
