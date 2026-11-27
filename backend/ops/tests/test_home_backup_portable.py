"""Phase 13b branch 2 (brief D1/D2): the backup and drill scripts run
unchanged with the macOS tool set -- here a PATH shim (no Mac in CI).

The shim is a directory of symlinks to the real tools the scripts use,
WITHOUT `flock`, `sha256sum`, `realpath` and GNU `stat` (a BSD-like `stat`
double refuses `-c`, as macOS's does), plus a `shasum` double. Under it:
the backup -> drill round trip writes the same `status.json` (with
`repository`); the lock directory stands in for `flock` (a second run while
it is held by a live process is refused, a stale one is recovered); a bash
older than 4.4 is refused with a clear message; a `RESTIC_REPOSITORY` with
userinfo or a query fails the run before anything is dumped, and no status
file is written. Then a macOS-declared preflight over the files the shimmed
run wrote has no failing `home_*` row.

Real tools (age, restic, pg_dump, ...) against this test run's Postgres and
a local restic repository, as `test_home_backup_restore.py` -- whose skip
rules apply (the module is skipped where the tools are missing, except in
the `home-ops` CI job)."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ops import preflight
from ops.preflight import FAIL, PASS, WARN
from ops.tests.test_home_backup_restore import (
    BACKUP,
    DRILL,
    STATUS_KEYS,
    _run,
    _url,
    chain,  # noqa: F401 -- a fixture
    create_scratch_db,
    drop_scratch_db,
    make_backup_home,
)

pytestmark = pytest.mark.django_db(transaction=True)

# What the scripts call (plus what `uv run python manage.py` needs). Not
# here, on purpose: flock, sha256sum, realpath, GNU stat.
SHIM_TOOLS = (
    "age",
    "restic",
    "pg_dump",
    "pg_restore",
    "psql",
    "uv",
    "bash",
    "sh",
    "env",
    "grep",
    "mkdir",
    "chmod",
    "date",
    "mv",
    "rm",
    "find",
    "cut",
    "tail",
    "head",
    "wc",
    "tr",
    "ps",
    "dirname",
    "basename",
    "cat",
    "sleep",
    "od",
    "seq",
    "sed",
    "ls",
    "id",
    "uname",
)
HIDDEN = ("flock", "sha256sum", "realpath")
SECRET = "hunter2-r2-secret"
SYSTEM_PATH = "/usr/local/bin:/usr/bin:/bin"


def make_mac_shim(tmp_path):
    """`(bin dir, call log)`: the macOS tool set double."""
    shim = tmp_path / "macos-bin"
    shim.mkdir()
    log = tmp_path / "macos-bin.log"
    for tool in SHIM_TOOLS:
        # The system's own tool first (a wrapper earlier on this PATH, e.g. a
        # sandbox guard, is not part of the tool set being emulated).
        real = shutil.which(tool, path=SYSTEM_PATH) or shutil.which(tool)
        assert real, f"{tool} is needed for the shim"
        (shim / tool).symlink_to(real)
    # BSD stat: no `-c` (macOS answers "illegal option -- c").
    (shim / "stat").write_text(
        "#!/bin/sh\n"
        f'echo "stat $*" >> "{log}"\n'
        'for a in "$@"; do case $a in -c*)\n'
        '  echo "stat: illegal option -- c" >&2; exit 1;;\n'
        "esac; done\n"
        "exit 1\n"
    )
    # shasum (Perl on macOS): `shasum -a 256 FILE` -> "<hex>  FILE".
    (shim / "shasum").write_text(
        f"#!{sys.executable}\n"
        "import hashlib, sys\n"
        f"open({str(log)!r}, 'a').write('shasum ' + ' '.join(sys.argv[1:]) + '\\n')\n"
        "args = sys.argv[1:]\n"
        "assert args[:2] == ['-a', '256'] and len(args) == 3, args\n"
        "h = hashlib.sha256(open(args[2], 'rb').read()).hexdigest()\n"
        "print(f'{h}  {args[2]}')\n"
    )
    for name in ("stat", "shasum"):
        (shim / name).chmod(0o755)
    for tool in HIDDEN:
        assert shutil.which(tool, path=str(shim)) is None
    return shim, log


@pytest.fixture
def mac_home(tmp_path):
    home = make_backup_home(tmp_path)
    shim, log = make_mac_shim(tmp_path)
    home["shim"], home["shim_log"] = shim, log
    home["backup_env"] = {**home["backup_env"], "PATH": str(shim)}
    return home


def _status_path(home):
    return home["dir"] / "status" / "status.json"


def _backup(home, *, env=None, bash=None, check=True):
    env = env or home["backup_env"]
    bash = bash or str(home["shim"] / "bash")
    return _run([bash, str(BACKUP)], env, check=check)


def _dead_pid():
    proc = subprocess.Popen(["true"])  # noqa: S603, S607 -- a fixed argv
    proc.wait()
    return proc.pid


def _old_bash(tmp_path):
    """A `bash` that reports 3.2 (macOS's /bin/bash): the real bash with
    BASH_VERSION overwritten before the script is sourced."""
    path = tmp_path / "bash-3.2"
    real = shutil.which("bash")
    path.write_text(
        "#!/bin/sh\n"
        'script="$1"; shift\n'
        f'exec {real} -c \'BASH_VERSION="3.2.57(1)-release"; . "$0"\' "$script" "$@"\n'
    )
    path.chmod(0o755)
    return str(path)


def test_the_shim_hides_the_gnu_only_tools(mac_home):
    env = mac_home["backup_env"]
    probe = _run(
        [str(mac_home["shim"] / "bash"), "-c", "command -v flock sha256sum realpath || true"], env
    )
    assert probe.stdout.strip() == ""
    refused = _run([str(mac_home["shim"] / "stat"), "-c", "%s", str(BACKUP)], env, check=False)
    assert refused.returncode != 0


def test_backup_then_drill_round_trip_with_the_macos_tools(mac_home, chain, settings, monkeypatch):  # noqa: F811
    home = mac_home
    _backup(home)
    status = json.loads(_status_path(home).read_text())
    assert set(status) == STATUS_KEYS
    assert status["repository"] == str(home["repo"])
    raw = (home["dir"] / status["dump"]).read_bytes()
    assert status["bytes"] == len(raw) > 0
    assert status["sha256"] == hashlib.sha256(raw).hexdigest()
    calls = home["shim_log"].read_text().splitlines()
    assert any(line.startswith("shasum -a 256 ") for line in calls), calls
    assert not any(line.startswith("stat ") for line in calls), calls
    # The lock directory was taken and released (no flock here).
    assert not (home["dir"] / ".friday-backup.lock.d").exists()
    assert not (home["dir"] / ".friday-backup.lock").exists()

    # The drill, with a RELATIVE key path and dump path (no `realpath -m`).
    scratch = create_scratch_db()
    try:
        env = {
            **home["env"],
            "PATH": str(home["shim"]),
            "FRIDAY_REPO_DIR": str(Path(BACKUP).resolve().parents[2]),
            "FRIDAY_BACKUP_DIR": str(home["dir"]),
            "FRIDAY_DRILL_MODE": "direct",
            "FRIDAY_DRILL_DATABASE_URL": _url(scratch),
            "DJANGO_SETTINGS_MODULE": "config.settings.dev",
        }
        completed = subprocess.run(  # noqa: S603 -- our own script, fixed argv
            [
                str(home["shim"] / "bash"),
                str(DRILL),
                os.path.relpath(home["key"], home["dir"]),
                status["dump"],
            ],
            env=env,
            cwd=home["dir"],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    finally:
        drop_scratch_db(scratch)
    assert completed.returncode == 0, completed.stderr
    assert "node audit chain OK" in completed.stdout
    assert "sha256 comparison skipped" not in completed.stderr  # shasum compared it
    drill = json.loads((home["dir"] / "status" / "drill.json").read_text())
    assert (drill["dump"], drill["result"]) == (status["dump"], "pass")

    # A macOS-declared preflight over what the shimmed run wrote: no fail.
    settings.HOME_SERVER_PLATFORM = "macos"
    settings.HOME_BACKUP_STATUS_PATH = str(_status_path(home))
    settings.HOME_RESTORE_DRILL_STATUS_PATH = str(home["dir"] / "status" / "drill.json")
    settings.HOME_BACKUP_OFFSITE_REPOSITORY = str(home["repo"])
    settings.HOME_FDE_CONFIRMED_ON = "2026-11-20"
    settings.ALLOWED_HOSTS = ["owner-mbp.local", "192.168.1.30", "127.0.0.1"]
    settings.PREFLIGHT_NODE_TLS_URL = ""
    monkeypatch.setattr(preflight, "resolve_host", lambda host: [])  # no DNS in tests
    rows = {row.name: row for row in preflight.check_home()}
    assert [row for row in rows.values() if row.status == FAIL] == []
    assert rows["home_backup_fresh"].status == PASS
    assert rows["home_restore_drill_age"].status == PASS
    assert rows["home_server_platform"].status == WARN
    assert rows["home_disk_encryption"].status == WARN


def test_a_held_lock_directory_refuses_a_second_run(mac_home):
    lock = mac_home["dir"] / ".friday-backup.lock.d"
    lock.mkdir()
    (lock / "pid").write_text(f"{os.getpid()}\n")  # this test process: alive
    completed = _backup(mac_home, check=False)
    assert completed.returncode != 0
    assert "another backup is running" in completed.stderr
    assert lock.is_dir()  # not the refused run's to remove
    assert not _status_path(mac_home).exists()
    assert not list(mac_home["dir"].glob("*.age"))


@pytest.mark.parametrize("holder", ["dead", "garbage", "missing"])
def test_a_stale_lock_directory_is_recovered(mac_home, holder):
    lock = mac_home["dir"] / ".friday-backup.lock.d"
    lock.mkdir()
    if holder == "dead":
        (lock / "pid").write_text(f"{_dead_pid()}\n")
    elif holder == "garbage":
        (lock / "pid").write_text("not-a-pid\n")
    completed = _backup(mac_home)
    assert "removing a stale lock" in completed.stderr
    assert _status_path(mac_home).is_file()
    assert not lock.exists()


def test_with_flock_present_the_lock_file_is_used(mac_home):
    """The Linux path is unchanged: with `flock` on PATH no lock directory
    is made (and a stray one does not block)."""
    if shutil.which("flock") is None:
        pytest.skip("no flock on this host")
    (mac_home["shim"] / "flock").symlink_to(shutil.which("flock"))
    stray = mac_home["dir"] / ".friday-backup.lock.d"
    stray.mkdir()
    (stray / "pid").write_text(f"{os.getpid()}\n")
    _backup(mac_home)
    assert _status_path(mac_home).is_file()
    assert (mac_home["dir"] / ".friday-backup.lock").exists()


@pytest.mark.parametrize("script", [BACKUP, DRILL], ids=["backup", "drill"])
def test_bash_older_than_4_4_is_refused_clearly(mac_home, tmp_path, script):
    argv = [_old_bash(tmp_path), str(script)]
    if script == DRILL:
        argv.append(str(mac_home["key"]))
    completed = _run(argv, mac_home["backup_env"], check=False)
    assert completed.returncode != 0
    assert "needs bash >= 4.4, this is bash 3.2.57" in completed.stderr
    assert "/opt/homebrew/bin/bash" in completed.stderr
    assert not (mac_home["dir"] / "status").exists()
    assert not list(mac_home["dir"].glob("*.age"))


def test_the_old_bash_double_really_runs_the_script(mac_home, tmp_path):
    """Control for the test above: the same double with a 5.x version
    string gets past the check (so the refusal is the version check)."""
    double = Path(_old_bash(tmp_path))
    double.write_text(double.read_text().replace("3.2.57(1)-release", "5.2.21(1)-release"))
    _run([str(double), str(BACKUP)], mac_home["backup_env"])
    assert _status_path(mac_home).is_file()


@pytest.mark.parametrize(
    "repository",
    [
        f"rest:https://friday:{SECRET}@backup.example.net/",
        f"s3:https://AKIA:{SECRET}@acct.r2.cloudflarestorage.com/friday-backup",
        f"s3:https://acct.r2.cloudflarestorage.com/friday-backup?secret={SECRET}",
        f'/srv/restic" , "{SECRET}',
    ],
)
@pytest.mark.parametrize("tools", ["gnu", "macos"])
def test_a_repository_that_could_carry_a_credential_fails_the_run(mac_home, repository, tools):
    env_file = mac_home["config"] / "restic.env"
    env_file.write_text(
        env_file.read_text().replace(
            f"RESTIC_REPOSITORY={mac_home['repo']}", f"RESTIC_REPOSITORY='{repository}'"
        )
    )
    assert repository in env_file.read_text()
    if tools == "gnu":
        completed = _run(
            ["bash", str(BACKUP)],
            {**mac_home["backup_env"], "PATH": os.environ["PATH"]},
            check=False,
        )
    else:
        completed = _backup(mac_home, check=False)
    assert completed.returncode != 0
    assert "refusing to record it" in completed.stderr
    assert SECRET not in completed.stderr + completed.stdout
    # Nothing was dumped, pushed or recorded.
    assert not _status_path(mac_home).exists()
    assert not list(mac_home["dir"].glob("*.age"))


def test_a_restic_env_without_a_repository_fails_the_run(mac_home):
    env_file = mac_home["config"] / "restic.env"
    env_file.write_text(
        "\n".join(
            line for line in env_file.read_text().splitlines() if "RESTIC_REPOSITORY" not in line
        )
        + "\n"
    )
    completed = _backup(mac_home, check=False)
    assert completed.returncode != 0
    assert "RESTIC_REPOSITORY is not set" in completed.stderr
    assert not _status_path(mac_home).exists()


def test_friday_path_replaces_path_for_launchd(mac_home):
    """launchd starts jobs with a bare PATH: the LaunchAgent sets
    FRIDAY_PATH, which the script puts in place of PATH."""
    env = {**mac_home["backup_env"], "PATH": "/nonexistent", "FRIDAY_PATH": str(mac_home["shim"])}
    _backup(mac_home, env=env)
    assert _status_path(mac_home).is_file()
    without = {**mac_home["backup_env"], "PATH": "/nonexistent"}
    _status_path(mac_home).unlink()
    completed = _backup(mac_home, env=without, check=False)
    assert completed.returncode != 0
    assert not _status_path(mac_home).exists()
