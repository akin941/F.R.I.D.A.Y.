"""Phase 4 branch 1 -- approvals: an `is_agent` actor never decides, and
every request records the channel it arrived over (`requested_channel` +
audit metadata), decisions included."""

import secrets
from types import SimpleNamespace
from unittest import mock

import pytest
from rest_framework.test import APIClient

from accounts.authentication import TOKEN_PREFIX, hash_token
from accounts.models import PersonalAccessToken
from accounts.services import register_user
from approvals.models import ApprovalRequest
from approvals.services import (
    ApprovalPermissionDenied,
    InvalidTransition,
    approve_request,
    create_request,
    reject_request,
    revoke_request,
)
from approvals.tasks import sweep_expired_approval_requests
from audit.models import AuditLog
from core.channels import CHANNEL_CLI, CHANNEL_SYSTEM, CHANNEL_WEB
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"

_TOOL = SimpleNamespace(
    gated=True, create_execution=lambda request: "exec", dispatch=lambda i: None
)


@pytest.fixture(autouse=True)
def _stub_tool():
    with mock.patch("approvals.services._resolve_tool", lambda name: _TOOL):
        yield


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


@pytest.fixture
def agent_user(workspace):
    """An agent service account holding a curator role -- the strongest
    position such an account could ever be put in."""
    user = register_user(email="agent@test.dev", password=PASSWORD, name="Agent")
    user.is_agent = True
    user.save(update_fields=["is_agent"])
    WorkspaceMember.objects.create(workspace=workspace, user=user, role=WorkspaceMember.Role.OWNER)
    return user


def web_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def pat_client(user, workspace):
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    PersonalAccessToken.objects.create(
        user=user, workspace=workspace, name="cli", token_hash=hash_token(raw)
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return client


def pending(workspace, owner, channel=CHANNEL_WEB):
    return create_request(
        workspace=workspace,
        actor=owner,
        tool_name="some_gated_tool",
        origin=ApprovalRequest.Origin.HUMAN,
        channel=channel,
    )


def audit(action, request):
    return AuditLog.objects.get(action=action, resource_id=str(request.id))


# --- is_agent never decides ---


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_agent_actor_on_the_web_channel_gets_403(workspace, owner, agent_user, action):
    request = pending(workspace, owner)
    response = web_client(agent_user).post(f"/api/v1/approvals/requests/{request.id}/{action}")
    assert response.status_code == 403
    request.refresh_from_db()
    assert request.status == ApprovalRequest.Status.PENDING
    assert request.decided_by is None


@pytest.mark.parametrize("decide", [approve_request, reject_request])
def test_agent_actor_is_refused_by_the_service_before_the_channel_check(
    workspace, owner, agent_user, decide
):
    request = pending(workspace, owner)
    with pytest.raises(ApprovalPermissionDenied, match="Agent"):
        decide(approval_request=request, actor=agent_user, channel=CHANNEL_WEB)
    # A low-trust channel gets the same agent refusal first, not the
    # channel one.
    with pytest.raises(ApprovalPermissionDenied, match="Agent"):
        decide(approval_request=request, actor=agent_user, channel=CHANNEL_CLI)


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_human_owner_on_the_web_channel_still_decides(workspace, owner, action):
    request = pending(workspace, owner)
    response = web_client(owner).post(f"/api/v1/approvals/requests/{request.id}/{action}")
    assert response.status_code == 200


# --- requested channel ---


def test_web_request_records_the_web_channel(workspace, owner):
    response = web_client(owner).post(
        "/api/v1/approvals/requests",
        {"workspace": str(workspace.id), "tool_name": "some_gated_tool"},
        format="json",
    )
    assert response.status_code == 201
    assert response.data["requested_channel"] == "web"
    request = ApprovalRequest.objects.get(id=response.data["id"])
    assert request.requested_channel == ApprovalRequest.RequestedChannel.WEB
    metadata = audit("approval.requested", request).metadata
    assert metadata["channel"] == "web"
    assert metadata["requested_channel"] == "web"


def test_cli_request_records_the_cli_channel_and_stays_origin_human(workspace, owner):
    response = pat_client(owner, workspace).post(
        "/api/v1/approvals/requests", {"tool_name": "some_gated_tool"}, format="json"
    )
    assert response.status_code == 201
    request = ApprovalRequest.objects.get(id=response.data["id"])
    assert request.requested_channel == ApprovalRequest.RequestedChannel.CLI
    assert request.origin == ApprovalRequest.Origin.HUMAN
    metadata = audit("approval.requested", request).metadata
    assert (metadata["channel"], metadata["requested_channel"]) == ("cli", "cli")


def test_client_cannot_spoof_the_requested_channel(workspace, owner):
    response = pat_client(owner, workspace).post(
        "/api/v1/approvals/requests",
        {"tool_name": "some_gated_tool", "requested_channel": "web"},
        format="json",
    )
    assert response.status_code == 201
    assert response.data["requested_channel"] == "cli"


def test_system_request_records_the_system_channel(workspace, owner):
    request = create_request(
        workspace=workspace,
        actor=owner,
        tool_name="some_gated_tool",
        origin=ApprovalRequest.Origin.AGENT,
        channel=CHANNEL_SYSTEM,
    )
    assert request.requested_channel == ApprovalRequest.RequestedChannel.SYSTEM
    assert audit("approval.requested", request).metadata["channel"] == "system"


def test_channel_is_required_and_validated(workspace, owner):
    with pytest.raises(TypeError):
        create_request(
            workspace=workspace,
            actor=owner,
            tool_name="some_gated_tool",
            origin=ApprovalRequest.Origin.HUMAN,
        )
    with pytest.raises(InvalidTransition):
        pending(workspace, owner, channel="carrier-pigeon")
    assert not ApprovalRequest.objects.exists()


@pytest.mark.parametrize(
    ("decide", "action"),
    [(approve_request, "approval.approved"), (reject_request, "approval.rejected")],
)
def test_decision_audit_rows_carry_both_channels(workspace, owner, decide, action):
    request = pending(workspace, owner, channel=CHANNEL_CLI)
    decide(approval_request=request, actor=owner, channel=CHANNEL_WEB)
    metadata = audit(action, request).metadata
    assert metadata["channel"] == "web"
    assert metadata["requested_channel"] == "cli"


def test_expiry_and_revoke_audit_rows_carry_the_channel(workspace, owner):
    expiring = pending(workspace, owner, channel=CHANNEL_CLI)
    ApprovalRequest.objects.filter(pk=expiring.pk).update(created_at="2000-01-01T00:00:00Z")
    sweep_expired_approval_requests()
    metadata = audit("approval.expired", expiring).metadata
    assert (metadata["channel"], metadata["requested_channel"]) == ("system", "cli")

    revoked = pending(workspace, owner)
    revoke_request(approval_request=revoked)
    metadata = audit("approval.revoked", revoked).metadata
    assert (metadata["channel"], metadata["requested_channel"]) == ("system", "web")
