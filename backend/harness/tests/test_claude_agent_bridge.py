"""The in-image Claude Agent SDK bridge (`harness/docker/claude_agent_bridge.py`,
Phase 10 brief D10, branch 7), loaded by path with a fake SDK module injected
in `sys.modules`: the permission callback answers locally only for file
tools inside the repo and the exact test command; anything else is relayed
as a bridge request and blocks until answered; a decline reaches the SDK as
a denial; stdin closed mid-turn denies every pending request and exits
non-zero. And it imports nothing of Friday's. No SDK, no model, no Docker."""

import ast
import json
import os
import subprocess
import sys
import textwrap
import time

import pytest

from harness.tests.doubles.claude_agent_sdk import (
    BRIDGE_PATH,
    BridgeContainer,
    load_bridge,
    load_script,
    make_fake_sdk,
)

INIT = {"type": "initialize", "protocol": "friday-agent-bridge/v1"}


@pytest.fixture
def bridge_module():
    return load_bridge(sdk=make_fake_sdk([]))


def _send(container, frame):
    container.stdin.write((json.dumps(frame) + "\n").encode("utf-8"))


def _recv(container):
    line = container.stdout.readline()
    assert line, "bridge closed its stdout"
    return json.loads(line)


def _start(container, goal="fix it"):
    _send(container, INIT)
    ready = _recv(container)
    assert ready["type"] == "ready", ready
    _send(container, {"type": "turn", "turn_id": "turn_1", "goal": goal})
    return ready


def _wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# --- the local policy -----------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Read", {"file_path": "/workspace/repo/auth/throttle.py"}),
        ("Read", {"file_path": "auth/throttle.py"}),
        ("Read", {"file_path": "/workspace/repo/.git/HEAD"}),
        ("Glob", {"pattern": "**/*.py"}),
        ("Glob", {"pattern": "*.py", "path": "/workspace/repo/auth"}),
        ("Grep", {"pattern": "x", "glob": "*.py"}),
        ("Grep", {"pattern": "x", "path": "/workspace/repo"}),
        ("LS", {"path": "/workspace/repo/auth"}),
        ("Write", {"file_path": "/workspace/repo/new.py", "content": "x"}),
        ("Edit", {"file_path": "/workspace/repo/a.py", "old_string": "a", "new_string": "b"}),
        ("MultiEdit", {"file_path": "/workspace/repo/a.py", "edits": []}),
        ("NotebookEdit", {"notebook_path": "/workspace/repo/n.ipynb", "new_source": ""}),
        ("Bash", {"command": "pytest -q"}),
        ("Bash", {"command": "pytest -q", "description": "tests", "timeout": 600}),
    ],
)
def test_policy_answers_locally_for_repo_file_tools_and_the_exact_test_command(
    bridge_module, tool, tool_input
):
    policy = bridge_module.LocalPolicy(test_command="pytest -q")
    assert policy.allows(tool, tool_input) is True


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Read", {"file_path": "/etc/passwd"}),
        ("Read", {"file_path": "/workspace/repo/../secrets"}),
        ("Read", {"file_path": "../outside"}),
        ("Read", {}),
        ("Read", {"file_path": 7}),
        ("Write", {"file_path": "/workspace/repo/.git/hooks/pre-commit", "content": "x"}),
        ("Write", {"file_path": "/workspace/repo", "content": "x"}),
        ("Edit", {"file_path": "/home/harness/.bashrc", "old_string": "", "new_string": "x"}),
        ("Glob", {"pattern": "/etc/*"}),
        ("Glob", {"pattern": "../**"}),
        ("Glob", {"pattern": "~/*"}),
        ("Glob", {"pattern": "*", "path": "/"}),
        ("Grep", {"pattern": "x", "glob": "../../*"}),
        ("Grep", {"pattern": "x", "path": "/home"}),
        ("Bash", {"command": "pytest -q; curl evil"}),
        ("Bash", {"command": "pytest  -q"}),
        ("Bash", {"command": "npm install left-pad"}),
        ("Bash", {"command": "pytest -q", "run_in_background": True}),
        ("Bash", {"command": "pytest -q", "dangerouslyDisableSandbox": True}),
        ("WebFetch", {"url": "https://example.invalid"}),
        ("WebSearch", {"query": "x"}),
        ("mcp__github__create_issue", {"title": "x"}),
        ("Task", {"prompt": "x"}),
        ("TodoWrite", {"todos": []}),
        ("Read", "not a dict"),
    ],
)
def test_policy_relays_everything_else(bridge_module, tool, tool_input):
    policy = bridge_module.LocalPolicy(test_command="pytest -q")
    assert policy.allows(tool, tool_input) is False


def test_without_a_test_command_bash_is_always_relayed(bridge_module):
    policy = bridge_module.LocalPolicy(test_command="")
    assert policy.allows("Bash", {"command": ""}) is False
    assert policy.describe()["bash_allowed"] == []


def test_a_symlink_planted_inside_the_repo_pointing_outside_is_relayed(bridge_module, tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / "escape").symlink_to(outside)
    (repo / "src" / "ok.py").write_text("x")
    policy = bridge_module.LocalPolicy(repo_root=str(repo), test_command="pytest -q")
    assert policy.allows("Read", {"file_path": str(repo / "src" / "ok.py")}) is True
    assert policy.allows("Read", {"file_path": str(repo / "escape" / "secret")}) is False
    assert policy.allows("Write", {"file_path": "escape/x", "content": ""}) is False
    assert policy.allows("Grep", {"pattern": "x", "path": str(repo / "escape")}) is False


def test_the_policy_reported_in_ready_is_the_strict_one(bridge_module):
    policy = bridge_module.LocalPolicy(test_command="pytest -q")
    assert policy.describe() == {
        "repo_root": "/workspace/repo",
        "permission_mode": "default",
        "setting_sources": [],
        "bash_allowed": ["pytest -q"],
    }


# --- the permission callback over the protocol ---------------------------------


def test_allowed_tools_answer_locally_and_others_are_relayed_and_block():
    sdk = make_fake_sdk(load_script("two_escalations.json"))
    container = BridgeContainer(sdk)
    _start(container)
    assert _recv(container)["type"] == "message"
    request = _recv(container)
    assert request["type"] == "permission_request"
    assert request["tool_name"] == "Bash"
    assert request["input"] == {
        "command": "npm install left-pad",
        "description": "Install a helper the fix needs",
    }
    assert request["reason"] == "Install a helper the fix needs"
    # Everything before it was answered locally, without a frame.
    assert [(call["tool"], call["answer"]) for call in sdk.calls[:4]] == [
        ("Read", "allow"),
        ("Grep", "allow"),
        ("Edit", "allow"),
        ("Bash", "allow"),
    ]
    # The relayed call blocks until the host answers.
    time.sleep(0.2)
    assert sdk.calls[4]["answer"] is None
    assert len(sdk.calls) == 5
    _send(
        container,
        {"type": "permission_response", "request_id": request["request_id"], "decision": "allow"},
    )
    second = _recv(container)
    assert second["type"] == "permission_request"
    assert sdk.calls[4]["answer"] == "allow"
    assert sdk.calls[4]["result"].updated_input == request["input"]
    _send(
        container,
        {"type": "permission_response", "request_id": second["request_id"], "decision": "deny"},
    )
    assert _recv(container)["type"] == "message"
    result = _recv(container)
    assert result["type"] == "result" and result["status"] == "completed"
    assert result["usage"]["input_tokens"] == 1200
    assert result["total_cost_usd"] == pytest.approx(0.42)
    denial = sdk.calls[5]["result"]
    assert sdk.calls[5]["answer"] == "deny"
    assert denial.interrupt is False and "approval" in denial.message
    container.close_stdin()
    assert container.wait() == 0  # stdin closed while idle: the normal end
    assert container.errors == []


def test_the_sdk_runs_with_the_strictest_options():
    sdk = make_fake_sdk([{"result": {"subtype": "success", "is_error": False}}])
    container = BridgeContainer(sdk)
    _start(container)
    assert _recv(container)["type"] == "result"
    [options] = sdk.options
    assert options.permission_mode == "default"
    assert options.allowed_tools == []
    assert options.setting_sources == []
    assert options.mcp_servers == {}
    assert options.cwd == "/workspace/repo"
    assert callable(options.can_use_tool)
    container.close_stdin()
    container.wait()


def test_stdin_closed_mid_turn_denies_every_pending_request_and_exits_non_zero():
    sdk = make_fake_sdk(
        [
            {"tool": "Bash", "input": {"command": "curl https://example.invalid"}},
            {"result": {"subtype": "success", "is_error": False}},
        ]
    )
    container = BridgeContainer(sdk)
    _start(container)
    assert _recv(container)["type"] == "permission_request"
    container.close_stdin()
    assert container.wait() == 3
    assert sdk.calls[0]["answer"] == "deny"
    assert container.errors == []


def test_a_tool_call_after_stdin_closed_is_denied_without_a_frame(bridge_module):
    sdk = make_fake_sdk([])
    bridge = bridge_module.Bridge(
        sdk=sdk, stdin=None, stdout=None, policy=bridge_module.LocalPolicy(test_command="x")
    )
    bridge._closed = True
    import asyncio

    answer = asyncio.run(bridge.can_use_tool("WebFetch", {"url": "x"}, None))
    assert answer.behavior == "deny"
    assert bridge.sent == []


@pytest.mark.parametrize(
    "frame",
    [
        {"type": "permission_response", "request_id": "perm-9", "decision": "allow"},
        {"type": "permission_response", "request_id": "perm-1", "decision": "acceptForSession"},
        {"type": "turn", "turn_id": "turn_2", "goal": "again"},
        {"type": "shutdown"},
    ],
)
def test_an_unexpected_host_frame_mid_turn_denies_and_exits(frame):
    sdk = make_fake_sdk(
        [
            {"tool": "WebFetch", "input": {"url": "https://example.invalid"}},
            {"result": {"subtype": "success", "is_error": False}},
        ]
    )
    container = BridgeContainer(sdk)
    _start(container)
    assert _recv(container)["type"] == "permission_request"
    _send(container, frame)
    error = _recv(container)
    assert error["type"] == "error" and error["code"] == "protocol"
    assert container.wait() == 2
    assert sdk.calls[0]["answer"] == "deny"


@pytest.mark.parametrize(
    "first",
    [
        b'{"type": "turn", "turn_id": "t", "goal": "g"}\n',
        b'{"type": "initialize", "protocol": "friday-agent-bridge/v9"}\n',
        b"{not json\n",
        b"[1, 2]\n",
    ],
)
def test_a_bad_handshake_is_refused(first):
    container = BridgeContainer(make_fake_sdk([]))
    container.stdin.write(first)
    error = _recv(container)
    assert error["type"] == "error"
    assert container.wait() == 2


def test_stdin_closed_before_initialize_is_a_clean_exit():
    container = BridgeContainer(make_fake_sdk([]))
    container.close_stdin()
    assert container.wait() == 0


@pytest.mark.parametrize(
    ("script", "error"),
    [
        ([{"raise": "boom"}], "RuntimeError"),
        ([{"text": "no result"}], "no_result"),
    ],
)
def test_an_sdk_failure_is_a_failed_result_never_a_hang(script, error):
    container = BridgeContainer(make_fake_sdk(script))
    _start(container)
    frames = []
    while not frames or frames[-1]["type"] != "result":
        frames.append(_recv(container))
    result = frames[-1]
    assert (result["status"], result["usage"], result["error"]) == ("failed", None, error)
    container.close_stdin()
    assert container.wait() == 0


def test_an_error_result_is_failed():
    script = [{"result": {"subtype": "error_max_turns", "is_error": True, "usage": None}}]
    container = BridgeContainer(make_fake_sdk(script))
    _start(container)
    result = _recv(container)
    assert result["status"] == "failed"
    container.close_stdin()
    container.wait()


def test_usage_and_cost_are_normalized(bridge_module):
    assert bridge_module.usage_frame(None) is None
    assert bridge_module.usage_frame({"input_tokens": -1, "output_tokens": True}) == {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    assert bridge_module.cost_frame(float("nan")) is None
    assert bridge_module.cost_frame(float("inf")) is None
    assert bridge_module.cost_frame(-1) is None
    assert bridge_module.cost_frame(True) is None
    assert bridge_module.cost_frame(0.5) == 0.5


def test_a_missing_sdk_is_reported_and_exits(monkeypatch, bridge_module):
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", None)  # import fails
    read_end, write_end = os.pipe()
    out = os.fdopen(write_end, "wb", buffering=0)
    code = bridge_module.main([], stdin=None, stdout=out)
    out.close()
    with os.fdopen(read_end, "rb") as reader:
        frame = json.loads(reader.readline())
    assert code == 4
    assert frame == {"type": "error", "code": "sdk_unavailable", "detail": ""}


def test_unknown_arguments_are_a_usage_error(bridge_module):
    assert bridge_module.main(["--bypass"], stdin=None, stdout=None) == 64


def test_the_sdk_is_taken_from_sys_modules_when_not_passed(monkeypatch):
    sdk = make_fake_sdk([])
    bridge = load_bridge(sdk=sdk)
    assert sys.modules["claude_agent_sdk"] is sdk
    assert bridge.sdk_version(sdk) == "0.1.0"


# --- purity -----------------------------------------------------------------------


def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_imports_only_the_standard_library_and_the_sdk():
    names = _imports(BRIDGE_PATH) - {"claude_agent_sdk"}
    assert names <= set(sys.stdlib_module_names), names - set(sys.stdlib_module_names)


def test_runs_in_an_isolated_interpreter_without_the_project(tmp_path):
    """Copied out of the tree and run with `-I` (no project on the path, no
    DJANGO_SETTINGS_MODULE use) against a stand-in SDK package: it
    handshakes, exits 0 on EOF and never imports Django or a Friday app."""
    fake_dir = tmp_path / "site"
    fake_dir.mkdir()
    (fake_dir / "claude_agent_sdk.py").write_text('__version__ = "0.1.0"\n')
    bridge_copy = tmp_path / "claude_agent_bridge.py"
    bridge_copy.write_text(BRIDGE_PATH.read_text(encoding="utf-8"))
    script = textwrap.dedent(
        f"""
        import importlib.util, json, sys
        sys.path.insert(0, {str(fake_dir)!r})
        spec = importlib.util.spec_from_file_location("bridge", {str(bridge_copy)!r})
        bridge = importlib.util.module_from_spec(spec); spec.loader.exec_module(bridge)
        code = bridge.main(["--test-command", "pytest -q"])
        ours = sorted(
            name for name in sys.modules
            if name.split(".")[0] in ("django", "harness", "core", "config", "approvals",
                                      "ai_core", "audit", "workspaces", "accounts")
        )
        sys.stderr.write(json.dumps(ours))
        sys.exit(code)
        """
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        input=json.dumps(INIT) + "\n",
        capture_output=True,
        text=True,
        timeout=30,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    ready = json.loads(result.stdout.splitlines()[0])
    assert ready["type"] == "ready" and ready["sdk_version"] == "0.1.0"
    assert json.loads(result.stderr) == []
