"""The node's bounded event spool and the node's event upload (Phase 9 brief
D10, D13 item 7): <= 10 MiB, <= 24 h, mode 0600, oldest dropped first,
deleted only after ack; live events, spooled uploads one at a time."""

import json
import os
import stat

import pytest

from nodes.conformance import cases as C
from nodes.conformance.runner import build_reference_node
from nodes.protocol.messages import validate
from nodes.reference.confirm import FakeClock
from nodes.reference.spool import (
    MAX_SPOOL_AGE_SECONDS,
    MAX_SPOOL_BYTES,
    Spool,
    SpoolError,
)


def _event(n, pad=0):
    return {
        "hook_event_name": "Stop",
        "session_id": f"cc-{n}",
        "event_id": f"{n:032x}",
        "occurred_at": "2026-11-30T00:00:00+00:00",
        "cwd": "/Users/owner/work/acme" + "x" * pad,
    }


@pytest.fixture
def clock():
    return FakeClock(C.NOW)


def test_the_normative_limits():
    assert MAX_SPOOL_BYTES == 10 * 1024 * 1024
    assert MAX_SPOOL_AGE_SECONDS == 24 * 3600


def test_files_are_0600_in_a_0700_directory(tmp_path, clock):
    spool = Spool(tmp_path / "spool", clock)
    batch_id = spool.append([_event(1)])
    assert stat.S_IMODE(os.stat(tmp_path / "spool").st_mode) == 0o700
    assert stat.S_IMODE(os.stat(tmp_path / "spool" / f"{batch_id}.json").st_mode) == 0o600


def test_an_existing_loose_directory_is_tightened_and_a_symlink_refused(tmp_path, clock):
    loose = tmp_path / "loose"
    loose.mkdir(mode=0o755)
    Spool(loose, clock)
    assert stat.S_IMODE(os.stat(loose).st_mode) == 0o700
    os.symlink(loose, tmp_path / "link")
    with pytest.raises(SpoolError):
        Spool(tmp_path / "link", clock)
    (tmp_path / "file").write_text("x")
    with pytest.raises(SpoolError):
        Spool(tmp_path / "file", clock)


def test_oldest_batches_are_dropped_first_when_over_the_size_cap(tmp_path, clock):
    spool = Spool(tmp_path / "spool", clock, max_bytes=1000)
    ids = [spool.append([_event(n, pad=200)]) for n in range(6)]
    remaining = [batch.batch_id for batch in spool.pending()]
    assert remaining == ids[-len(remaining) :]
    assert ids[0] not in remaining
    count, size = spool.depth()
    assert count == len(remaining) and size <= 1000
    assert spool.dropped == len(ids) - len(remaining)


def test_a_batch_larger_than_the_whole_budget_is_refused(tmp_path, clock):
    spool = Spool(tmp_path / "spool", clock, max_bytes=300)
    assert spool.append([_event(1, pad=500)]) is None
    assert spool.depth() == (0, 0)


def test_batches_older_than_24_hours_are_dropped(tmp_path, clock):
    spool = Spool(tmp_path / "spool", clock)
    old = spool.append([_event(1)])
    clock.advance(23 * 3600)
    young = spool.append([_event(2)])
    clock.advance(3601)
    assert [b.batch_id for b in spool.pending()] == [young]
    assert old not in {b.batch_id for b in spool.pending()}


def test_events_only(tmp_path, clock):
    spool = Spool(tmp_path / "spool", clock)
    assert spool.append([]) is None
    assert spool.append(["not an event"]) is None
    assert spool.append({"events": []}) is None
    assert spool.append([_event(n) for n in range(51)]) is None
    assert spool.append([_event(n) for n in range(50)]) is not None


def test_a_batch_is_deleted_only_after_its_ack_and_survives_a_restart(tmp_path, clock):
    spool = Spool(tmp_path / "spool", clock)
    first = spool.append([_event(1)])
    second = spool.append([_event(2)])
    assert spool.first().batch_id == first
    assert spool.first().batch_id == first  # reading never deletes
    restarted = Spool(tmp_path / "spool", clock)
    assert [b.batch_id for b in restarted.pending()] == [first, second]
    assert restarted.ack(first) is True
    assert restarted.ack(first) is False
    assert restarted.ack("../../etc/passwd") is False
    third = restarted.append([_event(3)])
    assert third > second  # ids keep increasing across restarts
    assert [b.events[0]["session_id"] for b in restarted.pending()] == ["cc-2", "cc-3"]


def test_a_corrupt_batch_file_is_dropped(tmp_path, clock):
    spool = Spool(tmp_path / "spool", clock)
    good = spool.append([_event(1)])
    (tmp_path / "spool" / "000000000099.json").write_text("{nope")
    assert [b.batch_id for b in spool.pending()] == [good]


# --- the node's event upload ------------------------------------------------


def _node(tmp_path, home=None, *, spool=True):
    node = build_reference_node({"setup": {"now": C.NOW}}, home=home or tmp_path)
    if spool:
        node.spool = Spool(tmp_path / "spool", node.clock)
    return node


def _hello_ack(version=1):
    return json.dumps(
        {
            "v": version,
            "type": "hello.ack",
            "id": C._uuid4("hello-ack"),
            "node_id": C.NODE_ID,
            "heartbeat_seconds": 15,
            "rotate_after_days": 30,
            "server_time": C.NOW,
            "accepted": ["apps.frontmost"],
        }
    )


def _event_ack(event_id, version=1):
    return json.dumps(
        {
            "v": version,
            "type": "event.ack",
            "id": C._uuid4("ack-" + event_id),
            "event_id": event_id,
            "stored": 1,
            "duplicate": 0,
            "dropped": 0,
        }
    )


def test_a_live_event_is_sent_after_hello_ack_and_never_answered(tmp_path):
    node = _node(tmp_path)
    node.connect(1)
    assert node.relay_hook_event(_event(1)) == []  # before hello.ack: spooled
    assert node.spool.depth()[0] == 1
    (upload,) = node.receive(_hello_ack())
    assert upload["type"] == "event" and upload["spooled"] is True
    assert upload["kind"] == "claude_code.hook" and upload["batch"] == [_event(1)]
    assert node.receive(_event_ack(upload["id"])) == []
    assert node.spool.depth() == (0, 0)
    (live,) = node.relay_hook_event(_event(2))
    assert live["spooled"] is False and live["batch"] == [_event(2)]
    validate(live, "node_to_server", 1)
    assert node.receive(_event_ack(live["id"])) == []


def test_spooled_batches_upload_in_order_one_in_flight(tmp_path):
    node = _node(tmp_path)
    for n in range(3):
        node.relay_hook_event(_event(n))  # offline: spooled
    assert node.spool.depth()[0] == 3
    node.connect(1)
    (first,) = node.receive(_hello_ack())
    assert first["batch"][0]["session_id"] == "cc-0"
    assert node.relay_hook_event(_event(9)) == []  # one in flight: spooled behind
    (second,) = node.receive(_event_ack(first["id"]))
    assert second["batch"][0]["session_id"] == "cc-1"
    (third,) = node.receive(_event_ack(second["id"]))
    (fourth,) = node.receive(_event_ack(third["id"]))
    assert fourth["batch"][0]["session_id"] == "cc-9"
    assert node.receive(_event_ack(fourth["id"])) == []
    assert node.spool.depth() == (0, 0)


def test_a_stray_ack_deletes_nothing(tmp_path):
    node = _node(tmp_path)
    node.relay_hook_event(_event(1))
    node.connect(1)
    (upload,) = node.receive(_hello_ack())
    assert node.receive(_event_ack(C._uuid4("other"))) == []
    assert node.stray_event_acks == 1
    assert node.spool.depth()[0] == 1


def test_an_unacked_upload_stays_spooled_and_is_resent_after_reconnect(tmp_path):
    node = _node(tmp_path)
    node.relay_hook_event(_event(1))
    node.connect(1)
    (upload,) = node.receive(_hello_ack())
    node.disconnect()
    assert node.spool.depth()[0] == 1
    node.connect(1)
    (again,) = node.receive(_hello_ack())
    assert again["batch"] == upload["batch"] and again["id"] != upload["id"]


def test_a_live_event_not_acked_before_the_socket_drops_is_spooled(tmp_path):
    node = _node(tmp_path)
    node.connect(1)
    node.receive(_hello_ack())
    (live,) = node.relay_hook_event(_event(1))
    node.disconnect()
    assert [b.events for b in node.spool.pending()] == [[_event(1)]]
    node.connect(1)
    (upload,) = node.receive(_hello_ack())
    assert upload["spooled"] is True and upload["batch"] == [_event(1)]


def test_without_a_spool_offline_events_are_dropped_and_counted(tmp_path):
    node = _node(tmp_path, spool=False)
    assert node.relay_hook_event(_event(1)) == []
    assert node.events_dropped == 1


def test_heartbeats_carry_the_spool_depth(tmp_path):
    node = _node(tmp_path)
    node.relay_hook_event(_event(1))
    node.relay_hook_event(_event(2))
    node.connect(1)
    (beat,) = node.heartbeat()
    count, size = node.spool.depth()
    assert beat["spool_count"] == count == 2 and beat["spool_bytes"] == size > 0


def test_event_frames_work_on_a_v2_session(tmp_path):
    node = _node(tmp_path)
    node.connect(2)
    node.receive(_hello_ack(2))
    (live,) = node.relay_hook_event(_event(1))
    validate(live, "node_to_server", 2)
    assert node.receive(_event_ack(live["id"], 2)) == []
