// swift-tools-version: 6.0
// friday-node: the Friday Node macOS app's core (Phase 9 brief "Mac session"
// M1, ADR-013). Apple frameworks only; no third-party dependency (any future
// one goes through docs/security/mcp-governance.md with Package.resolved).
//
// `swift build` / `swift test` cover NodeCore, the `friday-node` CLI and
// NodeCoreTests. The signed menu-bar app and its Debug / Release /
// Conformance configurations live in FridayNode.xcodeproj, which compiles
// the same NodeCore sources (scripts/build-sign-local.sh).
import PackageDescription

let package = Package(
    name: "friday-node",
    platforms: [.macOS(.v15)],
    products: [
        .library(name: "NodeCore", targets: ["NodeCore"]),
        .executable(name: "friday-node", targets: ["friday-node"]),
    ],
    targets: [
        .target(name: "NodeCore", swiftSettings: [.swiftLanguageMode(.v5)]),
        .executableTarget(
            name: "friday-node", dependencies: ["NodeCore"],
            swiftSettings: [.swiftLanguageMode(.v5)]),
        .testTarget(
            name: "NodeCoreTests", dependencies: ["NodeCore"],
            swiftSettings: [.swiftLanguageMode(.v5)]),
    ]
)
