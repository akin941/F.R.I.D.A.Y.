"""Phase 13b branch 2 (brief D1/D2): the interim Mac's backup LaunchAgent
`deploy/home/macos/com.friday.backup.plist`, parsed and validated with
`plistlib` -- never loaded (no macOS, no launchd here). It is the macOS
stand-in for `friday-backup.timer`: 03:10 daily (a run missed while the
Mac slept happens once at the next wake), Homebrew bash (>= 4.4) running
the repository's script, no KeepAlive, logs under ~/Library/Logs, and an
environment of FRIDAY_* paths only -- the bucket credentials stay in the
restic env file the script sources."""

import plistlib
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
PLIST = REPO / "deploy" / "home" / "macos" / "com.friday.backup.plist"
BACKUP_SCRIPT_VARS = re.compile(r"\$\{(FRIDAY_[A-Z_]+):-")

if not PLIST.parent.parent.is_dir():  # a backend-only image, not a checkout
    pytest.skip("no deploy/home checkout around the backend", allow_module_level=True)


@pytest.fixture(scope="module")
def agent():
    with open(PLIST, "rb") as handle:
        return plistlib.load(handle)


def test_it_parses_as_a_plist_dict(agent):
    assert isinstance(agent, dict)


def test_label(agent):
    assert agent["Label"] == "com.friday.backup"
    assert PLIST.name == f"{agent['Label']}.plist"


def test_it_runs_the_repository_script_with_homebrew_bash(agent):
    program = agent["ProgramArguments"]
    assert program[0] == "/opt/homebrew/bin/bash"  # macOS's /bin/bash is 3.2
    assert len(program) == 2
    assert program[1].endswith("/deploy/home/friday-backup.sh")
    assert "Program" not in agent


def test_it_runs_once_a_day_at_03_10(agent):
    assert agent["StartCalendarInterval"] == {"Hour": 3, "Minute": 10}


def test_it_is_not_kept_alive_or_run_at_load(agent):
    assert "KeepAlive" not in agent
    assert agent.get("RunAtLoad", False) is False
    assert "StartInterval" not in agent
    assert "WatchPaths" not in agent


def test_it_logs_under_library_logs(agent):
    for key in ("StandardOutPath", "StandardErrorPath"):
        assert agent[key].endswith("/Library/Logs/friday-backup.log"), key


def test_the_environment_holds_only_friday_paths(agent):
    environment = agent["EnvironmentVariables"]
    assert environment, "the script needs its FRIDAY_* locations"
    for name, value in environment.items():
        assert name.startswith("FRIDAY_"), name
        assert isinstance(value, str)
        assert value.startswith("/"), name
        for forbidden in ("AWS_", "RESTIC_PASSWORD", "RESTIC_REPOSITORY", "SECRET", "KEY="):
            assert forbidden not in name
            assert forbidden not in value
    assert not any(n.startswith(("AWS_", "RESTIC_")) for n in environment)


def test_the_environment_names_variables_the_script_reads(agent):
    script = (REPO / "deploy" / "home" / "friday-backup.sh").read_text(encoding="utf-8")
    known = set(BACKUP_SCRIPT_VARS.findall(script)) | {"FRIDAY_PATH"}
    assert "FRIDAY_PATH" in script
    environment = agent["EnvironmentVariables"]
    assert set(environment) <= known, set(environment) - known
    # The locations the Mac needs (runbook I5); the rest keep their defaults.
    assert {
        "FRIDAY_REPO_DIR",
        "FRIDAY_BACKUP_DIR",
        "FRIDAY_AGE_RECIPIENTS",
        "FRIDAY_RESTIC_ENV",
        "FRIDAY_ENV_FILE",
        "FRIDAY_PATH",
    } <= set(environment)
    assert environment["FRIDAY_RESTIC_ENV"].endswith("/.config/friday-backup/restic.env")
    # Homebrew first, so age/restic/bash resolve (launchd's PATH lacks them).
    assert environment["FRIDAY_PATH"].split(":")[0] == "/opt/homebrew/bin"


def test_every_path_is_the_one_placeholder_home(agent):
    paths = [
        *agent["ProgramArguments"][1:],
        agent["StandardOutPath"],
        agent["StandardErrorPath"],
        *agent["EnvironmentVariables"].values(),
    ]
    for value in paths:
        for part in value.split(":"):
            if part.startswith("/Users/"):
                assert part.startswith("/Users/USERNAME/"), part


def test_the_runbook_explains_installing_it():
    runbook = (REPO / "docs" / "runbooks" / "home-server-setup.md").read_text(encoding="utf-8")
    assert "deploy/home/macos/com.friday.backup.plist" in runbook
    assert "launchctl bootstrap" in runbook
