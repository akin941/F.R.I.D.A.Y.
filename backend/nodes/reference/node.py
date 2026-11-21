"""`ReferenceNode`: the node side of one Friday Node session, protocol v1
and v2 (Phase 9 brief D5, D13; Phase 10 brief D1, D5, D12).

The node is a synchronous state machine, driven by three inputs:

- `receive(raw)`: one server -> node frame; returns the frames the node
  sends in answer;
- `tick()`: the node clock or the executor moved (a window decision is due,
  output arrived, a command finished or hit its runtime cap, the server went
  silent); returns the frames to send;
- `disconnect()`: the socket is gone. **Fail closed** (ADR-013 decision 6):
  the open window closes, nothing that was not yet confirmed runs, a running
  command is stopped (SIGTERM, then SIGKILL after 5 s), and no late frame is
  ever sent.

Verification order v2 (D5): checks 1-21 (`nodes.protocol.verify`), 22 (local
policy, `verifier_v2`), then for confirm and gate classes:

- 23: one window at a time (`busy_confirming`); `confirmation{shown}`; the
  owner approves, denies (`owner_denied`), or the window times out after
  60 s (`confirm_timeout`); each outcome is reported as a `confirmation`
  frame followed, for a refusal, by a refused `result`;
- 24: after the click, the envelope's (and so the grant's) `expires_at` must
  still be in the future and the kill switch released
  (`expired_after_confirm`); a lost socket runs nothing and sends nothing.

Only then does the executor run. `repo.command.run` streams `output` frames
(scrubbed node-side with carry-over) and ends with a `result` carrying
`exit_code`, `output_bytes`, the scrubbed `tail`, `truncated` and
`sandbox_violation`.

Frame errors (checks 1-4) answer a `result{status: error}` without an
invocation id and are counted; three within 60 s close the session
(`closed_reason = "protocol_error"`). A refusal never echoes the payload.
`bye{revoked|kill_switch|credential_expired}` stops reconnecting; server
silence beyond 45 s drops the socket and schedules a reconnect with
exponential backoff (1 s -> 60 s, full jitter).

Every frame the node sends is validated against the session version's
schema before it leaves (a bug here is a test failure, never a malformed
frame on the wire).
"""

import json
import os
import random
import shutil
import tempfile
import uuid
from collections import deque
from dataclasses import dataclass, field

from nodes.capabilities import get_any_contract, load_action_contracts, load_contracts
from nodes.protocol.canonical import sha256_hex
from nodes.protocol.messages import HEARTBEAT_SECONDS, catalogue_sha256, validate
from nodes.protocol.verify import NonceCache, Refusal
from nodes.reference.confirm import ConfirmationWindowDouble, WindowBusy, render_confirmation
from nodes.reference.executor import ActionRequest
from nodes.reference.sandbox import SandboxError, build_sandbox
from nodes.reference.stream_scrub import OutputPipeline, default_scrub
from nodes.reference.verifier_v2 import node_state, verify
from nodes.reference.worktree import WorktreeError, create_worktree, remove_worktree

NODE_VERSION = "friday-node-reference 2.0"
OS_VERSION = "Linux reference"
FRAME_ERROR_LIMIT = 3
FRAME_ERROR_WINDOW_SECONDS = 60
SILENCE_SECONDS = 45
BACKOFF_MIN_SECONDS = 1
BACKOFF_MAX_SECONDS = 60
NO_RECONNECT_REASONS = frozenset({"revoked", "kill_switch", "credential_expired"})

# Read outputs of the reference node's fake readers (the Phase 9 readers
# proper are `feat/p9-reference-node`); every other read answers `error`.
DEFAULT_READ_OUTPUTS = {
    "apps.frontmost": {"app_name": "Terminal", "bundle_id": "com.apple.Terminal"}
}


def default_catalogue(availability=None):
    """The server's own contracts, read and action, as the node's catalogue
    entries (`availability` maps a name to a non-`granted` value)."""
    availability = availability or {}
    entries = []
    for name, contract in {**load_contracts(), **load_action_contracts()}.items():
        entries.append(contract.catalogue_entry(availability.get(name, "granted")))
    return entries


@dataclass
class _Pending:
    envelope: dict
    resolved: object
    received_at: int


@dataclass
class _Running:
    envelope: dict
    handle: object
    pipeline: OutputPipeline
    started_at: int
    received_at: int
    wall_seconds: int
    repo_root: str | None = None
    worktree: str | None = None
    temp_dir: str | None = None


@dataclass
class _Read:
    envelope: dict
    due_at: int
    received_at: int


@dataclass
class ReferenceNode:
    node_id: str
    pinned: dict
    policy: object
    executor: object
    clock: object
    window: ConfirmationWindowDouble | None = None
    catalogue: list | None = None
    read_outputs: dict = field(default_factory=lambda: dict(DEFAULT_READ_OUTPUTS))
    read_delay_seconds: int = 0
    node_env: dict = field(default_factory=dict)
    worktree_base: str | None = None
    scrub: object = None
    rng: random.Random = field(default_factory=lambda: random.Random(0))
    id_factory: object = None

    def __post_init__(self):
        if self.catalogue is None:
            self.catalogue = default_catalogue()
        if self.window is None:
            self.window = ConfirmationWindowDouble(self.clock)
        self.scrub = self.scrub or default_scrub()
        self.id_factory = self.id_factory or (lambda: str(uuid.uuid4()))
        self.nonce_cache = NonceCache()
        self.version = None
        self.connected = False
        self.closed_reason = None
        self.reconnect_allowed = True
        self.kill_switch = False
        self.sent: list = []
        self.frame_error_times: deque = deque()
        self.dropped_after_disconnect = 0
        self.last_server_frame_at = None
        self.reconnect_attempts = 0
        self.next_reconnect_delay = None
        self._heartbeat_seq = 0
        self._pending: _Pending | None = None
        self._running: list = []
        self._reads: list = []

    # --- session ---------------------------------------------------------

    def _state(self):
        return node_state(
            node_id=self.node_id,
            pinned=self.pinned,
            catalogue={entry["name"]: entry for entry in self.catalogue},
            nonce_cache=self.nonce_cache,
        )

    def connect(self, version: int) -> list:
        """A new socket negotiated `version`; returns `[hello]`. The nonce
        cache survives reconnects (a replay across sockets is a replay)."""
        if version not in (1, 2):
            raise ValueError("unsupported protocol version")
        self.version = version
        self.connected = True
        self.closed_reason = None
        self.frame_error_times.clear()
        self.last_server_frame_at = self.clock.now()
        self._last_heartbeat_at = self.clock.now()
        self.reconnect_attempts = 0
        self.next_reconnect_delay = None
        return self._send([self.hello()])

    def hello(self) -> dict:
        catalogue = [e for e in self.catalogue if self.version == 2 or e["class"] == "read"]
        return {
            "v": self.version,
            "type": "hello",
            "id": self.id_factory(),
            "node_version": NODE_VERSION,
            "os_version": OS_VERSION,
            "catalogue": catalogue,
            "catalogue_sha256": catalogue_sha256(catalogue),
        }

    def heartbeat(self) -> list:
        return self._send(self._heartbeat_frames()) if self.connected else []

    def _heartbeat_frames(self) -> list:
        self._heartbeat_seq += 1
        frame = {
            "v": self.version,
            "type": "heartbeat",
            "id": self.id_factory(),
            "seq": self._heartbeat_seq,
            "uptime_seconds": 0,
            "spool_count": 0,
            "spool_bytes": 0,
        }
        return [frame]

    def _send(self, frames) -> list:
        out = []
        for frame in frames:
            if not self.connected:
                self.dropped_after_disconnect += 1
                continue
            validate(frame, "node_to_server", self.version)
            self.sent.append(frame)
            out.append(frame)
        return out

    def disconnect(self, reason="socket_lost") -> None:
        """Fail closed: close the window, stop what runs, send nothing late."""
        self.connected = False
        if self.window.is_open:
            self.window.force_close("disconnect")
        self._pending = None
        for running in self._running:
            running.handle.terminate()
            self._cleanup(running)
        self._running = []
        self._reads = []
        if self.closed_reason is None:
            self.closed_reason = reason
        if self.reconnect_allowed:
            self._schedule_reconnect()

    def _schedule_reconnect(self):
        ceiling = min(BACKOFF_MAX_SECONDS, BACKOFF_MIN_SECONDS * 2**self.reconnect_attempts)
        self.reconnect_attempts += 1
        self.next_reconnect_delay = self.rng.uniform(0, ceiling)

    def engage_kill_switch(self) -> list:
        """The owner's local kill switch: closes an open window (reported as
        `confirmation{closed}` and a refused result) and stops what runs."""
        self.kill_switch = True
        frames = []
        if self._pending is not None:
            pending, self._pending = self._pending, None
            self.window.force_close("kill_switch")
            frames += self._confirmation(pending.envelope, "closed")
            frames.append(self._refused(pending.envelope, "owner_denied", "closed", pending))
        for running in list(self._running):
            running.handle.terminate()
            frames += self._finish(running, status="error", exit_code=None)
        return self._send(frames)

    # --- frames ----------------------------------------------------------

    def _result(
        self,
        envelope,
        *,
        status,
        refusal_code=None,
        output=None,
        received_at=None,
        confirm_state="not_required",
        exit_code=None,
        output_bytes=0,
        tail=None,
        truncated=False,
        sandbox_violation=False,
    ):
        now = self.clock.now()
        duration = max(now - received_at, 0) * 1000 if received_at is not None else 0
        frame = {
            "v": self.version,
            "type": "result",
            "id": self.id_factory(),
            "invocation_id": envelope.get("invocation_id") if envelope else None,
            "status": status,
            "refusal_code": refusal_code,
            "output": output,
            "truncated": truncated,
            "output_sha256": sha256_hex(output) if output is not None else "",
            "duration_ms": min(duration, 3_600_000),
        }
        if self.version == 2:
            frame.update(
                exit_code=exit_code,
                output_bytes=output_bytes,
                tail=tail,
                confirm_state=confirm_state,
                sandbox_violation=sandbox_violation,
            )
        return frame

    def _refused(self, envelope, code, confirm_state="not_required", pending=None):
        received = pending.received_at if pending is not None else self.clock.now()
        return self._result(
            envelope,
            status="refused",
            refusal_code=code,
            confirm_state=confirm_state,
            received_at=received,
        )

    def _confirmation(self, envelope, state):
        return [
            {
                "v": 2,
                "type": "confirmation",
                "id": self.id_factory(),
                "invocation_id": envelope["invocation_id"],
                "state": state,
                "at": self.clock.now(),
            }
        ]

    def _frame_error(self, code) -> list:
        now = self.clock.now()
        self.frame_error_times.append(now)
        while (
            self.frame_error_times and self.frame_error_times[0] <= now - FRAME_ERROR_WINDOW_SECONDS
        ):
            self.frame_error_times.popleft()
        frames = self._send([self._result(None, status="error", refusal_code=code)])
        if len(self.frame_error_times) >= FRAME_ERROR_LIMIT:
            self.closed_reason = "protocol_error"
            self.disconnect("protocol_error")
        return frames

    # --- receiving -------------------------------------------------------

    def receive(self, raw) -> list:
        if not self.connected:
            return []
        now = self.clock.now()
        self.last_server_frame_at = now
        try:
            verified = verify(
                raw, version=self.version, state=self._state(), policy=self.policy, now=now
            )
        except Refusal as refusal:
            if refusal.frame_error:
                return self._frame_error(refusal.code)
            envelope = raw if isinstance(raw, dict) else _parsed(raw)
            return self._send([self._refused(envelope, refusal.code)])
        envelope = verified.envelope
        kind = envelope["type"]
        if kind == "bye":
            if envelope["reason"] in NO_RECONNECT_REASONS:
                self.reconnect_allowed = False
            self.disconnect(f"bye:{envelope['reason']}")
            return []
        if kind != "invoke":
            return []  # hello.ack, heartbeat.ack, event.ack: nothing to answer
        if envelope["class"] == "read":
            return self._send(self._read(envelope, now))
        return self._send(self._open_window(envelope, verified.resolved, now))

    def _read(self, envelope, now):
        if self.read_delay_seconds:
            self._reads.append(_Read(envelope, now + self.read_delay_seconds, now))
            return []
        return [self._read_result(envelope, now)]

    def _read_result(self, envelope, received_at):
        output = self.read_outputs.get(envelope["capability"])
        if output is None:
            return self._result(envelope, status="error", received_at=received_at)
        return self._result(envelope, status="ok", output=output, received_at=received_at)

    def _open_window(self, envelope, resolved, now):
        contract = get_any_contract(envelope["capability"])
        recipe = contract.recipe_for(envelope["args"]) if contract is not None else None
        approval_id = envelope["grant"]["approval_id"] if envelope["grant"] else None
        text = render_confirmation(
            contract, envelope["args"], recipe=recipe, approval_id=approval_id, resolved=resolved
        )
        try:
            self.window.open(envelope["invocation_id"], text)
        except WindowBusy:
            return [self._refused(envelope, "busy_confirming")]
        self._pending = _Pending(envelope, resolved, now)
        return self._confirmation(envelope, "shown") + self._pump_window()

    # --- the clock moved -------------------------------------------------

    def tick(self) -> list:
        if not self.connected:
            return []
        now = self.clock.now()
        frames = self._pump_window()
        for read in list(self._reads):
            if now >= read.due_at:
                self._reads.remove(read)
                frames.append(self._read_result(read.envelope, read.received_at))
        for running in list(self._running):
            frames += self._pump_running(running)
        if now - self._last_heartbeat_at >= HEARTBEAT_SECONDS:
            self._last_heartbeat_at = now
            frames += self._heartbeat_frames()
        frames = self._send(frames)
        if (
            self.last_server_frame_at is not None
            and now - self.last_server_frame_at > SILENCE_SECONDS
        ):
            self.disconnect("server_silent")
        return frames

    def _pump_window(self):
        if self._pending is None:
            return []
        state = self.window.poll()
        if state is None:
            return []
        pending, self._pending = self._pending, None
        envelope = pending.envelope
        frames = self._confirmation(envelope, state)
        if state == "denied":
            return frames + [self._refused(envelope, "owner_denied", "denied", pending)]
        if state == "timed_out":
            return frames + [self._refused(envelope, "confirm_timeout", "timed_out", pending)]
        if state == "closed":
            return frames + [self._refused(envelope, "owner_denied", "closed", pending)]
        # Check 24: after the click.
        if not self.connected:
            return []
        if self.clock.now() >= envelope["expires_at"] or self.kill_switch:
            return frames + [self._refused(envelope, "expired_after_confirm", "approved", pending)]
        return frames + self._execute(pending)

    # --- executing -------------------------------------------------------

    def _execute(self, pending):
        envelope = pending.envelope
        resolved = pending.resolved
        if envelope["capability"] != "repo.command.run":
            outcome = self.executor.perform(
                ActionRequest(
                    capability=envelope["capability"],
                    args=envelope["args"],
                    bundle_id=resolved.bundle_id,
                    recipe=resolved.recipe,
                )
            )
            return [
                self._result(
                    envelope,
                    status="ok" if outcome.ok else "error",
                    confirm_state="approved",
                    received_at=pending.received_at,
                )
            ]
        contract = get_any_contract(envelope["capability"])
        command = resolved.command
        repo_root = resolved.repo.real_root
        temp_dir = tempfile.mkdtemp(prefix="friday-run-")
        os.chmod(temp_dir, 0o700)
        worktree = None
        try:
            write_root = repo_root
            if command.mode == "worktree":
                base = self.worktree_base or tempfile.mkdtemp(prefix="friday-wt-")
                worktree = create_worktree(repo_root, base)
                write_root = worktree
            spec = build_sandbox(
                command,
                home=self.policy.home,
                repo_root=repo_root,
                write_root=write_root,
                temp_dir=os.path.realpath(temp_dir),
                contract_max_runtime=contract.max_runtime_seconds,
                node_env=self.node_env,
            )
            handle = self.executor.start(spec)
        except (OSError, SandboxError, WorktreeError):
            if worktree is not None:
                remove_worktree(repo_root, worktree)
            _rmtree(temp_dir)
            return [
                self._result(
                    envelope,
                    status="error",
                    confirm_state="approved",
                    received_at=pending.received_at,
                )
            ]
        running = _Running(
            envelope=envelope,
            handle=handle,
            pipeline=OutputPipeline(scrub=self.scrub),
            started_at=self.clock.now(),
            received_at=pending.received_at,
            wall_seconds=spec.limits["wall_seconds"],
            repo_root=repo_root,
            worktree=worktree,
            temp_dir=temp_dir,
        )
        self._running.append(running)
        return self._pump_running(running)

    def _output_frames(self, running, pieces):
        return [
            {
                "v": 2,
                "type": "output",
                "id": self.id_factory(),
                "invocation_id": running.envelope["invocation_id"],
                "seq": piece.seq,
                "stream": piece.stream,
                "data": piece.data,
                "truncated": piece.truncated,
            }
            for piece in pieces
        ]

    def _pump_running(self, running):
        frames = []
        for stream, data in running.handle.read():
            frames += self._output_frames(running, running.pipeline.feed(stream, data))
        outcome = running.handle.poll()
        if outcome is not None:
            for stream, data in running.handle.read():
                frames += self._output_frames(running, running.pipeline.feed(stream, data))
            return frames + self._finish(
                running,
                status="ok" if outcome.error is None else "error",
                exit_code=outcome.exit_code,
                sandbox_violation=outcome.sandbox_violation,
            )
        if self.clock.now() - running.started_at >= running.wall_seconds:
            running.handle.terminate()
            return frames + self._finish(running, status="timeout", exit_code=None)
        return frames

    def _finish(self, running, *, status, exit_code, sandbox_violation=False):
        frames = self._output_frames(running, running.pipeline.finish())
        self._running.remove(running)
        self._cleanup(running)
        pipeline = running.pipeline
        return frames + [
            self._result(
                running.envelope,
                status=status,
                confirm_state="approved",
                received_at=running.received_at,
                exit_code=exit_code,
                output_bytes=pipeline.output_bytes,
                tail=pipeline.tail,
                truncated=pipeline.truncated,
                sandbox_violation=sandbox_violation,
            )
        ]

    def _cleanup(self, running):
        if running.worktree is not None:
            try:
                remove_worktree(running.repo_root, running.worktree)
            except WorktreeError:
                pass
            running.worktree = None
        if running.temp_dir is not None:
            _rmtree(running.temp_dir)
            running.temp_dir = None

    # --- introspection (tests and the conformance hook) ------------------

    @property
    def busy(self) -> bool:
        return self._pending is not None or bool(self._running)


def _parsed(raw):
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _rmtree(path):
    shutil.rmtree(path, ignore_errors=True)
