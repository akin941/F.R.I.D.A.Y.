"""The HUD node line's mic state (Phase 11 brief D15, branch 7):
`health.node.voice` in `/hud/events`, read from the node row the
`voice.state` event wrote (through `nodes`, never `voice`). Each state
shows; a state older than two heartbeats, or a node that is offline, reads
`unknown`; a cut-off node (kill switch, revoked) reads `off`; another
owner's node never shows; a state change is a delta; nothing but codes and
counts crosses."""

import json
from datetime import timedelta

import pytest
from django.utils import timezone

from accounts import devices
from accounts.services import register_user
from hud.services import node_section
from hud.tests.conftest import PASSWORD
from nodes import bus
from nodes.models import Node
from nodes.protocol.messages import VOICE_STATE_STALE_SECONDS, VOICE_STATES
from nodes.tests.gateway_helpers import direct_node

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
    bus.mark_present(node.pk)
    return node


def _report(node, state="listening", *, age=0, **extra):
    fields = {
        "voice_state": state,
        "voice_mode": "always_on",
        "voice_session_open": True,
        "voice_state_at": timezone.now() - timedelta(seconds=age),
        "voice_wake_accepts_today": 4,
        "voice_wake_discards_today": 2,
        "voice_storm_mutes_today": 1,
        **extra,
    }
    Node.objects.filter(pk=node.pk).update(**fields)


def _voice(stream, workspace):
    return stream(workspace.id)[1]["health"]["node"]["voice"]


@pytest.mark.parametrize("state", VOICE_STATES)
def test_each_reported_state_shows(stream, workspace, node, state):
    _report(node, state)
    voice = _voice(stream, workspace)
    assert set(voice) == VOICE_KEYS
    assert voice["state"] == state
    assert voice["mode"] == "always_on" and voice["session_open"] is True
    assert (
        voice["wake_accepts_today"],
        voice["wake_discards_today"],
        voice["storm_mutes_today"],
    ) == (4, 2, 1)
    assert voice["at"] is not None


def test_a_node_that_never_reported_is_unknown(stream, workspace, node):
    voice = _voice(stream, workspace)
    assert voice["state"] == "unknown" and voice["at"] is None


def test_a_state_older_than_two_heartbeats_is_unknown(stream, workspace, node):
    _report(node, "listening", age=VOICE_STATE_STALE_SECONDS - 2)
    assert _voice(stream, workspace)["state"] == "listening"
    _report(node, "listening", age=VOICE_STATE_STALE_SECONDS + 1)
    voice = _voice(stream, workspace)
    assert voice["state"] == "unknown"
    assert voice["mode"] == "" and voice["session_open"] is False
    assert voice["wake_accepts_today"] == 0
    assert voice["at"] is not None  # when the server last heard it


def test_an_offline_node_is_unknown(stream, workspace, node):
    _report(node, "speaking")
    bus.clear_presence(node.pk)
    assert _voice(stream, workspace)["state"] == "unknown"


def test_a_cut_off_node_reads_off(stream, workspace, owner, node):
    _report(node, "listening")
    devices.engage_node_kill_switch(owner=owner, via="test")
    assert _voice(stream, workspace)["state"] == "off"
    devices.release_node_kill_switch(owner=owner, via="test")
    assert _voice(stream, workspace)["state"] == "listening"
    devices.revoke_device(device=node.device, actor=owner)
    assert _voice(stream, workspace)["state"] == "off"


def test_another_owners_node_state_never_shows(stream, workspace, node_ids):
    stranger = register_user(email="stranger-voice@test.dev", password=PASSWORD)
    theirs, _ = direct_node(stranger, name="Onların")
    node_ids.append(theirs.pk)
    bus.mark_present(theirs.pk)
    _report(theirs, "listening")
    node_block = stream(workspace.id)[1]["health"]["node"]
    assert node_block["status"] == "never_paired"
    assert node_block["voice"]["state"] == "unknown"


def test_a_state_change_is_a_delta(stream, workspace, node, fake_clock):
    _report(node, "armed_wake", voice_session_open=False)
    fake_clock.ticks = [lambda: _report(node, "listening")]
    frames = stream(workspace.id)
    assert frames[1]["health"]["node"]["voice"]["state"] == "armed_wake"
    delta = frames[2]
    assert delta["type"] == "delta" and set(delta) == {"type", "health"}
    assert delta["health"]["node"]["voice"]["state"] == "listening"
    assert delta["health"]["node"]["voice"]["session_open"] is True


def test_the_voice_line_carries_codes_and_counts_only(owner, node):
    _report(node, "thinking")
    line = node_section(owner)["voice"]
    body = json.dumps(line)
    assert set(line) == VOICE_KEYS
    assert str(node.pk) not in body
    for value in line.values():
        assert value is None or isinstance(value, (str, int, bool))
