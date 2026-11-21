"""The Codex app-server driver against the fake app-server double over real
pipes (Phase 10 brief D10, branch 6). No Codex binary, no Docker, no DB."""

import json
import time

import pytest

from harness.drivers.base import (
    CODE_DEADLINE,
    CODE_EARLY_EXIT,
    CODE_MALFORMED_JSON,
    CODE_OVERSIZE_LINE,
    CODE_PROTOCOL_ERROR,
    CODE_TOO_MANY_LINES,
    CODE_UNKNOWN_METHOD,
    SANDBOX_POLICY,
    DriverError,
)
from harness.drivers.codex_app_server import CodexAppServerDriver
from harness.drivers.schema import load_codex_snapshot
from harness.tests.doubles.codex_app_server import (
    FakeContainer,
    load_transcript,
    script_from_transcript,
)

SNAPSHOT = load_codex_snapshot("0.1.0")
HANDSHAKE = script_from_transcript(load_transcript("two_escalations.jsonl"))[:3]


def _driver(**overrides):
    kwargs = {"snapshot": SNAPSHOT, "deadline": time.monotonic() + 10, "poll_seconds": 0.05}
    kwargs.update(overrides)
    return CodexAppServerDriver(**kwargs)


def _drain(driver, decide=lambda escalation: False):
    """Runs the event loop, answering escalations with `decide`. Returns
    (events, escalations)."""
    events, escalations = [], []
    for event in driver.events():
        events.append(event)
        if event.kind == "escalation":
            escalations.append(event.payload)
            driver.answer_approval(event.payload.request_id, decide(event.payload))
    return events, escalations


def _turn_reply(turn_id="turn_1"):
    return {"expect": "turn/start", "before": [], "reply": {"result": {"turn": {"id": turn_id}}}}


def _completed(turn_id="turn_1", status="completed"):
    return {
        "send": {
            "method": "turn/completed",
            "params": {"threadId": "thr_1", "turn": {"id": turn_id, "status": status}},
        }
    }


def test_full_transcript_one_accept_one_decline():
    steps = script_from_transcript(load_transcript("two_escalations.jsonl"))
    container = FakeContainer(steps)
    driver = _driver()
    driver.start(container)
    assert driver.thread_id == "thr_1"
    driver.run_turn("fix the login throttle test")
    events, escalations = _drain(driver, decide=lambda e: "npm" in e.command)
    driver.close()
    container.reap()
    assert container.errors == []
    assert [e.kind for e in events if e.kind != "idle"] == [
        "escalation",
        "escalation",
        "message",
        "turn_completed",
    ]
    assert escalations[0].command == "npm install left-pad"
    assert escalations[0].reason == "install the test dependency"
    assert escalations[1].command.startswith("curl ")
    assert container.answer(100) == "accept"
    assert container.answer(101) == "decline"
    [turn] = driver.usage()
    assert turn.reported is True
    assert (turn.input_tokens, turn.output_tokens) == (1200, 500)
    assert turn.cached_input_tokens == 300
    assert "login throttle" in driver.transcript.text()


def test_handshake_sends_the_strict_sandbox_policy():
    steps = [*HANDSHAKE, _turn_reply(), _completed()]
    container = FakeContainer(steps)
    driver = _driver()
    driver.start(container)
    driver.run_turn("goal")
    _drain(driver)
    driver.close()
    sent = dict(driver.sent)
    policy = SANDBOX_POLICY["codex_cli"]
    assert sent["thread/start"]["sandbox"] == "workspace-write"
    assert sent["thread/start"]["approvalPolicy"] == "on-request"
    assert sent["turn/start"]["sandboxPolicy"] == {
        "type": "workspaceWrite",
        "writableRoots": list(policy["writable_roots"]),
        "networkAccess": False,
    }
    assert "danger-full-access" not in json.dumps(container.received)
    assert "never" not in json.dumps([m.get("params") for m in container.received])


def test_driver_never_sends_accept_for_session():
    steps = script_from_transcript(load_transcript("two_escalations.jsonl"))
    container = FakeContainer(steps)
    driver = _driver()
    driver.start(container)
    driver.run_turn("goal")
    _drain(driver, decide=lambda e: True)
    driver.close()
    container.reap()
    decisions = {m["result"]["decision"] for m in container.received if "result" in m}
    assert decisions == {"accept"}


def test_answer_is_only_accept_for_literal_true():
    steps = script_from_transcript(load_transcript("two_escalations.jsonl"))
    container = FakeContainer(steps)
    driver = _driver()
    driver.start(container)
    driver.run_turn("goal")
    _drain(driver, decide=lambda e: "yes")  # truthy, not True
    driver.close()
    container.reap()
    assert container.answer(100) == "decline"


def test_answer_for_an_unknown_request_id_is_refused():
    container = FakeContainer([*HANDSHAKE])
    driver = _driver()
    driver.start(container)
    with pytest.raises(ValueError):
        driver.answer_approval(999, True)
    driver.close()


def test_file_change_escalation_and_turn_without_usage():
    steps = script_from_transcript(load_transcript("file_change_no_usage.jsonl"))
    container = FakeContainer(steps)
    driver = _driver()
    driver.start(container)
    driver.run_turn("tidy")
    _events, escalations = _drain(driver)
    driver.close()
    container.reap()
    assert escalations[0].kind == "file_change"
    assert escalations[0].cwd == "/etc"
    assert container.answer("fc-1") == "decline"
    [turn] = driver.usage()
    assert turn.reported is False


def _run_until_error(steps, **driver_kwargs):
    container = FakeContainer(steps)
    driver = _driver(**driver_kwargs)
    try:
        driver.start(container)
        driver.run_turn("goal")
        _drain(driver)
    except DriverError as exc:
        return exc.code, container
    finally:
        driver.close()
    raise AssertionError("expected a DriverError")


def test_malformed_json_fails_closed():
    code, _ = _run_until_error([*HANDSHAKE, _turn_reply(), {"raw": b"{not json\n"}])
    assert code == CODE_MALFORMED_JSON


def test_non_object_frame_is_malformed():
    code, _ = _run_until_error([*HANDSHAKE, _turn_reply(), {"raw": b"[1, 2]\n"}])
    assert code == CODE_MALFORMED_JSON


def test_invalid_utf8_is_malformed():
    code, _ = _run_until_error([*HANDSHAKE, _turn_reply(), {"raw": b'{"a": "\xff"}\n'}])
    assert code == CODE_MALFORMED_JSON


def test_oversize_line_fails_closed():
    code, _ = _run_until_error(
        [*HANDSHAKE, _turn_reply(), {"raw": b'{"x": "' + b"a" * 5000 + b'"}\n'}],
        max_line_bytes=1024,
    )
    assert code == CODE_OVERSIZE_LINE


def test_unknown_notification_fails_closed():
    code, _ = _run_until_error(
        [*HANDSHAKE, _turn_reply(), {"send": {"method": "codex/surprise", "params": {}}}]
    )
    assert code == CODE_UNKNOWN_METHOD


def test_unknown_server_request_fails_closed():
    code, _ = _run_until_error(
        [*HANDSHAKE, _turn_reply(), {"send": {"id": 7, "method": "exec/anything", "params": {}}}]
    )
    assert code == CODE_UNKNOWN_METHOD


def test_schema_violation_fails_closed():
    bad = {
        "id": 7,
        "method": "item/commandExecution/requestApproval",
        "params": {"threadId": "thr_1"},  # turnId/itemId missing
    }
    code, _ = _run_until_error([*HANDSHAKE, _turn_reply(), {"send": bad}])
    assert code == CODE_PROTOCOL_ERROR


def test_early_exit_fails_closed():
    code, _ = _run_until_error([*HANDSHAKE, _turn_reply(), {"exit": True}])
    assert code == CODE_EARLY_EXIT


def test_early_exit_during_handshake_fails_closed():
    code, _ = _run_until_error([{"exit": True}])
    assert code == CODE_EARLY_EXIT


def test_deadline_fails_closed():
    code, container = _run_until_error(
        [*HANDSHAKE, _turn_reply(), {"hang": True}], deadline=time.monotonic() + 0.3
    )
    assert code == CODE_DEADLINE


def test_too_many_lines_fails_closed():
    delta = {"send": {"method": "item/agentMessage/delta", "params": {"delta": "x"}}}
    code, _ = _run_until_error([*HANDSHAKE, _turn_reply(), *([delta] * 20)], max_lines=10)
    assert code == CODE_TOO_MANY_LINES


def test_error_response_to_a_request_fails_closed():
    steps = [{"expect": "initialize", "before": [], "reply": {"error": {"code": -1}}}]
    code, _ = _run_until_error(steps)
    assert code == CODE_PROTOCOL_ERROR


def test_completion_of_another_turn_fails_closed():
    code, _ = _run_until_error([*HANDSHAKE, _turn_reply(), _completed(turn_id="turn_other")])
    assert code == CODE_PROTOCOL_ERROR


def test_usage_is_the_delta_of_running_totals_across_turns():
    def usage(turn_id, inp, out):
        return {
            "send": {
                "method": "thread/tokenUsage/updated",
                "params": {
                    "threadId": "thr_1",
                    "turnId": turn_id,
                    "tokenUsage": {"total": {"inputTokens": inp, "outputTokens": out}},
                },
            }
        }

    steps = [
        *HANDSHAKE,
        _turn_reply("t1"),
        usage("t1", 100, 10),
        _completed("t1"),
        _turn_reply("t2"),
        usage("t2", 250, 40),
        _completed("t2"),
    ]
    container = FakeContainer(steps)
    driver = _driver()
    driver.start(container)
    driver.run_turn("one")
    _drain(driver)
    driver.run_turn("two")
    _drain(driver)
    driver.close()
    first, second = driver.usage()
    assert (first.input_tokens, first.output_tokens) == (100, 10)
    assert (second.input_tokens, second.output_tokens) == (150, 30)
