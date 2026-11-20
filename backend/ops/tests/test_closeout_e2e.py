"""Phase 5 closeout end to end (brief §10): request -> code -> live, through
every gate, in one test.

harness implementation job (stubbed CLI/container) -> review verdict
(stubbed) -> Gate B decided over the web -> push (stubbed git) -> "PR aç"
-> `github_open_pr` decided over the web -> PR opened on the fake
`github-mcp-server` -> "Deploy" -> `deploy` gate decided over the web ->
SSH runner against a scripted remote (`deploys.ssh.run_verb` seam) -> live
-> `work_log` rows visible to the owner through `GET /api/v1/memories`.

It lives in `ops` because it spans `harness`, `connectors` and `deploys`,
which may not import each other in that direction (ADR-010 contracts).
Nothing here reaches a real CLI, git remote, MCP server, GitHub or VPS:
the real-sshd run is `make deploy-smoke` (an owner step)."""

from unittest import mock

import pytest

from accounts.services import register_user
from approvals.models import ApprovalRequest
from connectors.models import ConnectorBinding, ConnectorWriteExecution
from connectors.services import set_binding_enabled
from connectors.tests import fakes
from core.channels import CHANNEL_WEB
from deploys.models import DeployRun, DeployTarget
from deploys.tests.conftest import HOST_KEY, FakeClock, FakeRemote
from harness import implementation_execution
from harness.models import HarnessImplementationJob, HarnessRepo, HarnessReview
from harness.services import request_harness_push
from harness.tests.test_implementation_execution import (
    _patch_happy_path_runner,
    _wrapper_result,
)
from memory.models import Memory
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"
REPO_URL = "git@github.com:acme/app.git"
LIVE_SHA = "c" * 40
NEW_SHA = fakes.HEAD_SHA  # the commit the harness produced and pushed


def _forbidden(*args, **kwargs):
    raise AssertionError("a test reached a real SSH/HTTP call")


@pytest.fixture(autouse=True)
def isolated(settings, monkeypatch, tmp_path):
    settings.HARNESS_SELF_REPO_IDENTIFIERS = ["git@github.com:owner/friday.git"]
    settings.HARNESS_GIT_SSH_KEY_PATH = str(tmp_path / "git_key")
    key = tmp_path / "deploy_key"
    key.write_text("placeholder, never read by a test\n")
    settings.DEPLOY_SSH_KEY_PATH = str(key)
    settings.DEPLOY_HEALTH_CHECKS_PER_WINDOW = 4
    monkeypatch.setattr("deploys.ssh._run_subprocess", _forbidden)
    monkeypatch.setattr("deploys.health._http_get", _forbidden)
    clock = FakeClock()
    monkeypatch.setattr("deploys.execution._sleep", clock.sleep)
    monkeypatch.setattr("deploys.execution._monotonic", clock.monotonic)


@pytest.fixture
def owner():
    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


@pytest.fixture
def gh(monkeypatch):
    state = fakes.install_github(monkeypatch, fakes.GithubServerState())
    state.workflow_runs = [fakes.workflow_run(NEW_SHA)]
    return state


@pytest.fixture
def remote(monkeypatch):
    fake = FakeRemote()
    fake.live_sha = LIVE_SHA
    fake.remote = REPO_URL
    fake.app = "app"
    monkeypatch.setattr("deploys.ssh.run_verb", fake)
    return fake


def _approve(client, request_id, committed):
    with committed(execute=True):
        response = client.post(f"/api/v1/approvals/requests/{request_id}/approve")
    assert response.status_code == 200, response.content


def test_request_to_live_with_work_log(
    owner,
    workspace,
    gh,
    remote,
    monkeypatch,
    web_client,
    django_capture_on_commit_callbacks,
):
    committed = django_capture_on_commit_callbacks
    client = web_client(owner)
    repo = HarnessRepo.objects.create(
        workspace=workspace,
        name="app",
        git_url=REPO_URL,
        default_branch="main",
        enabled=True,
        test_command="pytest -q",
    )
    with committed(execute=True):
        set_binding_enabled(
            workspace=workspace, actor=owner, connector="github", enabled=True, channel=CHANNEL_WEB
        )
    binding = ConnectorBinding.objects.get(workspace=workspace, connector="github")
    assert set(binding.last_health["tools"].values()) == {"ok"}

    # 1. Implementation (Gate A already decided): stubbed container/CLI.
    job = HarnessImplementationJob.objects.create(
        workspace=workspace,
        repo=repo,
        goal="Add a health header",
        agent=HarnessImplementationJob.Agent.CLAUDE_CODE,
        status=HarnessImplementationJob.Status.IMPLEMENTING,
        created_by=owner,
    )
    _patch_happy_path_runner(monkeypatch, result=_wrapper_result(commit_sha=NEW_SHA))
    with mock.patch.object(implementation_execution, "dispatch_created_review"):
        implementation_execution.run_harness_implementation_job(job.id)
    job.refresh_from_db()
    assert job.status == HarnessImplementationJob.Status.PENDING_REVIEW
    assert job.produced_commit_sha == NEW_SHA

    # 2. Review verdict (the review container is stubbed): approved -> Gate B.
    review = job.reviews.get()
    review.status = HarnessReview.Status.SUCCEEDED
    review.verdict = HarnessReview.Verdict.APPROVED
    review.save()
    job = request_harness_push(job=job, review=review)
    assert job.status == HarnessImplementationJob.Status.PENDING_GATE_B

    # 3. Gate B decided over the web -> push (stubbed git) -> succeeded.
    push = mock.Mock()
    monkeypatch.setattr(implementation_execution.runner, "create_bundle", mock.Mock())
    monkeypatch.setattr(implementation_execution.runner, "push_bundled_commit", push)
    _approve(client, job.gate_b_request_id, committed)
    job.refresh_from_db()
    assert job.status == HarnessImplementationJob.Status.SUCCEEDED
    push.assert_called_once()
    assert push.call_args.kwargs["produced_sha"] == NEW_SHA

    # 4. "PR aç" -> github_open_pr gate -> one web decision -> PR on the fake.
    gh.heads[job.branch_name] = NEW_SHA
    base = f"/api/v1/harness/implementation-jobs/{job.id}/pull-request"
    response = client.post(base, {"title": "Health header"}, format="json")
    assert response.status_code == 201, response.content
    pr_request_id = response.json()["request"]["id"]
    assert "create_pull_request" not in gh.names()  # nothing before the decision
    _approve(client, pr_request_id, committed)
    assert gh.names().count("create_pull_request") == 1
    assert ConnectorWriteExecution.objects.get().status == "succeeded"
    state = client.get(base).json()
    assert state["pull_request"]["url"] == "https://github.com/acme/app/pull/42"

    # 5. "Deploy" the pushed commit -> deploy gate -> SSH runner -> live.
    target = DeployTarget.objects.create(
        workspace=workspace,
        created_by=owner,
        name="App",
        environment="production",
        ssh_host="203.0.113.7",
        ssh_port=22,
        ssh_user="deploy",
        host_public_key=HOST_KEY,
        remote_app="app",
        repo_git_url=REPO_URL,
        tracked_branch="main",
        health_url="",
        health_timeout_seconds=120,
        require_ci_success=True,
        auto_rollback=True,
        enabled=True,
        current_sha=LIVE_SHA,
    )
    response = client.post(
        "/api/v1/deploys/requests", {"target": str(target.id), "sha": NEW_SHA}, format="json"
    )
    assert response.status_code == 201, response.content
    deploy_request = ApprovalRequest.objects.get(id=response.json()["id"])
    # The snapshot carries GitHub's title and green CI (require_ci_success).
    assert deploy_request.payload["commit"]["source"] == "github"
    assert deploy_request.payload["commit"]["ci_conclusion"] == "success"
    assert "deploy" not in remote.verbs()  # nothing before the decision
    # Eager Celery (tests): the runner's `run_deploy` executes on commit.
    _approve(client, deploy_request.id, committed)
    run = DeployRun.objects.get(approval_request=deploy_request)
    assert run.status == DeployRun.Status.SUCCEEDED
    assert remote.verbs().count("deploy") == 1
    target.refresh_from_db()
    assert target.current_sha == NEW_SHA

    # 6. work_log: the push and the deploy are visible to the owner.
    response = client.get(f"/api/v1/memories?workspace={workspace.id}&type=work_log")
    assert response.status_code == 200, response.content
    body = response.json()
    rows = body["results"] if isinstance(body, dict) else body
    contents = "\n".join(row["content"] for row in rows)
    assert "Harness implementation pushed" in contents
    assert str(deploy_request.id) in contents
    logs = Memory.objects.filter(workspace=workspace, memory_type=Memory.Type.WORK_LOG)
    assert logs.count() >= 2
    assert all(log.status == Memory.Status.APPROVED for log in logs)
