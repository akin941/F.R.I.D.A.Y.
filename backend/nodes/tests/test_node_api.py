"""The owner's node APIs (Phase 9 brief D11, branch 4): `GET /api/v1/nodes`,
`GET /api/v1/nodes/<id>`, `POST /api/v1/nodes/<id>/revoke`. Role x
endpoint over the five Phase 4 principals plus a `hook` PAT and a node
secret; another user's node is a 404; nothing in a body is a path or
content."""

import json
from datetime import timedelta

import pytest
from django.utils import timezone

from accounts import devices
from accounts.models import TrustedDevice
from audit.models import AuditLog
from nodes import bus, selectors
from nodes.protocol.messages import catalogue_sha256
from nodes.reference.node import default_catalogue
from nodes.services import record_capability_snapshot
from nodes.tests.conftest import PRINCIPALS, principal_client
from nodes.tests.gateway_helpers import direct_node, other_user

pytestmark = pytest.mark.django_db

LIST = "/api/v1/nodes"


def detail(node):
    return f"/api/v1/nodes/{node.pk}"


def revoke(node):
    return f"/api/v1/nodes/{node.pk}/revoke"


@pytest.fixture
def node(make_node, cleanup_nodes):
    result = make_node()
    cleanup_nodes.append(str(result.node.pk))
    catalogue = [e for e in default_catalogue() if e["class"] == "read"]
    catalogue.append(dict(catalogue[0], name="foo.bar"))
    record_capability_snapshot(
        node=result.node,
        catalogue=catalogue,
        catalogue_sha256=catalogue_sha256(catalogue),
        protocol_version=1,
    )
    return result.node


@pytest.mark.parametrize("principal", PRINCIPALS)
def test_role_by_endpoint(principal, owner, personal, node, web_client):
    client = principal_client(principal, owner, personal, web_client)
    responses = {
        "list": client.get(LIST),
        "detail": client.get(detail(node)),
        "revoke": client.post(revoke(node)),
    }
    if principal == "owner-web":
        assert {k: r.status_code for k, r in responses.items()} == {
            "list": 200,
            "detail": 200,
            "revoke": 200,
        }
        return
    expected = 401 if principal == "anonymous" else None
    for name, response in responses.items():
        if expected is not None:
            assert response.status_code == expected, name
        else:
            assert response.status_code in (401, 403), (name, response.status_code)
        assert str(node.pk) not in response.content.decode(), name
    node.device.refresh_from_db()
    assert node.device.revoked_at is None  # nobody else could revoke it


def test_the_list_shows_status_counts_and_no_content(owner, node, web_client):
    response = web_client(owner).get(LIST)
    assert response.status_code == 200
    (item,) = response.json()
    assert item["id"] == str(node.pk)
    assert item["status"] == "disconnected"
    assert item["advertised_count"] == 11 and item["invocable_count"] == 10
    assert len(item["catalogue_sha256"]) == 12
    assert item["credential_age_days"] == 0
    body = json.dumps(response.json())
    assert "args_schema" not in body and "/Users" not in body and "~/" not in body


def test_the_detail_lists_capabilities_with_reasons_and_no_toggle(owner, node, web_client):
    response = web_client(owner).get(detail(node))
    assert response.status_code == 200
    capabilities = {entry["name"]: entry for entry in response.json()["capabilities"]}
    assert capabilities["foo.bar"] == {
        "name": "foo.bar",
        "version": capabilities["foo.bar"]["version"],
        "class": "read",
        "availability": "granted",
        "invocable": False,
        "reason": "unknown_capability",
    }
    assert capabilities["apps.frontmost"]["invocable"] is True
    assert "args_schema" not in json.dumps(response.json())


def test_status_connected_follows_presence(owner, node, web_client):
    bus.mark_present(node.pk)
    assert web_client(owner).get(detail(node)).json()["status"] == "connected"
    bus.clear_presence(node.pk)
    assert web_client(owner).get(detail(node)).json()["status"] == "disconnected"


def test_status_kill_switch_revoked_and_expiring(owner, node, make_node, cleanup_nodes):
    assert selectors.node_status(node, online=False) == "disconnected"
    TrustedDevice.objects.filter(pk=node.device_id).update(
        secret_issued_at=timezone.now() - timedelta(days=31)
    )
    node.device.refresh_from_db()
    assert selectors.node_status(node, online=True) == "credential_expiring"
    devices.engage_node_kill_switch(owner=owner, via="test")
    assert selectors.node_status(node, online=True) == "kill_switch"
    devices.release_node_kill_switch(owner=owner, via="test")
    devices.revoke_device(device=node.device, actor=owner)
    node.device.refresh_from_db()
    assert selectors.node_status(node, online=True) == "revoked"


def test_node_health_blocks(owner, node):
    other = other_user()
    never = selectors.node_health(other)
    assert never["status"] == "never_paired" and set(never) == {"status", "voice"}
    assert never["voice"]["state"] == "unknown"
    health = selectors.node_health(owner)
    assert health["status"] == "disconnected" and health["node_id"] == str(node.pk)
    assert health["advertised_count"] == 11
    bus.mark_present(node.pk)
    assert selectors.node_health(owner)["status"] == "connected"
    bus.clear_presence(node.pk)


def test_another_users_node_is_a_404(owner, node, web_client, cleanup_nodes):
    theirs, _ = direct_node(other_user())
    cleanup_nodes.append(str(theirs.pk))
    client = web_client(owner)
    assert client.get(detail(theirs)).status_code == 404
    assert client.post(revoke(theirs)).status_code == 404
    assert [item["id"] for item in client.get(LIST).json()] == [str(node.pk)]
    theirs.device.refresh_from_db()
    assert theirs.device.revoked_at is None
    unknown = type(theirs)(pk="9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d")
    assert client.get(detail(unknown)).status_code == 404


def test_revoke_delegates_to_revoke_device_and_publishes(
    owner, node, web_client, fake_revocation_redis, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        response = web_client(owner).post(revoke(node))
    assert response.status_code == 200
    assert response.json()["status"] == "revoked"
    node.device.refresh_from_db()
    assert node.device.revoked_at is not None
    assert AuditLog.objects.filter(action="accounts.device.revoked").count() == 1
    (channel, payload), *_ = fake_revocation_redis.published
    assert json.loads(payload) == {"device_id": str(node.device_id), "user_id": str(owner.pk)}
    # Idempotent: a second revoke changes nothing.
    assert web_client(owner).post(revoke(node)).status_code == 200
    assert AuditLog.objects.filter(action="accounts.device.revoked").count() == 1
