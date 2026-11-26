"""Phase 13 D4: `nodes.selectors.node_presence` -- one entry per
non-revoked node of the owner, connected first, then room order, then
name; at most `NODE_PRESENCE_MAX`; Redis down reads every node offline;
a stale voice state reads `unknown`; a key allowlist (no path, capability
name, argument or output). Presence is the real Redis key (`nodes.bus`),
cleared after each test; no node process runs."""

import json
from datetime import timedelta

import pytest
from django.utils import timezone

from accounts import devices
from accounts.models import TrustedDevice
from core.channels import CHANNEL_WEB
from nodes import bus, presence
from nodes.models import Node
from nodes.protocol.messages import VOICE_STATE_STALE_SECONDS
from nodes.selectors import (
    NODE_PRESENCE_MAX,
    PRESENCE_KEYS,
    node_presence,
    node_presence_page,
    node_summary,
)
from nodes.tests.gateway_helpers import other_user
from nodes.tests.two_nodes import labelled_node, make_two_nodes
from workspaces import rooms

pytestmark = pytest.mark.django_db


@pytest.fixture
def cleanup():
    ids = []
    yield ids
    for node_id in ids:
        try:
            bus.clear_presence(node_id)
        except bus.BusUnavailable:
            pass


@pytest.fixture
def two_nodes(owner, cleanup):
    return make_two_nodes(owner, cleanup)


def _names(entries):
    return [entry["name"] for entry in entries]


def test_one_entry_per_node_with_exactly_the_allowed_keys(two_nodes, owner):
    entries = node_presence(owner)
    assert len(entries) == 2
    for entry in entries:
        assert tuple(entry) == PRESENCE_KEYS
        assert set(entry["room"]) == {"id", "name"}
    by_name = {entry["name"]: entry for entry in entries}
    mac, desk = by_name["MacBook"], by_name["Masaüstü"]
    assert (mac["platform"], mac["role"]) == ("macos", "laptop")
    assert (desk["platform"], desk["role"]) == ("linux", "workstation")
    assert mac["room"]["name"] == desk["room"]["name"] == "Çalışma odası"
    # The v2 MacBook can act; the v1 Linux desktop only reads (D9).
    assert mac["invocable_read_count"] > 0 and mac["invocable_action_count"] > 0
    assert desk["invocable_read_count"] == mac["invocable_read_count"]
    assert desk["invocable_action_count"] == 0


def test_no_path_capability_name_or_argument_in_the_payload(two_nodes, owner):
    body = json.dumps(node_presence(owner), default=str)
    for forbidden in (
        "~/",
        "/Users",
        "args_schema",
        "result_schema",
        "sessions.list",
        "git.status",
        "apps.open",
        "catalogue_sha256",
        "secret",
    ):
        assert forbidden not in body, forbidden


def test_connected_first_then_room_order_then_name(owner, personal, cleanup):
    salon = rooms.create_room(actor=owner, workspace=personal, channel=CHANNEL_WEB, name="Salon")
    study = rooms.create_room(
        actor=owner, workspace=personal, channel=CHANNEL_WEB, name="Çalışma odası"
    )
    rooms.update_room(actor=owner, room=study, channel=CHANNEL_WEB, sort_order=0)
    rooms.update_room(actor=owner, room=salon, channel=CHANNEL_WEB, sort_order=5)
    made = [
        labelled_node(owner, "Zeta", room=salon),
        labelled_node(owner, "Alfa", room=salon),
        labelled_node(owner, "Beta", room=study),
        labelled_node(owner, "Odasız"),
        labelled_node(owner, "Canlı", room=salon),
    ]
    cleanup.extend(node.pk for node in made)
    assert _names(node_presence(owner)) == ["Beta", "Alfa", "Canlı", "Zeta", "Odasız"]
    bus.mark_present(made[4].pk)  # "Canlı" goes online
    bus.mark_present(made[3].pk)  # "Odasız" goes online
    entries = node_presence(owner)
    assert _names(entries) == ["Canlı", "Odasız", "Beta", "Alfa", "Zeta"]
    assert [entry["status"] for entry in entries[:2]] == ["connected", "connected"]
    assert {entry["status"] for entry in entries[2:]} == {"disconnected"}


def test_revoked_and_expired_nodes_are_left_out(two_nodes, owner, cleanup):
    gone = labelled_node(owner, "Eski")
    cleanup.append(gone.pk)
    devices.revoke_device(device=gone.device, actor=owner)
    assert _names(node_presence(owner)) == ["MacBook", "Masaüstü"]
    entries, total = node_presence_page(owner)
    assert total == 2
    # Still listed on /devices (node_summary works on it and reads revoked).
    assert node_summary(Node.objects.get(pk=gone.pk))["status"] == "revoked"


def test_a_kill_switched_node_stays_listed_with_its_status(two_nodes, owner):
    bus.mark_present(two_nodes.macbook.pk)
    devices.engage_node_kill_switch(owner=owner, via="test")
    entries = node_presence(owner)
    assert {entry["status"] for entry in entries} == {"kill_switch"}
    assert {entry["voice"]["state"] for entry in entries} == {"off"}


def test_at_most_sixteen_nodes_and_the_total(owner, cleanup):
    made = [labelled_node(owner, f"Düğüm {index:02d}") for index in range(NODE_PRESENCE_MAX + 3)]
    cleanup.extend(node.pk for node in made)
    bus.mark_present(made[-1].pk)
    entries, total = node_presence_page(owner)
    assert len(entries) == NODE_PRESENCE_MAX == 16
    assert total == NODE_PRESENCE_MAX + 3
    # The cap applies after ordering: the one online node is never cut.
    assert entries[0]["name"] == made[-1].name and entries[0]["status"] == "connected"


def test_redis_down_reads_every_node_offline(two_nodes, owner, monkeypatch):
    for node in two_nodes.nodes:
        bus.mark_present(node.pk)
    assert {entry["status"] for entry in node_presence(owner)} == {"connected"}

    def down(node_id):
        raise bus.BusUnavailable("ConnectionError")

    monkeypatch.setattr(bus, "is_present", down)
    entries = node_presence(owner)
    assert {entry["status"] for entry in entries} == {"disconnected"}
    assert {entry["online"] for entry in entries} == {False}
    assert presence.is_online(two_nodes.macbook) is False


def test_voice_state_is_live_only_when_fresh_and_online(two_nodes, owner):
    now = timezone.now()
    mac = two_nodes.macbook
    Node.objects.filter(pk=mac.pk).update(
        voice_state="listening", voice_mode="always_on", voice_state_at=now
    )
    voice = {entry["name"]: entry["voice"] for entry in node_presence(owner, now=now)}
    assert voice["MacBook"]["state"] == "unknown"  # offline: fail closed
    bus.mark_present(mac.pk)
    voice = {entry["name"]: entry["voice"] for entry in node_presence(owner, now=now)}
    assert voice["MacBook"]["state"] == "listening"
    assert voice["MacBook"]["mode"] == "always_on"
    assert voice["Masaüstü"]["state"] == "unknown"
    later = now + timedelta(seconds=VOICE_STATE_STALE_SECONDS + 1)
    voice = {entry["name"]: entry["voice"] for entry in node_presence(owner, now=later)}
    assert voice["MacBook"]["state"] == "unknown"  # stale


def test_credential_expiring_is_reported_and_still_ordered_by_presence(two_nodes, owner):
    TrustedDevice.objects.filter(pk=two_nodes.desktop.device_id).update(
        secret_issued_at=timezone.now() - timedelta(days=31)
    )
    bus.mark_present(two_nodes.desktop.pk)
    entries = node_presence(owner)
    assert entries[0]["name"] == "Masaüstü"
    assert entries[0]["status"] == "credential_expiring" and entries[0]["online"] is True


def test_two_owners_each_see_only_their_own_nodes(two_nodes, owner, cleanup):
    stranger = other_user("stranger@test.dev")
    theirs = labelled_node(stranger, "Onların")
    cleanup.append(theirs.pk)
    bus.mark_present(theirs.pk)
    assert "Onların" not in _names(node_presence(owner))
    # A non-owner's node is revoked (one owner per installation), so the
    # stranger's own panel is empty -- and never shows the owner's nodes.
    assert node_presence(stranger) == []


def test_no_owner_means_no_entries():
    assert node_presence(None) == []
    assert node_presence_page(None) == ([], 0)


def test_presence_reads_write_no_row(two_nodes, owner):
    before = sorted(Node.objects.values_list("pk", "updated_at"))
    node_presence(owner)
    assert sorted(Node.objects.values_list("pk", "updated_at")) == before


def test_node_summary_carries_the_read_and_action_counts(two_nodes):
    mac = node_summary(two_nodes.macbook)
    desk = node_summary(two_nodes.desktop)
    assert mac["invocable_action_count"] > 0 and desk["invocable_action_count"] == 0
    assert mac["invocable_read_count"] + mac["invocable_action_count"] == mac["invocable_count"]
    assert desk["invocable_read_count"] == desk["invocable_count"]
