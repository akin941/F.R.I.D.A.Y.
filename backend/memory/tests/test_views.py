import pytest
from rest_framework.test import APIClient

from accounts.services import register_user
from audit.models import AuditLog
from memory.models import Memory, compute_fingerprint
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"

ROLES = [
    WorkspaceMember.Role.OWNER,
    WorkspaceMember.Role.ADMIN,
    WorkspaceMember.Role.MEMBER,
    WorkspaceMember.Role.VIEWER,
]


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


@pytest.fixture
def outsider():
    return register_user(email="outsider@test.dev", password=PASSWORD, name="Outsider")


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def user_with_role(workspace, role, email):
    user = register_user(email=email, password=PASSWORD, name=role)
    WorkspaceMember.objects.create(workspace=workspace, user=user, role=role)
    return user


def make_memory(workspace, content="Drinks coffee black.", **overrides):
    fields = {
        "workspace": workspace,
        "memory_type": Memory.Type.PREFERENCE,
        "status": Memory.Status.APPROVED,
        "source": Memory.Source.USER_CREATED,
        "title": "Coffee",
        "content": content,
        "fingerprint": compute_fingerprint(content),
    }
    fields.update(overrides)
    return Memory.objects.create(**fields)


def create_payload(workspace, **overrides):
    payload = {
        "workspace": str(workspace.id),
        "title": "Coffee",
        "content": "Drinks coffee black.",
        "memory_type": "preference",
        "sensitivity": "normal",
    }
    payload.update(overrides)
    return payload


class TestListCreate:
    def test_requires_auth(self, workspace):
        response = APIClient().get("/api/v1/memories", {"workspace": str(workspace.id)})
        assert response.status_code == 401

    def test_requires_workspace_param(self, owner):
        assert client_for(owner).get("/api/v1/memories").status_code == 400

    def test_list_filters_and_audits(self, workspace, owner):
        approved = make_memory(workspace)
        make_memory(workspace, content="pending", status=Memory.Status.PENDING_APPROVAL)
        response = client_for(owner).get(
            "/api/v1/memories", {"workspace": str(workspace.id), "status": "approved"}
        )
        assert response.status_code == 200
        assert [row["id"] for row in response.json()] == [str(approved.id)]
        entry = AuditLog.objects.get(action="memory.read")
        assert entry.metadata["count"] == 1
        assert entry.metadata["memory_ids"] == [str(approved.id)]

    def test_invalid_filters_are_400(self, workspace, owner):
        client = client_for(owner)
        base = {"workspace": str(workspace.id)}
        assert client.get("/api/v1/memories", {**base, "status": "bogus"}).status_code == 400
        assert client.get("/api/v1/memories", {**base, "type": "bogus"}).status_code == 400

    def test_out_of_scope_workspace_is_404(self, workspace, outsider):
        response = client_for(outsider).get("/api/v1/memories", {"workspace": str(workspace.id)})
        assert response.status_code == 404

    def test_create_ignores_client_supplied_server_fields(self, workspace, owner):
        response = client_for(owner).post(
            "/api/v1/memories",
            {
                **create_payload(workspace),
                "status": "pending_approval",
                "source": "ai_suggested",
                "created_by": None,
            },
            format="json",
        )
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "approved"
        assert body["source"] == "user_created"
        assert body["created_by"] == str(owner.id)

    def test_create_duplicate_is_400(self, workspace, owner):
        client = client_for(owner)
        assert (
            client.post("/api/v1/memories", create_payload(workspace), format="json").status_code
            == 201
        )
        response = client.post(
            "/api/v1/memories",
            create_payload(workspace, content="  DRINKS   coffee black. "),
            format="json",
        )
        assert response.status_code == 400


class TestPermissionMatrix:
    """Role × endpoint matrix (CLAUDE.md rule 7)."""

    @pytest.mark.parametrize("role", ROLES)
    def test_all_roles_can_read(self, workspace, role):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        make_memory(workspace)
        response = client_for(user).get("/api/v1/memories", {"workspace": str(workspace.id)})
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
    def test_create(self, workspace, role, expected):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        response = client_for(user).post(
            "/api/v1/memories", create_payload(workspace), format="json"
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
    def test_curation(self, workspace, role, expected, action):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        row = make_memory(
            workspace,
            status=Memory.Status.PENDING_APPROVAL,
            source=Memory.Source.AI_SUGGESTED,
        )
        response = client_for(user).post(f"/api/v1/memories/{row.id}/{action}", format="json")
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
    def test_edit_foreign_row(self, workspace, role, expected):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        row = make_memory(workspace)  # created_by is None → not the actor's own
        response = client_for(user).patch(
            f"/api/v1/memories/{row.id}", {"title": "New"}, format="json"
        )
        assert response.status_code == expected

    def test_member_edits_own_row(self, workspace):
        user = user_with_role(workspace, WorkspaceMember.Role.MEMBER, "m@test.dev")
        row = make_memory(workspace, created_by=user)
        response = client_for(user).patch(
            f"/api/v1/memories/{row.id}", {"title": "Mine"}, format="json"
        )
        assert response.status_code == 200

    @pytest.mark.parametrize(
        "role,expected",
        [
            (WorkspaceMember.Role.OWNER, 204),
            (WorkspaceMember.Role.ADMIN, 204),
            (WorkspaceMember.Role.MEMBER, 403),
            (WorkspaceMember.Role.VIEWER, 403),
        ],
    )
    def test_hard_delete(self, workspace, role, expected):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        row = make_memory(
            workspace,
            status=Memory.Status.PENDING_APPROVAL,
            source=Memory.Source.AI_SUGGESTED,
        )
        response = client_for(user).delete(f"/api/v1/memories/{row.id}")
        assert response.status_code == expected

    @pytest.mark.parametrize("role", ROLES)
    def test_all_roles_can_export(self, workspace, role):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        make_memory(workspace)
        response = client_for(user).get("/api/v1/memories/export", {"workspace": str(workspace.id)})
        assert response.status_code == 200


class TestDetailAndTransitions:
    def test_out_of_scope_detail_is_404_not_403(self, workspace, outsider):
        row = make_memory(workspace)
        client = client_for(outsider)
        assert client.get(f"/api/v1/memories/{row.id}").status_code == 404
        assert (
            client.patch(f"/api/v1/memories/{row.id}", {"title": "X"}, format="json").status_code
            == 404
        )
        assert client.post(f"/api/v1/memories/{row.id}/approve", format="json").status_code == 404

    def test_unknown_transition_is_404(self, workspace, owner):
        row = make_memory(workspace)
        response = client_for(owner).post(f"/api/v1/memories/{row.id}/promote", format="json")
        assert response.status_code == 404

    def test_illegal_transition_is_400(self, workspace, owner):
        row = make_memory(workspace)  # approved
        response = client_for(owner).post(f"/api/v1/memories/{row.id}/reject", format="json")
        assert response.status_code == 400

    def test_approve_with_curated_sensitivity(self, workspace, owner):
        row = make_memory(
            workspace,
            status=Memory.Status.PENDING_APPROVAL,
            source=Memory.Source.AI_SUGGESTED,
        )
        response = client_for(owner).post(
            f"/api/v1/memories/{row.id}/approve",
            {"sensitivity": "private", "title": "Curated"},
            format="json",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "approved"
        assert body["sensitivity"] == "private"
        assert body["title"] == "Curated"

    def test_detail_read_is_audited(self, workspace, owner):
        row = make_memory(workspace)
        client_for(owner).get(f"/api/v1/memories/{row.id}")
        entry = AuditLog.objects.get(action="memory.read")
        assert entry.metadata == {"memory_ids": [str(row.id)], "count": 1}


class TestExport:
    def test_export_contains_every_status_and_private(self, workspace, owner):
        make_memory(workspace, content="approved one")
        make_memory(
            workspace,
            content="private one",
            sensitivity=Memory.Sensitivity.PRIVATE,
        )
        make_memory(workspace, content="pending one", status=Memory.Status.PENDING_APPROVAL)
        make_memory(workspace, content="rejected one", status=Memory.Status.REJECTED)
        make_memory(workspace, content="archived one", status=Memory.Status.ARCHIVED)
        response = client_for(owner).get(
            "/api/v1/memories/export", {"workspace": str(workspace.id)}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 5
        statuses = {row["status"] for row in body["memories"]}
        assert statuses == {"approved", "pending_approval", "rejected", "archived"}
        sensitivities = {row["sensitivity"] for row in body["memories"]}
        assert "private" in sensitivities

    def test_export_headers_and_audit(self, workspace, owner):
        make_memory(workspace)
        response = client_for(owner).get(
            "/api/v1/memories/export", {"workspace": str(workspace.id)}
        )
        assert response["Cache-Control"] == "no-store"
        assert "attachment" in response["Content-Disposition"]
        entry = AuditLog.objects.get(action="memory.exported")
        assert entry.metadata["count"] == 1
        assert entry.metadata["statuses"] == {"approved": 1}
        assert set(entry.metadata.keys()) == {"count", "statuses"}  # no content, ever

    def test_export_scope_equals_read_scope(self, workspace, owner, outsider):
        make_memory(workspace)
        response = client_for(outsider).get(
            "/api/v1/memories/export", {"workspace": str(workspace.id)}
        )
        assert response.status_code == 404


class TestPermissionMatrixTransitions:
    """HTTP-level matrix for forget/restore + detail read (completes the
    role × endpoint coverage flagged in Codex round 1)."""

    @pytest.mark.parametrize("role", ROLES)
    def test_all_roles_can_read_detail(self, workspace, role):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        row = make_memory(workspace)
        response = client_for(user).get(f"/api/v1/memories/{row.id}")
        assert response.status_code == 200
        assert response.json()["id"] == str(row.id)

    @pytest.mark.parametrize(
        "role,expected",
        [
            (WorkspaceMember.Role.OWNER, 200),
            (WorkspaceMember.Role.ADMIN, 200),
            (WorkspaceMember.Role.MEMBER, 403),
            (WorkspaceMember.Role.VIEWER, 403),
        ],
    )
    def test_forget_foreign_row(self, workspace, role, expected):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        row = make_memory(workspace)  # approved, created_by None → foreign
        response = client_for(user).post(f"/api/v1/memories/{row.id}/forget", format="json")
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
    def test_restore(self, workspace, role, expected):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        row = make_memory(workspace, status=Memory.Status.ARCHIVED)
        response = client_for(user).post(f"/api/v1/memories/{row.id}/restore", format="json")
        assert response.status_code == expected

    def test_member_forgets_own_row_via_http(self, workspace):
        user = user_with_role(workspace, WorkspaceMember.Role.MEMBER, "own-forget@test.dev")
        row = make_memory(workspace, created_by=user)
        response = client_for(user).post(f"/api/v1/memories/{row.id}/forget", format="json")
        assert response.status_code == 200
        assert response.json()["status"] == "archived"

    def test_member_cannot_forget_ai_suggested_via_http(self, workspace, owner):
        user = user_with_role(workspace, WorkspaceMember.Role.MEMBER, "ai-forget@test.dev")
        row = make_memory(
            workspace,
            source=Memory.Source.AI_SUGGESTED,
            created_by=user,  # even as nominal creator: predicate needs user_created
        )
        response = client_for(user).post(f"/api/v1/memories/{row.id}/forget", format="json")
        assert response.status_code == 403
