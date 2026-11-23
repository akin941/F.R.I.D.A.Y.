"""Codex app-server driver (Phase 10 brief D10, branch 6).

Speaks the Codex app-server protocol -- JSON-RPC 2.0 messages without the
`"jsonrpc"` field, one JSON object per line -- over the interactive
container's stdio: `initialize` + `initialized`, `thread/start`,
`turn/start`; the item, token-usage and turn-completion notifications; and
the server-initiated command-execution and file-change approval requests.
Method names and payloads come from the pinned version's schema snapshot
(`harness.drivers.schema`); every frame read is validated against it, and
a method the snapshot does not know fails the run closed
(`unknown_method`). Every method this driver sends or handles is listed in
the class attributes below so the contract test can check each one exists
in the snapshot.

The agent runs with its own sandbox at `SANDBOX_POLICY["codex_cli"]`
(workspace-write, network off, approvals on request). The driver never
answers an escalation on its own: `answer_approval` is called by the
session only after the `harness_agent_action` row decided it, and only
ever sends `accept` or `decline` for that one request (never
`acceptForSession`).
"""

import collections
import json
import shlex
import time

from harness.drivers.base import (
    ACCEPT,
    CODE_DEADLINE,
    CODE_EARLY_EXIT,
    CODE_PROTOCOL_ERROR,
    CODE_UNKNOWN_METHOD,
    DECLINE,
    SANDBOX_POLICY,
    DriverError,
    DriverEvent,
    DriverTranscript,
    Escalation,
    JsonlReader,
    TurnUsage,
)
from harness.drivers.schema import (
    CLIENT_NOTIFICATIONS,
    CLIENT_REQUESTS,
    SERVER_NOTIFICATIONS,
    SERVER_REQUESTS,
)

CLIENT_NAME = "friday-harness"
CLIENT_TITLE = "Friday harness"
CLIENT_VERSION = "1"


def _token(value):
    return value if isinstance(value, int) and value >= 0 else 0


def _command_text(command):
    if isinstance(command, str):
        return command
    if isinstance(command, list) and all(isinstance(part, str) for part in command):
        return shlex.join(command)
    return ""


class CodexAppServerDriver:
    agent = "codex_cli"
    name = "app_server"

    SENDS_REQUESTS = ("initialize", "thread/start", "turn/start")
    SENDS_NOTIFICATIONS = ("initialized",)
    HANDLES_REQUESTS = {
        "item/commandExecution/requestApproval": "command",
        "item/fileChange/requestApproval": "file_change",
    }
    HANDLES_NOTIFICATIONS = ("item/completed", "thread/tokenUsage/updated", "turn/completed")

    def __init__(
        self,
        *,
        snapshot,
        deadline,
        clock=time.monotonic,
        poll_seconds=1.0,
        max_line_bytes=None,
        max_lines=None,
    ):
        self.snapshot = snapshot
        self.deadline = deadline
        self._clock = clock
        self._poll_seconds = poll_seconds
        self._reader_limits = {
            key: value
            for key, value in (("max_line_bytes", max_line_bytes), ("max_lines", max_lines))
            if value is not None
        }
        self._stdin = None
        self._reader = None
        self._next_id = 0
        self._backlog = collections.deque()
        self._pending_server_requests = {}
        self.thread_id = ""
        self._turn = None
        self._turns = []
        self._usage_total = (0, 0, 0, 0)
        self._turn_usage_total = None
        self.transcript = DriverTranscript()
        self.sent = []  # (method, params) the driver sent, for tests/audit

    # --- lifecycle ---------------------------------------------------------

    def start(self, container):
        self._stdin = container.stdin
        self._reader = JsonlReader(container.stdout, **self._reader_limits)
        self._call(
            "initialize",
            {"clientInfo": {"name": CLIENT_NAME, "title": CLIENT_TITLE, "version": CLIENT_VERSION}},
        )
        self._notify("initialized", {})
        policy = SANDBOX_POLICY[self.agent]
        result = self._call(
            "thread/start",
            {
                "cwd": policy["writable_roots"][0],
                "approvalPolicy": policy["approval_policy"],
                "sandbox": policy["sandbox"],
            },
        )
        self.thread_id = result["thread"]["id"]

    def run_turn(self, goal):
        policy = SANDBOX_POLICY[self.agent]
        result = self._call(
            "turn/start",
            {
                "threadId": self.thread_id,
                "input": [{"type": "text", "text": str(goal)}],
                "approvalPolicy": policy["approval_policy"],
                "sandboxPolicy": {
                    "type": "workspaceWrite",
                    "writableRoots": list(policy["writable_roots"]),
                    "networkAccess": policy["network_access"],
                },
            },
        )
        self._turn = TurnUsage(turn_id=result["turn"]["id"])
        self._turn_usage_total = None
        self._turns.append(self._turn)
        return self._turn.turn_id

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
        """Yields `DriverEvent`s until the current turn completes. Raises
        `DriverError` on any protocol fault or the deadline."""
        while True:
            message = self._backlog.popleft() if self._backlog else self._read(self._slice())
            if message is None:
                yield DriverEvent("idle")
                continue
            event = self._handle(message)
            if event is None:
                continue
            yield event
            if event.kind == "turn_completed":
                return

    def answer_approval(self, request_id, accept):
        key = json.dumps(request_id)
        if key not in self._pending_server_requests:
            raise ValueError("no pending escalation with this id")
        method = self._pending_server_requests.pop(key)
        result = {"decision": ACCEPT if accept is True else DECLINE}
        if not self.snapshot.validate(SERVER_REQUESTS, method, "result", result):
            raise DriverError(CODE_PROTOCOL_ERROR, f"answer to {method} fails the snapshot")
        self._write({"id": request_id, "result": result})

    # --- internals ----------------------------------------------------------

    def _slice(self):
        remaining = self.deadline - self._clock()
        if remaining <= 0:
            raise DriverError(CODE_DEADLINE, "driver deadline reached")
        return min(self._poll_seconds, remaining)

    def _read(self, timeout):
        return self._reader.next_message(timeout)

    def _write(self, message):
        line = json.dumps(message, separators=(",", ":"), ensure_ascii=False) + "\n"
        try:
            self._stdin.write(line.encode("utf-8"))
            self._stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            detail = f"agent stdin closed: {type(exc).__name__}"
            raise DriverError(CODE_EARLY_EXIT, detail) from None

    def _notify(self, method, params):
        if not self.snapshot.has(CLIENT_NOTIFICATIONS, method):
            raise DriverError(CODE_UNKNOWN_METHOD, f"snapshot has no client notification {method}")
        self.sent.append((method, params))
        self._write({"method": method, "params": params})

    def _call(self, method, params):
        if not self.snapshot.has(CLIENT_REQUESTS, method):
            raise DriverError(CODE_UNKNOWN_METHOD, f"snapshot has no client request {method}")
        if not self.snapshot.validate(CLIENT_REQUESTS, method, "params", params):
            raise DriverError(CODE_PROTOCOL_ERROR, f"{method} params fail the snapshot")
        self._next_id += 1
        request_id = self._next_id
        self.sent.append((method, params))
        self._write({"id": request_id, "method": method, "params": params})
        while True:
            message = self._read(self._slice())
            if message is None:
                continue
            if "method" in message:
                # A notification or server request before our response:
                # handled later, in order, by `events()`.
                self._backlog.append(message)
                continue
            if message.get("id") != request_id:
                raise DriverError(CODE_PROTOCOL_ERROR, "response to an unknown request id")
            if "error" in message or not isinstance(message.get("result"), dict):
                raise DriverError(CODE_PROTOCOL_ERROR, f"{method} returned an error")
            if not self.snapshot.validate(CLIENT_REQUESTS, method, "result", message["result"]):
                raise DriverError(CODE_PROTOCOL_ERROR, f"{method} result fails the snapshot")
            return message["result"]

    def _handle(self, message):
        method = message.get("method")
        if not isinstance(method, str):
            raise DriverError(CODE_PROTOCOL_ERROR, "frame without a method outside a call")
        params = message.get("params")
        if params is None:
            params = {}
        if "id" in message:
            return self._handle_server_request(message["id"], method, params)
        return self._handle_notification(method, params)

    def _handle_server_request(self, request_id, method, params):
        if not self.snapshot.has(SERVER_REQUESTS, method):
            raise DriverError(CODE_UNKNOWN_METHOD, method[:100])
        kind = self.HANDLES_REQUESTS.get(method)
        if kind is None:
            raise DriverError(CODE_UNKNOWN_METHOD, f"unhandled server request {method[:100]}")
        if not isinstance(request_id, (int, str)) or isinstance(request_id, bool):
            raise DriverError(CODE_PROTOCOL_ERROR, "server request id is not a string or integer")
        if not isinstance(params, dict) or not self.snapshot.validate(
            SERVER_REQUESTS, method, "params", params
        ):
            raise DriverError(CODE_PROTOCOL_ERROR, f"{method} params fail the snapshot")
        key = json.dumps(request_id)
        if key in self._pending_server_requests:
            raise DriverError(CODE_PROTOCOL_ERROR, "duplicate server request id")
        self._pending_server_requests[key] = method
        if kind == "command":
            escalation = Escalation(
                request_id=request_id,
                kind="command",
                command=_command_text(params.get("command")),
                cwd=params.get("cwd") if isinstance(params.get("cwd"), str) else "",
                reason=params.get("reason") if isinstance(params.get("reason"), str) else "",
            )
        else:
            grant_root = params.get("grantRoot")
            escalation = Escalation(
                request_id=request_id,
                kind="file_change",
                command="",
                cwd=grant_root if isinstance(grant_root, str) else "",
                reason=params.get("reason") if isinstance(params.get("reason"), str) else "",
            )
        return DriverEvent("escalation", escalation)

    def _handle_notification(self, method, params):
        if not self.snapshot.has(SERVER_NOTIFICATIONS, method):
            raise DriverError(CODE_UNKNOWN_METHOD, method[:100])
        if not self.snapshot.validate(SERVER_NOTIFICATIONS, method, "params", params):
            raise DriverError(CODE_PROTOCOL_ERROR, f"{method} params fail the snapshot")
        if method == "item/completed":
            item = params.get("item") or {}
            if item.get("type") == "agentMessage" and isinstance(item.get("text"), str):
                self.transcript.add(item["text"])
                return DriverEvent("message", item["text"])
            return None
        if method == "thread/tokenUsage/updated":
            if self._turn is not None and params.get("turnId") == self._turn.turn_id:
                total = params["tokenUsage"]["total"]
                self._turn_usage_total = (
                    _token(total.get("inputTokens")),
                    _token(total.get("outputTokens")),
                    _token(total.get("cachedInputTokens")),
                    _token(total.get("reasoningOutputTokens")),
                )
            return None
        if method == "turn/completed":
            turn = params["turn"]
            if self._turn is None or turn.get("id") != self._turn.turn_id:
                raise DriverError(CODE_PROTOCOL_ERROR, "completion of an unknown turn")
            self._turn.status = str(turn.get("status", ""))
            if self._turn_usage_total is not None:
                previous = self._usage_total
                current = self._turn_usage_total
                pairs = zip(current, previous, strict=True)
                delta = [max(0, now - before) for now, before in pairs]
                (
                    self._turn.input_tokens,
                    self._turn.output_tokens,
                    self._turn.cached_input_tokens,
                    self._turn.reasoning_output_tokens,
                ) = delta
                self._turn.reported = True
                self._usage_total = current
            completed, self._turn = self._turn, None
            return DriverEvent("turn_completed", completed)
        return None
