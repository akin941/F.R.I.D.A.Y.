"""Claude Code hook events relayed by the node, end to end (Phase 9 brief
D10; branch 6 tests and done criterion).

The composition root (`config.asgi_nodes.register_node_ports`) registers
the real 8A sink, `memory.ingestion.hooks.ingest_hook_events`; the
reference node is connected to `NodeSocketApp` in process (real Redis and
Postgres); events reach `CodingSession` through the 8A mapping rules alone.
It lives under `config/tests` because it wires `nodes` to `memory` exactly
as the composition root does (`nodes` tests may import neither `memory`
nor `config`, and `memory` tests may not import `nodes`)."""

import json
import os
import secrets
import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from django.db import connections
from rest_framework.test import APIClient

from accounts import devices
from accounts.authentication import TOKEN_PREFIX, hash_token
from accounts.models import DevicePairingCode, PersonalAccessToken, TrustedDevice
from accounts.services import register_user
from config import asgi_nodes
from core.channels import CHANNEL_WEB
from memory.ingestion.services import ensure_default_sources
from memory.models import CodingSession, Episode, IngestionSource, WorkspaceMappingRule
from memory.tests.ingestion_helpers import add_rule
from nodes import bus, events, services
from nodes.gateway import NodeSocketApp
from nodes.reference.hook_relay import HookRelay, relay_stdin
from nodes.reference.spool import Spool
from nodes.tests.gateway_harness import V1, V2, Harness, WallClock, reference_node
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db(transaction=True)

INGEST = "/api/v1/memory/ingest/hook-events"
SESSIONS = "/api/v1/memory/sessions"
PATH = WorkspaceMappingRule.RuleType.PATH_PREFIX
PASSWORD = "Str0ngDevPass!42"
SECRET = "sk-ant-api03-" + "Zx9Qw8Er7Ty6Ui5Op4As3Df2Gh1Jk0LmNbVcXz" * 2


@pytest.fixture(autouse=True)
def memory_sink():
    events.reset_sinks()
    asgi_nodes.register_node_ports()
    assert events.sink_registered(events.HOOK_EVENTS)
    yield
    events.reset_sinks()


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
    from workspaces.services import create_firm_workspace

    return create_firm_workspace(actor=owner, name="Acme", channel=CHANNEL_WEB)


@pytest.fixture
def mapped(owner, personal, firm):
    """The owner's code tree -> personal; the firm's tree -> firm."""
    ensure_default_sources(workspace=personal, created_by=owner)
    source = IngestionSource.objects.get(
        workspace=personal, kind=IngestionSource.Kind.CLAUDE_CODE_HOOKS
    )
    source.enabled = True
    source.save()
    add_rule(source, PATH, "/Users/o/code", personal)
    add_rule(source, PATH, "/Users/o/code/acme", firm)
    return source


@pytest.fixture
def hook_client(owner, personal):
    """`friday-hook`'s HTTP path: a `hook` PAT bound to the personal
    workspace."""
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    PersonalAccessToken.objects.create(
        workspace=personal,
        user=owner,
        name="hook",
        kind=PersonalAccessToken.Kind.HOOK,
        token_hash=hash_token(raw),
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return client


@pytest.fixture
def harness():
    made = []

    def make():
        app = NodeSocketApp(pubsub=False, tick_seconds=0.05)
        made.append(Harness(app))
        return app, made[-1]

    yield make
    for item in made:
        item.close()
    connections.close_all()


@pytest.fixture
def paired(owner):
    made = []

    def make(name="MacBook"):
        raw, _ = devices.issue_pairing_code(
            owner=owner,
            kind=DevicePairingCode.Kind.STRONG,
            issued_via=DevicePairingCode.IssuedVia.MANAGEMENT_COMMAND,
            device_kind=TrustedDevice.Kind.NODE,
        )
        result = services.pair_node(raw_code=raw, node_name=name, protocol_versions=[1])
        made.append(result.node.pk)
        return result.node, result.node_secret, result.keyring

    yield make
    for node_id in made:
        bus.clear_presence(node_id)
        bus.clear_inbox(node_id)
        bus.clear_inflight(node_id)


@pytest.fixture
def connected(harness, paired):
    """(app, link, node_row, reference) over the given subprotocols."""

    def make(subprotocols=(V1,), **node_kwargs):
        app, h = harness()
        node_row, secret, keyring = paired()
        reference = reference_node(node_row, keyring, **node_kwargs)
        link = h.connect(secret=secret, node=reference, subprotocols=subprotocols)
        assert link.frames_of("hello.ack"), link.close_code
        return app, link, node_row, reference

    return make


def _event(name="SessionStart", *, cwd="/Users/o/code/acme/api", session_id="cc-acme-1", **extra):
    return {
        "hook_event_name": name,
        "session_id": session_id,
        "cwd": cwd,
        "event_id": uuid.uuid4().hex,
        "occurred_at": datetime.now(UTC).isoformat(),
        **extra,
    }


def _relay(link, event):
    frames = link.node_call(lambda node: node.relay_hook_event(event))
    assert frames and frames[0]["type"] == "event"
    return frames[0]


def _ack_for(link, frame):
    assert link.wait_for(
        lambda: any(a["event_id"] == frame["id"] for a in link.frames_of("event.ack"))
    )
    return next(a for a in link.frames_of("event.ack") if a["event_id"] == frame["id"])


def _counts(ack):
    return ack["stored"], ack["duplicate"], ack["dropped"]


def _all_db_text():
    from django.db import connection

    chunks = []
    with connection.cursor() as cursor:
        for table in connection.introspection.table_names():
            if not table.startswith(("memory_", "audit_", "nodes_")):
                continue
            cursor.execute(f'SELECT * FROM "{table}"')  # noqa: S608 -- names from introspection
            chunks += [json.dumps(row, default=str) for row in cursor.fetchall()]
    return "\n".join(chunks)


# --- ingestion -----------------------------------------------------------


@pytest.mark.parametrize("subprotocols", [(V1,), (V2, V1)])
def test_a_node_batch_lands_in_the_mapped_workspace(connected, mapped, firm, subprotocols):
    _, link, _, _ = connected(subprotocols)
    ack = _ack_for(link, _relay(link, _event()))
    assert _counts(ack) == (1, 0, 0)
    session = CodingSession.objects.get()
    assert (session.workspace_id, session.session_id, session.project) == (
        firm.id,
        "cc-acme-1",
        "api",
    )
    assert session.status == CodingSession.Status.RUNNING


def test_an_unmapped_cwd_stores_nothing(connected, mapped):
    app, link, _, _ = connected()
    ack = _ack_for(link, _relay(link, _event(cwd="/private/elsewhere")))
    assert _counts(ack) == (0, 0, 1)
    assert app.counters["event_dropped_unmapped"] == 1
    assert not CodingSession.objects.exists() and not Episode.all_objects.exists()


def test_without_the_hook_source_enabled_nothing_is_stored(connected, mapped):
    mapped.enabled = False
    mapped.save()
    _, link, _, _ = connected()
    ack = _ack_for(link, _relay(link, _event()))
    assert _counts(ack) == (0, 0, 1)
    assert not CodingSession.objects.exists()


def test_friday_hook_then_the_node_is_stored_once(connected, mapped, hook_client):
    event = _event("Notification", message="Claude needs your permission")
    response = hook_client.post(INGEST, {"events": [event]}, format="json")
    assert response.status_code == 202 and response.json()["stored"] == 1
    _, link, _, _ = connected()
    ack = _ack_for(link, _relay(link, event))
    assert _counts(ack) == (0, 1, 0)
    assert Episode.all_objects.count() == 1
    assert CodingSession.objects.get().event_count == 1


def test_the_node_then_friday_hook_is_stored_once(connected, mapped, hook_client):
    event = _event()
    _, link, _, _ = connected()
    assert _counts(_ack_for(link, _relay(link, event))) == (1, 0, 0)
    response = hook_client.post(INGEST, {"events": [event]}, format="json")
    assert response.json()["stored"] == 0 and response.json()["duplicate"] == 1
    assert Episode.all_objects.count() == 1


def test_a_batch_of_51_is_refused_and_50_is_accepted(connected, mapped):
    app, link, _, _ = connected()
    batch = [_event(session_id=f"cc-{n}") for n in range(51)]
    link.send_frame(
        {
            "v": 1,
            "type": "event",
            "id": str(uuid.uuid4()),
            "kind": "claude_code.hook",
            "batch": batch,
            "spooled": False,
        }
    )
    assert link.wait_for(lambda: app.counters["frame_errors"] == 1)
    assert not link.frames_of("event.ack") and not Episode.all_objects.exists()
    frame = {
        "v": 1,
        "type": "event",
        "id": str(uuid.uuid4()),
        "kind": "claude_code.hook",
        "batch": batch[:50],
        "spooled": False,
    }
    link.send_frame(frame)
    assert _counts(_ack_for(link, frame)) == (50, 0, 0)


def test_an_unknown_event_name_is_ignored(connected, mapped):
    app, link, _, _ = connected()
    ack = _ack_for(link, _relay(link, _event("PreToolUse")))
    assert _counts(ack) == (0, 0, 1)
    assert app.counters["event_ignored"] == 1
    assert not Episode.all_objects.exists()


def test_an_event_older_than_24_hours_is_dropped(connected, mapped):
    app, link, _, _ = connected()
    old = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    ack = _ack_for(link, _relay(link, _event(occurred_at=old)))
    assert _counts(ack) == (0, 0, 1)
    assert app.counters["event_invalid"] == 1
    assert not Episode.all_objects.exists()


def test_the_ack_and_the_store_never_carry_the_secret(connected, mapped):
    _, link, _, _ = connected()
    event = _event("Notification", message=f"use key {SECRET} now")
    ack = _ack_for(link, _relay(link, event))
    assert _counts(ack) == (1, 0, 0)
    assert set(ack) == {"v", "type", "id", "event_id", "stored", "duplicate", "dropped"}
    assert SECRET not in json.dumps(link.server_frames)
    assert SECRET not in _all_db_text()


def test_a_spooled_backlog_is_ingested_in_order_after_reconnect(harness, paired, mapped, tmp_path):
    app, h = harness()
    node_row, secret, keyring = paired()
    clock = WallClock()
    reference = reference_node(node_row, keyring, clock=clock, spool=Spool(tmp_path / "s", clock))
    start = _event("SessionStart")
    note = _event("Notification", notification_type="permission_prompt", message="ok?")
    end = _event("SessionEnd", reason="exit")
    for event in (start, note, end):
        reference.relay_hook_event(event)
    link = h.connect(secret=secret, node=reference, subprotocols=(V1,))
    assert link.wait_for(lambda: len(link.frames_of("event.ack")) == 3)
    assert [_counts(ack) for ack in link.frames_of("event.ack")] == [(1, 0, 0)] * 3
    session = CodingSession.objects.get()
    assert (session.event_count, session.status, session.last_event) == (
        3,
        CodingSession.Status.STOPPED,
        "SessionEnd",
    )
    assert reference.spool.pending() == []


def test_the_node_principal_cannot_reach_a_workspace_the_owner_does_not_own(
    connected, mapped, owner
):
    """The mapping rules decide, but only into the owner's workspaces: a
    rule pointing at a stranger's workspace stores nothing."""
    stranger = register_user(email="stranger@test.dev", password=PASSWORD)
    stranger_ws = Workspace.objects.get(owner=stranger)
    add_rule(mapped, PATH, "/Users/o/code/stranger", stranger_ws)
    _, link, _, _ = connected()
    ack = _ack_for(link, _relay(link, _event(cwd="/Users/o/code/stranger/x")))
    assert ack["stored"] == 0
    assert not CodingSession.objects.filter(workspace=stranger_ws).exists()


# --- the done criterion --------------------------------------------------


@pytest.fixture
def socket_dir(tmp_path):
    # AF_UNIX paths are short (108 bytes); pytest's tmp_path can be long.
    path = f"/tmp/fn-{os.getpid()}-{id(tmp_path) % 100000}"
    yield path
    if os.path.isdir(path):
        for name in os.listdir(path):
            os.unlink(os.path.join(path, name))
        os.rmdir(path)


def test_the_done_criterion(connected, mapped, firm, owner, hook_client, web_client, socket_dir):
    """A hook event written to the reference relay's Unix socket reaches
    `GET /memory/sessions` through the node, and replaying it through
    `friday-hook` changes nothing."""
    _, link, _, reference = connected()
    relayed = []

    def deliver(event):
        relayed.append(event)
        link.node_call(lambda node: node.relay_hook_event(event))

    relay = HookRelay(
        os.path.join(socket_dir, "hook.sock"), deliver=deliver, clock=reference.clock
    ).start()
    try:
        payload = {
            "hook_event_name": "Notification",
            "session_id": "cc-acme-7",
            "cwd": "/Users/o/code/acme/web",
            "notification_type": "permission_prompt",
            "message": f"Claude needs permission; key {SECRET}",
            "transcript_path": "/Users/o/.claude/projects/x.jsonl",
        }
        writer = threading.Thread(
            target=relay_stdin, args=(relay.path, json.dumps(payload).encode()), daemon=True
        )
        writer.start()
        assert relay.handle_one(timeout=5) == "relayed"
        writer.join(5)
    finally:
        relay.close()
    assert link.wait_for(lambda: link.frames_of("event.ack"))
    assert _counts(link.frames_of("event.ack")[0]) == (1, 0, 0)

    rows = web_client(owner).get(SESSIONS).json()
    assert [(r["session_id"], r["workspace"]["id"], r["status"]) for r in rows] == [
        ("cc-acme-7", str(firm.id), "waiting_for_owner")
    ]
    assert SECRET not in json.dumps(rows)

    # Replay through friday-hook's HTTP path: a duplicate, nothing changes.
    (event,) = relayed
    response = hook_client.post(INGEST, {"events": [event]}, format="json")
    assert response.status_code == 202
    assert (response.json()["stored"], response.json()["duplicate"]) == (0, 1)
    assert web_client(owner).get(SESSIONS).json() == rows
    assert CodingSession.objects.get().event_count == 1
