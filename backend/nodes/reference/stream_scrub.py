"""Node-side streaming scrub with carry-over, and the output pipeline
(Phase 10 brief D12, D1 `output` frames).

A secret can straddle two output chunks (cut anywhere, even mid-line or
mid-UTF-8 sequence), so no chunk is scrubbed in isolation. Per stream the
node keeps a raw **carry** that is never emitted, stored or published.
`StreamScrubber.push(text)` is the reference algorithm the Swift
`OutputStreamer` (Mac session M1) and the server's `nodes.output.
StreamScrubber` (branch 3) must reproduce; `conformance/vectors/
scrub_stream.json` pins its output byte for byte.

The algorithm, for one pushed piece of text (already decoded from UTF-8 by
an incremental decoder, so no cut can ever split a UTF-8 sequence):

1. `buffer = carry + text`.
2. `c0` is the end of the last complete line in `buffer` (just after its
   last `\\n`; 0 when there is none). If the remainder after `c0` is longer
   than `SCRUB_CARRY_BYTES` (UTF-8 bytes), `c0` moves forward to the
   smallest index whose remainder fits in `SCRUB_CARRY_BYTES`.
3. A cut `c` is **scrub-neutral** when `scrub(buffer[:c]) +
   scrub(buffer[c:]) == scrub(buffer)`: cutting there loses nothing compared
   with scrubbing the whole buffer, so no match crosses it (a secret that
   only part of which is matched on one side, or that a rule only
   recognises whole, makes the two differ), **and** `scrub(buffer[:c])`
   does not end in a placeholder (trailing whitespace aside): a match that
   touches the cut may continue in data not received yet, such as the next
   line of a PEM block. The candidates, in order, are
   `c0`, then every index `i < c0` (descending) whose preceding character is
   whitespace (`" "`, `"\\t"`, `"\\n"`, `"\\r"`) and whose remainder fits in
   the carry bound `SCRUB_CARRY_BYTES + MAX_OUTPUT_CHUNK_BYTES`, at most
   `MAX_CUT_CANDIDATES` in total, then `0` when the whole buffer fits in that
   bound. The first scrub-neutral candidate is the cut (`0` always is: it
   emits nothing). If none is, the cut is forced to `c0` (a single match
   longer than the carry bound; the rules still match its parts).
4. `scrub(buffer[:cut])` is emitted; the raw `buffer[cut:]` becomes the new
   carry.
5. `flush()` (the final result, socket loss, the runtime cap) scrubs and
   emits the carry.

The carry is therefore always at most `SCRUB_CARRY_BYTES +
MAX_OUTPUT_CHUNK_BYTES` bytes and lives only in memory.

`OutputPipeline` turns raw process bytes into `output` frame payloads: one
incremental decoder and one `StreamScrubber` per stream, data cut into
pieces of at most 8 KiB (never inside a code point), a shared `seq`, the
1 MiB raw-output cap (then `truncated`), and the scrubbed 4 KiB `tail`.
"""

import codecs
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from nodes.protocol.messages import (
    MAX_OUTPUT_CHUNK_BYTES,
    MAX_OUTPUT_SEQ,
    MAX_TAIL_BYTES,
    OUTPUT_STREAMS,
)
from nodes.reference._scrubber_vendored import scrub_text

SCRUB_CARRY_BYTES = 1024
MAX_CUT_CANDIDATES = 64
MAX_OUTPUT_BYTES = 1024 * 1024
_WHITESPACE = frozenset(" \t\n\r")
# A scrubbed head that ends in a placeholder (trailing whitespace aside):
# the match may continue in data not yet received (a PEM block's next line,
# a long base64 run), so such a cut is never taken.
_PLACEHOLDER_AT_END = re.compile(r"\[(?:SECRET|PII|TERM):[a-z0-9_]+\]\s*\Z")


def default_scrub(terms=()) -> Callable[[str], str]:
    """The 8A scrubber (vendored), with optional `(id, literal)` terms. The
    reference node has no workspace terms (D13 item 6), so it uses none."""
    terms = tuple(terms)

    def scrub(text: str) -> str:
        return scrub_text(text, terms=terms).text if text else ""

    return scrub


def utf8_len(text: str) -> int:
    return len(text.encode("utf-8", "surrogatepass"))


def suffix_start(text: str, max_bytes: int) -> int:
    """The smallest index `i` such that `text[i:]` is at most `max_bytes`
    UTF-8 bytes."""
    size = 0
    index = len(text)
    while index > 0:
        width = utf8_len(text[index - 1])
        if size + width > max_bytes:
            break
        size += width
        index -= 1
    return index


def prefix_end(text: str, max_bytes: int) -> int:
    """The largest index `i` such that `text[:i]` is at most `max_bytes`
    UTF-8 bytes."""
    size = 0
    for index, char in enumerate(text):
        width = utf8_len(char)
        if size + width > max_bytes:
            return index
        size += width
    return len(text)


class StreamScrubber:
    """One stream's streaming scrub with a raw carry (see module docstring)."""

    def __init__(
        self,
        scrub: Callable[[str], str] | None = None,
        *,
        carry_bytes: int = SCRUB_CARRY_BYTES,
        max_chunk_bytes: int = MAX_OUTPUT_CHUNK_BYTES,
        max_candidates: int = MAX_CUT_CANDIDATES,
    ):
        self._scrub = scrub or default_scrub()
        self.carry_bytes = carry_bytes
        self.bound_bytes = carry_bytes + max_chunk_bytes
        self.max_candidates = max_candidates
        self._carry = ""

    @property
    def carry_size(self) -> int:
        """The carry's size in UTF-8 bytes (never its content)."""
        return utf8_len(self._carry)

    def _candidates(self, buffer: str):
        newline = buffer.rfind("\n")
        c0 = newline + 1 if newline >= 0 else 0
        c0 = max(c0, suffix_start(buffer, self.carry_bytes))
        lo = suffix_start(buffer, self.bound_bytes)
        candidates = [c0]
        index = c0 - 1
        while index >= max(lo, 1) and len(candidates) < self.max_candidates:
            if buffer[index - 1] in _WHITESPACE:
                candidates.append(index)
            index -= 1
        if lo == 0 and candidates[-1] != 0:
            candidates.append(0)
        return c0, candidates

    def push(self, text: str) -> str:
        """Add `text`; return the scrubbed text that may be emitted now."""
        buffer = self._carry + (text or "")
        if not buffer:
            return ""
        c0, candidates = self._candidates(buffer)
        whole = self._scrub(buffer)
        cut = c0
        for candidate in candidates:
            if candidate == 0:
                cut = 0
                break
            head = self._scrub(buffer[:candidate])
            if _PLACEHOLDER_AT_END.search(head):
                continue  # a match touching the cut may still grow
            if head + self._scrub(buffer[candidate:]) == whole:
                cut = candidate
                break
        emitted = self._scrub(buffer[:cut]) if cut else ""
        self._carry = buffer[cut:]
        return emitted

    def flush(self) -> str:
        """Scrub and emit the carry; the scrubber is empty afterwards."""
        carry, self._carry = self._carry, ""
        return self._scrub(carry)


def split_utf8(text: str, max_bytes: int = MAX_OUTPUT_CHUNK_BYTES):
    """`text` in pieces of at most `max_bytes` UTF-8 bytes, never inside a
    code point."""
    pieces = []
    while text:
        end = prefix_end(text, max_bytes) or 1
        pieces.append(text[:end])
        text = text[end:]
    return pieces


@dataclass
class OutputPiece:
    """The payload of one `output` frame (`seq`, `stream`, `data`,
    `truncated`); the node wraps it in a frame."""

    seq: int
    stream: str
    data: str
    truncated: bool


@dataclass
class OutputPipeline:
    """Raw process bytes in, scrubbed `output` payloads out (D1, D12)."""

    scrub: Callable[[str], str] = field(default_factory=default_scrub)
    max_output_bytes: int = MAX_OUTPUT_BYTES
    max_chunk_bytes: int = MAX_OUTPUT_CHUNK_BYTES
    carry_bytes: int = SCRUB_CARRY_BYTES

    def __post_init__(self):
        self.output_bytes = 0
        self.truncated = False
        self._seq = 0
        self._tail = ""
        self._decoders = {
            stream: codecs.getincrementaldecoder("utf-8")("replace") for stream in OUTPUT_STREAMS
        }
        self._scrubbers = {
            stream: StreamScrubber(
                self.scrub, carry_bytes=self.carry_bytes, max_chunk_bytes=self.max_chunk_bytes
            )
            for stream in OUTPUT_STREAMS
        }

    @property
    def tail(self) -> str:
        """The last <= 4 KiB of scrubbed, emitted text (both streams)."""
        return self._tail

    def _emit(self, stream, text):
        pieces = []
        if not text:
            return pieces
        self._tail = self._tail + text
        self._tail = self._tail[suffix_start(self._tail, MAX_TAIL_BYTES) :]
        for data in split_utf8(text, self.max_chunk_bytes):
            if self._seq > MAX_OUTPUT_SEQ:
                self.truncated = True
                break
            pieces.append(OutputPiece(self._seq, stream, data, self.truncated))
            self._seq += 1
        return pieces

    def feed(self, stream: str, raw: bytes):
        """Raw bytes of one stream; returns the payloads to send now. Bytes
        past the 1 MiB cap are counted but dropped (`truncated`)."""
        if stream not in OUTPUT_STREAMS:
            raise ValueError(f"unknown stream {stream!r}")
        room = max(self.max_output_bytes - self.output_bytes, 0)
        self.output_bytes += len(raw)
        if len(raw) > room:
            self.truncated = True
            raw = raw[:room]
        if not raw:
            return []
        text = self._decoders[stream].decode(raw)
        return self._emit(stream, self._scrubbers[stream].push(text))

    def finish(self):
        """Flush both decoders and carries (final result, socket loss or the
        runtime cap); returns the last payloads."""
        pieces = []
        for stream in OUTPUT_STREAMS:
            text = self._decoders[stream].decode(b"", final=True)
            emitted = self._scrubbers[stream].push(text) if text else ""
            emitted += self._scrubbers[stream].flush()
            pieces.extend(self._emit(stream, emitted))
        return pieces
