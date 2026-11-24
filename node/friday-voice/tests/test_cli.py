"""The `friday-voice` console script (brief D16)."""

import subprocess
import sys
import tomllib

import pytest
from conftest import PACKAGE_ROOT

from friday_voice import cli


def test_selftest_and_wake_eval_are_real_commands(capsys):
    # Branch 6 landed them (tests/test_selftest.py, tests/test_wake_eval.py).
    assert cli.main(["selftest"]) == 2  # neither --ci nor --nightly
    assert "lands in" not in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["wake-eval"])  # --model is required


def test_run_takes_no_configuration():
    with pytest.raises(SystemExit):
        cli.main(["run", "--token", "jpat_never_in_argv"])


def test_the_console_script_is_declared():
    project = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text("utf-8"))
    assert project["project"]["scripts"] == {"friday-voice": "friday_voice.cli:main"}


def test_run_exits_cleanly_when_the_host_closes_stdin_at_once():
    done = subprocess.run(
        [sys.executable, "-m", "friday_voice", "run"], input=b"", capture_output=True, timeout=30
    )
    assert done.returncode == 0
    assert done.stdout == b""  # nothing before hello + credential


def test_replay_server_needs_a_token_file(capsys):
    assert cli.main(["replay", "--server", "http://127.0.0.1:9"]) == 2
    assert "--token-file" in capsys.readouterr().err


def test_replay_has_no_token_option():
    with pytest.raises(SystemExit):
        cli.main(["replay", "--token", "jpat_never_in_argv"])
