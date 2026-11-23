#!/usr/bin/env python3
"""Friday agent bridge (`friday-agent-bridge/v1`, Phase 10 brief D10, branch 7).

Runs INSIDE the Claude Code harness image, started by `claude_code_run.sh
--driver agent_sdk`. It drives the pinned `claude-agent-sdk` and speaks
Friday's own line protocol to the host driver
(`backend/harness/drivers/claude_agent_sdk.py`) over this process's
stdin/stdout: one JSON object per line, schemas in
`backend/harness/drivers/claude_bridge_schema/`.

  host -> bridge   initialize, turn, permission_response
  bridge -> host   ready, message, permission_request, result, error

The agent's own sandbox is the strictest unattended setting that still lets
it work on the repository (brief D10 "Approval mapping"): permission mode
`default`, no pre-allowed tools, no settings files loaded from the repo
(`setting_sources=[]`, so a planted `.claude/settings.json` cannot
pre-approve anything), no MCP servers. Every tool call reaches the SDK's
permission callback (`can_use_tool`), which answers locally ONLY for

  * the file tools (Read, Glob, Grep, LS, Write, Edit, MultiEdit,
    NotebookEdit) on paths that resolve -- symlinks followed -- inside the
    repository (writes into `.git/` excluded), and
  * `Bash` running the repository's configured test command verbatim.

Anything else is relayed to the host as a `permission_request` and blocks
until the host answers; the host answers only after the owner decided the
matching `harness_agent_action` approval. A `deny` reaches the SDK as a
denial. If stdin closes while a turn is running, every pending request is
denied, the turn is abandoned and the bridge exits non-zero; stdin closing
while idle is the normal end of the protocol phase (exit 0).

Purity: this file imports only the standard library and the SDK -- nothing
of Friday's -- and is tested by loading it by path with a fake SDK module
(`backend/harness/tests/doubles/claude_agent_sdk.py`).

SDK names used are listed in `SDK_SURFACE_USED`; the contract test checks
each one against the pinned version's surface snapshot
(`backend/harness/drivers/claude_sdk_surface/<version>/sdk_surface.json`).
"""

import asyncio
import json
import math
import os
import sys
import threading

PROTOCOL = "friday-agent-bridge/v1"
REPO_ROOT = "/workspace/repo"
PERMISSION_MODE = "default"
MAX_LINE_BYTES = 1024 * 1024
MAX_TEXT_CHARS = 100_000

EXIT_OK = 0
EXIT_PROTOCOL = 2
EXIT_STDIN_CLOSED = 3
EXIT_SDK_UNAVAILABLE = 4
EXIT_USAGE = 64

# Tool -> the input keys that name a path. A read tool may omit its path
# (Glob, Grep and LS default to the working directory, the repo).
READ_TOOLS = {"Read": ("file_path",), "Glob": ("path",), "Grep": ("path",), "LS": ("path",)}
WRITE_TOOLS = {
    "Write": ("file_path",),
    "Edit": ("file_path",),
    "MultiEdit": ("file_path",),
    "NotebookEdit": ("notebook_path",),
}
OPTIONAL_PATH_TOOLS = frozenset({"Glob", "Grep", "LS"})
BASH_TOOL = "Bash"
BASH_LOCAL_KEYS = frozenset({"command", "description", "timeout"})

DENY_MESSAGE = "Declined: this action needs the owner's approval and was not approved."

# Every SDK name this bridge touches (contract test: each exists in the
# pinned surface snapshot, and the fake SDK provides each).
SDK_SURFACE_USED = {
    "ClaudeSDKClient": {"methods": ["__aenter__", "__aexit__", "query", "receive_response"]},
    "ClaudeAgentOptions": {
        "fields": [
            "cwd",
            "permission_mode",
            "allowed_tools",
            "setting_sources",
            "mcp_servers",
            "can_use_tool",
        ]
    },
    "PermissionResultAllow": {"fields": ["updated_input"]},
    "PermissionResultDeny": {"fields": ["message", "interrupt"]},
    "AssistantMessage": {"fields": ["content"]},
    "TextBlock": {"fields": ["text"]},
    "ResultMessage": {"fields": ["subtype", "is_error", "usage", "total_cost_usd"]},
}

_EOF = object()
_BAD = object()


def sdk_version(sdk):
    version = getattr(sdk, "__version__", None)
    if isinstance(version, str) and version:
        return version
    try:
        from importlib import metadata

        return metadata.version("claude-agent-sdk")
    except Exception:
        return ""


def _nonneg_int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def usage_frame(usage):
    if not isinstance(usage, dict):
        return None
    return {
        "input_tokens": _nonneg_int(usage.get("input_tokens")),
        "output_tokens": _nonneg_int(usage.get("output_tokens")),
        "cache_read_input_tokens": _nonneg_int(usage.get("cache_read_input_tokens")),
        "cache_creation_input_tokens": _nonneg_int(usage.get("cache_creation_input_tokens")),
    }


def cost_frame(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return float(value)


class LocalPolicy:
    """The local half of the permission callback: True only for what the
    agent may do without asking (file tools inside the repo, the exact test
    command). Everything else is relayed to the owner."""

    def __init__(self, *, repo_root=REPO_ROOT, test_command=""):
        self.repo_root = os.path.realpath(repo_root)
        self.test_command = test_command or ""

    def describe(self):
        return {
            "repo_root": REPO_ROOT,
            "permission_mode": PERMISSION_MODE,
            "setting_sources": [],
            "bash_allowed": [self.test_command] if self.test_command else [],
        }

    def _inside(self, path, *, write):
        if not isinstance(path, str) or not path or "\x00" in path:
            return False
        candidate = path if os.path.isabs(path) else os.path.join(self.repo_root, path)
        real = os.path.realpath(candidate)
        if real != self.repo_root and not real.startswith(self.repo_root + os.sep):
            return False
        if write:
            relative = os.path.relpath(real, self.repo_root)
            first = relative.split(os.sep, 1)[0]
            if relative == "." or first == ".git":
                return False
        return True

    @staticmethod
    def _safe_pattern(pattern):
        if not isinstance(pattern, str):
            return False
        if pattern.startswith("/") or pattern.startswith("~"):
            return False
        return ".." not in pattern.replace("\\", "/").split("/")

    def allows(self, tool_name, tool_input):
        if not isinstance(tool_input, dict):
            return False
        if tool_name == BASH_TOOL:
            return (
                bool(self.test_command)
                and set(tool_input) <= BASH_LOCAL_KEYS
                and tool_input.get("command") == self.test_command
            )
        write = tool_name in WRITE_TOOLS
        keys = WRITE_TOOLS.get(tool_name) or READ_TOOLS.get(tool_name)
        if keys is None:
            return False
        for key in keys:
            if key not in tool_input:
                if tool_name in OPTIONAL_PATH_TOOLS:
                    continue
                return False
            if not self._inside(tool_input[key], write=write):
                return False
        if tool_name == "Glob" and not self._safe_pattern(tool_input.get("pattern")):
            return False
        if tool_name == "Grep" and "glob" in tool_input:
            return self._safe_pattern(tool_input["glob"])
        return True


class Bridge:
    def __init__(self, *, sdk, stdin, stdout, policy):
        self.sdk = sdk
        self.policy = policy
        self._stdin = stdin
        self._stdout = stdout
        self._loop = None
        self._queue = None
        self._eof = False
        self._closed = False
        self._pending = {}
        self._seq = 0
        self._turn_id = None
        self.sent = []

    # --- transport ----------------------------------------------------------

    def send(self, frame):
        line = json.dumps(frame, ensure_ascii=False, separators=(",", ":")) + "\n"
        self.sent.append(frame)
        try:
            self._stdout.write(line.encode("utf-8"))
            self._stdout.flush()
        except (OSError, ValueError):
            self._closed = True

    def _pump(self):
        try:
            while True:
                line = self._stdin.readline(MAX_LINE_BYTES + 1)
                if not line:
                    break
                if len(line.rstrip(b"\n")) > MAX_LINE_BYTES:
                    self._loop.call_soon_threadsafe(self._queue.put_nowait, _BAD)
                    break
                if not line.strip():
                    continue
                try:
                    frame = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    frame = _BAD
                if not isinstance(frame, dict):
                    frame = _BAD
                self._loop.call_soon_threadsafe(self._queue.put_nowait, frame)
        except (OSError, ValueError, RuntimeError):
            pass
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, _EOF)
        except RuntimeError:
            pass  # the loop already ended

    async def _next(self):
        if self._eof:
            return _EOF
        item = await self._queue.get()
        if item is _EOF:
            self._eof = True
        return item

    # --- protocol -----------------------------------------------------------

    async def serve(self):
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        threading.Thread(target=self._pump, name="bridge-stdin", daemon=True).start()
        first = await self._next()
        if first is _EOF:
            return EXIT_OK
        if (
            first is _BAD
            or first.get("type") != "initialize"
            or first.get("protocol") != PROTOCOL
        ):
            self.send({"type": "error", "code": "protocol", "detail": "expected initialize"})
            return EXIT_PROTOCOL
        self.send(
            {
                "type": "ready",
                "protocol": PROTOCOL,
                "sdk_version": sdk_version(self.sdk),
                "policy": self.policy.describe(),
            }
        )
        while True:
            frame = await self._next()
            if frame is _EOF:
                return EXIT_OK
            if (
                frame is _BAD
                or frame.get("type") != "turn"
                or not isinstance(frame.get("turn_id"), str)
                or not isinstance(frame.get("goal"), str)
            ):
                self.send({"type": "error", "code": "protocol", "detail": "expected turn"})
                return EXIT_PROTOCOL
            code = await self._turn(frame["turn_id"], frame["goal"])
            if code != EXIT_OK:
                return code

    async def _turn(self, turn_id, goal):
        self._turn_id = turn_id
        task = asyncio.ensure_future(self._run_sdk(turn_id, goal))
        try:
            while not task.done():
                getter = asyncio.ensure_future(self._next())
                done, _ = await asyncio.wait({task, getter}, return_when=asyncio.FIRST_COMPLETED)
                if getter not in done:
                    getter.cancel()
                    continue
                frame = getter.result()
                if frame is _EOF:
                    if task.done():
                        break  # the turn finished; EOF is the idle end
                    await self._abandon(task)
                    return EXIT_STDIN_CLOSED
                if (
                    frame is _BAD
                    or frame.get("type") != "permission_response"
                    or frame.get("decision") not in ("allow", "deny")
                    or frame.get("request_id") not in self._pending
                ):
                    self.send(
                        {"type": "error", "code": "protocol", "detail": "unexpected frame"}
                    )
                    await self._abandon(task)
                    return EXIT_PROTOCOL
                future = self._pending.pop(frame["request_id"])
                if not future.done():
                    future.set_result(frame["decision"])
            await task
            return EXIT_OK
        finally:
            self._turn_id = None

    async def _abandon(self, task):
        """Denies every pending request, lets the callbacks hand the denials
        to the SDK, then cancels the turn."""
        self._closed = True
        for future in list(self._pending.values()):
            if not future.done():
                future.set_result("deny")
        self._pending.clear()
        for _ in range(5):
            await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    async def _run_sdk(self, turn_id, goal):
        sdk = self.sdk
        status, usage, cost, error = "failed", None, None, ""
        saw_result = False
        try:
            options = sdk.ClaudeAgentOptions(
                cwd=REPO_ROOT,
                permission_mode=PERMISSION_MODE,
                allowed_tools=[],
                setting_sources=[],
                mcp_servers={},
                can_use_tool=self.can_use_tool,
            )
            async with sdk.ClaudeSDKClient(options=options) as client:
                await client.query(goal)
                async for message in client.receive_response():
                    if isinstance(message, sdk.AssistantMessage):
                        for block in getattr(message, "content", None) or ():
                            if isinstance(block, sdk.TextBlock) and isinstance(block.text, str):
                                self.send(
                                    {
                                        "type": "message",
                                        "turn_id": turn_id,
                                        "text": block.text[:MAX_TEXT_CHARS],
                                    }
                                )
                    elif isinstance(message, sdk.ResultMessage):
                        saw_result = True
                        ok = message.is_error is False and message.subtype == "success"
                        status = "completed" if ok else "failed"
                        usage = usage_frame(message.usage)
                        cost = cost_frame(message.total_cost_usd)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # reported to the host, never a hang
            status, error = "failed", type(exc).__name__[:100]
        if not saw_result and not error:
            error = "no_result"
        frame = {
            "type": "result",
            "turn_id": turn_id,
            "status": status,
            "usage": usage,
            "total_cost_usd": cost,
        }
        if error:
            frame["error"] = error
        self.send(frame)

    async def can_use_tool(self, tool_name, tool_input, context=None):
        sdk = self.sdk
        if self.policy.allows(tool_name, tool_input):
            return sdk.PermissionResultAllow(updated_input=tool_input)
        if self._closed or self._turn_id is None:
            return sdk.PermissionResultDeny(message=DENY_MESSAGE, interrupt=False)
        self._seq += 1
        request_id = f"perm-{self._seq}"
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        reason = tool_input.get("description") if isinstance(tool_input, dict) else None
        self.send(
            {
                "type": "permission_request",
                "request_id": request_id,
                "turn_id": self._turn_id,
                "tool_name": str(tool_name)[:200],
                "input": tool_input if isinstance(tool_input, dict) else {},
                "reason": reason if isinstance(reason, str) else "",
            }
        )
        try:
            decision = await future
        finally:
            self._pending.pop(request_id, None)
        if decision == "allow":
            return sdk.PermissionResultAllow(updated_input=tool_input)
        return sdk.PermissionResultDeny(message=DENY_MESSAGE, interrupt=False)


def parse_args(argv):
    test_command = ""
    args = list(argv)
    while args:
        flag = args.pop(0)
        if flag == "--test-command" and args:
            test_command = args.pop(0)
        else:
            return None
    return {"test_command": test_command}


def main(argv=None, *, stdin=None, stdout=None, sdk=None, repo_root=REPO_ROOT):
    options = parse_args(sys.argv[1:] if argv is None else argv)
    if options is None:
        sys.stderr.write("usage: claude_agent_bridge.py [--test-command CMD]\n")
        return EXIT_USAGE
    stdin = stdin if stdin is not None else sys.stdin.buffer
    stdout = stdout if stdout is not None else sys.stdout.buffer
    if sdk is None:
        try:
            import claude_agent_sdk as sdk
        except ImportError:
            line = json.dumps({"type": "error", "code": "sdk_unavailable", "detail": ""})
            stdout.write((line + "\n").encode("utf-8"))
            stdout.flush()
            return EXIT_SDK_UNAVAILABLE
    bridge = Bridge(
        sdk=sdk,
        stdin=stdin,
        stdout=stdout,
        policy=LocalPolicy(repo_root=repo_root, test_command=options["test_command"]),
    )
    return asyncio.run(bridge.serve())


if __name__ == "__main__":
    sys.exit(main())
