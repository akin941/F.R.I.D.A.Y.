"""The node -> server `voice.state` event (Phase 11 brief D15, branch 7):
the reference node relays a scripted voice-child state, the gateway stores
it on the node row (coalesced: two in the same second -> one write), the
HUD reads it, a bad item or a v1 frame is a frame error, the kind never
reaches a sink or the invoke bus, a revoked node's or another node's frame
never writes a row, and the protocol constants match the voice child's
`friday-voice/v1` `state` schema."""

import json
import time
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from django.utils import timezone

from accounts import devices
from nodes import bus, events
from nodes.models import Node
from nodes.protocol.messages import (
    EVENT_KINDS,
    EVENT_KINDS_BY_VERSION,
    MAX_VOICE_COUNTER,
    POLICY_SHAPED,
    SERVER_TO_NODE,
    SERVER_TO_NODE_V2,
    VOICE_MODES,
    VOICE_STATE_COUNTERS,
    VOICE_STATE_ITEM_KEYS,
    VOICE_STATE_KIND,
    VOICE_STATES,
    ProtocolError,
    load_schema,
    validate,
)
from nodes.selectors import node_health
from nodes.tests.gateway_harness import V1, V2

pytestmark = pytest.mark.django_db(transaction=True)

REPO = Path(__file__).resolve().parents[3]
CHILD_STATE_SCHEMA = (
    REPO
    / "node"
    / "friday-voice"
    / "friday_voice"
    / "protocol"
    / "schemas"
    / "v1"
    / "child_to_host"
    / "state.schema.json"
)

LISTENING = {
    "state": "listening",
    "mode": "always_on",
    "session_open": True,
    "wake_accepts_today": 3,
    "wake_discards_today": 1,
    "storm_mutes_today": 0,
}


def _item(**overrides):
    return {**LISTENING, "at": int(time.time()), **overrides}


def _frame(version, batch, *, kind=VOICE_STATE_KIND, spooled=False):
    return {
        "v": version,
        "type": "event",
        "id": str(uuid.uuid4()),
        "kind": kind,
        "batch": batch,
        "spooled": spooled,
    }


def _row(node_row):
    return Node.objects.get(pk=node_row.pk)


# --- the protocol -------------------------------------------------------


def test_the_kind_is_v2_only_and_the_server_to_node_set_is_untouched():
    assert EVENT_KINDS == ("claude_code.hook", "grant.availability_changed")
    assert EVENT_KINDS_BY_VERSION[1] == EVENT_KINDS
    assert EVENT_KINDS_BY_VERSION[2] == (*EVENT_KINDS, VOICE_STATE_KIND)
    assert load_schema("event", 1)["properties"]["kind"]["enum"] == list(EVENT_KINDS)
    assert load_schema("event", 2)["properties"]["kind"]["enum"] == list(EVENT_KINDS_BY_VERSION[2])
    assert SERVER_TO_NODE_V2 is SERVER_TO_NODE
    assert SERVER_TO_NODE == {"hello.ack", "heartbeat.ack", "invoke", "event.ack", "bye"}


def test_the_item_schema_is_the_constants():
    item = load_schema("event", 2)["$defs"]["voice_state_item"]
    assert set(item["required"]) == set(item["properties"]) == VOICE_STATE_ITEM_KEYS
    assert item["additionalProperties"] is False
    assert item["properties"]["state"]["enum"] == list(VOICE_STATES)
    assert item["properties"]["mode"]["enum"] == list(VOICE_MODES)
    for counter in VOICE_STATE_COUNTERS:
        assert item["properties"][counter] == {
            "type": "integer",
            "minimum": 0,
            "maximum": MAX_VOICE_COUNTER,
        }
    for name in (*item["properties"], VOICE_STATE_KIND):
        assert not POLICY_SHAPED.search(name), name


@pytest.mark.skipif(not CHILD_STATE_SCHEMA.exists(), reason="no node/friday-voice checkout")
def test_the_states_modes_and_counters_match_the_voice_childs_state_frame():
    child = json.loads(CHILD_STATE_SCHEMA.read_text(encoding="utf-8"))["properties"]
    assert child["state"]["enum"] == list(VOICE_STATES)
    assert child["mode"]["enum"] == list(VOICE_MODES)
    for counter in VOICE_STATE_COUNTERS:
        assert child[counter]["maximum"] == MAX_VOICE_COUNTER


def test_a_valid_item_validates_on_v2_only():
    validate(_frame(2, [_item()]), "node_to_server", 2)
    with pytest.raises(ProtocolError) as exc:
        validate(_frame(1, [_item()]), "node_to_server", 1)
    assert exc.value.code == "schema"


@pytest.mark.parametrize(
    "batch, spooled",
    [
        ([_item(state="recording")], False),
        ([_item(mode="remote")], False),
        ([_item(wake_accepts_today=MAX_VOICE_COUNTER + 1)], False),
        ([_item(storm_mutes_today=-1)], False),
        ([_item(at=4_102_444_801)], False),
        ([_item(session_open="yes")], False),
        ([{**_item(), "transcript": "Hey Friday, bugün ne var?"}], False),
        ([{k: v for k, v in _item().items() if k != "at"}], False),
        ([_item(), _item()], False),
        ([_item()], True),
        ([_item(wake_accepts_today=1.0)], False),
    ],
    ids=[
        "unknown-state",
        "unknown-mode",
        "oversize-counter",
        "negative-counter",
        "at-beyond-2100",
        "non-bool-session",
        "extra-text-key",
        "missing-key",
        "two-items",
        "spooled",
        "float-counter",
    ],
)
def test_a_bad_voice_item_is_a_schema_error(batch, spooled):
    with pytest.raises(ProtocolError) as exc:
        validate(_frame(2, batch, spooled=spooled), "node_to_server", 2)
    assert exc.value.code == "schema"


def test_other_kinds_keep_their_opaque_items():
    validate(
        _frame(2, [{"anything": "goes", "within": 32}], kind="claude_code.hook"),
        "node_to_server",
        2,
    )


# --- through the gateway --------------------------------------------------


def test_the_reference_node_relays_a_scripted_state_and_the_hud_shows_it(link_node, owner):
    link, node_row, reference = link_node(subprotocols=(V2, V1))
    assert reference.version == 2
    sent = link.node_call(lambda node: node.relay_voice_state(LISTENING))
    assert len(sent) == 1 and sent[0]["kind"] == VOICE_STATE_KIND
    assert link.wait_for(lambda: link.frames_of("event.ack"))
    ack = link.frames_of("event.ack")[0]
    assert (ack["event_id"], ack["stored"], ack["duplicate"], ack["dropped"]) == (
        sent[0]["id"],
        1,
        0,
        0,
    )
    row = _row(node_row)
    assert (row.voice_state, row.voice_mode, row.voice_session_open) == (
        "listening",
        "always_on",
        True,
    )
    assert (
        row.voice_wake_accepts_today,
        row.voice_wake_discards_today,
        row.voice_storm_mutes_today,
    ) == (3, 1, 0)
    assert row.voice_state_at is not None
    # The ack of a voice state is not a stray ack and frees nothing.
    assert reference.stray_event_acks == 0 and reference._event_inflight is None
    # The HUD's node line reads exactly this block (`hud.services.node_section`).
    line = node_health(owner)
    assert line["status"] == "connected"
    assert line["voice"]["state"] == "listening" and line["voice"]["session_open"] is True


def test_two_states_in_the_same_second_are_one_write(gateway, link_node):
    app, _ = gateway
    link, node_row, _ = link_node(subprotocols=(V2,))
    link.send_frame(_frame(2, [_item(state="listening")]))
    link.send_frame(_frame(2, [_item(state="thinking")]))
    assert link.wait_for(lambda: len(link.frames_of("event.ack")) == 2)
    first, second = link.frames_of("event.ack")
    assert (first["stored"], first["duplicate"]) == (1, 0)
    assert (second["stored"], second["duplicate"], second["dropped"]) == (0, 1, 0)
    assert _row(node_row).voice_state == "listening"
    assert app.counters["event_voice_state"] == 1
    assert app.counters["event_voice_state_coalesced"] == 1
    # A second later the next state is written.
    Node.objects.filter(pk=node_row.pk).update(voice_state_at=timezone.now() - timedelta(seconds=2))
    link.send_frame(_frame(2, [_item(state="speaking")]))
    assert link.wait_for(lambda: len(link.frames_of("event.ack")) == 3)
    assert link.frames_of("event.ack")[2]["stored"] == 1
    assert _row(node_row).voice_state == "speaking"


@pytest.mark.parametrize(
    "batch",
    [
        [_item(state="recording")],
        [_item(wake_accepts_today=MAX_VOICE_COUNTER + 1)],
        [{**_item(), "transcript": "terminali aç"}],
    ],
    ids=["unknown-state", "oversize", "extra-key"],
)
def test_a_bad_item_is_a_frame_error_and_writes_nothing(gateway, link_node, batch):
    app, _ = gateway
    link, node_row, _ = link_node(subprotocols=(V2,))
    link.send_frame(_frame(2, batch))
    assert link.wait_for(lambda: app.counters["frame_errors"] == 1)
    assert not link.frames_of("event.ack")
    assert _row(node_row).voice_state == "" and _row(node_row).voice_state_at is None


def test_on_v1_the_kind_is_a_frame_error_and_the_reference_node_never_sends_it(gateway, link_node):
    app, _ = gateway
    link, node_row, reference = link_node(subprotocols=(V1,))
    assert reference.version == 1
    assert link.node_call(lambda node: node.relay_voice_state(LISTENING)) == []
    assert reference.voice_states_dropped == 1
    link.send_frame(_frame(1, [_item()]))
    assert link.wait_for(lambda: app.counters["frame_errors"] == 1)
    assert not link.frames_of("event.ack")
    assert _row(node_row).voice_state == ""


def test_the_kind_never_takes_a_sink_and_never_reaches_one(gateway, link_node, monkeypatch):
    with pytest.raises(ValueError):
        events.register_sink(VOICE_STATE_KIND, lambda **kwargs: {})
    assert VOICE_STATE_KIND not in events.SINK_KINDS
    calls = []
    events.register_sink(events.HOOK_EVENTS, lambda **kwargs: calls.append(kwargs) or {})
    pushed = []
    monkeypatch.setattr(bus, "push_invoke", lambda *a, **k: pushed.append((a, k)))
    link, node_row, _ = link_node(subprotocols=(V2,))
    link.send_frame(_frame(2, [_item()]))
    assert link.wait_for(lambda: link.frames_of("event.ack"))
    time.sleep(0.2)
    assert calls == [] and pushed == []
    assert bus._run(lambda c: c.llen(bus.inbox_key(node_row.pk))) == 0
    assert {frame["type"] for frame in link.server_frames} == {"hello.ack", "event.ack"}


def test_a_frame_writes_only_the_sending_nodes_row(link_node):
    link_a, row_a, _ = link_node(subprotocols=(V2,), name="MacBook")
    _, row_b, _ = link_node(subprotocols=(V2,), name="iMac")
    link_a.send_frame(_frame(2, [_item(state="speaking")]))
    assert link_a.wait_for(lambda: link_a.frames_of("event.ack"))
    assert _row(row_a).voice_state == "speaking"
    assert _row(row_b).voice_state == "" and _row(row_b).voice_state_at is None


def test_a_revoked_node_cannot_report_a_state(gateway, owner, paired):
    _, harness = gateway
    from nodes.tests.gateway_harness import reference_node

    node_row, secret, keyring = paired()
    devices.revoke_device(device=node_row.device, actor=owner)
    reference = reference_node(node_row, keyring)
    link = harness.connect(secret=secret, node=reference, subprotocols=(V2,))
    assert link.wait_closed() or not link.accepted
    assert not link.frames_of("hello.ack")
    link.node_call(lambda node: node.relay_voice_state(LISTENING))
    time.sleep(0.2)
    assert _row(node_row).voice_state == ""


def test_the_kill_switch_ends_the_session_and_the_hud_reads_off(gateway, owner, link_node):
    link, node_row, _ = link_node(subprotocols=(V2,))
    link.node_call(lambda node: node.relay_voice_state(LISTENING))
    assert link.wait_for(lambda: link.frames_of("event.ack"))
    devices.engage_node_kill_switch(owner=owner, via="test")
    assert link.wait_closed(5)
    assert node_health(owner)["voice"]["state"] == "off"
    devices.release_node_kill_switch(owner=owner, via="test")


# --- the server's defensive second look ----------------------------------


def test_apply_voice_state_refuses_what_the_schema_would(owner, paired):
    node_row, _, _ = paired()
    bad = [
        [],
        [_item(), _item()],
        ["listening"],
        [_item(state="recording")],
        [_item(session_open=1)],
        [_item(wake_accepts_today=True)],
        [_item(at=-1)],
        [{**_item(), "text": "x"}],
    ]
    for batch in bad:
        outcome = events.apply_voice_state(node_row, batch)
        assert outcome.stored == 0 and outcome.dropped == len(batch), batch
    assert _row(node_row).voice_state_at is None
    now = timezone.now()
    assert events.apply_voice_state(node_row, [_item()], now=now).stored == 1
    assert events.apply_voice_state(node_row, [_item()], now=now).duplicate == 1
    later = now + timedelta(seconds=1)
    assert events.apply_voice_state(node_row, [_item(state="off")], now=later).stored == 1
    assert _row(node_row).voice_state == "off"


# --- the reference node's relay -------------------------------------------


def test_the_reference_node_coalesces_refreshes_and_resends_after_a_reconnect(link_node):
    link, _, reference = link_node(subprotocols=(V2,))
    first = link.node_call(lambda node: node.relay_voice_state(LISTENING))
    second = link.node_call(lambda node: node.relay_voice_state({**LISTENING, "state": "thinking"}))
    assert len(first) == 1 and second == []  # coalesced inside the second
    assert reference._voice_pending is True
    reference.clock.advance(1)
    flushed = link.node_call(lambda node: node.tick())
    voice = [frame for frame in flushed if frame.get("kind") == VOICE_STATE_KIND]
    assert [frame["batch"][0]["state"] for frame in voice] == ["thinking"]
    # The heartbeat re-sends the current state, as of now.
    reference.clock.advance(15)
    refreshed = link.node_call(lambda node: node.tick())
    kinds = [(frame["type"], frame.get("kind")) for frame in refreshed]
    assert ("heartbeat", None) in kinds and ("event", VOICE_STATE_KIND) in kinds
    voice = [frame for frame in refreshed if frame.get("kind") == VOICE_STATE_KIND]
    assert voice[0]["batch"][0]["at"] == reference.clock.now()
    # A dropped socket spools nothing; the reconnect re-sends the state.
    link.drop()
    assert reference._event_inflight is None
    assert reference.connect(2)[0]["type"] == "hello"
    assert reference._voice_pending is True


# --- the conformance cases catch a bad node (negative controls) ----------


@pytest.fixture(scope="module")
def fixture_home(tmp_path_factory):
    from nodes.reference.fixtures.factory import build_fixture_home

    return build_fixture_home(tmp_path_factory.mktemp("voice-conformance-home"))


def _case(name):
    from nodes.conformance import cases as C

    return next(c for v in C.all_cases().values() for c in v if c["name"] == name)


def _careless(self, state):
    """A node that relays whatever the child said, on any version."""
    frame = {
        "v": self.version,
        "type": "event",
        "id": self.id_factory(),
        "kind": VOICE_STATE_KIND,
        "batch": [{**state, "at": self.clock.now()}],
        "spooled": False,
    }
    self.sent.append(frame)
    return [frame]


@pytest.mark.parametrize(
    "name",
    [
        "v1_voice_state_never_sent",
        "v2_voice_state_unknown_state",
        "v2_voice_state_oversize_counter",
        "v2_voice_state_extra_key",
        "v2_voice_state_unknown_mode",
    ],
)
def test_a_node_that_relays_unchecked_items_fails_the_cases(name, fixture_home, monkeypatch):
    from nodes.conformance.runner import run_case_in_process
    from nodes.reference import node as node_module

    monkeypatch.setattr(node_module.ReferenceNode, "relay_voice_state", _careless)
    result = run_case_in_process(_case(name), home=fixture_home)
    assert not result.passed
    assert any("expected 0 frames" in failure for failure in result.failures)
    assert any("gateway frame errors" in failure for failure in result.failures)


def test_a_node_that_does_not_coalesce_fails_the_case(fixture_home, monkeypatch):
    from nodes.conformance.runner import run_case_in_process
    from nodes.reference import node as node_module

    monkeypatch.setattr(node_module, "VOICE_STATE_MIN_INTERVAL_SECONDS", 0)
    result = run_case_in_process(_case("v2_voice_state_coalesced"), home=fixture_home)
    assert not result.passed
