"""Claude Agent SDK driver (Phase 10 brief D10, branch 7).

Speaks Friday's own bridge protocol (`friday-agent-bridge/v1`, schemas in
`claude_bridge_schema/`) over the interactive container's stdio to
`harness/docker/claude_agent_bridge.py`, which runs the pinned
`claude-agent-sdk` inside the Claude Code image:

  host -> bridge   initialize, turn, permission_response
  bridge -> host   ready, message, permission_request, result, error

Every frame read is validated against the bridge schema; a frame type the
schema does not know fails the run closed (`unknown_method`), a frame that
does not match its schema or arrives out of order is `protocol_error`.

The agent's sandbox is the bridge's: permission mode `default`, nothing
pre-allowed, no settings files from the repo, no MCP servers; the bridge
answers its permission callback locally only for file tools inside
`/workspace/repo` and `Bash` running the repo's test command verbatim. The
`ready` frame reports that policy and the installed SDK version, and the
driver refuses to go on unless both are exactly what it expects (the pin,
the repo root, `default` mode, no setting sources, the job's test command).
Everything else the agent wants is a `permission_request`; the driver never
answers one on its own: `answer_approval` is called by the session only
after the `harness_agent_action` row decided it, and sends `allow` or
`deny` for that one request.

The command on the approval card is exactly what will run: for a plain
`Bash` call, the command string; for any other tool (or a `Bash` call with
extra options), the tool name followed by its whole input as canonical
JSON. `approval_mapping.request_agent_action` refuses (declines) anything
the card could not show verbatim.
"""

import collections
import json
import time
from decimal import Decimal, InvalidOperation

from harness.drivers.base import (
    CODE_DEADLINE,
    CODE_EARLY_EXIT,
    CODE_PROTOCOL_ERROR,
    CODE_UNKNOWN_METHOD,
    SANDBOX_POLICY,
    DriverError,
    DriverEvent,
    DriverTranscript,
    Escalation,
    JsonlReader,
    TurnUsage,
)
from harness.drivers.claude_surface import BRIDGE_PROTOCOL, BRIDGE_TO_HOST, HOST_TO_BRIDGE

BASH_TOOL = "Bash"
BASH_PLAIN_KEYS = frozenset({"command", "description", "timeout"})
WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def escalation_for(request_id, tool_name, tool_input, reason, *, cwd):
    """The `Escalation` for one relayed permission request. `command` binds
    the approval to exactly what will run."""
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if tool_name == BASH_TOOL and isinstance(command, str) and set(tool_input) <= BASH_PLAIN_KEYS:
        return Escalation(request_id=request_id, kind="command", command=command, cwd=cwd, reason=reason)
    if tool_name == BASH_TOOL:
        kind = "command"
    elif tool_name in WRITE_TOOLS:
        kind = "file_change"
    else:
        kind = "tool"
    return Escalation(
        request_id=request_id,
        kind=kind,
        command=f"{tool_name} {_canonical(tool_input)}",
        cwd=cwd,
        reason=reason,
    )


def _cost(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        cost = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return cost if cost.is_finite() and cost >= 0 else None


class ClaudeAgentSdkDriver:
    agent = "claude_code"
    name = "agent_sdk"

    SENDS = ("initialize", "turn", "permission_response")
    HANDLES = ("ready", "message", "permission_request", "result", "error")

    def __init__(
        self,
        *,
        bridge_schema,
        sdk_version,
        deadline,
        test_command="",
        clock=time.monotonic,
        poll_seconds=1.0,
        max_line_bytes=None,
        max_lines=None,
    ):
        self.bridge_schema = bridge_schema
        self.sdk_version = str(sdk_version)
        self.deadline = deadline
        self.test_command = test_command or ""
        self._clock = clock
        self._poll_seconds = poll_seconds
        self._reader_limits = {
            key: value
            for key, value in (("max_line_bytes", max_line_bytes), ("max_lines", max_lines))
            if value is not None
        }
        self._stdin = None
        self._reader = None
        self._backlog = collections.deque()
        self._pending = set()
        self._turn = None
        self._turns = []
        self._turn_seq = 0
        self.transcript = DriverTranscript()
        self.sent = []  # frames the driver sent, for tests/audit

    # --- lifecycle ---------------------------------------------------------

    def expected_policy(self):
        policy = SANDBOX_POLICY[self.agent]
        return {
            "repo_root": policy["writable_roots"][0],
            "permission_mode": policy["permission_mode"],
            "setting_sources": list(policy["setting_sources"]),
            "bash_allowed": [self.test_command] if self.test_command else [],
        }

    def start(self, container):
        self._stdin = container.stdin
        self._reader = JsonlReader(container.stdout, **self._reader_limits)
        self._send({"type": "initialize", "protocol": BRIDGE_PROTOCOL})
        while True:
            frame = self._read(self._slice())
            if frame is not None:
                break
        frame_type = self._check(frame)
        if frame_type == "error":
            raise DriverError(CODE_PROTOCOL_ERROR, f"bridge error {frame.get('code')!r}")
        if frame_type != "ready":
            raise DriverError(CODE_PROTOCOL_ERROR, f"expected ready, got {frame_type}")
        if frame["sdk_version"] != self.sdk_version:
            raise DriverError(
                CODE_PROTOCOL_ERROR,
                f"bridge runs SDK {frame['sdk_version']!r}, pin is {self.sdk_version!r}",
            )
        if frame["policy"] != self.expected_policy():
            raise DriverError(CODE_PROTOCOL_ERROR, "bridge reports a sandbox policy other than ours")

    def run_turn(self, goal):
        self._turn_seq += 1
        turn_id = f"turn_{self._turn_seq}"
        self._send({"type": "turn", "turn_id": turn_id, "goal": str(goal)})
        self._turn = TurnUsage(turn_id=turn_id)
        self._turns.append(self._turn)
        return turn_id

    def close(self):
        if self._stdin is not None:
            try:
                self._stdin.close()
            except OSError:
                pass
        if self._reader is not None:
            self._reader.discard_rest()

    def usage(self):
        return list(self._turns)

    # --- events -----------------------------------------------------------

    def events(self):
        """Yields `DriverEvent`s until the current turn's `result`. Raises
        `DriverError` on any protocol fault or the deadline."""
        while True:
            frame = self._backlog.popleft() if self._backlog else self._read(self._slice())
            if frame is None:
                yield DriverEvent("idle")
                continue
            event = self._handle(frame)
            if event is None:
                continue
            yield event
            if event.kind == "turn_completed":
                return

    def answer_approval(self, request_id, accept):
        if request_id not in self._pending:
            raise ValueError("no pending escalation with this id")
        self._pending.discard(request_id)
        self._send(
            {
                "type": "permission_response",
                "request_id": request_id,
                "decision": "allow" if accept is True else "deny",
            }
        )

    # --- internals ----------------------------------------------------------

    def _slice(self):
        remaining = self.deadline - self._clock()
        if remaining <= 0:
            raise DriverError(CODE_DEADLINE, "driver deadline reached")
        return min(self._poll_seconds, remaining)

    def _read(self, timeout):
        return self._reader.next_message(timeout)

    def _send(self, frame):
        if not self.bridge_schema.validate(HOST_TO_BRIDGE, frame):
            raise DriverError(CODE_PROTOCOL_ERROR, f"{frame.get('type')} frame fails the schema")
        self.sent.append(frame)
        line = json.dumps(frame, separators=(",", ":"), ensure_ascii=False) + "\n"
        try:
            self._stdin.write(line.encode("utf-8"))
            self._stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            raise DriverError(CODE_EARLY_EXIT, f"bridge stdin closed: {type(exc).__name__}") from None

    def _check(self, frame):
        frame_type = frame.get("type")
        if not isinstance(frame_type, str):
            raise DriverError(CODE_PROTOCOL_ERROR, "frame without a type")
        if not self.bridge_schema.has(BRIDGE_TO_HOST, frame_type):
            raise DriverError(CODE_UNKNOWN_METHOD, frame_type[:100])
        if not self.bridge_schema.validate(BRIDGE_TO_HOST, frame):
            raise DriverError(CODE_PROTOCOL_ERROR, f"{frame_type} frame fails the schema")
        return frame_type

    def _current_turn(self, frame):
        if self._turn is None or frame["turn_id"] != self._turn.turn_id:
            raise DriverError(CODE_PROTOCOL_ERROR, f"{frame['type']} for an unknown turn")

    def _handle(self, frame):
        frame_type = self._check(frame)
        if frame_type == "error":
            raise DriverError(CODE_PROTOCOL_ERROR, f"bridge error {frame.get('code')!r}")
        if frame_type == "ready":
            raise DriverError(CODE_PROTOCOL_ERROR, "ready outside the handshake")
        self._current_turn(frame)
        if frame_type == "message":
            self.transcript.add(frame["text"])
            return DriverEvent("message", frame["text"])
        if frame_type == "permission_request":
            request_id = frame["request_id"]
            if request_id in self._pending:
                raise DriverError(CODE_PROTOCOL_ERROR, "duplicate permission request id")
            self._pending.add(request_id)
            escalation = escalation_for(
                request_id,
                frame["tool_name"],
                frame["input"],
                frame["reason"],
                cwd=SANDBOX_POLICY[self.agent]["writable_roots"][0],
            )
            return DriverEvent("escalation", escalation)
        # result
        turn = self._turn
        if self._pending:
            raise DriverError(CODE_PROTOCOL_ERROR, "turn ended with an unanswered request")
        turn.status = frame["status"]
        usage = frame["usage"]
        if usage is not None:
            turn.input_tokens = usage["input_tokens"]
            turn.output_tokens = usage["output_tokens"]
            turn.cached_input_tokens = usage["cache_read_input_tokens"]
            turn.cache_creation_input_tokens = usage["cache_creation_input_tokens"]
            turn.reported = True
            turn.cost_usd = _cost(frame["total_cost_usd"])
        self._turn = None
        return DriverEvent("turn_completed", turn)
