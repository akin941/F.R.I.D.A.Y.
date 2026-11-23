"""The node's Unix-socket hook relay (Phase 9 brief D10, D13 item 7): socket
0600 in a 0700 directory, peer uid checked, oversized input dropped, the
friday-hook event shape, offline events spooled. The reference for M3."""

import json
import os
import socket
import stat
import sys
import threading
import time

import pytest

from nodes.conformance import cases as C
from nodes.conformance.runner import build_reference_node
from nodes.reference import hook_relay
from nodes.reference.confirm import FakeClock
from nodes.reference.hook_relay import (
    MAX_INPUT_BYTES,
    HookRelay,
    RelayError,
    build_hook_event,
    relay_stdin,
)
from nodes.reference.spool import Spool

SECRET = "sk-" + "proj-HookFAKE0123456789abcdefGHIJKLmnopQRstu"


def _payload(**extra):
    return {
        "hook_event_name": "Notification",
        "session_id": "cc-acme-1",
        "cwd": "/Users/owner/work/acme",
        "notification_type": "permission_prompt",
        "message": f"Claude needs permission; key {SECRET}",
        "transcript_path": "/Users/owner/.claude/projects/x.jsonl",
        "prompt": "PROMPT-PHRASE",
        **extra,
    }


@pytest.fixture
def socket_dir(tmp_path):
    # AF_UNIX paths are short (108 bytes); pytest's tmp_path can be long.
    path = f"/tmp/fr-{os.getpid()}-{id(tmp_path) % 100000}"
    yield path
    if os.path.isdir(path):
        for name in os.listdir(path):
            os.unlink(os.path.join(path, name))
        os.rmdir(path)


def _relay(socket_dir, delivered, **kwargs):
    kwargs.setdefault("clock", FakeClock(C.NOW))
    return HookRelay(
        os.path.join(socket_dir, "hook.sock"), deliver=delivered.append, **kwargs
    ).start()


def _send(path, data: bytes):
    thread = threading.Thread(target=relay_stdin, args=(path, data))
    thread.start()
    return thread


def test_socket_is_0600_in_a_0700_directory(socket_dir):
    relay = _relay(socket_dir, [])
    try:
        assert stat.S_IMODE(os.stat(socket_dir).st_mode) == 0o700
        info = os.stat(relay.path)
        assert stat.S_ISSOCK(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
    finally:
        relay.close()
    assert not os.path.exists(relay.path)


def test_a_hook_event_is_relayed_allow_listed_and_scrubbed(socket_dir):
    delivered = []
    relay = _relay(socket_dir, delivered, id_factory=lambda: "e" * 32)
    try:
        sender = _send(relay.path, json.dumps(_payload()).encode())
        assert relay.handle_one(timeout=5) == "relayed"
        sender.join(5)
    finally:
        relay.close()
    (event,) = delivered
    assert set(event) == {
        "hook_event_name",
        "session_id",
        "cwd",
        "notification_type",
        "message",
        "occurred_at",
        "event_id",
    }
    assert SECRET not in event["message"] and "[SECRET:openai_key]" in event["message"]
    assert event["occurred_at"] == "2026-11-30T00:53:25+00:00"
    assert event["event_id"] == "e" * 32


def test_the_event_shape_matches_friday_hook():
    """The same payload gives the same event as friday-hook (so the server
    dedupes an event relayed by both on its event_id)."""
    from datetime import UTC, datetime

    from integrations.cli.friday_hook import build_event

    now = datetime.fromtimestamp(C.NOW, UTC)
    for payload in (_payload(), _payload(hook_event_name="Stop", reason="x" * 3000), {"a": 1}):
        ours = build_hook_event(payload, now=C.NOW, event_id="f" * 32)
        theirs = build_event(payload, now=now, event_id="f" * 32)
        assert ours == theirs


def test_a_connection_from_another_uid_is_refused_before_reading(socket_dir):
    delivered = []
    relay = _relay(socket_dir, delivered, peer_uid=lambda conn: os.getuid() + 1)
    try:
        sender = _send(relay.path, json.dumps(_payload()).encode())
        assert relay.handle_one(timeout=5) == "peer_refused"
        sender.join(5)
    finally:
        relay.close()
    assert delivered == []
    assert relay.outcomes == {"peer_refused": 1}


def test_a_failing_peer_credential_check_fails_closed(socket_dir):
    def broken(conn):
        raise OSError("no peer credentials")

    delivered = []
    relay = _relay(socket_dir, delivered, peer_uid=broken)
    try:
        sender = _send(relay.path, json.dumps(_payload()).encode())
        assert relay.handle_one(timeout=5) == "peer_refused"
        sender.join(5)
    finally:
        relay.close()
    assert delivered == []


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="SO_PEERCRED is Linux")
def test_so_peercred_reads_the_real_peer_uid():
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    with left, right:
        assert hook_relay.so_peercred_uid(left) == os.getuid()


def test_oversized_input_is_dropped_whole(socket_dir):
    delivered = []
    relay = _relay(socket_dir, delivered)
    try:
        big = json.dumps(_payload(message="x" * (MAX_INPUT_BYTES + 10))).encode()
        sender = _send(relay.path, big)
        assert relay.handle_one(timeout=5) == "oversized"
        sender.join(5)
    finally:
        relay.close()
    assert delivered == []


@pytest.mark.parametrize(
    ("data", "outcome"),
    [
        (b"{nope", "malformed"),
        (b"\xff\xfe", "malformed"),
        (json.dumps({"hook_event_name": "PreToolUse"}).encode(), "ignored"),
        (json.dumps(["SessionStart"]).encode(), "ignored"),
    ],
)
def test_malformed_and_unknown_events_are_dropped(socket_dir, data, outcome):
    delivered = []
    relay = _relay(socket_dir, delivered)
    try:
        sender = _send(relay.path, data)
        assert relay.handle_one(timeout=5) == outcome
        sender.join(5)
    finally:
        relay.close()
    assert delivered == []


def test_an_existing_non_socket_path_is_refused(socket_dir):
    os.makedirs(socket_dir, mode=0o700)
    with open(os.path.join(socket_dir, "hook.sock"), "w") as handle:
        handle.write("not a socket")
    with pytest.raises(RelayError):
        _relay(socket_dir, [])


def test_a_symlinked_socket_directory_is_refused(socket_dir, tmp_path):
    os.symlink(tmp_path, socket_dir)
    try:
        with pytest.raises(RelayError):
            _relay(socket_dir, [])
    finally:
        os.unlink(socket_dir)


def test_the_cli_exits_0_quickly_when_the_node_is_down(tmp_path):
    started = time.monotonic()
    assert relay_stdin(tmp_path / "missing.sock", b"{}") == 0
    assert relay_stdin("", b"{}") == 0
    assert time.monotonic() - started < 3


def test_relayed_events_reach_the_node_live_or_through_the_spool(socket_dir, tmp_path):
    node = build_reference_node({"setup": {"now": C.NOW}}, home=tmp_path)
    node.spool = Spool(tmp_path / "spool", node.clock)
    sent = []
    relay = _relay(socket_dir, [], clock=node.clock)
    relay.deliver = lambda event: sent.extend(node.relay_hook_event(event))
    try:
        sender = _send(relay.path, json.dumps(_payload()).encode())
        assert relay.handle_one(timeout=5) == "relayed"
        sender.join(5)
        assert sent == [] and node.spool.depth()[0] == 1  # offline: spooled
        node.connect(1)
        hello_ack = {
            "v": 1,
            "type": "hello.ack",
            "id": C._uuid4("relay-hello-ack"),
            "node_id": C.NODE_ID,
            "heartbeat_seconds": 15,
            "rotate_after_days": 30,
            "server_time": C.NOW,
            "accepted": [],
        }
        (upload,) = node.receive(json.dumps(hello_ack))
        assert upload["spooled"] is True
        assert SECRET not in json.dumps(upload) and "PROMPT-PHRASE" not in json.dumps(upload)
    finally:
        relay.close()
