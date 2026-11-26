"""The `nodes`-backed transcript port (runbook A12): the pull through
`nodes.services.invoke_read` and the gateway's read path to the Python
reference node (`tools/tests/node_reads_helpers.py`: a `NodeSession`, the
per-socket in-flight set, result validation, the Redis result key), its
refusals, the actor and channel checks, and `capability_granted` from the
node's latest `CapabilitySnapshot`. No Mac, Claude Code or Codex."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from audit.models import AuditLog
from core.channels import CHANNEL_SYSTEM, CHANNEL_WEB
from memory.models import Episode, IngestionSource, TranscriptCursor
from nodes import services
from nodes.audit_chain import verify_chain
from nodes.conformance import cases as C
from nodes.models import NodeInvocation
from nodes.reference.fixtures.factory import build_fixture_home
from tools import transcript_ingest
from tools.tests.node_reads_helpers import _user, cleanup, connect, make_world
from tools.transcript_ingest import (
    AUDIT_ACTION,
    FAILED,
    NODE_REFUSED,
    SENT,
    SERVER_REFUSED,
    NodeHandle,
    UnavailableNodePort,
    pull_transcripts,
)
from tools.transcript_node_port import NODE_UNKNOWN, NodesTranscriptPort, install

pytestmark = pytest.mark.django_db

CLAUDE = "claude.transcripts.owner_turns"
CODEX = "codex.transcripts.owner_turns"
Kind = IngestionSource.Kind
# The readers' clock is the fixture time (`HostileReaders`); the pull runs
# at the same instant, so its 7-day floor and the node's agree.
NOW = datetime.fromtimestamp(C.NOW, UTC)
ARGS = {
    "since": C.OWNER_TURN_SINCE,
    "cwd_prefixes": ["~/work"],
    "max_sessions": 50,
    "max_turns": 200,
}


@pytest.fixture(scope="module")
def home(tmp_path_factory):
    return build_fixture_home(tmp_path_factory.mktemp("transcript-port-home"))


@pytest.fixture
def world(db):
    """The node-read world (`~/work` -> Acme, `~/code` -> personal), both
    transcript sources enabled long ago."""
    world = make_world(nested=False)
    world.hooks.enabled = True
    world.hooks.save()
    IngestionSource.objects.filter(
        workspace=world.personal, kind__in=[Kind.CLAUDE_CODE_TRANSCRIPTS, Kind.CODEX_TRANSCRIPTS]
    ).update(enabled=True, enabled_at=NOW - timedelta(days=30))
    yield world
    cleanup(world)


@pytest.fixture
def link(world, home, monkeypatch):
    def make(**kwargs):
        return connect(world, home, **kwargs).install(monkeypatch)

    return make


@pytest.fixture
def port():
    return NodesTranscriptPort()


def handle(world):
    return NodeHandle(ref=str(world.node.pk), owner=world.owner)


def episodes(workspace):
    return Episode.objects.filter(workspace=workspace, kind__endswith=".owner_turn")


# --- installation ----------------------------------------------------------------


def test_the_app_installs_the_nodes_backed_port_as_the_default():
    assert isinstance(transcript_ingest.get_node_port(), NodesTranscriptPort)


def test_the_server_recheck_covers_every_reader_marker():
    """Moved from the 8B reader tests (`nodes` may not import `memory`)."""
    from memory.ingestion.transcripts import INJECTED_MARKERS
    from nodes.reference import owner_turns, readers

    markers = set(owner_turns.CLAUDE_MARKERS) | set(owner_turns.CODEX_MARKERS)
    assert markers == set(INJECTED_MARKERS)
    assert set(readers.CLAUDE_MARKERS) | set(readers.CODEX_MARKERS) == markers


def test_install_replaces_only_the_placeholder():
    previous = transcript_ingest.set_node_port(UnavailableNodePort())
    try:
        install()
        assert isinstance(transcript_ingest.get_node_port(), NodesTranscriptPort)
        custom = object()
        transcript_ingest.set_node_port(custom)
        install()
        assert transcript_ingest.get_node_port() is custom
    finally:
        transcript_ingest.set_node_port(previous)


# --- which node, and granted -------------------------------------------------------


def test_no_node_is_reachable_without_a_connected_node(world, port):
    assert port.owner_node(world.owner) is None  # paired, never connected
    stranger = _user("stranger@test.dev")
    assert port.owner_node(stranger) is None  # no node at all
    assert port.owner_node(None) is None


def test_the_connected_node_is_the_owners_handle(world, link, port):
    session = link()
    node = port.owner_node(world.owner)
    assert node == handle(world)
    session.go_offline()
    assert port.owner_node(world.owner) is None


def test_another_owners_node_is_never_picked(world, link, port):
    link()
    stranger = _user("stranger@test.dev", is_owner=False)
    assert port.owner_node(stranger) is None


class _FakeNode:
    def __init__(self, name, *, online, offers, active_minutes_ago=None, created_minutes_ago=60):
        self.pk = name
        self.name = name
        self.owner = object()
        self.online = online
        self.offers = offers
        self.last_active_at = (
            None if active_minutes_ago is None else NOW - timedelta(minutes=active_minutes_ago)
        )
        self.created_at = NOW - timedelta(minutes=created_minutes_ago)


def _pick(monkeypatch, nodes):
    from tools import transcript_node_port as module

    monkeypatch.setattr(module, "nodes_for", lambda owner: list(nodes))
    monkeypatch.setattr(module, "is_usable", lambda node: True)
    monkeypatch.setattr(module.presence, "is_online", lambda node: node.online)
    monkeypatch.setattr(module, "_offers_owner_turns", lambda node: node.offers)
    owner = type("Owner", (), {"pk": 1})()
    picked = NodesTranscriptPort().owner_node(owner)
    return None if picked is None else picked.ref


def test_an_online_node_wins_over_an_offline_one_that_offers_the_reads(monkeypatch):
    """Review follow-up (A12): online first, then offering the reads."""
    offline_offering = _FakeNode(
        "offline-offering", online=False, offers=True, active_minutes_ago=1
    )
    online_plain = _FakeNode("online-plain", online=True, offers=False, active_minutes_ago=30)
    assert _pick(monkeypatch, [offline_offering, online_plain]) == "online-plain"
    assert _pick(monkeypatch, [online_plain, offline_offering]) == "online-plain"


def test_among_online_nodes_the_one_offering_the_reads_wins(monkeypatch):
    plain = _FakeNode("online-plain", online=True, offers=False, active_minutes_ago=1)
    offering = _FakeNode("online-offering", online=True, offers=True, active_minutes_ago=30)
    assert _pick(monkeypatch, [plain, offering]) == "online-offering"


def test_then_the_most_recently_active_then_the_newest(monkeypatch):
    older = _FakeNode("older", online=True, offers=True, active_minutes_ago=10)
    recent = _FakeNode("recent", online=True, offers=True, active_minutes_ago=2)
    assert _pick(monkeypatch, [recent, older]) == "recent"
    first = _FakeNode("first", online=True, offers=True, created_minutes_ago=90)
    newest = _FakeNode("newest", online=True, offers=True, created_minutes_ago=5)
    assert _pick(monkeypatch, [newest, first]) == "newest"


def test_no_online_node_is_no_node_even_when_an_offline_one_offers(monkeypatch):
    offline = _FakeNode("offline-offering", online=False, offers=True, active_minutes_ago=1)
    assert _pick(monkeypatch, [offline]) is None


def test_capability_granted_follows_the_latest_snapshot(world, link, port):
    link()
    assert port.capability_granted(handle(world), CLAUDE)
    assert port.capability_granted(handle(world), CODEX)
    # A later hello with the ~/.claude grant off: the newest snapshot wins.
    link(grants={"claude": False})
    assert not port.capability_granted(handle(world), CLAUDE)
    assert port.capability_granted(handle(world), CODEX)


def test_capability_granted_refuses_unknown_and_foreign_handles(world, link, port):
    link()
    stranger = _user("stranger@test.dev")
    assert not port.capability_granted(NodeHandle(ref=str(world.node.pk), owner=stranger), CLAUDE)
    assert not port.capability_granted(NodeHandle(ref="not-a-node", owner=world.owner), CLAUDE)
    assert not port.capability_granted(handle(world), "no.such.capability")


def test_a_node_advertising_a_different_schema_is_not_granted(world, link, port):
    session = link()
    catalogue = [dict(entry) for entry in session.node.catalogue]
    for entry in catalogue:
        if entry["name"] == CLAUDE:
            entry["result_schema"] = {**entry["result_schema"], "maxProperties": 3}
    services.record_capability_snapshot(
        node=world.node, catalogue=catalogue, catalogue_sha256="0" * 64, protocol_version=1
    )
    assert not port.capability_granted(handle(world), CLAUDE)


# --- the pull over the node layer -------------------------------------------------------


def test_the_pull_reads_each_workspace_with_its_prefixes_through_the_chain(world, link, port):
    session = link()
    report = pull_transcripts(now=NOW, port=port)
    assert (report.invokes, report.refused, report.failed) == (4, 0, 0)
    sent = {
        (e["capability"], e["workspace_id"], tuple(e["args"]["cwd_prefixes"]))
        for e in session.received
    }
    assert sent == {
        (CLAUDE, str(world.firm.pk), ("~/work",)),
        (CLAUDE, str(world.personal.pk), ("~/code",)),
        (CODEX, str(world.firm.pk), ("~/work",)),
        (CODEX, str(world.personal.pk), ("~/code",)),
    }
    rows = list(NodeInvocation.objects.order_by("seq"))
    assert [(r.status, r.channel, r.actor_id) for r in rows] == [
        ("ok", CHANNEL_SYSTEM, world.owner.pk)
    ] * 4
    assert {r.nonce for r in rows} == {e["nonce"] for e in session.received}
    assert verify_chain().ok
    assert AuditLog.objects.filter(action="nodes.invoke").count() == 4
    pulls = AuditLog.objects.filter(action=AUDIT_ACTION)
    assert pulls.count() == 4 and {p.metadata["status"] for p in pulls} == {SENT}
    # Hashes and sizes only in the chain; never a turn or a path.
    stored = json.dumps(list(NodeInvocation.objects.values()), default=str)
    assert "Fix the flaky" not in stored and "~/work" not in stored


def test_owner_turns_land_scrubbed_in_their_workspaces(world, link, port):
    link()
    pull_transcripts(now=NOW, port=port)
    firm = {e.owner_text for e in episodes(world.firm)}
    personal = {e.owner_text for e in episodes(world.personal)}
    assert "Fix the flaky invoice test in acme." in firm
    assert any("[SECRET:github_token]" in text for text in firm)  # codex, scrubbed node-side
    assert any("[SECRET:aws_key]" in text for text in personal)
    assert "Review the HUD panel copy." in personal
    every = json.dumps(sorted(firm | personal), ensure_ascii=False)
    for phrase in (*C.FIXTURE_SECRETS, *C.NEVER_RETURNED, "I will look at the test first."):
        assert phrase not in every, phrase
    assert "SCRATCH-PHRASE" not in every  # /tmp/scratch has no rule, so no prefix asks for it


def test_the_cursor_advances_and_the_next_beat_stores_nothing_twice(world, link, port):
    link()
    pull_transcripts(now=NOW, port=port)
    cursors = {
        (c.source.kind, c.target_workspace_id): c.since for c in TranscriptCursor.objects.all()
    }
    assert cursors[(Kind.CLAUDE_CODE_TRANSCRIPTS, world.personal.pk)] == datetime(
        2026, 11, 30, 0, 40, tzinfo=UTC
    )
    assert all(c.node_ref == str(world.node.pk) for c in TranscriptCursor.objects.all())
    before = Episode.all_objects.count()
    report = pull_transcripts(now=NOW + timedelta(minutes=15), port=port)
    assert report.invokes == 4 and report.failed == 0
    assert Episode.all_objects.count() == before


def test_a_capability_the_node_did_not_grant_is_skipped(world, link, port):
    session = link(grants={"claude": False})
    report = pull_transcripts(now=NOW, port=port)
    assert ("claude", "not_granted") in report.skipped
    assert {e["capability"] for e in session.received} == {CODEX}


def test_an_offline_node_is_skipped_and_nothing_is_sent(world, link, port):
    session = link()
    session.go_offline()
    report = pull_transcripts(now=NOW, port=port)
    assert report.invokes == 0 and ("claude", "no_node") in report.skipped
    assert NodeInvocation.objects.count() == 0


# --- refusals: actor, channel, node -----------------------------------------------------


def _invoke(port, world, **overrides):
    kwargs = {
        "node": handle(world),
        "capability": CLAUDE,
        "args": ARGS,
        "workspace": world.firm,
        "actor": world.owner,
        "channel": CHANNEL_SYSTEM,
        "trace_id": "trace-port",
    }
    kwargs.update(overrides)
    return port.invoke_read(**kwargs)


def test_an_actor_who_is_not_the_node_owner_is_refused_before_anything_is_sent(world, link, port):
    session = link()
    outcome = _invoke(port, world, actor=world.member)
    assert (outcome.status, outcome.code) == (SERVER_REFUSED, "actor_not_owner")
    outcome = _invoke(port, world, actor=None)
    assert (outcome.status, outcome.code) == (SERVER_REFUSED, "actor_not_owner")
    assert session.received == []
    rows = NodeInvocation.objects.order_by("seq")
    assert [(r.status, r.refusal_code, r.nonce) for r in rows] == [
        ("server_refused", "actor_not_owner", "")
    ] * 2


def test_the_system_channel_is_only_for_the_owner_turn_reads(world, link, port):
    session = link()
    listed = _invoke(
        port, world, capability="claude.sessions.list", args={"since_hours": 24, "limit": 5}
    )
    assert (listed.status, listed.code) == (SERVER_REFUSED, "not_invocable")
    other_channel = _invoke(port, world, channel="cli")
    assert (other_channel.status, other_channel.code) == (SERVER_REFUSED, "not_invocable")
    assert session.received == []
    assert _invoke(port, world, channel=CHANNEL_SYSTEM).status == SENT
    assert _invoke(port, world, channel=CHANNEL_WEB).status == SENT  # an owner web read


def test_args_outside_the_contract_are_refused_by_the_server(world, link, port):
    session = link()
    outcome = _invoke(port, world, args={**ARGS, "include_assistant": True})
    assert (outcome.status, outcome.code) == (SERVER_REFUSED, "args_contract")
    assert session.received == []


def test_an_unknown_node_reference_fails_without_a_call(world, port):
    outcome = _invoke(port, world, node=NodeHandle(ref="not-a-node", owner=world.owner))
    assert (outcome.status, outcome.code) == (FAILED, NODE_UNKNOWN)
    assert NodeInvocation.objects.count() == 0


def test_a_node_that_went_offline_fails_node_offline(world, link, port):
    session = link()
    session.go_offline()
    outcome = _invoke(port, world)
    assert (outcome.status, outcome.code) == (FAILED, "node_offline")


def test_a_node_refusal_is_node_refused(world, link, port):
    session = link()
    # A since the server's schema accepts but the node's own reader refuses
    # (older than its 7-day bound) is an error without a code; a schema the
    # node narrowed would be a coded refusal. Both are never `ok`.
    old = (NOW - timedelta(days=8)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    outcome = _invoke(port, world, args={**ARGS, "since": old})
    assert (outcome.status, outcome.code) == (FAILED, "error:node_error")
    assert session.received
    session.node.catalogue = [
        {**e, "availability": "not_granted"} if e["name"] == CLAUDE else e
        for e in session.node.catalogue
    ]
    outcome = _invoke(port, world)
    assert (outcome.status, outcome.code) == (NODE_REFUSED, "not_granted")


def test_a_result_outside_the_contract_never_reaches_the_pull(world, link, port):
    def lie(output):
        return {**output, "sessions": [{"session_id": "x", "cwd": "~/work", "turns": []}]}

    link(rewrite={CLAUDE: lie})
    outcome = _invoke(port, world)
    assert outcome.status == FAILED and outcome.code.startswith("error:result_schema")
    assert outcome.output is None


def test_bus_down_is_a_failure(world, link, port, settings):
    link()
    url = settings.REDIS_URL
    settings.REDIS_URL = "redis://127.0.0.1:1/0"
    try:
        outcome = _invoke(port, world)
    finally:
        settings.REDIS_URL = url  # the world's Redis cleanup runs after this
    assert (outcome.status, outcome.code) == (FAILED, "bus_unavailable")
