"""`runner.launch_interactive_container` (Phase 10 brief D10): the same
hardening as the implementation launch plus `-i` and pipes, secrets never in
argv; and the bounded JSONL reader. `subprocess.Popen` is faked: no Docker."""

import io
import os
from unittest import mock

import pytest

from harness import runner
from harness.drivers.base import (
    CODE_EARLY_EXIT,
    CODE_MALFORMED_JSON,
    CODE_OVERSIZE_LINE,
    CODE_TOO_MANY_LINES,
    DriverError,
    JsonlReader,
)


def _launch(monkeypatch, cli_env=None):
    popen = mock.Mock()
    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    container = runner.launch_interactive_container(
        image="img:1",
        workspace_dir="/tmp/ws",
        goal="fix it",
        test_command="pytest -q",
        base_sha="base",
        cli_env=cli_env if cli_env is not None else {"CODEX_API_KEY": "sk-secret-value"},
        name="harness-impl-1",
        driver="app_server",
    )
    return container, popen


def test_launch_keeps_the_hardening_flags_and_pipes_stdio(monkeypatch, settings):
    container, popen = _launch(monkeypatch)
    argv = popen.call_args.args[0]
    kwargs = popen.call_args.kwargs
    assert argv[:3] == ["docker", "run", "--interactive"]
    assert "--detach" not in argv
    for flag, value in (
        ("--pull", "never"),
        ("--cap-drop", "ALL"),
        ("--security-opt", "no-new-privileges"),
        ("--network", settings.HARNESS_DOCKER_NETWORK),
        ("--memory", settings.HARNESS_CONTAINER_MEMORY),
        ("--cpus", settings.HARNESS_CONTAINER_CPUS),
        ("--pids-limit", str(settings.HARNESS_CONTAINER_PIDS_LIMIT)),
        ("--name", "harness-impl-1"),
        ("--user", f"{os.getuid()}:{os.getgid()}"),
        ("--mount", "type=bind,source=/tmp/ws,target=/workspace"),
    ):
        assert argv[argv.index(flag) + 1] == value, flag
    tail = argv[argv.index("img:1") :]
    assert tail == [
        "img:1",
        "--driver",
        "app_server",
        "--goal",
        "fix it",
        "--test-command",
        "pytest -q",
        "--base-commit",
        "base",
    ]
    assert kwargs["stdin"] == runner.subprocess.PIPE
    assert kwargs["stdout"] == runner.subprocess.PIPE
    assert container.name == "harness-impl-1"


def test_secrets_are_passed_by_name_only(monkeypatch):
    monkeypatch.setenv("UNRELATED_WORKER_SECRET", "should-not-leak")
    _container, popen = _launch(monkeypatch)
    argv = popen.call_args.args[0]
    env = popen.call_args.kwargs["env"]
    assert "sk-secret-value" not in " ".join(argv)
    assert argv[argv.index("--env") + 1] == "CODEX_API_KEY"
    assert env["CODEX_API_KEY"] == "sk-secret-value"
    assert "UNRELATED_WORKER_SECRET" not in env
    assert "--env-file" not in argv


def test_no_permission_bypass_flag_is_ever_in_argv(monkeypatch):
    _container, popen = _launch(monkeypatch)
    argv = " ".join(popen.call_args.args[0])
    for forbidden in ("--privileged", "dangerously", "bypass", "full-auto", "danger-full-access"):
        assert forbidden not in argv


def test_launch_failure_is_a_runner_error(monkeypatch):
    monkeypatch.setattr(runner.subprocess, "Popen", mock.Mock(side_effect=OSError("no docker")))
    with pytest.raises(runner.RunnerError):
        runner.launch_interactive_container(
            image="i",
            workspace_dir="/w",
            goal="g",
            test_command="t",
            base_sha="b",
            cli_env={},
            name="n",
            driver="app_server",
        )


def test_kill_goes_through_the_daemon_and_the_client(monkeypatch):
    kill = mock.Mock()
    monkeypatch.setattr(runner, "kill_container", kill)
    process = mock.Mock()
    process.stdin.closed = False
    container = runner.InteractiveContainer(name="harness-impl-1", process=process)
    container.kill()
    kill.assert_called_once_with("harness-impl-1")
    process.kill.assert_called_once()
    process.stdin.close.assert_called_once()


# --- JsonlReader ---------------------------------------------------------------


def _reader(data, **kwargs):
    return JsonlReader(io.BytesIO(data), **kwargs)


def test_reader_parses_lines_and_skips_blanks():
    reader = _reader(b'{"a": 1}\n\n{"b": 2}\n')
    assert reader.next_message(1) == {"a": 1}
    assert reader.next_message(1) == {"b": 2}
    with pytest.raises(DriverError) as exc:
        reader.next_message(1)
    assert exc.value.code == CODE_EARLY_EXIT


def test_reader_eof_is_sticky():
    reader = _reader(b"")
    for _ in range(2):
        with pytest.raises(DriverError):
            reader.next_message(1)


def test_reader_line_at_the_bound_passes_and_over_it_fails():
    line = b'{"k": "' + b"x" * 10 + b'"}'
    assert _reader(line + b"\n", max_line_bytes=len(line)).next_message(1) == {"k": "x" * 10}
    with pytest.raises(DriverError) as exc:
        _reader(line + b"\n", max_line_bytes=len(line) - 1).next_message(1)
    assert exc.value.code == CODE_OVERSIZE_LINE


def test_reader_line_cap():
    reader = _reader(b"{}\n" * 5, max_lines=3)
    for _ in range(3):
        reader.next_message(1)
    with pytest.raises(DriverError) as exc:
        reader.next_message(1)
    assert exc.value.code == CODE_TOO_MANY_LINES


def test_reader_malformed_json():
    with pytest.raises(DriverError) as exc:
        _reader(b"nope\n").next_message(1)
    assert exc.value.code == CODE_MALFORMED_JSON


def test_reader_times_out_without_data():
    read_fd, write_fd = os.pipe()
    try:
        reader = JsonlReader(os.fdopen(read_fd, "rb"))
        assert reader.next_message(0.05) is None
    finally:
        os.close(write_fd)


def test_reader_discards_everything_after_the_protocol_phase():
    read_fd, write_fd = os.pipe()
    reader = JsonlReader(os.fdopen(read_fd, "rb"), max_line_bytes=8)
    reader.discard_rest()
    writer = os.fdopen(write_fd, "wb", buffering=0)
    # Far more than a pipe buffer, including over-long lines: never blocks.
    writer.write((b"x" * 100 + b"\n") * 2000)
    writer.close()
    reader._thread.join(5)
    assert not reader._thread.is_alive()
