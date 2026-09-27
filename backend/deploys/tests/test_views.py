"""Deploy API: role x endpoint against the Phase 4 principals (owner web
session, owner PAT `cli`, owner PAT `openai`, non-owner, `is_agent`,
anonymous), workspace isolation, and the target/run/request payloads."""

import pytest
from rest_framework.test import APIClient

from accounts.models import PersonalAccessToken
from accounts.services import register_user
from approvals.models import ApprovalRequest
from audit.models import AuditLog
from deploys.models import DeployRun, DeployTarget
from deploys.services import request_deploy
from deploys.tests.conftest import (
    HOST_KEY,
    PASSWORD,
    REPO,
    SELF_REPO,
    SHA_A,
    SHA_B,
    agent_user,
    make_target,
    pat_client,
)
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

PRINCIPALS = ["owner-web", "owner-cli", "owner-openai", "non-owner", "agent", "anonymous"]


def principal_client(principal, owner, workspace, web_client):
    if principal == "owner-web":
        return web_client(owner)
    if principal == "owner-cli":
        return pat_client(owner, workspace)
    if principal == "owner-openai":
        return pat_client(owner, workspace, kind=PersonalAccessToken.Kind.OPENAI)
    if principal == "non-owner":
        member = register_user(email="member@test.dev", password=PASSWORD)
        # Even an admin member of the owner's workspace is not the owner.
        WorkspaceMember.objects.create(
            workspace=workspace, user=member, role=WorkspaceMember.Role.ADMIN
        )
        return web_client(member)
    if principal == "agent":
        return web_client(agent_user())
    return APIClient()


def _target_body(workspace, **overrides):
    return {
        "workspace": str(workspace.id),
        "name": "Staging",
        "environment": "staging",
        "ssh_host": "vps.example.com",
        "ssh_port": 22,
        "ssh_user": "deploy",
        "host_public_key": HOST_KEY + " root@vps",
        "remote_app": "adia-web",
        "repo_git_url": REPO,
        "tracked_branch": "main",
        "health_url": "https://adia.example/healthz",
        "health_timeout_seconds": 90,
        "require_ci_success": True,
        "auto_rollback": True,
        **overrides,
    }


# expected status per principal, in PRINCIPALS order
WEB_ONLY = {
    "owner-cli": 403,
    "owner-openai": 403,
    "non-owner": 403,
    "agent": 403,
    "anonymous": 401,
}
WEB_OR_CLI = {**WEB_ONLY}
WEB_OR_CLI.pop("owner-cli")


def _expected(ok, matrix, principal, cli_ok=None):
    if principal == "owner-web":
        return ok
    if principal == "owner-cli" and cli_ok is not None:
        return cli_ok
    return matrix[principal]


@pytest.fixture
def pending_run(owner, workspace, target):
    from approvals.services import approve_request
    from core.channels import CHANNEL_WEB

    request = request_deploy(
        workspace=workspace, actor=owner, target=target, sha=SHA_B, channel=CHANNEL_WEB
    )
    approve_request(approval_request=request, actor=owner, channel=CHANNEL_WEB)
    return DeployRun.objects.get(approval_request=request)


@pytest.mark.parametrize("principal", PRINCIPALS)
def test_target_list(owner, workspace, target, web_client, principal):
    client = principal_client(principal, owner, workspace, web_client)
    response = client.get(f"/api/v1/deploys/targets?workspace={workspace.id}")
    assert response.status_code == _expected(200, WEB_ONLY, principal)
    if principal == "owner-web":
        assert [t["name"] for t in response.json()] == ["ADİA"]
    if principal == "owner-cli":
        assert response.json()["code"] == "channel_forbidden"
    if principal == "owner-openai":
        assert response.json()["code"] == "token_kind_forbidden"
    if principal in ("non-owner", "agent"):
        assert response.json()["code"] == "owner_required"


@pytest.mark.parametrize("principal", PRINCIPALS)
def test_target_create(owner, workspace, web_client, principal):
    client = principal_client(principal, owner, workspace, web_client)
    response = client.post("/api/v1/deploys/targets", _target_body(workspace), format="json")
    assert response.status_code == _expected(201, WEB_ONLY, principal)
    assert DeployTarget.objects.count() == (1 if principal == "owner-web" else 0)


@pytest.mark.parametrize("principal", PRINCIPALS)
def test_target_detail_and_edit(owner, workspace, target, web_client, principal):
    client = principal_client(principal, owner, workspace, web_client)
    url = f"/api/v1/deploys/targets/{target.id}"
    assert client.get(url).status_code == _expected(200, WEB_ONLY, principal)
    response = client.patch(url, {"enabled": False}, format="json")
    assert response.status_code == _expected(200, WEB_ONLY, principal)
    target.refresh_from_db()
    assert target.enabled is (principal != "owner-web")


@pytest.mark.parametrize("principal", PRINCIPALS)
def test_target_status_refresh(owner, workspace, target, web_client, principal, remote):
    client = principal_client(principal, owner, workspace, web_client)
    response = client.post(f"/api/v1/deploys/targets/{target.id}/status")
    assert response.status_code == _expected(202, WEB_ONLY, principal)


@pytest.mark.parametrize("principal", PRINCIPALS)
def test_run_list(owner, workspace, pending_run, web_client, principal):
    client = principal_client(principal, owner, workspace, web_client)
    response = client.get(f"/api/v1/deploys/runs?workspace={workspace.id}")
    assert response.status_code == _expected(200, WEB_OR_CLI, principal, cli_ok=200)
    if response.status_code == 200:
        assert [r["id"] for r in response.json()] == [str(pending_run.id)]


@pytest.mark.parametrize("principal", PRINCIPALS)
def test_run_detail(owner, workspace, pending_run, web_client, principal):
    client = principal_client(principal, owner, workspace, web_client)
    response = client.get(f"/api/v1/deploys/runs/{pending_run.id}")
    assert response.status_code == _expected(200, WEB_OR_CLI, principal, cli_ok=200)
    if response.status_code == 200:
        assert response.json()["output_trust"] == "low"


@pytest.mark.parametrize("principal", PRINCIPALS)
def test_run_cancel(owner, workspace, pending_run, web_client, principal):
    client = principal_client(principal, owner, workspace, web_client)
    response = client.post(f"/api/v1/deploys/runs/{pending_run.id}/cancel")
    assert response.status_code == _expected(200, WEB_ONLY, principal)
    pending_run.refresh_from_db()
    expected = DeployRun.Status.CANCELLED if principal == "owner-web" else DeployRun.Status.PENDING
    assert pending_run.status == expected


@pytest.mark.parametrize("principal", PRINCIPALS)
def test_deploy_request(owner, workspace, target, web_client, principal):
    client = principal_client(principal, owner, workspace, web_client)
    response = client.post(
        "/api/v1/deploys/requests", {"target": str(target.id), "sha": SHA_B}, format="json"
    )
    assert response.status_code == _expected(201, WEB_OR_CLI, principal, cli_ok=201)
    created = ApprovalRequest.objects.filter(tool_name="deploy")
    if response.status_code == 201:
        body = response.json()
        assert body["tool_name"] == "deploy"
        assert body["status"] == "pending"
        channel = "web" if principal == "owner-web" else "cli"
        assert body["requested_channel"] == channel
        assert created.get().origin == ApprovalRequest.Origin.HUMAN
    else:
        assert not created.exists()


# --- isolation and payloads ---


@pytest.fixture
def foreign_target(owner):
    stranger = register_user(email="stranger@test.dev", password=PASSWORD)
    stranger_ws = Workspace.objects.get(owner=stranger)
    return make_target(stranger_ws, stranger, name="Theirs")


def test_foreign_targets_are_404(owner, workspace, web_client, foreign_target):
    client = web_client(owner)
    assert client.get(f"/api/v1/deploys/targets/{foreign_target.id}").status_code == 404
    response = client.post(
        "/api/v1/deploys/requests", {"target": str(foreign_target.id), "sha": SHA_B}, format="json"
    )
    assert response.status_code == 404
    listing = client.get(f"/api/v1/deploys/targets?workspace={foreign_target.workspace_id}")
    assert listing.status_code == 404


def test_pat_is_forced_to_its_bound_workspace(owner, workspace, web_client):
    from core.channels import CHANNEL_WEB
    from workspaces.services import create_firm_workspace

    firm = create_firm_workspace(actor=owner, name="Acme", channel=CHANNEL_WEB)
    firm_target = make_target(firm, owner, name="Firm")
    client = pat_client(owner, workspace)  # bound to the personal workspace
    response = client.post(
        "/api/v1/deploys/requests", {"target": str(firm_target.id), "sha": SHA_B}, format="json"
    )
    assert response.status_code == 404
    # A web session may request it.
    response = web_client(owner).post(
        "/api/v1/deploys/requests", {"target": str(firm_target.id), "sha": SHA_B}, format="json"
    )
    assert response.status_code == 201


def test_target_create_validates_and_normalizes(owner, workspace, web_client):
    response = web_client(owner).post(
        "/api/v1/deploys/targets", _target_body(workspace, enabled=True), format="json"
    )
    body = response.json()
    assert response.status_code == 201
    assert body["host_public_key"] == HOST_KEY  # comment dropped
    assert body["enabled"] is False  # off until the owner enables it
    assert body["current_sha"] == ""
    assert body["ssh_destination"] == "deploy@vps.example.com"
    log = AuditLog.objects.get(action="deploy.target.created")
    assert log.metadata["remote_app"] == "adia-web"


@pytest.mark.parametrize(
    "overrides",
    [
        {"remote_app": "Adia_Web"},
        {"ssh_host": "-oProxyCommand=sh"},
        {"ssh_user": "root;id"},
        {"host_public_key": "not a key"},
        {"repo_git_url": "https://token@github.com/acme/adia.git"},
        {"tracked_branch": "../main"},
        {"health_timeout_seconds": 5},
        {"health_url": "ftp://x"},
        {"ssh_port": 0},
    ],
)
def test_target_create_rejects_bad_fields(owner, workspace, web_client, overrides):
    response = web_client(owner).post(
        "/api/v1/deploys/targets", _target_body(workspace, **overrides), format="json"
    )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid"


def test_target_create_refuses_the_self_repo(owner, workspace, web_client):
    response = web_client(owner).post(
        "/api/v1/deploys/targets", _target_body(workspace, repo_git_url=SELF_REPO), format="json"
    )
    assert response.status_code == 400
    assert response.json()["code"] == "deploy.invalid_transition"


def test_target_edit_changes_the_config_hash_and_audits(owner, workspace, target, web_client):
    before = target.config_hash
    response = web_client(owner).patch(
        f"/api/v1/deploys/targets/{target.id}", {"health_timeout_seconds": 60}, format="json"
    )
    assert response.status_code == 200
    assert response.json()["config_hash"] != before
    log = AuditLog.objects.get(action="deploy.target.updated")
    assert log.metadata["changed"] == ["health_timeout_seconds"]
    assert log.metadata["config_changed"] is True


def test_enabling_is_not_a_config_change(owner, workspace, target, web_client):
    before = target.config_hash
    web_client(owner).patch(
        f"/api/v1/deploys/targets/{target.id}", {"enabled": False}, format="json"
    )
    target.refresh_from_db()
    assert target.config_hash == before


def test_ci_required_is_a_coded_400(owner, workspace, target, web_client):
    target.require_ci_success = True
    target.save()
    response = web_client(owner).post(
        "/api/v1/deploys/requests", {"target": str(target.id), "sha": SHA_B}, format="json"
    )
    assert response.status_code == 400
    assert response.json()["code"] == "deploy.ci_required"


def test_status_refresh_while_a_run_is_active_is_refused(owner, pending_run, web_client):
    response = web_client(owner).post(f"/api/v1/deploys/targets/{pending_run.target_id}/status")
    assert response.status_code == 400


def test_status_refresh_runs_on_commit(
    owner, target, web_client, remote, django_capture_on_commit_callbacks
):
    remote.live_sha = SHA_B
    with django_capture_on_commit_callbacks(execute=True):
        response = web_client(owner).post(f"/api/v1/deploys/targets/{target.id}/status")
    assert response.status_code == 202
    target.refresh_from_db()
    assert target.current_sha == SHA_B
    assert remote.verbs() == ["status"]


def test_run_detail_shows_the_plan_and_bounded_output(owner, pending_run, web_client):
    body = web_client(owner).get(f"/api/v1/deploys/runs/{pending_run.id}").json()
    assert body["sha"] == SHA_B
    assert body["expected_current_sha"] == body["rollback_sha"] == SHA_A
    assert body["auto_rollback"] is True
    assert body["health_plan"]["window_seconds"] == 120
    assert "plan" not in body  # the raw snapshot is not exposed


def test_cancel_after_the_deploy_verb_is_a_coded_400(owner, pending_run, web_client):
    from django.utils import timezone

    pending_run.status = DeployRun.Status.DEPLOYING
    pending_run.deploy_started_at = timezone.now()
    pending_run.save()
    response = web_client(owner).post(f"/api/v1/deploys/runs/{pending_run.id}/cancel")
    assert response.status_code == 400
    assert response.json()["code"] == "deploy.invalid_transition"
