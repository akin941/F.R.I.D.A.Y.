// The D12 carry-over scrub and the output pipeline (Phase 10 brief D12;
// runbook B5 contract test 1): backend/nodes/conformance/vectors/
// scrub_stream.json reproduced piece for piece, scalar for scalar, plus the
// incremental UTF-8 decoder against CPython's.

import Foundation
import NodeCore
import XCTest

final class ScrubStreamVectorTests: XCTestCase {
    private func vectors() throws -> JSONValue {
        let doc = try Repo.json("backend/nodes/conformance/vectors/scrub_stream.json")
        XCTAssertEqual(doc["carry_bytes"]?.intValue, Int64(StreamScrubber.carryBytes))
        XCTAssertEqual(doc["max_chunk_bytes"]?.intValue, Int64(NodeProtocol.maxOutputChunkBytes))
        XCTAssertEqual(doc["max_cut_candidates"]?.intValue, Int64(StreamScrubber.maxCutCandidates))
        return doc
    }

    /// Scalar-for-scalar equality (Swift's String `==` is canonical
    /// equivalence; the vectors are exact).
    private func assertSame(_ actual: String, _ expected: String?, _ message: String) {
        XCTAssertEqual(Array(actual.unicodeScalars), Array((expected ?? "\u{0}").unicodeScalars), message)
    }

    func testEveryTextCaseEmitsTheReferencePieces() throws {
        let cases = try XCTUnwrap(try vectors()["text"]?.arrayValue)
        XCTAssertGreaterThanOrEqual(cases.count, 70)
        for item in cases {
            let name = item["name"]?.stringValue ?? "?"
            let scrubber = StreamScrubber()
            let chunks = try XCTUnwrap(item["chunks_b64"]?.arrayValue, name)
            let emitted = try XCTUnwrap(item["emitted"]?.arrayValue, name)
            XCTAssertEqual(chunks.count, emitted.count, name)
            for (index, (chunk, expected)) in zip(chunks, emitted).enumerated() {
                let data = try XCTUnwrap(Data(base64Encoded: chunk.stringValue ?? ""), name)
                let text = try XCTUnwrap(String(data: data, encoding: .utf8), name)
                assertSame(scrubber.push(text), expected.stringValue, "\(name) push \(index)")
            }
            assertSame(scrubber.flush(), item["flush"]?.stringValue, "\(name) flush")
            XCTAssertEqual(scrubber.carrySize, 0, name)
        }
    }

    func testEveryPipelineCaseEmitsTheReferencePieces() throws {
        let cases = try XCTUnwrap(try vectors()["pipeline"]?.arrayValue)
        XCTAssertGreaterThanOrEqual(cases.count, 3)
        for item in cases {
            let name = item["name"]?.stringValue ?? "?"
            let streamer = OutputStreamer()
            var pieces: [OutputPiece] = []
            for chunk in try XCTUnwrap(item["chunks"]?.arrayValue, name) {
                let data = try XCTUnwrap(Data(base64Encoded: chunk["data_b64"]?.stringValue ?? ""), name)
                pieces += streamer.feed(try XCTUnwrap(chunk["stream"]?.stringValue), data)
            }
            pieces += streamer.finish()
            let expected = try XCTUnwrap(item["pieces"]?.arrayValue, name)
            XCTAssertEqual(pieces.count, expected.count, name)
            for (piece, want) in zip(pieces, expected) {
                XCTAssertEqual(piece.seq, want["seq"]?.intValue, name)
                XCTAssertEqual(piece.stream, want["stream"]?.stringValue, name)
                XCTAssertEqual(piece.truncated, want["truncated"]?.boolValue, name)
                assertSame(piece.data, want["data"]?.stringValue, "\(name) seq \(piece.seq)")
            }
            XCTAssertEqual(streamer.outputBytes, item["output_bytes"]?.intValue, name)
            XCTAssertEqual(streamer.truncated, item["truncated"]?.boolValue, name)
            assertSame(streamer.tail, item["tail"]?.stringValue, "\(name) tail")
        }
    }

    /// A secret split across two chunks never reaches an emitted piece.
    func testASplitSecretIsStillRemoved() {
        let streamer = OutputStreamer()
        let secret = "sk-proj-Ab3dEf6hIj9kLm2nOp5qRs8tUv1wXy4zAB7CD0"
        var text = ""
        for piece in streamer.feed("stdout", Data("export OPENAI_API_KEY=\(secret.prefix(20))".utf8)) { text += piece.data }
        for piece in streamer.feed("stdout", Data("\(secret.dropFirst(20))\ndone\n".utf8)) { text += piece.data }
        for piece in streamer.finish() { text += piece.data }
        XCTAssertFalse(text.contains(secret))
        XCTAssertFalse(text.contains(String(secret.prefix(20))))
        XCTAssertTrue(text.hasSuffix("done\n"))
        XCTAssertFalse(streamer.tail.contains(String(secret.suffix(16))))
    }

    func testTheOneMebibyteCapTruncatesAndCountsEveryByte() {
        let streamer = OutputStreamer(scrub: { $0 })
        let line = Data(String(repeating: "x", count: 1023).utf8 + [0x0A])
        var pieces: [OutputPiece] = []
        for _ in 0..<1100 { pieces += streamer.feed("stdout", line) }
        pieces += streamer.finish()
        XCTAssertTrue(streamer.truncated)
        XCTAssertEqual(streamer.outputBytes, 1100 * 1024)
        XCTAssertEqual(pieces.reduce(0) { $0 + $1.data.utf8.count }, OutputStreamer.maxOutputBytes)
        XCTAssertTrue(pieces.allSatisfy { $0.data.utf8.count <= NodeProtocol.maxOutputChunkBytes })
        XCTAssertLessThanOrEqual(streamer.tail.utf8.count, NodeProtocol.maxTailBytes)
        XCTAssertEqual(pieces.map(\.seq), (0..<Int64(pieces.count)).map { $0 })
    }

    func testPiecesNeverSplitACodePoint() {
        let streamer = OutputStreamer(scrub: { $0 })
        let text = String(repeating: "ğ", count: 5000)  // 2 bytes each, no newline
        var pieces = streamer.feed("stdout", Data(text.utf8))
        pieces += streamer.finish()
        XCTAssertEqual(pieces.map(\.data).joined(), text)
        XCTAssertTrue(pieces.allSatisfy { $0.data.utf8.count <= NodeProtocol.maxOutputChunkBytes })
    }
}

/// The incremental decoder against CPython's
/// `codecs.getincrementaldecoder("utf-8")("replace")`, byte split by byte
/// split, over valid and ill-formed input.
final class IncrementalUTF8DecoderTests: XCTestCase {
    static let samples: [[UInt8]] = [
        Array("Testler geçti: çğıöşü ✓ 😀\n".utf8),
        [0x61, 0xE2, 0x82, 0x41, 0x62],  // truncated 3-byte sequence then ASCII
        [0xF0, 0x9F, 0x98],  // a truncated 4-byte sequence at the very end
        [0xED, 0xA0, 0x80, 0x61],  // an encoded surrogate
        [0xC0, 0xAF, 0xE0, 0x80, 0xAF],  // overlongs
        [0xF4, 0x90, 0x80, 0x80, 0x61],  // above U+10FFFF
        [0x80, 0xBF, 0xFF, 0xFE, 0x61],  // stray continuation and invalid bytes
        [0xE2, 0x82, 0xAC, 0xE2, 0x82],  // a complete euro sign and a truncated one
    ]

    private func swiftDecode(_ bytes: [UInt8], cut: Int) -> [UInt32] {
        var decoder = IncrementalUTF8Decoder()
        var text = decoder.decode(Data(bytes[..<cut]))
        text += decoder.decode(Data(bytes[cut...]))
        text += decoder.decode(Data(), final: true)
        return text.unicodeScalars.map(\.value)
    }

    func testEverySplitMatchesCPython() throws {
        let python = URL(fileURLWithPath: "/usr/bin/python3")
        guard FileManager.default.isExecutableFile(atPath: python.path) else {
            throw XCTSkip("python3 is not installed")
        }
        // One JSON document in, the code points of every (sample, cut) out.
        let input = try JSON.encode(.array(Self.samples.map { .array($0.map { .int(Int64($0)) }) }))
        let script = """
            import codecs, json, sys
            out = []
            for sample in json.loads(sys.stdin.read()):
                data = bytes(sample)
                row = []
                for cut in range(len(data) + 1):
                    d = codecs.getincrementaldecoder("utf-8")("replace")
                    text = d.decode(data[:cut]) + d.decode(data[cut:]) + d.decode(b"", final=True)
                    row.append([ord(c) for c in text])
                out.append(row)
            print(json.dumps(out))
            """
        let process = Process()
        process.executableURL = python
        process.arguments = ["-I", "-c", script]
        let stdin = Pipe()
        let stdout = Pipe()
        process.standardInput = stdin
        process.standardOutput = stdout
        try process.run()
        stdin.fileHandleForWriting.write(Data(input.utf8))
        try stdin.fileHandleForWriting.close()
        let data = stdout.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        let expected = try XCTUnwrap(try JSON.parse(data).arrayValue)
        for (sample, row) in zip(Self.samples, expected) {
            for (cut, want) in (row.arrayValue ?? []).enumerated() {
                let wanted = (want.arrayValue ?? []).compactMap(\.intValue).map(UInt32.init)
                XCTAssertEqual(swiftDecode(sample, cut: cut), wanted, "\(sample) cut at \(cut)")
            }
        }
    }
}
