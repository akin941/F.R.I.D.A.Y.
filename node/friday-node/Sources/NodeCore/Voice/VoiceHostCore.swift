// The host's side of `friday-voice/v1` as a pure, clock-injected state
// machine (Phase 11 brief D6, D9, D12; Mac session M1). A line-by-line port
// of node/friday-voice/friday_voice/reference/host.py `HostCore`, the
// executable spec: the offline host cases (VoiceConformanceTests), the live
// runs (the Conformance build) and the app (VoiceChildHost) all drive this
// type, so they exercise exactly one implementation.
//
// Host rules (D6, D12), each pinned by a host case:
// - the child's first frame must be `ready`; then `state` at least every
//   10 s, else the watchdog fires;
// - a malformed, over-length or unknown frame, a policy-shaped name (refused
//   by the frozen sets and `additionalProperties: false`), odd-length PCM, a
//   missing `state` for 10 s and the child's exit all mean: release the mic,
//   show `off`, drop the link and restart the child with backoff 1 s -> 60 s
//   (reset after 60 s of a healthy child);
// - `play` must open an utterance at seq 0 and continue it seq by seq; a
//   frame for an unknown, closed or out-of-order utterance is dropped and
//   counted, the link stays up;
// - the host releases the mic itself on its own fail-closed inputs (node
//   link down, mute on or unreadable, mode off) whatever the child does, and
//   only (re)acquires it when the child reports an armed or listening state
//   while the local mode, mute and link allow it;
// - capture frames reach the child only while the mic is held.
//
// Paced playback (`pacedPlayback: true`, the app; branch 4): utterances
// queue in `play` order, each reports `started` when it begins and `ended`
// once its audio has run for its length on the host clock (`tick()`), and
// `stop_playback` stops an utterance that is still queued or playing, even
// after its last frame arrived. The audio itself reaches the speakers
// through `effects` (AudioEngine): the head utterance's PCM as it arrives,
// a queued one's when it becomes the head, a stop at once.

import Foundation

/// Milliseconds: monotonic (watchdog, backoff, pacing) and wall clock (the
/// `at_ms` / `captured_at_ms` fields).
public protocol VoiceClock: AnyObject {
    func monotonicMs() -> Int64
    func nowMs() -> Int64
}

public final class SystemVoiceClock: VoiceClock, @unchecked Sendable {
    public init() {}
    public func monotonicMs() -> Int64 { Int64(DispatchTime.now().uptimeNanoseconds / 1_000_000) }
    public func nowMs() -> Int64 { Int64(Date().timeIntervalSince1970 * 1000) }
}

/// A clock tests (and the live runner's manual cases) move by hand.
public final class ManualVoiceClock: VoiceClock, @unchecked Sendable {
    public var monotonic: Int64
    public var wall: Int64
    /// ports.ManualClock starts at 2027-01-15T08:00:00Z.
    public init(monotonic: Int64 = 0, wall: Int64 = 1_799_913_600_000) {
        self.monotonic = monotonic
        self.wall = wall
    }
    public func monotonicMs() -> Int64 { monotonic }
    public func nowMs() -> Int64 { wall }
    public func advance(_ ms: Int64) {
        monotonic += ms
        wall += ms
    }
}

/// What the host must do outside the protocol: speakers, earcons, the
/// `voice.state` relay, the local log.
public enum VoiceHostEffect: Equatable {
    /// An utterance starts playing (paced: it became the head).
    case playbackBegan(String)
    /// PCM of the playing utterance, in order.
    case playbackAudio(String, Data)
    /// An utterance was stopped (`stop_playback` honoured, or the child was
    /// failed closed while it played): silence it now.
    case playbackStopped(String)
    case earcon(String)
    /// The child's `state` item (`state`, `mode`, `session_open`, the three
    /// counters), for NodeSession.relayVoiceState.
    case voiceState(JSONValue)
    /// The child released the mic (its reason): `credential_refused` makes
    /// the host re-mint.
    case childReleasedMic(String)
    case log(String)
}

public final class VoiceHostCore {
    public static let watchdogMs: Int64 = 10_000
    public static let backoffStartMs: Int64 = 1_000
    public static let backoffMaxMs: Int64 = 60_000
    public static let backoffResetAfterMs: Int64 = 60_000
    public static let acquireStates: Set<String> = ["armed_ptt", "armed_wake", "listening", "thinking", "speaking"]
    public static let frameMs = 20
    public static let frameBytes = 16_000 * 2 * frameMs / 1000  // 640
    /// Paced playback keeps a queued utterance's PCM until it plays; past
    /// this much queued audio a frame is dropped and counted (memory bound;
    /// the reference keeps only durations).
    public static let maxQueuedPCMBytes = 16 * 1024 * 1024

    public enum Muted: Equatable, Sendable {
        case no, yes, unreadable
        public var json: JSONValue {
            switch self {
            case .no: return .bool(false)
            case .yes: return .bool(true)
            case .unreadable: return .string("unreadable")
            }
        }
    }

    public let clock: VoiceClock
    public private(set) var credential: [String: JSONValue]?
    public private(set) var mode: String
    public private(set) var muted: Muted
    public private(set) var linkUp: Bool
    public let hostVersion: String
    public let pacedPlayback: Bool

    private struct Queued {
        let utteranceID: String
        var durationMs: Int64 = 0
        var lastReceived = false
        var startedAt: Int64?
        var pending: [Data] = []
    }
    private var queue: [Queued] = []
    private var queuedBytes = 0
    public private(set) var stoppedAt: [(String, Int64)] = []
    public private(set) var micHeld = false
    public private(set) var indicator = "off"
    public private(set) var childRunning = false
    public private(set) var childReady = false
    public private(set) var linkOpen = false
    public private(set) var childState: String?
    public private(set) var backoffMs = VoiceHostCore.backoffStartMs
    public private(set) var restartAt: Int64?
    public private(set) var restartInMs: Int64?
    public private(set) var starts = 0
    public private(set) var failures: [(String, Int64)] = []
    public private(set) var releases: [String] = []
    public private(set) var droppedPlay = 0
    public private(set) var droppedStop = 0
    public private(set) var played: [(String, Int64, Int)] = []  // (utterance, seq, bytes)
    public private(set) var earcons: [String] = []
    public private(set) var logs: [String] = []
    public private(set) var audioSent = 0
    public private(set) var framesReceived = 0
    private var utterances: [String: Int64] = [:]
    private var closed: Set<String> = []
    private var outbound: [Data] = []
    private var effects: [VoiceHostEffect] = []
    private var decoder: VoiceFrameDecoder?
    private var readyAt: Int64?
    private var lastStateAt: Int64?
    private var seq: Int64 = 0
    public private(set) var stopping = false

    public init(
        clock: VoiceClock, credential: [String: JSONValue]? = nil, mode: String = "push_to_talk",
        muted: Muted = .no, linkUp: Bool = true, hostVersion: String = "0.1.0", pacedPlayback: Bool = false
    ) {
        self.clock = clock
        self.credential = credential
        self.mode = mode
        self.muted = muted
        self.linkUp = linkUp
        self.hostVersion = hostVersion
        self.pacedPlayback = pacedPlayback
    }

    // MARK: Lifecycle

    public var lastFailure: String? { failures.last?.0 }

    /// The driver spawned a fresh child: greet it.
    public func startChild() {
        starts += 1
        childRunning = true
        childReady = false
        linkOpen = true
        childState = nil
        restartAt = nil
        restartInMs = nil
        stopping = false
        decoder = VoiceFrameDecoder()
        utterances.removeAll()
        closed.removeAll()
        stopQueue()
        lastStateAt = clock.monotonicMs()
        send([
            "type": "hello", "protocol": .string(VoiceProtocol.name), "host_version": .string(hostVersion),
            "sample_rate": .int(Int64(VoiceProtocol.sampleRate)), "mode": .string(mode), "muted": muted.json,
            "link_up": .bool(linkUp),
        ])
        if let credential { sendCredential(credential) }
    }

    /// `credential` without its `type` (token, expires_at, server_url,
    /// ca_sha256).
    public func sendCredential(_ credential: [String: JSONValue]) {
        self.credential = credential
        credentialRefused = false
        var header = credential
        header["type"] = "credential"
        send(header)
    }

    /// Forget the credential (the kill switch): nothing is sent; the
    /// child's own copy expires on its own clock.
    public func dropCredential() { credential = nil }

    /// The server refused the node secret (401 on the mint): a host-side
    /// fail-closed input like mute. The mic is released, the indicator reads
    /// `error`, and nothing re-acquires it until a new credential is sent.
    public private(set) var credentialRefused = false

    public func refuseCredential() {
        credential = nil
        credentialRefused = true
        release("credential_refused")
        indicator = "error"
    }

    public func restartDue() -> Bool {
        guard !childRunning, let restartAt else { return false }
        return clock.monotonicMs() >= restartAt
    }

    public func childExited(code: Int64 = 0) {
        guard childRunning else { return }
        if stopping {
            stopped()
            return
        }
        fail("child_exit")
    }

    /// The host is quitting (or stopping the child on purpose): tell the
    /// child, release, and treat its exit as expected (no restart: the
    /// driver never restarts while `stopping`). The audio engine is
    /// silenced by the driver.
    public func shutdown() {
        stopping = true
        if linkOpen { send(["type": "shutdown"]) }
        release("shutdown")
        indicator = "off"
    }

    private func stopped() {
        linkOpen = false
        childRunning = false
        childReady = false
    }

    /// The driver could not spawn the child at all (no install, lock hash
    /// mismatch): fail closed as for an exit, with that code in the log.
    public func spawnFailed(_ code: String) {
        childRunning = true
        fail(code)
    }

    // MARK: Outbound

    @discardableResult
    private func send(_ header: [String: JSONValue], _ payload: Data = Data()) -> Bool {
        guard linkOpen else { return false }
        guard let frame = try? VoiceFraming.encode(header, payload: payload, direction: .hostToChild) else {
            return false  // never reached with the host's own headers (VoiceHostCoreTests)
        }
        outbound.append(frame)
        return true
    }

    public func takeOutbound() -> [Data] {
        defer { outbound = [] }
        return outbound
    }

    public func takeEffects() -> [VoiceHostEffect] {
        defer { effects = [] }
        return effects
    }

    // MARK: The mic

    private func localOK() -> Bool { mode != "off" && muted == .no && linkUp && !credentialRefused }

    private func release(_ reason: String) {
        releases.append(reason)
        micHeld = false
    }

    private func maybeAcquire() {
        if !micHeld, !stopping, childReady, linkOpen, let state = childState, Self.acquireStates.contains(state),
            localOK()
        {
            micHeld = true
        }
    }

    /// Release the mic for a host-side reason that is not a protocol event
    /// (a refused credential): the child keeps running.
    public func releaseMic(_ reason: String, indicator: String? = nil) {
        release(reason)
        if let indicator { self.indicator = indicator }
    }

    /// Fail the child closed from the driver (it stopped reading).
    func failClosed(_ code: String) {
        guard childRunning else { return }
        fail(code)
    }

    private func fail(_ code: String) {
        failures.append((code, clock.monotonicMs()))
        release("failure:\(code)")
        indicator = "off"
        linkOpen = false
        childRunning = false
        childReady = false
        outbound.removeAll()
        stopQueue()
        restartInMs = backoffMs
        restartAt = clock.monotonicMs() + backoffMs
        backoffMs = min(backoffMs * 2, Self.backoffMaxMs)
    }

    /// Silence whatever plays and forget the queue (failure, restart,
    /// shutdown): no `playback` frame, the link is gone or going.
    private func stopQueue() {
        if let head = queue.first, head.startedAt != nil { effects.append(.playbackStopped(head.utteranceID)) }
        queue.removeAll()
        queuedBytes = 0
    }

    // MARK: Inbound from the child

    public func childBytes(_ data: Data) {
        guard linkOpen, let decoder else { return }
        let (frames, error) = decoder.feed(data)
        framesReceived += frames.count + (error == nil ? 0 : 1)
        for (header, payload) in frames {
            guard linkOpen else { return }
            do {
                try VoiceProtocol.validate(.object(header), .childToHost)
                try handle(header, payload)
            } catch let refusal as VoiceProtocolError {
                fail(refusal.code)
                return
            } catch {
                fail("schema_invalid")
                return
            }
        }
        if let error, linkOpen { fail(error) }
    }

    /// The child's stdout ended: bytes left inside a frame are a short read;
    /// a clean end is the child exiting.
    public func childStreamClosed() {
        guard linkOpen, let decoder else { return }
        let error = decoder.close()
        if stopping && error == nil {
            stopped()
            return
        }
        fail(error ?? "child_exit")
    }

    private func handle(_ header: [String: JSONValue], _ payload: Data) throws {
        let type = header["type"]?.stringValue ?? ""
        if !childReady {
            guard type == "ready" else { throw VoiceProtocolError("ready_expected") }
            childReady = true
            readyAt = clock.monotonicMs()
            return
        }
        switch type {
        case "ready": throw VoiceProtocolError("duplicate_ready")
        case "state": onState(header)
        case "play": onPlay(header, payload)
        case "stop_playback": onStopPlayback(header)
        case "earcon":
            let name = header["earcon"]?.stringValue ?? ""
            earcons.append(name)
            effects.append(.earcon(name))
        case "release_mic":
            let reason = header["reason"]?.stringValue ?? ""
            release("child:\(reason)")
            effects.append(.childReleasedMic(reason))
        case "log":
            let code = header["code"]?.stringValue ?? ""
            logs.append(code)
            effects.append(.log(code))
        default: throw VoiceProtocolError("unknown_type")  // unreachable after validate
        }
    }

    private func onState(_ header: [String: JSONValue]) {
        childState = header["state"]?.stringValue
        lastStateAt = clock.monotonicMs()
        if localOK(), let childState { indicator = childState }
        maybeAcquire()
        var item = header
        item.removeValue(forKey: "type")
        item.removeValue(forKey: "at_ms")
        effects.append(.voiceState(.object(item)))
    }

    private func onPlay(_ header: [String: JSONValue], _ payload: Data) {
        let utteranceID = header["utterance_id"]?.stringValue ?? ""
        let seq = header["seq"]?.intValue ?? -1
        let last = header["last"]?.boolValue ?? false
        var expected = utterances[utteranceID]
        if expected == nil {
            if seq != 0 || closed.contains(utteranceID) {
                droppedPlay += 1
                return
            }
            expected = 0
        }
        guard seq == expected else {
            droppedPlay += 1
            return
        }
        if pacedPlayback && queuedBytes + payload.count > Self.maxQueuedPCMBytes {
            droppedPlay += 1
            return
        }
        played.append((utteranceID, seq, payload.count))
        if pacedPlayback {
            queuePlay(utteranceID, seq: seq, last: last, payload)
            return
        }
        if seq == 0 {
            effects.append(.playbackBegan(utteranceID))
            send(playback(utteranceID, "started"))
        }
        if !payload.isEmpty { effects.append(.playbackAudio(utteranceID, payload)) }
        if last {
            utterances.removeValue(forKey: utteranceID)
            closed.insert(utteranceID)
            send(playback(utteranceID, "ended"))
        } else {
            utterances[utteranceID] = seq + 1
        }
    }

    private func playback(_ utteranceID: String, _ state: String) -> [String: JSONValue] {
        ["type": "playback", "utterance_id": .string(utteranceID), "state": .string(state), "at_ms": .int(clock.nowMs())]
    }

    /// Paced playback still has audio queued or playing.
    public var playbackPending: Bool { !queue.isEmpty && linkOpen }

    private func queuePlay(_ utteranceID: String, seq: Int64, last: Bool, _ payload: Data) {
        if seq == 0 { queue.append(Queued(utteranceID: utteranceID)) }
        guard let index = queue.firstIndex(where: { $0.utteranceID == utteranceID }) else { return }
        queue[index].durationMs += Int64(payload.count) * 1000 / Int64(VoiceProtocol.sampleRate * 2)
        if queue[index].startedAt != nil {
            if !payload.isEmpty { effects.append(.playbackAudio(utteranceID, payload)) }
        } else if !payload.isEmpty {
            queue[index].pending.append(payload)
            queuedBytes += payload.count
        }
        if last {
            queue[index].lastReceived = true
            utterances.removeValue(forKey: utteranceID)
            closed.insert(utteranceID)
        } else {
            utterances[utteranceID] = seq + 1
        }
        advancePlayback()
    }

    /// Start the head of the queue, end it once its audio has run for its
    /// length.
    private func advancePlayback() {
        let now = clock.monotonicMs()
        while !queue.isEmpty {
            if queue[0].startedAt == nil {
                queue[0].startedAt = now
                let id = queue[0].utteranceID
                send(playback(id, "started"))
                effects.append(.playbackBegan(id))
                for chunk in queue[0].pending { effects.append(.playbackAudio(id, chunk)) }
                queuedBytes -= queue[0].pending.reduce(0) { $0 + $1.count }
                queue[0].pending = []
            }
            let head = queue[0]
            guard head.lastReceived, let started = head.startedAt, now >= started + head.durationMs else { return }
            queue.removeFirst()
            send(playback(head.utteranceID, "ended"))
        }
    }

    private func onStopPlayback(_ header: [String: JSONValue]) {
        let utteranceID = header["utterance_id"]?.stringValue ?? ""
        let queued = queue.contains { $0.utteranceID == utteranceID }
        guard utterances[utteranceID] != nil || queued else {
            droppedStop += 1
            return
        }
        utterances.removeValue(forKey: utteranceID)
        if let index = queue.firstIndex(where: { $0.utteranceID == utteranceID }) {
            let entry = queue.remove(at: index)
            queuedBytes -= entry.pending.reduce(0) { $0 + $1.count }
            if entry.startedAt != nil { effects.append(.playbackStopped(utteranceID)) }
        } else {
            effects.append(.playbackStopped(utteranceID))  // unpaced: whatever of it still sounds
        }
        stoppedAt.append((utteranceID, clock.monotonicMs()))
        closed.insert(utteranceID)
        send(playback(utteranceID, "stopped"))
    }

    // MARK: Local events (the node UI, the hotkey, the node WebSocket)

    public func setLink(_ up: Bool) {
        linkUp = up
        if !up {
            release("link_down")
            indicator = "off"
        }
        send(["type": "link", "state": .string(up ? "up" : "down")])
    }

    public func setMute(_ value: Muted) {
        muted = value
        if value != .no {
            release(value == .unreadable ? "mute_unreadable" : "muted")
            indicator = "muted"
        }
        send(["type": "mute", "muted": value.json])
    }

    public func setMode(_ value: String) {
        mode = value
        if value == "off" {
            release("mode_off")
            indicator = "off"
        }
        send(["type": "mode", "mode": .string(value)])
    }

    public func ptt(_ key: String) {
        if childReady { send(["type": "ptt", "key": .string(key)]) }
    }

    /// One captured 20 ms frame: reaches the child only while the mic is
    /// held.
    @discardableResult
    public func feedAudio(_ pcm: Data, aec: String = "on") -> Bool {
        guard micHeld, childReady, linkOpen else { return false }
        let sent = send(
            ["type": "audio", "seq": .int(seq), "captured_at_ms": .int(clock.nowMs()), "aec": .string(aec)], pcm)
        if sent {
            seq = (seq + 1) % 4_294_967_296
            audioSent += 1
        }
        return sent
    }

    // MARK: Time

    public func tick() {
        let now = clock.monotonicMs()
        if childRunning, let last = lastStateAt, now - last >= Self.watchdogMs {
            fail("watchdog")
            return
        }
        if pacedPlayback && linkOpen { advancePlayback() }
        if childReady, let readyAt, now - readyAt >= Self.backoffResetAfterMs {
            backoffMs = Self.backoffStartMs
        }
    }
}
