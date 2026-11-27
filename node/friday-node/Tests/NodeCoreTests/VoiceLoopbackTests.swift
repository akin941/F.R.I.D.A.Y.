// `friday-node voice-loopback`, the token hand-over and the nightly script
// (Phase 11 brief M3, code half; runbook B8). No real microphone or speaker
// is ever opened: the loopback runs against fakes (and, in DEBUG, the CLI's
// echo engine), the app's side against a fake hold and a scripted mint.

import Darwin
import Foundation
import NodeCore
import XCTest

/// Delivers `frames` synchronously from `startCapture` (deterministic) and
/// records every call.
final class LoopFake: VoiceAudioIO, @unchecked Sendable {
    var onCapture: ((Data, String) -> Void)?
    var frames: [Data] = []
    var aec = "on"
    var failStart = false
    /// Seconds `startCapture` takes (a loopback that is still running).
    var startDelay = 0.0
    private(set) var calls: [String] = []

    func startCapture() throws {
        calls.append("startCapture")
        if failStart { throw VoiceAudioError.noInput }
        if startDelay > 0 { usleep(UInt32(startDelay * 1_000_000)) }
        for frame in frames { onCapture?(frame, aec) }
    }
    func stopCapture() { calls.append("stopCapture") }
    func beginPlayback(_ utteranceID: String) { calls.append("begin") }
    func enqueue(_ utteranceID: String, pcm: Data) { calls.append("enqueue:\(pcm.count)") }
    func stopPlayback(_ utteranceID: String) { calls.append("stopPlayback") }
    func playEarcon(_ name: String) { calls.append("earcon") }
    func shutdown() { calls.append("shutdown") }

    var released: Bool { calls.contains("stopCapture") && calls.contains("shutdown") }
}

enum WAVBytes {
    /// A WAV like Python's `wave` writes, with any format fields.
    static func make(
        _ pcm: Data, tag: UInt16 = 1, channels: UInt16 = 1, rate: UInt32 = 16_000, bits: UInt16 = 16,
        extraChunk: Data? = nil
    ) -> Data {
        var out = Data("RIFF".utf8)
        func u32(_ v: UInt32) { withUnsafeBytes(of: v.littleEndian) { out.append(contentsOf: $0) } }
        func u16(_ v: UInt16) { withUnsafeBytes(of: v.littleEndian) { out.append(contentsOf: $0) } }
        u32(0)  // patched below
        out.append(Data("WAVE".utf8))
        out.append(Data("fmt ".utf8))
        u32(16)
        u16(tag)
        u16(channels)
        u32(rate)
        u32(rate * UInt32(channels) * UInt32(bits / 8))
        u16(channels * bits / 8)
        u16(bits)
        if let extraChunk { out.append(extraChunk) }
        out.append(Data("data".utf8))
        u32(UInt32(pcm.count))
        out.append(pcm)
        let size = UInt32(out.count - 8).littleEndian
        withUnsafeBytes(of: size) { out.replaceSubrange(4..<8, with: $0) }
        return out
    }

    static func pattern(_ bytes: Int) -> Data { Data((0..<bytes).map { UInt8(truncatingIfNeeded: $0 * 7 + 3) }) }
}

final class LoopbackWAVTests: XCTestCase {
    func testA16kMonoPCM16WavDecodesToItsSamples() throws {
        let pcm = WAVBytes.pattern(3200)
        XCTAssertEqual(try LoopbackWAV.decode(WAVBytes.make(pcm)), pcm)
        // A chunk before `data` (LIST, odd size: padded) is skipped.
        var list = Data("LIST".utf8)
        list.append(contentsOf: [3, 0, 0, 0, 0x61, 0x62, 0x63, 0])
        XCTAssertEqual(try LoopbackWAV.decode(WAVBytes.make(pcm, extraChunk: list)), pcm)
        // An odd byte count drops the half sample.
        XCTAssertEqual(try LoopbackWAV.decode(WAVBytes.make(WAVBytes.pattern(3201))).count, 3200)
        XCTAssertEqual(try LoopbackWAV.decode(WAVBytes.make(Data())), Data())
    }

    func testTheEncoderWritesTheCanonicalHeaderAndRoundTrips() throws {
        let pcm = WAVBytes.pattern(640)
        let wav = LoopbackWAV.encode(pcm)
        XCTAssertEqual(wav, WAVBytes.make(pcm), "byte for byte what Python's wave module writes")
        XCTAssertEqual(wav.count, 44 + 640)
        XCTAssertEqual(try LoopbackWAV.decode(wav), pcm)
    }

    func testAnythingElseIsRefusedWithAStableCode() {
        let pcm = WAVBytes.pattern(640)
        let cases: [(Data, VoiceSelftestCode)] = [
            (Data(), .wavInvalid),
            (Data("not a wav at all".utf8), .wavInvalid),
            (WAVBytes.make(pcm).prefix(60), .wavInvalid),  // truncated data chunk
            (Data(WAVBytes.make(pcm).prefix(36)), .wavInvalid),  // no data chunk
            (WAVBytes.make(pcm, channels: 2), .wavUnsupported),
            (WAVBytes.make(pcm, rate: 44_100), .wavUnsupported),
            (WAVBytes.make(pcm, bits: 8), .wavUnsupported),
            (WAVBytes.make(pcm, tag: 3, bits: 32), .wavUnsupported),  // float
            (WAVBytes.make(Data(count: VoiceLoopback.maxClipBytes + 2)), .wavTooLong),
        ]
        for (index, (wav, code)) in cases.enumerated() {
            XCTAssertThrowsError(try LoopbackWAV.decode(wav), "case \(index)") {
                XCTAssertEqual($0 as? VoiceSelftestCode, code, "case \(index)")
            }
        }
        // `data` before `fmt `.
        var early = Data("RIFF".utf8) + Data([0, 0, 0, 0]) + Data("WAVE".utf8) + Data("data".utf8)
        early.append(contentsOf: [0, 0, 0, 0])
        XCTAssertThrowsError(try LoopbackWAV.decode(early)) { XCTAssertEqual($0 as? VoiceSelftestCode, .wavInvalid) }
    }

    func testEveryCodeHasAStableExitStatus() {
        XCTAssertEqual(VoiceSelftestCode.usage.exitStatus, 2)
        XCTAssertEqual(VoiceSelftestCode.wavInvalid.exitStatus, 3)
        XCTAssertEqual(VoiceSelftestCode.tccNotGranted.exitStatus, 4)
        XCTAssertEqual(VoiceSelftestCode.captureTimeout.exitStatus, 4)
        XCTAssertEqual(VoiceSelftestCode.appUnreachable.exitStatus, 5)
        XCTAssertEqual(VoiceSelftestCode.tokenFileUnsafe.exitStatus, 6)
        for code in VoiceSelftestCode.allCases {
            XCTAssertNotEqual(code.exitStatus, 0, code.rawValue)
            XCTAssertTrue(code.rawValue.count <= 32 && code.rawValue.allSatisfy { $0 == "_" || ("a"..."z").contains($0) }, code.rawValue)
        }
    }
}

final class VoiceLoopbackTests: XCTestCase {
    static let second = VoiceLoopback.bytesPerSecond

    func frames(_ pcm: Data) -> [Data] {
        var framer = PCMFramer()
        return framer.append(pcm)
    }

    /// The length rule: the clip plus 1 s, exactly, whatever the engine
    /// delivers beyond it; the clip is played on the voice-processing graph.
    func testTheCaptureIsTheClipPlusOneSecond() throws {
        let clip = WAVBytes.pattern(6400)  // 200 ms
        let fake = LoopFake()
        let delivered = WAVBytes.pattern(6400 + Self.second + 6400)
        fake.frames = frames(delivered)
        let out = try VoiceLoopback.run(wav: WAVBytes.make(clip), audio: fake, micAuthorized: { true })
        let capture = try LoopbackWAV.decode(out)
        XCTAssertEqual(capture.count, 6400 + Self.second)
        XCTAssertEqual(capture, delivered.prefix(6400 + Self.second))
        XCTAssertEqual(VoiceLoopback.captureBytes(forClip: 6400), 6400 + Self.second)
        XCTAssertEqual(VoiceLoopback.captureBytes(forClip: 0), Self.second)
        XCTAssertEqual(Array(fake.calls.prefix(3)), ["startCapture", "begin", "enqueue:6400"])
        XCTAssertTrue(fake.released, "the mic is released after a run")
        XCTAssertNil(fake.onCapture)
    }

    func testNoTCCGrantRefusesBeforeTheDeviceIsTouched() {
        let fake = LoopFake()
        XCTAssertThrowsError(try VoiceLoopback.run(wav: WAVBytes.make(Data(count: 640)), audio: fake, micAuthorized: { false })) {
            XCTAssertEqual($0 as? VoiceSelftestCode, .tccNotGranted)
        }
        XCTAssertEqual(fake.calls, [], "no capture is opened (and no TCC prompt is asked for)")
        XCTAssertThrowsError(try VoiceLoopback.run(wav: Data("x".utf8), audio: fake, micAuthorized: { true })) {
            XCTAssertEqual($0 as? VoiceSelftestCode, .wavInvalid)
        }
        XCTAssertEqual(fake.calls, [], "a bad clip never reaches the device")
    }

    func testAudioFailuresAreStableCodesAndReleaseTheMic() {
        let wav = WAVBytes.make(Data(count: 640))
        let failing = LoopFake()
        failing.failStart = true
        XCTAssertThrowsError(try VoiceLoopback.run(wav: wav, audio: failing, micAuthorized: { true })) {
            XCTAssertEqual($0 as? VoiceSelftestCode, .micUnavailable)
        }
        XCTAssertTrue(failing.released)

        let noAEC = LoopFake()
        noAEC.aec = "off"
        noAEC.frames = frames(Data(count: 640 + Self.second))
        XCTAssertThrowsError(try VoiceLoopback.run(wav: wav, audio: noAEC, micAuthorized: { true })) {
            XCTAssertEqual($0 as? VoiceSelftestCode, .aecUnavailable, "a capture without AEC measures nothing")
        }
        XCTAssertTrue(noAEC.released)

        let silent = LoopFake()
        silent.frames = frames(Data(count: 6400))  // short of clip + 1 s
        let started = Date()
        XCTAssertThrowsError(try VoiceLoopback.run(wav: wav, audio: silent, micAuthorized: { true }, slackSeconds: 0.1)) {
            XCTAssertEqual($0 as? VoiceSelftestCode, .captureTimeout)
        }
        XCTAssertLessThan(Date().timeIntervalSince(started), 3)
        XCTAssertTrue(silent.released)
    }

    /// Nothing in the loopback or the socket code touches the file system
    /// (the token file has its own type); the clip and the capture live in
    /// memory only.
    func testTheLoopbackSourcesWriteNothingToDisk() throws {
        let loopback = String(decoding: try Repo.data("node/friday-node/Sources/NodeCore/Voice/VoiceLoopback.swift"), as: UTF8.self)
        for word in ["FileManager", "FileHandle", "write(to", "fopen", "open(", "createFile", "mkstemp", "NSTemporaryDirectory"] {
            XCTAssertFalse(loopback.contains(word), word)
        }
        let selftest = String(decoding: try Repo.data("node/friday-node/Sources/NodeCore/Voice/VoiceSelftest.swift"), as: UTF8.self)
        let beforeTokenFile = try XCTUnwrap(selftest.components(separatedBy: "public enum SelftestTokenFile").first)
        for word in ["FileManager", "FileHandle", "write(to", "fopen", " open(", "createFile", "mkstemp"] {
            XCTAssertFalse(beforeTokenFile.contains(word), word)
        }
    }

    #if DEBUG
    func testTheEchoEngineEchoesTheClipThenSilence() throws {
        let clip = WAVBytes.pattern(3200)
        let echo = EchoVoiceEngine()
        let capture = try LoopbackWAV.decode(VoiceLoopback.run(wav: WAVBytes.make(clip), audio: echo, micAuthorized: { true }))
        XCTAssertEqual(capture, clip + Data(count: Self.second))
        XCTAssertEqual(echo.captureStarts, 1)
        XCTAssertEqual(echo.captureStops, 1)
        XCTAssertEqual(echo.shutdowns, 1)
    }

    /// The DEBUG CLI (SwiftPM builds it next to the test bundle): the WAV
    /// contract on stdin/stdout, the stable error codes on stderr, and an
    /// empty HOME, TMPDIR and working directory afterwards.
    func testTheDebugCLIMeetsTheContractAndWritesNothing() throws {
        let cli = Bundle(for: Self.self).bundleURL.deletingLastPathComponent().appendingPathComponent("friday-node")
        guard FileManager.default.isExecutableFile(atPath: cli.path) else {
            throw XCTSkip("no friday-node binary next to the test bundle (an Xcode run)")
        }
        let sandbox = FileManager.default.temporaryDirectory.appendingPathComponent("fnl-\(UUID().uuidString)")
        let dirs = ["home", "tmp", "cwd"].map { sandbox.appendingPathComponent($0) }
        for dir in dirs { try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true) }
        defer { try? FileManager.default.removeItem(at: sandbox) }
        func run(_ args: [String], _ input: Data) throws -> (Int32, Data, String) {
            let process = Process()
            process.executableURL = cli
            process.arguments = args
            process.currentDirectoryURL = dirs[2]
            process.environment = ["HOME": dirs[0].path, "TMPDIR": dirs[1].path + "/", "PATH": "/usr/bin:/bin"]
            let stdin = Pipe(), stdout = Pipe(), stderr = Pipe()
            process.standardInput = stdin
            process.standardOutput = stdout
            process.standardError = stderr
            try process.run()
            stdin.fileHandleForWriting.write(input)
            try stdin.fileHandleForWriting.close()
            let out = stdout.fileHandleForReading.readDataToEndOfFile()
            let err = String(decoding: stderr.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
            process.waitUntilExit()
            return (process.terminationStatus, out, err)
        }
        let clip = WAVBytes.pattern(6400)
        let ok = try run(["voice-loopback", "--debug-echo-engine"], WAVBytes.make(clip))
        XCTAssertEqual(ok.0, 0, ok.2)
        XCTAssertEqual(try LoopbackWAV.decode(ok.1), clip + Data(count: Self.second))
        XCTAssertEqual(ok.2, "")
        let bad = try run(["voice-loopback", "--debug-echo-engine"], Data("not a wav".utf8))
        XCTAssertEqual(bad.0, 3)
        XCTAssertEqual(bad.1, Data(), "no audio on stdout after a failure")
        XCTAssertEqual(bad.2, "friday-node voice-loopback: error wav_invalid\n")
        // No app socket under this HOME: the real path is refused, not faked.
        let down = try run(["voice-loopback"], WAVBytes.make(clip))
        XCTAssertEqual(down.0, 5)
        XCTAssertEqual(down.2, "friday-node voice-loopback: error app_unreachable\n")
        XCTAssertEqual(down.1, Data())
        let usage = try run(["voice-loopback", "--echo"], Data())
        XCTAssertEqual(usage.0, 2)
        for dir in dirs {
            XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: dir.path), [], dir.lastPathComponent)
        }
    }
    #endif
}

// MARK: The app's side and its socket

final class FakeHold: VoiceSelftestHold, @unchecked Sendable {
    private let lock = NSLock()
    private(set) var history: [Bool] = []
    var micBusy = false
    var refuse = false

    func setSelftestHold(_ on: Bool, timeout: Double) -> Bool {
        lock.lock()
        history.append(on)
        lock.unlock()
        return !(on && refuse)
    }

    var micInUse: Bool { micBusy }

    var calls: [Bool] {
        lock.lock()
        defer { lock.unlock() }
        return history
    }
}

final class VoiceSelftestHostTests: XCTestCase {
    var enabled = true
    var killSwitch = false
    var credential: NodeCredential? = VoiceFixtures.nodeCredential()
    var audio = LoopFake()

    func makeHost(_ mint: ScriptedMint, hold: FakeHold, lease: Double = 900) -> VoiceSelftestHost {
        VoiceSelftestHost(
            enabled: { self.enabled }, hold: hold, credential: { self.credential }, killSwitch: { self.killSwitch },
            mint: { try await mint.mint($0) }, makeAudio: { self.audio }, micAuthorized: { true },
            leaseSeconds: lease, slackSeconds: 0.2)
    }

    func code(_ body: () throws -> Any) -> VoiceSelftestCode? {
        do {
            _ = try body()
            return nil
        } catch {
            return error as? VoiceSelftestCode
        }
    }

    func testTheGatesComeFirst() {
        let mint = ScriptedMint([.success(ScriptedMint.token())])
        let hold = FakeHold()
        let host = makeHost(mint, hold: hold)
        enabled = false
        XCTAssertEqual(code { try host.selftestBegin() }, .selftestDisabled)
        XCTAssertEqual(code { try host.selftestLoopback(Data()) }, .selftestDisabled)
        enabled = true
        killSwitch = true
        XCTAssertEqual(code { try host.selftestBegin() }, .killSwitch)
        killSwitch = false
        credential = nil
        XCTAssertEqual(code { try host.selftestBegin() }, .notPaired)
        XCTAssertEqual(mint.calls, 0, "nothing is minted behind a closed gate")
        XCTAssertEqual(hold.calls, [], "and the live voice is never touched")
    }

    func testBeginHoldsMintsAndEndGivesTheVoiceBack() throws {
        let mint = ScriptedMint([.success(ScriptedMint.token()), .success(ScriptedMint.token("B"))])
        let hold = FakeHold()
        let host = makeHost(mint, hold: hold)
        XCTAssertEqual(code { try host.selftestLoopback(WAVBytes.make(Data())) }, .noSelftestHold)
        let token = try host.selftestBegin()
        XCTAssertEqual(token.token, ScriptedMint.token().token)
        XCTAssertEqual(hold.calls, [true])
        XCTAssertTrue(host.holding)
        XCTAssertEqual(code { try host.selftestBegin() }, .busy, "one selftest at a time")
        // The loopback runs inside the hold, on a fresh engine.
        audio.frames = Array(repeating: Data(count: 640), count: 60)
        let capture = try LoopbackWAV.decode(host.selftestLoopback(WAVBytes.make(Data(count: 640))))
        XCTAssertEqual(capture.count, 640 + VoiceLoopback.bytesPerSecond)
        XCTAssertTrue(audio.released)
        host.selftestEnd()
        XCTAssertEqual(hold.calls, [true, false])
        XCTAssertFalse(host.holding)
        host.selftestEnd()
        XCTAssertEqual(hold.calls, [true, false], "a second end is a no-op")
        XCTAssertEqual(code { try host.selftestLoopback(WAVBytes.make(Data())) }, .noSelftestHold)
    }

    /// `end` is never gated: turning the setting off never strands a hold.
    func testEndWorksWithTheSettingOff() throws {
        let hold = FakeHold()
        let host = makeHost(ScriptedMint([.success(ScriptedMint.token())]), hold: hold)
        _ = try host.selftestBegin()
        enabled = false
        host.selftestEnd()
        XCTAssertEqual(hold.calls, [true, false])
        XCTAssertFalse(host.holding)
    }

    func testAFailedMintGivesTheVoiceBack() {
        let hold = FakeHold()
        let refused = makeHost(ScriptedMint([.failure(.refused)]), hold: hold)
        XCTAssertEqual(code { try refused.selftestBegin() }, .credentialRefused)
        XCTAssertEqual(hold.calls, [true, false])
        XCTAssertFalse(refused.holding)
        let failed = makeHost(ScriptedMint([.failure(.transport)]), hold: hold)
        XCTAssertEqual(code { try failed.selftestBegin() }, .mintFailed)
        XCTAssertEqual(hold.calls, [true, false, true, false])
        let busy = FakeHold()
        busy.refuse = true
        XCTAssertEqual(code { try self.makeHost(ScriptedMint([]), hold: busy).selftestBegin() }, .micBusy)
        XCTAssertEqual(busy.calls, [true, false])
    }

    func testTheLeaseEndsAForgottenHold() throws {
        let hold = FakeHold()
        let host = makeHost(ScriptedMint([.success(ScriptedMint.token())]), hold: hold, lease: 0.2)
        _ = try host.selftestBegin()
        let deadline = Date().addingTimeInterval(3)
        while host.holding, Date() < deadline { usleep(20_000) }
        XCTAssertFalse(host.holding)
        XCTAssertEqual(hold.calls, [true, false])
    }

    /// The lease never ends the hold under a running loopback: the live
    /// voice would take the mic back mid-capture. The expiry waits for the
    /// loopback, then ends the hold.
    func testALeaseThatRunsOutDuringALoopbackWaitsForIt() throws {
        let hold = FakeHold()
        let host = makeHost(ScriptedMint([.success(ScriptedMint.token())]), hold: hold, lease: 0.2)
        _ = try host.selftestBegin()
        audio.startDelay = 0.8
        audio.frames = Array(repeating: Data(count: 640), count: 60)
        let during = DispatchSemaphore(value: 0)
        var seenDuring: [Bool] = []
        DispatchQueue.global().asyncAfter(deadline: .now() + 0.5) {
            seenDuring = hold.calls
            during.signal()
        }
        let capture = try LoopbackWAV.decode(host.selftestLoopback(WAVBytes.make(Data(count: 640))))
        XCTAssertEqual(capture.count, 640 + VoiceLoopback.bytesPerSecond, "the loopback finished")
        during.wait()
        XCTAssertEqual(seenDuring, [true], "the expired lease did not release the hold mid-loopback")
        XCTAssertEqual(hold.calls, [true, false], "it ended the hold once the loopback was done")
        XCTAssertFalse(host.holding)
    }

    func testTheLoopbackRefusesWhileTheLiveVoiceHoldsTheMic() throws {
        let hold = FakeHold()
        let host = makeHost(ScriptedMint([.success(ScriptedMint.token())]), hold: hold)
        _ = try host.selftestBegin()
        hold.micBusy = true
        XCTAssertEqual(code { try host.selftestLoopback(WAVBytes.make(Data())) }, .micBusy)
        XCTAssertEqual(audio.calls, [], "no second engine opens the mic")
        host.selftestEnd()
    }
}

final class FakeHandler: VoiceSelftestHandler, @unchecked Sendable {
    var beginResult: Result<VoiceToken, VoiceSelftestCode> = .success(ScriptedMint.token())
    var ended = 0
    var received: Data?

    func selftestBegin() throws -> VoiceToken { try beginResult.get() }
    func selftestEnd() { ended += 1 }
    func selftestLoopback(_ wav: Data) throws -> Data {
        received = wav
        return LoopbackWAV.encode(try LoopbackWAV.decode(wav) + Data(count: 4))
    }
}

final class VoiceSelftestSocketTests: XCTestCase {
    var directory: String!
    var path: String { directory + "/s.sock" }

    override func setUp() {
        directory = "/tmp/fns-" + String(UUID().uuidString.prefix(8)).lowercased()
    }

    override func tearDown() {
        try? FileManager.default.removeItem(atPath: directory)
    }

    func testTheCLIAndTheAppTalkOverTheSocket() throws {
        let handler = FakeHandler()
        let socket = VoiceSelftestSocket(path: path, handler: handler)
        try socket.start()
        defer { socket.stop() }
        var info = stat()
        XCTAssertEqual(lstat(path, &info), 0)
        XCTAssertEqual(info.st_mode & 0o777, 0o600)
        XCTAssertEqual(lstat(directory, &info), 0)
        XCTAssertEqual(info.st_mode & 0o777, 0o700)

        let minted = try VoiceSelftestClient.begin(path: path)
        XCTAssertEqual(minted.token, ScriptedMint.token().token)
        XCTAssertEqual(minted.serverURL, "https://friday.example")
        XCTAssertEqual(minted.caSHA256, String(repeating: "ab", count: 32))
        let clip = WAVBytes.pattern(640)
        let capture = try VoiceSelftestClient.loopback(LoopbackWAV.encode(clip), path: path)
        XCTAssertEqual(try LoopbackWAV.decode(capture), clip + Data(count: 4))
        XCTAssertEqual(handler.received, LoopbackWAV.encode(clip))
        try VoiceSelftestClient.end(path: path)
        XCTAssertEqual(handler.ended, 1)
        handler.beginResult = .failure(.selftestDisabled)
        XCTAssertThrowsError(try VoiceSelftestClient.begin(path: path)) {
            XCTAssertEqual($0 as? VoiceSelftestCode, .selftestDisabled, "the app's code reaches the CLI")
        }
    }

    func testAnotherUserAndAMalformedRequestGetNothing() throws {
        let handler = FakeHandler()
        let refusing = VoiceSelftestSocket(path: path, handler: handler, peerUID: { _ in 4242 })
        try refusing.start()
        XCTAssertThrowsError(try VoiceSelftestClient.begin(path: path)) {
            // Closed before a byte is read: the send or the read fails.
            let code = $0 as? VoiceSelftestCode
            XCTAssertTrue(code == .protocolError || code == .appUnreachable, "closed without an answer")
        }
        refusing.stop()
        XCTAssertThrowsError(try VoiceSelftestClient.begin(path: path)) {
            XCTAssertEqual($0 as? VoiceSelftestCode, .appUnreachable)
        }
        let socket = VoiceSelftestSocket(path: path, handler: handler)
        try socket.start()
        defer { socket.stop() }
        for request in [#"{"op":"begin","x":1}"#, #"{"op":"shell"}"#, "nope", #"{"op":"loopback","body_bytes":-1}"#] {
            let fd = try XCTUnwrap(LocalSocketProbe.connect(path))
            defer { close(fd) }
            _ = LocalSocketProbe.send(fd, Data((request + "\n").utf8))
            let answer = LocalSocketProbe.readAll(fd)
            XCTAssertEqual(String(decoding: answer, as: UTF8.self), #"{"error":"protocol","ok":false}"# + "\n", request)
        }
        XCTAssertNil(handler.received)
        XCTAssertEqual(handler.ended, 0)
    }
}

/// A raw client (the tests' own, not the CLI's).
enum LocalSocketProbe {
    static func connect(_ path: String) -> Int32? {
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        withUnsafeMutableBytes(of: &address.sun_path) { $0.copyBytes(from: Array(path.utf8)) }
        let ok = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        return ok == 0 ? fd : nil
    }

    static func send(_ fd: Int32, _ data: Data) -> Int {
        data.withUnsafeBytes { Darwin.send(fd, $0.baseAddress, $0.count, 0) }
    }

    static func readAll(_ fd: Int32) -> Data {
        var out = Data()
        var buffer = [UInt8](repeating: 0, count: 4096)
        while true {
            let count = recv(fd, &buffer, buffer.count, 0)
            if count <= 0 { return out }
            out.append(contentsOf: buffer[0..<count])
        }
    }
}

final class SelftestTokenFileTests: XCTestCase {
    var directory: URL!

    override func setUpWithError() throws {
        directory = FileManager.default.temporaryDirectory.appendingPathComponent("fnt-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: directory)
    }

    func testTheTokenFileIsNew0600AndRemovedAfterwards() throws {
        let path = directory.appendingPathComponent("selftest.token").path
        let fd = try SelftestTokenFile.create(path)
        try SelftestTokenFile.write("jpat_" + String(repeating: "A", count: 20), to: fd, path: path)
        var info = stat()
        XCTAssertEqual(lstat(path, &info), 0)
        XCTAssertEqual(info.st_mode & 0o777, 0o600)
        XCTAssertEqual(info.st_uid, getuid())
        XCTAssertEqual(try String(contentsOfFile: path, encoding: .utf8), "jpat_" + String(repeating: "A", count: 20) + "\n")
        XCTAssertThrowsError(try SelftestTokenFile.create(path), "never over an existing file") {
            XCTAssertEqual($0 as? VoiceSelftestCode, .tokenFileUnsafe)
        }
        SelftestTokenFile.remove(path)
        XCTAssertNotEqual(lstat(path, &info), 0)
    }

    func testSymlinksSharedDirectoriesAndRelativePathsAreRefused() throws {
        let target = directory.appendingPathComponent("target").path
        FileManager.default.createFile(atPath: target, contents: Data("keep".utf8))
        let link = directory.appendingPathComponent("link").path
        try FileManager.default.createSymbolicLink(atPath: link, withDestinationPath: target)
        XCTAssertThrowsError(try SelftestTokenFile.create(link))
        SelftestTokenFile.remove(link)
        XCTAssertEqual(try String(contentsOfFile: target, encoding: .utf8), "keep", "a symlink is removed, never followed")
        XCTAssertFalse(FileManager.default.fileExists(atPath: link))
        let shared = directory.appendingPathComponent("shared")
        try FileManager.default.createDirectory(at: shared, withIntermediateDirectories: false)
        chmod(shared.path, 0o777)
        XCTAssertThrowsError(try SelftestTokenFile.create(shared.appendingPathComponent("t").path))
        XCTAssertThrowsError(try SelftestTokenFile.create("relative.token"))
        XCTAssertThrowsError(try SelftestTokenFile.create("/nonexistent-dir/t"))
    }
}

// MARK: The nightly script

final class SelftestNightlyScriptTests: XCTestCase {
    var home: URL!
    var log: URL { home.appendingPathComponent("calls.log") }
    var bin: URL { home.appendingPathComponent("Library/Application Support/Friday/bin") }
    var tokenFile: String { home.path + "/Library/Application Support/Friday/voice/selftest.token" }

    override func setUpWithError() throws {
        home = FileManager.default.temporaryDirectory.appendingPathComponent("fnn-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: bin, withIntermediateDirectories: true)
        try stub("pmset", #"echo "Now drawing from '$STUB_POWER'""#)
        // The CLI: `--out` writes a 0600 token (as the real one does),
        // `--end` removes it; every call is logged.
        try stub("friday-node", #"""
            echo "node $1 $2" >> "$STUB_LOG"
            case "$1 $2" in
              "voice-token --out")
                [ -e "$3" ] && { echo "exists" >> "$STUB_LOG"; exit 6; }
                [ "$STUB_MINT" = fail ] && { echo "friday-node voice-token: error mint_failed" >&2; exit 5; }
                (umask 077; printf 'jpat_AAAAAAAAAAAAAAAAAAAA\n' > "$3")
                printf 'server_url=https://friday.example:8443\nca_sha256=%s\nexpires_at=2099-01-01T00:00:00Z\n' "$(printf 'ab%.0s' $(seq 32))" ;;
              "voice-token --end") rm -f "$3" ;;
            esac
            """#)
        // friday-voice: logs its argv one per line and the token file's mode.
        try stub("friday-voice", #"""
            echo "voice-begin" >> "$STUB_LOG"
            for a in "$@"; do echo "arg:$a" >> "$STUB_LOG"; done
            prev=""
            for a in "$@"; do
              [ "$prev" = "--token-file" ] && echo "token-mode:$(stat -f %Lp "$a")" >> "$STUB_LOG"
              prev=$a
            done
            if [ -n "$STUB_SLEEP" ]; then
              # A run that takes 30 s and stops on SIGTERM (as friday-voice does).
              trap 'echo voice-term >> "$STUB_LOG"; kill $s 2>/dev/null; exit 143' TERM
              echo "voice-pid:$$" >> "$STUB_LOG"
              sleep 30 & s=$!
              wait $s
            fi
            exit "${STUB_EXIT:-0}"
            """#)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: home)
    }

    func stub(_ name: String, _ body: String) throws {
        let url = bin.appendingPathComponent(name)
        try ("#!/bin/sh\n" + body + "\n").write(to: url, atomically: true, encoding: .utf8)
        chmod(url.path, 0o755)
    }

    func run(power: String, mint: String = "ok", exit: Int = 0) throws -> (Int32, [String]) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/bash")
        process.arguments = [Repo.url("node/friday-node/scripts/selftest-nightly.sh").path]
        process.environment = [
            "HOME": home.path, "PATH": "/usr/bin:/bin", "STUB_POWER": power, "STUB_LOG": log.path, "STUB_MINT": mint,
            "STUB_EXIT": String(exit), "FRIDAY_NODE_CLI": bin.appendingPathComponent("friday-node").path,
            "FRIDAY_VOICE_BIN": bin.appendingPathComponent("friday-voice").path,
            "FRIDAY_SELFTEST_PMSET": bin.appendingPathComponent("pmset").path,
        ]
        process.standardError = FileHandle.nullDevice
        process.standardOutput = FileHandle.nullDevice
        try process.run()
        process.waitUntilExit()
        let lines = ((try? String(contentsOf: log, encoding: .utf8)) ?? "").split(separator: "\n").map(String.init)
        return (process.terminationStatus, lines)
    }

    func testOnBatteryNothingRuns() throws {
        // A token file a killed run left behind.
        try FileManager.default.createDirectory(
            atPath: (tokenFile as NSString).deletingLastPathComponent, withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: tokenFile, contents: Data("jpat_old\n".utf8))
        let (status, calls) = try run(power: "Battery Power")
        XCTAssertEqual(status, 0)
        XCTAssertEqual(
            calls, ["node voice-token --end", "node voice-token --end"],
            "no token, no audio on battery: only the cleanup (before the check and on exit)")
        XCTAssertFalse(FileManager.default.fileExists(atPath: tokenFile), "a stale token goes even on battery")
    }

    /// launchd's SIGTERM mid-run: the trap fires at once (friday-voice runs
    /// in the background), stops the child and deletes the token, so a
    /// SIGKILL that follows finds nothing on disk.
    func testATermMidRunDeletesTheTokenAndStopsTheChild() throws {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/bash")
        process.arguments = [Repo.url("node/friday-node/scripts/selftest-nightly.sh").path]
        process.environment = [
            "HOME": home.path, "PATH": "/usr/bin:/bin", "STUB_POWER": "AC Power", "STUB_LOG": log.path,
            "STUB_MINT": "ok", "STUB_SLEEP": "1", "FRIDAY_NODE_CLI": bin.appendingPathComponent("friday-node").path,
            "FRIDAY_VOICE_BIN": bin.appendingPathComponent("friday-voice").path,
            "FRIDAY_SELFTEST_PMSET": bin.appendingPathComponent("pmset").path,
        ]
        process.standardError = FileHandle.nullDevice
        process.standardOutput = FileHandle.nullDevice
        try process.run()
        func lines() -> [String] {
            ((try? String(contentsOf: log, encoding: .utf8)) ?? "").split(separator: "\n").map(String.init)
        }
        let deadline = Date().addingTimeInterval(10)
        while !lines().contains(where: { $0.hasPrefix("voice-pid:") }), Date() < deadline { usleep(20_000) }
        let pidLine = try XCTUnwrap(lines().first { $0.hasPrefix("voice-pid:") }, "friday-voice started")
        let voicePID = try XCTUnwrap(Int32(pidLine.dropFirst("voice-pid:".count)))
        XCTAssertTrue(FileManager.default.fileExists(atPath: tokenFile), "the token is on disk during the run")
        let sent = Date()
        kill(process.processIdentifier, SIGTERM)  // to the script only, as launchd sends it
        process.waitUntilExit()
        XCTAssertLessThan(Date().timeIntervalSince(sent), 5, "the trap did not wait for the 30 s child")
        XCTAssertEqual(process.terminationStatus, 143)
        XCTAssertFalse(FileManager.default.fileExists(atPath: tokenFile), "the token file is gone")
        XCTAssertEqual(lines().filter { $0 == "node voice-token --end" }.count, 2, "the hold was ended")
        let gone = Date().addingTimeInterval(3)
        while kill(voicePID, 0) == 0, Date() < gone { usleep(20_000) }
        XCTAssertNotEqual(kill(voicePID, 0), 0, "the child was stopped")
        XCTAssertTrue(lines().contains("voice-term"), "by the script's trap")
    }

    func testOnPowerTheTokenIsHandedOverAndDeletedAfterTheRun() throws {
        let (status, calls) = try run(power: "AC Power", exit: 1)
        XCTAssertEqual(status, 1, "the selftest's exit code is the job's")
        let node = bin.appendingPathComponent("friday-node").path
        XCTAssertEqual(calls.filter { $0.hasPrefix("node ") }, [
            "node voice-token --end", "node voice-token --out", "node voice-token --end",
        ])
        let args = calls.filter { $0.hasPrefix("arg:") }.map { String($0.dropFirst(4)) }
        func value(_ flag: String) -> String? { args.firstIndex(of: flag).map { args[$0 + 1] } }
        XCTAssertEqual(Array(args.prefix(2)), ["selftest", "--nightly"])
        XCTAssertEqual(value("--server"), "https://friday.example:8443")
        XCTAssertEqual(value("--ca-sha256"), String(repeating: "ab", count: 32))
        XCTAssertEqual(value("--token-file"), tokenFile)
        XCTAssertEqual(value("--loopback-cmd"), node.replacingOccurrences(of: " ", with: "\\ ") + " voice-loopback")
        XCTAssertEqual(value("--model-dir"), home.path + "/Library/Application Support/Friday/voice/models")
        XCTAssertEqual(value("--result-file"), home.path + "/Library/Application Support/Friday/voice/selftest-result.json")
        XCTAssertTrue(calls.contains("token-mode:600"))
        XCTAssertFalse(FileManager.default.fileExists(atPath: tokenFile), "the token file is gone after the run")
    }

    /// The installer (run here against a temporary HOME only) copies the
    /// script, renders the agent with absolute paths and never loads it.
    func testTheInstallerRendersTheAgentWithoutLoadingIt() throws {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/bash")
        process.arguments = [Repo.url("node/friday-node/scripts/install-voice-selftest.sh").path]
        process.environment = ["HOME": home.path, "PATH": bin.path + ":/usr/bin:/bin"]
        let out = Pipe()
        process.standardOutput = out
        process.standardError = FileHandle.nullDevice
        try process.run()
        let text = String(decoding: out.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
        process.waitUntilExit()
        XCTAssertEqual(process.terminationStatus, 0)
        XCTAssertTrue(text.contains("(not loaded)"))
        XCTAssertTrue(text.contains("launchctl bootstrap gui/$(id -u)"))
        let plistPath = home.path + "/Library/LaunchAgents/ai.friday.voice-selftest.plist"
        let plist = try XCTUnwrap(
            PropertyListSerialization.propertyList(from: try Data(contentsOf: URL(fileURLWithPath: plistPath)), format: nil)
                as? [String: Any])
        XCTAssertEqual(plist["Label"] as? String, "ai.friday.voice-selftest")
        let script = home.path + "/Library/Application Support/Friday/voice/selftest-nightly.sh"
        XCTAssertEqual(plist["ProgramArguments"] as? [String], ["/bin/bash", script])
        let when = try XCTUnwrap(plist["StartCalendarInterval"] as? [String: Int])
        XCTAssertEqual(when, ["Hour": 3, "Minute": 17])
        XCTAssertEqual(plist["StandardErrorPath"] as? String, home.path + "/Library/Logs/Friday/voice-selftest.log")
        XCTAssertNil(plist["RunAtLoad"], "nothing runs when the owner loads it")
        var info = stat()
        XCTAssertEqual(lstat(script, &info), 0)
        XCTAssertEqual(info.st_mode & 0o777, 0o700)
        XCTAssertEqual(
            try Data(contentsOf: URL(fileURLWithPath: script)),
            try Repo.data("node/friday-node/scripts/selftest-nightly.sh"))
        XCTAssertEqual(lstat(plistPath, &info), 0)
        XCTAssertEqual(info.st_mode & 0o777, 0o644)
    }

    func testANightWithoutATokenRecordsAnErrorNotAPass() throws {
        let (status, calls) = try run(power: "AC Power", mint: "fail", exit: 2)
        XCTAssertEqual(status, 2)
        let args = calls.filter { $0.hasPrefix("arg:") }.map { String($0.dropFirst(4)) }
        XCTAssertTrue(args.contains("--result-file"), "the selftest still writes its error line")
        XCTAssertFalse(args.contains("--token-file"))
        XCTAssertFalse(args.contains("--server"))
        XCTAssertFalse(FileManager.default.fileExists(atPath: tokenFile))
    }
}
