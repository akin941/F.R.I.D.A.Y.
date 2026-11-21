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

Until branch 5 (`feat/p10-hud-terminal`) adds the Redis output stream, the
gateway scrubs each `output` frame through a `StreamScrubber`, counts it
into `output_bytes` and drops it: nothing is stored or published.
"""

import logging
from collections.abc import Callable

from nodes.protocol.messages import MAX_TAIL_BYTES
from nodes.reference.stream_scrub import (
    SCRUB_CARRY_BYTES,
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


def _safe(scrub, text: str) -> str:
    try:
        result = scrub(text)
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


__all__ = [
    "FAIL_CLOSED_MARKER",
    "SCRUB_CARRY_BYTES",
    "StreamScrubber",
    "bound_tail",
    "register_scrubber",
    "reset_scrubber",
    "scrub_tail",
    "scrubber_for",
    "scrubber_registered",
    "utf8_len",
]
