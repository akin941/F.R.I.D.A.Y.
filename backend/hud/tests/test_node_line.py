"""The HUD health panel's node line (Phase 9 brief D11, branch 7):
`health.node` in `/hud/events` for every status (`connected`,
`disconnected`, `kill_switch`, `revoked`, `credential_expiring`,
`never_paired`), no path, capability name or content in it, another user's
node never shown, and a delta when presence flips. Presence is the real
Redis key (`nodes.bus`), cleared after each test; no node process runs."""

import json
from datetime import timedelta

import pytest
from django.utils import timezone

from accounts import devices
from accounts.models import TrustedDevice
from hud.services import node_section
from hud.tests.conftest import PASSWORD
from nodes import bus
from nodes.protocol.messages import catalogue_sha256
from nodes.reference.node import default_catalogue
from nodes.services import record_capability_snapshot
from nodes.tests.gateway_helpers import direct_node
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

NODE_KEYS = {
    "status",
    "last_heartbeat_at",
    "catalogue_sha256",
    "invocable_count",
    "advertised_count",
    "spool_depth",
    "node_version",
}


@pytest.fixture
def node_ids():
    ids = []
    yield ids
    for node_id in ids:
        try:
            bus.clear_presence(node_id)
        except bus.BusUnavailable:
            pass


@pytest.fixture
def node(owner, node_ids):
    node, _ = direct_node(owner, name="MacBook")
    node_ids.append(node.pk)
    catalogue = [entry for entry in default_catalogue() if entry["class"] == "read"]
    record_capability_snapshot(
        node=node,
        catalogue=catalogue,
        catalogue_sha256=catalogue_sha256(catalogue),
        protocol_version=1,
    )
    node.node_version = "0.1.0"
    node.spool_count = 3
    node.last_heartbeat_at = timezone.now() - timedelta(seconds=20)
    node.save(update_fields=["node_version", "spool_count", "last_heartbeat_at"])
    return node


def _health(stream, workspace_id):
    frames = stream(workspace_id)
    return frames[1]["health"]


def test_never_paired(stream, workspace):
    assert _health(stream, workspace.id)["node"] == {
        "status": "never_paired",
        "last_heartbeat_at": None,
        "catalogue_sha256": "",
        "invocable_count": 0,
        "advertised_count": 0,
        "spool_depth": 0,
        "node_version": "",
    }


def test_disconnected_block_has_counts_and_no_content(stream, workspace, node):
    block = _health(stream, workspace.id)["node"]
    assert set(block) == NODE_KEYS
    assert block["status"] == "disconnected"
    assert block["advertised_count"] == block["invocable_count"] > 0
    assert len(block["catalogue_sha256"]) == 12
    assert block["spool_depth"] == 3 and block["node_version"] == "0.1.0"
    assert block["last_heartbeat_at"] is not None
    body = json.dumps(block)
    for forbidden in ("~/", "/Users", "args_schema", "sessions.list", "claude.", str(node.pk)):
        assert forbidden not in body, forbidden


def test_connected_follows_presence(stream, workspace, node):
    bus.mark_present(node.pk)
    assert _health(stream, workspace.id)["node"]["status"] == "connected"


def test_kill_switch(stream, workspace, owner, node):
    bus.mark_present(node.pk)
    devices.engage_node_kill_switch(owner=owner, via="test")
    assert _health(stream, workspace.id)["node"]["status"] == "kill_switch"
    devices.release_node_kill_switch(owner=owner, via="test")
    assert _health(stream, workspace.id)["node"]["status"] == "connected"


def test_revoked(stream, workspace, owner, node):
    devices.revoke_device(device=node.device, actor=owner)
    assert _health(stream, workspace.id)["node"]["status"] == "revoked"


def test_credential_expiring(stream, workspace, node):
    TrustedDevice.objects.filter(pk=node.device_id).update(
        secret_issued_at=timezone.now() - timedelta(days=31)
    )
    assert _health(stream, workspace.id)["node"]["status"] == "credential_expiring"


def test_a_connected_node_wins_over_a_revoked_newer_one(stream, workspace, owner, node, node_ids):
    newer, _ = direct_node(owner, name="Eski")
    node_ids.append(newer.pk)
    devices.revoke_device(device=newer.device, actor=owner)
    bus.mark_present(node.pk)
    assert _health(stream, workspace.id)["node"]["status"] == "connected"


def test_another_users_node_never_shows(stream, workspace, node_ids):
    from accounts.services import register_user

    stranger = register_user(email="stranger@test.dev", password=PASSWORD)
    theirs, _ = direct_node(stranger, name="Onların")
    node_ids.append(theirs.pk)
    bus.mark_present(theirs.pk)
    assert _health(stream, workspace.id)["node"]["status"] == "never_paired"
    # Theirs exists (a non-owner's node reads as revoked: one owner per
    # installation), but only their own line could ever show it.
    assert node_section(stranger)["status"] == "revoked"


def test_the_owners_node_line_shows_in_a_firm_stream_too(stream, owner, node):
    firm = Workspace.objects.create(name="Firma", owner=owner, type="company")
    WorkspaceMember.objects.create(workspace=firm, user=owner, role="owner")
    assert _health(stream, firm.id)["node"]["status"] == "disconnected"


def test_the_section_changes_when_presence_flips(stream, workspace, node, fake_clock):
    fake_clock.ticks = [lambda: bus.mark_present(node.pk), lambda: bus.clear_presence(node.pk)]
    frames = stream(workspace.id)
    assert frames[1]["health"]["node"]["status"] == "disconnected"
    up, down = frames[2], frames[3]
    assert up["type"] == "delta" and set(up) == {"type", "health"}
    assert up["health"]["node"]["status"] == "connected"
    assert down["type"] == "delta" and down["health"]["node"]["status"] == "disconnected"


def test_the_stream_writes_no_node_row(stream, workspace, node):
    before = (node.__class__.objects.get(pk=node.pk).updated_at, TrustedDevice.objects.count())
    stream(workspace.id)
    after = (node.__class__.objects.get(pk=node.pk).updated_at, TrustedDevice.objects.count())
    assert before == after
