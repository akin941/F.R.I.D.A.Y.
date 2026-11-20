"""The deploy snapshot's GitHub wiring (Phase 5 branch 5, brief branch
table: "GitHub title/CI snapshot wired by whichever of 5/6 lands second"):
with an enabled GitHub binding and the target's repo in the workspace's
scope, `request_deploy` snapshots the commit title and CI conclusion from
audited `github_*` reads; otherwise -- or on any read failure -- the manual
path (no title, CI `unknown`) stays, and a CI requirement is refused."""

import pytest

from audit.models import AuditLog
from connectors import github
from connectors.models import ConnectorBinding
from connectors.services import set_binding_enabled
from connectors.tests import fakes
from core.channels import CHANNEL_CLI, CHANNEL_WEB
from deploys.services import DeployCiRequired, request_deploy
from deploys.tests.conftest import SHA_B

pytestmark = pytest.mark.django_db


@pytest.fixture
def gh(monkeypatch):
    state = fakes.install_github(monkeypatch, fakes.GithubServerState())
    state.commit_message = "Fix\x07 the header\nlong body"
    state.workflow_runs = [fakes.workflow_run(SHA_B)]
    return state


@pytest.fixture
def in_scope(monkeypatch, workspace):
    """The target's repo is a registered HarnessRepo of the workspace
    (deploys may not import harness: the resolver is substituted)."""
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


def _request(owner, workspace, target, channel=CHANNEL_WEB):
    return request_deploy(
        workspace=workspace, actor=owner, target=target, sha=SHA_B, channel=channel
    )


def test_github_snapshot_title_and_green_ci(owner, workspace, target, gh, github_on, in_scope):
    target.require_ci_success = True
    target.save()
    request = _request(owner, workspace, target, channel=CHANNEL_CLI)
    assert request.payload["commit"] == {
        "title": "Fix  the header",
        "ci_conclusion": "success",
        "source": "github",
    }
    names = gh.names()
    assert names[-2:] == ["get_commit", "actions_list"]
    assert gh.calls[-1][1]["workflow_runs_filter"] == {"branch": "main"}
    reads = AuditLog.objects.filter(action="connector.read", metadata__outcome="ok")
    assert {r.metadata["tool"] for r in reads} == {"github_branch_head", "github_ci_status"}
    assert all(r.metadata["channel"] == CHANNEL_CLI for r in reads)


def test_failed_ci_is_refused_when_required(owner, workspace, target, gh, github_on, in_scope):
    gh.workflow_runs = [fakes.workflow_run(SHA_B, conclusion="failure")]
    target.require_ci_success = True
    target.save()
    with pytest.raises(DeployCiRequired):
        _request(owner, workspace, target)


def test_out_of_scope_repo_is_the_manual_path(owner, workspace, target, gh, github_on):
    gh.calls.clear()
    request = _request(owner, workspace, target)
    assert request.payload["commit"] == {
        "title": "",
        "ci_conclusion": "unknown",
        "source": "manual",
    }
    assert gh.calls == []


def test_no_binding_is_the_manual_path(owner, workspace, target, gh, in_scope):
    request = _request(owner, workspace, target)
    assert request.payload["commit"]["source"] == "manual"
    assert gh.calls == []


def test_a_sha_mismatch_or_read_failure_is_the_manual_path(
    owner, workspace, target, gh, github_on, in_scope
):
    gh.heads[SHA_B] = fakes.MOVED_SHA  # the server answers for another commit
    request = _request(owner, workspace, target)
    assert request.payload["commit"]["source"] == "manual"
