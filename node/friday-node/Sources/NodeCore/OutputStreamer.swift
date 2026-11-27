// Node-side streaming scrub with carry-over, and the output pipeline (Phase
// 10 brief D12, D1 `output` frames). A port of
// backend/nodes/reference/stream_scrub.py; `ScrubStreamVectorTests`
// reproduces backend/nodes/conformance/vectors/scrub_stream.json piece for
// piece.
//
// A secret can straddle two output chunks (cut anywhere, even mid-line or
// mid-UTF-8 sequence), so no chunk is scrubbed in isolation. Per stream the
// node keeps a raw carry that is never emitted, stored or published. For
// one pushed piece of text (already decoded by an incremental UTF-8 decoder,
// so no cut ever splits a sequence):
//
// 1. `buffer = carry + text`;
// 2. `c0` is the end of the last complete line (after its last "\n"; 0 when
//    there is none), moved forward so that the remainder fits in
//    `carryBytes` UTF-8 bytes;
// 3. the cut is the first scrub-neutral candidate: `c0`, then every index
//    below it (descending) right after whitespace (" ", "\t", "\n", "\r")
//    whose remainder fits in `carryBytes + maxChunkBytes`, at most
//    `maxCandidates` in all, then 0 when the whole buffer fits in that
//    bound. A cut is scrub-neutral when `scrub(head) + scrub(tail) ==
//    scrub(buffer)` and `scrub(head)` does not end in a placeholder
//    (trailing whitespace aside: the match may still grow). No neutral
//    candidate forces the cut to `c0`;
// 4. `scrub(buffer[..<cut])` is emitted; the raw rest is the new carry;
// 5. `flush()` scrubs and emits the carry.
//
// Indices count Unicode scalars (Python str indices) and comparisons are
// scalar by scalar: Swift's String equality is canonical equivalence, which
// Python's is not.
//
// `OutputStreamer` turns raw process bytes into `output` payloads: one
// incremental decoder (U+FFFD replacement, as Python's "replace") and one
// `StreamScrubber` per stream, pieces of at most 8 KiB (never inside a code
// point), one shared `seq`, the 1 MiB raw-output cap (then `truncated`),
// and the scrubbed 4 KiB `tail`.

import Foundation

public typealias ScrubFunction = (String) -> String

enum UTF8Width {
    static func of(_ scalar: Unicode.Scalar) -> Int {
        switch scalar.value {
        case 0..<0x80: return 1
        case 0x80..<0x800: return 2
        case 0x800..<0x10000: return 3
        default: return 4
        }
    }

    static func of(_ scalars: ArraySlice<Unicode.Scalar>) -> Int { scalars.reduce(0) { $0 + of($1) } }

    /// The smallest index `i` such that `text[i...]` is at most `maxBytes`.
    static func suffixStart(_ text: [Unicode.Scalar], _ maxBytes: Int) -> Int {
        var size = 0
        var index = text.count
        while index > 0 {
            let width = of(text[index - 1])
            if size + width > maxBytes { break }
            size += width
            index -= 1
        }
        return index
    }

    /// The largest index `i` such that `text[..<i]` is at most `maxBytes`.
    static func prefixEnd(_ text: ArraySlice<Unicode.Scalar>, _ maxBytes: Int) -> Int {
        var size = 0
        for (offset, scalar) in text.enumerated() {
            let width = of(scalar)
            if size + width > maxBytes { return offset }
            size += width
        }
        return text.count
    }
}

extension Array where Element == Unicode.Scalar {
    var string: String { String(String.UnicodeScalarView(self)) }
}

extension ArraySlice where Element == Unicode.Scalar {
    var string: String { String(String.UnicodeScalarView(self)) }
}

/// One stream's streaming scrub with a raw carry (see the file header).
public final class StreamScrubber {
    public static let carryBytes = 1024
    public static let maxCutCandidates = 64
    private static let whitespace: Set<Unicode.Scalar> = [" ", "\t", "\n", "\r"]

    private let scrub: ScrubFunction
    public let carryBytes: Int
    public let boundBytes: Int
    public let maxCandidates: Int
    private var carry: [Unicode.Scalar] = []

    public init(
        scrub: @escaping ScrubFunction = Scrubber.scrub, carryBytes: Int = StreamScrubber.carryBytes,
        maxChunkBytes: Int = NodeProtocol.maxOutputChunkBytes, maxCandidates: Int = StreamScrubber.maxCutCandidates
    ) {
        self.scrub = scrub
        self.carryBytes = carryBytes
        boundBytes = carryBytes + maxChunkBytes
        self.maxCandidates = maxCandidates
    }

    /// The carry's size in UTF-8 bytes (never its content).
    public var carrySize: Int { UTF8Width.of(carry[...]) }

    private func scrubbed(_ text: ArraySlice<Unicode.Scalar>) -> [Unicode.Scalar] {
        text.isEmpty ? [] : Array(scrub(text.string).unicodeScalars)
    }

    private func candidates(_ buffer: [Unicode.Scalar]) -> (c0: Int, all: [Int]) {
        var c0 = (buffer.lastIndex(of: "\n").map { $0 + 1 }) ?? 0
        c0 = max(c0, UTF8Width.suffixStart(buffer, carryBytes))
        let lo = UTF8Width.suffixStart(buffer, boundBytes)
        var all = [c0]
        var index = c0 - 1
        while index >= max(lo, 1) && all.count < maxCandidates {
            if Self.whitespace.contains(buffer[index - 1]) { all.append(index) }
            index -= 1
        }
        if lo == 0 && all.last != 0 { all.append(0) }
        return (c0, all)
    }

    /// A scrubbed head that ends in a placeholder (Python `\s` aside):
    /// `\[(?:SECRET|PII|TERM):[a-z0-9_]+\]\s*\Z`.
    static func endsInPlaceholder(_ text: [Unicode.Scalar]) -> Bool {
        var end = text.count
        while end > 0, PythonChars.isSpace(text[end - 1]) { end -= 1 }
        guard end > 0, text[end - 1] == "]" else { return false }
        guard let open = text[..<(end - 1)].lastIndex(of: "[") else { return false }
        let inner = text[(open + 1)..<(end - 1)].string
        for kind in ["SECRET:", "PII:", "TERM:"] where inner.hasPrefix(kind) {
            let name = inner.unicodeScalars.dropFirst(kind.unicodeScalars.count)
            return !name.isEmpty && name.allSatisfy { ("a"..."z").contains($0) || ("0"..."9").contains($0) || $0 == "_" }
        }
        return false
    }

    /// Add `text`; return the scrubbed text that may be emitted now.
    public func push(_ text: String) -> String {
        let buffer = carry + Array(text.unicodeScalars)
        guard !buffer.isEmpty else { return "" }
        let (c0, all) = candidates(buffer)
        let whole = scrubbed(buffer[...])
        var cut = c0
        for candidate in all {
            if candidate == 0 {
                cut = 0
                break
            }
            let head = scrubbed(buffer[..<candidate])
            if Self.endsInPlaceholder(head) { continue }  // a match touching the cut may still grow
            if head + scrubbed(buffer[candidate...]) == whole {
                cut = candidate
                break
            }
        }
        let emitted = cut > 0 ? scrubbed(buffer[..<cut]) : []
        carry = Array(buffer[cut...])
        return emitted.string
    }

    /// Scrub and emit the carry; the scrubber is empty afterwards.
    public func flush() -> String {
        let rest = carry
        carry = []
        return scrubbed(rest[...]).string
    }
}

/// An incremental UTF-8 decoder with U+FFFD replacement (Python's
/// `codecs.getincrementaldecoder("utf-8")("replace")`): a trailing valid
/// prefix of a multi-byte sequence is held for the next call; everything
/// else is decoded, an ill-formed subpart becoming one U+FFFD.
public struct IncrementalUTF8Decoder {
    private var pending: [UInt8] = []

    public init() {}

    /// The number of bytes the sequence led by `lead` takes, or nil for a
    /// byte that cannot start one.
    private static func sequenceLength(_ lead: UInt8) -> Int? {
        switch lead {
        case 0xC2...0xDF: return 2
        case 0xE0...0xEF: return 3
        case 0xF0...0xF4: return 4
        default: return nil
        }
    }

    /// Whether `bytes` (a lead and fewer continuation bytes than the
    /// sequence needs) can still become a valid sequence.
    private static func isValidPrefix(_ bytes: ArraySlice<UInt8>) -> Bool {
        guard let lead = bytes.first, let length = sequenceLength(lead), bytes.count < length else { return false }
        for (offset, byte) in bytes.dropFirst().enumerated() {
            var range: ClosedRange<UInt8> = 0x80...0xBF
            if offset == 0 {
                switch lead {
                case 0xE0: range = 0xA0...0xBF
                case 0xED: range = 0x80...0x9F
                case 0xF0: range = 0x90...0xBF
                case 0xF4: range = 0x80...0x8F
                default: break
                }
            }
            if !range.contains(byte) { return false }
        }
        return true
    }

    public mutating func decode(_ data: Data, final: Bool = false) -> String {
        var bytes = pending + Array(data)
        pending = []
        if !final {
            let start = max(bytes.count - 3, 0)
            for index in stride(from: bytes.count - 1, through: start, by: -1) where index >= 0 {
                let byte = bytes[index]
                if byte & 0xC0 == 0x80 { continue }  // a continuation byte: keep looking for the lead
                if Self.isValidPrefix(bytes[index...]) {
                    pending = Array(bytes[index...])
                    bytes.removeSubrange(index...)
                }
                break
            }
        }
        return String(decoding: bytes, as: UTF8.self)
    }
}

/// The payload of one `output` frame (`seq`, `stream`, `data`,
/// `truncated`); the session wraps it in a frame.
public struct OutputPiece: Equatable {
    public let seq: Int64
    public let stream: String
    public let data: String
    public let truncated: Bool
}

/// Raw process bytes in, scrubbed `output` payloads out (D1, D12).
public final class OutputStreamer {
    public static let maxOutputBytes = 1024 * 1024

    public let maxOutputBytes: Int
    public let maxChunkBytes: Int
    public private(set) var outputBytes: Int64 = 0
    public private(set) var truncated = false
    private var seq: Int64 = 0
    private var tailScalars: [Unicode.Scalar] = []
    private var decoders: [String: IncrementalUTF8Decoder] = [:]
    private var scrubbers: [String: StreamScrubber] = [:]

    public init(
        scrub: @escaping ScrubFunction = Scrubber.scrub, maxOutputBytes: Int = OutputStreamer.maxOutputBytes,
        maxChunkBytes: Int = NodeProtocol.maxOutputChunkBytes, carryBytes: Int = StreamScrubber.carryBytes
    ) {
        self.maxOutputBytes = maxOutputBytes
        self.maxChunkBytes = maxChunkBytes
        for stream in NodeProtocol.outputStreams {
            decoders[stream] = IncrementalUTF8Decoder()
            scrubbers[stream] = StreamScrubber(scrub: scrub, carryBytes: carryBytes, maxChunkBytes: maxChunkBytes)
        }
    }

    /// The last <= 4 KiB of scrubbed, emitted text (both streams).
    public var tail: String { tailScalars.string }

    /// `text` in pieces of at most `maxBytes` UTF-8 bytes, never inside a
    /// code point.
    static func split(_ text: [Unicode.Scalar], maxBytes: Int) -> [[Unicode.Scalar]] {
        var pieces: [[Unicode.Scalar]] = []
        var rest = text[...]
        while !rest.isEmpty {
            let end = max(UTF8Width.prefixEnd(rest, maxBytes), 1)
            pieces.append(Array(rest.prefix(end)))
            rest = rest.dropFirst(end)
        }
        return pieces
    }

    private func emit(_ stream: String, _ text: String) -> [OutputPiece] {
        let scalars = Array(text.unicodeScalars)
        guard !scalars.isEmpty else { return [] }
        tailScalars += scalars
        tailScalars = Array(tailScalars[UTF8Width.suffixStart(tailScalars, NodeProtocol.maxTailBytes)...])
        var pieces: [OutputPiece] = []
        for data in Self.split(scalars, maxBytes: maxChunkBytes) {
            if seq > NodeProtocol.maxOutputSeq {
                truncated = true
                break
            }
            pieces.append(OutputPiece(seq: seq, stream: stream, data: data.string, truncated: truncated))
            seq += 1
        }
        return pieces
    }

    /// Raw bytes of one stream; returns the payloads to send now. Bytes past
    /// the 1 MiB cap are counted but dropped (`truncated`). An unknown
    /// stream yields nothing.
    public func feed(_ stream: String, _ raw: Data) -> [OutputPiece] {
        guard var decoder = decoders[stream], let scrubber = scrubbers[stream] else { return [] }
        let room = max(Int64(maxOutputBytes) - outputBytes, 0)
        outputBytes += Int64(raw.count)
        var raw = raw
        if Int64(raw.count) > room {
            truncated = true
            raw = raw.prefix(Int(room))
        }
        guard !raw.isEmpty else { return [] }
        let text = decoder.decode(raw)
        decoders[stream] = decoder
        return emit(stream, scrubber.push(text))
    }

    /// Flush both decoders and carries (final result, socket loss or the
    /// runtime cap); returns the last payloads.
    public func finish() -> [OutputPiece] {
        var pieces: [OutputPiece] = []
        for stream in NodeProtocol.outputStreams {
            guard var decoder = decoders[stream], let scrubber = scrubbers[stream] else { continue }
            let text = decoder.decode(Data(), final: true)
            decoders[stream] = decoder
            var emitted = text.isEmpty ? "" : scrubber.push(text)
            emitted += scrubber.flush()
            pieces += emit(stream, emitted)
        }
        return pieces
    }
}
