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
// No model calls, no provider keys, no network.

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

switch arguments.first {
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
        + " | scrub-preview --owner-turns <dir> | scrub-preview --codex <dir>\n"
    #else
    let usage = "usage: friday-node version | hook | scrub --stdin | scrub --jsonl\n"
    #endif
    FileHandle.standardError.write(Data(usage.utf8))
    exit(arguments.isEmpty ? 0 : 2)
}
