// FridayNode.app: the Friday Node menu-bar app (Phase 9 brief M1; ADR-013).
// An `LSUIElement` agent with an `NSStatusItem`; no Dock icon, no window
// until the owner pairs. Outbound connections only: the app opens no TCP
// listener of any kind.

import AppKit
import NodeCore

#if CONFORMANCE
// The unsigned Conformance build only: serve the fake gateway's hook and
// exit (see NodeCore/ConformanceSupport.swift).
do {
    if let hook = try ConformanceHookClient.fromArguments(CommandLine.arguments) {
        exit(hook.run())
    }
} catch {
    FileHandle.standardError.write(Data("conformance mode: \(error)\n".utf8))
    exit(2)
}
#endif

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
