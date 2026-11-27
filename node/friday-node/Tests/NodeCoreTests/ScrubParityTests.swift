// Scrub parity (Phase 9 brief M2, contract test 1): every vector of
// backend/nodes/conformance/vectors/scrub.json, loaded from the repo path,
// scrubs byte for byte to its `expected` (recall alone is not enough).

import Foundation
import NodeCore
import XCTest

final class ScrubParityTests: XCTestCase {
    func testEveryVectorIsByteIdentical() throws {
        let doc = try Repo.json("backend/nodes/conformance/vectors/scrub.json")
        let items = try XCTUnwrap(doc["items"]?.arrayValue)
        XCTAssertGreaterThanOrEqual(items.count, 190)
        var failures: [String] = []
        for item in items {
            let id = item["id"]?.stringValue ?? "?"
            let input = String(decoding: Data(base64Encoded: item["input_b64"]?.stringValue ?? "") ?? Data(), as: UTF8.self)
            let expected = item["expected"]?.stringValue ?? ""
            let actual = Scrubber.scrub(input)
            if Data(actual.utf8) != Data(expected.utf8) { failures.append(id) }
            // Placeholders are idempotent: a second scrub changes nothing.
            XCTAssertEqual(Scrubber.scrub(actual), actual, "not idempotent: \(id)")
        }
        XCTAssertEqual(failures, [], "scrub parity failures")
    }

    func testInputIsBoundedAtAWhitespace() {
        let text = String(repeating: "a", count: Scrubber.maxInput - 10) + " tail " + String(repeating: "b", count: 500)
        let out = Scrubber.scrub(text)
        XCTAssertEqual(out.unicodeScalars.count, Scrubber.maxInput - 10 + 5)
        XCTAssertTrue(out.hasSuffix(" tail"))
    }
}
