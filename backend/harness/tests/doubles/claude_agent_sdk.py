"""Fake `claude-agent-sdk` and an in-process bridge "container" (Phase 10
brief D10, branch 7 test doubles).

`make_fake_sdk(script)` builds a module that stands in for the pinned SDK:
the names the bridge uses (`SDK_SURFACE_USED`) with the provisional
surface's shapes. Its `ClaudeSDKClient` replays a script instead of calling
a model:

- `{"text": "..."}` -> an `AssistantMessage` with one `TextBlock`;
- `{"tool": name, "input": {...}}` -> awaits the options' `can_use_tool`
  callback, exactly as the SDK does before running a tool, and records the
  answer in `module.calls` (the tool itself never runs);
- `{"result": {...}}` -> a `ResultMessage` (subtype, is_error, usage,
  total_cost_usd) that ends the response;
- `{"raise": "RuntimeError"}` -> the SDK fails mid-turn;
- `{"hang": true}` -> never returns (until cancelled).

`BridgeContainer` runs the real `harness/docker/claude_agent_bridge.py`
(loaded by path, with the fake SDK injected in `sys.modules`) on a thread
over real OS pipes, standing in for `docker run -i` of the Claude Code
image in driver mode: the host driver writes `stdin`, reads `stdout`. No
Docker, no SDK, no model.
"""

import asyncio
import importlib.util
import json
import os
import sys
import threading
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BRIDGE_PATH = Path(__file__).resolve().parents[2] / "docker" / "claude_agent_bridge.py"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "claude_agent_sdk"
FAKE_SDK_VERSION = "0.1.0"


def load_bridge(*, sdk=None, name="claude_agent_bridge_under_test"):
    """The bridge module, loaded by path (it is not a package module). With
    `sdk`, the fake is injected in `sys.modules` first, as the brief asks."""
    if sdk is not None:
        sys.modules["claude_agent_sdk"] = sdk
    spec = importlib.util.spec_from_file_location(name, BRIDGE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_script(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def make_fake_sdk(script, *, version=FAKE_SDK_VERSION):
    sdk = types.ModuleType("claude_agent_sdk")
    sdk.__version__ = version
    sdk.calls = []
    sdk.options = []

    @dataclass
    class ClaudeAgentOptions:
        cwd: Any = None
        permission_mode: Any = None
        allowed_tools: list = field(default_factory=list)
        setting_sources: Any = None
        mcp_servers: Any = field(default_factory=dict)
        can_use_tool: Any = None

    @dataclass
    class TextBlock:
        text: str

    @dataclass
    class AssistantMessage:
        content: list
        model: str = "fake"

    @dataclass
    class ResultMessage:
        subtype: str
        is_error: bool
        usage: Any = None
        total_cost_usd: Any = None
        duration_ms: int = 0
        num_turns: int = 1
        session_id: str = "fake-session"

    @dataclass
    class PermissionResultAllow:
        updated_input: Any = None
        behavior: str = "allow"

    @dataclass
    class PermissionResultDeny:
        message: str = ""
        interrupt: bool = False
        behavior: str = "deny"

    @dataclass
    class ToolPermissionContext:
        suggestions: list = field(default_factory=list)

    class ClaudeSDKClient:
        def __init__(self, options=None):
            self.options = options
            self._prompt = None
            sdk.options.append(options)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def query(self, prompt):
            self._prompt = prompt

        async def receive_response(self):
            for step in script:
                if "text" in step:
                    yield AssistantMessage(content=[TextBlock(text=step["text"])])
                elif "tool" in step:
                    entry = {"tool": step["tool"], "input": step["input"], "answer": None}
                    sdk.calls.append(entry)
                    answer = await self.options.can_use_tool(
                        step["tool"], step["input"], ToolPermissionContext()
                    )
                    entry["answer"] = answer.behavior
                    entry["result"] = answer
                elif "result" in step:
                    yield ResultMessage(**step["result"])
                    return
                elif "raise" in step:
                    raise RuntimeError(step["raise"])
                elif "hang" in step:
                    await asyncio.Event().wait()

    for item in (
        ClaudeAgentOptions,
        TextBlock,
        AssistantMessage,
        ResultMessage,
        PermissionResultAllow,
        PermissionResultDeny,
        ToolPermissionContext,
        ClaudeSDKClient,
    ):
        item.__module__ = "claude_agent_sdk"
        setattr(sdk, item.__name__, item)
    sdk.__all__ = [
        "ClaudeAgentOptions",
        "TextBlock",
        "AssistantMessage",
        "ResultMessage",
        "PermissionResultAllow",
        "PermissionResultDeny",
        "ToolPermissionContext",
        "ClaudeSDKClient",
    ]
    return sdk


def answers(sdk):
    """`[(tool, input, "allow" | "deny" | None)]` in call order."""
    return [(call["tool"], call["input"], call["answer"]) for call in sdk.calls]


class _Tee:
    """The bridge's stdout: forwards to the pipe and keeps a copy, so tests
    can validate every frame the bridge sent."""

    def __init__(self, out):
        self._out = out
        self.data = bytearray()

    def write(self, data):
        self.data.extend(data)
        return self._out.write(data)

    def flush(self):
        self._out.flush()

    def close(self):
        self._out.close()


class BridgeContainer:
    """The real bridge on a thread, over OS pipes; the InteractiveContainer
    surface the driver and the job use."""

    def __init__(self, sdk, *, test_command="pytest -q", name="harness-impl-fake", repo_root=None):
        self.name = name
        self.sdk = sdk
        self.bridge = load_bridge(sdk=sdk)
        self.exit_code = None
        self.errors = []
        self.closed_stdin = False
        self.killed = False
        self.reaped = False
        c2s_read, c2s_write = os.pipe()
        s2c_read, s2c_write = os.pipe()
        self.stdin = os.fdopen(c2s_write, "wb", buffering=0)
        self.stdout = os.fdopen(s2c_read, "rb")
        self._bridge_in = os.fdopen(c2s_read, "rb")
        self._bridge_out = _Tee(os.fdopen(s2c_write, "wb", buffering=0))
        argv = ["--test-command", test_command] if test_command else []
        kwargs = {"repo_root": repo_root} if repo_root is not None else {}

        def run():
            try:
                self.exit_code = self.bridge.main(
                    argv, stdin=self._bridge_in, stdout=self._bridge_out, sdk=sdk, **kwargs
                )
            except Exception as exc:  # recorded for the test
                self.errors.append(repr(exc))
            finally:
                try:
                    self._bridge_out.close()
                except OSError:
                    pass

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def frames_sent(self):
        return [json.loads(line) for line in bytes(self._bridge_out.data).splitlines() if line]

    def close_stdin(self):
        self.closed_stdin = True
        try:
            self.stdin.close()
        except OSError:
            pass

    def kill(self):
        self.killed = True
        self.close_stdin()

    def reap(self, timeout=5):
        self.reaped = True
        self.thread.join(timeout)

    def wait(self, timeout=5):
        self.thread.join(timeout)
        return self.exit_code
