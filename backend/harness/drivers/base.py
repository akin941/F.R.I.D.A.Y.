"""Driver framework (Phase 10 brief D10): the `AgentDriver` protocol, the
bounded JSONL line reader and the in-container sandbox policy table.

Everything a driver reads comes from an agent-controlled process inside the
container, so the reader is bounded (<= 1 MiB per line, <= 50 000 lines per
run) and every protocol fault is a `DriverError` with a deterministic
`code` the job maps to a `FailureReason` -- never a hang, never a silent
"proceed anyway".
"""

import json
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

MAX_LINE_BYTES = 1024 * 1024
MAX_LINES = 50_000

# DriverError codes (each maps to one HarnessImplementationJob.FailureReason
# in `harness.implementation_execution`).
CODE_OVERSIZE_LINE = "oversize_line"
CODE_MALFORMED_JSON = "malformed_json"
CODE_UNKNOWN_METHOD = "unknown_method"
CODE_EARLY_EXIT = "early_exit"
CODE_TOO_MANY_LINES = "too_many_lines"
CODE_PROTOCOL_ERROR = "protocol_error"
CODE_DEADLINE = "deadline"
CODE_TURN_FAILED = "turn_failed"
CODE_STOPPED = "stopped"
DRIVER_ERROR_CODES = (
    CODE_OVERSIZE_LINE,
    CODE_MALFORMED_JSON,
    CODE_UNKNOWN_METHOD,
    CODE_EARLY_EXIT,
    CODE_TOO_MANY_LINES,
    CODE_PROTOCOL_ERROR,
    CODE_DEADLINE,
    CODE_TURN_FAILED,
    CODE_STOPPED,
)

# The agent's own sandbox at its strictest unattended setting that still
# allows writes to the repo (D10 "Approval mapping"). Anything outside it is
# an escalation the agent must ask for, and every ask becomes one
# `harness_agent_action` approval. Never "never ask", never full access,
# never network.
SANDBOX_POLICY = {
    "codex_cli": {
        "sandbox": "workspace-write",
        "approval_policy": "on-request",
        "writable_roots": ("/workspace/repo",),
        "network_access": False,
    },
}

# Answers a driver may send to an escalation: one request only, never "for
# the session".
ACCEPT = "accept"
DECLINE = "decline"


class DriverError(Exception):
    """A driver run failed closed. `code` is one of `DRIVER_ERROR_CODES`."""

    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass
class TurnUsage:
    """Token usage of one turn. `reported` False when the turn ended without
    a usage report (the turn still gets a `success=False` ModelUsage row)."""

    turn_id: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_output_tokens: int = 0
    reported: bool = False
    status: str = ""


@dataclass
class Escalation:
    """One escalation the agent asked for, normalized across drivers.
    `command`, `cwd` and `reason` are agent-written: untrusted text."""

    request_id: Any
    kind: str  # command | file_change | tool
    command: str = ""
    cwd: str = ""
    reason: str = ""


@dataclass
class DriverEvent:
    """`kind` is `escalation` (payload: Escalation), `message` (payload: the
    agent's text), `turn_completed` (payload: TurnUsage) or `idle` (nothing
    arrived within the poll slice; lets the caller check its brakes)."""

    kind: str
    payload: Any = None


@dataclass
class DriverTranscript:
    """Bounded record of the agent's own messages, for `raw_transcript`."""

    max_chars: int = 50_000
    parts: list = field(default_factory=list)
    size: int = 0

    def add(self, text):
        if not isinstance(text, str) or not text or self.size >= self.max_chars:
            return
        room = self.max_chars - self.size
        chunk = text[:room]
        self.parts.append(chunk)
        self.size += len(chunk)

    def text(self):
        return "\n".join(self.parts)


class AgentDriver(Protocol):
    """D10 `AgentDriver`. `start` performs the handshake on an already
    launched interactive container; `run_turn` starts one turn; `events`
    yields `DriverEvent`s until the turn completes (raising `DriverError` on
    any protocol fault); `answer_approval` answers one escalation;
    `usage` is the per-turn usage so far; `close` ends the session."""

    agent: str
    name: str

    def start(self, container) -> None: ...

    def run_turn(self, goal: str) -> str: ...

    def events(self): ...

    def answer_approval(self, request_id, accept: bool) -> None: ...

    def usage(self) -> list[TurnUsage]: ...

    def close(self) -> None: ...


_EOF = object()
_OVERSIZE = object()


class JsonlReader:
    """Bounded newline-delimited JSON reader over a binary stream.

    A daemon thread pumps lines into a queue so the caller can wait with a
    timeout (the driver's deadline and cancel polling) without blocking on a
    pipe the agent controls. Bounds: a line longer than `max_line_bytes` ->
    `oversize_line` (the pump stops reading); more than `max_lines` lines ->
    `too_many_lines`; EOF -> `early_exit` unless the caller expected it.
    `discard_rest()` keeps draining (and dropping) whatever the container
    still prints after the protocol phase, so the wrapper's test output can
    never block on a full pipe."""

    def __init__(self, stream, *, max_line_bytes=MAX_LINE_BYTES, max_lines=MAX_LINES):
        self._stream = stream
        self._max_line_bytes = max_line_bytes
        self._max_lines = max_lines
        self._queue = queue.Queue()
        self._discard = threading.Event()
        self._lines = 0
        self._thread = threading.Thread(target=self._pump, name="driver-jsonl", daemon=True)
        self._thread.start()

    def _pump(self):
        try:
            while True:
                line = self._stream.readline(self._max_line_bytes + 1)
                if not line:
                    self._queue.put(_EOF)
                    return
                if self._discard.is_set():
                    continue
                # `readline(max + 1)` returns at most max + 1 bytes, so a body
                # over the bound is exactly a line with no newline in reach.
                body = line[:-1] if line.endswith(b"\n") else line
                if len(body) > self._max_line_bytes:
                    # Stop reading: the run fails closed and is torn down.
                    self._queue.put(_OVERSIZE)
                    return
                self._queue.put(body)
        except (OSError, ValueError):
            self._queue.put(_EOF)

    def next_message(self, timeout):
        """The next parsed JSON object, or None if nothing arrived within
        `timeout` seconds. Blank lines are skipped (but counted)."""
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            remaining = deadline - time.monotonic()
            try:
                item = self._queue.get(timeout=max(0.0, remaining))
            except queue.Empty:
                return None
            if item is _EOF:
                # Put it back: every later read sees the same EOF.
                self._queue.put(_EOF)
                raise DriverError(CODE_EARLY_EXIT, "agent stdout closed")
            if item is _OVERSIZE:
                raise DriverError(CODE_OVERSIZE_LINE, f"line over {self._max_line_bytes} bytes")
            self._lines += 1
            if self._lines > self._max_lines:
                raise DriverError(CODE_TOO_MANY_LINES, f"more than {self._max_lines} lines")
            if not item.strip():
                continue
            try:
                message = json.loads(item.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise DriverError(CODE_MALFORMED_JSON, type(exc).__name__) from None
            if not isinstance(message, dict):
                raise DriverError(CODE_MALFORMED_JSON, "frame is not a JSON object")
            return message

    def discard_rest(self):
        self._discard.set()
