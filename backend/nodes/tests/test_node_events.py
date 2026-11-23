"""Node-relayed events through the gateway (Phase 9 brief D10; branch 6
tests): the sink registry, `event.ack` counts only, the spool upload (in
order, one in flight, deleted only after its ack), the spooled-batch
throttle, no reverse path to the invoke bus, no sink -> `dropped`, and
`grant.availability_changed` moving the snapshot's flags (and so the HUD's
node block). The memory sink itself is exercised end to end under
`config/tests/test_hooks_via_node.py` (`nodes` tests may not import
`memory`)."""

import json
import os
import time

import pytest

from nodes import bus, events, services
from nodes.capabilities import get_contract
from nodes.models import CapabilitySnapshot
from nodes.reference.node import default_catalogue
from nodes.reference.spool import Spool
from nodes.selectors import latest_snapshot, node_health
from nodes.tests.gateway_harness import V1, V2, WallClock, reference_node

pytestmark = pytest.mark.django_db(transaction=True)

SECRET = "ghp_" + "Q" * 36
FRONTMOST_VERSION = get_contract("apps.frontmost").version


def _hook_event(n, **extra):
    return {
        "hook_event_name": "Stop",
        "session_id": f"cc-{n}",
        "cwd": "/Users/o/code/friday",
        "event_id": f"{n:032x}",
        **extra,
    }


def _frame(version, kind, batch, *, spooled=False, frame_id=None):
    import uuid

    return {
        "v": version,
        "type": "event",
        "id": frame_id or str(uuid.uuid4()),
        "kind": kind,
        "batch": batch,
        "spooled": spooled,
    }


class RecordingSink:
    """Stands in for `memory.ingestion.hooks.ingest_hook_events`: records
    each call and answers 8A-shaped counts."""

    def __init__(self, counts=None, fail=False):
        self.calls = []
        self.counts = counts
        self.fail = fail

    def __call__(self, *, user, bound_workspace_id, events):
        self.calls.append(
            {"user": user.pk, "workspace": bound_workspace_id, "events": list(events)}
        )
        if self.fail:
            raise RuntimeError("database is down")
        if self.counts is not None:
            return dict(self.counts)
        return {"received": len(events), "stored": len(events), "duplicate": 0}


@pytest.fixture
def sink():
    recording = RecordingSink()
    events.register_sink(events.HOOK_EVENTS, recording)
    return recording


# --- the registry --------------------------------------------------------


def test_only_the_hook_kind_takes_a_sink():
    with pytest.raises(ValueError):
        events.register_sink(events.AVAILABILITY_CHANGED, RecordingSink())
    with pytest.raises(ValueError):
        events.register_sink("policy.set", RecordingSink())
    with pytest.raises(TypeError):
        events.register_sink(events.HOOK_EVENTS, "not callable")
    assert not events.sink_registered(events.HOOK_EVENTS)
    events.register_sink(events.HOOK_EVENTS, RecordingSink())
    assert events.sink_registered(events.HOOK_EVENTS)


# --- the hook path -------------------------------------------------------


@pytest.mark.parametrize("subprotocols", [(V1,), (V2, V1)])
def test_a_hook_batch_reaches_the_sink_with_the_node_principal(link_node, sink, subprotocols):
    link, node_row, reference = link_node(subprotocols=subprotocols)
    frames = link.node_call(lambda node: node.relay_hook_event(_hook_event(1)))
    assert frames and frames[0]["type"] == "event" and frames[0]["spooled"] is False
    assert link.wait_for(lambda: link.frames_of("event.ack"))
    ack = link.frames_of("event.ack")[0]
    assert ack["v"] == reference.version
    assert (ack["event_id"], ack["stored"], ack["duplicate"], ack["dropped"]) == (
        frames[0]["id"],
        1,
        0,
        0,
    )
    # The `hook` PAT's binding: the owner and the node's personal workspace.
    assert sink.calls == [
        {"user": node_row.owner_id, "workspace": node_row.workspace_id, "events": [_hook_event(1)]}
    ]
    # The node forgot its in-flight batch on the ack.
    assert reference._event_inflight is None


def test_the_ack_maps_the_sink_counts_and_keeps_the_detail_server_side(gateway, link_node):
    app, _ = gateway
    sink = RecordingSink(
        counts={
            "received": 5,
            "stored": 1,
            "duplicate": 1,
            "dropped_unmapped": 1,
            "dropped_paused": 0,
            "ignored": 1,
            "invalid": 1,
        }
    )
    events.register_sink(events.HOOK_EVENTS, sink)
    link, _, _ = link_node(subprotocols=(V1,))
    link.send_frame(_frame(1, events.HOOK_EVENTS, [_hook_event(n) for n in range(5)]))
    assert link.wait_for(lambda: link.frames_of("event.ack"))
    ack = link.frames_of("event.ack")[0]
    assert (ack["stored"], ack["duplicate"], ack["dropped"]) == (1, 1, 3)
    assert app.counters["event_ignored"] == 1 and app.counters["event_dropped_unmapped"] == 1
    assert app.counters["event_invalid"] == 1


def test_a_lying_sink_cannot_overflow_the_ack(link_node):
    events.register_sink(
        events.HOOK_EVENTS, RecordingSink(counts={"stored": 99, "duplicate": 99, "bogus": "x"})
    )
    link, _, _ = link_node(subprotocols=(V1,))
    link.send_frame(_frame(1, events.HOOK_EVENTS, [_hook_event(1), _hook_event(2)]))
    assert link.wait_for(lambda: link.frames_of("event.ack"))
    ack = link.frames_of("event.ack")[0]
    assert (ack["stored"], ack["duplicate"], ack["dropped"]) == (2, 0, 0)


def test_the_ack_carries_counts_only(link_node, sink):
    """A secret-bearing payload: the ack is exactly the counts."""
    link, _, _ = link_node(subprotocols=(V1,))
    frame = _frame(
        1,
        events.HOOK_EVENTS,
        [_hook_event(1, hook_event_name="Notification", message=f"token {SECRET}")],
    )
    link.send_frame(frame)
    assert link.wait_for(lambda: link.frames_of("event.ack"))
    ack = link.frames_of("event.ack")[0]
    assert set(ack) == {"v", "type", "id", "event_id", "stored", "duplicate", "dropped"}
    assert ack["event_id"] == frame["id"]
    assert SECRET not in json.dumps(link.server_frames)


def test_without_a_sink_the_batch_is_dropped_and_nothing_is_called(gateway, link_node):
    app, _ = gateway
    link, _, reference = link_node(subprotocols=(V1,))
    frames = link.node_call(lambda node: node.relay_hook_event(_hook_event(1)))
    assert link.wait_for(lambda: link.frames_of("event.ack"))
    ack = link.frames_of("event.ack")[0]
    assert (ack["event_id"], ack["stored"], ack["dropped"]) == (frames[0]["id"], 0, 1)
    assert app.counters["event_no_sink"] == 1


def test_a_failing_sink_sends_no_ack_and_the_session_stays(gateway, link_node):
    app, _ = gateway
    failing = RecordingSink(fail=True)
    events.register_sink(events.HOOK_EVENTS, failing)
    link, _, reference = link_node(subprotocols=(V1,))
    link.node_call(lambda node: node.relay_hook_event(_hook_event(1)))
    assert link.wait_for(lambda: app.counters["event_sink_failed"] == 1)
    assert not link.frames_of("event.ack")
    assert reference._event_inflight is not None  # the node keeps the batch
    assert link.close_code is None
    # The session is otherwise healthy: a heartbeat is still answered.
    link.node_call(lambda node: node._heartbeat_frames())
    assert link.wait_for(lambda: link.frames_of("heartbeat.ack"))


def test_an_oversized_batch_is_a_frame_error_and_reaches_no_sink(gateway, link_node, sink):
    app, _ = gateway
    link, _, _ = link_node(subprotocols=(V1,))
    link.send_frame(_frame(1, events.HOOK_EVENTS, [_hook_event(n) for n in range(51)]))
    assert link.wait_for(lambda: app.counters["frame_errors"] == 1)
    assert not link.frames_of("event.ack") and sink.calls == []


# --- the spool upload ----------------------------------------------------


@pytest.fixture
def spool_dir(tmp_path):
    return tmp_path / "spool"


def _spooled_node(paired, spool_dir):
    node_row, secret, keyring = paired()
    clock = WallClock()
    reference = reference_node(node_row, keyring, clock=clock, spool=Spool(spool_dir, clock))
    return node_row, secret, reference


def test_a_reconnect_uploads_the_spool_in_order_one_in_flight(gateway, paired, sink, spool_dir):
    _, harness = gateway
    node_row, secret, reference = _spooled_node(paired, spool_dir)
    # Offline: every event is spooled, one batch each.
    for n in range(1, 4):
        assert reference.relay_hook_event(_hook_event(n)) == []
    assert len(reference.spool.pending()) == 3
    link = harness.connect(secret=secret, node=reference, subprotocols=(V1,))
    assert link.wait_for(lambda: len(link.frames_of("event.ack")) == 3)
    sent = [frame for frame in reference.sent if frame["type"] == "event"]
    assert [frame["spooled"] for frame in sent] == [True, True, True]
    # In order, and each acked before the next was sent (one in flight).
    assert [call["events"][0]["event_id"] for call in sink.calls] == [
        f"{n:032x}" for n in range(1, 4)
    ]
    assert [ack["event_id"] for ack in link.frames_of("event.ack")] == [f["id"] for f in sent]
    order = [(f["type"], f.get("id")) for f in reference.sent if f["type"] == "event"]
    assert len(order) == len(set(order))
    assert reference.spool.pending() == []
    assert not os.listdir(spool_dir)


def test_the_spool_deletes_only_acked_batches(gateway, paired, spool_dir):
    _, harness = gateway
    node_row, secret, reference = _spooled_node(paired, spool_dir)
    reference.relay_hook_event(_hook_event(1))
    reference.relay_hook_event(_hook_event(2))
    failing = RecordingSink(fail=True)
    events.register_sink(events.HOOK_EVENTS, failing)
    link = harness.connect(secret=secret, node=reference, subprotocols=(V1,))
    assert link.wait_for(lambda: len(failing.calls) == 1)
    time.sleep(0.2)
    # Not acked: both batches are still on disk, the first one in flight.
    assert len(reference.spool.pending()) == 2
    assert not link.frames_of("event.ack")
    link.drop()
    assert link.wait_done()
    # Reconnect with a working sink: both are sent again and deleted.
    working = RecordingSink()
    events.register_sink(events.HOOK_EVENTS, working)
    again = harness.connect(secret=secret, node=reference, subprotocols=(V1,))
    assert again.wait_for(lambda: len(again.frames_of("event.ack")) == 2)
    assert [call["events"][0]["event_id"] for call in working.calls] == [
        f"{1:032x}",
        f"{2:032x}",
    ]
    assert reference.spool.pending() == []


def test_spooled_batches_are_paced_to_ten_a_second(gateway, link_node, sink):
    app, _ = gateway
    link, _, _ = link_node(subprotocols=(V1,))
    sent = [_frame(1, events.HOOK_EVENTS, [_hook_event(n)], spooled=True) for n in range(12)]
    started = time.monotonic()
    for frame in sent:
        link.send_frame(frame)
    assert link.wait_for(lambda: len(link.frames_of("event.ack")) == 12, timeout=10)
    elapsed = time.monotonic() - started
    assert app.counters["event_throttled"] >= 1 and elapsed >= 0.9
    assert [ack["event_id"] for ack in link.frames_of("event.ack")] == [f["id"] for f in sent]
    assert link.close_code is None  # paced, never refused


def test_the_throttle_books_ten_slots_a_second():
    now = [100.0]
    throttle = events.SpoolThrottle(clock=lambda: now[0])
    assert [throttle.delay() for _ in range(10)] == [0.0] * 10
    assert throttle.delay() == pytest.approx(1.0)
    now[0] = 101.5
    assert throttle.delay() == 0.0


def test_live_batches_are_not_throttled(gateway, link_node, sink):
    app, _ = gateway
    link, _, _ = link_node(subprotocols=(V1,))
    for n in range(12):
        link.send_frame(_frame(1, events.HOOK_EVENTS, [_hook_event(n)]))
    assert link.wait_for(lambda: len(link.frames_of("event.ack")) == 12)
    assert app.counters["event_throttled"] == 0


# --- no reverse path -----------------------------------------------------


def test_no_event_ever_leads_to_an_invoke(gateway, link_node, sink, monkeypatch):
    pushed = []
    monkeypatch.setattr(bus, "push_invoke", lambda *a, **k: pushed.append((a, k)))
    link, node_row, _ = link_node(subprotocols=(V2, V1))
    link.send_frame(_frame(2, events.HOOK_EVENTS, [_hook_event(1)]))
    link.send_frame(
        _frame(
            2,
            events.AVAILABILITY_CHANGED,
            [{"name": "apps.frontmost", "version": FRONTMOST_VERSION, "availability": "granted"}],
        )
    )
    link.send_frame(_frame(2, events.HOOK_EVENTS, [_hook_event(2)], spooled=True))
    assert link.wait_for(lambda: len(link.frames_of("event.ack")) == 3)
    time.sleep(0.2)
    assert pushed == []
    assert bus._run(lambda c: c.llen(bus.inbox_key(node_row.pk))) == 0
    assert {frame["type"] for frame in link.server_frames} == {"hello.ack", "event.ack"}


# --- grant.availability_changed -----------------------------------------


def _availability(name, availability, version=None):
    return {
        "name": name,
        "version": FRONTMOST_VERSION if version is None else version,
        "availability": availability,
    }


def _entry(snapshot, name):
    return next(entry for entry in snapshot.entries if entry["name"] == name)


def test_an_availability_change_moves_the_snapshot_flags_and_the_hud_block(link_node, owner):
    link, node_row, _ = link_node(subprotocols=(V1,))
    before = latest_snapshot(node_row)
    assert _entry(before, "apps.frontmost")["invocable"] is True
    health_before = node_health(owner)
    link.send_frame(
        _frame(1, events.AVAILABILITY_CHANGED, [_availability("apps.frontmost", "not_granted")])
    )
    assert link.wait_for(lambda: link.frames_of("event.ack"))
    ack = link.frames_of("event.ack")[0]
    assert (ack["stored"], ack["duplicate"], ack["dropped"]) == (1, 0, 0)
    after = latest_snapshot(node_row)
    assert after.pk != before.pk and after.catalogue_sha256 == before.catalogue_sha256
    entry = _entry(after, "apps.frontmost")
    assert (entry["availability"], entry["invocable"], entry["reason"]) == (
        "not_granted",
        False,
        "not_granted",
    )
    # Only the named entry moved.
    others = [(e["name"], e["invocable"]) for e in after.entries if e["name"] != "apps.frontmost"]
    assert others == [
        (e["name"], e["invocable"]) for e in before.entries if e["name"] != "apps.frontmost"
    ]
    health_after = node_health(owner)
    assert health_after["invocable_count"] == health_before["invocable_count"] - 1
    assert health_after["advertised_count"] == health_before["advertised_count"]
    # The server stops offering the read; nothing is sent to the node.
    outcome = services.invoke_read(
        node=node_row,
        capability="apps.frontmost",
        args={},
        workspace=node_row.workspace,
        actor=node_row.owner,
    )
    assert (outcome.status, outcome.refusal_code) == ("server_refused", "not_invocable")
    assert not link.frames_of("invoke")
    # Granted again: invocable again.
    link.send_frame(
        _frame(1, events.AVAILABILITY_CHANGED, [_availability("apps.frontmost", "granted")])
    )
    assert link.wait_for(lambda: len(link.frames_of("event.ack")) == 2)
    assert _entry(latest_snapshot(node_row), "apps.frontmost")["invocable"] is True
    assert node_health(owner)["invocable_count"] == health_before["invocable_count"]


def test_unchanged_unknown_and_malformed_items(link_node):
    link, node_row, _ = link_node(subprotocols=(V1,))
    rows = CapabilitySnapshot.objects.filter(node=node_row).count()
    batch = [
        _availability("apps.frontmost", "granted"),  # already granted
        _availability("no.such.capability", "granted"),
        _availability("apps.frontmost", "granted", version=99),
        {"name": "apps.frontmost", "version": FRONTMOST_VERSION, "availability": "maybe"},
        {"name": "apps.frontmost", "availability": "granted"},
        {**_availability("apps.frontmost", "granted"), "allow": True},
        {"name": "apps.frontmost", "version": True, "availability": "granted"},
    ]
    link.send_frame(_frame(1, events.AVAILABILITY_CHANGED, batch))
    assert link.wait_for(lambda: link.frames_of("event.ack"))
    ack = link.frames_of("event.ack")[0]
    assert (ack["stored"], ack["duplicate"], ack["dropped"]) == (0, 1, 6)
    assert CapabilitySnapshot.objects.filter(node=node_row).count() == rows


def test_a_contract_mismatch_stays_not_invocable_whatever_the_availability(link_node):
    catalogue = default_catalogue({"apps.frontmost": "not_granted"})
    for entry in catalogue:
        if entry["name"] == "apps.frontmost":
            entry["version"] = 7  # not the server's contract version
    link, node_row, _ = link_node(subprotocols=(V1,), catalogue=catalogue)
    assert _entry(latest_snapshot(node_row), "apps.frontmost")["reason"] == "version_mismatch"
    link.send_frame(
        _frame(1, events.AVAILABILITY_CHANGED, [_availability("apps.frontmost", "granted", 7)])
    )
    assert link.wait_for(lambda: link.frames_of("event.ack"))
    entry = _entry(latest_snapshot(node_row), "apps.frontmost")
    assert (entry["availability"], entry["invocable"], entry["reason"]) == (
        "granted",
        False,
        "version_mismatch",
    )


def test_unsupported_is_its_own_reason(link_node):
    link, node_row, _ = link_node(subprotocols=(V1,))
    link.send_frame(
        _frame(1, events.AVAILABILITY_CHANGED, [_availability("apps.frontmost", "unsupported")])
    )
    assert link.wait_for(lambda: link.frames_of("event.ack"))
    entry = _entry(latest_snapshot(node_row), "apps.frontmost")
    assert (entry["invocable"], entry["reason"]) == (False, "unsupported")


def test_a_reconnect_with_the_old_catalogue_does_not_inherit_the_changed_flags(gateway, paired):
    """The successor keeps the hello's hash; a later `hello` with that same
    catalogue (the owner granted it back while offline) is classified anew
    instead of reusing the changed row."""
    _, harness = gateway
    node_row, secret, keyring = paired()
    reference = reference_node(node_row, keyring)
    link = harness.connect(secret=secret, node=reference, subprotocols=(V1,))
    link.send_frame(
        _frame(1, events.AVAILABILITY_CHANGED, [_availability("apps.frontmost", "not_granted")])
    )
    assert link.wait_for(lambda: link.frames_of("event.ack"))
    assert _entry(latest_snapshot(node_row), "apps.frontmost")["invocable"] is False
    link.drop()
    assert link.wait_done()
    again = harness.connect(secret=secret, node=reference, subprotocols=(V1,))
    assert again.frames_of("hello.ack")
    assert "apps.frontmost" in again.frames_of("hello.ack")[0]["accepted"]
    assert _entry(latest_snapshot(node_row), "apps.frontmost")["invocable"] is True


def test_no_snapshot_drops_the_availability_batch(owner, paired):
    node_row, _, _ = paired()
    outcome = events.apply_availability_change(node_row, [_availability("apps.frontmost", "x")])
    assert (outcome.stored, outcome.dropped) == (0, 1)
    assert not CapabilitySnapshot.objects.filter(node=node_row).exists()
