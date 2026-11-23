"""Fixtures shared by the gateway tests (`test_gateway_*.py`). Imported by
those modules with `from nodes.tests.gateway_fixtures import *`-style
explicit imports, so pytest registers them."""

import secrets

import pytest
from django.db import connections

from accounts.authentication import hash_token
from accounts.models import TrustedDevice
from accounts.services import register_user
from core import revocation
from nodes import bus
from nodes.gateway import NodeSocketApp
from nodes.keys import signing_keyring
from nodes.models import Node
from nodes.tests.conftest import PASSWORD
from nodes.tests.gateway_harness import Harness, reference_node
from workspaces.models import Workspace

# Captured at import (collection), before any fixture patches it.
_REAL_REVOCATION_CLIENT = revocation._client


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


def personal_of(user):
    return Workspace.objects.get(owner=user, type=Workspace.Type.PERSONAL)


def direct_node(user, name="Direct", **device_fields):
    """A node row created directly (not through pairing): returns (node,
    raw_secret)."""
    raw = "jnd_" + secrets.token_urlsafe(32)
    device = TrustedDevice.objects.create(
        user=user,
        name=name,
        kind=TrustedDevice.Kind.NODE,
        secret_hash=hash_token(raw),
        enrolled_via=TrustedDevice.EnrolledVia.values[0],
        **device_fields,
    )
    node = Node.objects.create(
        id=device.pk,
        device=device,
        owner=user,
        workspace=personal_of(user),
        name=name,
        protocol_version=1,
        pinned_key_id=signing_keyring().key_id,
    )
    return node, raw


def other_user(email="other@test.dev"):
    return register_user(email=email, password=PASSWORD, name="Other")
