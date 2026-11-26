// The node's outbound WebSocket (Phase 9 brief D6, D13 items 1-4; ADR-013
// decision 1: outbound only, no listener of any kind).
//
// `NodeConnection` is one socket: `wss://<server>/ws/node/v1`, subprotocol
// `friday-node.v1`, `Authorization: Bearer <jnd_...>`, no `Origin`, the TLS
// SPKI pin enforced in place of CA validation. It feeds every received frame
// to `NodeSession` and sends what the session answers; it never decides
// protocol behaviour itself.
//
// `NodeRunner` keeps the node connected: it reconnects after the session's
// full-jitter backoff, stops for good after `bye{revoked|kill_switch|
// credential_expired}` (until the owner acts in the menu) or a 4401 close
// (until re-paired), and rotates the secret when `rotate_after_days` has
// passed, before connecting.
//
// Everything runs on one serial queue; `NodeSession` is never touched from
// another thread.

import Foundation
import os

public enum CloseCode {
    public static let normal = 1000
    public static let tryAgainLater = 1013
    public static let badSubprotocol = 4400
    public static let unauthorized = 4401
    public static let originRefused = 4403
    public static let helloTimeout = 4408
    public static let rateLimited = 4429
}

public final class NodeConnection: NSObject, URLSessionWebSocketDelegate, @unchecked Sendable {
    public let url: URL
    private let secret: String
    private let pin: Data
    private let session: NodeSession
    private let queue: DispatchQueue
    private let autoTick: Bool
    private var urlSession: URLSession?
    private var task: URLSessionWebSocketTask?
    private var timer: DispatchSourceTimer?
    private var opened = false
    private var closing = false
    private var finished = false

    /// Called once on `queue` when the socket is gone, with the close code
    /// the server sent (nil when the connection failed or was dropped).
    public var onClose: ((Int?) -> Void)?
    /// Called on `queue` after every frame received or sent (status display).
    public var onActivity: (() -> Void)?

    /// `autoTick` drives `session.tick()` once a second from the system
    /// clock (the app); the conformance hook ticks the session itself.
    public init(url: URL, secret: String, pin: Data, session: NodeSession, queue: DispatchQueue, autoTick: Bool) {
        self.url = url
        self.secret = secret
        self.pin = pin
        self.session = session
        self.queue = queue
        self.autoTick = autoTick
    }

    public func start() {
        queue.async { self.open() }
    }

    /// Close from the node side (owner action, quit). Fails closed.
    public func stop() {
        queue.async { self.finish(code: nil, cancel: true) }
    }

    private func open() {
        let delegateQueue = OperationQueue()
        delegateQueue.underlyingQueue = queue
        delegateQueue.maxConcurrentOperationCount = 1
        let configuration = URLSessionConfiguration.ephemeral
        configuration.httpCookieStorage = nil
        configuration.urlCache = nil
        configuration.httpShouldSetCookies = false
        let pinning = PinningDelegate(pin: pin)
        let urlSession = URLSession(
            configuration: configuration, delegate: Forwarder(self, pinning), delegateQueue: delegateQueue)
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalAndRemoteCacheData, timeoutInterval: 15)
        request.setValue("Bearer \(secret)", forHTTPHeaderField: "Authorization")
        request.setValue(NodeProtocol.subprotocol, forHTTPHeaderField: "Sec-WebSocket-Protocol")
        let task = urlSession.webSocketTask(with: request)
        // Larger than the protocol's 256 KiB so an oversized frame reaches
        // the session (check 1, `too_large`) instead of killing the socket.
        task.maximumMessageSize = 1 << 20
        self.urlSession = urlSession
        self.task = task
        task.resume()
    }

    // MARK: URLSessionWebSocketDelegate (on `queue`)

    public func urlSession(
        _ session: URLSession, webSocketTask: URLSessionWebSocketTask, didOpenWithProtocol protocol: String?
    ) {
        guard webSocketTask === task, !finished else { return }
        guard `protocol` == NodeProtocol.subprotocol else {
            finish(code: nil, cancel: true)
            return
        }
        opened = true
        send(self.session.connect())
        receiveNext()
        if autoTick { startTimer() }
    }

    public func urlSession(
        _ session: URLSession, webSocketTask: URLSessionWebSocketTask,
        didCloseWith closeCode: URLSessionWebSocketTask.CloseCode, reason: Data?
    ) {
        guard webSocketTask === task else { return }
        finish(code: closeCode.rawValue, cancel: false)
    }

    func didComplete(_ task: URLSessionTask) {
        guard task === self.task else { return }
        finish(code: serverCloseCode, cancel: false)
    }

    /// The close code the server sent, if any (4401 must never be missed,
    /// whichever callback reports the close first).
    private var serverCloseCode: Int? {
        guard let code = task?.closeCode.rawValue, code != URLSessionWebSocketTask.CloseCode.invalid.rawValue else {
            return nil
        }
        return code
    }

    // MARK: Frames

    private func receiveNext() {
        task?.receive { [weak self] result in
            guard let self else { return }
            self.queue.async {
                guard !self.finished, !self.closing else { return }
                switch result {
                case .success(.string(let text)): self.deliver(Data(text.utf8))
                case .success(.data(let data)): self.deliver(data)
                case .success: self.deliver(Data())
                case .failure:
                    self.finish(code: self.serverCloseCode, cancel: true)
                    return
                }
                if !self.closing { self.receiveNext() }
            }
        }
    }

    private func deliver(_ raw: Data) {
        let frames = session.receive(raw)
        onActivity?()
        sendThenCloseIfDone(frames)
    }

    /// The conformance hook and the timer call this on `queue`.
    public func tick() {
        guard !finished, !closing else { return }
        sendThenCloseIfDone(session.tick())
    }

    public var isFinished: Bool { finished }

    /// Send frames the session produced outside a receive or tick (an
    /// availability change). Call on `queue`.
    public func sendSessionFrames(_ frames: [JSONValue]) {
        guard !finished, !closing else { return }
        sendThenCloseIfDone(frames)
    }

    /// Send `frames`; when the session has just closed itself (a stopping
    /// bye, the third frame error, server silence), close the socket only
    /// after the last of them left, so a final error result is not lost.
    private func sendThenCloseIfDone(_ frames: [JSONValue]) {
        guard !session.connected else {
            send(frames)
            return
        }
        closing = true
        timer?.cancel()
        timer = nil
        send(frames) { [weak self] in
            guard let self else { return }
            self.queue.async { self.finish(code: nil, cancel: true) }
        }
    }

    private func send(_ frames: [JSONValue], then done: (() -> Void)? = nil) {
        let texts = frames.compactMap { try? JSON.encode($0) }
        guard let task, !texts.isEmpty else {
            done?()
            return
        }
        for (index, text) in texts.enumerated() {
            let last = index == texts.count - 1
            task.send(.string(text)) { _ in if last { done?() } }
        }
        onActivity?()
    }

    private func startTimer() {
        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(deadline: .now() + 1, repeating: 1)
        timer.setEventHandler { [weak self] in self?.tick() }
        timer.resume()
        self.timer = timer
    }

    private func finish(code: Int?, cancel: Bool) {
        guard !finished else { return }
        finished = true
        timer?.cancel()
        timer = nil
        if opened {
            session.disconnect(reason: code.map { "closed:\($0)" } ?? "socket_lost")
        } else {
            // The handshake never completed (server down, TLS pin mismatch,
            // refused upgrade): still one failed attempt for the backoff.
            session.connectionFailed(reason: code.map { "closed:\($0)" } ?? "connect_failed")
        }
        if cancel { task?.cancel(with: .normalClosure, reason: nil) }
        urlSession?.invalidateAndCancel()
        task = nil
        urlSession = nil
        onClose?(code)
        onClose = nil
    }
}

/// Splits URLSession's delegate callbacks between the pinning delegate and
/// the connection (URLSession holds its delegate strongly).
private final class Forwarder: NSObject, URLSessionWebSocketDelegate, @unchecked Sendable {
    weak var connection: NodeConnection?
    let pinning: PinningDelegate

    init(_ connection: NodeConnection, _ pinning: PinningDelegate) {
        self.connection = connection
        self.pinning = pinning
    }

    func urlSession(
        _ session: URLSession, didReceive challenge: URLAuthenticationChallenge,
        completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        pinning.urlSession(session, didReceive: challenge, completionHandler: completionHandler)
    }

    func urlSession(
        _ session: URLSession, task: URLSessionTask, didReceive challenge: URLAuthenticationChallenge,
        completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        pinning.urlSession(session, task: task, didReceive: challenge, completionHandler: completionHandler)
    }

    func urlSession(
        _ session: URLSession, webSocketTask: URLSessionWebSocketTask, didOpenWithProtocol protocol: String?
    ) {
        connection?.urlSession(session, webSocketTask: webSocketTask, didOpenWithProtocol: `protocol`)
    }

    func urlSession(
        _ session: URLSession, webSocketTask: URLSessionWebSocketTask,
        didCloseWith closeCode: URLSessionWebSocketTask.CloseCode, reason: Data?
    ) {
        connection?.urlSession(session, webSocketTask: webSocketTask, didCloseWith: closeCode, reason: reason)
    }

    /// Never follow a redirect: the pin and the bearer belong to the server
    /// the node paired with.
    func urlSession(
        _ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void
    ) {
        completionHandler(nil)
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        connection?.didComplete(task)
    }
}

// MARK: - Keeping the node connected

public final class NodeRunner: @unchecked Sendable {
    public enum Status: Equatable {
        case notPaired
        case connecting
        case connected
        case waiting(seconds: Int)
        /// `bye{revoked|kill_switch|credential_expired}`: the owner must act.
        case stopped(reason: String)
        /// 4401 or a credential the server refuses: pair again.
        case needsPairing
        case error(String)
    }

    private let store: CredentialStore
    private let clock: NodeClock
    private let queue = DispatchQueue(label: "friday-node.runner")
    private let policyStore: LocalPolicyStore
    private let desktop: DesktopSource
    private let usage: UsageSource
    private let home: String
    private var policy = LocalPolicy()
    private var credential: NodeCredential?
    private var session: NodeSession?
    private var connection: NodeConnection?
    private var reconnectWork: DispatchWorkItem?
    private var running = false
    private var rotating = false
    /// Bumped by start, stop, adopt and unpair: a late callback of an older
    /// connection or rotation sees another generation and changes nothing.
    private var generation = 0

    public var onStatus: ((Status) -> Void)?
    public private(set) var status: Status = .notPaired {
        didSet {
            guard status != oldValue else { return }
            let s = status
            // Local, never uploaded, no secret or frame content (D13 item 8).
            Self.log.info("status: \(String(describing: s), privacy: .public)")
            DispatchQueue.main.async { self.onStatus?(s) }
        }
    }
    private static let log = Logger(subsystem: "dev.friday.friday-node", category: "runner")

    /// `home`: the owner's home (the readers' `~`); `usage`: how
    /// `usage.ccusage` is answered (`.unsupported` until a vetted ccusage
    /// binary is configured).
    public init(
        store: CredentialStore, policyStore: LocalPolicyStore, desktop: DesktopSource, home: String,
        usage: UsageSource = .unsupported, clock: NodeClock = SystemClock()
    ) {
        self.store = store
        self.policyStore = policyStore
        self.desktop = desktop
        self.home = home
        self.usage = usage
        self.clock = clock
    }

    // MARK: The hook relay (M3)

    private var hookSocket: HookSocket?
    private var spool: Spool?

    /// Start the hook relay: the spool under `directory` and the Unix socket
    /// at `socketPath`. An event arrives on the socket's thread and is handed
    /// to the session on the runner queue: live when the session is up,
    /// spooled otherwise (also when the node is not paired yet).
    public func startHookRelay(
        socketPath: String = HookSocket.defaultPath,
        spoolDirectory: String = NSHomeDirectory() + "/Library/Application Support/Friday/spool"
    ) throws {
        let spool = try Spool(directory: spoolDirectory, clock: clock)
        let socket = HookSocket(path: socketPath, clock: clock) { [weak self] event in
            guard let self else { return }
            self.queue.async {
                if let session = self.session {
                    let frames = session.relayHookEvent(event)
                    if !frames.isEmpty { self.connection?.sendSessionFrames(frames) }
                } else {
                    spool.append([event])
                }
            }
        }
        try socket.start()
        queue.sync {
            self.spool = spool
            self.session?.spool = spool
        }
        hookSocket = socket
    }

    public func stopHookRelay() {
        hookSocket?.stop()
        hookSocket = nil
    }

    /// The owner changed the local policy (after LocalAuthentication): the
    /// readers and the advertised catalogue follow at once, and a connected
    /// session tells the server what changed.
    public func policyChanged(_ policy: LocalPolicy) {
        queue.async {
            self.policy = policy
            guard let session = self.session else { return }
            let (catalogue, readers) = self.readersAndCatalogue()
            let frames = session.update(catalogue: catalogue, readers: readers)
            if !frames.isEmpty { self.connection?.sendSessionFrames(frames) }
        }
    }

    private func readersAndCatalogue() -> (Catalogue, NodeReaders) {
        let readers = policy.readers(home: home, desktop: desktop, usage: usage)
        return (.standard(availability: readers.availability()), readers)
    }

    /// Load the credential and connect.
    public func start() {
        queue.async {
            self.reset(running: true)
            do {
                self.policy = try self.policyStore.load()
            } catch {
                Self.log.error("local policy: unreadable; everything stays off")
                self.policy = LocalPolicy()
            }
            do {
                self.credential = try self.store.load()
                Self.log.info("credential: \(self.credential == nil ? "none" : "present", privacy: .public)")
            } catch CredentialError.keychain(let code) {
                Self.log.error("credential: keychain error \(code, privacy: .public)")
                self.status = .error("keychain \(code)")
                return
            } catch {
                Self.log.error("credential: unreadable")
                self.status = .error("credential")
                return
            }
            guard let credential = self.credential else {
                self.status = .notPaired
                return
            }
            self.session = self.makeSession(credential)
            self.connect()
        }
    }

    public func stop() {
        queue.async { self.reset(running: false) }
    }

    /// Store a freshly paired credential and connect with it.
    public func adopt(_ credential: NodeCredential) throws {
        try queue.sync {
            try store.save(credential)
            reset(running: true)
            self.credential = credential
            session = makeSession(credential)
            connect()
        }
    }

    /// The owner's local action after a stop (menu "Resume").
    public func ownerResume() {
        queue.async {
            guard let session = self.session, self.credential != nil else { return }
            session.ownerResume()
            self.running = true
            self.connect()
        }
    }

    /// Forget the credential (menu "Unpair"): the node must pair again.
    public func unpair() throws {
        try queue.sync {
            reset(running: false)
            try store.delete()
            credential = nil
            session = nil
            status = .notPaired
        }
    }

    /// On `queue`: drop the connection, any pending reconnect and any
    /// rotation in flight (by moving to a new generation).
    private func reset(running: Bool) {
        generation += 1
        self.running = running
        rotating = false
        reconnectWork?.cancel()
        reconnectWork = nil
        connection?.onClose = nil
        connection?.onActivity = nil
        connection?.stop()
        connection = nil
    }

    private func makeSession(_ credential: NodeCredential) -> NodeSession {
        let (catalogue, readers) = readersAndCatalogue()
        let session = NodeSession(
            nodeID: credential.nodeID, pinned: credential.pinnedKeys, catalogue: catalogue,
            readers: readers, clock: clock, nodeVersion: NodeInfo.nodeVersion, osVersion: NodeInfo.osVersion)
        session.spool = spool
        return session
    }

    private func connect() {
        guard running, !rotating, connection == nil, let credential, let session else { return }
        guard session.reconnectAllowed else {
            status = .stopped(reason: session.closedReason ?? "stopped")
            return
        }
        status = .connecting
        guard credential.rotationDue(now: clock.now()) else {
            openSocket(credential, session)
            return
        }
        rotating = true
        let generation = self.generation
        let now = clock.now()
        // Persisted on `queue`, and only while this generation is current: an
        // unpair or re-pair meanwhile wins, and the rotated secret is dropped.
        let persist: (NodeCredential) throws -> Void = { [weak self] rotated in
            guard let self else { throw RunnerError.superseded }
            try self.queue.sync {
                guard self.generation == generation else { throw RunnerError.superseded }
                try self.store.save(rotated)
            }
        }
        Task {
            let outcome: Result<NodeCredential, Error>
            do {
                outcome = .success(try await Rotation.rotate(credential, now: now, persist: persist))
            } catch {
                outcome = .failure(error)
            }
            self.queue.async {
                guard self.generation == generation else { return }
                self.rotating = false
                switch outcome {
                case .success(let rotated):
                    self.credential = rotated
                    self.openSocket(rotated, session)
                case .failure(NodeHTTPError.status(401, _)):
                    self.status = .needsPairing
                case .failure:
                    session.connectionFailed(reason: "rotation_failed")
                    self.scheduleReconnect(session)
                }
            }
        }
    }

    private func openSocket(_ credential: NodeCredential, _ session: NodeSession) {
        guard running, connection == nil, let url = credential.gatewayURL else { return }
        let generation = self.generation
        let connection = NodeConnection(
            url: url, secret: credential.secret, pin: credential.tlsPinSHA256, session: session, queue: queue,
            autoTick: true)
        connection.onActivity = { [weak self] in
            guard let self, self.generation == generation, session.sessionReady else { return }
            self.status = .connected
        }
        connection.onClose = { [weak self] code in
            guard let self, self.generation == generation else { return }
            self.connection = nil
            self.handleClose(code, session)
        }
        self.connection = connection
        connection.start()
    }

    private func handleClose(_ code: Int?, _ session: NodeSession) {
        if code == CloseCode.unauthorized {
            status = .needsPairing
            return
        }
        if !session.reconnectAllowed {
            status = .stopped(reason: session.closedReason ?? "stopped")
            return
        }
        scheduleReconnect(session)
    }

    private func scheduleReconnect(_ session: NodeSession) {
        guard running else { return }
        let delay = session.nextReconnectDelay ?? Backoff.minimumSeconds
        status = .waiting(seconds: Int(delay.rounded(.up)))
        let generation = self.generation
        let work = DispatchWorkItem { [weak self] in
            guard let self, self.generation == generation else { return }
            self.connect()
        }
        reconnectWork = work
        queue.asyncAfter(deadline: .now() + delay, execute: work)
    }
}

enum RunnerError: Error {
    /// The runner moved on (unpaired, re-paired, stopped) while this ran.
    case superseded
}

/// The catalogue a node with the default local policy advertises: no
/// transcript grant, no repo, titles off, ccusage unsupported (the desktop
/// readers are available, as in the reference node).
public enum NodeCatalogue {
    public static func defaultPolicy(home: String = NSHomeDirectory()) -> Catalogue {
        let readers = LocalPolicy().readers(home: home, desktop: SystemDesktop(), usage: .unsupported)
        return .standard(availability: readers.availability())
    }
}
