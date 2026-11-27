// `friday-node`: the node's command-line companion (Phase 9 brief M1-M3).
// - `version`
// - `scrub --stdin`: scrub standard input with the node's scrubber (the
//   port of the 8A scrubber, no workspace terms) and print the result;
// - `scrub --jsonl`: each stdin line is one JSON string; each output line
//   is its scrubbed JSON string (node/scripts/scrubber-corpus.sh and the
//   differential check against the Python scrubber use it).
// - `hook`: Claude Code's command hook (Phase 9 brief M3): stdin (at most
//   256 KiB) goes to the app's Unix socket; always exits 0, within 3 s,
//   prints nothing (a down node only loses the event).
// - `scrub-preview --owner-turns <dir>` / `scrub-preview --codex <dir>`
//   (debug builds only, `#if DEBUG`; Phase 8B brief M1, runbook B4): the
//   owner-turn rules over one Claude Code project directory or a Codex
//   sessions directory, printed as counts per line kind and per excluded
//   marker, never any text (NodeCore OwnerTurnPreview).
// - `voice-loopback` (Phase 11 M3, runbook B8): one 16 kHz mono PCM16 WAV on
//   stdin -> the running app plays it through the voice host's
//   voice-processing graph and captures the AEC-cleaned mic for the clip
//   plus 1 s -> that capture as a WAV on stdout. Nothing is written to disk.
//   Exit 0, or the code's status with `friday-node voice-loopback: error
//   <code>` on stderr (VoiceSelftestCode). DEBUG builds also take
//   `--debug-echo-engine` (in process, a zero-latency echo, no device: the
//   `CommandLoopback` contract run; never in Release).
// - `voice-token --out <file>`: the app takes the selftest hold and mints a
//   fresh `node_voice` token, written to a new 0600 file (and nowhere else);
//   stdout gets `server_url=`, `ca_sha256=` and `expires_at=` lines (no
//   token). `voice-token --end <file>`: removes the file and ends the hold.
// No model calls, no provider keys. The voice commands talk only to the
// app's own Unix socket (VoiceSelftestSocket); the app holds the mic grant
// and the node secret (README "Nightly self-trigger").

import Foundation
import NodeCore

let arguments = Array(CommandLine.arguments.dropFirst())

if arguments.first == "hook" {
    // The hard deadline: whatever blocks (a stdin that never closes, a stuck
    // socket), the process exits 0 after 3 s.
    signal(SIGALRM) { _ in _exit(0) }
    alarm(UInt32(HookClient.deadlineSeconds))
    let input = FileHandle.standardInput.readData(ofLength: HookEvent.maxInputBytes + 1)
    HookClient.relay(input, to: HookSocket.defaultPath)
    exit(0)
}

func voiceFailure(_ command: String, _ code: VoiceSelftestCode) -> Never {
    FileHandle.standardError.write(Data("friday-node \(command): error \(code.rawValue)\n".utf8))
    exit(code.exitStatus)
}

/// stdin, at most `VoiceLoopback.maxInputBytes` (more is `wav_too_long`).
func readClip() -> Data {
    var input = Data()
    while true {
        let chunk = FileHandle.standardInput.readData(ofLength: 65_536)
        if chunk.isEmpty { break }
        input.append(chunk)
        if input.count > VoiceLoopback.maxInputBytes { voiceFailure("voice-loopback", .wavTooLong) }
    }
    return input
}

switch arguments.first {
case "voice-loopback" where arguments.count == 1:
    let clip = readClip()
    do {
        _ = try LoopbackWAV.decode(clip)  // refused here before the app is asked
        FileHandle.standardOutput.write(try VoiceSelftestClient.loopback(clip))
    } catch let code as VoiceSelftestCode {
        voiceFailure("voice-loopback", code)
    } catch {
        voiceFailure("voice-loopback", .protocolError)
    }
#if DEBUG
case "voice-loopback" where arguments == ["voice-loopback", "--debug-echo-engine"]:
    do {
        let capture = try VoiceLoopback.run(wav: readClip(), audio: EchoVoiceEngine(), micAuthorized: { true })
        FileHandle.standardOutput.write(capture)
    } catch let code as VoiceSelftestCode {
        voiceFailure("voice-loopback", code)
    } catch {
        voiceFailure("voice-loopback", .protocolError)
    }
#endif
case "voice-token" where arguments.count == 3 && arguments[1] == "--out":
    let path = arguments[2]
    do {
        let fd = try SelftestTokenFile.create(path)
        let minted: SelftestToken
        do {
            minted = try VoiceSelftestClient.begin()
        } catch {
            close(fd)
            SelftestTokenFile.remove(path)
            throw error
        }
        do {
            try SelftestTokenFile.write(minted.token, to: fd, path: path)
        } catch {
            try? VoiceSelftestClient.end()
            throw error
        }
        let info = "server_url=\(minted.serverURL)\nca_sha256=\(minted.caSHA256 ?? "")\nexpires_at=\(minted.expiresAt)\n"
        FileHandle.standardOutput.write(Data(info.utf8))
    } catch let code as VoiceSelftestCode {
        voiceFailure("voice-token", code)
    } catch {
        voiceFailure("voice-token", .protocolError)
    }
case "voice-token" where arguments.count == 3 && arguments[1] == "--end":
    SelftestTokenFile.remove(arguments[2])
    do {
        try VoiceSelftestClient.end()
    } catch let code as VoiceSelftestCode {
        voiceFailure("voice-token", code)
    } catch {
        voiceFailure("voice-token", .protocolError)
    }
case "version", "--version":
    print("friday-node \(NodeInfo.version) (protocol v\(NodeProtocol.version))")
case "scrub" where arguments.dropFirst() == ["--stdin"]:
    let input = FileHandle.standardInput.readDataToEndOfFile()
    guard let text = String(data: input, encoding: .utf8) else {
        FileHandle.standardError.write(Data("friday-node scrub: input is not UTF-8\n".utf8))
        exit(1)
    }
    FileHandle.standardOutput.write(Data(Scrubber.scrub(text).utf8))
case "scrub" where arguments.dropFirst() == ["--jsonl"]:
    let input = FileHandle.standardInput.readDataToEndOfFile()
    var out = Data()
    for line in input.split(separator: 0x0A, omittingEmptySubsequences: true) {
        guard case .string(let text)? = try? JSON.parse(Data(line)), let encoded = try? JSON.encode(.string(Scrubber.scrub(text)))
        else {
            FileHandle.standardError.write(Data("friday-node scrub: a line is not a JSON string\n".utf8))
            exit(1)
        }
        out.append(Data(encoded.utf8))
        out.append(0x0A)
    }
    FileHandle.standardOutput.write(out)
#if DEBUG
case "scrub-preview" where arguments.count == 3 && ["--owner-turns", "--codex"].contains(arguments[1]):
    var isDirectory: ObjCBool = false
    guard FileManager.default.fileExists(atPath: arguments[2], isDirectory: &isDirectory), isDirectory.boolValue else {
        FileHandle.standardError.write(Data("friday-node scrub-preview: not a directory\n".utf8))
        exit(1)
    }
    let counts = arguments[1] == "--owner-turns"
        ? OwnerTurnPreview.claude(directory: arguments[2]) : OwnerTurnPreview.codex(directory: arguments[2])
    // Counts only: the keys are rule names, format words and the fixed
    // marker strings; no transcript text, identifier or file name.
    var out = "friday-node scrub-preview \(arguments[1]) (debug build; counts only, no text)\n"
    for (key, count) in counts { out += "\(key)\t\(count)\n" }
    FileHandle.standardOutput.write(Data(out.utf8))
#endif
default:
    #if DEBUG
    let usage = "usage: friday-node version | hook | scrub --stdin | scrub --jsonl"
        + " | voice-loopback [--debug-echo-engine] | voice-token --out <file> | voice-token --end <file>"
        + " | scrub-preview --owner-turns <dir> | scrub-preview --codex <dir>\n"
    #else
    let usage = "usage: friday-node version | hook | scrub --stdin | scrub --jsonl"
        + " | voice-loopback | voice-token --out <file> | voice-token --end <file>\n"
    #endif
    FileHandle.standardError.write(Data(usage.utf8))
    exit(arguments.isEmpty ? 0 : 2)
}
