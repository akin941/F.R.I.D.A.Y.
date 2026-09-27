from types import SimpleNamespace
from unittest import mock

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from accounts.services import register_user
from approvals.models import ApprovalRequest
from tools.models import VaultExportRun
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"

ROLES = [
    WorkspaceMember.Role.OWNER,
    WorkspaceMember.Role.ADMIN,
    WorkspaceMember.Role.MEMBER,
    WorkspaceMember.Role.VIEWER,
]

_GATED_TOOL = SimpleNamespace(gated=True)


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


@pytest.fixture
def second_admin(workspace):
    user = register_user(email="admin2@test.dev", password=PASSWORD, name="Admin2")
    WorkspaceMember.objects.create(workspace=workspace, user=user, role=WorkspaceMember.Role.ADMIN)
    return user


@pytest.fixture
def outsider():
    return register_user(email="outsider@test.dev", password=PASSWORD, name="Outsider")


def user_with_role(workspace, role, email):
    user = register_user(email=email, password=PASSWORD, name=role)
    WorkspaceMember.objects.create(workspace=workspace, user=user, role=role)
    return user


def make_request(workspace, requested_by=None, **overrides):
    fields = {"workspace": workspace, "tool_name": "some_gated_tool", "requested_by": requested_by}
    fields.update(overrides)
    return ApprovalRequest.objects.create(**fields)


def create_payload(workspace, **overrides):
    payload = {"workspace": str(workspace.id), "tool_name": "some_gated_tool"}
    payload.update(overrides)
    return payload


@mock.patch("approvals.services._resolve_tool", lambda name: _GATED_TOOL)
class TestListCreate:
    def test_requires_auth(self, workspace):
        response = APIClient().get("/api/v1/approvals/requests", {"workspace": str(workspace.id)})
        assert response.status_code == 401

    def test_requires_workspace_param(self, owner, web_client):
        assert web_client(owner).get("/api/v1/approvals/requests").status_code == 400

    def test_list_filters_by_status(self, workspace, owner, second_admin, web_client):
        pending = make_request(workspace, requested_by=owner)
        make_request(workspace, requested_by=owner, status=ApprovalRequest.Status.APPROVED)
        response = web_client(owner).get(
            "/api/v1/approvals/requests", {"workspace": str(workspace.id), "status": "pending"}
        )
        assert response.status_code == 200
        assert [row["id"] for row in response.json()] == [str(pending.id)]

    def test_invalid_status_filter_is_400(self, workspace, owner, web_client):
        response = web_client(owner).get(
            "/api/v1/approvals/requests", {"workspace": str(workspace.id), "status": "bogus"}
        )
        assert response.status_code == 400

    def test_out_of_scope_workspace_is_404(self, workspace, outsider, web_client):
        response = web_client(outsider).get(
            "/api/v1/approvals/requests", {"workspace": str(workspace.id)}
        )
        assert response.status_code == 404

    def test_create_ignores_client_supplied_server_fields(self, workspace, owner, web_client):
        response = web_client(owner).post(
            "/api/v1/approvals/requests",
            {**create_payload(workspace), "status": "approved", "decided_by": None},
            format="json",
        )
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "pending"
        assert body["requested_by"] == str(owner.id)

    def test_response_never_includes_payload(self, workspace, owner, web_client):
        response = web_client(owner).post(
            "/api/v1/approvals/requests",
            create_payload(workspace, payload={"secret": "value"}),
            format="json",
        )
        assert response.status_code == 201
        assert "payload" not in response.json()

    def test_unknown_tool_is_400(self, workspace, owner, web_client):
        with mock.patch("approvals.services._resolve_tool", lambda name: None):
            response = web_client(owner).post(
                "/api/v1/approvals/requests", create_payload(workspace), format="json"
            )
        assert response.status_code == 400


class TestExecutionField:
    """brief §4: a run-status summary from the request's tool, or `None`
    if the tool has no `describe_execution` hook or nothing has run yet."""

    def test_execution_is_none_without_a_describe_execution_hook(
        self, workspace, owner, web_client
    ):
        make_request(workspace, requested_by=owner)
        with mock.patch("approvals.services._resolve_tool", lambda name: _GATED_TOOL):
            response = web_client(owner).get(
                "/api/v1/approvals/requests", {"workspace": str(workspace.id)}
            )
        assert response.status_code == 200
        assert response.json()[0]["execution"] is None

    def test_execution_reflects_the_tools_describe_execution_hook(
        self, workspace, owner, web_client
    ):
        make_request(workspace, requested_by=owner)
        tool_with_execution = SimpleNamespace(
            gated=True,
            describe_execution=lambda approval_request: {"status": "succeeded"},
        )
        with mock.patch("approvals.services._resolve_tool", lambda name: tool_with_execution):
            response = web_client(owner).get(
                "/api/v1/approvals/requests", {"workspace": str(workspace.id)}
            )
        assert response.status_code == 200
        assert response.json()[0]["execution"] == {"status": "succeeded"}

    def test_the_execution_field_does_not_cost_a_query_per_row(self, workspace, owner, web_client):
        """Codex review (branch 4): the list endpoint is unpaginated, and
        `describe_vault_export_execution` used to run its own query per
        row -- turning one list read into N+1. The view now
        `select_related("vault_export_run")`s the queryset, so the query
        count must stay flat regardless of how many requests are listed."""
        requests = [
            ApprovalRequest.objects.create(
                workspace=workspace, tool_name="vault_export", requested_by=owner
            )
            for _ in range(3)
        ]
        for request in requests:
            VaultExportRun.objects.create(
                approval_request=request, workspace=workspace, status=VaultExportRun.Status.PENDING
            )

        with CaptureQueriesContext(connection) as one_row:
            response = web_client(owner).get(
                "/api/v1/approvals/requests", {"workspace": str(workspace.id)}
            )
        assert response.status_code == 200

        more_requests = requests + [
            ApprovalRequest.objects.create(
                workspace=workspace, tool_name="vault_export", requested_by=owner
            )
            for _ in range(5)
        ]
        for request in more_requests[3:]:
            VaultExportRun.objects.create(
                approval_request=request, workspace=workspace, status=VaultExportRun.Status.PENDING
            )

        with CaptureQueriesContext(connection) as more_rows:
            response = web_client(owner).get(
                "/api/v1/approvals/requests", {"workspace": str(workspace.id)}
            )
        assert response.status_code == 200
        assert len(more_rows.captured_queries) == len(one_row.captured_queries)


class TestPermissionMatrix:
    """Role x endpoint matrix (CLAUDE.md rule 7)."""

    @pytest.mark.parametrize("role", ROLES)
    def test_all_roles_can_read(self, workspace, role, web_client):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        make_request(workspace)
        response = web_client(user).get(
            "/api/v1/approvals/requests", {"workspace": str(workspace.id)}
        )
        assert response.status_code == 200
        assert len(response.json()) == 1

    @pytest.mark.parametrize(
        "role,expected",
        [
            (WorkspaceMember.Role.OWNER, 201),
            (WorkspaceMember.Role.ADMIN, 201),
            (WorkspaceMember.Role.MEMBER, 201),
            (WorkspaceMember.Role.VIEWER, 403),
        ],
    )
    @mock.patch("approvals.services._resolve_tool", lambda name: _GATED_TOOL)
    def test_create(self, workspace, role, expected, web_client):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        response = web_client(user).post(
            "/api/v1/approvals/requests", create_payload(workspace), format="json"
        )
        assert response.status_code == expected

    @pytest.mark.parametrize(
        "role,expected",
        [
            (WorkspaceMember.Role.OWNER, 200),
            (WorkspaceMember.Role.ADMIN, 200),
            (WorkspaceMember.Role.MEMBER, 403),
            (WorkspaceMember.Role.VIEWER, 403),
        ],
    )
    @pytest.mark.parametrize("action", ["approve", "reject"])
    def test_decide(self, workspace, owner, role, expected, action, web_client):
        user = user_with_role(workspace, role, f"role-{role}-{action}@test.dev")
        request = make_request(workspace, requested_by=owner)
        response = web_client(user).post(
            f"/api/v1/approvals/requests/{request.id}/{action}", format="json"
        )
        assert response.status_code == expected


class TestOwnerDecision:
    def test_owner_approves_own_request_via_http(self, workspace, owner, web_client):
        # ADR-008 decision 3: the explicit, separate, audited decision over
        # the high-trust web channel IS the gate -- there is no second
        # human in a single-owner system.
        request = make_request(workspace, requested_by=owner)
        response = web_client(owner).post(
            f"/api/v1/approvals/requests/{request.id}/approve", format="json"
        )
        assert response.status_code == 200
        assert response.json()["status"] == "approved"

    def test_different_admin_can_approve_via_http(self, workspace, owner, second_admin, web_client):
        request = make_request(workspace, requested_by=owner)
        response = web_client(second_admin).post(
            f"/api/v1/approvals/requests/{request.id}/approve", format="json"
        )
        assert response.status_code == 200
        assert response.json()["status"] == "approved"


class TestDetailAndTransitions:
    def test_out_of_scope_transition_is_404_not_403(self, workspace, owner, outsider, web_client):
        request = make_request(workspace, requested_by=owner)
        response = web_client(outsider).post(
            f"/api/v1/approvals/requests/{request.id}/approve", format="json"
        )
        assert response.status_code == 404

    def test_unknown_action_is_404(self, workspace, owner, second_admin, web_client):
        request = make_request(workspace, requested_by=owner)
        response = web_client(second_admin).post(
            f"/api/v1/approvals/requests/{request.id}/promote", format="json"
        )
        assert response.status_code == 404

    def test_illegal_transition_is_400(self, workspace, owner, second_admin, web_client):
        request = make_request(
            workspace, requested_by=owner, status=ApprovalRequest.Status.APPROVED
        )
        response = web_client(second_admin).post(
            f"/api/v1/approvals/requests/{request.id}/reject", format="json"
        )
        assert response.status_code == 400
