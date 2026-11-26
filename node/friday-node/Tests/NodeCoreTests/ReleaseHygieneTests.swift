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

    /// Whatever the build, the protocol offer is v2 then v1, and the release
    /// executor runs nothing: every action contract is `unsupported`.
    func testTheReleaseExecutorRunsNothing() {
        XCTAssertEqual(NodeProtocol.offeredSubprotocols, ["friday-node.v2", "friday-node.v1"])
        let executor = UnavailableExecutor()
        for contract in CapabilityContract.actions {
            XCTAssertEqual(executor.availability(for: contract.name), "unsupported", contract.name)
        }
    }
}
