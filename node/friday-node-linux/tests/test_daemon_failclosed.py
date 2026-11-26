"""The daemon fails closed (D10): server gone -> no invoke served, hook
events spooled within the bounds, reconnect backoff 1 s -> 60 s with
jitter (fake clock); a revocation ends it; a SIGHUP reload swaps the policy
between sessions only."""

import json
import os
import random
import socket
import stat
import time

import pytest

from friday_node_linux import daemon as daemon_module
from friday_node_linux.daemon import BACKOFF_MAX_SECONDS, Backoff, Daemon, os_version
from friday_node_linux.policy import LinuxPolicy

from .test_credential import make_credential

HOOK_EVENT = {"hook_event_name": "Stop", "session_id": "cc-acme-1", "cwd": "/tmp/x"}


class FakeTime:
    def __init__(self):
        self.now = 1_000.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeClock:
    def __init__(self, start=1_796_000_000):
        self.value = start

    def now(self):
        return int(self.value)


class FakeClient:
    """Stands in for `PinnedNodeClient`: `script(client)` runs at start."""

    def __init__(self, node, fake_time, script):
        self.node = node
        self.fake_time = fake_time
        self.script = script
        self.error = None
        self.rejected = False
        self.accepted_at = None
        self.until = None
        self.stopped = False
        self._alive = False

    def start(self):
        self._alive = True
        self.script(self)
        return self

    @property
    def alive(self):
        if self.until is not None and self.fake_time.now >= self.until:
            self._end("socket_lost")
        return self._alive

    def _end(self, reason):
        if self._alive:
            self._alive = False
            self.node.disconnect(reason)

    def stop(self):
        self.stopped = True
        self._end("socket_lost")

    def call(self, fn):
        return fn(self.node)


def server_gone(client):
    client.error = ConnectionRefusedError()
    client._end("transport_error")


def make_daemon(tmp_path, home, tls, script, *, fake_time=None, clock=None, **kwargs):
    fake_time = fake_time or FakeTime()

    def factory(node):
        return FakeClient(node, fake_time, script)

    d = Daemon(
        credential=make_credential(tls),
        policy_path=tmp_path / "policy.json",
        home=home,
        spool_dir=tmp_path / "state" / "spool",
        hook_socket=tmp_path / "run" / "hook.sock",
        clock=clock or FakeClock(),
        sleep=fake_time.sleep,
        monotonic=fake_time.monotonic,
        rng=random.Random(7),
        client_factory=factory,
        **kwargs,
    )
    return d, fake_time


def test_backoff_grows_from_1_to_60_seconds_with_jitter():
    backoff = Backoff(random.Random(3))
    delays = [backoff.next_delay() for _ in range(12)]
    assert delays[0] == 1  # attempt 0: [1, 1]
    for attempt, delay in enumerate(delays):
        assert 1 <= delay <= min(BACKOFF_MAX_SECONDS, 2**attempt)
    assert max(delays) <= 60 and max(delays[6:]) > 32  # it reaches the cap range
    assert len({round(d, 6) for d in delays[6:]}) > 1  # jittered, not a fixed ladder
    backoff.reset()
    assert backoff.next_delay() == 1


def test_server_gone_reconnects_with_backoff_and_serves_nothing(tmp_path, home, tls):
    d, fake_time = make_daemon(tmp_path, home, tls, server_gone, max_sessions=10)
    started = fake_time.now
    assert d.run() == 0
    assert d.sessions == 10 and len(d.delays) == 9
    for attempt, delay in enumerate(d.delays):
        assert 1 <= delay <= min(60, 2**attempt)
    assert fake_time.now - started == pytest.approx(sum(d.delays), abs=1)
    # Disconnected: an invoke is never answered and no reader ever ran.
    assert not d.node.connected
    assert d.node.receive(json.dumps({"type": "invoke", "v": 1})) == []
    assert d.node.readers.calls == []


def test_hook_events_are_spooled_while_the_server_is_gone(tmp_path, home, tls):
    clock = FakeClock()
    d, _ = make_daemon(tmp_path, home, tls, server_gone, clock=clock, max_sessions=1)
    assert d.run() == 0
    for index in range(3):
        d._deliver({**HOOK_EVENT, "event_id": f"e{index}", "occurred_at": "2026-11-29T22:00:00Z"})
    spool = d.node.spool
    assert [len(batch.events) for batch in spool.pending()] == [1, 1, 1]
    assert spool.max_bytes == 10 * 1024 * 1024 and spool.max_age == 24 * 3600
    directory = tmp_path / "state" / "spool"
    assert stat.S_IMODE(os.stat(directory).st_mode) == 0o700
    assert all(stat.S_IMODE(os.stat(p).st_mode) == 0o600 for p in directory.iterdir())
    clock.value += 24 * 3600 + 1  # older than 24 h: dropped
    spool.enforce()
    assert spool.pending() == []


def test_the_hook_socket_relays_into_the_spool(tmp_path, home, tls):
    """`friday-node-linux hook` -> the 0600 socket -> the daemon -> spool."""
    from friday_node_linux._vendor.nodes.reference.hook_relay import relay_stdin

    holder = {}

    def stay_down(client):
        holder.setdefault("first", client)
        server_gone(client)

    d, fake_time = make_daemon(tmp_path, home, tls, stay_down, max_sessions=1)
    d.load_policy()
    d.start_relay()
    try:
        socket_path = tmp_path / "run" / "hook.sock"
        assert stat.S_IMODE(os.stat(socket_path).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(socket_path.parent).st_mode) == 0o700
        assert relay_stdin(socket_path, json.dumps(HOOK_EVENT).encode()) == 0
        deadline = time.monotonic() + 5
        while not d.node.spool.pending() and time.monotonic() < deadline:
            time.sleep(0.02)
        [batch] = d.node.spool.pending()
        assert batch.events[0]["hook_event_name"] == "Stop"
    finally:
        d.stop_event.set()
        d.relay.close()


def test_the_node_has_no_listening_tcp_socket(tmp_path, home, tls):
    d, _ = make_daemon(tmp_path, home, tls, server_gone, max_sessions=1)
    d.load_policy()
    d.start_relay()
    try:
        inodes = set()
        for name in os.listdir("/proc/self/fd"):
            try:
                target = os.readlink(f"/proc/self/fd/{name}")
            except OSError:
                continue
            if target.startswith("socket:["):
                inodes.add(target[8:-1])
        listening = set()
        for table in ("/proc/self/net/tcp", "/proc/self/net/tcp6"):
            if not os.path.exists(table):
                continue
            with open(table) as handle:
                for line in handle.readlines()[1:]:
                    fields = line.split()
                    if fields[3] == "0A":  # LISTEN
                        listening.add(fields[9])
        assert not inodes & listening
    finally:
        d.stop_event.set()
        d.relay.close()


def test_revocation_ends_the_daemon_without_reconnecting(tmp_path, home, tls):
    def revoked(client):
        client.node.reconnect_allowed = False
        client._end("bye:revoked")

    d, _ = make_daemon(tmp_path, home, tls, revoked)
    assert d.run() == 0
    assert d.sessions == 1 and d.delays == []


def test_a_stable_session_resets_the_backoff(tmp_path, home, tls):
    fake_time = FakeTime()
    runs = []

    def script(client):
        runs.append(fake_time.now)
        if len(runs) != 4:
            server_gone(client)
            return
        client.accepted_at = fake_time.now  # run 4: a session that stays up 120 s
        client.until = fake_time.now + 120

    d, _ = make_daemon(tmp_path, home, tls, script, fake_time=fake_time, max_sessions=6)
    assert d.run() == 0
    assert len(d.delays) == 5
    assert d.delays[3] == 1  # reset by the stable run 4: attempt 0 again
    assert 1 <= d.delays[4] <= 2
    assert d.backoff.attempts == 2


def test_sighup_reloads_the_policy_between_sessions(tmp_path, home, tls):
    policy_path = tmp_path / "policy.json"
    LinuxPolicy().save(policy_path)
    fake_time = FakeTime()
    seen = []
    holder = {}

    def script(client):
        catalogue = {e["name"]: e["availability"] for e in client.node.catalogue}
        seen.append(catalogue["claude.sessions.list"])
        if len(seen) == 1:
            client.accepted_at = fake_time.now
            client.until = fake_time.now + 1_000
            LinuxPolicy().with_grant("claude", True).save(policy_path)
            holder["daemon"].request_reload()  # what SIGHUP does
        else:
            client.node.reconnect_allowed = False
            client._end("bye:revoked")

    d, _ = make_daemon(tmp_path, home, tls, script, fake_time=fake_time)
    holder["daemon"] = d
    assert d.run() == 0
    assert seen == ["not_granted", "granted"]
    assert d.delays == []  # a reload reconnects at once


def test_an_unsafe_policy_refuses_to_start(tmp_path, home, tls):
    LinuxPolicy().save(tmp_path / "policy.json")
    os.chmod(tmp_path / "policy.json", 0o644)
    d, _ = make_daemon(tmp_path, home, tls, server_gone)
    assert d.run() == 1
    assert d.sessions == 0


def test_the_hello_names_the_linux_node(tmp_path, home, tls):
    d, _ = make_daemon(tmp_path, home, tls, server_gone)
    d.load_policy()
    d.node.connect(1)
    hello = d.node.sent[0]
    assert hello["type"] == "hello" and hello["v"] == 1
    assert hello["node_version"] == daemon_module.NODE_VERSION
    assert hello["os_version"].startswith("Linux")
    assert {entry["class"] for entry in hello["catalogue"]} == {"read"}
    with pytest.raises(ValueError):
        d.node.connect(3)


def test_os_version_fits_the_hello_pattern():
    assert os_version("6.8.0-48-generic") == "Linux 6.8.0-48-generic"
    assert os_version("weird/release#1") == "Linux weird-release-1"
    assert len(os_version("x" * 200)) == 64


def test_the_transport_offers_v1_only():
    assert daemon_module.OFFER == ("friday-node.v1",)


def test_the_pinned_client_checks_the_pin_before_sending_anything(tmp_path, home, tls, other_tls):
    """A server off the pins gets a TLS handshake and nothing else: no
    WebSocket request, so never the `Authorization: Bearer` secret."""
    import threading

    from friday_node_linux.daemon import PinnedNodeClient

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    received = []

    def serve():
        conn, _ = listener.accept()
        try:
            tls_conn = other_tls.server_context.wrap_socket(conn, server_side=True)
            tls_conn.settimeout(2)
            received.append(tls_conn.recv(65536))
        except OSError:
            received.append(b"")

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    d, _ = make_daemon(tmp_path, home, tls, server_gone)
    d.load_policy()
    client = PinnedNodeClient(
        d.node, "127.0.0.1", listener.getsockname()[1], pins=d.credential.pins, secret="jnd_x"
    ).start()
    client.join(5)
    thread.join(5)
    listener.close()
    assert client.error is not None
    assert received in ([], [b""])
