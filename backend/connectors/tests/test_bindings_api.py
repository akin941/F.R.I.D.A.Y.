"""`/api/v1/connectors/bindings` (brief D3): owner, web session only --
role x endpoint against the five Phase 4 principals."""

import secrets

import pytest
from rest_framework.test import APIClient

from accounts.authentication import TOKEN_PREFIX, hash_token
from accounts.models import PersonalAccessToken, User
from accounts.services import register_user
from audit.models import AuditLog
from connectors.models import ConnectorBinding

pytestmark = pytest.mark.django_db

LIST = "/api/v1/connectors/bindings"


def _pat_client(owner, workspace, kind):
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    PersonalAccessToken.objects.create(
        workspace=workspace, user=owner, name=kind, kind=kind, token_hash=hash_token(raw)
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return client


def _client(principal, owner, workspace, web_client):
    if principal == "owner-web":
        return web_client(owner)
    if principal == "owner-pat-cli":
        return _pat_client(owner, workspace, PersonalAccessToken.Kind.CLI)
    if principal == "owner-pat-openai":
        return _pat_client(owner, workspace, PersonalAccessToken.Kind.OPENAI)
    if principal == "non-owner":
        return web_client(register_user(email="member@test.dev", password="Str0ngDevPass!42"))
    if principal == "agent":
        return web_client(User.objects.create_user(email="agent@test.dev", is_agent=True))
    return APIClient()


PRINCIPALS = [
    ("owner-web", 200),
    ("owner-pat-cli", 401),
    ("owner-pat-openai", 401),
    ("non-owner", 403),
    ("agent", 403),
    ("anonymous", 401),
]


@pytest.mark.parametrize("principal, expected", PRINCIPALS)
def test_list_role_matrix(fake_manifest, owner, workspace, web_client, principal, expected):
    client = _client(principal, owner, workspace, web_client)
    response = client.get(LIST, {"workspace": str(workspace.id)})
    assert response.status_code == expected


@pytest.mark.parametrize("principal, expected", PRINCIPALS)
def test_toggle_role_matrix(
    fake_manifest, owner, workspace, web_client, server, principal, expected, committed
):
    client = _client(principal, owner, workspace, web_client)
    with committed():
        response = client.put(
            f"{LIST}/fake", {"workspace": str(workspace.id), "enabled": True}, format="json"
        )
    assert response.status_code == expected
    enabled = ConnectorBinding.objects.filter(connector="fake", enabled=True).exists()
    assert enabled == (expected == 200)


def test_owner_lists_and_toggles(fake_manifest, owner, workspace, web_client, server, committed):
    client = web_client(owner)
    rows = client.get(LIST, {"workspace": str(workspace.id)}).json()
    fake = next(row for row in rows if row["connector"] == "fake")
    assert fake["enabled"] is False and fake["bindable"] is True
    assert {t["name"]: t["pin_status"] for t in fake["tools"]} == {
        "fake_search": "unchecked",
        "fake_create_note": "unchecked",
    }
    # The external placeholder is for company workspaces only.
    assert "external_installation" not in [row["connector"] for row in rows]
    with committed():
        body = client.put(
            f"{LIST}/fake", {"workspace": str(workspace.id), "enabled": True}, format="json"
        ).json()
    assert body["enabled"] is True
    # Nothing is callable at enable time; the pin check runs after commit.
    assert {t["pin_status"] for t in body["tools"]} == {"unchecked"}
    rows = client.get(LIST, {"workspace": str(workspace.id)}).json()
    body = next(row for row in rows if row["connector"] == "fake")
    assert {t["name"]: t["pin_status"] for t in body["tools"]} == {
        "fake_search": "ok",
        "fake_create_note": "ok",
    }
    assert body["health"]["protocol_version"] == "2026-07-28"
    assert "SERVER-DESCRIPTION" not in str(body)
    body = client.put(
        f"{LIST}/fake", {"workspace": str(workspace.id), "enabled": False}, format="json"
    ).json()
    assert body["enabled"] is False
    assert AuditLog.objects.filter(action="connector.binding.disabled").count() == 1


def test_blocked_external_installation_is_listed_but_unbindable(owner, firm, web_client):
    client = web_client(owner)
    rows = client.get(LIST, {"workspace": str(firm.id)}).json()
    external_installation = next(row for row in rows if row["connector"] == "external_installation")
    assert external_installation["status"] == "blocked" and external_installation["bindable"] is False
    response = client.put(
        f"{LIST}/external_installation", {"workspace": str(firm.id), "enabled": True}, format="json"
    )
    assert response.status_code == 400
    assert response.json()["code"] == "connector.invalid_transition"
    assert not ConnectorBinding.objects.exists()


def test_unknown_connector_or_foreign_workspace_is_404(fake_manifest, owner, web_client):
    stranger = register_user(email="stranger@test.dev", password="Str0ngDevPass!42")
    from workspaces.models import Workspace

    foreign = Workspace.objects.get(owner=stranger)
    client = web_client(owner)
    assert client.get(LIST, {"workspace": str(foreign.id)}).status_code == 404
    response = client.put(
        f"{LIST}/fake", {"workspace": str(foreign.id), "enabled": True}, format="json"
    )
    assert response.status_code == 404
    assert (
        client.put(
            f"{LIST}/nope", {"workspace": str(foreign.id), "enabled": True}, format="json"
        ).status_code
        == 404
    )
