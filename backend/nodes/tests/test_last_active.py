"""Phase 13 D6: `Node.last_active_at` writers. A node-relayed hook batch
bumps it, a heartbeat or another event kind never does, at most one write a
minute per node, one node's events never bump another's, and a click on the
node's own confirmation window (approved or denied) bumps it while `shown`
does not. The `node_voice` transcription writer is tested end to end in
`config/tests/test_node_voice_last_active.py`."""

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from nodes import events, presence, services
from nodes.activity import LAST_ACTIVE_MIN_INTERVAL_SECONDS, mark_node_active
from nodes.models import Node, NodeActionExecution
from nodes.tests.two_nodes import cleanup, make_two_nodes

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(owner, personal):
    world = make_two_nodes(owner, personal)
    yield world
    cleanup(world)


def stamp(node):
    return Node.objects.values_list("last_active_at", flat=True).get(pk=node.pk)


def hook_frame(n=1):
    return {
        "kind": events.HOOK_EVENTS,
        "batch": [{"event_id": str(uuid.uuid4()), "hook_event_name": "Stop"} for _ in range(n)],
    }


@pytest.fixture
def sink():
    calls = []

    def fake(*, user, bound_workspace_id, events):
        calls.append((user.pk, bound_workspace_id, len(events)))
        return {"stored": len(events)}

    events.register_sink(events.HOOK_EVENTS, fake)
    yield calls
    events.reset_sinks()


def test_a_relayed_hook_batch_bumps_its_node_only(world, sink):
    assert stamp(world.mac) is None and stamp(world.desk) is None
    outcome = events.handle_event(node=world.mac, frame=hook_frame(2))
    assert outcome.stored == 2
    assert stamp(world.mac) is not None
    assert stamp(world.desk) is None


def test_an_empty_batch_or_no_sink_writes_nothing(world, sink):
    events.handle_event(node=world.mac, frame=hook_frame(0))
    assert stamp(world.mac) is None
    events.reset_sinks()
    events.handle_event(node=world.desk, frame=hook_frame(1))  # acked dropped
    assert stamp(world.desk) is None


def test_other_event_kinds_and_heartbeats_never_count(world, sink):
    events.handle_event(
        node=world.mac,
        frame={
            "kind": events.AVAILABILITY_CHANGED,
            "batch": [{"name": "git.status", "version": 1, "availability": "not_granted"}],
        },
    )
    presence.record_hello(world.mac, node_version="1.0", os_version="15")
    presence.record_heartbeat(world.mac, spool_count=0, spool_bytes=0)
    assert stamp(world.mac) is None


def test_at_most_one_write_a_minute(world):
    start = timezone.now()
    assert mark_node_active(world.mac.pk, now=start)
    assert not mark_node_active(world.mac.pk, now=start + timedelta(seconds=30))
    assert stamp(world.mac) == start
    later = start + timedelta(seconds=LAST_ACTIVE_MIN_INTERVAL_SECONDS + 1)
    assert mark_node_active(world.mac.pk, now=later)
    assert stamp(world.mac) == later


@pytest.mark.parametrize("bad", [None, "not-a-uuid", str(uuid.uuid4()), 7])
def test_an_unknown_id_writes_nothing(world, bad):
    assert not mark_node_active(bad)


def _sent_execution(node):
    return NodeActionExecution.objects.create(
        workspace=node.workspace,
        node=node,
        capability="apps.open",
        capability_version=1,
        capability_class="confirm",
        args_sha256="0" * 64,
        status=NodeActionExecution.Status.SENT,
        trace_id=uuid.uuid4().hex,
        invocation_id=uuid.uuid4(),
        actor=node.owner,
    )


def test_a_shown_window_is_not_a_click(world):
    execution = _sent_execution(world.mac)
    services.record_gated_progress(
        node_id=world.mac.pk, invocation_id=execution.invocation_id, confirmation="shown"
    )
    assert stamp(world.mac) is None


@pytest.mark.parametrize("state", ["approved", "denied"])
def test_a_click_on_the_nodes_window_bumps_it(world, state):
    execution = _sent_execution(world.mac)
    services.record_gated_progress(
        node_id=world.mac.pk, invocation_id=execution.invocation_id, confirmation=state
    )
    assert stamp(world.mac) is not None
    assert stamp(world.desk) is None


def test_a_result_after_a_click_bumps_it(world):
    execution = _sent_execution(world.mac)
    services.record_gated_progress(
        node_id=world.mac.pk,
        invocation_id=execution.invocation_id,
        result={"status": "refused", "refusal_code": "denied", "confirm_state": "denied"},
    )
    assert stamp(world.mac) is not None


def test_another_nodes_frame_cannot_bump_this_one(world):
    """The row is found by invocation id AND node id: Masaüstü reporting
    MacBook's invocation moves nothing and bumps nobody."""
    execution = _sent_execution(world.mac)
    assert (
        services.record_gated_progress(
            node_id=world.desk.pk, invocation_id=execution.invocation_id, confirmation="approved"
        )
        is None
    )
    assert stamp(world.mac) is None
    # Masaüstü's own (absent) execution: nothing written for it either.
    assert stamp(world.desk) is None
