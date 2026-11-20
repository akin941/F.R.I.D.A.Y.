"""`GET /deploys/targets/<id>/head` (Phase 5 branch 9): the "Deploy"
dialog's SHA prefill. Owner + web only; GitHub reads are the audited
`github_*` reads of the snapshot path; any other case is the manual path
(empty `sha`, CI `unknown`), never an error."""

import pytest

from audit.models import AuditLog
from connectors import github
from connectors.models import ConnectorBinding
from connectors.services import set_binding_enabled
from connectors.tests import fakes
from core.channels import CHANNEL_CLI, CHANNEL_WEB
from deploys.services import DeployPermissionDenied, resolve_branch_head
from deploys.tests.test_views import PRINCIPALS, WEB_ONLY, _expected, principal_client

pytestmark = pytest.mark.django_db


@pytest.fixture
def gh(monkeypatch):
    state = fakes.install_github(monkeypatch, fakes.GithubServerState())
    state.commit_message = "Ship\x07 the header\nlong body"
    state.workflow_runs = [fakes.workflow_run(fakes.HEAD_SHA)]
    return state


@pytest.fixture
def in_scope(monkeypatch, workspace):
    canonical = github.canonical_remote("acme", "adia-web")

    def resolver(ws, remote):
        if ws.id == workspace.id and remote == canonical:
            return github.RepoScope(sensitive=False, default_branch="main")
        return None

    monkeypatch.setattr(github, "_repo_resolver", resolver)


@pytest.fixture
def github_on(workspace, owner, gh, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        set_binding_enabled(
            workspace=workspace, actor=owner, connector="github", enabled=True, channel=CHANNEL_WEB
        )
    return ConnectorBinding.objects.get(connector="github")


def _url(target):
    return f"/api/v1/deploys/targets/{target.id}/head"


@pytest.mark.parametrize("principal", PRINCIPALS)
def test_head_role_matrix(owner, workspace, target, web_client, principal):
    client = principal_client(principal, owner, workspace, web_client)
    response = client.get(_url(target))
    assert response.status_code == _expected(200, WEB_ONLY, principal)


def test_manual_path_without_github(owner, workspace, target, web_client):
    body = web_client(owner).get(_url(target)).json()
    assert body == {
        "tracked_branch": "main",
        "current_sha": target.current_sha,
        "sha": "",
        "title": "",
        "title_trust": "repo_text",
        "ci_conclusion": "unknown",
        "source": "manual",
        "require_ci_success": False,
    }


def test_github_head_title_and_ci(owner, workspace, target, gh, github_on, in_scope, web_client):
    body = web_client(owner).get(_url(target)).json()
    assert body["sha"] == fakes.HEAD_SHA
    assert body["title"] == "Ship  the header"
    assert body["ci_conclusion"] == "success"
    assert body["source"] == "github"
    reads = AuditLog.objects.filter(action="connector.read", metadata__outcome="ok")
    assert {r.metadata["tool"] for r in reads} == {"github_branch_head", "github_ci_status"}
    assert all(r.metadata["channel"] == CHANNEL_WEB for r in reads)
    # The tracked branch name is the ref read: the dialog suggests its head.
    head_calls = [args for name, args in gh.calls if name == "get_commit"]
    assert head_calls[-1]["sha"] == "main"


def test_out_of_scope_repo_reads_nothing(owner, workspace, target, gh, github_on, web_client):
    gh.calls.clear()
    body = web_client(owner).get(_url(target)).json()
    assert body["source"] == "manual" and body["sha"] == ""
    assert gh.calls == []


def test_read_failure_is_the_manual_path(owner, workspace, target, gh, github_on, in_scope):
    # A server that answers with a non-SHA is never suggested.
    gh.heads["main"] = "not-a-sha"
    result = resolve_branch_head(target=target, actor=owner, channel=CHANNEL_WEB)
    assert result["source"] == "manual" and result["sha"] == ""
    assert result["ci_conclusion"] == "unknown"


def test_cli_channel_refused_in_service(owner, target):
    with pytest.raises(DeployPermissionDenied):
        resolve_branch_head(target=target, actor=owner, channel=CHANNEL_CLI)


def test_foreign_target_is_404(owner, web_client):
    from accounts.services import register_user
    from deploys.tests.conftest import make_target
    from workspaces.models import Workspace

    stranger = register_user(email="stranger@test.dev", password="Str0ngDevPass!42")
    foreign = make_target(Workspace.objects.get(owner=stranger), stranger)
    assert web_client(owner).get(_url(foreign)).status_code == 404
