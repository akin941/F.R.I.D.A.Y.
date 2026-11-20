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

    def test_requires_workspace_param(self, owner, web_client):
        assert web_client(owner).get("/api/v1/memories").status_code == 400

    def test_list_filters_and_audits(self, workspace, owner, web_client):
        approved = make_memory(workspace)
        make_memory(workspace, content="pending", status=Memory.Status.PENDING_APPROVAL)
        response = web_client(owner).get(
            "/api/v1/memories", {"workspace": str(workspace.id), "status": "approved"}
        )
        assert response.status_code == 200
        assert [row["id"] for row in response.json()] == [str(approved.id)]
        entry = AuditLog.objects.get(action="memory.read")
        assert entry.metadata["count"] == 1
        assert entry.metadata["memory_ids"] == [str(approved.id)]

    def test_invalid_filters_are_400(self, workspace, owner, web_client):
        client = web_client(owner)
        base = {"workspace": str(workspace.id)}
        assert client.get("/api/v1/memories", {**base, "status": "bogus"}).status_code == 400
        assert client.get("/api/v1/memories", {**base, "type": "bogus"}).status_code == 400

    def test_out_of_scope_workspace_is_404(self, workspace, outsider, web_client):
        response = web_client(outsider).get("/api/v1/memories", {"workspace": str(workspace.id)})
        assert response.status_code == 404

    def test_create_ignores_client_supplied_server_fields(self, workspace, owner, web_client):
        response = web_client(owner).post(
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

    def test_create_duplicate_is_400(self, workspace, owner, web_client):
        client = web_client(owner)
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
    def test_all_roles_can_read(self, workspace, role, web_client):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        make_memory(workspace)
        response = web_client(user).get("/api/v1/memories", {"workspace": str(workspace.id)})
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
    def test_create(self, workspace, role, expected, web_client):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        response = web_client(user).post(
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
    def test_curation(self, workspace, role, expected, action, web_client):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        row = make_memory(
            workspace,
            status=Memory.Status.PENDING_APPROVAL,
            source=Memory.Source.AI_SUGGESTED,
        )
        response = web_client(user).post(f"/api/v1/memories/{row.id}/{action}", format="json")
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
    def test_edit_foreign_row(self, workspace, role, expected, web_client):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        row = make_memory(workspace)  # created_by is None → not the actor's own
        response = web_client(user).patch(
            f"/api/v1/memories/{row.id}", {"title": "New"}, format="json"
        )
        assert response.status_code == expected

    def test_member_edits_own_row(self, workspace, web_client):
        user = user_with_role(workspace, WorkspaceMember.Role.MEMBER, "m@test.dev")
        row = make_memory(workspace, created_by=user)
        response = web_client(user).patch(
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
    def test_hard_delete(self, workspace, role, expected, web_client):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        row = make_memory(
            workspace,
            status=Memory.Status.PENDING_APPROVAL,
            source=Memory.Source.AI_SUGGESTED,
        )
        response = web_client(user).delete(f"/api/v1/memories/{row.id}")
        assert response.status_code == expected

    @pytest.mark.parametrize("role", ROLES)
    def test_all_roles_can_export(self, workspace, role, web_client):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        make_memory(workspace)
        response = web_client(user).get("/api/v1/memories/export", {"workspace": str(workspace.id)})
        assert response.status_code == 200


class TestDetailAndTransitions:
    def test_out_of_scope_detail_is_404_not_403(self, workspace, outsider, web_client):
        row = make_memory(workspace)
        client = web_client(outsider)
        assert client.get(f"/api/v1/memories/{row.id}").status_code == 404
        assert (
            client.patch(f"/api/v1/memories/{row.id}", {"title": "X"}, format="json").status_code
            == 404
        )
        assert client.post(f"/api/v1/memories/{row.id}/approve", format="json").status_code == 404

    def test_unknown_transition_is_404(self, workspace, owner, web_client):
        row = make_memory(workspace)
        response = web_client(owner).post(f"/api/v1/memories/{row.id}/promote", format="json")
        assert response.status_code == 404

    def test_illegal_transition_is_400(self, workspace, owner, web_client):
        row = make_memory(workspace)  # approved
        response = web_client(owner).post(f"/api/v1/memories/{row.id}/reject", format="json")
        assert response.status_code == 400

    def test_approve_with_curated_sensitivity(self, workspace, owner, web_client):
        row = make_memory(
            workspace,
            status=Memory.Status.PENDING_APPROVAL,
            source=Memory.Source.AI_SUGGESTED,
        )
        response = web_client(owner).post(
            f"/api/v1/memories/{row.id}/approve",
            {"sensitivity": "private", "title": "Curated"},
            format="json",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "approved"
        assert body["sensitivity"] == "private"
        assert body["title"] == "Curated"

    def test_detail_read_is_audited(self, workspace, owner, web_client):
        row = make_memory(workspace)
        web_client(owner).get(f"/api/v1/memories/{row.id}")
        entry = AuditLog.objects.get(action="memory.read")
        assert entry.metadata == {"memory_ids": [str(row.id)], "count": 1}


class TestExport:
    def test_export_contains_every_status_and_private(self, workspace, owner, web_client):
        make_memory(workspace, content="approved one")
        make_memory(
            workspace,
            content="private one",
            sensitivity=Memory.Sensitivity.PRIVATE,
        )
        make_memory(workspace, content="pending one", status=Memory.Status.PENDING_APPROVAL)
        make_memory(workspace, content="rejected one", status=Memory.Status.REJECTED)
        make_memory(workspace, content="archived one", status=Memory.Status.ARCHIVED)
        response = web_client(owner).get(
            "/api/v1/memories/export", {"workspace": str(workspace.id)}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 5
        statuses = {row["status"] for row in body["memories"]}
        assert statuses == {"approved", "pending_approval", "rejected", "archived"}
        sensitivities = {row["sensitivity"] for row in body["memories"]}
        assert "private" in sensitivities

    def test_export_headers_and_audit(self, workspace, owner, web_client):
        make_memory(workspace)
        response = web_client(owner).get(
            "/api/v1/memories/export", {"workspace": str(workspace.id)}
        )
        assert response["Cache-Control"] == "no-store"
        assert "attachment" in response["Content-Disposition"]
        entry = AuditLog.objects.get(action="memory.exported")
        assert entry.metadata["count"] == 1
        assert entry.metadata["statuses"] == {"approved": 1}
        assert set(entry.metadata.keys()) == {"count", "statuses"}  # no content, ever

    def test_export_scope_equals_read_scope(self, workspace, owner, outsider, web_client):
        make_memory(workspace)
        response = web_client(outsider).get(
            "/api/v1/memories/export", {"workspace": str(workspace.id)}
        )
        assert response.status_code == 404


class TestPermissionMatrixTransitions:
    """HTTP-level matrix for forget/restore + detail read (completes the
    role × endpoint coverage flagged in Codex round 1)."""

    @pytest.mark.parametrize("role", ROLES)
    def test_all_roles_can_read_detail(self, workspace, role, web_client):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        row = make_memory(workspace)
        response = web_client(user).get(f"/api/v1/memories/{row.id}")
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
    def test_forget_foreign_row(self, workspace, role, expected, web_client):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        row = make_memory(workspace)  # approved, created_by None → foreign
        response = web_client(user).post(f"/api/v1/memories/{row.id}/forget", format="json")
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
    def test_restore(self, workspace, role, expected, web_client):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        row = make_memory(workspace, status=Memory.Status.ARCHIVED)
        response = web_client(user).post(f"/api/v1/memories/{row.id}/restore", format="json")
        assert response.status_code == expected

    def test_member_forgets_own_row_via_http(self, workspace, web_client):
        user = user_with_role(workspace, WorkspaceMember.Role.MEMBER, "own-forget@test.dev")
        row = make_memory(workspace, created_by=user)
        response = web_client(user).post(f"/api/v1/memories/{row.id}/forget", format="json")
        assert response.status_code == 200
        assert response.json()["status"] == "archived"

    def test_member_cannot_forget_ai_suggested_via_http(self, workspace, owner, web_client):
        user = user_with_role(workspace, WorkspaceMember.Role.MEMBER, "ai-forget@test.dev")
        row = make_memory(
            workspace,
            source=Memory.Source.AI_SUGGESTED,
            created_by=user,  # even as nominal creator: predicate needs user_created
        )
        response = web_client(user).post(f"/api/v1/memories/{row.id}/forget", format="json")
        assert response.status_code == 403


class TestVoiceProposal:
    """Phase 6 branch 3 (ADR-015, brief A6): the voice route "hatırla ki ..."
    creates a `pending_approval` proposal through this same create path --
    never an approved row -- that the owner approves by click."""

    def test_proposal_is_born_pending_and_user_created(self, workspace, owner, web_client):
        response = web_client(owner).post(
            "/api/v1/memories", create_payload(workspace, proposal=True), format="json"
        )
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "pending_approval"
        assert body["source"] == "user_created"
        memory = Memory.objects.get(id=body["id"])
        assert memory.status == Memory.Status.PENDING_APPROVAL
        assert memory.embedding is None
        entry = AuditLog.objects.get(action="memory.created")
        assert entry.metadata["status"] == "pending_approval"
        assert entry.metadata["proposal"] is True
        assert "content" not in entry.metadata and "title" not in entry.metadata

    def test_proposal_cannot_request_a_status(self, workspace, owner, web_client):
        response = web_client(owner).post(
            "/api/v1/memories",
            {**create_payload(workspace, proposal=True), "status": "approved"},
            format="json",
        )
        assert response.status_code == 201
        assert response.json()["status"] == "pending_approval"

    def test_default_create_is_still_approved(self, workspace, owner, web_client):
        response = web_client(owner).post(
            "/api/v1/memories", create_payload(workspace), format="json"
        )
        assert response.json()["status"] == "approved"
        assert "proposal" not in AuditLog.objects.get(action="memory.created").metadata

    def test_owner_approves_the_proposal_by_click(self, workspace, owner, web_client):
        client = web_client(owner)
        created = client.post(
            "/api/v1/memories", create_payload(workspace, proposal=True), format="json"
        ).json()
        response = client.post(f"/api/v1/memories/{created['id']}/approve", format="json")
        assert response.status_code == 200
        assert Memory.objects.get(id=created["id"]).status == Memory.Status.APPROVED

    @pytest.mark.parametrize(
        "role,expected",
        [
            (WorkspaceMember.Role.OWNER, 201),
            (WorkspaceMember.Role.ADMIN, 201),
            (WorkspaceMember.Role.MEMBER, 201),
            (WorkspaceMember.Role.VIEWER, 403),
        ],
    )
    def test_proposal_role_matrix(self, workspace, role, expected, web_client):
        user = user_with_role(workspace, role, f"proposal-{role}@test.dev")
        response = web_client(user).post(
            "/api/v1/memories", create_payload(workspace, proposal=True), format="json"
        )
        assert response.status_code == expected
        if expected == 201:
            assert response.json()["status"] == "pending_approval"

    def test_proposal_into_another_workspace_is_404(self, workspace, outsider, web_client):
        response = web_client(outsider).post(
            "/api/v1/memories", create_payload(workspace, proposal=True), format="json"
        )
        assert response.status_code == 404
        assert not Memory.objects.exists()

    def test_pending_proposal_is_never_context(self, workspace, owner, web_client):
        from memory.context import select_context_memories

        web_client(owner).post(
            "/api/v1/memories", create_payload(workspace, proposal=True), format="json"
        )
        selected = select_context_memories(actor=owner, workspace=workspace, query_text="")
        assert selected == []
