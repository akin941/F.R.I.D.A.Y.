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
//   Sound that arrives while it is not (an earcon after a release) goes to a
//   second, output-only engine that never touches the input.
//
// Tests never open the real microphone: everything above `VoiceAudioIO` is
// driven with a fake (VoiceHostIntegrationTests); `AVAudioVoiceEngine` is
// only built by the app.

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

    public init() {}

    public func startCapture() throws {
        lock.lock()
        defer { lock.unlock() }
        guard engine == nil else { return }
        let engine = AVAudioEngine()
        let input = engine.inputNode
        do {
            try input.setVoiceProcessingEnabled(true)
            aec = "on"
        } catch {
            aec = "off"
        }
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
        framer.reset()
        input.installTap(onBus: 0, bufferSize: 1024, format: inputFormat) { [weak self] buffer, _ in
            self?.captured(buffer, converter)
        }
        engine.prepare()
        try engine.start()
        player.play()
        earcons.play()
        self.engine = engine
        self.player = player
        self.earconPlayer = earcons
        stopOutputEngine()
    }

    private func captured(_ buffer: AVAudioPCMBuffer, _ converter: AVAudioConverter) {
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
        let frames = framer.append(data)
        let aec = self.aec
        let handler = onCapture
        lock.unlock()
        for frame in frames { handler?(frame, aec) }
    }

    public func stopCapture() {
        lock.lock()
        defer { lock.unlock() }
        guard let engine else { return }
        engine.inputNode.removeTap(onBus: 0)
        player?.stop()
        earconPlayer?.stop()
        engine.stop()
        self.engine = nil
        player = nil
        earconPlayer = nil
        current = nil
        framer.reset()
    }

    public func beginPlayback(_ utteranceID: String) {
        lock.lock()
        current = utteranceID
        lock.unlock()
    }

    public func enqueue(_ utteranceID: String, pcm: Data) {
        lock.lock()
        defer { lock.unlock() }
        guard current == utteranceID, let buffer = PCMConvert.floatBuffer(pcm) else { return }
        if let player {
            player.scheduleBuffer(buffer)
        } else {
            outputNode()?.scheduleBuffer(buffer)
        }
    }

    public func stopPlayback(_ utteranceID: String) {
        lock.lock()
        defer { lock.unlock() }
        guard current == utteranceID else { return }
        current = nil
        // stop() drops every scheduled buffer at once; play() re-arms the node.
        player?.stop()
        player?.play()
        outputPlayer?.stop()
        outputPlayer?.play()
    }

    public func playEarcon(_ name: String) {
        lock.lock()
        defer { lock.unlock() }
        guard let pcm = Earcons.pcm(name), let buffer = PCMConvert.floatBuffer(pcm) else { return }
        if let earconPlayer {
            earconPlayer.scheduleBuffer(buffer)
        } else {
            outputNode()?.scheduleBuffer(buffer)
        }
    }

    public func shutdown() {
        stopCapture()
        lock.lock()
        stopOutputEngine()
        lock.unlock()
    }

    /// Under the lock: the output-only engine (no input node is touched).
    private func outputNode() -> AVAudioPlayerNode? {
        if let outputPlayer { return outputPlayer }
        let engine = AVAudioEngine()
        let player = AVAudioPlayerNode()
        engine.attach(player)
        engine.connect(player, to: engine.mainMixerNode, format: PCMConvert.playFormat)
        engine.prepare()
        guard (try? engine.start()) != nil else { return nil }
        player.play()
        outputEngine = engine
        outputPlayer = player
        return player
    }

    private func stopOutputEngine() {
        outputPlayer?.stop()
        outputEngine?.stop()
        outputPlayer = nil
        outputEngine = nil
    }
}

public enum VoiceAudioError: Error, Equatable {
    case noInput
}
