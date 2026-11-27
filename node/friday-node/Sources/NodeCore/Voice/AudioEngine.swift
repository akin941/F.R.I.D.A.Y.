// The voice host's audio (Phase 11 brief D6, D9; Mac session M1; runbook
// B7): capture and playback through one AVAudioEngine with Apple's voice
// processing on, so what Friday says is the echo canceller's reference.
//
// - Capture: `inputNode.setVoiceProcessingEnabled(true)`, a tap on the
//   input converted to 16 kHz mono PCM16 little-endian and cut into 20 ms
//   (640-byte) frames (`PCMFramer`), handed to the host, which forwards
//   them to the child only while the mic is held (VoiceHostCore). `aec` is
//   `on` when voice processing could be enabled, else `off`.
// - Playback: the PCM of `play` frames, scheduled on a player node of the
//   same engine; the pacing (`started` / `ended` / `stopped`) is the host
//   core's (`HostCore(paced_playback=True)` exactly); a stop calls
//   `AVAudioPlayerNode.stop()`, which drops what is scheduled at once
//   (well inside the 100 ms budget).
// - Earcons: a fixed set the host owns (`wake`, `end`, `error`), synthesised
//   here (no file, nothing downloaded).
// - The engine (and so the microphone) runs only while the mic is held.
//   Releasing the mic stops it at once, so a reply still playing is cut off
//   with it (a release is a fail-closed input; the host core's pacing goes
//   on and reports `ended` on its clock). Sound that arrives while the mic
//   is not held (an earcon after a release) goes to a second, output-only
//   engine that never touches the input.
//
// Tests never open the real microphone: everything above `VoiceAudioIO` is
// driven with a fake (VoiceServiceTests); `AVAudioVoiceEngine` is only built
// by the app.

import AVFoundation
import Foundation

public protocol VoiceAudioIO: AnyObject {
    /// Captured 20 ms frames (640 bytes, 16 kHz mono PCM16 LE) and the AEC
    /// state, on any thread.
    var onCapture: ((Data, String) -> Void)? { get set }
    /// Open the mic (the host holds it). Throws when the engine cannot start
    /// (no TCC grant, no input device).
    func startCapture() throws
    /// Release the mic.
    func stopCapture()
    func beginPlayback(_ utteranceID: String)
    func enqueue(_ utteranceID: String, pcm: Data)
    func stopPlayback(_ utteranceID: String)
    func playEarcon(_ name: String)
    /// Stop everything (quit, kill switch).
    func shutdown()
}

/// Cuts a PCM16 stream into exact 20 ms frames.
public struct PCMFramer {
    public static let frameBytes = VoiceHostCore.frameBytes
    private var buffer = Data()

    public init() {}

    public mutating func append(_ pcm: Data) -> [Data] {
        buffer.append(pcm)
        var frames: [Data] = []
        while buffer.count >= Self.frameBytes {
            frames.append(Data(buffer.prefix(Self.frameBytes)))
            buffer.removeFirst(Self.frameBytes)
        }
        return frames
    }

    public mutating func reset() { buffer = Data() }
    public var buffered: Int { buffer.count }
}

/// Which capture session may still deliver audio (guarded by the engine's
/// lock): a tap callback of a session that was stopped is dropped, so audio
/// captured before a release can never reach the child after it.
public struct CaptureGate: Equatable {
    private var issued = 0
    private var live = 0

    public init() {}

    /// A new session id (not yet admitted).
    public mutating func next() -> Int {
        issued += 1
        return issued
    }

    public mutating func open(_ session: Int) { live = session }

    public mutating func close(_ session: Int) { if live == session { live = 0 } }

    public mutating func closeAll() { live = 0 }

    public func admits(_ session: Int) -> Bool { session != 0 && live == session }
}

/// The host's fixed earcon set: short sine tones, 16 kHz mono PCM16 LE.
public enum Earcons {
    public static let names = VoiceProtocol.earcons

    /// (frequency Hz, duration ms) segments.
    static let tones: [String: [(Double, Int)]] = [
        "wake": [(660, 90), (880, 110)],
        "end": [(880, 90), (660, 110)],
        "error": [(330, 140), (0, 60), (330, 140)],
    ]

    public static func pcm(_ name: String) -> Data? {
        guard let segments = tones[name] else { return nil }
        var out = Data()
        for (frequency, ms) in segments {
            let count = VoiceProtocol.sampleRate * ms / 1000
            for index in 0..<count {
                // A 5 ms ramp at both ends: no click.
                let ramp = min(1.0, Double(min(index, count - 1 - index)) / 80.0)
                let value = frequency == 0 ? 0 : sin(2 * .pi * frequency * Double(index) / 16_000) * 0.25 * ramp
                let sample = Int16(clamping: Int(value * Double(Int16.max)))
                withUnsafeBytes(of: sample.littleEndian) { out.append(contentsOf: $0) }
            }
        }
        return out
    }
}

enum PCMConvert {
    static let playFormat = AVAudioFormat(
        commonFormat: .pcmFormatFloat32, sampleRate: Double(VoiceProtocol.sampleRate), channels: 1, interleaved: false)!
    static let captureFormat = AVAudioFormat(
        commonFormat: .pcmFormatInt16, sampleRate: Double(VoiceProtocol.sampleRate), channels: 1, interleaved: true)!

    /// PCM16 LE bytes -> a float buffer at 16 kHz mono.
    static func floatBuffer(_ pcm: Data) -> AVAudioPCMBuffer? {
        let frames = pcm.count / 2
        guard frames > 0, let buffer = AVAudioPCMBuffer(pcmFormat: playFormat, frameCapacity: AVAudioFrameCount(frames)),
            let channel = buffer.floatChannelData?[0]
        else { return nil }
        pcm.withUnsafeBytes { raw in
            for index in 0..<frames {
                let sample = Int16(littleEndian: raw.loadUnaligned(fromByteOffset: index * 2, as: Int16.self))
                channel[index] = Float(sample) / 32768
            }
        }
        buffer.frameLength = AVAudioFrameCount(frames)
        return buffer
    }
}

/// The real engine (the app only).
///
/// Lock discipline (B7 review): `lock` guards this object's own state only.
/// No AVFoundation call that may wait for an audio thread (`installTap`,
/// `removeTap`, `start`, `stop`, `play`, `scheduleBuffer`) runs while it is
/// held, because the tap callback takes it: state is swapped under the lock,
/// the engine is driven after it is released. Each capture session has a
/// generation; a tap callback of an earlier generation (a late buffer after
/// `stopCapture`) drops its audio and never reaches `onCapture`.
public final class AVAudioVoiceEngine: VoiceAudioIO, @unchecked Sendable {
    public var onCapture: ((Data, String) -> Void)?
    private let lock = NSLock()
    private var engine: AVAudioEngine?
    private var player: AVAudioPlayerNode?
    private var earconPlayer: AVAudioPlayerNode?
    private var outputEngine: AVAudioEngine?
    private var outputPlayer: AVAudioPlayerNode?
    private var current: String?
    private var aec = "off"
    private var framer = PCMFramer()
    /// The capture session the tap belongs to; 0 while none runs.
    private var gate = CaptureGate()

    public init() {}

    public func startCapture() throws {
        lock.lock()
        let running = engine != nil
        let session = gate.next()
        lock.unlock()
        guard !running else { return }
        let engine = AVAudioEngine()
        let input = engine.inputNode
        let aec = (try? input.setVoiceProcessingEnabled(true)) != nil ? "on" : "off"
        let player = AVAudioPlayerNode()
        let earcons = AVAudioPlayerNode()
        engine.attach(player)
        engine.attach(earcons)
        engine.connect(player, to: engine.mainMixerNode, format: PCMConvert.playFormat)
        engine.connect(earcons, to: engine.mainMixerNode, format: PCMConvert.playFormat)
        let inputFormat = input.outputFormat(forBus: 0)
        guard inputFormat.sampleRate > 0, let converter = AVAudioConverter(from: inputFormat, to: PCMConvert.captureFormat)
        else { throw VoiceAudioError.noInput }
        converter.downmix = true
        lock.lock()
        framer.reset()
        self.aec = aec
        gate.open(session)  // the tap may deliver from here on
        lock.unlock()
        input.installTap(onBus: 0, bufferSize: 1024, format: inputFormat) { [weak self] buffer, _ in
            self?.captured(buffer, converter, session: session)
        }
        engine.prepare()
        do {
            try engine.start()
        } catch {
            input.removeTap(onBus: 0)
            lock.lock()
            gate.close(session)
            lock.unlock()
            throw error
        }
        player.play()
        earcons.play()
        lock.lock()
        self.engine = engine
        self.player = player
        self.earconPlayer = earcons
        let output = (outputEngine, outputPlayer)
        outputEngine = nil
        outputPlayer = nil
        lock.unlock()
        output.1?.stop()
        output.0?.stop()
    }

    private func captured(_ buffer: AVAudioPCMBuffer, _ converter: AVAudioConverter, session: Int) {
        lock.lock()
        let live = gate.admits(session)
        lock.unlock()
        guard live else { return }  // a late buffer of a stopped capture
        let ratio = PCMConvert.captureFormat.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio + 32)
        guard let out = AVAudioPCMBuffer(pcmFormat: PCMConvert.captureFormat, frameCapacity: capacity) else { return }
        var given = false
        var error: NSError?
        converter.convert(to: out, error: &error) { _, status in
            if given {
                status.pointee = .noDataNow
                return nil
            }
            given = true
            status.pointee = .haveData
            return buffer
        }
        guard error == nil, let samples = out.int16ChannelData?[0], out.frameLength > 0 else { return }
        let data = Data(bytes: samples, count: Int(out.frameLength) * 2)
        lock.lock()
        // Checked again: the capture may have stopped while converting.
        guard gate.admits(session) else {
            lock.unlock()
            return
        }
        let frames = framer.append(data)
        let aec = self.aec
        let handler = onCapture
        lock.unlock()
        for frame in frames { handler?(frame, aec) }
    }

    /// Releasing the mic stops the engine at once, and with it whatever of
    /// a reply still plays on it (the safer reading of a release).
    public func stopCapture() {
        lock.lock()
        let stopped = (engine, player, earconPlayer)
        engine = nil
        player = nil
        earconPlayer = nil
        current = nil
        gate.closeAll()  // late tap callbacks drop their audio from here on
        framer.reset()
        lock.unlock()
        guard let engine = stopped.0 else { return }
        engine.inputNode.removeTap(onBus: 0)
        stopped.1?.stop()
        stopped.2?.stop()
        engine.stop()
    }

    public func beginPlayback(_ utteranceID: String) {
        lock.lock()
        current = utteranceID
        lock.unlock()
    }

    public func enqueue(_ utteranceID: String, pcm: Data) {
        lock.lock()
        let target = current == utteranceID ? (player ?? outputPlayer) : nil
        let needsOutput = current == utteranceID && player == nil && outputPlayer == nil
        lock.unlock()
        guard let buffer = PCMConvert.floatBuffer(pcm) else { return }
        if let target {
            target.scheduleBuffer(buffer)
        } else if needsOutput {
            outputNode()?.scheduleBuffer(buffer)
        }
    }

    public func stopPlayback(_ utteranceID: String) {
        lock.lock()
        guard current == utteranceID else {
            lock.unlock()
            return
        }
        current = nil
        let nodes = [player, outputPlayer].compactMap { $0 }
        lock.unlock()
        // stop() drops every scheduled buffer at once; play() re-arms the node.
        for node in nodes {
            node.stop()
            node.play()
        }
    }

    public func playEarcon(_ name: String) {
        guard let pcm = Earcons.pcm(name), let buffer = PCMConvert.floatBuffer(pcm) else { return }
        lock.lock()
        let target = earconPlayer ?? outputPlayer
        lock.unlock()
        (target ?? outputNode())?.scheduleBuffer(buffer)
    }

    public func shutdown() {
        stopCapture()
        lock.lock()
        let output = (outputEngine, outputPlayer)
        outputEngine = nil
        outputPlayer = nil
        lock.unlock()
        output.1?.stop()
        output.0?.stop()
    }

    /// The output-only engine (no input node is touched), made outside the
    /// lock and adopted under it (a concurrent maker's engine is dropped).
    private func outputNode() -> AVAudioPlayerNode? {
        lock.lock()
        if let outputPlayer {
            lock.unlock()
            return outputPlayer
        }
        lock.unlock()
        let engine = AVAudioEngine()
        let player = AVAudioPlayerNode()
        engine.attach(player)
        engine.connect(player, to: engine.mainMixerNode, format: PCMConvert.playFormat)
        engine.prepare()
        guard (try? engine.start()) != nil else { return nil }
        player.play()
        lock.lock()
        if let existing = outputPlayer {
            lock.unlock()
            player.stop()
            engine.stop()
            return existing
        }
        outputEngine = engine
        outputPlayer = player
        lock.unlock()
        return player
    }
}

public enum VoiceAudioError: Error, Equatable {
    case noInput
}
