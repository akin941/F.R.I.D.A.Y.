"""`ReferenceNode` v2: checks 22-24, the executor port, fail closed
(Phase 10 brief D5; ADR-013 decision 6)."""

import json
import os
import shutil
import sys
import time

import pytest

from nodes.conformance import cases as C
from nodes.conformance.runner import InProcessDriver, build_reference_node, play
from nodes.protocol.messages import validate
from nodes.reference.executor import FakeCommandScript, SrtExecutor
from nodes.reference.fixtures.factory import build_fixture_home
from nodes.reference.worktree import list_worktrees


@pytest.fixture(scope="module")
def home(tmp_path_factory):
    return build_fixture_home(tmp_path_factory.mktemp("node-home"))


def _node(home, **setup):
    return build_reference_node({"setup": {"now": C.NOW, **setup}}, home=home)


def _send(node, frame):
    return node.receive(json.dumps(frame)) + node.tick()


ALL_CASES = [case for cases in C.all_cases().values() for case in cases]


def _only_refusals(case):
    results = [m for m in case["expect"]["frames"] if m.get("type") == "result"]
    return results and all(m.get("status") in ("refused", "error") for m in results)


@pytest.mark.parametrize(
    "case", [c for c in ALL_CASES if _only_refusals(c)], ids=lambda c: c["name"]
)
def test_the_executor_is_never_called_on_a_refusal_path(case, home):
    node = build_reference_node(case, home=home)
    play(case, InProcessDriver(node, case["version"]))
    assert node.executor.calls == []


def test_every_frame_the_node_sends_is_schema_valid(home):
    for case in ALL_CASES:
        node = build_reference_node(case, home=home)
        play(case, InProcessDriver(node, case["version"]))
        for frame in node.sent:
            validate(frame, "node_to_server", case["version"])


def test_a_v1_session_advertises_reads_only(home):
    node = _node(home)
    hello = node.connect(1)[0]
    assert {entry["class"] for entry in hello["catalogue"]} == {"read"}
    node2 = _node(home)
    classes = {entry["class"] for entry in node2.connect(2)[0]["catalogue"]}
    assert classes == {"read", "confirm", "gate"}


def test_disconnect_closes_the_window_and_nothing_runs_later(home):
    node = _node(home, window=[{"action": "approve", "after": 5}])
    node.connect(2)
    frames = _send(node, C.gated("node-drop"))
    assert [f["state"] for f in frames] == ["shown"]
    sent = len(node.sent)
    node.disconnect()
    assert node.window.closed_by == ["disconnect"]
    node.clock.advance(10)
    assert node.tick() == [] and node.receive(json.dumps(C.gated("node-drop-2"))) == []
    assert len(node.sent) == sent
    assert node.executor.calls == []


def test_disconnect_while_running_terminates_and_sends_no_late_result(home):
    node = _node(
        home,
        window=[{"action": "approve", "after": 0}],
        executor=[{"chunks": [["stdout", "a\n", 0], ["stdout", "b\n", 3]], "duration": 5}],
    )
    node.connect(2)
    _send(node, C.gated("node-drop-run"))
    assert node.busy
    node.disconnect()
    assert node.executor.calls[0]["terminated"] is True
    assert node.executor.calls[0]["signals"] == ["SIGTERM", "SIGKILL"]
    node.clock.advance(10)
    assert node.tick() == []
    assert not any(f["type"] == "result" for f in node.sent)
    assert not node.busy


def test_the_kill_switch_stops_a_running_command(home):
    node = _node(
        home, window=[{"action": "approve", "after": 0}], executor=[{"chunks": [], "hang": True}]
    )
    node.connect(2)
    _send(node, C.gated("node-kill-run"))
    frames = node.engage_kill_switch()
    assert frames[-1]["type"] == "result" and frames[-1]["status"] == "error"
    assert node.executor.calls[0]["terminated"]
    # After the switch even an approved window cannot run anything.
    node2 = _node(home, window=[{"action": "approve", "after": 1}])
    node2.connect(2)
    _send(node2, C.gated("node-kill-after"))
    node2.kill_switch = True
    node2.clock.advance(1)
    last = node2.tick()[-1]
    assert last["refusal_code"] == "expired_after_confirm"
    assert node2.executor.calls == []


def test_output_over_one_mib_is_truncated_in_the_result(home):
    big = "y" * 65535 + "\n"
    node = _node(
        home,
        window=[{"action": "approve", "after": 0}],
        executor=[{"chunks": [["stdout", big, 0]] * 32, "exit_code": 0}],
    )
    node.connect(2)
    frames = _send(node, C.gated("node-big"))
    result = frames[-1]
    assert result["type"] == "result" and result["truncated"] is True
    assert result["output_bytes"] == 32 * 65536
    emitted = sum(len(f["data"].encode()) for f in frames if f["type"] == "output")
    assert emitted <= 1024 * 1024
    assert len(result["tail"].encode()) <= 4096


def test_actions_go_to_the_executor_with_the_resolved_intent(home):
    node = _node(home, window=[{"action": "approve", "after": 0}] * 2)
    node.connect(2)
    _send(node, C.plain("node-open", "apps.open", C.TERMINAL, cls="confirm"))
    _send(
        node,
        C.gated(
            "node-recipe",
            "applescript.recipe.run",
            {"recipe": "system.volume_set", "params": {"level": 20}},
        ),
    )
    calls = node.executor.calls
    assert calls[0]["kind"] == "action" and calls[0]["bundle_id"] == "com.apple.Terminal"
    assert calls[1]["recipe"] == "system.volume_set"


def test_the_executor_gets_the_generated_sandbox_never_server_text(home):
    node = _node(home, window=[{"action": "approve", "after": 0}])
    node.connect(2)
    _send(node, C.gated("node-sandbox"))
    call = node.executor.calls[0]
    assert call["argv"] == ["python3", "-c", "print('acme tests ok')"]
    assert call["cwd"] == str(home / "work" / "acme")
    assert "GITHUB_TOKEN" not in call["env"]
    assert call["settings"]["network"]["allowedDomains"] == []
    assert call["limits"]["wall_seconds"] == 300


def test_worktree_mode_runs_in_a_throwaway_worktree_and_removes_it(home, tmp_path):
    node = build_reference_node(
        {"setup": {"now": C.NOW, "window": [{"action": "approve", "after": 0}]}},
        home=home,
        worktree_base=tmp_path,
    )
    node.connect(2)
    repo = str(home / "work" / "acme")
    before = list_worktrees(repo)
    frames = _send(node, C.gated("node-wt", args=C.ACME_LINT))
    call = node.executor.calls[0]
    assert call["mode"] == "worktree"
    assert call["cwd"] != repo and call["cwd"].startswith(str(tmp_path))
    assert frames[-1]["status"] == "ok"
    assert not os.path.exists(call["cwd"])
    assert list_worktrees(repo) == before


def test_heartbeats_every_15_seconds(home):
    node = _node(home)
    node.connect(2)
    beats = []
    for _ in range(6):
        node.clock.advance(5)
        beats += [f for f in node.tick() if f["type"] == "heartbeat"]
        node.receive(
            json.dumps(
                {"v": 2, "type": "heartbeat.ack", "id": C._uuid4("hb"), "seq": 1, "server_time": 0}
            )
        )
    assert [b["seq"] for b in beats] == [1, 2]


def test_a_start_failure_is_an_error_result_and_cleans_up(home):
    node = _node(home, window=[{"action": "approve", "after": 0}])
    node.executor.scripts = [FakeCommandScript(fail_to_start=True)]
    node.connect(2)
    frames = _send(node, C.gated("node-fail"))
    assert frames[-1]["status"] == "error" and frames[-1]["confirm_state"] == "approved"
    assert not node.busy


# --- the process plumbing of SrtExecutor, without srt ------------------
# `argv_template=()` runs the argv directly: an UNSANDBOXED test double for
# the process handling (streams, exit codes, SIGTERM/SIGKILL). Test-only.


def _unsandboxed_node(home, script, *, after=0):
    node = _node(home, window=[{"action": "approve", "after": after}])
    node.executor = SrtExecutor(srt_path=sys.executable, argv_template=())
    repo = node.policy.repos[0]
    command = repo.commands["test"]
    object.__setattr__(command, "argv", (sys.executable, "-c", script))
    return node


@pytest.fixture
def fresh_home(tmp_path):
    return build_fixture_home(tmp_path)


def _wait_result(node, timeout=20):
    frames = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frames += node.tick()
        if any(f["type"] == "result" for f in frames):
            return frames
        time.sleep(0.02)
    raise AssertionError("no result")


def test_a_real_process_streams_output_and_exit_code(fresh_home):
    node = _unsandboxed_node(
        fresh_home, "import sys; print('ok out'); print('warn', file=sys.stderr); sys.exit(3)"
    )
    node.connect(2)
    frames = _send(node, C.gated("node-real")) + _wait_result(node)
    result = frames[-1]
    assert result["status"] == "ok" and result["exit_code"] == 3
    outputs = {(f["stream"], f["data"]) for f in frames if f["type"] == "output"}
    assert ("stdout", "ok out\n") in outputs and ("stderr", "warn\n") in outputs


def test_disconnect_kills_a_real_process_that_ignores_sigterm(fresh_home, tmp_path):
    pid_file = tmp_path / "pid"
    script = (
        "import os, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(60)"
    )
    node = _unsandboxed_node(fresh_home, script)
    node.connect(2)
    _send(node, C.gated("node-kill"))
    deadline = time.monotonic() + 10
    while not pid_file.exists() or not pid_file.read_text():
        assert time.monotonic() < deadline
        time.sleep(0.05)
    pid = int(pid_file.read_text())
    started = time.monotonic()
    node.disconnect()
    assert time.monotonic() - started < 8  # SIGTERM, 5 s grace, SIGKILL
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert not any(f["type"] == "result" for f in node.sent)


@pytest.mark.skipif(shutil.which("srt") is None, reason="srt is not installed")
@pytest.mark.srt
def test_a_real_srt_run(fresh_home):  # pragma: no cover - opt-in
    node = _node(fresh_home, window=[{"action": "approve", "after": 0}])
    node.executor = SrtExecutor()
    node.connect(2)
    frames = _send(node, C.gated("node-srt")) + _wait_result(node, timeout=60)
    assert frames[-1]["exit_code"] == 0
