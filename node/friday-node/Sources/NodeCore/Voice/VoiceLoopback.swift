// `friday-node voice-loopback` (Phase 11 brief M3, code half; runbook B8;
// docs/voice/live-eval.md §4): the host's real audio path for the nightly
// self-trigger test, as the `CommandLoopback` of
// node/friday-voice/friday_voice/selftest.py runs it.
//
// - Input: one PCM WAV (16 kHz, mono, 16-bit: what `encode_wav` writes),
//   at most `maxClipSeconds` long. Anything else is refused before any audio
//   device is touched.
// - The clip is played through a `VoiceAudioIO` (the app: B7's
//   `AVAudioVoiceEngine`, the same voice-processing graph the voice host
//   uses, so Apple's echo canceller has the clip as its reference) while
//   the AEC-cleaned microphone signal is captured from the moment capture
//   starts, for the clip's length plus `tailMilliseconds` (1 s).
// - Output: that capture as a 16 kHz mono PCM16 WAV (in memory).
// - Fail closed with a stable code (`VoiceSelftestCode`): no Microphone TCC
//   grant (never asked for here: nobody is there to answer at 03:17), the
//   mic cannot be opened, voice processing is off (a capture without AEC
//   measures the wrong thing), or the capture does not arrive in time.
// - The mic is released on every path (`stopCapture`, `shutdown`); nothing
//   is written to disk: the clip and the capture only ever live in memory.

import AVFoundation
import Foundation

/// Every stable code the loopback, the token hand-over and the app's
/// selftest socket report (stderr: `friday-node <command>: error <code>`;
/// the socket: `{"ok": false, "error": "<code>"}`). No audio, token or path
/// is ever part of one.
public enum VoiceSelftestCode: String, Error, CaseIterable, Sendable {
    // 2: usage.
    case usage
    // 3: the input clip.
    case wavInvalid = "wav_invalid"
    case wavUnsupported = "wav_unsupported"
    case wavTooLong = "wav_too_long"
    // 4: audio or TCC.
    case tccNotGranted = "tcc_not_granted"
    case micUnavailable = "mic_unavailable"
    case micBusy = "mic_busy"
    case aecUnavailable = "aec_unavailable"
    case captureTimeout = "capture_timeout"
    // 5: the app (its socket, its gates, the mint).
    case appUnreachable = "app_unreachable"
    case protocolError = "protocol"
    case selftestDisabled = "selftest_disabled"
    case busy
    case noSelftestHold = "no_selftest_hold"
    case notPaired = "not_paired"
    case killSwitch = "kill_switch"
    case credentialRefused = "credential_refused"
    case mintFailed = "mint_failed"
    // 6: the token file.
    case tokenFileUnsafe = "token_file_unsafe"

    public var exitStatus: Int32 {
        switch self {
        case .usage: return 2
        case .wavInvalid, .wavUnsupported, .wavTooLong: return 3
        case .tccNotGranted, .micUnavailable, .micBusy, .aecUnavailable, .captureTimeout: return 4
        case .appUnreachable, .protocolError, .selftestDisabled, .busy, .noSelftestHold, .notPaired, .killSwitch,
            .credentialRefused, .mintFailed:
            return 5
        case .tokenFileUnsafe: return 6
        }
    }
}

/// The loopback's WAV rules: one RIFF/WAVE file in, one out, in memory.
public enum LoopbackWAV {
    /// 16 kHz mono PCM16 bytes of a WAV, or a stable code.
    public static func decode(_ data: Data) throws -> Data {
        let bytes = [UInt8](data)
        guard bytes.count >= 12, bytes[0..<4] == [0x52, 0x49, 0x46, 0x46], bytes[8..<12] == [0x57, 0x41, 0x56, 0x45]
        else { throw VoiceSelftestCode.wavInvalid }
        var offset = 12
        var format: (tag: UInt16, channels: UInt16, rate: UInt32, bits: UInt16)?
        while offset + 8 <= bytes.count {
            let id = bytes[offset..<offset + 4]
            let size = Int(le32(bytes, offset + 4))
            let body = offset + 8
            guard size <= bytes.count - body else { throw VoiceSelftestCode.wavInvalid }  // truncated
            if id == [0x66, 0x6D, 0x74, 0x20] {  // "fmt "
                guard size >= 16 else { throw VoiceSelftestCode.wavInvalid }
                var tag = le16(bytes, body)
                if tag == 0xFFFE {  // WAVE_FORMAT_EXTENSIBLE: the sub-format's tag
                    guard size >= 40 else { throw VoiceSelftestCode.wavInvalid }
                    tag = le16(bytes, body + 24)
                }
                format = (tag, le16(bytes, body + 2), le32(bytes, body + 4), le16(bytes, body + 14))
            } else if id == [0x64, 0x61, 0x74, 0x61] {  // "data"
                guard let format else { throw VoiceSelftestCode.wavInvalid }  // data before fmt
                guard format.tag == 1, format.channels == 1, format.rate == UInt32(VoiceProtocol.sampleRate),
                    format.bits == 16
                else { throw VoiceSelftestCode.wavUnsupported }
                let even = size - size % 2
                guard even <= VoiceLoopback.maxClipBytes else { throw VoiceSelftestCode.wavTooLong }
                return Data(bytes[body..<body + even])
            }
            offset = body + size + size % 2  // chunks are word-aligned
        }
        throw VoiceSelftestCode.wavInvalid
    }

    /// 16 kHz mono PCM16 -> a canonical 44-byte-header WAV.
    public static func encode(_ pcm: Data) -> Data {
        var out = Data()
        func ascii(_ text: String) { out.append(contentsOf: Array(text.utf8)) }
        func u32(_ value: UInt32) { withUnsafeBytes(of: value.littleEndian) { out.append(contentsOf: $0) } }
        func u16(_ value: UInt16) { withUnsafeBytes(of: value.littleEndian) { out.append(contentsOf: $0) } }
        let rate = UInt32(VoiceProtocol.sampleRate)
        ascii("RIFF")
        u32(UInt32(36 + pcm.count))
        ascii("WAVE")
        ascii("fmt ")
        u32(16)
        u16(1)  // PCM
        u16(1)  // mono
        u32(rate)
        u32(rate * 2)  // byte rate
        u16(2)  // block align
        u16(16)  // bits
        ascii("data")
        u32(UInt32(pcm.count))
        out.append(pcm)
        return out
    }

    private static func le16(_ bytes: [UInt8], _ at: Int) -> UInt16 {
        UInt16(bytes[at]) | UInt16(bytes[at + 1]) << 8
    }

    private static func le32(_ bytes: [UInt8], _ at: Int) -> UInt32 {
        UInt32(bytes[at]) | UInt32(bytes[at + 1]) << 8 | UInt32(bytes[at + 2]) << 16 | UInt32(bytes[at + 3]) << 24
    }
}

public enum VoiceLoopback {
    /// The capture runs this much past the clip (live-eval.md §4).
    public static let tailMilliseconds = 1000
    public static let maxClipSeconds = 60
    public static let bytesPerSecond = VoiceProtocol.sampleRate * 2
    public static let maxClipBytes = maxClipSeconds * bytesPerSecond
    /// The most the CLI reads from stdin (the clip plus any WAV header).
    public static let maxInputBytes = maxClipBytes + 64 * 1024
    /// How long past the capture's own length to wait before giving up.
    public static let defaultSlackSeconds = 10.0
    static let utteranceID = "loopback"

    /// The capture's length for a clip of `clipBytes` PCM16 bytes.
    public static func captureBytes(forClip clipBytes: Int) -> Int {
        clipBytes - clipBytes % 2 + tailMilliseconds * bytesPerSecond / 1000
    }

    /// Play the WAV `wav` through `audio` and return the AEC-cleaned capture
    /// as a WAV. `micAuthorized` is the Microphone TCC state (checked first,
    /// never requested). The mic is released on every path.
    public static func run(
        wav: Data, audio: VoiceAudioIO, micAuthorized: () -> Bool, slackSeconds: Double = defaultSlackSeconds
    ) throws -> Data {
        let clip = try LoopbackWAV.decode(wav)
        guard micAuthorized() else { throw VoiceSelftestCode.tccNotGranted }
        let target = captureBytes(forClip: clip.count)
        let collector = CaptureCollector(target: target)
        audio.onCapture = { frame, aec in collector.add(frame, aec: aec) }
        defer {
            audio.onCapture = nil
            audio.stopPlayback(utteranceID)
            audio.stopCapture()
            audio.shutdown()
        }
        do {
            try audio.startCapture()
        } catch {
            throw VoiceSelftestCode.micUnavailable
        }
        audio.beginPlayback(utteranceID)
        if !clip.isEmpty { audio.enqueue(utteranceID, pcm: clip) }
        let seconds = Double(target) / Double(bytesPerSecond) + slackSeconds
        guard collector.wait(seconds: seconds) else { throw VoiceSelftestCode.captureTimeout }
        guard collector.aecOn else { throw VoiceSelftestCode.aecUnavailable }
        return LoopbackWAV.encode(collector.capture)
    }

    /// The Microphone TCC grant of this process (its responsible process:
    /// see the README "Nightly self-trigger"). `.notDetermined` is a refusal:
    /// the loopback never asks.
    public static func microphoneAuthorized() -> Bool {
        AVCaptureDevice.authorizationStatus(for: .audio) == .authorized
    }
}

/// Collects captured frames up to `target` bytes (any thread).
final class CaptureCollector: @unchecked Sendable {
    private let lock = NSLock()
    private let done = DispatchSemaphore(value: 0)
    private let target: Int
    private var buffer = Data()
    private var finished = false
    private var aecOff = false

    init(target: Int) {
        self.target = target
        if target == 0 { finish() }
    }

    func add(_ frame: Data, aec: String) {
        lock.lock()
        guard !finished else {
            lock.unlock()
            return
        }
        if aec != "on" { aecOff = true }
        buffer.append(frame.prefix(target - buffer.count))
        let full = buffer.count >= target || aecOff
        lock.unlock()
        if full { finish() }
    }

    private func finish() {
        lock.lock()
        let first = !finished
        finished = true
        lock.unlock()
        if first { done.signal() }
    }

    func wait(seconds: Double) -> Bool {
        done.wait(timeout: .now() + seconds) == .success
    }

    var aecOn: Bool {
        lock.lock()
        defer { lock.unlock() }
        return !aecOff
    }

    var capture: Data {
        lock.lock()
        defer { lock.unlock() }
        return buffer
    }
}

extension ReleaseProbe {
    /// True only in DEBUG builds, which carry the loopback's echo engine.
    public static var voiceLoopbackEchoCompiledIn: Bool {
        #if DEBUG
        return true
        #else
        return false
        #endif
    }
}

#if DEBUG
/// A zero-latency speaker -> mic path for the CLI's DEBUG-only
/// `--debug-echo-engine` (the `CommandLoopback` contract run without a real
/// microphone or speaker; check-release-markers.sh refuses a Release binary
/// that names it). Capture starts with the clip: the first frame waits until
/// the clip was enqueued (or 200 ms passed, for an empty clip), then the
/// clip is echoed frame by frame, then silence.
public final class EchoVoiceEngine: VoiceAudioIO, @unchecked Sendable {
    public var onCapture: ((Data, String) -> Void)?
    private let lock = NSLock()
    private let enqueued = DispatchSemaphore(value: 0)
    private var played = Data()
    private var running = false
    public private(set) var captureStarts = 0
    public private(set) var captureStops = 0
    public private(set) var shutdowns = 0

    public init() {}

    public func startCapture() throws {
        lock.lock()
        captureStarts += 1
        running = true
        lock.unlock()
        Thread.detachNewThread { [weak self] in self?.pump() }
    }

    private func pump() {
        _ = enqueued.wait(timeout: .now() + 0.2)
        var framer = PCMFramer()
        while true {
            lock.lock()
            guard running else {
                lock.unlock()
                return
            }
            let chunk = played.prefix(PCMFramer.frameBytes)
            played.removeFirst(chunk.count)
            let handler = onCapture
            lock.unlock()
            var frame = Data(chunk)
            frame.append(Data(count: PCMFramer.frameBytes - frame.count))
            for out in framer.append(frame) { handler?(out, "on") }
            usleep(200)
        }
    }

    public func stopCapture() {
        lock.lock()
        captureStops += 1
        running = false
        lock.unlock()
    }

    public func beginPlayback(_ utteranceID: String) {}

    public func enqueue(_ utteranceID: String, pcm: Data) {
        lock.lock()
        played.append(pcm)
        lock.unlock()
        enqueued.signal()
    }

    public func stopPlayback(_ utteranceID: String) {}
    public func playEarcon(_ name: String) {}

    public func shutdown() {
        lock.lock()
        shutdowns += 1
        running = false
        lock.unlock()
    }
}
#endif
