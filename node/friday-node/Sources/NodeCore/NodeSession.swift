// The node side of one Friday Node session, protocol v1 and v2 (Phase 9
// brief D5, D6, D13 items 1-4 and 9; Phase 10 brief D1, D5, D12; Phase 11
// D15). A port of the reference node (backend/nodes/reference/node.py).
//
// A synchronous state machine driven by these inputs:
// - `connect(version:)`: a new socket negotiated `version`; returns `[hello]`;
// - `receive(raw)`: one server -> node frame; returns the frames to send;
// - `tick()`: the clock moved (a heartbeat is due, a delayed read is due, the
//   window has a decision or timed out, a command produced output, finished
//   or hit its runtime cap, the server went silent); returns the frames;
// - `disconnect(reason:)`: the socket is gone. Fail closed (ADR-013
//   decision 6): the open window closes, nothing that was not yet confirmed
//   runs, a running command is stopped (SIGTERM, then SIGKILL after 5 s),
//   and no late frame is ever sent;
// - `engageKillSwitch()` (the owner's local kill switch), `decide(...)` (the
//   window's buttons) and `stop(invocationID:)` ("Durdur").
//
// The transport (Session.swift) owns the socket; this type owns the rules,
// so the offline conformance suite drives exactly the code the app runs.
//
// v2 verification order (D5): checks 1-22 (VerifierV2), then for confirm
// and gate classes:
// - 23: one window at a time (`busy_confirming`); `confirmation{shown}`; the
//   owner approves, denies (`owner_denied`) or the window times out after
//   60 s (`confirm_timeout`); each outcome is a `confirmation` frame
//   followed, for a refusal, by a refused `result`;
// - 24: after the click, the envelope's (and so the grant's) `expires_at`
//   must still be in the future and the kill switch released
//   (`expired_after_confirm`); a lost socket runs nothing and sends nothing.
// Only then does the executor run. `repo.command.run` streams `output`
// frames (scrubbed with carry-over, OutputStreamer) and ends with one
// `result` carrying `exit_code`, `output_bytes`, the scrubbed `tail`,
// `truncated` and `sandbox_violation`.
//
// - Frame errors (checks 1-4) answer `result{status: error}` without an
//   invocation id; three within 60 s close the session
//   (`closedReason = "protocol_error"`). A refusal never echoes the payload.
// - `bye{revoked|kill_switch|credential_expired}` stops reconnecting until
//   the owner acts locally (`ownerResume()`); any other close schedules a
//   reconnect with full-jitter backoff (1 s -> 60 s).
// - Server silence beyond 45 s drops the socket.
// - A read runs only after every check passed; its output is checked
//   against the node's own `result_schema` and size bound.
// - Every frame the node sends is validated against the session version's
//   schema first; a frame that fails is never sent (and counted, so tests
//   catch the bug).
// - `voice.state` (v2 only): validated against the v2 event schema's
//   `$defs.voice_state_item`, coalesced to one per second, re-sent with
//   every heartbeat and after a reconnect, never spooled, never waiting for
//   its ack. A mic state handed to a v1 session is dropped and counted.

import Foundation

public struct ReadOutcome: Equatable {
    public let ok: Bool
    public let output: JSONValue?
    public let truncated: Bool

    public static func success(_ output: JSONValue, truncated: Bool = false) -> ReadOutcome {
        ReadOutcome(ok: true, output: output, truncated: truncated)
    }

    public static let failure = ReadOutcome(ok: false, output: nil, truncated: false)
}

/// The readers (M2).
public protocol ReadProvider: AnyObject {
    /// Run one reader. `args` already passed every check.
    func read(capability: String, args: JSONValue, now: Int64) -> ReadOutcome
}

/// The owner's local kill switch: one flag, readable and settable from any
/// thread (the menu engages it at once, before the session queue gets to
/// it), owned by `NodeRunner` for the app's lifetime.
public final class KillSwitch: @unchecked Sendable {
    private let lock = NSLock()
    private var isEngaged = false

    public init(engaged: Bool = false) { isEngaged = engaged }

    public var engaged: Bool {
        lock.lock()
        defer { lock.unlock() }
        return isEngaged
    }

    public func engage() {
        lock.lock()
        isEngaged = true
        lock.unlock()
    }

    public func release() {
        lock.lock()
        isEngaged = false
        lock.unlock()
    }
}

/// What `hello.ack` told the node.
public struct HelloAck: Equatable {
    public let nodeID: String
    public let heartbeatSeconds: Int64
    public let rotateAfterDays: Int64
    public let serverTime: Int64
    public let accepted: [String]
}

/// Not thread-safe by design: the app confines it to `NodeRunner`'s serial
/// queue (and tests to one thread).
public final class NodeSession: @unchecked Sendable {
    public static let frameErrorLimit = 3
    public static let frameErrorWindowSeconds: Int64 = 60
    public static let silenceSeconds: Int64 = 45
    public static let maxDurationMilliseconds: Int64 = 3_600_000
    public static let voiceStateKind = "voice.state"
    public static let voiceStateItemKeys: Set<String> = [
        "state", "mode", "session_open", "wake_accepts_today", "wake_discards_today", "storm_mutes_today", "at",
    ]
    public static let voiceStateMinIntervalSeconds: Int64 = 1
    static let voiceAckMemory = 64

    public let nodeID: String
    public let pinned: [String: Data]
    public private(set) var catalogue: Catalogue
    public let clock: NodeClock
    public let nodeVersion: String
    public let osVersion: String
    public var readDelaySeconds: Int64

    private var readers: ReadProvider
    private let makeID: () -> String
    private let nonceCache = NonceCache()
    private var backoff: Backoff

    // v2: the local action policy, the executor, the window.
    public private(set) var policy: ActionPolicy
    public private(set) var executor: ActionExecutor
    public let window: ConfirmationWindowModel
    private let scrub: ScrubFunction
    /// Where `worktree` mode creates its throwaway worktrees (nil: a fresh
    /// private temp directory per run).
    public var worktreeBase: String?
    public weak var confirmationObserver: ConfirmationObserver?
    /// The owner's local kill switch. Shared with `NodeRunner`, which keeps
    /// one for the app's lifetime: a re-pair (a new session) or an unpaired
    /// node keeps the state the menu shows (B5 review finding 3).
    public let killSwitch: KillSwitch
    public var killSwitchEngaged: Bool { killSwitch.engaged }
    /// The node's own environment: the names a mapping lists are taken from
    /// it (never a secret-shaped one; SandboxPolicy).
    public var nodeEnvironment: [String: String]
    /// The node's own data directories, denied to every sandboxed command.
    public var nodeDirs: [String]

    /// The negotiated protocol version of the current (or last) socket.
    public private(set) var version: Int64 = NodeProtocol.version
    public private(set) var connected = false
    public private(set) var sessionReady = false
    public private(set) var closedReason: String?
    public private(set) var reconnectAllowed = true
    public private(set) var nextReconnectDelay: Double?
    public private(set) var helloAck: HelloAck?
    public private(set) var voiceStatesDropped = 0
    public private(set) var voiceStatesSent = 0
    public private(set) var invalidOutgoingFrames = 0
    public private(set) var framesDroppedAfterDisconnect = 0

    private var frameErrorTimes: [Int64] = []
    private var lastServerFrameAt: Int64?
    private var lastHeartbeatAt: Int64 = 0
    private var connectedAt: Int64 = 0
    private var heartbeatSeq: Int64 = 0
    private var pendingReads: [(envelope: JSONValue, dueAt: Int64, receivedAt: Int64)] = []
    /// The catalogue the current socket's `hello` carried: a policy change
    /// before `hello.ack` is announced once the session is up.
    private var catalogueAtHello: Catalogue?

    private struct Pending {
        let envelope: JSONValue
        let resolved: ResolvedIntent?
        let receivedAt: Int64
        let prompt: ConfirmationPrompt
    }

    private final class Running {
        let envelope: JSONValue
        let handle: RunningCommand
        let streamer: OutputStreamer
        let startedAt: Int64
        let receivedAt: Int64
        let wallSeconds: Int64
        let prompt: ConfirmationPrompt
        let repoRoot: String?
        var worktree: String?
        var tempDir: String?
        var ownBase: String?

        init(envelope: JSONValue, handle: RunningCommand, streamer: OutputStreamer, startedAt: Int64,
             receivedAt: Int64, wallSeconds: Int64, prompt: ConfirmationPrompt, repoRoot: String?,
             worktree: String?, tempDir: String?, ownBase: String?) {
            self.envelope = envelope
            self.handle = handle
            self.streamer = streamer
            self.startedAt = startedAt
            self.receivedAt = receivedAt
            self.wallSeconds = wallSeconds
            self.prompt = prompt
            self.repoRoot = repoRoot
            self.worktree = worktree
            self.tempDir = tempDir
            self.ownBase = ownBase
        }

        var invocationID: String { envelope["invocation_id"]?.stringValue ?? "" }
    }

    private var pending: Pending?
    private var running: [Running] = []

    // Voice state (Phase 11 D15, v2 only).
    private var voiceCurrent: [String: JSONValue]?
    private var voicePending = false
    private var voiceSentAt: Int64?
    private var voiceFrameIDs: [String] = []

    // Hook events (M3): sent live when the session is up and nothing is in
    // flight or spooled; otherwise spooled and uploaded after `hello.ack`,
    // in order, one batch in flight, each deleted only on the `event.ack`
    // naming that frame's id.
    public var spool: Spool?
    private var eventInFlight: (frameID: String, events: [JSONValue], batchID: String?)?
    public private(set) var eventsDropped = 0
    public private(set) var strayEventAcks = 0
    /// Frame ids of availability events (their acks wait on nothing).
    private var availabilityFrameIDs: [String] = []

    public init(
        nodeID: String, pinned: [String: Data], catalogue: Catalogue, readers: ReadProvider,
        clock: NodeClock, nodeVersion: String, osVersion: String, readDelaySeconds: Int64 = 0,
        backoff: Backoff = Backoff(), makeID: @escaping () -> String = { UUID().uuidString.lowercased() },
        policy: ActionPolicy? = nil, executor: ActionExecutor = UnavailableExecutor(),
        window: ConfirmationWindowModel = ConfirmationWindowModel(), scrub: @escaping ScrubFunction = Scrubber.scrub,
        worktreeBase: String? = nil, killSwitch: KillSwitch = KillSwitch(),
        nodeEnvironment: [String: String] = ProcessInfo.processInfo.environment,
        nodeDirs: [String] = SandboxPolicy.defaultNodeDirs
    ) {
        self.killSwitch = killSwitch
        self.nodeEnvironment = nodeEnvironment
        self.nodeDirs = nodeDirs
        self.nodeID = nodeID
        self.pinned = pinned
        self.catalogue = catalogue
        self.readers = readers
        self.clock = clock
        self.nodeVersion = nodeVersion
        self.osVersion = osVersion
        self.readDelaySeconds = readDelaySeconds
        self.backoff = backoff
        self.makeID = makeID
        self.policy = policy ?? .empty(home: NSHomeDirectory())
        self.executor = executor
        self.window = window
        self.scrub = scrub
        self.worktreeBase = worktreeBase
    }

    private var verifierState: VerifierState {
        VerifierState(nodeID: nodeID, pinned: pinned, catalogue: catalogue, nonceCache: nonceCache)
    }

    /// A confirm or gate invoke is waiting on the window, or a command runs.
    public var busy: Bool { pending != nil || !running.isEmpty }

    /// The open window's prompt, if any (the panel draws it).
    public var openPrompt: ConfirmationPrompt? { pending?.prompt }

    /// The prompt of the command that runs, if any ("Durdur").
    public var runningPrompt: ConfirmationPrompt? { running.first?.prompt }

    // MARK: Session

    /// A new socket is up and negotiated `version`; returns `[hello]`. The
    /// nonce cache survives reconnects. Whether to open a socket at all
    /// (after a `bye` that stops reconnecting) is the transport's decision:
    /// it checks `reconnectAllowed` first.
    public func connect(version: Int64 = NodeProtocol.version) -> [JSONValue] {
        let now = clock.now()
        self.version = NodeProtocol.versions.contains(version) ? version : NodeProtocol.version
        connected = true
        sessionReady = false
        closedReason = nil
        frameErrorTimes.removeAll()
        lastServerFrameAt = now
        lastHeartbeatAt = now
        connectedAt = now
        nextReconnectDelay = nil
        catalogueAtHello = catalogue
        eventInFlight = nil
        // A reconnect re-sends the current mic state once the session is up.
        voiceSentAt = nil
        voicePending = voiceCurrent != nil
        return send([hello()])
    }

    /// The entries a `hello` of this session's version carries: v1 the
    /// reads only, v2 everything.
    private func helloEntries(_ catalogue: Catalogue) -> [JSONValue] {
        catalogue.entries.filter { version == 2 || $0["class"]?.stringValue == "read" }
    }

    public func hello() -> JSONValue {
        let entries = helloEntries(catalogue)
        return [
            "v": .int(version),
            "type": "hello",
            "id": .string(makeID()),
            "node_version": .string(nodeVersion),
            "os_version": .string(osVersion),
            "catalogue": .array(entries),
            "catalogue_sha256": .string((try? NodeProtocol.catalogueSHA256(entries)) ?? ""),
        ]
    }

    /// The socket is gone. Idempotent. Fail closed: the window closes, what
    /// runs is stopped, nothing is sent late.
    public func disconnect(reason: String = "socket_lost") {
        if !connected && closedReason != nil { return }
        connected = false
        sessionReady = false
        if let pending {
            window.forceClose("disconnect")
            confirmationObserver?.confirmationClosed(invocationID: pending.prompt.invocationID, reason: "disconnect")
        }
        pending = nil
        for run in running {
            run.handle.terminate()
            cleanup(run)
            confirmationObserver?.commandEnded(invocationID: run.invocationID)
        }
        running.removeAll()
        pendingReads.removeAll()
        if let inFlight = eventInFlight, inFlight.batchID == nil {
            spoolEvents(inFlight.events)  // a live event never acked
        }
        eventInFlight = nil
        if closedReason == nil { closedReason = reason }
        if reconnectAllowed { nextReconnectDelay = backoff.nextDelay() }
    }

    /// A connection attempt that never opened a session (server down, TLS
    /// pin mismatch, refused upgrade): one more failed attempt, so the
    /// backoff keeps growing (1 s -> 60 s) instead of retrying at the first
    /// delay forever.
    public func connectionFailed(reason: String = "connect_failed") {
        guard !connected else { return }
        closedReason = reason
        if reconnectAllowed { nextReconnectDelay = backoff.nextDelay() }
    }

    /// The owner's local action after a `bye` that stops reconnecting
    /// (revoked, kill switch, credential expired): allow reconnecting again.
    public func ownerResume() {
        reconnectAllowed = true
        backoff.reset()
        nextReconnectDelay = 0
    }

    /// The owner's local kill switch: closes an open window (reported as
    /// `confirmation{closed}` and a refused `owner_denied` result) and stops
    /// what runs (an `error` result). While engaged, check 24 refuses every
    /// click (`expired_after_confirm`).
    public func engageKillSwitch() -> [JSONValue] {
        killSwitch.engage()
        var frames: [JSONValue] = []
        if let pending {
            self.pending = nil
            window.forceClose("kill_switch")
            confirmationObserver?.confirmationClosed(invocationID: pending.prompt.invocationID, reason: "kill_switch")
            frames += confirmation(pending.envelope, "closed")
            frames.append(refused(pending.envelope, "owner_denied", confirmState: "closed", receivedAt: pending.receivedAt))
        }
        for run in running {
            run.handle.terminate()
            frames += finish(run, status: "error", exitCode: nil)
        }
        return send(frames)
    }

    /// The owner released the local kill switch (the app asks for
    /// LocalAuthentication first).
    public func releaseKillSwitch() {
        killSwitch.release()
    }

    /// The owner changed the local policy: from now on the node answers
    /// with `readers` and advertises `catalogue` (a revoked grant is
    /// `not_granted` at once, so the availability check refuses before any
    /// reader runs). Returns the `grant.availability_changed` event to send,
    /// if the session is up and anything changed (items exactly `{name,
    /// version, availability}`, only for entries this session's `hello`
    /// carries). A node that is not connected sends nothing: its next
    /// `hello` carries the new catalogue.
    public func update(
        catalogue newCatalogue: Catalogue, readers newReaders: ReadProvider, policy newPolicy: ActionPolicy? = nil,
        executor newExecutor: ActionExecutor? = nil
    ) -> [JSONValue] {
        let items = availabilityItems(from: catalogue, to: newCatalogue)
        catalogue = newCatalogue
        readers = newReaders
        if let newPolicy { policy = newPolicy }
        if let newExecutor { executor = newExecutor }
        // Before `hello.ack`, `catalogueAtHello` holds what the server saw:
        // the difference is sent when the ack arrives.
        guard connected, sessionReady else { return [] }
        return send(availabilityEvents(items))
    }

    private func availabilityItems(from old: Catalogue, to new: Catalogue) -> [JSONValue] {
        AvailabilityChange.items(from: old, to: Catalogue(entries: helloEntries(new)))
    }

    // MARK: Hook events and the spool

    /// One hook event from the relay: sent live when the session is up and
    /// nothing is in flight or spooled; otherwise spooled (and the oldest
    /// spooled batch uploaded when the session is up).
    public func relayHookEvent(_ event: JSONValue) -> [JSONValue] {
        if canUpload && (spool == nil || spool?.first() == nil) {
            let frame = eventFrame([event], spooled: false)
            eventInFlight = (frame["id"]?.stringValue ?? "", [event], nil)
            return send([frame])
        }
        spoolEvents([event])
        return send(nextUpload())
    }

    private var canUpload: Bool { connected && sessionReady && eventInFlight == nil }

    private func spoolEvents(_ events: [JSONValue]) {
        if spool?.append(events) == nil { eventsDropped += events.count }
    }

    private func eventFrame(_ events: [JSONValue], spooled: Bool) -> JSONValue {
        [
            "v": .int(version),
            "type": "event",
            "id": .string(makeID()),
            "kind": "claude_code.hook",
            "batch": .array(events),
            "spooled": .bool(spooled),
        ]
    }

    private func nextUpload() -> [JSONValue] {
        guard canUpload, let spool, let batch = spool.first() else { return [] }
        let frame = eventFrame(batch.events, spooled: true)
        eventInFlight = (frame["id"]?.stringValue ?? "", batch.events, batch.batchID)
        return [frame]
    }

    /// An `event.ack` can only delete the batch it names; it is never
    /// answered.
    private func eventAcked(_ eventID: String) -> [JSONValue] {
        if voiceFrameIDs.contains(eventID) { return [] }  // a voice state's ack: nothing waits for it
        if let index = availabilityFrameIDs.firstIndex(of: eventID) {
            availabilityFrameIDs.remove(at: index)
            return []
        }
        guard let inFlight = eventInFlight, inFlight.frameID == eventID else {
            strayEventAcks += 1  // never deletes anything
            return []
        }
        eventInFlight = nil
        if let batchID = inFlight.batchID { spool?.ack(batchID) }
        return nextUpload()
    }

    private func availabilityEvents(_ items: [JSONValue]) -> [JSONValue] {
        guard !items.isEmpty else { return [] }
        return stride(from: 0, to: items.count, by: 50).map { start in
            let id = makeID()
            availabilityFrameIDs.append(id)
            if availabilityFrameIDs.count > 64 { availabilityFrameIDs.removeFirst() }
            return [
                "v": .int(version),
                "type": "event",
                "id": .string(id),
                "kind": "grant.availability_changed",
                "batch": .array(Array(items[start..<min(start + 50, items.count)])),
                "spooled": false,
            ]
        }
    }

    // MARK: Voice state (Phase 11 D15)

    /// One mic state from the voice host: `state`, `mode`, `session_open`
    /// and the three daily counters (the node adds `at`, its clock). Returns
    /// the frames to send now (none while coalescing). v1 never relays one.
    public func relayVoiceState(_ state: JSONValue) -> [JSONValue] {
        guard version == 2, var item = state.objectValue else {
            voiceStatesDropped += 1
            return []
        }
        item["at"] = .int(clock.now())
        guard Set(item.keys) == Self.voiceStateItemKeys,
            (try? NodeProtocol.validate(voiceFrame(item), direction: .nodeToServer, version: version)) != nil
        else {
            voiceStatesDropped += 1
            return []
        }
        voiceCurrent = item
        voicePending = true
        return send(voiceFlush())
    }

    private func voiceFrame(_ item: [String: JSONValue]) -> JSONValue {
        [
            "v": .int(version),
            "type": "event",
            "id": .string(makeID()),
            "kind": .string(Self.voiceStateKind),
            "batch": [.object(item)],
            "spooled": false,
        ]
    }

    /// The pending (latest) state, when the session is up and the last
    /// voice frame left at least a second ago.
    private func voiceFlush() -> [JSONValue] {
        guard connected, sessionReady, version == 2, voicePending, let current = voiceCurrent else { return [] }
        let now = clock.now()
        if let sentAt = voiceSentAt, now - sentAt < Self.voiceStateMinIntervalSeconds { return [] }
        let frame = voiceFrame(current)
        voicePending = false
        voiceSentAt = now
        voiceFrameIDs.append(frame["id"]?.stringValue ?? "")
        if voiceFrameIDs.count > Self.voiceAckMemory { voiceFrameIDs.removeFirst() }
        voiceStatesSent += 1
        return [frame]
    }

    // MARK: Inputs

    public func receive(_ raw: Data) -> [JSONValue] {
        guard connected else { return [] }
        let now = clock.now()
        lastServerFrameAt = now
        let verified: VerifiedFrame
        do {
            if version == 2 {
                verified = try VerifierV2.verify(raw, state: verifierState, policy: policy, now: now)
            } else {
                verified = VerifiedFrame(message: try Verifier.verify(raw, state: verifierState, now: now), resolved: nil)
            }
        } catch let refusal as Refusal {
            if refusal.isFrameError { return frameError(refusal.code) }
            // Checks 1-4 passed, so the frame parses.
            let envelope = (try? JSON.parse(raw)) ?? .null
            return send([refused(envelope, refusal.code, receivedAt: now)])
        } catch {
            return frameError("malformed")
        }
        let message = verified.message
        switch message["type"]?.stringValue {
        case "bye":
            let reason = message["reason"]?.stringValue ?? ""
            if NodeProtocol.noReconnectReasons.contains(reason) { reconnectAllowed = false }
            disconnect(reason: "bye:\(reason)")
            return []
        case "hello.ack":
            sessionReady = true
            // The backoff restarts only once the server accepted the session:
            // a server that opens the socket and closes it again (4408, 4429)
            // keeps growing the delay.
            backoff.reset()
            helloAck = HelloAck(
                nodeID: message["node_id"]?.stringValue ?? "",
                heartbeatSeconds: message["heartbeat_seconds"]?.intValue ?? NodeProtocol.heartbeatSeconds,
                rotateAfterDays: message["rotate_after_days"]?.intValue ?? 0,
                serverTime: message["server_time"]?.intValue ?? 0,
                accepted: (message["accepted"]?.arrayValue ?? []).compactMap(\.stringValue))
            // A policy change between `hello` and `hello.ack` goes out now,
            // the spooled backlog starts uploading, the mic state goes out.
            let items = catalogueAtHello.map { availabilityItems(from: $0, to: catalogue) } ?? []
            catalogueAtHello = nil
            return send(nextUpload() + voiceFlush() + availabilityEvents(items))
        case "event.ack":
            return send(eventAcked(message["event_id"]?.stringValue ?? ""))
        case "invoke":
            if version == 1 || message["class"]?.stringValue == "read" {
                if readDelaySeconds > 0 {
                    pendingReads.append((message, now + readDelaySeconds, now))
                    return []
                }
                return send([readResult(message, receivedAt: now)])
            }
            return send(openWindow(message, resolved: verified.resolved, now: now))
        default:
            return []  // heartbeat.ack: nothing to answer
        }
    }

    public func tick() -> [JSONValue] {
        guard connected else { return [] }
        let now = clock.now()
        var frames = pumpWindow()
        let due = pendingReads.filter { now >= $0.dueAt }
        pendingReads.removeAll { now >= $0.dueAt }
        for read in due { frames.append(readResult(read.envelope, receivedAt: read.receivedAt)) }
        for run in running { frames += pumpRunning(run) }
        if now - lastHeartbeatAt >= NodeProtocol.heartbeatSeconds {
            lastHeartbeatAt = now
            frames.append(heartbeat(now: now))
            if var current = voiceCurrent, sessionReady {
                // The keep-alive: the current mic state, as of now.
                current["at"] = .int(now)
                voiceCurrent = current
                voicePending = true
            }
        }
        frames += voiceFlush()
        let out = send(frames)
        if let last = lastServerFrameAt, now - last > Self.silenceSeconds {
            disconnect(reason: "server_silent")
        }
        return out
    }

    /// The owner clicked the open window's button. Takes effect on the next
    /// `tick()` (the caller ticks right away).
    @discardableResult
    public func decide(_ action: ConfirmationAction, invocationID: String) -> Bool {
        window.decide(action, invocationID: invocationID, now: clock.now())
    }

    /// "Durdur": stop the running command of `invocationID` (SIGTERM, then
    /// SIGKILL after the grace) and report it as an `error` result, as the
    /// kill switch does for every running command.
    public func stop(invocationID: String) -> [JSONValue] {
        guard connected, let run = running.first(where: { $0.invocationID == invocationID }) else { return [] }
        run.handle.terminate()
        return send(finish(run, status: "error", exitCode: nil))
    }

    // MARK: The window (checks 23 and 24)

    private func openWindow(_ envelope: JSONValue, resolved: ResolvedIntent?, now: Int64) -> [JSONValue] {
        let prompt = ConfirmationRenderer.prompt(
            envelope: envelope, resolved: resolved, shownAt: now, timeout: window.timeout)
        do {
            try window.open(prompt)
        } catch {
            return [refused(envelope, "busy_confirming", receivedAt: now)]
        }
        pending = Pending(envelope: envelope, resolved: resolved, receivedAt: now, prompt: prompt)
        confirmationObserver?.confirmationShown(prompt)
        return confirmation(envelope, "shown") + pumpWindow()
    }

    private func pumpWindow() -> [JSONValue] {
        guard let current = pending, let state = window.poll(now: clock.now()) else { return [] }
        pending = nil
        confirmationObserver?.confirmationClosed(invocationID: current.prompt.invocationID, reason: state)
        let envelope = current.envelope
        let frames = confirmation(envelope, state)
        switch state {
        case "denied":
            return frames + [refused(envelope, "owner_denied", confirmState: "denied", receivedAt: current.receivedAt)]
        case "timed_out":
            return frames
                + [refused(envelope, "confirm_timeout", confirmState: "timed_out", receivedAt: current.receivedAt)]
        case "closed":
            return frames + [refused(envelope, "owner_denied", confirmState: "closed", receivedAt: current.receivedAt)]
        default:
            break
        }
        // Check 24: after the click.
        guard connected else { return [] }
        if clock.now() >= envelope["expires_at"]?.intValue ?? 0 || killSwitchEngaged {
            return frames
                + [refused(envelope, "expired_after_confirm", confirmState: "approved", receivedAt: current.receivedAt)]
        }
        return frames + execute(current)
    }

    // MARK: Executing

    private func execute(_ current: Pending) -> [JSONValue] {
        let envelope = current.envelope
        let capability = envelope["capability"]?.stringValue ?? ""
        guard capability == ActionContracts.commandCapability else {
            // A repo recipe's repo must still resolve to the mapped root now.
            if let repo = current.resolved?.repo, !stillResolves(repo, envelope["args"]?["params"]?["repo_path"]) {
                return [result(for: envelope, status: "error", confirmState: "approved", receivedAt: current.receivedAt)]
            }
            let outcome = executor.perform(
                ActionRequest(
                    capability: capability, args: envelope["args"] ?? [:], bundleID: current.resolved?.bundleID,
                    recipe: current.resolved?.recipe, repoRoot: current.resolved?.repo?.realRoot))
            return [
                result(
                    for: envelope, status: outcome.ok ? "ok" : "error", confirmState: "approved",
                    receivedAt: current.receivedAt)
            ]
        }
        let failed = [result(for: envelope, status: "error", confirmState: "approved", receivedAt: current.receivedAt)]
        guard let repo = current.resolved?.repo, let command = current.resolved?.command else { return failed }
        let repoRoot = repo.realRoot
        // Check 22 ran before the window; the repo must still resolve to the
        // same mapped root now (B5 review finding 4).
        guard stillResolves(repo, envelope["args"]?["repo_path"]) else { return failed }
        var tempDir: String?
        var worktree: String?
        var ownBase: String?
        do {
            let temp = try TempDirectory.make(prefix: "friday-run-")
            tempDir = temp
            var writeRoot = repoRoot
            if command.mode == "worktree" {
                var base = worktreeBase
                if base == nil {
                    ownBase = try TempDirectory.make(prefix: "friday-wt-")
                    base = ownBase
                }
                let made = try Worktree.create(repoRoot, base: base ?? "")
                worktree = made
                writeRoot = made
            }
            // The kill switch may have been engaged from the menu while git
            // ran (it stops a running git from any thread).
            guard !killSwitch.engaged else { throw ExecutorError("kill switch") }
            let contractMax = CapabilityContract.named(capability)?.maxRuntimeSeconds ?? ActionContracts.maxRuntimeSeconds
            let realTemp = ReaderPaths.realpathC(temp)
            let sandbox = try SandboxPolicy.build(
                command: command, home: policy.home, repoRoot: repoRoot, writeRoot: writeRoot, tempDir: realTemp,
                contractMaxRuntime: contractMax, nodeEnvironment: nodeEnvironment, nodeDirs: nodeDirs)
            let spec = CommandSpec(
                mode: command.mode, argv: command.argv, cwd: writeRoot, repoRoot: repoRoot, writeRoot: writeRoot,
                tempDir: realTemp, path: command.path, networkDomains: command.networkDomains,
                wallSeconds: sandbox.wallSeconds, sandbox: sandbox)
            let handle = try executor.start(spec)
            let run = Running(
                envelope: envelope, handle: handle, streamer: OutputStreamer(scrub: scrub), startedAt: clock.now(),
                receivedAt: current.receivedAt, wallSeconds: spec.wallSeconds, prompt: current.prompt,
                repoRoot: repoRoot, worktree: worktree, tempDir: tempDir, ownBase: ownBase)
            running.append(run)
            confirmationObserver?.commandStarted(invocationID: run.invocationID)
            return pumpRunning(run)
        } catch {
            if let worktree { Worktree.remove(repoRoot, worktree) }
            TempDirectory.remove(tempDir)
            TempDirectory.remove(ownBase)
            return failed
        }
    }

    /// `path` (as the server sent it) and the mapping's own root both still
    /// realpath-resolve to the root check 22 found.
    private func stillResolves(_ repo: RepoMapping, _ path: JSONValue?) -> Bool {
        guard let path = path?.stringValue else { return false }
        let viaArgs = ReaderPaths.realpathC(ActionPolicy.expand(path, home: policy.home))
        let viaMapping = ReaderPaths.realpathC(ActionPolicy.expand(repo.root, home: policy.home))
        return Scalars.equal(viaArgs, repo.realRoot) && Scalars.equal(viaMapping, repo.realRoot)
    }

    private func feed(_ run: Running, _ chunk: OutputChunk) -> [JSONValue] {
        var frames = outputFrames(run, run.streamer.feed(chunk.stream, chunk.data))
        if chunk.droppedBytes > 0 { frames += outputFrames(run, run.streamer.countDropped(chunk.droppedBytes)) }
        return frames
    }

    private func outputFrames(_ run: Running, _ pieces: [OutputPiece]) -> [JSONValue] {
        pieces.map { piece in
            [
                "v": 2,
                "type": "output",
                "id": .string(makeID()),
                "invocation_id": .string(run.invocationID),
                "seq": .int(piece.seq),
                "stream": .string(piece.stream),
                "data": .string(piece.data),
                "truncated": .bool(piece.truncated),
            ]
        }
    }

    private func pumpRunning(_ run: Running) -> [JSONValue] {
        var frames: [JSONValue] = []
        for chunk in run.handle.read() { frames += feed(run, chunk) }
        if let outcome = run.handle.poll() {
            for chunk in run.handle.read() { frames += feed(run, chunk) }
            return frames
                + finish(
                    run, status: outcome.error == nil ? "ok" : "error", exitCode: outcome.exitCode,
                    sandboxViolation: outcome.sandboxViolation)
        }
        if clock.now() - run.startedAt >= run.wallSeconds {
            run.handle.terminate()
            return frames + finish(run, status: "timeout", exitCode: nil)
        }
        return frames
    }

    private func finish(_ run: Running, status: String, exitCode: Int64?, sandboxViolation: Bool = false)
        -> [JSONValue]
    {
        let frames = outputFrames(run, run.streamer.finish())
        running.removeAll { $0 === run }
        cleanup(run)
        confirmationObserver?.commandEnded(invocationID: run.invocationID)
        return frames + [
            result(
                for: run.envelope, status: status, truncated: run.streamer.truncated, confirmState: "approved",
                receivedAt: run.receivedAt, exitCode: exitCode.map { min(max($0, -255), 255) },
                outputBytes: run.streamer.outputBytes, tail: run.streamer.tail, sandboxViolation: sandboxViolation)
        ]
    }

    /// Remove the run's worktree and temp dirs once its process tree is gone
    /// (`afterExit`: at once for a finished command, later for one that was
    /// just told to stop; the real executor does it off the session queue).
    private func cleanup(_ run: Running) {
        let worktree = run.worktree
        let repoRoot = run.repoRoot
        let tempDir = run.tempDir
        let ownBase = run.ownBase
        run.worktree = nil
        run.tempDir = nil
        run.ownBase = nil
        guard worktree != nil || tempDir != nil || ownBase != nil else { return }
        run.handle.afterExit {
            if let worktree, let repoRoot { Worktree.remove(repoRoot, worktree) }
            TempDirectory.remove(tempDir)
            TempDirectory.remove(ownBase)
        }
    }

    // MARK: Frames

    private func heartbeat(now: Int64) -> JSONValue {
        heartbeatSeq += 1
        let depth = spool?.depth() ?? (count: 0, bytes: 0)
        return [
            "v": .int(version),
            "type": "heartbeat",
            "id": .string(makeID()),
            "seq": .int(heartbeatSeq),
            "uptime_seconds": .int(max(now - connectedAt, 0)),
            "spool_count": .int(Int64(depth.count)),
            "spool_bytes": .int(Int64(depth.bytes)),
        ]
    }

    private func readResult(_ envelope: JSONValue, receivedAt: Int64) -> JSONValue {
        let capability = envelope["capability"]?.stringValue ?? ""
        let outcome = readers.read(capability: capability, args: envelope["args"] ?? .null, now: clock.now())
        guard outcome.ok, let output = outcome.output, outputFits(output, capability: capability) else {
            return result(for: envelope, status: "error", receivedAt: receivedAt)
        }
        return result(
            for: envelope, status: "ok", output: output, truncated: outcome.truncated, receivedAt: receivedAt)
    }

    /// The node's own result_schema and size bound hold for `output`.
    private func outputFits(_ output: JSONValue, capability: String) -> Bool {
        guard let entry = catalogue.byName[capability], let schema = entry["result_schema"],
            Schema.isValid(output, against: schema), let bytes = try? JSON.canonical(output)
        else { return false }
        let limit = CapabilityContract.all.first { $0.name == capability }?.maxResultBytes
            ?? Int64(NodeProtocol.maxResultBytes)
        return bytes.count <= min(Int(limit), NodeProtocol.maxResultBytes)
    }

    private func confirmation(_ envelope: JSONValue, _ state: String) -> [JSONValue] {
        [
            [
                "v": 2,
                "type": "confirmation",
                "id": .string(makeID()),
                "invocation_id": envelope["invocation_id"] ?? .null,
                "state": .string(state),
                "at": .int(clock.now()),
            ]
        ]
    }

    private func refused(
        _ envelope: JSONValue?, _ code: String, confirmState: String = "not_required", receivedAt: Int64
    ) -> JSONValue {
        result(for: envelope, status: "refused", refusalCode: code, confirmState: confirmState, receivedAt: receivedAt)
    }

    private func result(
        for envelope: JSONValue?, status: String, refusalCode: String? = nil, output: JSONValue? = nil,
        truncated: Bool = false, confirmState: String = "not_required", receivedAt: Int64? = nil,
        exitCode: Int64? = nil, outputBytes: Int64 = 0, tail: String? = nil, sandboxViolation: Bool = false
    ) -> JSONValue {
        let now = clock.now()
        let duration = receivedAt.map { min(max(now - $0, 0) * 1000, Self.maxDurationMilliseconds) } ?? 0
        let invocation = envelope?["invocation_id"]?.stringValue
        var frame: [String: JSONValue] = [
            "v": .int(version),
            "type": "result",
            "id": .string(makeID()),
            "invocation_id": invocation.map(JSONValue.string) ?? .null,
            "status": .string(status),
            "refusal_code": refusalCode.map(JSONValue.string) ?? .null,
            "output": output ?? .null,
            "truncated": .bool(truncated),
            "output_sha256": .string(output.flatMap { try? JSON.sha256Hex($0) } ?? ""),
            "duration_ms": .int(duration),
        ]
        if version == 2 {
            frame["exit_code"] = exitCode.map(JSONValue.int) ?? .null
            frame["output_bytes"] = .int(outputBytes)
            frame["tail"] = tail.map(JSONValue.string) ?? .null
            frame["confirm_state"] = .string(confirmState)
            frame["sandbox_violation"] = .bool(sandboxViolation)
        }
        return .object(frame)
    }

    private func frameError(_ code: String) -> [JSONValue] {
        let now = clock.now()
        frameErrorTimes.append(now)
        frameErrorTimes.removeAll { $0 <= now - Self.frameErrorWindowSeconds }
        let frames = send([result(for: nil, status: "error", refusalCode: code)])
        if frameErrorTimes.count >= Self.frameErrorLimit {
            closedReason = "protocol_error"
            disconnect(reason: "protocol_error")
        }
        return frames
    }

    private func send(_ frames: [JSONValue]) -> [JSONValue] {
        var out: [JSONValue] = []
        for frame in frames {
            guard connected else {
                framesDroppedAfterDisconnect += 1
                continue
            }
            guard (try? NodeProtocol.validate(frame, direction: .nodeToServer, version: version)) != nil else {
                invalidOutgoingFrames += 1
                continue
            }
            out.append(frame)
        }
        return out
    }
}
