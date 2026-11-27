// The conformance build rule (ADR-013 decisions 2 and 4; Phase 9 brief M1
// contract test 2): the conformance pin type exists only in the unsigned
// `Conformance` configuration. Run in Release:
//
//   xcodebuild -project FridayNode.xcodeproj -scheme FridayNode \
//     -configuration Release test -only-testing:NodeCoreTests/ReleaseHygieneTests
//
// and by `swift test` (SwiftPM never defines CONFORMANCE).

import Foundation
import NodeCore
import XCTest

final class ReleaseHygieneTests: XCTestCase {
    func testTheConformancePinTypeIsNotCompiledIn() throws {
        #if CONFORMANCE
        // Positive control: the one configuration that has it, so the
        // lookup below can tell present from absent.
        XCTAssertNotNil(NSClassFromString("FridayNodeConformancePins"))
        #else
        XCTAssertNil(NSClassFromString("FridayNodeConformancePins"),
                     "the conformance pin type is compiled into a non-Conformance build")
        #endif
    }

    /// Runbook B5: the scripted window owner and the scripted executor (the
    /// seams the conformance hook and the offline suite drive) exist only in
    /// DEBUG builds; Release has neither (a public probe: no `@testable`).
    func testTheScriptedDoublesAreNotCompiledIntoRelease() {
        #if DEBUG
        XCTAssertTrue(ReleaseProbe.scriptedDoublesCompiledIn)
        #else
        XCTAssertFalse(ReleaseProbe.scriptedDoublesCompiledIn,
                       "the scripted window owner or executor is compiled into a Release build")
        #endif
    }

    /// Whatever the build, the protocol offer is v2 then v1, and a node
    /// without executors runs nothing: every action contract is
    /// `unsupported`.
    func testTheExecutorlessNodeRunsNothing() {
        XCTAssertEqual(NodeProtocol.offeredSubprotocols, ["friday-node.v2", "friday-node.v1"])
        let executor = UnavailableExecutor()
        for contract in CapabilityContract.actions {
            XCTAssertEqual(executor.availability(for: contract.name), "unsupported", contract.name)
        }
    }

    /// Runbook B6: the Release app advertises the real executors, each gated
    /// by its own grant: with no usable srt, no Accessibility grant and no
    /// Automation grant for an enabled recipe's target, only open/focus
    /// (which need no TCC grant) are `granted`; check 22 (the owner's local
    /// mappings) still gates every one of them.
    func testTheReleaseExecutorIsGatedByTCCAndTheSrtLocation() {
        let denied = DeniedProbe()
        let executor = MacActionExecutor(
            srt: SrtExecutor(location: SrtLocation(node: "/nonexistent/node", prefix: "/nonexistent")),
            recipesEnabled: ["music.play_pause"], permissions: denied)
        XCTAssertEqual(executor.availability(for: "repo.command.run"), "unsupported")
        XCTAssertEqual(executor.availability(for: "applescript.recipe.run"), "not_granted")
        XCTAssertEqual(executor.availability(for: "ax.press"), "not_granted")
        XCTAssertEqual(executor.availability(for: "ax.type_text"), "not_granted")
        XCTAssertEqual(executor.availability(for: "apps.open"), "granted")
        XCTAssertEqual(executor.availability(for: "apps.focus"), "granted")
        XCTAssertEqual(ActionPolicy.empty(home: "/Users/o").repos.count, 0)
    }
}

/// Every TCC answer "no" (the Release hygiene test never asks the real TCC).
final class DeniedProbe: PermissionProbe {
    var accessibilityTrusted: Bool { false }
    func automation(_ bundleID: String, ask: Bool) -> AutomationStatus { .undetermined }
}
