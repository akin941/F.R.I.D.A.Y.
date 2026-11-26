// The node side of one Friday Node session, protocol v1 (Phase 9 brief D5,
// D6, D13 items 1-4 and 9). A port of the reference node
// (backend/nodes/reference/node.py), limited to what v1 / M1 needs.
//
// A synchronous state machine driven by four inputs:
// - `connect()`: a new socket is up; returns `[hello]`;
// - `receive(raw)`: one server -> node frame; returns the frames to send;
// - `tick()`: the clock moved (a heartbeat is due, a delayed read is due, the
//   server went silent); returns the frames to send;
// - `disconnect(reason:)`: the socket is gone. Fail closed: in-flight reads
//   are abandoned and no late frame is ever sent.
//
// The transport (Session.swift) owns the socket; this type owns the rules,
// so the offline conformance suite drives exactly the code the app runs.
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
// - Every frame the node sends is validated against the v1 schema first; a
//   frame that fails is never sent (and counted, so tests catch the bug).
// - No `voice.state` in v1: a mic state handed to a v1 session is dropped
//   and counted (Phase 11 D15).

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

/// The readers (M2). M1 ships none that touch owner data.
public protocol ReadProvider: AnyObject {
    /// Run one reader. `args` already passed checks 1-16.
    func read(capability: String, args: JSONValue, now: Int64) -> ReadOutcome
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

    public private(set) var connected = false
    public private(set) var sessionReady = false
    public private(set) var closedReason: String?
    public private(set) var reconnectAllowed = true
    public private(set) var nextReconnectDelay: Double?
    public private(set) var helloAck: HelloAck?
    public private(set) var voiceStatesDropped = 0
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
        backoff: Backoff = Backoff(), makeID: @escaping () -> String = { UUID().uuidString.lowercased() }
    ) {
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
    }

    private var verifierState: VerifierState {
        VerifierState(nodeID: nodeID, pinned: pinned, catalogue: catalogue, nonceCache: nonceCache)
    }

    // MARK: Session

    /// A new socket is up (protocol v1); returns `[hello]`. The nonce cache
    /// survives reconnects. Whether to open a socket at all (after a `bye`
    /// that stops reconnecting) is the transport's decision: it checks
    /// `reconnectAllowed` first.
    public func connect() -> [JSONValue] {
        let now = clock.now()
        connected = true
        sessionReady = false
        closedReason = nil
        frameErrorTimes.removeAll()
        lastServerFrameAt = now
        lastHeartbeatAt = now
        connectedAt = now
        nextReconnectDelay = nil
        catalogueAtHello = catalogue
        return send([hello()])
    }

    public func hello() -> JSONValue {
        let entries = catalogue.entries.filter { $0["class"]?.stringValue == "read" }
        return [
            "v": .int(NodeProtocol.version),
            "type": "hello",
            "id": .string(makeID()),
            "node_version": .string(nodeVersion),
            "os_version": .string(osVersion),
            "catalogue": .array(entries),
            "catalogue_sha256": .string((try? NodeProtocol.catalogueSHA256(entries)) ?? ""),
        ]
    }

    /// The socket is gone. Idempotent.
    public func disconnect(reason: String = "socket_lost") {
        if !connected && closedReason != nil { return }
        connected = false
        sessionReady = false
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

    /// The owner changed the local policy: from now on the node answers
    /// with `readers` and advertises `catalogue` (a revoked grant is
    /// `not_granted` at once, so check 16 refuses before any reader runs).
    /// Returns the `grant.availability_changed` event to send, if the
    /// session is up and anything changed (items exactly `{name, version,
    /// availability}`). A node that is not connected sends nothing: its next
    /// `hello` carries the new catalogue.
    public func update(catalogue newCatalogue: Catalogue, readers newReaders: ReadProvider) -> [JSONValue] {
        let items = AvailabilityChange.items(from: catalogue, to: newCatalogue)
        catalogue = newCatalogue
        readers = newReaders
        // Before `hello.ack`, `catalogueAtHello` holds what the server saw:
        // the difference is sent when the ack arrives.
        guard connected, sessionReady else { return [] }
        return send(availabilityEvents(items))
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
            "v": .int(NodeProtocol.version),
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
                "v": .int(NodeProtocol.version),
                "type": "event",
                "id": .string(id),
                "kind": "grant.availability_changed",
                "batch": .array(Array(items[start..<min(start + 50, items.count)])),
                "spooled": false,
            ]
        }
    }

    /// A mic state from the voice host. v1 never relays one.
    public func relayVoiceState(_ state: JSONValue) -> [JSONValue] {
        voiceStatesDropped += 1
        return []
    }

    // MARK: Inputs

    public func receive(_ raw: Data) -> [JSONValue] {
        guard connected else { return [] }
        let now = clock.now()
        lastServerFrameAt = now
        let message: JSONValue
        do {
            message = try Verifier.verify(raw, state: verifierState, now: now)
        } catch let refusal as Refusal {
            if refusal.isFrameError { return frameError(refusal.code) }
            // Checks 1-4 passed, so the frame parses.
            let envelope = (try? JSON.parse(raw)) ?? .null
            return send([result(for: envelope, status: "refused", refusalCode: refusal.code, receivedAt: now)])
        } catch {
            return frameError("malformed")
        }
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
            // and the spooled backlog starts uploading.
            let items = catalogueAtHello.map { AvailabilityChange.items(from: $0, to: catalogue) } ?? []
            catalogueAtHello = nil
            return send(nextUpload() + availabilityEvents(items))
        case "event.ack":
            return send(eventAcked(message["event_id"]?.stringValue ?? ""))
        case "invoke":
            if readDelaySeconds > 0 {
                pendingReads.append((message, now + readDelaySeconds, now))
                return []
            }
            return send([readResult(message, receivedAt: now)])
        default:
            return []  // heartbeat.ack, event.ack: nothing to answer
        }
    }

    public func tick() -> [JSONValue] {
        guard connected else { return [] }
        let now = clock.now()
        var frames: [JSONValue] = []
        let due = pendingReads.filter { now >= $0.dueAt }
        pendingReads.removeAll { now >= $0.dueAt }
        for read in due { frames.append(readResult(read.envelope, receivedAt: read.receivedAt)) }
        if now - lastHeartbeatAt >= NodeProtocol.heartbeatSeconds {
            lastHeartbeatAt = now
            frames.append(heartbeat(now: now))
        }
        let out = send(frames)
        if let last = lastServerFrameAt, now - last > Self.silenceSeconds {
            disconnect(reason: "server_silent")
        }
        return out
    }

    // MARK: Frames

    private func heartbeat(now: Int64) -> JSONValue {
        heartbeatSeq += 1
        return [
            "v": .int(NodeProtocol.version),
            "type": "heartbeat",
            "id": .string(makeID()),
            "seq": .int(heartbeatSeq),
            "uptime_seconds": .int(max(now - connectedAt, 0)),
            "spool_count": .int(Int64(spool?.depth().count ?? 0)),
            "spool_bytes": .int(Int64(spool?.depth().bytes ?? 0)),
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

    private func result(
        for envelope: JSONValue?, status: String, refusalCode: String? = nil, output: JSONValue? = nil,
        truncated: Bool = false, receivedAt: Int64? = nil
    ) -> JSONValue {
        let now = clock.now()
        let duration = receivedAt.map { min(max(now - $0, 0) * 1000, Self.maxDurationMilliseconds) } ?? 0
        let invocation = envelope?["invocation_id"]?.stringValue
        return [
            "v": .int(NodeProtocol.version),
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
            guard (try? NodeProtocol.validate(frame, direction: .nodeToServer)) != nil else {
                invalidOutgoingFrames += 1
                continue
            }
            out.append(frame)
        }
        return out
    }
}
