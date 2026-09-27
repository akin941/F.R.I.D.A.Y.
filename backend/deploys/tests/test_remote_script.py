"""The real forced-command script (`deploy/remote/jarvis-deploy.sh`) under
pytest -- the `harness/tests/test_write_mode_wrapper.py` pattern: a throwaway
origin + app checkout under `tmp_path`, real `git`, a stub `docker` first on
the script's PATH recording every call, and the script's constants rewritten
to point at the temp tree (config dir, state dir, root-ownership check off
because the test does not run as root)."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "deploy" / "remote" / "jarvis-deploy.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("bash") is None,
    reason="remote-script tests need git and bash",
)

_GIT_ENV = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}

_STUB_DOCKER = """#!/bin/sh
printf '%s\\n' "$*" >> "$STUB_DIR/docker.log"
case "$*" in
  *build*) [ -f "$STUB_DIR/fail-build" ] && exit 1 ;;
  *up*) [ -f "$STUB_DIR/fail-up" ] && exit 1 ;;
esac
exit 0
"""


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **_GIT_ENV},
    ).stdout.strip()


def _commit(repo, content, message):
    (repo / "health.txt").write_text(content)
    (repo / "version.txt").write_text(f"{message}\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


class Env:
    def __init__(self, tmp_path):
        self.root = tmp_path
        self.stub_dir = tmp_path / "stubs"
        self.stub_dir.mkdir()
        docker = self.stub_dir / "docker"
        docker.write_text(_STUB_DOCKER)
        docker.chmod(0o755)
        self.config_dir = tmp_path / "etc" / "apps"
        self.config_dir.mkdir(parents=True)
        self.config_dir.chmod(0o755)
        self.state_dir = tmp_path / "state"
        self.state_dir.mkdir()

        work = tmp_path / "work"
        work.mkdir()
        _git(work, "init", "-q", "-b", "main")
        self.sha_a = _commit(work, "ok\n", "A")
        self.sha_b = _commit(work, "ok\n", "B")
        _git(work, "checkout", "-q", "-b", "side")
        self.sha_side = _commit(work, "ok side\n", "side")
        _git(work, "checkout", "-q", "main")
        self.origin = tmp_path / "origin.git"
        subprocess.run(
            ["git", "clone", "-q", "--bare", str(work), str(self.origin)],
            check=True,
            env={**os.environ, **_GIT_ENV},
        )
        self.app_dir = tmp_path / "app"
        subprocess.run(
            ["git", "clone", "-q", str(self.origin), str(self.app_dir)],
            check=True,
            env={**os.environ, **_GIT_ENV},
        )
        _git(self.app_dir, "checkout", "-q", "--detach", self.sha_a)
        self.write_config()

        script = SCRIPT.read_text()
        script = script.replace(
            "readonly CONFIG_DIR=/etc/jarvis-deploy/apps", f"readonly CONFIG_DIR={self.config_dir}"
        )
        script = script.replace(
            "readonly STATE_DIR=/var/lib/jarvis-deploy", f"readonly STATE_DIR={self.state_dir}"
        )
        script = script.replace(
            "readonly REQUIRE_ROOT_OWNED_CONFIG=1", "readonly REQUIRE_ROOT_OWNED_CONFIG=0"
        )
        script = script.replace(
            "readonly SAFE_PATH=/usr/local/sbin:",
            f"readonly SAFE_PATH={self.stub_dir}:{os.path.dirname(shutil.which('git'))}:"
            "/usr/local/sbin:",
        )
        assert script.count(str(self.config_dir)) == 1
        self.script = tmp_path / "jarvis-deploy.sh"
        self.script.write_text(script)
        self.script.chmod(0o755)

    def write_config(self, **overrides):
        values = {
            "APP_DIR": str(self.app_dir),
            "TRACKED_BRANCH": "main",
            "BUILD_CMD": "docker compose build",
            "RESTART_CMD": "docker compose up -d",
            "HEALTH_CMD": "grep -qx ok health.txt",
            **overrides,
        }
        conf = self.config_dir / "adia-web.conf"
        conf.write_text("".join(f'{key}="{value}"\n' for key, value in values.items()))
        conf.chmod(0o644)
        return conf

    def run(self, command, **env):
        full_env = {**os.environ, **_GIT_ENV, "STUB_DIR": str(self.stub_dir), **env}
        if command is None:
            full_env.pop("SSH_ORIGINAL_COMMAND", None)
        else:
            full_env["SSH_ORIGINAL_COMMAND"] = command
        return subprocess.run(
            ["bash", str(self.script)], capture_output=True, text=True, env=full_env, timeout=60
        )

    def head(self):
        return _git(self.app_dir, "rev-parse", "HEAD")

    def docker_calls(self):
        log = self.stub_dir / "docker.log"
        return log.read_text().splitlines() if log.exists() else []

    def previous(self):
        path = self.state_dir / "adia-web.previous"
        return path.read_text().strip() if path.exists() else None


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


@pytest.fixture(scope="module")
def shared_env(tmp_path_factory):
    """For the tests that change nothing (refusals, `status`)."""
    return Env(tmp_path_factory.mktemp("shared"))


# --- the command grammar ---


@pytest.mark.parametrize(
    "command",
    [
        None,
        "",
        "shell adia-web",
        "bash",
        "status adia-web; id",
        "status adia-web && id",
        "status $(id)",
        "status adia-web\nid",
        "status Adia-web",
        "status -adia",
        "status adia-web extra",
        "health adia-web " + "a" * 40,
        "deploy adia-web",
        "deploy adia-web abc123",
        "deploy adia-web " + "A" * 40,
        "rollback adia-web " + "a" * 39,
        "deploy ../etc " + "a" * 40,
        "status " + "a" * 200,
    ],
)
def test_anything_but_the_four_verbs_is_refused_with_usage(shared_env, command):
    proc = shared_env.run(command)
    assert proc.returncode == 64, proc.stderr
    assert proc.stdout == ""
    assert shared_env.docker_calls() == []
    assert shared_env.head() == shared_env.sha_a


def test_unknown_app_is_refused(shared_env):
    proc = shared_env.run("status other-app")
    assert proc.returncode == 65
    assert "unknown app" in proc.stderr


def test_status_reports_the_live_state(shared_env):
    env = shared_env
    proc = env.run("status adia-web")
    assert proc.returncode == 0, proc.stderr
    lines = dict(line.split("=", 1) for line in proc.stdout.splitlines())
    assert lines == {
        "app": "adia-web",
        "sha": env.sha_a,
        "branch": "main",
        "remote": str(env.origin),
        "previous": "",
    }


def test_health_runs_the_configured_command(env):
    assert env.run("health adia-web").returncode == 0
    (env.app_dir / "health.txt").write_text("broken\n")
    assert env.run("health adia-web").returncode == 1


def test_deploy_of_a_merged_commit_records_previous_and_restarts(env):
    proc = env.run(f"deploy adia-web {env.sha_b}")
    assert proc.returncode == 0, proc.stderr
    assert env.head() == env.sha_b
    assert env.previous() == env.sha_a
    assert env.docker_calls() == ["compose build", "compose up -d"]
    assert not (env.state_dir / "adia-web.lock").exists()


def test_deploy_of_a_non_ancestor_sha_is_refused_and_touches_nothing(env):
    proc = env.run(f"deploy adia-web {env.sha_side}")
    assert proc.returncode == 65
    assert "not on origin/main" in proc.stderr
    assert env.head() == env.sha_a
    assert env.previous() is None
    assert env.docker_calls() == []


def test_deploy_of_an_unknown_sha_is_refused(env):
    proc = env.run("deploy adia-web " + "0" * 40)
    assert proc.returncode == 65
    assert env.head() == env.sha_a


def test_deploy_of_the_live_sha_is_refused(env):
    proc = env.run(f"deploy adia-web {env.sha_a}")
    assert proc.returncode == 65
    assert env.docker_calls() == []


def test_a_failed_build_restores_the_checkout_and_restarts_nothing(env):
    (env.stub_dir / "fail-build").touch()
    proc = env.run(f"deploy adia-web {env.sha_b}")
    assert proc.returncode == 66
    assert env.head() == env.sha_a
    assert env.docker_calls() == ["compose build"]


def test_a_failed_restart_is_reported_as_touched(env):
    (env.stub_dir / "fail-up").touch()
    proc = env.run(f"deploy adia-web {env.sha_b}")
    assert proc.returncode == 1
    assert env.head() == env.sha_b


def test_rollback_to_an_unrecorded_sha_is_refused(env):
    proc = env.run(f"rollback adia-web {env.sha_b}")
    assert proc.returncode == 65
    assert "not the recorded previous" in proc.stderr
    assert env.run(f"deploy adia-web {env.sha_b}").returncode == 0
    # Recorded previous is A; B (live) and the side commit are refused.
    for sha in (env.sha_side, env.sha_b, "f" * 40):
        proc = env.run(f"rollback adia-web {sha}")
        assert proc.returncode == 65, sha
    assert env.head() == env.sha_b


def test_rollback_to_the_recorded_previous_restores_it_once(env):
    assert env.run(f"deploy adia-web {env.sha_b}").returncode == 0
    proc = env.run(f"rollback adia-web {env.sha_a}")
    assert proc.returncode == 0, proc.stderr
    assert env.head() == env.sha_a
    assert env.previous() is None
    # A second rollback needs a new deploy.
    assert env.run(f"rollback adia-web {env.sha_a}").returncode == 65


def test_a_writable_config_is_refused(env):
    conf = env.write_config()
    conf.chmod(0o666)
    proc = env.run("status adia-web")
    assert proc.returncode == 65
    assert "writable" in proc.stderr


def test_a_config_missing_a_command_is_refused(env):
    env.write_config(HEALTH_CMD="")
    assert env.run("status adia-web").returncode == 65


def test_a_held_lock_makes_deploy_busy(env):
    lock = env.state_dir / "adia-web.lock"
    lock.mkdir()
    (lock / "pid").write_text(f"{os.getpid()}\n")  # a live process
    proc = env.run(f"deploy adia-web {env.sha_b}")
    assert proc.returncode == 75
    assert env.head() == env.sha_a


def test_a_stale_lock_is_reclaimed(env):
    lock = env.state_dir / "adia-web.lock"
    lock.mkdir()
    dead = subprocess.Popen(["true"])
    dead.wait()
    (lock / "pid").write_text(f"{dead.pid}\n")
    assert env.run(f"deploy adia-web {env.sha_b}").returncode == 0


def test_local_modifications_block_a_deploy(env):
    (env.app_dir / "health.txt").write_text("edited on the server\n")
    proc = env.run(f"deploy adia-web {env.sha_b}")
    assert proc.returncode == 65
    assert env.docker_calls() == []


def test_the_script_is_executable_and_parses(env):
    assert os.access(SCRIPT, os.X_OK)
    assert subprocess.run(["bash", "-n", str(SCRIPT)], check=False).returncode == 0
