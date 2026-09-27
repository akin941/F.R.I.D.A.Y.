"""PersonalAccessToken model, authentication, mint/list/revoke endpoints,
and cross-app allowlist containment (MVP 9 §1)."""

import pytest
from rest_framework.test import APIClient

from accounts.authentication import hash_token
from accounts.models import PersonalAccessToken
from accounts.services import register_user
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


@pytest.fixture
def other_member(workspace):
    user = register_user(email="member@test.dev", password=PASSWORD, name="Member")
    WorkspaceMember.objects.create(workspace=workspace, user=user, role=WorkspaceMember.Role.MEMBER)
    return user


def pat_client(raw_token):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw_token}")
    return client


def mint(web_client, user, workspace, name="laptop cli"):
    response = web_client(user).post(
        "/api/v1/auth/tokens", {"workspace": str(workspace.id), "name": name}, format="json"
    )
    assert response.status_code == 201, response.data
    return response.data


# --- mint/list/revoke endpoints ---


def test_mint_returns_raw_token_exactly_once(owner, workspace, web_client):
    data = mint(web_client, owner, workspace)
    assert data["token"].startswith("jpat_")
    assert data["name"] == "laptop cli"
    assert str(data["workspace"]) == str(workspace.id)

    listing = web_client(owner).get("/api/v1/auth/tokens").data
    assert len(listing) == 1
    assert "token" not in listing[0]
    assert "token_hash" not in listing[0]


def test_mint_requires_workspace_membership(owner, web_client):
    stranger = register_user(email="stranger@test.dev", password=PASSWORD, name="Stranger")
    other_workspace = Workspace.objects.get(owner=stranger)
    response = web_client(owner).post(
        "/api/v1/auth/tokens",
        {"workspace": str(other_workspace.id), "name": "x"},
        format="json",
    )
    assert response.status_code == 404
    assert not PersonalAccessToken.objects.exists()


def test_list_only_shows_own_tokens_even_in_a_shared_workspace(
    owner, workspace, other_member, web_client
):
    mint(web_client, owner, workspace, name="owner token")
    mint(web_client, other_member, workspace, name="member token")

    owner_tokens = web_client(owner).get("/api/v1/auth/tokens").data
    member_tokens = web_client(other_member).get("/api/v1/auth/tokens").data
    assert [t["name"] for t in owner_tokens] == ["owner token"]
    assert [t["name"] for t in member_tokens] == ["member token"]


def test_revoke_is_idempotent_and_marks_revoked_at(owner, workspace, web_client):
    data = mint(web_client, owner, workspace)
    response = web_client(owner).post(f"/api/v1/auth/tokens/{data['id']}/revoke")
    assert response.status_code == 200
    assert response.data["revoked_at"] is not None

    # Idempotent: revoking again does not error or overwrite the timestamp.
    first_revoked_at = response.data["revoked_at"]
    response2 = web_client(owner).post(f"/api/v1/auth/tokens/{data['id']}/revoke")
    assert response2.status_code == 200
    assert response2.data["revoked_at"] == first_revoked_at


def test_cannot_revoke_another_users_token(owner, workspace, other_member, web_client):
    data = mint(web_client, owner, workspace)
    response = web_client(other_member).post(f"/api/v1/auth/tokens/{data['id']}/revoke")
    assert response.status_code == 404
    token = PersonalAccessToken.objects.get(id=data["id"])
    assert token.revoked_at is None


def test_revoked_token_can_no_longer_authenticate(owner, workspace, web_client):
    data = mint(web_client, owner, workspace)
    web_client(owner).post(f"/api/v1/auth/tokens/{data['id']}/revoke")
    response = pat_client(data["token"]).get(f"/api/v1/ai/conversations?workspace={workspace.id}")
    assert response.status_code == 401


# --- authentication behavior ---


def test_valid_token_authenticates_and_stamps_last_used_at(owner, workspace, web_client):
    data = mint(web_client, owner, workspace)
    token = PersonalAccessToken.objects.get(id=data["id"])
    assert token.last_used_at is None

    response = pat_client(data["token"]).get(f"/api/v1/ai/conversations?workspace={workspace.id}")
    assert response.status_code == 200

    token.refresh_from_db()
    assert token.last_used_at is not None


def test_unknown_token_is_rejected(owner, workspace):
    response = pat_client("jpat_" + "x" * 32).get(
        f"/api/v1/ai/conversations?workspace={workspace.id}"
    )
    assert response.status_code == 401


def test_malformed_bearer_value_falls_through_and_is_rejected(owner, workspace):
    # Not jpat_-prefixed at all -- PersonalAccessTokenAuthentication returns
    # None immediately and JWTAuthentication (next in line) also rejects it,
    # proving the fall-through/ordering actually works end-to-end, not just
    # on paper.
    response = pat_client("not-a-real-token").get(
        f"/api/v1/ai/conversations?workspace={workspace.id}"
    )
    assert response.status_code == 401


def test_deactivated_users_still_unrevoked_token_is_rejected(owner, workspace, web_client):
    data = mint(web_client, owner, workspace)
    owner.is_active = False
    owner.save(update_fields=["is_active"])

    response = pat_client(data["token"]).get(f"/api/v1/ai/conversations?workspace={workspace.id}")
    assert response.status_code == 401

    token = PersonalAccessToken.objects.get(id=data["id"])
    assert token.revoked_at is None  # rejected via is_active check, not revocation


def test_raw_token_is_never_persisted_only_its_hash(owner, workspace, web_client):
    data = mint(web_client, owner, workspace)
    token = PersonalAccessToken.objects.get(id=data["id"])
    assert token.token_hash == hash_token(data["token"])
    assert data["token"] not in token.token_hash


# --- PAT allowlist containment (round-1 P1 fix) ---


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/tasks",
        "/api/v1/memories",
        "/api/v1/knowledge/documents",
        "/api/v1/workspaces",
        "/api/v1/auth/me",
    ],
)
def test_pat_is_rejected_on_non_allowlisted_endpoints(owner, workspace, path, web_client):
    """A valid PAT presented against an app this MVP never allowlisted is
    rejected exactly as a missing/absent credential would be -- proves the
    explicit per-view allowlist actually contains the credential rather
    than just being described as doing so (round-1 P1 fix)."""
    data = mint(web_client, owner, workspace)
    response = pat_client(data["token"]).get(path)
    assert response.status_code == 401
