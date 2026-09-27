"""Role x endpoint matrix + cross-workspace isolation for the workspace
detail/PATCH surface (brief §2, CLAUDE.md rule 7)."""

import pytest
from rest_framework.test import APIClient

from accounts.services import register_user
from audit.models import AuditLog
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"

ROLES = [
    WorkspaceMember.Role.OWNER,
    WorkspaceMember.Role.ADMIN,
    WorkspaceMember.Role.MEMBER,
    WorkspaceMember.Role.VIEWER,
]

VALID_PROFILE = {
    "repos": [{"name": "jarvis-os", "url": "https://github.com/x/jarvis-os"}],
    "notes": "dossier notes",
}


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


class TestRetrieve:
    def test_requires_auth(self, workspace):
        response = APIClient().get(f"/api/v1/workspaces/{workspace.id}")
        assert response.status_code == 401

    @pytest.mark.parametrize("role", ROLES)
    def test_all_roles_can_retrieve(self, workspace, role, web_client):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        response = web_client(user).get(f"/api/v1/workspaces/{workspace.id}")
        assert response.status_code == 200
        assert response.json()["id"] == str(workspace.id)
        assert response.json()["profile"] == {}
        assert response.json()["role"] == role

    def test_outsider_gets_404_not_403(self, workspace, outsider, web_client):
        response = web_client(outsider).get(f"/api/v1/workspaces/{workspace.id}")
        assert response.status_code == 404

    def test_unknown_workspace_id_is_404(self, owner, web_client):
        response = web_client(owner).get("/api/v1/workspaces/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404


class TestPatchPermissionMatrix:
    """Role x endpoint matrix (CLAUDE.md rule 7): owner/admin write, member/
    viewer are read-only on `profile`."""

    @pytest.mark.parametrize(
        "role,expected",
        [
            (WorkspaceMember.Role.OWNER, 200),
            (WorkspaceMember.Role.ADMIN, 200),
            (WorkspaceMember.Role.MEMBER, 403),
            (WorkspaceMember.Role.VIEWER, 403),
        ],
    )
    def test_patch_profile_by_role(self, workspace, role, expected, web_client):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        response = web_client(user).patch(
            f"/api/v1/workspaces/{workspace.id}",
            {"profile": VALID_PROFILE},
            format="json",
        )
        assert response.status_code == expected
        workspace.refresh_from_db()
        if expected == 200:
            assert workspace.profile == VALID_PROFILE
            assert response.json()["profile"] == VALID_PROFILE
        else:
            assert workspace.profile == {}

    def test_patch_requires_auth(self, workspace):
        response = APIClient().patch(
            f"/api/v1/workspaces/{workspace.id}", {"profile": {}}, format="json"
        )
        assert response.status_code == 401

    def test_outsider_patch_is_404_not_403(self, workspace, outsider, web_client):
        response = web_client(outsider).patch(
            f"/api/v1/workspaces/{workspace.id}", {"profile": {}}, format="json"
        )
        assert response.status_code == 404

    def test_patch_invalid_profile_is_400(self, workspace, owner, web_client):
        response = web_client(owner).patch(
            f"/api/v1/workspaces/{workspace.id}",
            {"profile": {"unknown_key": "x"}},
            format="json",
        )
        assert response.status_code == 400
        workspace.refresh_from_db()
        assert workspace.profile == {}

    def test_patch_ignores_other_fields(self, workspace, owner, web_client):
        """`type`/`enabled_modules` never become writable through this
        endpoint -- only `profile` and (Phase 4 branch 2) `name`."""
        response = web_client(owner).patch(
            f"/api/v1/workspaces/{workspace.id}",
            {"profile": VALID_PROFILE, "type": "company", "enabled_modules": ["x"]},
            format="json",
        )
        assert response.status_code == 200
        workspace.refresh_from_db()
        assert workspace.type == Workspace.Type.PERSONAL
        assert workspace.enabled_modules == []

    def test_patch_replaces_profile_wholesale(self, workspace, owner, web_client):
        client = web_client(owner)
        client.patch(
            f"/api/v1/workspaces/{workspace.id}",
            {"profile": VALID_PROFILE},
            format="json",
        )
        response = client.patch(
            f"/api/v1/workspaces/{workspace.id}",
            {"profile": {"notes": "replaced"}},
            format="json",
        )
        assert response.status_code == 200
        workspace.refresh_from_db()
        assert workspace.profile == {"notes": "replaced"}


class TestAudit:
    def test_successful_patch_records_audit_without_profile_content(
        self, workspace, owner, web_client
    ):
        before = AuditLog.objects.count()
        response = web_client(owner).patch(
            f"/api/v1/workspaces/{workspace.id}",
            {"profile": {"notes": "Super secret client roster."}},
            format="json",
        )
        assert response.status_code == 200
        assert AuditLog.objects.count() == before + 1
        entry = AuditLog.objects.latest("created_at")
        assert entry.action == "workspace.profile_updated"
        assert entry.actor_id == owner.id
        assert entry.workspace_id == workspace.id
        assert str(workspace.id) == entry.resource_id
        blob = str(entry.metadata).lower()
        assert "secret" not in blob
        assert "roster" not in blob

    def test_denied_patch_records_no_audit(self, workspace, web_client):
        member = user_with_role(workspace, WorkspaceMember.Role.MEMBER, "m@test.dev")
        before = AuditLog.objects.count()
        response = web_client(member).patch(
            f"/api/v1/workspaces/{workspace.id}",
            {"profile": VALID_PROFILE},
            format="json",
        )
        assert response.status_code == 403
        assert AuditLog.objects.count() == before

    def test_invalid_profile_records_no_audit(self, workspace, owner, web_client):
        before = AuditLog.objects.count()
        response = web_client(owner).patch(
            f"/api/v1/workspaces/{workspace.id}",
            {"profile": {"bogus": "x"}},
            format="json",
        )
        assert response.status_code == 400
        assert AuditLog.objects.count() == before


class TestCrossWorkspaceIsolation:
    """A user cannot retrieve/patch a workspace they are not a member of --
    404 via scoping, mirroring `test_scoping.py`."""

    def test_cannot_retrieve_other_users_workspace(self, owner, outsider, web_client):
        outsider_ws = Workspace.objects.get(owner=outsider)
        response = web_client(owner).get(f"/api/v1/workspaces/{outsider_ws.id}")
        assert response.status_code == 404

    def test_cannot_patch_other_users_workspace(self, owner, outsider, web_client):
        outsider_ws = Workspace.objects.get(owner=outsider)
        response = web_client(owner).patch(
            f"/api/v1/workspaces/{outsider_ws.id}",
            {"profile": VALID_PROFILE},
            format="json",
        )
        assert response.status_code == 404
        outsider_ws.refresh_from_db()
        assert outsider_ws.profile == {}
