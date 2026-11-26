"""`friday-node-linux conformance`: the packaged node's side of the
external conformance hook (Phase 13 brief D10; the protocol is
`backend/nodes/conformance/gateway.py`, the same one the Swift
`Conformance` build implements).

    manage.py node_conformance_server --external --tls-self-signed --protocol 1 \\
        --port 8765 --hook-socket /tmp/friday-conformance-hook.sock
    friday-node-linux conformance --hook-socket /tmp/friday-conformance-hook.sock \\
        --gateway 127.0.0.1:8765 --ca <the certificate the server printed>

The node connects to the JSON-lines hook socket; for each `{"op":
"prepare", "case": ...}` it builds a fresh node **with the daemon's own
builder** (`daemon.build_node`: the vendored session machine, the Linux
catalogue and `LinuxReaders`, v1 only, no executor) configured by the
case's `setup`, and connects it to the hostile gateway over TLS pinned to
`--ca` (`PinnedNodeClient`, as `run` does). The other ops drive the case's
fake clock (`advance`, `tick`), the kill switch, the report and the
disconnect wait. Only what a conformance build must stand in for differs
from `run`: the case clock, the fixture home of
`nodes.reference.fixtures.factory` (vendored) instead of `$HOME`, the
fixture desktop for `apps.frontmost` / `windows.list` (unsupported on a
real Linux desktop), `ccusage` treated as present, and the conformance
node id and signing key (published test keys, refused by every real server).
"""

import json
import random
import socket
import tempfile
from pathlib import Path

from friday_node_linux._vendor.nodes.protocol.signing import load_public_key_b64
from friday_node_linux._vendor.nodes.reference.confirm import FakeClock
from friday_node_linux._vendor.nodes.reference.fixtures.factory import (
    build_fixture_home,
    policy_document,
)
from friday_node_linux._vendor.nodes.reference.readers import DESKTOP_FIXTURE
from friday_node_linux.daemon import PinnedNodeClient, build_node
from friday_node_linux.policy import LinuxPolicy
from friday_node_linux.readers_root import LinuxReaders

# backend/nodes/conformance/cases.py NODE_ID (scripts/vendor.py --check
# fails when they differ).
CONFORMANCE_NODE_ID = "6f1e2d3c-4b5a-4987-8a6b-5c4d3e2f1a0b"
KEYS_PATH = (
    Path(__file__).resolve().parent / "_vendor" / "nodes" / "conformance" / "keys"
) / "conformance_keys.json"


def conformance_pinned() -> dict:
    """The conformance signing key, the only key a conformance node pins
    (`unpinned` must be refused)."""
    keys = json.loads(KEYS_PATH.read_text(encoding="utf-8"))
    signing = keys["signing"]
    return {signing["key_id"]: load_public_key_b64(signing["public_key_b64"])}


def fixture_linux_policy(*, reader_grants=None) -> LinuxPolicy:
    """The fixture policy's repos as a Linux `policy.json` (repos only), with
    both directory grants on unless the case turns one off."""
    repos = [{"name": r["name"], "root": r["root"]} for r in policy_document()["repos"]]
    grants = {"claude": True, "codex": True, **(reader_grants or {})}
    return LinuxPolicy.from_document({"v": 1, "grants": grants, "repos": repos})


def build_case_node(case, *, home):
    setup = case.get("setup", {})
    policy = fixture_linux_policy(reader_grants=setup.get("reader_grants"))
    readers = LinuxReaders(
        home=home,
        policy=policy,
        desktop=json.loads(DESKTOP_FIXTURE.read_text(encoding="utf-8")),
        window_titles=bool(setup.get("window_titles", False)),
        which=lambda name: f"/usr/bin/{name}",
        availability_overrides=setup.get("availability"),
    )
    clock = FakeClock(setup.get("now"))
    return build_node(
        node_id=CONFORMANCE_NODE_ID,
        pinned=conformance_pinned(),
        readers=readers,
        clock=clock,
        rng=random.Random(0),
        read_delay_seconds=setup.get("read_delay", 0),
    )


def node_report(node) -> dict:
    """The hook's `report` (the fields of `nodes.conformance.runner.node_report`)."""
    calls = getattr(node.executor, "calls", [])
    return {
        "reader_calls": len(node.readers.calls) if node.readers is not None else 0,
        "executor_calls": len(calls),
        "executor_terminated": sum(1 for call in calls if call.get("terminated")),
        "windows_shown": len(node.window.shown),
        "closed_reason": node.closed_reason,
        "reconnect_allowed": node.reconnect_allowed,
        "policy_fingerprint": node.policy.fingerprint(),
        "next_reconnect_delay": node.next_reconnect_delay,
        "voice_states_dropped": getattr(node, "voice_states_dropped", 0),
    }


class ConformanceHookClient:
    def __init__(self, hook_path, gateway_host, gateway_port, *, pins, home):
        self.hook_path = hook_path
        self.gateway = (gateway_host, gateway_port)
        self.pins = pins
        self.home = home
        self.node = None
        self.client = None
        self.cases = 0

    def _finish(self):
        if self.client is not None:
            self.client.stop()
            self.client = None

    def _connect(self):
        self.client = PinnedNodeClient(self.node, *self.gateway, pins=self.pins).start()

    def _call(self, fn):
        return self.client.call(fn)

    def answer(self, op) -> dict:
        name = op.get("op")
        if name == "prepare":
            self._finish()
            self.node = build_case_node(op["case"], home=self.home)
            fingerprint = self.node.policy.fingerprint()
            self._connect()
            self.cases += 1
            return {"ok": True, "policy_fingerprint": fingerprint}
        if name == "report":
            return self._call(node_report)
        if name == "advance":
            seconds = op["seconds"]

            def advance(node):
                node.clock.advance(seconds)
                return node.tick()

            self._call(advance)
        elif name == "tick":
            self._call(lambda node: node.tick())
        elif name == "kill_switch":
            self._call(lambda node: node.engage_kill_switch())
        elif name == "voice_state":
            state = op["state"]
            self._call(lambda node: node.relay_voice_state(state))
        elif name == "wait_disconnected":
            self.client.join(5)
        else:
            return {"ok": False, "error": "unknown_op"}
        return {"ok": True}

    def run(self) -> int:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(str(self.hook_path))
            buffer = b""
            while True:
                data = sock.recv(65536)
                if not data:
                    return 0 if self.cases else 1
                buffer += data
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    reply = self.answer(json.loads(line))
                    sock.sendall((json.dumps(reply) + "\n").encode("utf-8"))
        finally:
            sock.close()
            self._finish()


def run_conformance(*, hook_socket, gateway, pins, fixture_base=None) -> int:
    host, _, port = gateway.rpartition(":")
    base = Path(fixture_base or tempfile.mkdtemp(prefix="friday-node-linux-conformance-"))
    home = build_fixture_home(base)
    return ConformanceHookClient(
        hook_socket, host or "127.0.0.1", int(port), pins=pins, home=home
    ).run()
