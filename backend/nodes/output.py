"""Node output scrubbing on the server (Phase 10 brief D7, D12).

The node scrubs its output before it sends it (D5, M1), and the server never
trusts that: every `output` chunk and every `tail` passes through the
**registered output scrubber** with the target workspace's `ScrubTerm`s
before it is used or stored.

- `register_scrubber(factory)`: the composition root (`config.asgi_nodes`,
  the node gateway's process, and only there -- a source test pins it) hands
  in `factory(workspace) -> scrub(text) -> text`, the 8A scrubber bound to
  that workspace's terms. `nodes` imports neither `memory` nor `config`
  (import contract), hence the port (the Phase 9 D10 injection pattern).
- **Fail closed.** With no scrubber registered, or a scrubber that raises,
  the text is replaced by `FAIL_CLOSED_MARKER` ("[çıktı gizlendi]"): nothing
  unscrubbed is ever stored or published.
- `StreamScrubber`: one per invocation and stream, the D12 carry-over
  algorithm. The algorithm itself is the reference node's
  (`nodes.reference.stream_scrub.StreamScrubber`, the executable spec the
  Swift `OutputStreamer` also reproduces), driven here by the registered
  scrubber, so `conformance/vectors/scrub_stream.json` holds for the server
  too (a test replays it). A secret split across two chunks, even mid-line
  or mid-UTF-8 sequence, is never emitted unscrubbed; the raw carry lives
  only in this object and is scrubbed at `flush()` (final result, socket
  loss, watchdog).
- `scrub_tail(tail, workspace)`: the whole tail (<= 4 096 bytes, complete,
  so no chunk boundary applies) through the scrubber before
  `record_gated_progress` saves it.

Branch 5 (`feat/p10-hud-terminal`) adds the transit (D12):

- `sanitize_terminal_text(text)`: ANSI CSI / OSC / other escape sequences,
  C0 and C1 control characters (but `\\n` and `\\t`; a `\\r` becomes
  `\\n`), bidi controls, zero-width and every other Unicode format (`Cf`)
  character are removed. It runs **before** the scrubber, inside the scrub
  function the carry algorithm drives, so a secret interleaved with escape
  codes is joined before it is matched, and a cut through an escape
  sequence is never scrub-neutral (the carry algorithm then picks another).
  The same holds for `scrub_tail`.
- `OutputStreamWriter`: appends scrubbed text to the Redis stream
  `nodes:output:<invocation_id>` (`XADD ... MAXLEN 2000`, at most 1 MiB of
  text per invocation, then `truncated`; the key expires 15 minutes after
  the last write) and, at the end, one `end` entry with the terminal
  status. Redis down = the stream stops; the execution is never failed for
  it and nothing is buffered elsewhere.
- `OutputTransit`: one invocation's `StreamScrubber` per stream plus its
  writer. The gateway hands it every `output` frame; only the scrubber's
  emitted text is appended, never the raw carry. No scrubber registered:
  one `FAIL_CLOSED_MARKER` per stream and nothing else.
- `read_output(invocation_id, after=...)`: the HUD's reader (`XREAD`, never
  blocking).

Output is never written to Postgres except the scrubbed `tail`.
"""

import logging
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field

from nodes import bus
from nodes.protocol.messages import MAX_TAIL_BYTES, OUTPUT_STREAMS
from nodes.reference.stream_scrub import (
    SCRUB_CARRY_BYTES,
    prefix_end,
    suffix_start,
    utf8_len,
)
from nodes.reference.stream_scrub import StreamScrubber as _CarryScrubber

logger = logging.getLogger(__name__)

FAIL_CLOSED_MARKER = "[çıktı gizlendi]"
# A ScrubTerm is at most 200 characters (memory.ingestion.services); the
# carry must hold at least two of them (D12, checked at boot by
# `nodes.checks`).
SCRUB_TERM_MAX_CHARS = 200

_factory: Callable | None = None


def register_scrubber(factory: Callable) -> None:
    """Install the output scrubber factory: `factory(workspace)` returns a
    `scrub(text) -> text` for that workspace's terms. Called by
    `config.asgi_nodes` only."""
    global _factory
    if not callable(factory):
        raise TypeError("the output scrubber factory must be callable")
    _factory = factory


def reset_scrubber() -> None:
    """Test-only: back to the unregistered (fail-closed) state."""
    global _factory
    _factory = None


def scrubber_registered() -> bool:
    return _factory is not None


def scrubber_for(workspace) -> Callable[[str], str] | None:
    """The registered scrubber bound to `workspace`, or None (fail closed).
    A factory that raises counts as unregistered."""
    if _factory is None:
        return None
    try:
        scrub = _factory(workspace)
    except Exception:
        logger.warning("node output scrubber factory failed; failing closed")
        return None
    return scrub if callable(scrub) else None


# --- terminal text sanitation (D12) -------------------------------------

# CSI (`ESC [ ... final`), OSC (`ESC ] ... BEL|ST`, also unterminated up to
# the next ESC or the end), DCS/SOS/PM/APC strings (`ESC P|X|^|_ ... ST`),
# any other escape (`ESC` + intermediates + final), and the 8-bit CSI/OSC.
_ESCAPE_SEQUENCE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?"
    r"|\x1b[PX^_][^\x1b]*(?:\x1b\\)?"
    r"|\x1b[ -/]*[0-~]"
    r"|\x9b[0-?]*[ -/]*[@-~]"
    r"|\x9d[^\x07\x1b\x9c]*(?:\x07|\x9c)?"
)
_KEEP_CONTROLS = frozenset("\n\t")
# Cf: bidi controls, zero-width characters, BOM, ...; Zl/Zp: the Unicode
# line and paragraph separators; Co/Cs/Cn: private use, lone surrogates,
# unassigned code points.
_STRIPPED_CATEGORIES = frozenset({"Cf", "Zl", "Zp", "Co", "Cs", "Cn"})


def _is_stripped(char: str) -> bool:
    if char in _KEEP_CONTROLS:
        return False
    code = ord(char)
    if code < 0x20 or 0x7F <= code <= 0x9F:
        return True  # C0, DEL, C1 (a lone ESC included)
    return unicodedata.category(char) in _STRIPPED_CATEGORIES


def sanitize_terminal_text(text: str) -> str:
    """`text` as the terminal panel and the read tool may show it: escape
    sequences, control characters (but newline and tab), bidi, zero-width
    and other invisible formatting removed; `\\r\\n` and a lone `\\r` become
    `\\n` (no carriage-return overprinting)."""
    if not text:
        return ""
    text = _ESCAPE_SEQUENCE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(char for char in text if not _is_stripped(char))


def _safe(scrub, text: str) -> str:
    try:
        result = scrub(sanitize_terminal_text(text))
    except Exception:
        logger.warning("node output scrubber failed; failing closed")
        return FAIL_CLOSED_MARKER
    return result if isinstance(result, str) else FAIL_CLOSED_MARKER


def bound_tail(text: str) -> str:
    """The last <= 4 096 UTF-8 bytes of `text`."""
    text = text or ""
    return text[suffix_start(text, MAX_TAIL_BYTES) :]


def scrub_tail(tail, workspace=None, *, scrub=None) -> str:
    """The `tail` as the server stores it: bounded, then scrubbed with the
    registered scrubber (or `scrub`), or the fail-closed marker."""
    if not tail:
        return ""
    scrub = scrub or scrubber_for(workspace)
    if scrub is None:
        return FAIL_CLOSED_MARKER
    return bound_tail(_safe(scrub, bound_tail(str(tail))))


class StreamScrubber:
    """The server's per-invocation, per-stream streaming scrub (D12).

    `scrub` is the workspace-bound scrubber (`scrubber_for(workspace)`).
    `push(text)` returns the scrubbed text that may be emitted now (the raw
    remainder stays in the carry); `flush()` scrubs and returns the carry.
    With no scrubber (`scrub=None`) every non-empty push yields
    `FAIL_CLOSED_MARKER` and nothing is carried."""

    def __init__(self, scrub=None, *, carry_bytes=SCRUB_CARRY_BYTES):
        self._scrub = scrub
        self._inner = None
        if self._scrub is not None:
            scrub_fn = self._scrub
            self._inner = _CarryScrubber(
                lambda text: _safe(scrub_fn, text), carry_bytes=carry_bytes
            )
        self.bound_bytes = self._inner.bound_bytes if self._inner is not None else carry_bytes
        self.max_carry_seen = 0

    @property
    def fail_closed(self) -> bool:
        return self._inner is None

    @property
    def carry_size(self) -> int:
        """The carry's size in UTF-8 bytes (never its content)."""
        return self._inner.carry_size if self._inner is not None else 0

    def push(self, text: str) -> str:
        if self._inner is None:
            return FAIL_CLOSED_MARKER if text else ""
        emitted = self._inner.push(text or "")
        self.max_carry_seen = max(self.max_carry_seen, self._inner.carry_size)
        return emitted

    def flush(self) -> str:
        if self._inner is None:
            return ""
        return self._inner.flush()


# --- the Redis output stream (D12, branch 5) -----------------------------

OUTPUT_STREAM_MAXLEN = 2000
OUTPUT_STREAM_MAX_BYTES = 1024 * 1024
OUTPUT_STREAM_TTL_SECONDS = 15 * 60
OUTPUT_READ_COUNT = 200
ENTRY_CHUNK = "chunk"
ENTRY_END = "end"
_STREAM_ID = re.compile(r"^\d{1,20}-\d{1,20}$")


def output_stream_key(invocation_id) -> str:
    return f"nodes:output:{invocation_id}"


def valid_stream_id(value) -> bool:
    return isinstance(value, str) and bool(_STREAM_ID.match(value))


@dataclass
class OutputStreamWriter:
    """Appends one invocation's scrubbed output to its Redis stream.

    Never raises for Redis: on the first `BusUnavailable` the writer is
    `broken` and appends nothing more (the execution itself is unaffected;
    the `tail` in Postgres stays the record). Logs ids and sizes only."""

    invocation_id: str
    max_bytes: int = OUTPUT_STREAM_MAX_BYTES
    maxlen: int = OUTPUT_STREAM_MAXLEN
    ttl_seconds: int = OUTPUT_STREAM_TTL_SECONDS
    appended_bytes: int = 0
    seq: int = 0
    truncated: bool = False
    broken: bool = False
    ended: bool = False

    @property
    def key(self) -> str:
        return output_stream_key(self.invocation_id)

    def _xadd(self, fields: dict) -> bool:
        key, maxlen, ttl = self.key, int(self.maxlen), int(self.ttl_seconds)

        def operation(client):
            pipe = client.pipeline(transaction=True)
            pipe.xadd(key, fields, maxlen=maxlen, approximate=False)
            pipe.expire(key, ttl)
            pipe.execute()

        try:
            bus._run(operation)
        except bus.BusUnavailable:
            self.broken = True
            logger.warning(
                "node output stream unavailable invocation=%s appended=%d",
                self.invocation_id,
                self.appended_bytes,
            )
            return False
        return True

    def append(self, stream: str, text: str) -> bool:
        """Append scrubbed `text` of `stream`; past the byte cap the rest is
        dropped and the stream is `truncated`. Returns whether anything was
        written."""
        if self.broken or self.ended or not text or stream not in OUTPUT_STREAMS:
            return False
        room = max(int(self.max_bytes) - self.appended_bytes, 0)
        if utf8_len(text) > room:
            self.truncated = True
            text = text[: prefix_end(text, room)]
        if not text:
            return False
        if self.seq >= int(self.maxlen):
            # From here on MAXLEN trims the oldest entries.
            self.truncated = True
        fields = {"kind": ENTRY_CHUNK, "seq": self.seq, "stream": stream, "text": text}
        if not self._xadd(fields):
            return False
        self.seq += 1
        self.appended_bytes += utf8_len(text)
        return True

    def end(self, *, status, exit_code=None, truncated=False) -> bool:
        """The final entry: `kind=end`, the terminal status, exit code and
        whether anything was cut."""
        if self.broken or self.ended:
            return False
        self.ended = True
        self.truncated = self.truncated or bool(truncated)
        fields = {
            "kind": ENTRY_END,
            "status": str(status or "")[:32],
            "exit_code": "" if exit_code is None else str(int(exit_code)),
            "truncated": "1" if self.truncated else "0",
        }
        return self._xadd(fields)


@dataclass
class OutputTransit:
    """One invocation's output path in the gateway: per stream a
    `StreamScrubber` (the carry), then the writer. Only scrubbed, emitted
    text ever reaches `writer`; with no scrubber (`scrub=None`) each stream
    gets one `FAIL_CLOSED_MARKER` and nothing else."""

    scrub: Callable | None
    writer: object
    scrubbers: dict = field(default_factory=dict)
    received_bytes: int = 0
    emitted_bytes: int = 0
    node_truncated: bool = False
    marked: set = field(default_factory=set)

    def __post_init__(self):
        if not self.scrubbers:
            self.scrubbers = {stream: StreamScrubber(self.scrub) for stream in OUTPUT_STREAMS}

    @property
    def truncated(self) -> bool:
        return self.node_truncated or bool(getattr(self.writer, "truncated", False))

    def _emit(self, stream, text):
        if not text:
            return
        if self.scrubbers[stream].fail_closed:
            if stream in self.marked:
                return
            self.marked.add(stream)
            text = FAIL_CLOSED_MARKER
        self.emitted_bytes += utf8_len(text)
        self.writer.append(stream, text)

    def push(self, stream: str, data: str, *, truncated=False) -> None:
        """One schema-valid `output` frame's `stream`, `data`, `truncated`."""
        self.received_bytes += utf8_len(data or "")
        self.node_truncated = self.node_truncated or bool(truncated)
        self._emit(stream, self.scrubbers[stream].push(data or ""))

    def flush(self) -> None:
        """Scrub and append both carries (result, socket loss, watchdog)."""
        for stream in OUTPUT_STREAMS:
            self._emit(stream, self.scrubbers[stream].flush())

    def end(self, execution) -> None:
        """The final stream entry, from the terminal execution row."""
        if execution is None:
            return
        self.writer.end(
            status=execution.status,
            exit_code=execution.exit_code,
            truncated=self.truncated or bool(getattr(execution, "truncated", False)),
        )


def _text(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return "" if value is None else str(value)


def read_output(invocation_id, *, after="0-0", count=OUTPUT_READ_COUNT) -> list:
    """The entries after stream id `after` (exclusive), oldest first, as
    dicts `{id, kind, ...}`; never blocks. Raises `bus.BusUnavailable`."""
    after = after if valid_stream_id(after) else "0-0"
    key = output_stream_key(invocation_id)
    response = bus._run(lambda client: client.xread({key: after}, count=int(count)))
    entries = []
    for _key, rows in response or []:
        for entry_id, fields in rows:
            item = {_text(k): _text(v) for k, v in (fields or {}).items()}
            item["id"] = _text(entry_id)
            entries.append(item)
    return entries


__all__ = [
    "ENTRY_CHUNK",
    "ENTRY_END",
    "FAIL_CLOSED_MARKER",
    "OUTPUT_STREAMS",
    "OUTPUT_STREAM_MAXLEN",
    "OUTPUT_STREAM_MAX_BYTES",
    "OUTPUT_STREAM_TTL_SECONDS",
    "SCRUB_CARRY_BYTES",
    "OutputStreamWriter",
    "OutputTransit",
    "StreamScrubber",
    "bound_tail",
    "output_stream_key",
    "read_output",
    "register_scrubber",
    "reset_scrubber",
    "sanitize_terminal_text",
    "scrub_tail",
    "scrubber_for",
    "scrubber_registered",
    "utf8_len",
    "valid_stream_id",
]
