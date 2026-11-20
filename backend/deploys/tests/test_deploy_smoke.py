"""Real-infrastructure deploy smoke (manual/nightly: `make deploy-smoke`,
which starts the container and sets the env below; skipped otherwise).

Drives the whole path through the real system `ssh` client against an sshd
container running the real `deploy/remote/friday-deploy.sh` as a forced
command, with a dummy compose app: status refresh -> deploy of a merged
commit -> deploy of a commit whose health fails, automatically rolled back
-> a non-merged SHA refused by the remote. Every decision goes through the
approvals API over the web channel, exactly as in production."""

import os
import urllib.request

import pytest

from approvals.models import ApprovalRequest
from core.channels import CHANNEL_WEB
from deploys import execution
from deploys.models import DeployRun, DeployTarget
from deploys.services import request_deploy

_ENV = (
    "DEPLOY_SMOKE_SSH_PORT",
    "DEPLOY_SMOKE_HTTP_PORT",
    "DEPLOY_SMOKE_HOST_KEY",
    "DEPLOY_SMOKE_KEY_PATH",
    "SMOKE_SHA_A",
    "SMOKE_SHA_B",
    "SMOKE_SHA_C",
    "SMOKE_SHA_D",
)

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        not all(os.environ.get(name) for name in _ENV),
        reason="deploy smoke runs only via `make deploy-smoke` (needs Docker)",
    ),
]

S = DeployRun.Status
E = DeployRun.ErrorCode


# Override the package's autouse fakes: this module talks to a real sshd,
# a real HTTP server and a real clock.
@pytest.fixture(autouse=True)
def _no_real_remote():
    return None


@pytest.fixture(autouse=True)
def fake_clock():
    return None


@pytest.fixture(autouse=True)
def _deploy_settings(settings):
    settings.DEPLOY_SSH_KEY_PATH = os.environ["DEPLOY_SMOKE_KEY_PATH"]
    settings.HARNESS_SELF_REPO_IDENTIFIERS = ["git@example.invalid:friday/self.git"]
    settings.DEPLOY_HEALTH_CHECKS_PER_WINDOW = 4
    settings.DEPLOY_SSH_DEPLOY_TIMEOUT_SECONDS = 120


def _live_version():
    port = os.environ["DEPLOY_SMOKE_HTTP_PORT"]
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/version", timeout=5) as response:
        return response.read().decode().strip()


def _deploy(owner, workspace, web_client, target, sha, django_capture_on_commit_callbacks):
    request = request_deploy(
        workspace=workspace, actor=owner, target=target, sha=sha, channel=CHANNEL_WEB
    )
    with django_capture_on_commit_callbacks(execute=True):
        response = web_client(owner).post(f"/api/v1/approvals/requests/{request.id}/approve")
    assert response.status_code == 200, response.content
    assert ApprovalRequest.objects.get(pk=request.pk).status == "approved"
    return DeployRun.objects.get(approval_request=request)


def test_deploy_rollback_and_refusal_against_a_real_sshd(
    owner, workspace, web_client, django_capture_on_commit_callbacks
):
    sha_a, sha_b, sha_c, sha_d = (os.environ[f"SMOKE_SHA_{x}"] for x in "ABCD")
    http_port = os.environ["DEPLOY_SMOKE_HTTP_PORT"]
    target = DeployTarget.objects.create(
        workspace=workspace,
        created_by=owner,
        name="Smoke",
        environment="staging",
        ssh_host="127.0.0.1",
        ssh_port=int(os.environ["DEPLOY_SMOKE_SSH_PORT"]),
        ssh_user="deploy",
        host_public_key=os.environ["DEPLOY_SMOKE_HOST_KEY"],
        remote_app="smoke-app",
        repo_git_url="git@example.invalid:acme/smoke-app.git",
        tracked_branch="main",
        health_url=f"http://127.0.0.1:{http_port}/health",
        health_timeout_seconds=30,
        require_ci_success=False,
        auto_rollback=True,
        enabled=True,
    )
    assert _live_version() == "A"

    # 1. First status: Friday learns the live SHA over the real ssh client.
    assert execution.refresh_target_status(str(target.id)) is None
    target.refresh_from_db()
    assert target.current_sha == sha_a

    # 2. A merged, healthy commit deploys.
    run = _deploy(owner, workspace, web_client, target, sha_b, django_capture_on_commit_callbacks)
    assert (run.status, run.error_code) == (S.SUCCEEDED, ""), run.output
    assert _live_version() == "B"
    target.refresh_from_db()
    assert target.current_sha == sha_b

    # 3. A merged commit whose health fails: three consecutive failures,
    #    automatic rollback to the decided SHA, health passes again.
    run = _deploy(owner, workspace, web_client, target, sha_c, django_capture_on_commit_callbacks)
    assert run.status == S.ROLLED_BACK, run.output
    assert run.error_code == E.HEALTH_CHECKS_FAILED
    phases = [(c["phase"], c["verb_ok"]) for c in run.health_checks]
    assert phases[:3] == [("deploy", False)] * 3
    assert phases[3:] == [("rollback", True)] * 2
    assert _live_version() == "B"
    target.refresh_from_db()
    assert target.current_sha == sha_b

    # 4. A commit that is not on the tracked branch is refused by the
    #    remote script, and nothing changes.
    run = _deploy(owner, workspace, web_client, target, sha_d, django_capture_on_commit_callbacks)
    assert (run.status, run.error_code) == (S.FAILED, E.DEPLOY_REFUSED), run.output
    assert "not on origin/main" in run.output
    assert _live_version() == "B"
