"""The `friday-voice` console script (brief D16)."""

import subprocess
import sys
import tomllib

import pytest
from conftest import PACKAGE_ROOT

from friday_voice import cli


@pytest.mark.parametrize("command", ["replay", "selftest", "wake-eval"])
def test_later_commands_say_where_they_land(command, capsys):
    assert cli.main([command]) == 2
    assert "lands in feat/p11-" in capsys.readouterr().err


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
