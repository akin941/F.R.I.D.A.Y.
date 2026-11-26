// `friday-node`: the node's command-line companion (Phase 9 brief M1-M3).
// - `version`
// - `scrub --stdin`: scrub standard input with the node's scrubber (the
//   port of the 8A scrubber, no workspace terms) and print the result;
// - `scrub --jsonl`: each stdin line is one JSON string; each output line
//   is its scrubbed JSON string (node/scripts/scrubber-corpus.sh and the
//   differential check against the Python scrubber use it).
// `hook` arrives with M3. No model calls, no provider keys, no network.

import Foundation
import NodeCore

let arguments = Array(CommandLine.arguments.dropFirst())
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
default:
    FileHandle.standardError.write(Data("usage: friday-node version | friday-node scrub --stdin | --jsonl\n".utf8))
    exit(arguments.isEmpty ? 0 : 2)
}
