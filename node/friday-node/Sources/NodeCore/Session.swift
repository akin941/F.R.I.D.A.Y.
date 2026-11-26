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
    private var finished = false

    /// Called once on `queue` when the socket is gone, with the close code
    /// the server sent (nil when the connection failed or was dropped).
    public var onClose: ((Int?) -> Void)?
    /// Called on `queue` after every frame the node sent (status display).
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
        let urlSession = URLSession(configuration: configuration, delegate: Forwarder(self, pinning), delegateQueue: delegateQueue)
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
        let code = self.task?.closeCode.rawValue
        finish(code: code == URLSessionWebSocketTask.CloseCode.invalid.rawValue ? nil : code, cancel: false)
    }

    // MARK: Frames

    private func receiveNext() {
        task?.receive { [weak self] result in
            guard let self else { return }
            self.queue.async {
                guard !self.finished else { return }
                switch result {
                case .success(.string(let text)): self.deliver(Data(text.utf8))
                case .success(.data(let data)): self.deliver(data)
                case .success: self.deliver(Data())
                case .failure:
                    self.finish(code: nil, cancel: true)
                    return
                }
                self.receiveNext()
            }
        }
    }

    private func deliver(_ raw: Data) {
        send(session.receive(raw))
        if !session.connected { finish(code: nil, cancel: true) }
    }

    /// The conformance hook and the timer call this on `queue`.
    public func tick() {
        guard !finished else { return }
        send(session.tick())
        if !session.connected { finish(code: nil, cancel: true) }
    }

    public var isFinished: Bool { finished }

    private func send(_ frames: [JSONValue]) {
        for frame in frames {
            guard let text = try? JSON.encode(frame) else { continue }
            task?.send(.string(text)) { _ in }
        }
        if !frames.isEmpty { onActivity?() }
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
        session.disconnect(reason: code.map { "closed:\($0)" } ?? "socket_lost")
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
    private let readers: ReadProvider
    private var credential: NodeCredential?
    private var session: NodeSession?
    private var connection: NodeConnection?
    private var reconnectWork: DispatchWorkItem?
    private var running = false

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

    public init(store: CredentialStore, readers: ReadProvider, clock: NodeClock = SystemClock()) {
        self.store = store
        self.readers = readers
        self.clock = clock
    }

    /// Load the credential and connect.
    public func start() {
        queue.async {
            self.running = true
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
        queue.async {
            self.running = false
            self.reconnectWork?.cancel()
            self.connection?.stop()
            self.connection = nil
        }
    }

    /// Store a freshly paired credential and connect with it.
    public func adopt(_ credential: NodeCredential) throws {
        try store.save(credential)
        queue.async {
            self.connection?.stop()
            self.credential = credential
            self.session = self.makeSession(credential)
            self.running = true
            self.connect()
        }
    }

    /// The owner's local action after a stop (menu "Resume").
    public func ownerResume() {
        queue.async {
            guard let session = self.session else { return }
            session.ownerResume()
            self.running = true
            self.connect()
        }
    }

    /// Forget the credential (menu "Unpair"): the node must pair again.
    public func unpair() throws {
        try store.delete()
        queue.async {
            self.reconnectWork?.cancel()
            self.connection?.stop()
            self.connection = nil
            self.credential = nil
            self.session = nil
            self.status = .notPaired
        }
    }

    private func makeSession(_ credential: NodeCredential) -> NodeSession {
        NodeSession(
            nodeID: credential.nodeID, pinned: credential.pinnedKeys, catalogue: NodeCatalogue.current(),
            readers: readers, clock: clock, nodeVersion: NodeInfo.nodeVersion, osVersion: NodeInfo.osVersion)
    }

    private func connect() {
        guard running, var credential, let session, connection == nil else { return }
        guard session.reconnectAllowed else {
            status = .stopped(reason: session.closedReason ?? "stopped")
            return
        }
        status = .connecting
        if credential.rotationDue(now: clock.now()) {
            let store = self.store
            let now = clock.now()
            Task {
                do {
                    let rotated = try await Rotation.rotate(credential, store: store, now: now)
                    self.queue.async {
                        self.credential = rotated
                        self.openSocket(rotated, session)
                    }
                } catch NodeHTTPError.status(401, _) {
                    self.queue.async { self.status = .needsPairing }
                } catch {
                    self.queue.async { self.scheduleReconnect(session) }
                }
            }
            return
        }
        credential = self.credential!
        openSocket(credential, session)
    }

    private func openSocket(_ credential: NodeCredential, _ session: NodeSession) {
        guard running, connection == nil, let url = credential.gatewayURL else { return }
        let connection = NodeConnection(
            url: url, secret: credential.secret, pin: credential.tlsPinSHA256, session: session, queue: queue,
            autoTick: true)
        connection.onActivity = { [weak self] in
            guard let self, session.sessionReady else { return }
            self.status = .connected
        }
        connection.onClose = { [weak self] code in
            guard let self else { return }
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
        let work = DispatchWorkItem { [weak self] in self?.connect() }
        reconnectWork = work
        queue.asyncAfter(deadline: .now() + delay, execute: work)
    }
}

/// The catalogue the app advertises. M1 has no readers that touch owner
/// data, so every capability is `not_granted` until M2 adds readers and the
/// owner's local grants.
public enum NodeCatalogue {
    public static func current() -> Catalogue {
        var availability: [String: String] = [:]
        for contract in CapabilityContract.all { availability[contract.name] = "not_granted" }
        return .standard(availability: availability)
    }
}

/// M1's reader: none. Every read fails closed (and with every capability
/// `not_granted`, the verifier refuses first).
public final class NoReaders: ReadProvider {
    public init() {}
    public func read(capability: String, args: JSONValue, now: Int64) -> ReadOutcome { .failure }
}
