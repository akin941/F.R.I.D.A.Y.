// The node's outbound WebSocket (Phase 9 brief D6, D13 items 1-4; ADR-013
// decision 1: outbound only, no listener of any kind).
//
// `NodeConnection` is one socket: `wss://<server>/ws/node/v1`, subprotocols
// `friday-node.v2, friday-node.v1` offered (Phase 10 D1: the gateway picks
// the highest it supports, and the session runs the version it picked),
// `Authorization: Bearer <jnd_...>`, no `Origin`, the TLS SPKI pin enforced
// in place of CA validation. It feeds every received frame to `NodeSession`
// and sends what the session answers; it never decides protocol behaviour
// itself.
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
    /// The subprotocols offered, highest first.
    public let offered: [String]
    private var urlSession: URLSession?
    private var pinning: PinningDelegate?
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
    /// `offered`: `friday-node.v2, friday-node.v1` unless a caller narrows it.
    public init(
        url: URL, secret: String, pin: Data, session: NodeSession, queue: DispatchQueue, autoTick: Bool,
        offered: [String] = NodeProtocol.offeredSubprotocols
    ) {
        self.url = url
        self.secret = secret
        self.pin = pin
        self.session = session
        self.queue = queue
        self.autoTick = autoTick
        self.offered = offered.filter { NodeProtocol.version(ofSubprotocol: $0) != nil }
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
        self.pinning = pinning
        let urlSession = URLSession(
            configuration: configuration, delegate: Forwarder(self, pinning), delegateQueue: delegateQueue)
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalAndRemoteCacheData, timeoutInterval: 15)
        request.setValue("Bearer \(secret)", forHTTPHeaderField: "Authorization")
        request.setValue(offered.joined(separator: ", "), forHTTPHeaderField: "Sec-WebSocket-Protocol")
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
        // Only a subprotocol the node offered; its version is the session's.
        guard let picked = `protocol`, offered.contains(picked),
            let version = NodeProtocol.version(ofSubprotocol: picked)
        else {
            finish(code: nil, cancel: true)
            return
        }
        opened = true
        send(self.session.connect(version: version))
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

    /// The TLS handshake was refused because the server's leaf SPKI is not
    /// the pinned one: nothing was sent (no upgrade request, no Bearer, so
    /// never `hello`). Read on `queue`, e.g. in `onClose`.
    public var pinRefused: Bool { pinning?.refusedPin ?? false }

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
            // Whichever side closed, with whatever code: `socket_lost`, as the
            // reference transport (transport.py) records it; the code itself
            // reaches the runner through `onClose` (4401 => pair again).
            session.disconnect(reason: "socket_lost")
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
        /// Phase 13 M1: the server at the stored address presented another
        /// TLS key than the pinned one; refused before anything is sent
        /// ("Sunucu kimliği değişti — yeniden eşleştirin"). The runner keeps
        /// retrying the same address with backoff (the Linux daemon's
        /// behaviour); it never falls back to another address.
        case identityChanged
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
    /// Phase 10: the action policy (check 22) and the executor, both built
    /// from the owner's command mappings (runbook B6: `CommandMappings`, the
    /// keychain item the "İzinler" menu edits behind LocalAuthentication).
    /// Without `makeExecutor` the executor is `UnavailableExecutor`.
    private var actionPolicy: ActionPolicy
    private var executor: ActionExecutor
    private let mappingsStore: CommandMappingsStore?
    private let makeExecutor: ((CommandMappings) -> ActionExecutor)?
    /// The owner's local kill switch, for the app's lifetime: every session
    /// shares it, so the menu and the node agree after a re-pair and while
    /// unpaired (B5 review finding 3).
    public let killSwitch = KillSwitch()
    /// The node's own data directories (denied to every sandboxed command).
    private let nodeDirs: [String]
    private var availabilityTimer: DispatchSourceTimer?
    public static let availabilityRefreshSeconds = 15
    private var credential: NodeCredential?
    private var session: NodeSession?
    private var connection: NodeConnection?
    private var reconnectWork: DispatchWorkItem?
    private var running = false
    private var rotating = false
    /// The rotation's HTTPS POST (tests inject a fake).
    private let rotationPost: Rotation.Post
    /// Bumped by start, stop, adopt and unpair: a late callback of an older
    /// connection or rotation sees another generation and changes nothing.
    private var generation = 0

    public var onStatus: ((Status) -> Void)?
    /// Phase 11 M1: the voice host's `link` (the node WebSocket is up, i.e.
    /// the session got `hello.ack`), called on the runner queue whenever it
    /// changes (ADR-013 decision 6: a lost socket releases the mic).
    public var onVoiceLink: ((Bool) -> Void)?
    private var voiceLinkUp = false
    public private(set) var status: Status = .notPaired {
        didSet {
            guard status != oldValue else { return }
            let s = status
            // Local, never uploaded, no secret or frame content (D13 item 8).
            Self.log.info("status: \(String(describing: s), privacy: .public)")
            DispatchQueue.main.async { self.onStatus?(s) }
            let up = s == .connected
            if up != voiceLinkUp {
                voiceLinkUp = up
                onVoiceLink?(up)
            }
        }
    }
    private static let log = Logger(subsystem: "dev.friday.friday-node", category: "runner")

    /// `home`: the owner's home (the readers' `~`); `usage`: how
    /// `usage.ccusage` is answered (`.unsupported` until a vetted ccusage
    /// binary is configured).
    public init(
        store: CredentialStore, policyStore: LocalPolicyStore, desktop: DesktopSource, home: String,
        usage: UsageSource = .unsupported, clock: NodeClock = SystemClock(), actionPolicy: ActionPolicy? = nil,
        executor: ActionExecutor = UnavailableExecutor(), mappingsStore: CommandMappingsStore? = nil,
        makeExecutor: ((CommandMappings) -> ActionExecutor)? = nil, nodeDirs: [String] = SandboxPolicy.appNodeDirs,
        rotationPost: @escaping Rotation.Post = Rotation.pinnedPost
    ) {
        self.store = store
        self.rotationPost = rotationPost
        self.policyStore = policyStore
        self.desktop = desktop
        self.home = home
        self.usage = usage
        self.clock = clock
        self.actionPolicy = actionPolicy ?? .empty(home: home)
        self.executor = executor
        self.mappingsStore = mappingsStore
        self.makeExecutor = makeExecutor
        self.nodeDirs = nodeDirs
    }

    // MARK: Command mappings (runbook B6)

    /// On `queue`: the check-22 policy and the executor from `mappings`; an
    /// unreadable or unsafe document maps nothing (fail closed).
    private func adoptMappings(_ mappings: CommandMappings?) {
        guard let mappings else {
            actionPolicy = .empty(home: home)
            executor = makeExecutor?(CommandMappings()) ?? UnavailableExecutor()
            return
        }
        do {
            actionPolicy = try mappings.actionPolicy(home: home)
            executor = makeExecutor?(mappings) ?? UnavailableExecutor()
        } catch {
            Self.log.error("command mappings: refused; nothing is mapped")
            actionPolicy = .empty(home: home)
            executor = makeExecutor?(CommandMappings()) ?? UnavailableExecutor()
        }
    }

    /// The owner changed the command mappings (after LocalAuthentication):
    /// check 22, the executor and the advertised availability follow at once.
    public func mappingsChanged(_ mappings: CommandMappings) {
        queue.async {
            self.adoptMappings(mappings)
            self.pushCatalogue()
        }
    }

    /// Recompute the catalogue (the TCC grants may have changed in System
    /// Settings) and tell a connected server what changed.
    public func refreshAvailability() {
        queue.async { self.pushCatalogue() }
    }

    private func pushCatalogue() {
        guard let session else { return }
        let (catalogue, readers) = readersAndCatalogue()
        let frames = session.update(catalogue: catalogue, readers: readers, policy: actionPolicy, executor: executor)
        if !frames.isEmpty { connection?.sendSessionFrames(frames) }
    }

    private func startAvailabilityTimer() {
        guard availabilityTimer == nil else { return }
        let timer = DispatchSource.makeTimerSource(queue: queue)
        let every = DispatchTimeInterval.seconds(Self.availabilityRefreshSeconds)
        timer.schedule(deadline: .now() + every, repeating: every)
        timer.setEventHandler { [weak self] in self?.pushCatalogue() }
        availabilityTimer = timer
        timer.resume()
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
        // The spool is in place before the socket accepts its first event.
        queue.sync {
            self.spool = spool
            self.session?.spool = spool
        }
        try socket.start()
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
        var availability = readers.availability()
        for contract in CapabilityContract.actions {
            availability[contract.name] = executor.availability(for: contract.name)
        }
        return (.standard(availability: availability, actions: true), readers)
    }

    // MARK: The confirmation window (Phase 10 M1)

    /// The app's panel; called on the runner queue.
    public weak var confirmationObserver: ConfirmationObserver? {
        didSet {
            let observer = confirmationObserver
            queue.async { self.session?.confirmationObserver = observer }
        }
    }

    /// The panel's content now (the panel polls it once a second for the
    /// countdown).
    public func panelView() -> ConfirmationPanelView {
        queue.sync {
            guard let session else { return .hidden }
            return ConfirmationPanelView.make(
                prompt: session.openPrompt, running: session.runningPrompt, now: clock.now())
        }
    }

    /// The owner clicked "Çalıştır" or "Reddet" (or closed the panel).
    public func decide(_ action: ConfirmationAction, invocationID: String) {
        queue.async {
            guard let session = self.session, session.decide(action, invocationID: invocationID) else { return }
            self.connection?.tick()
        }
    }

    /// The owner clicked "Durdur". Stopping never waits on the process
    /// (SIGTERM now, SIGKILL after the grace, off the queue).
    public func stop(invocationID: String) {
        queue.async {
            guard let session = self.session else { return }
            let frames = session.stop(invocationID: invocationID)
            if !frames.isEmpty { self.connection?.sendSessionFrames(frames) }
        }
    }

    /// The owner's local kill switch: engaged at once (before the queue gets
    /// to it), a blocking helper the queue may be waiting on (git creating a
    /// worktree, osascript) is killed from here, then the session closes the
    /// open window, stops what runs, and refuses every later click until
    /// released.
    public func engageKillSwitch() {
        killSwitch.engage()
        ChildRegistry.shared.killAll()
        queue.async {
            guard let session = self.session else { return }
            let frames = session.engageKillSwitch()
            if !frames.isEmpty { self.connection?.sendSessionFrames(frames) }
        }
    }

    /// Release the local kill switch (the app authenticates the owner first).
    public func releaseKillSwitch() {
        killSwitch.release()
    }

    /// Never waits on the session queue.
    public var killSwitchEngaged: Bool { killSwitch.engaged }

    // MARK: The voice host (Phase 11 M1)

    /// One mic state from the voice child (`state`, `mode`, `session_open`,
    /// the three counters): relayed as `voice.state` by the session's rules
    /// (v2 only, after `hello.ack`, coalesced to one per second, validated,
    /// re-sent with every heartbeat, never spooled).
    public func relayVoiceState(_ state: JSONValue) {
        queue.async {
            guard let session = self.session else { return }
            let frames = session.relayVoiceState(state)
            if !frames.isEmpty { self.connection?.sendSessionFrames(frames) }
        }
    }

    /// The paired credential, for the voice token mint (memory only; the
    /// voice host never stores it).
    public func credentialForVoice() -> NodeCredential? {
        queue.sync { credential }
    }

    /// The server host the menu shows, from the stored credential only.
    public var serverHost: String? {
        ServerAddress.displayHost(of: currentCredential())
    }

    /// Whether the node WebSocket is up now.
    public var voiceLink: Bool { queue.sync { voiceLinkUp } }

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
            if let mappingsStore = self.mappingsStore {
                self.adoptMappings(try? mappingsStore.load())
            }
            self.startAvailabilityTimer()
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

    /// Phase 13 M1: the owner re-pointed the node (ServerAddressEditor, after
    /// LocalAuthentication and the pin probe). Saved on `queue`; every pin,
    /// the node id and the secret are kept. A running node reconnects to the
    /// new address; a stopped session stays stopped (re-pointing is not
    /// "Resume").
    ///
    /// A rotation in flight (its POST went to the old host) is **not**
    /// superseded: the identity did not change, and the server may already
    /// have issued the new secret, which must not be lost (it would end in
    /// `needsPairing` after the grace period). The swap waits for it: the
    /// new address is saved now, the rotation's `persist` merges the rotated
    /// secret into the credential as it is then (so the new address stays),
    /// and its completion opens the socket to the new address. Unpair and
    /// re-pair still bump the generation and drop the rotated secret.
    public func repoint(to serverURL: URL, nodeID: String) throws -> NodeCredential {
        try queue.sync {
            guard var next = try credential ?? store.load(), next.nodeID == nodeID else {
                throw ServerAddressError.notPaired
            }
            next.serverURL = serverURL
            try store.save(next)
            credential = next
            if running, session != nil, !rotating {
                reset(running: true)
                connect()
            }
            return next
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
            readers: readers, clock: clock, nodeVersion: NodeInfo.nodeVersion, osVersion: NodeInfo.osVersion,
            policy: actionPolicy, executor: executor, killSwitch: killSwitch, nodeDirs: nodeDirs)
        session.spool = spool
        session.confirmationObserver = confirmationObserver
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
        // The rotated secret is merged into the credential as it is now, so a
        // re-point while the POST was in flight keeps its new address
        // (identity and pins must still match, else it is dropped too).
        let persist: (NodeCredential) throws -> Void = { [weak self] rotated in
            guard let self else { throw RunnerError.superseded }
            try self.queue.sync {
                guard self.generation == generation, let current = self.credential,
                    current.nodeID == rotated.nodeID, current.pinnedKeys == rotated.pinnedKeys,
                    current.tlsPinSHA256 == rotated.tlsPinSHA256
                else { throw RunnerError.superseded }
                var merged = current
                merged.secret = rotated.secret
                merged.secretIssuedAt = rotated.secretIssuedAt
                merged.rotateAfterDays = rotated.rotateAfterDays
                try self.store.save(merged)
                self.credential = merged
            }
        }
        Task {
            let outcome: Result<NodeCredential, Error>
            do {
                outcome = .success(
                    try await Rotation.rotate(credential, now: now, persist: persist, post: self.rotationPost))
            } catch {
                outcome = .failure(error)
            }
            self.queue.async {
                guard self.generation == generation else { return }
                self.rotating = false
                switch outcome {
                case .success:
                    // `persist` already stored the merged credential (the
                    // current address, the rotated secret).
                    guard let current = self.credential else { return }
                    self.openSocket(current, session)
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
        connection.onClose = { [weak self, weak connection] code in
            guard let self, self.generation == generation else { return }
            self.connection = nil
            if connection?.pinRefused == true {
                Self.log.error("server identity changed: TLS pin refused before hello")
                self.status = .identityChanged
                self.scheduleReconnect(session, showWaiting: false)
                return
            }
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

    private func scheduleReconnect(_ session: NodeSession, showWaiting: Bool = true) {
        guard running else { return }
        let delay = session.nextReconnectDelay ?? Backoff.minimumSeconds
        if showWaiting { status = .waiting(seconds: Int(delay.rounded(.up))) }
        let generation = self.generation
        let work = DispatchWorkItem { [weak self] in
            guard let self, self.generation == generation else { return }
            self.connect()
        }
        reconnectWork = work
        queue.asyncAfter(deadline: .now() + delay, execute: work)
    }
}

extension NodeRunner: ServerAddressTarget {
    /// The credential the runner holds, else the stored one (local only).
    public func currentCredential() -> NodeCredential? {
        queue.sync { credential ?? (try? store.load()) }
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
