"""`github_open_pr` from a pushed harness job (Phase 5 branch 5, brief D7):
`POST`/`GET /api/v1/harness/implementation-jobs/<id>/pull-request` (owner,
web session only), the system-created gate minted only from a `succeeded`
job, the real HarnessRepo scope resolver, a PR after one web decision and
its CI state on the job -- against the fake `github-mcp-server`."""

import secrets

import pytest
from rest_framework.test import APIClient

from accounts.authentication import TOKEN_PREFIX, hash_token
from accounts.models import PersonalAccessToken, User
from accounts.services import register_user
from approvals.models import ApprovalRequest
from audit.models import AuditLog
from connectors import github
from connectors.models import ConnectorBinding, ConnectorWriteExecution
from connectors.services import set_binding_enabled
from connectors.tests import fakes
from core.channels import CHANNEL_WEB
from harness.models import HarnessImplementationJob, HarnessRepo, HarnessReview
from harness.pull_requests import build_body, resolve_repo_scope
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"
BASE = "/api/v1/harness/implementation-jobs"


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
def repo(workspace):
    return HarnessRepo.objects.create(
        workspace=workspace,
        name="app",
        git_url="git@github.com:Acme/App.git",
        default_branch="main",
        enabled=True,
        test_command="pytest -q",
    )


def _job(workspace, owner, repo, *, status=HarnessImplementationJob.Status.SUCCEEDED, sha=None):
    job = HarnessImplementationJob.objects.create(
        workspace=workspace,
        repo=repo,
        goal="Add a health header\n\nDetails follow.",
        created_by=owner,
        status=status,
        produced_commit_sha=fakes.HEAD_SHA if sha is None else sha,
        diff_summary="Adds X-Health to every response.",
        test_result={"exit_code": 0, "passed": True},
    )
    HarnessReview.objects.create(
        workspace=workspace,
        implementation_job=job,
        agent="codex_cli",
        status=HarnessReview.Status.SUCCEEDED,
        verdict=HarnessReview.Verdict.APPROVED,
    )
    return job


@pytest.fixture
def job(workspace, owner, repo):
    return _job(workspace, owner, repo)


@pytest.fixture
def gh(monkeypatch):
    return fakes.install_github(monkeypatch, fakes.GithubServerState())


@pytest.fixture
def github_on(workspace, owner, gh, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        set_binding_enabled(
            workspace=workspace, actor=owner, connector="github", enabled=True, channel=CHANNEL_WEB
        )
    binding = ConnectorBinding.objects.get(workspace=workspace, connector="github")
    assert set(binding.last_health["tools"].values()) == {"ok"}
    return binding


def _pat_client(owner, workspace, kind):
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    PersonalAccessToken.objects.create(
        workspace=workspace, user=owner, name=kind, kind=kind, token_hash=hash_token(raw)
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return client


def _client(principal, owner, workspace, web_client):
    if principal == "owner-web":
        return web_client(owner)
    if principal == "owner-pat-cli":
        return _pat_client(owner, workspace, PersonalAccessToken.Kind.CLI)
    if principal == "owner-pat-openai":
        return _pat_client(owner, workspace, PersonalAccessToken.Kind.OPENAI)
    if principal == "non-owner":
        return web_client(register_user(email="member@test.dev", password=PASSWORD))
    if principal == "agent":
        return web_client(User.objects.create_user(email="agent@test.dev", is_agent=True))
    return APIClient()


PRINCIPALS = [
    ("owner-web", True),
    ("owner-pat-cli", 401),
    ("owner-pat-openai", 401),
    ("non-owner", 403),
    ("agent", 403),
    ("anonymous", 401),
]


@pytest.mark.parametrize("principal, expected", PRINCIPALS)
def test_post_role_matrix(principal, expected, owner, workspace, job, github_on, web_client):
    response = _client(principal, owner, workspace, web_client).post(
        f"{BASE}/{job.id}/pull-request", {}, format="json"
    )
    assert response.status_code == (201 if expected is True else expected), response.content
    minted = ApprovalRequest.objects.filter(tool_name="github_open_pr").exists()
    assert minted == (expected is True)


@pytest.mark.parametrize("principal, expected", PRINCIPALS)
def test_get_role_matrix(principal, expected, owner, workspace, job, github_on, web_client):
    response = _client(principal, owner, workspace, web_client).get(f"{BASE}/{job.id}/pull-request")
    assert response.status_code == (200 if expected is True else expected)


def test_one_web_decision_opens_the_pr_and_ci_shows_on_the_job(
    owner, workspace, job, gh, github_on, web_client, django_capture_on_commit_callbacks
):
    gh.heads[job.branch_name] = fakes.HEAD_SHA
    gh.workflow_runs = [fakes.workflow_run(fakes.HEAD_SHA)]
    client = web_client(owner)
    response = client.post(
        f"{BASE}/{job.id}/pull-request", {"title": "  Health header  "}, format="json"
    )
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["request"]["status"] == "pending"
    assert body["pull_request"] is None
    request = ApprovalRequest.objects.get(id=body["request"]["id"])
    args = request.payload["arguments"]
    assert args == {
        "owner": "acme",
        "repo": "app",
        "title": "Health header",
        "body": build_body(job),
        "head": job.branch_name,
        "base": "main",
        "draft": False,
        "maintainer_can_modify": False,
        "reviewers": [],
    }
    assert request.payload["context"] == {
        "head_sha": fakes.HEAD_SHA,
        "origin_ref": f"harness_implementation_job:{job.id}",
    }
    assert "model-written" in args["body"] and "Add a health header" in args["body"]
    assert "passed" in args["body"] and "approved" in args["body"]
    assert AuditLog.objects.filter(
        action="approval.requested", metadata__system_created=True
    ).exists()
    assert "create_pull_request" not in gh.names()  # nothing before the decision

    with django_capture_on_commit_callbacks(execute=True):
        decided = client.post(f"/api/v1/approvals/requests/{request.id}/approve")
    assert decided.status_code == 200
    assert gh.names().count("create_pull_request") == 1
    assert ConnectorWriteExecution.objects.get().status == "succeeded"

    state = client.get(f"{BASE}/{job.id}/pull-request").json()
    assert state["pull_request"] == {"number": 42, "url": "https://github.com/acme/app/pull/42"}
    assert state["execution"]["status"] == "succeeded"
    assert state["ci"]["state"] == "success"
    assert state["can_request"] is False
    # A second PR for the same branch is refused.
    again = client.post(f"{BASE}/{job.id}/pull-request", {}, format="json")
    assert again.status_code == 409
    assert again.json()["code"] == "harness.pull_request_exists"


def test_head_moved_is_shown_and_a_new_request_is_allowed(
    owner, workspace, job, gh, github_on, web_client, django_capture_on_commit_callbacks
):
    gh.heads[job.branch_name] = fakes.MOVED_SHA
    client = web_client(owner)
    request_id = client.post(f"{BASE}/{job.id}/pull-request", {}, format="json").json()["request"][
        "id"
    ]
    with django_capture_on_commit_callbacks(execute=True):
        client.post(f"/api/v1/approvals/requests/{request_id}/approve")
    state = client.get(f"{BASE}/{job.id}/pull-request").json()
    assert state["execution"] == {
        "status": "failed",
        "error_type": "head_moved",
        "finished_at": state["execution"]["finished_at"],
    }
    assert "create_pull_request" not in gh.names()
    assert state["can_request"] is True


@pytest.mark.parametrize(
    "status",
    [
        HarnessImplementationJob.Status.PENDING_GATE_B,
        HarnessImplementationJob.Status.PUSHING,
        HarnessImplementationJob.Status.FAILED,
    ],
)
def test_non_succeeded_jobs_cannot_get_a_pr(owner, workspace, repo, github_on, web_client, status):
    job = _job(workspace, owner, repo, status=status)
    response = web_client(owner).post(f"{BASE}/{job.id}/pull-request", {}, format="json")
    assert response.status_code == 400
    assert response.json()["code"] == "harness.pull_request_not_ready"
    assert not ApprovalRequest.objects.exists()


def test_a_job_without_a_commit_cannot_get_a_pr(owner, workspace, repo, github_on, web_client):
    job = _job(workspace, owner, repo, sha="")
    response = web_client(owner).post(f"{BASE}/{job.id}/pull-request", {}, format="json")
    assert response.status_code == 400
    assert not ApprovalRequest.objects.exists()


def test_github_not_enabled_is_refused(owner, workspace, job, gh, web_client):
    response = web_client(owner).post(f"{BASE}/{job.id}/pull-request", {}, format="json")
    assert response.status_code == 400
    assert response.json()["code"] == "harness.github_unavailable"
    assert gh.calls == []


def test_non_github_repo_is_refused(owner, workspace, github_on, web_client):
    other = HarnessRepo.objects.create(
        workspace=workspace, name="x", git_url="git@gitlab.com:acme/x.git", enabled=True
    )
    job = _job(workspace, owner, other)
    response = web_client(owner).post(f"{BASE}/{job.id}/pull-request", {}, format="json")
    assert response.status_code == 400
    assert response.json()["code"] == "harness.github_unavailable"


def test_another_workspaces_job_is_invisible(owner, workspace, github_on, web_client):
    from workspaces.services import create_firm_workspace

    firm = create_firm_workspace(actor=owner, name="Firm B", channel=CHANNEL_WEB)
    firm_repo = HarnessRepo.objects.create(
        workspace=firm, name="b", git_url="https://github.com/firmb/app", enabled=True
    )
    job = _job(firm, owner, firm_repo)
    # The owner is a member of both; the GitHub binding is only the personal
    # workspace's -> refused before any GitHub traffic.
    response = web_client(owner).post(f"{BASE}/{job.id}/pull-request", {}, format="json")
    assert response.status_code == 400
    assert response.json()["code"] == "harness.github_unavailable"
    outsider = register_user(email="out@test.dev", password=PASSWORD)
    outsider.is_owner = True  # even an installation owner outside the workspace
    assert web_client(outsider).get(f"{BASE}/{job.id}/pull-request").status_code == 404


def test_real_resolver_scopes_by_workspace_and_spelling(owner, workspace, repo):
    from workspaces.services import create_firm_workspace

    firm = create_firm_workspace(actor=owner, name="Firm B", channel=CHANNEL_WEB)
    scope = resolve_repo_scope(workspace, github.canonical_remote("acme", "app"))
    assert scope == github.RepoScope(sensitive=False, default_branch="main")
    assert resolve_repo_scope(firm, github.canonical_remote("acme", "app")) is None
    assert resolve_repo_scope(workspace, github.canonical_remote("acme", "other")) is None
    # Registered at startup by HarnessConfig.ready().
    assert github.get_repo_resolver() is resolve_repo_scope


def test_cross_workspace_repo_read_is_refused_before_any_call(
    owner, workspace, repo, gh, github_on
):
    from tools.registry import ReadToolError
    from workspaces.services import create_firm_workspace

    firm = create_firm_workspace(actor=owner, name="Firm B", channel=CHANNEL_WEB)
    HarnessRepo.objects.create(
        workspace=firm, name="b", git_url="https://github.com/firmb/secret", enabled=True
    )
    gh.calls.clear()
    with pytest.raises(ReadToolError) as exc:
        github.read_commit(
            workspace=workspace,
            actor=owner,
            owner="firmb",
            repo="secret",
            ref="main",
            channel=CHANNEL_WEB,
        )
    assert exc.value.code == "out_of_scope"
    assert gh.calls == []
