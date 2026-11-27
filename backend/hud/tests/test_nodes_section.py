"""Phase 13 D4: the HUD `nodes` section -- one row per non-revoked node of
the actor in every stream (snapshot and deltas), the rows' key allowlist,
isolation between owners and in a firm stream, and the existing
`health.node` line unchanged. Presence is the real Redis key
(`nodes.bus`), cleared after each test; no node process runs."""

import json
from datetime import UTC, datetime

import pytest

from accounts.services import register_user
from hud import services
from hud.tests.conftest import EVENTS, PASSWORD, read_stream
from nodes import bus
from nodes.models import Node
from nodes.selectors import PRESENCE_KEYS
from nodes.tests.presence_nodes import labelled_node, make_two_nodes
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

VOICE_KEYS = {
    "state",
    "mode",
    "session_open",
    "wake_accepts_today",
    "wake_discards_today",
    "storm_mutes_today",
    "at",
}


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


def _firm(owner):
    firm = Workspace.objects.create(name="Firma", owner=owner, type="company")
    WorkspaceMember.objects.create(workspace=firm, user=owner, role="owner")
    return firm


def test_nodes_is_a_section_of_every_stream(stream, workspace, owner):
    assert "nodes" in services.SECTIONS
    snapshot = stream(workspace.id)[1]
    assert snapshot["type"] == "snapshot"
    assert snapshot["nodes"] == {"items": [], "total": 0, "online": 0}
    assert stream(_firm(owner).id)[1]["nodes"] == {"items": [], "total": 0, "online": 0}


def test_two_nodes_are_two_rows_with_live_status(stream, workspace, two_nodes):
    bus.mark_present(two_nodes.desktop.pk)
    section = stream(workspace.id)[1]["nodes"]
    assert section["total"] == 2 and section["online"] == 1
    rows = section["items"]
    assert [row["name"] for row in rows] == ["Masaüstü", "MacBook"]
    assert [row["status"] for row in rows] == ["connected", "disconnected"]
    for row in rows:
        assert tuple(row) == PRESENCE_KEYS
        assert set(row["voice"]) == VOICE_KEYS
        assert row["room"]["name"] == "Çalışma odası"
    assert rows[0]["platform"] == "linux" and rows[0]["invocable_action_count"] == 0
    assert rows[1]["platform"] == "macos" and rows[1]["invocable_action_count"] > 0


def test_rows_carry_no_path_capability_or_content(stream, workspace, two_nodes):
    body = json.dumps(stream(workspace.id)[1]["nodes"])
    for forbidden in ("~/", "/Users", "args_schema", "sessions.list", "git.status", "claude."):
        assert forbidden not in body, forbidden


def test_a_delta_carries_the_section_when_presence_flips(stream, workspace, two_nodes, fake_clock):
    mac = two_nodes.macbook
    fake_clock.ticks = [lambda: bus.mark_present(mac.pk), lambda: bus.clear_presence(mac.pk)]
    frames = stream(workspace.id)
    assert frames[1]["nodes"]["online"] == 0
    up, down = frames[2], frames[3]
    # The health line follows too (its "most relevant node" is now live).
    assert up["type"] == "delta" and set(up) == {"type", "health", "nodes"}
    assert up["nodes"]["items"][0]["name"] == "MacBook"
    assert up["nodes"]["items"][0]["status"] == "connected"
    assert down["type"] == "delta" and down["nodes"]["online"] == 0


def test_another_owners_nodes_never_show(stream, workspace, two_nodes, cleanup):
    stranger = register_user(email="stranger@test.dev", password=PASSWORD)
    theirs = labelled_node(stranger, "Onların")
    cleanup.append(theirs.pk)
    bus.mark_present(theirs.pk)
    names = [row["name"] for row in stream(workspace.id)[1]["nodes"]["items"]]
    assert names == ["MacBook", "Masaüstü"]
    assert services.nodes_section(stranger)["items"] == []


def test_a_firm_stream_shows_the_owners_own_nodes_only(
    stream, owner, two_nodes, cleanup, web_client
):
    firm = _firm(owner)
    member = register_user(email="member@test.dev", password=PASSWORD)
    WorkspaceMember.objects.create(workspace=firm, user=member, role="member")
    theirs = labelled_node(member, "Üyenin")
    cleanup.append(theirs.pk)
    names = [row["name"] for row in stream(firm.id)[1]["nodes"]["items"]]
    assert names == ["MacBook", "Masaüstü"]


def test_the_health_node_line_is_unchanged(stream, workspace, two_nodes):
    """The Phase 9 line's payload (keys and shape) is unchanged by the new
    section: it still names one node, by status and counts only."""
    heartbeat = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    Node.objects.filter(pk=two_nodes.desktop.pk).update(
        node_version="0.2.0", spool_count=1, last_heartbeat_at=heartbeat
    )
    bus.mark_present(two_nodes.desktop.pk)
    frames = stream(workspace.id)
    node = frames[1]["health"]["node"]
    assert node["catalogue_sha256"] and len(node["catalogue_sha256"]) == 12
    assert node == {
        "status": "connected",
        "last_heartbeat_at": "2026-11-20T12:00:00+00:00",
        "catalogue_sha256": node["catalogue_sha256"],
        "invocable_count": frames[1]["nodes"]["items"][0]["invocable_read_count"],
        "advertised_count": node["advertised_count"],
        "spool_depth": 1,
        "node_version": "0.2.0",
        "voice": {
            "state": "unknown",
            "mode": "",
            "session_open": False,
            "wake_accepts_today": 0,
            "wake_discards_today": 0,
            "storm_mutes_today": 0,
            "at": None,
        },
    }
    assert str(two_nodes.desktop.pk) not in json.dumps(frames[1]["health"])


@pytest.mark.parametrize(
    ("principal", "expected"),
    [
        ("owner-web", 200),
        ("owner-pat-cli", 403),
        ("owner-pat-openai", 403),
        ("non-owner", 403),
        ("agent", 403),
        ("anonymous", 401),
    ],
)
def test_only_the_owners_web_session_reads_the_section(
    principal_client, workspace, two_nodes, principal, expected
):
    response = principal_client(principal).get(EVENTS, {"workspace": str(workspace.id)})
    assert response.status_code == expected
    if expected == 200:
        assert len(read_stream(response)[1]["nodes"]["items"]) == 2
    else:
        assert not getattr(response, "streaming", False)
        assert "Masaüstü" not in response.content.decode()


def test_the_section_writes_no_row(workspace, owner, two_nodes):
    before = sorted(Node.objects.values_list("pk", "updated_at"))
    services.build_sections(owner, workspace)
    assert sorted(Node.objects.values_list("pk", "updated_at")) == before
