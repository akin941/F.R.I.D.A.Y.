// The node's hook relay (Phase 9 brief M3, D10, D13 item 7; ADR-013
// decision 7; a port of backend/nodes/reference/hook_relay.py).
//
// Claude Code runs `friday-node hook` as a command hook; the CLI writes its
// stdin to a user-owned Unix domain socket and exits 0 within 3 s whatever
// happens. The app listens on that socket (`HookSocket`):
// - the socket is 0600 inside a 0700 directory owned by this user (an
//   existing directory with another owner, or a symlink, is refused);
// - every connection's peer uid is checked with getpeereid() before a byte
//   is read; another uid (or a failing check) is refused;
// - input over 256 KiB is dropped whole, as is anything not a JSON object;
// - the payload is reduced to the 8A hook-event shape exactly as
//   `friday-hook` does (`HookEvent.build`: the allow-listed event names and
//   fields, the free-text `message` scrubbed first and then cut), so the
//   server dedupes a relayed event on `event_id`;
// - the event is handed to `deliver`; nothing from the socket ever becomes
//   a command. No TCP listener of any kind (D13 item 1).

import Foundation

public enum HookEvent {
    public static let allowedEvents: Set<String> = ["SessionStart", "Notification", "Stop", "SessionEnd"]
    public static let stringFields = ["hook_event_name", "session_id", "cwd", "notification_type", "source", "reason"]
    public static let maxFieldChars = 1000
    public static let maxMessageChars = 200
    public static let maxInputBytes = 256 * 1024

    /// The allow-listed, scrubbed hook event (friday-hook `build_event`), or
    /// nil when the payload is not one Friday ingests.
    public static func build(_ payload: LooseJSONValue, now: Int64, eventID: String) -> JSONValue? {
        guard let fields = payload.fields, fields["hook_event_name"].flatMap(\.text).map(allowedEvents.contains) == true
        else { return nil }
        var event: [String: JSONValue] = [:]
        for key in stringFields {
            if let value = fields[key]?.text, !value.isEmpty {
                event[key] = .string(Scalars.prefix(value, maxFieldChars))
            }
        }
        if let message = fields["message"]?.text, !message.isEmpty {
            // Scrub first, then cut: a cut never leaves half a secret behind.
            let scrubbed = Scrubber.scrub(Scalars.prefix(message, maxFieldChars * 4))
            event["message"] = .string(Scalars.prefix(scrubbed, maxMessageChars))
        }
        event["occurred_at"] = .string(isoformat(now))
        event["event_id"] = .string(eventID)
        return .object(event)
    }

    /// `datetime.fromtimestamp(now, UTC).isoformat()` for whole seconds.
    static func isoformat(_ seconds: Int64) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(identifier: "UTC")
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        return formatter.string(from: Date(timeIntervalSince1970: TimeInterval(seconds))) + "+00:00"
    }

    /// `uuid.uuid4().hex`.
    public static func newID() -> String {
        UUID().uuidString.lowercased().replacingOccurrences(of: "-", with: "")
    }

    /// Parse the hook's stdin like `json.loads` (it is Claude Code's JSON).
    public static func parse(_ data: Data) -> LooseJSONValue? {
        LooseJSON.parse(data).map(LooseJSONValue.init)
    }
}

/// A public view of a parsed hook payload (only what `build` reads).
public struct LooseJSONValue {
    let value: LooseJSON
    init(_ value: LooseJSON) { self.value = value }
    var fields: [String: LooseJSONValue]? { value.object?.mapValues(LooseJSONValue.init) }
    var text: String? { value.string }
}

public enum HookSocketError: Error, Equatable {
    case unsafeDirectory
    case unsafePath
    case socket
}

public enum HookOutcome: String {
    case relayed, ignored, malformed, oversized, peerRefused = "peer_refused"
}

public final class HookSocket: @unchecked Sendable {
    public static let readTimeoutSeconds = 2

    public let path: String
    private let deliver: (JSONValue) -> Void
    private let clock: NodeClock
    private let expectedUID: uid_t
    private let peerUID: (Int32) -> uid_t?
    private let makeID: () -> String
    private var fd: Int32 = -1
    private let lock = NSLock()
    private var outcomeCounts: [HookOutcome: Int] = [:]

    /// How many connections ended with `outcome` (thread-safe: the accept
    /// loop runs on its own thread).
    public func count(of outcome: HookOutcome) -> Int {
        lock.lock()
        defer { lock.unlock() }
        return outcomeCounts[outcome] ?? 0
    }

    /// `peerUID` reads a connection's peer uid (getpeereid by default; tests
    /// inject a refusing one).
    public init(
        path: String, clock: NodeClock, deliver: @escaping (JSONValue) -> Void, expectedUID: uid_t = getuid(),
        peerUID: @escaping (Int32) -> uid_t? = HookSocket.getpeereidUID, makeID: @escaping () -> String = HookEvent.newID
    ) {
        self.path = path
        self.clock = clock
        self.deliver = deliver
        self.expectedUID = expectedUID
        self.peerUID = peerUID
        self.makeID = makeID
    }

    public static func getpeereidUID(_ connection: Int32) -> uid_t? {
        var uid: uid_t = 0
        var gid: gid_t = 0
        return getpeereid(connection, &uid, &gid) == 0 ? uid : nil
    }

    /// The default socket: ~/Library/Application Support/Friday/node.sock.
    public static var defaultPath: String {
        NSHomeDirectory() + "/Library/Application Support/Friday/node.sock"
    }

    // MARK: Lifecycle

    public func start() throws {
        lock.lock()
        let running = fd >= 0
        lock.unlock()
        guard !running else { return }  // already listening
        let socketFD = try LocalSocket.listen(path: path)
        lock.lock()
        fd = socketFD
        lock.unlock()
        Thread.detachNewThread { [weak self] in self?.acceptLoop(socketFD) }
    }

    public func stop() {
        lock.lock()
        let socketFD = fd
        fd = -1
        lock.unlock()
        if socketFD >= 0 {
            shutdown(socketFD, SHUT_RDWR)
            close(socketFD)
        }
        LocalSocket.unlinkSocket(path)
    }

    private func acceptLoop(_ socketFD: Int32) {
        while true {
            let connection = accept(socketFD, nil, nil)
            if connection < 0 {
                if errno == EINTR { continue }
                return  // closed by stop()
            }
            handle(connection)
        }
    }

    private func count(_ outcome: HookOutcome) {
        lock.lock()
        outcomeCounts[outcome, default: 0] += 1
        lock.unlock()
    }

    /// One connection: peer uid first, then a bounded read, then the event.
    func handle(_ connection: Int32) {
        guard let uid = peerUID(connection), uid == expectedUID else {
            close(connection)
            count(.peerRefused)
            return
        }
        let data = Self.readBounded(connection, limit: HookEvent.maxInputBytes)
        close(connection)
        guard let data else {
            count(.oversized)
            return
        }
        guard let payload = HookEvent.parse(data) else {
            count(.malformed)
            return
        }
        guard let event = HookEvent.build(payload, now: clock.now(), eventID: makeID()) else {
            count(.ignored)
            return
        }
        deliver(event)
        count(.relayed)
    }

    /// At most `limit` bytes until EOF, or nil when the peer sends more
    /// (dropped whole) or stalls past the read timeout.
    static func readBounded(_ connection: Int32, limit: Int) -> Data? {
        var timeout = timeval(tv_sec: readTimeoutSeconds, tv_usec: 0)
        setsockopt(connection, SOL_SOCKET, SO_RCVTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
        // SO_RCVTIMEO bounds one recv; the whole read is bounded too, so a
        // client trickling a byte at a time cannot hold the accept thread.
        let deadline = Date().addingTimeInterval(TimeInterval(readTimeoutSeconds))
        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 65_536)
        while true {
            if Date() > deadline { return nil }
            let count = recv(connection, &buffer, buffer.count, 0)
            if count == 0 { break }
            if count < 0 { return nil }
            data.append(contentsOf: buffer[0..<count])
            if data.count > limit { return nil }
        }
        return data
    }
}

/// `friday-node hook`: write `data` (the hook's stdin) to the node's socket.
/// Never fails Claude Code: the caller exits 0 whatever this returns.
public enum HookClient {
    public static let deadlineSeconds = 3

    public static func relay(_ data: Data, to path: String) {
        guard let socketFD = LocalSocket.connect(path: path, timeoutSeconds: deadlineSeconds) else { return }
        defer { close(socketFD) }
        let payload = [UInt8](data.prefix(HookEvent.maxInputBytes + 1))
        var offset = 0
        while offset < payload.count {
            let sent = payload[offset...].withUnsafeBytes { send(socketFD, $0.baseAddress, $0.count, 0) }
            if sent <= 0 { return }  // the send timeout or a closed peer: drop it
            offset += sent
        }
        shutdown(socketFD, SHUT_WR)
    }
}
