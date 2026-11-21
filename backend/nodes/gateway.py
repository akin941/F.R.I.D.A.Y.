"""The node gateway's per-socket session for protocol v2 actions (Phase 10
brief D7, D12; Phase 9 brief D6).

`NodeSession` is the transport-agnostic core of one live node socket: the
socket app (Phase 9 branch 4, `feat/p9-node-gateway`: handshake, the inbox
`BLPOP` loop, revocation fan-out) feeds it frames and calls it from the
event loop through `asgiref.sync.sync_to_async(thread_sensitive=True)`;
the tests drive it in process against the reference node. It never blocks
and never retries.

- `hello(frame)` records the `CapabilitySnapshot` for the session's
  negotiated version, marks the node present and answers `hello.ack`;
  `heartbeat(frame)` refreshes presence and answers `heartbeat.ack`.
- `pull_invokes()` drains the node's inbox: an envelope for another node is
  dropped and its execution ends `server_refused:node_mismatch`; an
  envelope that belongs to no in-flight execution is dropped and counted;
  every other one joins **this socket's** in-flight set and is returned for
  forwarding.
- `receive(frame)` accepts `confirmation`, `output` and `result` frames
  only for invocations in this socket's in-flight set (per-socket routing):
  a frame for another socket's, an unknown or a finished invocation is
  discarded and counted (`discarded`), never applied. `confirmation` moves
  the status (`shown` -> confirming, `approved` -> running); `output` is
  counted into `output_bytes` and handed to the invocation's
  `nodes.output.OutputTransit`: sanitised and scrubbed through its
  `StreamScrubber` (carry-over, D12), and only the emitted text is
  appended to the Redis stream `nodes:output:<invocation_id>` (branch 5;
  the raw carry never is); `result` flushes the carry and ends the
  execution through `nodes.services.record_gated_progress`, which
  re-scrubs the `tail`, then writes the stream's `end` entry.
- `tick(now)`: one watchdog per invocation, deadline = `expires_at` + the
  contract's `max_runtime_seconds` + 30 s -> `timeout`.
- `disconnect()`: every in-flight invocation ends `node_disconnected` (the
  carry is scrubbed and flushed to the stream first, then its `end`
  entry), presence is cleared. Nothing is
  retried (ADR-013 decision 6).

Frame bodies are never logged: ids, types, sizes and codes only.
"""

import logging
import time
from dataclasses import dataclass, field

from nodes import bus, services
from nodes.output import OutputStreamWriter, OutputTransit, scrubber_for, utf8_len
from nodes.protocol.messages import ProtocolError, catalogue_sha256, validate

logger = logging.getLogger(__name__)

ACTION_FRAME_TYPES = frozenset({"confirmation", "output", "result"})


@dataclass
class InFlight:
    invocation_id: str
    execution_id: object
    deadline: int | None
    transit: OutputTransit
    output_bytes: int = 0

    @property
    def emitted_bytes(self) -> int:
        """Scrubbed bytes handed to the stream (a count, never the text)."""
        return self.transit.emitted_bytes


@dataclass
class NodeSession:
    node: object
    version: int
    clock: object = None
    in_flight: dict = field(default_factory=dict)
    discarded: int = 0
    frame_errors: int = 0
    closed: bool = False

    def __post_init__(self):
        if self.version not in (1, 2):
            raise ValueError("unsupported protocol version")
        self.clock = self.clock or (lambda: int(time.time()))
        self.node_id = str(self.node.pk)

    # --- session ---------------------------------------------------------

    def hello(self, frame) -> dict:
        """Record the advertised catalogue and mark the node present.
        Returns the accepted (invocable) entry names and the snapshot; the
        socket app builds `hello.ack` from them (Phase 9 branch 4)."""
        validate(frame, "node_to_server", self.version)
        snapshot = services.record_capability_snapshot(
            node=self.node,
            catalogue=frame["catalogue"],
            catalogue_sha256=catalogue_sha256(frame["catalogue"]),
            protocol_version=self.version,
            node_version=frame.get("node_version", ""),
            os_version=frame.get("os_version", ""),
        )
        bus.mark_present(self.node_id)
        return {
            "accepted": [entry["name"] for entry in snapshot.entries if entry.get("invocable")],
            "snapshot": snapshot,
        }

    def heartbeat(self, frame) -> None:
        validate(frame, "node_to_server", self.version)
        bus.mark_present(self.node_id)

    # --- inbox -----------------------------------------------------------

    def pull_invokes(self) -> list:
        """The envelopes to forward to this node now (see module doc)."""
        if self.closed:
            return []
        forward = []
        for envelope in bus.drain_inbox(self.node_id):
            invocation_id = envelope.get("invocation_id")
            if str(envelope.get("node_id")) != self.node_id:
                services.refuse_misrouted(invocation_id)
                self.discarded += 1
                logger.warning("node gateway dropped a misrouted envelope node=%s", self.node_id)
                continue
            execution = services.execution_for_session(
                node_id=self.node_id, invocation_id=invocation_id
            )
            if execution is None:
                self.discarded += 1
                continue
            scrub = scrubber_for(execution.workspace)
            self.in_flight[str(invocation_id)] = InFlight(
                invocation_id=str(invocation_id),
                execution_id=execution.pk,
                deadline=services.watchdog_deadline(execution),
                transit=OutputTransit(scrub, OutputStreamWriter(str(invocation_id))),
            )
            forward.append(envelope)
        return forward

    # --- frames from the node --------------------------------------------

    def receive(self, frame) -> str | None:
        """Apply one node frame. Returns what happened (`applied`,
        `discarded`, `ignored`, `frame_error`), never raises for a hostile
        frame."""
        if self.closed:
            return "ignored"
        try:
            validate(frame, "node_to_server", self.version)
        except ProtocolError:
            self.frame_errors += 1
            return "frame_error"
        kind = frame["type"]
        if kind not in ACTION_FRAME_TYPES:
            return "ignored"
        invocation_id = frame.get("invocation_id")
        flight = self.in_flight.get(str(invocation_id)) if invocation_id else None
        if flight is None:
            # Another socket's, an unknown or a finished invocation (or a
            # v1 read result, which the Phase 9 read path owns).
            self.discarded += 1
            return "discarded"
        if kind == "output":
            return self._output(flight, frame)
        if kind == "confirmation":
            moved = services.record_gated_progress(
                node_id=self.node_id,
                invocation_id=flight.invocation_id,
                confirmation=frame["state"],
            )
            if moved is None:
                self._forget(flight)
                self.discarded += 1
                return "discarded"
            return "applied"
        self._flush(flight)
        finished = services.record_gated_progress(
            node_id=self.node_id,
            invocation_id=flight.invocation_id,
            result=frame,
            output_bytes=flight.output_bytes,
        )
        flight.transit.end(finished)
        self._forget(flight)
        if finished is None:
            self.discarded += 1
            return "discarded"
        return "applied"

    def _output(self, flight, frame):
        data = frame["data"]
        flight.output_bytes += utf8_len(data)
        # Only the scrubber's emitted text reaches `nodes:output:<id>`.
        flight.transit.push(frame["stream"], data, truncated=frame.get("truncated"))
        return "applied"

    def _flush(self, flight):
        flight.transit.flush()

    def _forget(self, flight):
        self.in_flight.pop(flight.invocation_id, None)

    # --- the clock and the socket ----------------------------------------

    def tick(self, now=None) -> list:
        """The watchdog: in-flight invocations past their deadline end
        `timeout`. Returns their invocation ids."""
        now = int(now if now is not None else self.clock())
        timed_out = []
        for flight in list(self.in_flight.values()):
            if flight.deadline is not None and now > flight.deadline:
                self._flush(flight)
                finished = services.record_gated_progress(
                    node_id=self.node_id,
                    invocation_id=flight.invocation_id,
                    terminal="timeout",
                    output_bytes=flight.output_bytes,
                )
                flight.transit.end(finished)
                self._forget(flight)
                timed_out.append(flight.invocation_id)
        return timed_out

    def disconnect(self) -> None:
        """Fail closed: every in-flight invocation ends `node_disconnected`
        (carry scrubbed and flushed first); presence is cleared."""
        if self.closed:
            return
        self.closed = True
        for flight in list(self.in_flight.values()):
            self._flush(flight)
            finished = services.record_gated_progress(
                node_id=self.node_id,
                invocation_id=flight.invocation_id,
                terminal="node_disconnected",
                output_bytes=flight.output_bytes,
            )
            flight.transit.end(finished)
            self._forget(flight)
        try:
            bus.clear_presence(self.node_id)
        except bus.BusUnavailable:
            logger.warning("node gateway could not clear presence node=%s", self.node_id)
