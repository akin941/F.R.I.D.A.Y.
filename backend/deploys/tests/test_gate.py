"""The `deploy` gate (ADR-010 decisions 2 and 7, brief D10): minted only by
`request_deploy` (system-created), its dialog text, `create_execution`'s
named refusals -- `target_changed`, `target_disabled`, `target_busy`
including the `IntegrityError` backstop and concurrent approvals -- and the
end-to-end approve -> dispatch -> run path."""

import ast
import threading
from pathlib import Path

import pytest
from django.db import connection, connections
from rest_framework.test import APIClient

from approvals.models import ApprovalRequest
from approvals.services import InvalidTransition, approve_request, create_request
from audit.models import AuditLog
from core.channels import CHANNEL_CLI, CHANNEL_SYSTEM, CHANNEL_WEB
from deploys import tool
from deploys.models import DeployRun, DeployTarget
from deploys.services import (
    CommitSnapshot,
    DeployCiRequired,
    DeployInvalidTransition,
    DeployPermissionDenied,
    build_payload,
    request_deploy,
)
from deploys.tests.conftest import (
    PASSWORD,
    SELF_REPO,
    SHA_A,
    SHA_B,
    SHA_C,
    make_target,
    pat_client,
)
from tools.registry import get_tool, list_tool_names

pytestmark = pytest.mark.django_db

BACKEND = Path(__file__).resolve().parents[2]
S = DeployRun.Status
E = DeployRun.ErrorCode


def _request(owner, workspace, target, sha=SHA_B, channel=CHANNEL_WEB):
    return request_deploy(workspace=workspace, actor=owner, target=target, sha=sha, channel=channel)


# --- minting ---


def test_deploy_is_a_registered_system_only_gate():
    spec = get_tool("deploy")
    assert spec.gated and spec.user_invocable is False and spec.records_work_log
    assert "deploy" not in list_tool_names()


@pytest.mark.parametrize("origin", ApprovalRequest.Origin.values)
def test_deploy_is_unmintable_without_system_created(owner, workspace, target, origin):
    payload = build_payload(target, SHA_B, CommitSnapshot("", "unknown", "manual"))
    with pytest.raises(InvalidTransition, match="only be requested by the system"):
        create_request(
            workspace=workspace,
            actor=owner,
            tool_name="deploy",
            payload=payload,
            origin=origin,
            channel=CHANNEL_SYSTEM,
        )


def test_deploy_is_unmintable_via_post_approvals(owner, workspace, target, web_client):
    payload = build_payload(target, SHA_B, CommitSnapshot("", "unknown", "manual"))
    body = {"workspace": str(workspace.id), "tool_name": "deploy", "payload": payload}
    for client in (web_client(owner), pat_client(owner, workspace)):
        response = client.post("/api/v1/approvals/requests", body, format="json")
        assert response.status_code == 400
        assert response.json()["code"] == "approval.invalid_transition"
    assert not ApprovalRequest.objects.filter(tool_name="deploy").exists()


def test_only_request_deploy_passes_system_created():
    """The one owning call site (ADR-010 decision 2), by AST scan of the
    whole app."""
    hits = []
    for path in (BACKEND / "deploys").rglob("*.py"):
        if "tests" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.keyword) and node.arg == "system_created":
                hits.append((path.name, getattr(node.value, "value", None)))
    assert hits == [("services.py", True)]


@pytest.mark.parametrize("channel", [CHANNEL_WEB, CHANNEL_CLI])
def test_request_deploy_mints_a_human_origin_system_created_gate(owner, workspace, target, channel):
    request = _request(owner, workspace, target, channel=channel)
    assert request.tool_name == "deploy"
    assert request.status == ApprovalRequest.Status.PENDING
    assert request.origin == ApprovalRequest.Origin.HUMAN
    assert request.requested_channel == channel
    requested = AuditLog.objects.get(action="approval.requested")
    assert requested.metadata["system_created"] is True
    assert AuditLog.objects.filter(action="deploy.requested").exists()
    payload = request.payload
    assert payload["sha"] == SHA_B
    assert payload["expected_current_sha"] == payload["rollback_sha"] == SHA_A
    assert payload["config_hash"] == target.config_hash
    assert payload["commit"] == {"title": "", "ci_conclusion": "unknown", "source": "manual"}
    assert payload["verbs"] == ["status", "deploy", "health", "rollback", "health"]
    assert payload["health_plan"]["pass_threshold"] == 2
    assert payload["health_plan"]["fail_threshold"] == 3


def test_manual_sha_is_refused_when_ci_is_required(owner, workspace, target):
    target.require_ci_success = True
    target.save()
    with pytest.raises(DeployCiRequired):
        _request(owner, workspace, target)
    assert not ApprovalRequest.objects.exists()


def test_a_green_ci_snapshot_satisfies_the_requirement(owner, workspace, target, monkeypatch):
    """The branch-5 seam: a GitHub-backed snapshot with `success` passes."""
    target.require_ci_success = True
    target.save()
    monkeypatch.setattr(
        "deploys.services.resolve_commit_snapshot",
        lambda target, sha, **_: CommitSnapshot(
            "Fix\x07 the header\nfor real", "success", "github"
        ),
    )
    request = _request(owner, workspace, target)
    assert request.payload["commit"] == {
        "title": "Fix  the header for real",
        "ci_conclusion": "success",
        "source": "github",
    }


@pytest.mark.parametrize(
    "change, message",
    [
        ({"enabled": False}, "not enabled"),
        ({"current_sha": ""}, "status check"),
        ({"current_sha": SHA_B}, "already live"),
    ],
)
def test_request_refusals(owner, workspace, target, change, message):
    for name, value in change.items():
        setattr(target, name, value)
    target.save()
    with pytest.raises(DeployInvalidTransition, match=message):
        _request(owner, workspace, target)


def test_request_refuses_a_self_repo_target(owner, workspace, target):
    DeployTarget.objects.filter(pk=target.pk).update(repo_git_url=SELF_REPO)
    target.refresh_from_db()
    with pytest.raises(DeployInvalidTransition, match="Jarvis's own"):
        _request(owner, workspace, target)


def test_request_fails_closed_without_self_repo_identifiers(owner, workspace, target, settings):
    settings.HARNESS_SELF_REPO_IDENTIFIERS = []
    with pytest.raises(DeployInvalidTransition, match="not configured"):
        _request(owner, workspace, target)


@pytest.mark.parametrize("sha", ["abc123", "z" * 40, "", SHA_B[:39]])
def test_request_refuses_a_malformed_sha(owner, workspace, target, sha):
    with pytest.raises(DeployInvalidTransition):
        _request(owner, workspace, target, sha=sha)


@pytest.mark.parametrize("channel", ["openai", "telegram", "system", "unknown"])
def test_request_refuses_other_channels(owner, workspace, target, channel):
    with pytest.raises(DeployPermissionDenied):
        _request(owner, workspace, target, channel=channel)


def test_request_refuses_a_non_owner(workspace, target):
    from accounts.services import register_user
    from workspaces.models import WorkspaceMember

    member = register_user(email="member@test.dev", password=PASSWORD)
    WorkspaceMember.objects.create(
        workspace=workspace, user=member, role=WorkspaceMember.Role.ADMIN
    )
    with pytest.raises(DeployPermissionDenied):
        _request(member, workspace, target)


def test_request_uppercase_sha_is_normalized(owner, workspace, target):
    assert _request(owner, workspace, target, sha=SHA_B.upper()).payload["sha"] == SHA_B


# --- the dialog ---


def test_display_text_states_the_whole_plan(owner, workspace, target):
    request = _request(owner, workspace, target)
    summary = tool.describe_deploy_request(request)
    assert summary["text"] == (
        "Deploy `a1b2c3d` to ADİA production (deploy@203.0.113.7, app `adia-web`). "
        "Current `9f8e7d6`. If health fails within 120 s, Jarvis rolls back to `9f8e7d6`."
    )
    assert summary["rollback_sha"] == SHA_A
    assert summary["commit_title_trust"] == "repo_text"
    assert summary["health"]["window_seconds"] == 120
    assert summary["health"]["fail_threshold"] == 3


def test_display_text_without_auto_rollback(owner, workspace, target):
    target.auto_rollback = False
    target.ssh_port = 2222
    target.save()
    request = _request(owner, workspace, target)
    text = tool.describe_deploy_request(request)["text"]
    assert "(deploy@203.0.113.7:2222, app `adia-web`)" in text
    assert text.endswith("nothing is rolled back without a new decision.")
    assert request.payload["verbs"] == ["status", "deploy", "health"]


def test_display_summary_reaches_the_web_decider_only(owner, workspace, target, web_client):
    request = _request(owner, workspace, target)
    url = f"/api/v1/approvals/requests/{request.id}"
    web = web_client(owner).get(url).json()
    assert web["display_summary"]["kind"] == "deploy"
    cli = pat_client(owner, workspace).get(url).json()
    assert cli["display_summary"] is None
    assert "payload" not in web


def test_describe_request_ignores_a_tampered_payload(owner, workspace, target):
    request = _request(owner, workspace, target)
    request.payload["target"]["ssh_host"] = "evil.example"
    assert tool.describe_deploy_request(request) is None


# --- create_execution refusals ---


def _approve(owner, request):
    return approve_request(approval_request=request, actor=owner, channel=CHANNEL_WEB)


def test_approval_creates_a_pending_run(owner, workspace, target):
    request = _approve(owner, _request(owner, workspace, target))
    run = request.deploy_run
    assert run.status == S.PENDING
    assert run.plan == request.payload
    assert tool.describe_deploy_execution(request)["status"] == "pending"


def test_changed_config_is_refused_at_approval(owner, workspace, target):
    request = _request(owner, workspace, target)
    target.tracked_branch = "release"
    target.save()
    _approve(owner, request)
    run = DeployRun.objects.get(approval_request=request)
    assert (run.status, run.error_code) == (S.REFUSED, E.TARGET_CHANGED)
    assert AuditLog.objects.filter(action="deploy.refused", resource_id=str(run.id)).exists()


def test_disabled_target_is_refused_at_approval(owner, workspace, target):
    request = _request(owner, workspace, target)
    target.enabled = False
    target.save()
    _approve(owner, request)
    run = DeployRun.objects.get(approval_request=request)
    assert (run.status, run.error_code) == (S.REFUSED, E.TARGET_DISABLED)


def test_second_approval_for_a_busy_target_is_target_busy(owner, workspace, target, web_client):
    first = _request(owner, workspace, target)
    second = _request(owner, workspace, target, sha=SHA_C)  # a second pending request may exist
    _approve(owner, first)
    response = web_client(owner).post(f"/api/v1/approvals/requests/{second.id}/approve")
    assert response.status_code == 200
    assert response.json()["execution"]["status"] == "refused"
    assert response.json()["execution"]["error_code"] == "target_busy"
    run = DeployRun.objects.get(approval_request=second)
    assert (run.status, run.error_code) == (S.REFUSED, E.TARGET_BUSY)
    assert AuditLog.objects.filter(action="deploy.refused", resource_id=str(run.id)).exists()
    assert DeployRun.objects.filter(target=target, status=S.PENDING).count() == 1


def test_integrity_error_backstop_maps_to_target_busy(
    owner, workspace, target, web_client, monkeypatch
):
    first = _request(owner, workspace, target)
    second = _request(owner, workspace, target, sha=SHA_C)
    _approve(owner, first)
    # Blind the locked pre-check: only the partial unique constraint is left.
    monkeypatch.setattr("deploys.tool._active_run_exists", lambda target: False)
    response = web_client(owner).post(f"/api/v1/approvals/requests/{second.id}/approve")
    assert response.status_code == 200
    run = DeployRun.objects.get(approval_request=second)
    assert (run.status, run.error_code) == (S.REFUSED, E.TARGET_BUSY)
    assert (
        DeployRun.objects.filter(target=target, status__in=DeployRun.ACTIVE_STATUSES).count() == 1
    )


def test_after_the_first_run_finishes_the_drift_check_decides(owner, workspace, target, remote):
    first = _request(owner, workspace, target)
    second = _request(owner, workspace, target, sha=SHA_C)
    from deploys import execution

    _approve(owner, first)
    execution.execute_deploy_run(str(first.deploy_run.id))
    _approve(owner, second)
    run = DeployRun.objects.get(approval_request=second)
    execution.execute_deploy_run(str(run.id))
    run.refresh_from_db()
    # Live is now SHA_B; the second request expected SHA_A.
    assert (run.status, run.error_code) == (S.FAILED, E.TARGET_DRIFTED)
    assert remote.verbs().count("deploy") == 1


def test_validate_request_rejects_a_tampered_or_foreign_payload(owner, workspace, target):
    payload = build_payload(target, SHA_B, CommitSnapshot("", "unknown", "manual"))
    tool.validate_deploy_request(workspace, payload)
    from tools.registry import ToolValidationError

    tampered = {**payload, "target": {**payload["target"], "ssh_user": "root"}}
    with pytest.raises(ToolValidationError):
        tool.validate_deploy_request(workspace, tampered)
    with pytest.raises(ToolValidationError):
        tool.validate_deploy_request(workspace, {**payload, "target_id": "not-a-uuid"})
    other = make_target(workspace, owner, name="Other")
    with pytest.raises(ToolValidationError):
        tool.validate_deploy_request(workspace, {**payload, "config_hash": other.config_hash})


# --- end to end through the approvals API ---


def test_web_decision_runs_the_deploy(
    owner, workspace, target, web_client, remote, django_capture_on_commit_callbacks
):
    request = _request(owner, workspace, target)
    with django_capture_on_commit_callbacks(execute=True):
        response = web_client(owner).post(f"/api/v1/approvals/requests/{request.id}/approve")
    assert response.status_code == 200
    run = DeployRun.objects.get(approval_request=request)
    assert run.status == S.SUCCEEDED
    assert remote.verbs() == ["status", "deploy", "health", "health"]


def test_cli_cannot_decide_a_deploy(owner, workspace, target, remote):
    request = _request(owner, workspace, target, channel=CHANNEL_CLI)
    response = pat_client(owner, workspace).post(f"/api/v1/approvals/requests/{request.id}/approve")
    assert response.status_code == 403
    request.refresh_from_db()
    assert request.status == ApprovalRequest.Status.PENDING
    assert not DeployRun.objects.exists()
    assert remote.calls == []


def test_anonymous_cannot_decide(owner, workspace, target):
    request = _request(owner, workspace, target)
    assert APIClient().post(f"/api/v1/approvals/requests/{request.id}/approve").status_code == 401


# --- concurrency ---


@pytest.mark.django_db(transaction=True)
def test_concurrent_approvals_leave_exactly_one_active_run(monkeypatch):
    """Two approvals of two requests for one target, decided at the same
    moment in two connections: one pending run, one `target_busy`."""
    from accounts.services import register_user
    from workspaces.models import Workspace

    # The runs stay `pending`: the eager on_commit dispatch finds nothing to do.
    monkeypatch.setattr("deploys.execution.execute_deploy_run", lambda run_id: None)
    owner = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    owner.is_owner = True
    owner.save()
    workspace = Workspace.objects.get(owner=owner)
    target = make_target(workspace, owner)
    requests = [
        _request(owner, workspace, target),
        _request(owner, workspace, target, sha=SHA_C),
    ]
    barrier = threading.Barrier(2)
    errors = []

    def decide(request):
        try:
            barrier.wait(timeout=10)
            approve_request(approval_request=request, actor=owner, channel=CHANNEL_WEB)
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=decide, args=(r,)) for r in requests]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert errors == []
    runs = list(DeployRun.objects.filter(target=target))
    assert sorted(r.status for r in runs) == [S.PENDING, S.REFUSED]
    assert [r.error_code for r in runs if r.status == S.REFUSED] == [E.TARGET_BUSY]
    connection.close()


def test_commit_snapshot_is_resolved_outside_the_row_lock(owner, workspace, target, monkeypatch):
    """The snapshot's connector reads run before `select_for_update`, never
    under it; a target changed meanwhile is refused, not requested blind."""
    from deploys import services

    def snapshot_while_the_target_moves(unlocked, sha, *, actor, channel):
        DeployTarget.objects.filter(pk=unlocked.pk).update(current_sha=SHA_C)
        return CommitSnapshot("title", "success", "manual")

    monkeypatch.setattr(services, "resolve_commit_snapshot", snapshot_while_the_target_moves)
    with pytest.raises(DeployInvalidTransition, match="changed"):
        _request(owner, workspace, target)
    assert not ApprovalRequest.objects.filter(tool_name="deploy").exists()
