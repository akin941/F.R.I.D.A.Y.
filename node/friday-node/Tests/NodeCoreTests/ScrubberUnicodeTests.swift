// The scrubber's `\w` / `\d` sets equal CPython 3.12's exactly (Unicode
// 15.0), on this Mac's ICU: count and sha256 of the code points, measured
// with `re.fullmatch(r'\w', chr(i))` / `r'\d'` over U+0000..U+10FFFF on the
// backend's Python. An ICU (macOS) update that adds or moves characters
// fails here instead of letting the Swift scrubber drift from the server's.

import CryptoKit
import Foundation
import NodeCore
import XCTest

final class ScrubberUnicodeTests: XCTestCase {
    private func fingerprint(_ set: String) -> (Int, String) {
        let regex = try! NSRegularExpression(pattern: "^" + set + "\\z")
        var members: [String] = []
        for value in UInt32(0)...0x10FFFF where !(0xD800...0xDFFF).contains(value) {
            let text = String(Unicode.Scalar(value)!)
            if regex.firstMatch(in: text, range: NSRange(location: 0, length: (text as NSString).length)) != nil {
                members.append(String(value))
            }
        }
        let digest = SHA256.hash(data: Data(members.joined(separator: ",").utf8)).map { String(format: "%02x", $0) }.joined()
        return (members.count, digest)
    }

    func testWordSetIsPython312() {
        let (count, digest) = fingerprint(Scrubber.pythonWordSet)
        XCTAssertEqual(count, 137_936)
        XCTAssertEqual(digest, "5e2c3a1556af4de7938b53868be13dc4d3ecb49af895e79d0d8ede573431b3dd")
    }

    func testDigitSetIsPython312() {
        let (count, digest) = fingerprint(Scrubber.pythonDigitSet)
        XCTAssertEqual(count, 680)
        XCTAssertEqual(digest, "5f61be82102b086e3163808c62247ce8a3bffcf95cf566f661b040196ce81225")
    }

    /// The reviewer's case: a key followed by a Unicode 16 letter is not a
    /// word boundary in Swift's ICU but is one in Python 3.12.
    func testAKeyNextToANewerLetterIsRedacted() {
        XCTAssertEqual(Scrubber.scrub("AKIAIOSFODNN7EXAMPLE\u{88F}"), "[SECRET:aws_key]\u{88F}")
    }
}
