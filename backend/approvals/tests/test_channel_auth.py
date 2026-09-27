"""PAT/CLI-channel behavior on the approvals endpoints (MVP 9 §1/§2):
scoped_for_actor() workspace fixation, and the channel-restriction on
approve/reject -- the core safety property this MVP exists to guarantee."""

from types import SimpleNamespace
from unittest import mock

import pytest
from rest_framework.test import APIClient

from accounts.authentication import hash_token
from accounts.models import PersonalAccessToken
from accounts.services import register_user
from approvals.models import ApprovalRequest
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"

_GATED_TOOL = SimpleNamespace(gated=True)


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


@pytest.fixture
def other_workspace(owner):
    stranger = register_user(email="stranger@test.dev", password=PASSWORD, name="Stranger")
    ws = Workspace.objects.get(owner=stranger)
    WorkspaceMember.objects.create(workspace=ws, user=owner, role=WorkspaceMember.Role.ADMIN)
    return ws


@pytest.fixture
def pat(owner, workspace):
    raw = "jpat_test-raw-token-0000000000000000"
    PersonalAccessToken.objects.create(
        user=owner, workspace=workspace, name="test cli", token_hash=hash_token(raw)
    )
    return raw


def pat_client(raw_token):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw_token}")
    return client


def make_request(workspace, requested_by=None, **overrides):
    fields = {"workspace": workspace, "tool_name": "some_gated_tool", "requested_by": requested_by}
    fields.update(overrides)
    return ApprovalRequest.objects.create(**fields)


# --- workspace fixation ---


@mock.patch("approvals.services._resolve_tool", lambda name: _GATED_TOOL)
def test_pat_create_with_mismatched_body_workspace_is_forced_to_bound(
    pat, workspace, other_workspace
):
    response = pat_client(pat).post(
        "/api/v1/approvals/requests",
        {"workspace": str(other_workspace.id), "tool_name": "some_gated_tool"},
        format="json",
    )
    assert response.status_code == 201
    assert str(response.data["workspace"]) == str(workspace.id)


def test_pat_list_without_workspace_param_succeeds_scoped_to_bound_workspace(
    pat, workspace, other_workspace, owner
):
    make_request(workspace, requested_by=owner)
    make_request(other_workspace, requested_by=owner)

    response = pat_client(pat).get("/api/v1/approvals/requests")
    assert response.status_code == 200
    assert len(response.data) == 1
    assert str(response.data[0]["workspace"]) == str(workspace.id)


def test_pat_transition_for_request_in_a_different_workspace_is_refused_uniformly(
    pat, owner, workspace, other_workspace
):
    """Since Phase 4 branch 2 IsWebChannel refuses every PAT decision up
    front -- the same 403 for a foreign request as for one in the bound
    workspace, so existence still never leaks."""
    foreign = make_request(other_workspace, requested_by=owner)
    local = make_request(workspace, requested_by=owner)
    foreign_response = pat_client(pat).post(f"/api/v1/approvals/requests/{foreign.id}/approve")
    local_response = pat_client(pat).post(f"/api/v1/approvals/requests/{local.id}/approve")
    assert foreign_response.status_code == local_response.status_code == 403


# --- channel restriction on decisions (the core safety property) ---


def test_pat_authenticated_curator_cannot_approve(owner, workspace, pat):
    other_admin = register_user(email="admin2@test.dev", password=PASSWORD, name="Admin2")
    WorkspaceMember.objects.create(
        workspace=workspace, user=other_admin, role=WorkspaceMember.Role.ADMIN
    )
    request = make_request(workspace, requested_by=owner)

    # Bind a PAT to other_admin so requester != decider still holds --
    # isolates the channel check as the thing under test.
    raw = "jpat_admin2-raw-token-0000000000000"
    PersonalAccessToken.objects.create(
        user=other_admin, workspace=workspace, name="admin2 cli", token_hash=hash_token(raw)
    )

    response = pat_client(raw).post(f"/api/v1/approvals/requests/{request.id}/approve")
    assert response.status_code == 403
    request.refresh_from_db()
    assert request.status == ApprovalRequest.Status.PENDING


def test_pat_authenticated_curator_cannot_reject(owner, workspace, pat):
    other_admin = register_user(email="admin3@test.dev", password=PASSWORD, name="Admin3")
    WorkspaceMember.objects.create(
        workspace=workspace, user=other_admin, role=WorkspaceMember.Role.ADMIN
    )
    request = make_request(workspace, requested_by=owner)

    raw = "jpat_admin3-raw-token-0000000000000"
    PersonalAccessToken.objects.create(
        user=other_admin, workspace=workspace, name="admin3 cli", token_hash=hash_token(raw)
    )

    response = pat_client(raw).post(f"/api/v1/approvals/requests/{request.id}/reject")
    assert response.status_code == 403
    request.refresh_from_db()
    assert request.status == ApprovalRequest.Status.PENDING


def test_web_authenticated_curator_decision_is_unaffected(owner, workspace, web_client):
    """Regression guard: this MVP must not break the one channel that
    already worked (MVP 5's existing approval flow)."""
    other_admin = register_user(email="admin4@test.dev", password=PASSWORD, name="Admin4")
    WorkspaceMember.objects.create(
        workspace=workspace, user=other_admin, role=WorkspaceMember.Role.ADMIN
    )
    request = make_request(workspace, requested_by=owner)

    response = web_client(other_admin).post(f"/api/v1/approvals/requests/{request.id}/approve")
    assert response.status_code == 200
    assert response.data["status"] == "approved"
