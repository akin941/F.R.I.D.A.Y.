// The nightly selftest's way into the app (Phase 11 brief M3, code half;
// runbook B8; docs/voice/live-eval.md §4).
//
// Why the app and not the CLI (README "Nightly self-trigger"): the
// Microphone TCC grant and the Keychain node secret both belong to the
// signed FridayNode.app. A launchd job's processes are attributed to the
// job itself (`/bin/sh` running selftest-nightly.sh), which holds no
// Microphone grant and can never be asked for one at 03:17; and the node
// secret lives in the data protection keychain under the app's
// `keychain-access-groups` entitlement, which a separate, unentitled
// `friday-node` binary cannot read. So the CLI is a thin client of the
// running app:
//
//   friday-node voice-token --out <file>   -> {"op":"begin"}
//   friday-node voice-loopback             -> {"op":"loopback","body_bytes":N} + WAV
//   friday-node voice-token --end <file>   -> {"op":"end"}
//
// over `VoiceSelftestSocket` (a user-owned Unix socket next to the hook
// socket, the same rules: 0600 in a 0700 directory, the peer uid checked
// before a byte is read, bounded reads; no TCP listener). Each request is one
// JSON line plus an optional body; each answer is one JSON line plus an
// optional body: `{"ok": true, ...}` or `{"ok": false, "error": "<code>"}`.
//
// The app's side (`VoiceSelftestHost`) refuses everything while the owner's
// "Gece öz-testi" setting is off (Keychain, LocalAuthentication to turn on),
// while the kill switch is engaged or the node is unpaired. `begin` takes the
// selftest hold (the live voice child stops, the mic is released: one voice
// at a time, and the node's one live `node_voice` token is the selftest's),
// mints a fresh token with the node secret and hands it to the CLI, which
// writes it to a 0600 file for `--token-file` and nowhere else. The hold
// ends with `end` or after `leaseSeconds` (the token's own 15 min), and the
// live voice then mints again, superseding the selftest's token.
// `loopback` runs only inside a hold.

import Foundation

public protocol VoiceSelftestHandler: AnyObject {
    func selftestBegin() throws -> VoiceToken
    func selftestEnd()
    func selftestLoopback(_ wav: Data) throws -> Data
}

/// What the selftest needs from the live voice host (VoiceService).
public protocol VoiceSelftestHold: AnyObject {
    @discardableResult func setSelftestHold(_ on: Bool, timeout: Double) -> Bool
    var micInUse: Bool { get }
}

extension VoiceService: VoiceSelftestHold {}

// MARK: The app's side

public final class VoiceSelftestHost: VoiceSelftestHandler, @unchecked Sendable {
    public static let leaseSeconds = 15.0 * 60
    public static let mintTimeoutSeconds = 30.0

    private let enabled: () -> Bool
    private let hold: VoiceSelftestHold
    private let credential: () -> NodeCredential?
    private let killSwitch: () -> Bool
    private let mint: (NodeCredential) async throws -> VoiceToken
    private let makeAudio: () -> VoiceAudioIO
    private let micAuthorized: () -> Bool
    private let lease: Double
    private let slackSeconds: Double
    private let lock = NSLock()
    private var leaseID = 0
    private var leaseActive = false
    private var loopbackRunning = false
    /// The lease ran out while a loopback held the mic: the hold ends when
    /// that loopback does, never under it.
    private var expiryDeferred = false

    public init(
        enabled: @escaping () -> Bool, hold: VoiceSelftestHold, credential: @escaping () -> NodeCredential?,
        killSwitch: @escaping () -> Bool,
        mint: @escaping (NodeCredential) async throws -> VoiceToken = { try await VoiceCredential.mint($0) },
        makeAudio: @escaping () -> VoiceAudioIO, micAuthorized: @escaping () -> Bool = VoiceLoopback.microphoneAuthorized,
        leaseSeconds: Double = VoiceSelftestHost.leaseSeconds,
        slackSeconds: Double = VoiceLoopback.defaultSlackSeconds
    ) {
        self.enabled = enabled
        self.hold = hold
        self.credential = credential
        self.killSwitch = killSwitch
        self.mint = mint
        self.makeAudio = makeAudio
        self.micAuthorized = micAuthorized
        self.lease = leaseSeconds
        self.slackSeconds = slackSeconds
    }

    public var holding: Bool {
        lock.lock()
        defer { lock.unlock() }
        return leaseActive
    }

    private func gates() throws -> NodeCredential {
        guard enabled() else { throw VoiceSelftestCode.selftestDisabled }
        guard !killSwitch() else { throw VoiceSelftestCode.killSwitch }
        guard let credential = credential() else { throw VoiceSelftestCode.notPaired }
        return credential
    }

    public func selftestBegin() throws -> VoiceToken {
        let credential = try gates()
        lock.lock()
        guard !leaseActive else {
            lock.unlock()
            throw VoiceSelftestCode.busy
        }
        leaseActive = true
        expiryDeferred = false
        leaseID += 1
        let id = leaseID
        lock.unlock()
        guard hold.setSelftestHold(true, timeout: 3) else {
            selftestEnd()
            throw VoiceSelftestCode.micBusy
        }
        DispatchQueue.global().asyncAfter(deadline: .now() + lease) { [weak self] in self?.expire(id) }
        let outcome = VoiceSelftestHost.blocking(timeout: Self.mintTimeoutSeconds) { [mint] in try await mint(credential) }
        switch outcome {
        case .success(let token)?:
            return token
        case .failure(VoiceCredentialError.refused)?:
            selftestEnd()
            throw VoiceSelftestCode.credentialRefused
        default:
            selftestEnd()
            throw VoiceSelftestCode.mintFailed
        }
    }

    private func expire(_ id: Int) {
        lock.lock()
        guard leaseActive, leaseID == id else {
            lock.unlock()
            return
        }
        if loopbackRunning {
            expiryDeferred = true  // ended by the loopback's own exit
            lock.unlock()
            return
        }
        lock.unlock()
        selftestEnd()
    }

    public func selftestEnd() {
        lock.lock()
        let was = leaseActive
        leaseActive = false
        lock.unlock()
        if was { hold.setSelftestHold(false, timeout: 0) }
    }

    public func selftestLoopback(_ wav: Data) throws -> Data {
        _ = try gates()
        lock.lock()
        guard leaseActive else {
            lock.unlock()
            throw VoiceSelftestCode.noSelftestHold
        }
        guard !loopbackRunning else {
            lock.unlock()
            throw VoiceSelftestCode.busy
        }
        loopbackRunning = true
        lock.unlock()
        defer {
            lock.lock()
            loopbackRunning = false
            let expired = expiryDeferred
            expiryDeferred = false
            lock.unlock()
            if expired { selftestEnd() }
        }
        guard !hold.micInUse else { throw VoiceSelftestCode.micBusy }
        return try VoiceLoopback.run(
            wav: wav, audio: makeAudio(), micAuthorized: micAuthorized, slackSeconds: slackSeconds)
    }

    /// Run `body` to completion on a task and wait at most `timeout`
    /// seconds; nil when it did not finish.
    static func blocking<T>(timeout: Double, _ body: @escaping @Sendable () async throws -> T) -> Result<T, Error>? {
        let box = ResultBox<T>()
        let done = DispatchSemaphore(value: 0)
        Task {
            do {
                box.set(.success(try await body()))
            } catch {
                box.set(.failure(error))
            }
            done.signal()
        }
        guard done.wait(timeout: .now() + timeout) == .success else { return nil }
        return box.get()
    }
}

final class ResultBox<T>: @unchecked Sendable {
    private let lock = NSLock()
    private var value: Result<T, Error>?
    func set(_ result: Result<T, Error>) {
        lock.lock()
        value = result
        lock.unlock()
    }
    func get() -> Result<T, Error>? {
        lock.lock()
        defer { lock.unlock() }
        return value
    }
}

// MARK: The socket (app)

public final class VoiceSelftestSocket: @unchecked Sendable {
    public static let headerLimit = 4096
    /// Connections are served one at a time (the loopback holds the one
    /// microphone anyway), so a client that stalls holds the others back for
    /// at most this long; a real request (a ≤ 2 MB clip over a local socket)
    /// arrives in milliseconds.
    public static let readSeconds = 2

    /// ~/Library/Application Support/Friday/voice-selftest.sock.
    public static var defaultPath: String {
        NSHomeDirectory() + "/Library/Application Support/Friday/voice-selftest.sock"
    }

    public let path: String
    private weak var handler: VoiceSelftestHandler?
    private let expectedUID: uid_t
    private let peerUID: (Int32) -> uid_t?
    private let lock = NSLock()
    private var fd: Int32 = -1

    public init(
        path: String = VoiceSelftestSocket.defaultPath, handler: VoiceSelftestHandler, expectedUID: uid_t = getuid(),
        peerUID: @escaping (Int32) -> uid_t? = HookSocket.getpeereidUID
    ) {
        self.path = path
        self.handler = handler
        self.expectedUID = expectedUID
        self.peerUID = peerUID
    }

    public func start() throws {
        lock.lock()
        let running = fd >= 0
        lock.unlock()
        guard !running else { return }
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
                return
            }
            handle(connection)
            close(connection)
        }
    }

    /// One request, one answer. Never more than one at a time.
    func handle(_ connection: Int32) {
        guard let uid = peerUID(connection), uid == expectedUID else { return }  // not a byte read
        LocalSocket.setTimeouts(connection, seconds: Self.readSeconds)
        let deadline = Date().addingTimeInterval(TimeInterval(Self.readSeconds))
        let answer: (JSONValue, Data)
        do {
            answer = try respond(connection, deadline: deadline)
        } catch let code as VoiceSelftestCode {
            answer = (["ok": false, "error": .string(code.rawValue)], Data())
        } catch {
            answer = (["ok": false, "error": .string(VoiceSelftestCode.protocolError.rawValue)], Data())
        }
        guard var line = try? JSON.canonical(answer.0) else { return }
        line.append(0x0A)
        if LocalSocket.sendAll(connection, line), !answer.1.isEmpty { _ = LocalSocket.sendAll(connection, answer.1) }
    }

    private func respond(_ connection: Int32, deadline: Date) throws -> (JSONValue, Data) {
        guard let line = LocalSocket.readLine(connection, limit: Self.headerLimit, deadline: deadline),
            let request = try? JSON.parse(line), let fields = request.objectValue, let op = fields["op"]?.stringValue
        else { throw VoiceSelftestCode.protocolError }
        guard let handler else { throw VoiceSelftestCode.appUnreachable }
        switch op {
        case "begin" where fields.count == 1:
            let token = try handler.selftestBegin()
            return (
                [
                    "ok": true, "token": .string(token.token), "expires_at": .string(token.expiresAt),
                    "server_url": .string(token.serverURL), "ca_sha256": token.caSHA256.map { .string($0) } ?? .null,
                ], Data()
            )
        case "end" where fields.count == 1:
            handler.selftestEnd()
            return (["ok": true], Data())
        case "loopback" where fields.count == 2:
            guard let size = fields["body_bytes"]?.intValue, size >= 0, size <= Int64(VoiceLoopback.maxInputBytes)
            else { throw VoiceSelftestCode.protocolError }
            guard let wav = LocalSocket.readExactly(connection, count: Int(size), deadline: deadline)
            else { throw VoiceSelftestCode.protocolError }
            let capture = try handler.selftestLoopback(wav)
            return (["ok": true, "body_bytes": .int(Int64(capture.count))], capture)
        default:
            throw VoiceSelftestCode.protocolError
        }
    }
}

// MARK: The CLI's side

public struct SelftestToken: Equatable, Sendable {
    public let token: String
    public let expiresAt: String
    public let serverURL: String
    public let caSHA256: String?
}

public enum VoiceSelftestClient {
    /// Longer than a 60 s clip plus its tail and the loopback's slack.
    public static let timeoutSeconds = 100

    static func call(_ request: JSONValue, body: Data = Data(), path: String) throws -> ([String: JSONValue], Data) {
        guard let socketFD = LocalSocket.connect(path: path, timeoutSeconds: timeoutSeconds) else {
            throw VoiceSelftestCode.appUnreachable
        }
        defer { close(socketFD) }
        guard var line = try? JSON.canonical(request) else { throw VoiceSelftestCode.protocolError }
        line.append(0x0A)
        guard LocalSocket.sendAll(socketFD, line), LocalSocket.sendAll(socketFD, body) else {
            throw VoiceSelftestCode.appUnreachable
        }
        let deadline = Date().addingTimeInterval(TimeInterval(timeoutSeconds))
        guard let answerLine = LocalSocket.readLine(socketFD, limit: VoiceSelftestSocket.headerLimit, deadline: deadline),
            let answer = try? JSON.parse(answerLine).objectValue, let ok = answer["ok"]?.boolValue
        else { throw VoiceSelftestCode.protocolError }
        guard ok else {
            let code = answer["error"]?.stringValue.flatMap(VoiceSelftestCode.init(rawValue:))
            throw code ?? VoiceSelftestCode.protocolError
        }
        var payload = Data()
        if let size = answer["body_bytes"]?.intValue {
            guard size >= 0, size <= Int64(VoiceLoopback.maxInputBytes),
                let data = LocalSocket.readExactly(socketFD, count: Int(size), deadline: deadline)
            else { throw VoiceSelftestCode.protocolError }
            payload = data
        }
        return (answer, payload)
    }

    public static func begin(path: String = VoiceSelftestSocket.defaultPath) throws -> SelftestToken {
        let (answer, _) = try call(["op": "begin"], path: path)
        guard let token = answer["token"]?.stringValue, let expiresAt = answer["expires_at"]?.stringValue,
            let server = answer["server_url"]?.stringValue,
            answer["ca_sha256"] == .null || answer["ca_sha256"]?.stringValue != nil
        else { throw VoiceSelftestCode.protocolError }
        return SelftestToken(
            token: token, expiresAt: expiresAt, serverURL: server, caSHA256: answer["ca_sha256"]?.stringValue)
    }

    public static func end(path: String = VoiceSelftestSocket.defaultPath) throws {
        _ = try call(["op": "end"], path: path)
    }

    public static func loopback(_ wav: Data, path: String = VoiceSelftestSocket.defaultPath) throws -> Data {
        let (_, capture) = try call(["op": "loopback", "body_bytes": .int(Int64(wav.count))], body: wav, path: path)
        return capture
    }
}

/// The token file for `friday-voice selftest --token-file`: created new
/// (never over an existing file or through a symlink), 0600, owned by this
/// user, in a directory only this user can write; removed after the run.
public enum SelftestTokenFile {
    /// Create the empty file (before anything is minted).
    public static func create(_ path: String) throws -> Int32 {
        let directory = (path as NSString).deletingLastPathComponent
        var info = stat()
        guard path.hasPrefix("/"), lstat(directory, &info) == 0, (info.st_mode & S_IFMT) == S_IFDIR,
            info.st_uid == getuid(), info.st_mode & 0o022 == 0
        else { throw VoiceSelftestCode.tokenFileUnsafe }
        let fd = open(path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0o600)
        guard fd >= 0 else { throw VoiceSelftestCode.tokenFileUnsafe }
        guard fchmod(fd, 0o600) == 0, fstat(fd, &info) == 0, (info.st_mode & S_IFMT) == S_IFREG,
            info.st_uid == getuid()
        else {
            close(fd)
            unlink(path)
            throw VoiceSelftestCode.tokenFileUnsafe
        }
        return fd
    }

    /// Write the token (one line) and close.
    public static func write(_ token: String, to fd: Int32, path: String) throws {
        defer { close(fd) }
        let data = Data((token + "\n").utf8)
        let written = data.withUnsafeBytes { Darwin.write(fd, $0.baseAddress, $0.count) }
        guard written == data.count else {
            unlink(path)
            throw VoiceSelftestCode.tokenFileUnsafe
        }
    }

    /// Remove the file if it is one (a symlink is removed, never followed).
    public static func remove(_ path: String) {
        var info = stat()
        guard lstat(path, &info) == 0 else { return }
        let kind = info.st_mode & S_IFMT
        if kind == S_IFREG || kind == S_IFLNK { unlink(path) }
    }
}
