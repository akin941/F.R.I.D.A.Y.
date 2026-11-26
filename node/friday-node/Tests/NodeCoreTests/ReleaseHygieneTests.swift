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
}
