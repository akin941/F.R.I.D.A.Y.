import secrets

import pytest
from rest_framework.test import APIClient

from accounts import devices
from accounts.authentication import TOKEN_PREFIX, hash_token
from accounts.models import DevicePairingCode, PersonalAccessToken, TrustedDevice, User
from accounts.services import register_user
from core import revocation
from workspaces.models import Workspace, WorkspaceMember

PASSWORD = "Str0ngDevPass!42"


class FakeRedis:
    """Stands in for the revocation publisher (no real Redis in tests)."""

    def __init__(self):
        self.published = []

    def publish(self, channel, payload):
        self.published.append((channel, payload))

    def close(self):
        pass


@pytest.fixture(autouse=True)
def fake_revocation_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(revocation, "_client", lambda: fake)
    return fake


@pytest.fixture
def owner(db):
    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def personal(owner):
    return Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)


@pytest.fixture
def firm(owner):
    workspace = Workspace.objects.create(name="Acme", type=Workspace.Type.COMPANY, owner=owner)
    WorkspaceMember.objects.create(workspace=workspace, user=owner, role=WorkspaceMember.Role.OWNER)
    return workspace


def node_code(owner, kind=DevicePairingCode.Kind.STRONG, device_kind=TrustedDevice.Kind.NODE):
    raw, _ = devices.issue_pairing_code(
        owner=owner,
        kind=kind,
        issued_via=DevicePairingCode.IssuedVia.MANAGEMENT_COMMAND,
        device_kind=device_kind,
    )
    return raw


@pytest.fixture
def make_node(owner):
    """Pair a node through the service; returns the PairingResult."""
    from nodes.services import pair_node

    def make(name="MacBook"):
        return pair_node(raw_code=node_code(owner), node_name=name, protocol_versions=[1])

    return make


def bearer(raw):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return client


def principal_client(kind, owner, workspace, web_client):
    """The five Phase 4 principals plus a `hook` PAT and a node secret."""
    if kind == "owner-web":
        return web_client(owner)
    if kind in ("owner-cli", "owner-openai", "owner-hook"):
        raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
        PersonalAccessToken.objects.create(
            workspace=workspace,
            user=owner,
            name=kind,
            kind=kind.removeprefix("owner-"),
            token_hash=hash_token(raw),
        )
        return bearer(raw)
    if kind == "node":
        from nodes.services import pair_node

        result = pair_node(raw_code=node_code(owner), node_name="other", protocol_versions=[1])
        return bearer(result.node_secret)
    if kind == "non-owner":
        return web_client(register_user(email="member@test.dev", password=PASSWORD))
    if kind == "agent":
        return web_client(User.objects.create_user(email="agent@test.dev", is_agent=True))
    return APIClient()


PRINCIPALS = [
    "owner-web",
    "owner-cli",
    "owner-openai",
    "owner-hook",
    "node",
    "non-owner",
    "agent",
    "anonymous",
]
