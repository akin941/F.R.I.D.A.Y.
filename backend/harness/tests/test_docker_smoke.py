"""Real-container smoke test (Phase 3 branch 8). NOT part of the default
suite: it needs Docker and a built smoke image, so it runs only when
`HARNESS_DOCKER_SMOKE_IMAGE` names one (`make harness-smoke` builds it from
the real harness image with the CLI replaced by a stub -- no paid model
call). It exercises what the mocked suite can't: the real `docker run`
flags (cap-drop, no-new-privileges, user mapping, network, resource
limits), the bind mount, the shared write-mode wrapper inside the image,
quiescence of a stray background process, the result file and the
wrapper's base64 output frame."""

import os
import shutil
import subprocess
import time
import uuid

import pytest

from harness import runner
from harness.execution import _parse_claude_code_output
from harness.implementation_execution import extract_framed_cli_output

IMAGE = os.environ.get("HARNESS_DOCKER_SMOKE_IMAGE", "")

pytestmark = pytest.mark.skipif(
    not IMAGE or shutil.which("docker") is None or shutil.which("git") is None,
    reason="set HARNESS_DOCKER_SMOKE_IMAGE (make harness-smoke) to run the real-container smoke",
)

_GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_AUTHOR_NAME": "smoke",
    "GIT_AUTHOR_EMAIL": "smoke@example.invalid",
    "GIT_COMMITTER_NAME": "smoke",
    "GIT_COMMITTER_EMAIL": "smoke@example.invalid",
}


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=_GIT_ENV
    ).stdout.strip()


@pytest.fixture
def smoke_network(settings):
    name = f"friday-harness-smoke-{uuid.uuid4().hex[:8]}"
    subprocess.run(["docker", "network", "create", "--internal", name], check=True)
    settings.HARNESS_DOCKER_NETWORK = name
    yield name
    subprocess.run(["docker", "network", "rm", name], capture_output=True)


def test_write_mode_round_trip_in_a_real_container(tmp_path, smoke_network):
    workspace = tmp_path / "workspace"
    repo = workspace / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("smoke\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "harness/p3-smoke")
    base = _git(repo, "rev-parse", "HEAD")

    name = f"harness-smoke-{uuid.uuid4().hex[:8]}"
    runner.launch_implementation_container(
        image=IMAGE,
        workspace_dir=str(workspace),
        goal="smoke goal",
        test_command="grep -q 'smoke change' SMOKE.md",
        base_sha=base,
        cli_env={},
        name=name,
    )
    try:
        deadline = time.monotonic() + 180
        while runner.container_is_running(name):
            assert time.monotonic() < deadline, "smoke container did not finish (quiescence?)"
            time.sleep(1)
        logs = runner.capture_logs(name)
        assert runner.container_exit_code(name) == 0, logs
    finally:
        runner.remove_container(name)

    result = runner.read_implementation_result(str(workspace))
    assert result["tests"]["passed"] is True, result
    assert result["no_changes"] is False
    assert result["changed_files"] == ["SMOKE.md"]
    assert result["commit_sha"] == _git(repo, "rev-parse", "HEAD")
    assert _git(repo, "rev-parse", "HEAD^") == base

    cli_output = extract_framed_cli_output(logs)
    assert cli_output is not None and "smoke ok" in cli_output
    assert _parse_claude_code_output(cli_output)[3] == {"usage_source": "cli_reported"}
