// `friday-node`: the node's command-line companion (Phase 9 brief M1-M3).
// M1 ships only `version`; `hook` (M3) and `scrub` (M2) arrive with their
// branches. No model calls, no provider keys, no network.

import Foundation
import NodeCore

let arguments = CommandLine.arguments.dropFirst()
switch arguments.first {
case "version", "--version":
    print("friday-node \(NodeInfo.version) (protocol v\(NodeProtocol.version))")
default:
    FileHandle.standardError.write(Data("usage: friday-node version\n".utf8))
    exit(arguments.isEmpty ? 0 : 2)
}
