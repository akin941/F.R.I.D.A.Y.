import secrets

import pytest
from django.db import connections
from rest_framework.test import APIClient

from accounts import devices
from accounts.authentication import TOKEN_PREFIX, hash_token
from accounts.models import DevicePairingCode, PersonalAccessToken, TrustedDevice, User
from accounts.services import register_user
from core import revocation
from nodes import bus
from nodes.gateway import NodeSocketApp
from nodes.tests.gateway_harness import Harness, reference_node
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


# Captured at import (collection), before any fixture patches it.
_REAL_REVOCATION_CLIENT = revocation._client


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


# --- the gateway tests (Phase 9 branch 4; `gateway_harness`) ---


@pytest.fixture
def real_revocation(fake_revocation_redis, monkeypatch):
    """Replace the package conftest's fake publisher with the real one:
    these tests need the real `friday:device-revoked` pub/sub."""
    monkeypatch.setattr(revocation, "_client", _REAL_REVOCATION_CLIENT)
    return revocation


@pytest.fixture
def make_app():
    harnesses = []

    def make(**kwargs):
        kwargs.setdefault("tick_seconds", 0.05)
        app = NodeSocketApp(**kwargs)
        harness = Harness(app)
        harnesses.append(harness)
        return app, harness

    yield make
    for harness in harnesses:
        harness.close()
    connections.close_all()


@pytest.fixture
def gateway(make_app, real_revocation):
    app, harness = make_app()
    return app, harness


@pytest.fixture
def cleanup_nodes():
    """Collects node ids whose Redis keys must not outlive the test."""
    ids = []
    yield ids
    for node_id in ids:
        for clear in (bus.clear_presence, bus.clear_inbox, bus.clear_inflight):
            try:
                clear(node_id)
            except bus.BusUnavailable:
                pass


@pytest.fixture
def paired(make_node, cleanup_nodes):
    """A node paired through the service: (node, secret, keyring)."""

    def make(name="MacBook"):
        result = make_node(name)
        cleanup_nodes.append(str(result.node.pk))
        return result.node, result.node_secret, result.keyring

    return make


@pytest.fixture
def link_node(gateway, paired):
    """Pair a node and connect a reference node to the gateway over the
    given subprotocols; returns (link, node_row, reference)."""
    app, harness = gateway

    def make(*, subprotocols=None, name="MacBook", **node_kwargs):
        node_row, secret, keyring = paired(name)
        reference = reference_node(node_row, keyring, **node_kwargs)
        kwargs = {} if subprotocols is None else {"subprotocols": subprotocols}
        link = harness.connect(secret=secret, node=reference, **kwargs)
        return link, node_row, reference

    return make
