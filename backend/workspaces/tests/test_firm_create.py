"""Owner-only firm workspace creation (Phase 4 branch 2): `POST
/api/v1/workspaces`, role x endpoint against the five principals, audit,
isolation, and the `name` rename on PATCH."""

import secrets

import pytest
from rest_framework.test import APIClient

from accounts.authentication import TOKEN_PREFIX, hash_token
from accounts.models import PersonalAccessToken, User
from accounts.services import register_user
from audit.models import AuditLog
from core.channels import CHANNEL_CLI, CHANNEL_WEB
from workspaces.models import Workspace, WorkspaceMember
from workspaces.services import WorkspacePermissionDenied, create_firm_workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"
URL = "/api/v1/workspaces"


@pytest.fixture
def owner():
    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


def _client(principal, owner, workspace, web_client):
    if principal == "owner-web":
        return web_client(owner)
    if principal == "owner-pat":
        raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
        PersonalAccessToken.objects.create(
            workspace=workspace, user=owner, name="cli", token_hash=hash_token(raw)
        )
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        return client
    if principal == "non-owner":
        return web_client(register_user(email="member@test.dev", password=PASSWORD))
    if principal == "agent":
        return web_client(User.objects.create_user(email="agent@test.dev", is_agent=True))
    return APIClient()


@pytest.mark.parametrize(
    "principal, expected",
    [
        ("owner-web", 201),
        ("owner-pat", 401),
        ("non-owner", 403),
        ("agent", 403),
        ("anonymous", 401),
    ],
)
def test_firm_create_role_matrix(owner, workspace, web_client, principal, expected):
    client = _client(principal, owner, workspace, web_client)
    before = Workspace.objects.count()
    response = client.post(URL, {"name": "Acme A.Ş."}, format="json")
    assert response.status_code == expected
    assert Workspace.objects.count() == before + (1 if expected == 201 else 0)


def test_owner_creates_a_company_workspace_with_a_profile(owner, web_client):
    profile = {"notes": "Muhasebe müşterisi", "repos": [{"name": "site", "url": "https://x"}]}
    response = web_client(owner).post(
        URL, {"name": "  Acme  ", "profile": profile, "type": "personal"}, format="json"
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Acme"
    assert body["type"] == "company"  # never client-chosen
    assert body["role"] == "owner"
    assert body["profile"] == profile
    firm = Workspace.objects.get(pk=body["id"])
    assert list(firm.members.values_list("user_id", "role")) == [(owner.pk, "owner")]
    log = AuditLog.objects.get(action="workspace.created", workspace=firm)
    assert log.metadata == {"type": "company"}
    assert log.actor == owner


def test_firm_create_adds_no_other_membership(owner, web_client):
    stranger = register_user(email="stranger@test.dev", password=PASSWORD)
    before = WorkspaceMember.objects.filter(user=stranger).count()
    web_client(owner).post(URL, {"name": "Acme"}, format="json")
    assert WorkspaceMember.objects.filter(user=stranger).count() == before
    listing = web_client(stranger).get(URL).json()
    assert "Acme" not in [w["name"] for w in listing]


@pytest.mark.parametrize("body", [{}, {"name": "   "}, {"name": "x", "profile": {"bogus": 1}}])
def test_invalid_firm_create_is_400(owner, web_client, body):
    response = web_client(owner).post(URL, body, format="json")
    assert response.status_code == 400
    assert not Workspace.objects.filter(type=Workspace.Type.COMPANY).exists()


def test_service_requires_the_web_channel(owner):
    with pytest.raises(WorkspacePermissionDenied):
        create_firm_workspace(actor=owner, name="Acme", channel=CHANNEL_CLI)
    create_firm_workspace(actor=owner, name="Acme", channel=CHANNEL_WEB)


def test_patch_renames_and_audits(owner, workspace, web_client):
    response = web_client(owner).patch(
        f"/api/v1/workspaces/{workspace.id}", {"name": "Kişisel"}, format="json"
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Kişisel"
    assert AuditLog.objects.filter(action="workspace.renamed", workspace=workspace).exists()


def test_patch_with_nothing_writable_is_400(owner, workspace, web_client):
    response = web_client(owner).patch(
        f"/api/v1/workspaces/{workspace.id}", {"type": "company"}, format="json"
    )
    assert response.status_code == 400


def test_viewer_cannot_rename(owner, workspace, web_client):
    viewer = register_user(email="viewer@test.dev", password=PASSWORD)
    WorkspaceMember.objects.create(
        workspace=workspace, user=viewer, role=WorkspaceMember.Role.VIEWER
    )
    response = web_client(viewer).patch(
        f"/api/v1/workspaces/{workspace.id}", {"name": "Hijacked"}, format="json"
    )
    assert response.status_code == 403
    workspace.refresh_from_db()
    assert workspace.name != "Hijacked"
