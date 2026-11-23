"""The Claude Agent SDK driver (`harness/drivers/claude_agent_sdk.py`, Phase 10
brief D10, branch 7) over pipes: against the real bridge with the fake SDK,
and against a scripted raw peer for every protocol fault. The driver checks
the bridge's sandbox policy and SDK version, binds each escalation to exactly
what would run, answers only `allow`/`deny` for one request id, takes usage
and cost from the result frame, and fails closed with a deterministic code
on anything else. No SDK, no Docker, no model."""

import json
import os
import threading
import time
from decimal import Decimal

import pytest

from harness.drivers.base import (
    CODE_DEADLINE,
    CODE_EARLY_EXIT,
    CODE_MALFORMED_JSON,
    CODE_OVERSIZE_LINE,
    CODE_PROTOCOL_ERROR,
    CODE_UNKNOWN_METHOD,
    DriverError,
)
from harness.drivers.claude_agent_sdk import ClaudeAgentSdkDriver, escalation_for
from harness.drivers.claude_surface import load_bridge_schema
from harness.tests.doubles.claude_agent_sdk import BridgeContainer, load_script, make_fake_sdk

READY = {
    "type": "ready",
    "protocol": "friday-agent-bridge/v1",
    "sdk_version": "0.1.0",
    "policy": {
        "repo_root": "/workspace/repo",
        "permission_mode": "default",
        "setting_sources": [],
        "bash_allowed": ["pytest -q"],
    },
}


def _driver(*, deadline=None, test_command="pytest -q", sdk_version="0.1.0", **kwargs):
    return ClaudeAgentSdkDriver(
        bridge_schema=load_bridge_schema(),
        sdk_version=sdk_version,
        deadline=deadline if deadline is not None else time.monotonic() + 30,
        test_command=test_command,
        poll_seconds=0.05,
        **kwargs,
    )


class RawPeer:
    """A scripted bridge: `read` consumes one host frame (recorded),
    `send` writes a frame, `raw` writes bytes, `exit` closes stdout,
    `hang` waits for the host to close stdin."""

    def __init__(self, steps):
        self.steps = steps
        self.received = []
        c2s_read, c2s_write = os.pipe()
        s2c_read, s2c_write = os.pipe()
        self.stdin = os.fdopen(c2s_write, "wb", buffering=0)
        self.stdout = os.fdopen(s2c_read, "rb")
        self._in = os.fdopen(c2s_read, "rb")
        self._out = os.fdopen(s2c_write, "wb", buffering=0)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        try:
            for step in self.steps:
                if "read" in step:
                    line = self._in.readline()
                    if not line:
                        return
                    self.received.append(json.loads(line))
                elif "send" in step:
                    self._out.write((json.dumps(step["send"]) + "\n").encode())
                elif "raw" in step:
                    self._out.write(step["raw"])
                elif "exit" in step:
                    return
                elif "hang" in step:
                    while self._in.readline():
                        pass
                    return
            while self._in.readline():
                pass
        except (OSError, ValueError):
            pass
        finally:
            try:
                self._out.close()
            except OSError:
                pass


HANDSHAKE = [{"read": True}, {"send": READY}, {"read": True}]


def _drain(driver):
    return list(driver.events())


def _run(driver, container, goal="fix it"):
    driver.start(container)
    driver.run_turn(goal)


# --- against the real bridge --------------------------------------------------


def test_driver_and_bridge_run_a_turn_with_two_escalations():
    sdk = make_fake_sdk(load_script("two_escalations.json"))
    container = BridgeContainer(sdk)
    driver = _driver()
    _run(driver, container)
    escalations, messages, completed = [], [], None
    for event in driver.events():
        if event.kind == "escalation":
            escalation = event.payload
            escalations.append(escalation)
            driver.answer_approval(escalation.request_id, "npm" in escalation.command)
        elif event.kind == "message":
            messages.append(event.payload)
        elif event.kind == "turn_completed":
            completed = event.payload
    driver.close()
    assert [(e.kind, e.command, e.cwd) for e in escalations] == [
        ("command", "npm install left-pad", "/workspace/repo"),
        ("command", "curl https://example.invalid/x", "/workspace/repo"),
    ]
    assert escalations[0].reason == "Install a helper the fix needs"
    assert [(c["tool"], c["answer"]) for c in sdk.calls][-2:] == [
        ("Bash", "allow"),
        ("Bash", "deny"),
    ]
    assert messages == ["Looking at the login throttle test.", "Fixed the login throttle test."]
    assert completed.status == "completed" and completed.reported is True
    assert (completed.input_tokens, completed.output_tokens) == (1200, 500)
    assert completed.cached_input_tokens == 300
    assert completed.cache_creation_input_tokens == 40
    assert completed.cost_usd == Decimal("0.42")
    assert driver.usage() == [completed]
    assert "login throttle" in driver.transcript.text()
    # What the driver sent: never anything but allow/deny for one id.
    decisions = [f["decision"] for f in driver.sent if f["type"] == "permission_response"]
    assert decisions == ["allow", "deny"]
    assert container.wait() == 0


def test_every_frame_on_the_wire_matches_the_bridge_schema():
    schema = load_bridge_schema()
    sdk = make_fake_sdk(load_script("two_escalations.json"))
    container = BridgeContainer(sdk)
    driver = _driver()
    _run(driver, container)
    for event in driver.events():
        if event.kind == "escalation":
            driver.answer_approval(event.payload.request_id, False)
    driver.close()
    container.wait()
    sent_by_bridge = container.frames_sent()
    assert {f["type"] for f in sent_by_bridge} == {
        "ready",
        "message",
        "permission_request",
        "result",
    }
    assert all(schema.validate("bridge_to_host", f) for f in sent_by_bridge)
    assert all(schema.validate("host_to_bridge", f) for f in driver.sent)


# --- handshake ------------------------------------------------------------------


@pytest.mark.parametrize(
    "ready",
    [
        {**READY, "sdk_version": "0.2.0"},
        {**READY, "policy": {**READY["policy"], "permission_mode": "bypassPermissions"}},
        {**READY, "policy": {**READY["policy"], "permission_mode": "acceptEdits"}},
        {**READY, "policy": {**READY["policy"], "setting_sources": ["project"]}},
        {**READY, "policy": {**READY["policy"], "bash_allowed": ["pytest"]}},
        {**READY, "policy": {**READY["policy"], "repo_root": "/"}},
        {"type": "error", "code": "sdk_unavailable"},
        {"type": "message", "turn_id": "turn_1", "text": "hi"},
    ],
)
def test_the_handshake_refuses_anything_but_our_policy_and_pin(ready):
    peer = RawPeer([{"read": True}, {"send": ready}])
    with pytest.raises(DriverError) as exc:
        _driver().start(peer)
    assert exc.value.code == CODE_PROTOCOL_ERROR
    assert peer.received == [{"type": "initialize", "protocol": "friday-agent-bridge/v1"}]


def test_a_bridge_without_the_test_command_matches_a_job_without_one():
    ready = {**READY, "policy": {**READY["policy"], "bash_allowed": []}}
    peer = RawPeer([{"read": True}, {"send": ready}])
    _driver(test_command="").start(peer)


# --- robustness -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tail", "code"),
    [
        ([{"raw": b"{oops\n"}], CODE_MALFORMED_JSON),
        ([{"send": {"type": "telemetry", "x": 1}}], CODE_UNKNOWN_METHOD),
        ([{"send": {"no_type": True}}], CODE_PROTOCOL_ERROR),
        ([{"send": {"type": "message", "turn_id": "turn_1"}}], CODE_PROTOCOL_ERROR),
        ([{"send": {"type": "message", "turn_id": "turn_9", "text": "x"}}], CODE_PROTOCOL_ERROR),
        ([{"send": READY}], CODE_PROTOCOL_ERROR),
        ([{"send": {"type": "error", "code": "protocol"}}], CODE_PROTOCOL_ERROR),
        (
            [
                {
                    "send": {
                        "type": "result",
                        "turn_id": "turn_1",
                        "status": "completed",
                        "usage": {"input_tokens": -5},
                        "total_cost_usd": 1,
                    }
                }
            ],
            CODE_PROTOCOL_ERROR,
        ),
        ([{"exit": True}], CODE_EARLY_EXIT),
    ],
)
def test_protocol_faults_fail_closed(tail, code):
    peer = RawPeer([*HANDSHAKE, *tail])
    driver = _driver()
    _run(driver, peer)
    with pytest.raises(DriverError) as exc:
        _drain(driver)
    assert exc.value.code == code


def test_a_duplicate_request_id_is_a_protocol_error():
    request = {
        "type": "permission_request",
        "request_id": "perm-1",
        "turn_id": "turn_1",
        "tool_name": "WebFetch",
        "input": {"url": "x"},
        "reason": "",
    }
    peer = RawPeer([*HANDSHAKE, {"send": request}, {"send": request}])
    driver = _driver()
    _run(driver, peer)
    events = driver.events()
    assert next(events).kind == "escalation"
    with pytest.raises(DriverError) as exc:
        next(events)
    assert exc.value.code == CODE_PROTOCOL_ERROR


def test_a_result_with_an_unanswered_request_is_a_protocol_error():
    request = {
        "type": "permission_request",
        "request_id": "perm-1",
        "turn_id": "turn_1",
        "tool_name": "WebFetch",
        "input": {"url": "x"},
        "reason": "",
    }
    result = {
        "type": "result",
        "turn_id": "turn_1",
        "status": "completed",
        "usage": None,
        "total_cost_usd": None,
    }
    peer = RawPeer([*HANDSHAKE, {"send": request}, {"send": result}])
    driver = _driver()
    _run(driver, peer)
    events = driver.events()
    assert next(events).kind == "escalation"
    with pytest.raises(DriverError) as exc:
        next(events)
    assert exc.value.code == CODE_PROTOCOL_ERROR


def test_an_oversize_line_fails_closed():
    peer = RawPeer([{"read": True}, {"raw": b'{"type": "ready", "pad": "' + b"x" * 500 + b'"}\n'}])
    driver = _driver(max_line_bytes=64)
    with pytest.raises(DriverError) as exc:
        driver.start(peer)
    assert exc.value.code == CODE_OVERSIZE_LINE


def test_the_deadline_fails_closed():
    peer = RawPeer([*HANDSHAKE, {"hang": True}])
    driver = _driver(deadline=time.monotonic() + 0.5)
    _run(driver, peer)
    with pytest.raises(DriverError) as exc:
        _drain(driver)
    assert exc.value.code == CODE_DEADLINE
    driver.close()


def test_idle_slices_are_yielded_while_nothing_arrives():
    peer = RawPeer([*HANDSHAKE, {"hang": True}])
    driver = _driver()
    _run(driver, peer)
    assert next(driver.events()).kind == "idle"
    driver.close()


def test_a_turn_without_usage_is_unreported():
    result = {
        "type": "result",
        "turn_id": "turn_1",
        "status": "failed",
        "usage": None,
        "total_cost_usd": None,
        "error": "RuntimeError",
    }
    peer = RawPeer([*HANDSHAKE, {"send": result}])
    driver = _driver()
    _run(driver, peer)
    [event] = _drain(driver)
    assert (event.payload.reported, event.payload.status, event.payload.cost_usd) == (
        False,
        "failed",
        None,
    )


def test_answering_an_unknown_request_is_refused():
    peer = RawPeer([*HANDSHAKE, {"hang": True}])
    driver = _driver()
    _run(driver, peer)
    with pytest.raises(ValueError):
        driver.answer_approval("perm-1", True)
    driver.close()


def test_only_the_literal_true_is_an_allow():
    request = {
        "type": "permission_request",
        "request_id": "perm-1",
        "turn_id": "turn_1",
        "tool_name": "WebFetch",
        "input": {"url": "x"},
        "reason": "",
    }
    peer = RawPeer([*HANDSHAKE, {"send": request}, {"read": True}])
    driver = _driver()
    _run(driver, peer)
    escalation = next(driver.events()).payload
    driver.answer_approval(escalation.request_id, "yes")
    driver.close()
    peer.thread.join(5)
    assert peer.received[-1]["decision"] == "deny"


# --- escalation rendering -----------------------------------------------------------


def test_a_plain_bash_call_shows_the_command_verbatim():
    e = escalation_for("p", "Bash", {"command": "npm ci", "description": "d"}, "d", cwd="/w")
    assert (e.kind, e.command, e.cwd, e.reason) == ("command", "npm ci", "/w", "d")


def test_a_bash_call_with_extra_options_shows_its_whole_input():
    e = escalation_for("p", "Bash", {"command": "x", "run_in_background": True}, "", cwd="/w")
    assert e.kind == "command"
    assert e.command == 'Bash {"command":"x","run_in_background":true}'


@pytest.mark.parametrize(
    ("tool", "kind"),
    [
        ("Write", "file_change"),
        ("Edit", "file_change"),
        ("WebFetch", "tool"),
        ("mcp__x__y", "tool"),
    ],
)
def test_other_tools_show_their_whole_input_as_canonical_json(tool, kind):
    e = escalation_for("p", tool, {"z": 1, "a": "ü"}, "", cwd="/w")
    assert e.kind == kind
    assert e.command == f'{tool} {{"a":"ü","z":1}}'
