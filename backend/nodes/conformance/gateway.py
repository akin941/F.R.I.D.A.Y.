"""The hostile fake gateway over a real localhost socket (Phase 9 brief D5,
Phase 10 brief branch 2; `manage.py node_conformance_server`).

`ConformanceGateway` listens on localhost (optionally TLS with a self-signed
certificate) and plays one case per accepted WebSocket connection:

1. handshake with `wsproto`: the node's offered subprotocols are negotiated
   against the case's version only (`negotiate_subprotocol`), so a v1 case
   against a v2 node is played on a v1 session; anything else is rejected;
2. the node's `hello` (schema-checked);
3. the case's steps: frames go over the socket; the clock, the owner's
   window decision and the kill switch go through the **conformance hook**,
   never through the protocol (the protocol has no such message);
4. the node's frames are collected, heartbeats answered, and the case is
   checked by `runner.check` with the hook's report.

Two hooks exist:

- `InProcessHook`: the reference node in the same process (its
  `NodeClient.call`), for the tests and the default command run;
- `ExternalHook`: a JSON-lines Unix socket for a separately running node
  (the Swift `Conformance` build, Mac session M1): the node connects to the
  hook socket and to the gateway; the gateway sends `{"op": "prepare",
  "case": ...}`, `{"op": "advance", "seconds": n}`, `{"op": "tick"}`,
  `{"op": "kill_switch"}`, `{"op": "report"}`, `{"op": "wait_disconnected"}`
  and reads one JSON line per op (`{"ok": true}`; `prepare` adds the node's
  `policy_fingerprint` before the case, `report` answers the report object
  of `runner.node_report`). After `prepare` the node connects to the gateway
  for that case. The reference node's side of it is `HookClient`.
  `prepare` also configures the node's readers from the case's `setup`
  (`reader_grants`, `window_titles`) over the fixture home, and `report`
  includes `reader_calls` (how many times a reader ran; Phase 9 branch 3).
"""

import datetime
import ipaddress
import json
import os
import select
import socket
import ssl
import tempfile
import threading
import time

from wsproto import ConnectionType, WSConnection
from wsproto.events import (
    AcceptConnection,
    CloseConnection,
    Ping,
    RejectConnection,
    Request,
    TextMessage,
)

from nodes.conformance.runner import (
    ADVANCE_STEP_SECONDS,
    CaseResult,
    GatewayChecker,
    build_reference_node,
    check,
    node_report,
    play,
)
from nodes.protocol.messages import negotiate_subprotocol, validate
from nodes.reference.transport import NodeClient, encode_frame

SETTLE_SECONDS = 0.1
FRAME_TIMEOUT_SECONDS = 5.0


class GatewayError(RuntimeError):
    pass


def self_signed_context(host="127.0.0.1"):
    """(server_context, client_context) for a throwaway self-signed
    localhost certificate (`--tls-self-signed`); the client pins it."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "friday-node-conformance")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address(host)), x509.DNSName("localhost")]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    directory = tempfile.mkdtemp(prefix="friday-conformance-tls-")
    cert_path = os.path.join(directory, "cert.pem")
    key_path = os.path.join(directory, "key.pem")
    with open(cert_path, "wb") as handle:
        handle.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as handle:
        handle.write(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(cert_path, key_path)
    client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client.load_verify_locations(cert_path)
    return server, client, cert_path


class SocketDriver:
    """The server side of one case over one accepted connection."""

    def __init__(self, conn, version, hook, *, settle=SETTLE_SECONDS):
        self.conn = conn
        self.version = version
        self.hook = hook
        self.settle = settle
        self.ws = WSConnection(ConnectionType.SERVER)
        self.frames: list = []
        self.checker = GatewayChecker(version)
        self.silent = False
        self.open = False
        self._text = []
        self._handshake()

    # --- wire ------------------------------------------------------------

    def _send(self, event):
        data = self.ws.send(event)
        if data:
            self.conn.sendall(data)

    def _read(self, timeout):
        readable, _, _ = select.select([self.conn], [], [], timeout)
        if not readable:
            return False
        try:
            data = self.conn.recv(65536)
        except (ConnectionError, OSError):
            data = b""
        if not data:
            self.open = False
            self.ws.receive_data(None)
            return True
        self.ws.receive_data(data)
        return True

    def _handshake(self):
        deadline = time.monotonic() + FRAME_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            self._read(0.1)
            for event in self.ws.events():
                if isinstance(event, Request):
                    picked = negotiate_subprotocol(event.subprotocols, supported=(self.version,))
                    if picked is None:
                        self._send(RejectConnection(status_code=400))
                        raise GatewayError("the node offered no matching subprotocol")
                    self._send(AcceptConnection(subprotocol=picked[1]))
                    self.open = True
                elif isinstance(event, TextMessage):
                    self._on_text(event)
            if any(frame["type"] == "hello" for frame in self.frames):
                return
        raise GatewayError("no hello from the node")

    def _on_text(self, event):
        self._text.append(event.data)
        if not event.message_finished:
            return
        text, self._text = "".join(self._text), []
        try:
            frame = json.loads(text)
        except ValueError:
            self.checker.frame_errors += 1
            self.checker.errors.append("malformed")
            return
        if not isinstance(frame, dict):
            self.checker.frame_errors += 1
            return
        if frame.get("type") == "hello":
            validate(frame, "node_to_server", self.version)
        else:
            self.checker.node_sent(frame)
        self.frames.append(frame)
        if frame.get("type") == "heartbeat" and not self.silent and self.open:
            ack = {
                "v": self.version,
                "type": "heartbeat.ack",
                "id": f"00000000-0000-4000-8000-{frame.get('seq', 0):012d}",
                "seq": frame.get("seq", 0),
                "server_time": 0,
            }
            self._send(TextMessage(data=encode_frame(ack)))

    def collect(self, settle=None):
        """Read node frames until the socket is quiet for `settle` seconds."""
        settle = self.settle if settle is None else settle
        deadline = time.monotonic() + FRAME_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            got = self._read(settle) if self.open else False
            for event in self.ws.events():
                if isinstance(event, TextMessage):
                    self._on_text(event)
                elif isinstance(event, Ping):
                    self._send(event.response())
                elif isinstance(event, CloseConnection):
                    self.open = False
                    try:
                        self._send(event.response())
                    except Exception:  # noqa: BLE001
                        pass
            if not got:
                return

    # --- the driver interface (runner.play) ------------------------------

    def send(self, raw, frame=None):
        if not self.open:
            return
        if frame is not None:
            self.checker.server_sent(frame)
        self._send(TextMessage(data=raw))
        self.collect()

    def advance(self, seconds):
        while seconds > 0:
            step = min(seconds, ADVANCE_STEP_SECONDS)
            self.hook.advance(step)
            seconds -= step
            self.collect(0.05)
        self.collect()

    def tick(self):
        self.hook.tick()
        self.collect()

    def disconnect(self):
        if self.open:
            try:
                self._send(CloseConnection(code=1001))
            except Exception:  # noqa: BLE001
                pass
        self.open = False
        try:
            self.conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.conn.close()
        self.hook.wait_disconnected()

    def kill_switch(self):
        self.hook.kill_switch()
        self.collect()

    def report(self):
        return self.hook.report()

    def close(self):
        if self.open:
            self.disconnect()


class InProcessHook:
    """The conformance hook for a reference node running in this process."""

    def __init__(self, case, *, home, worktree_base=None):
        self.node = build_reference_node(case, home=home, worktree_base=worktree_base)
        self.fingerprint = self.node.policy.fingerprint()
        self.client = None

    def connect(self, host, port, ssl_context=None):
        self.client = NodeClient(self.node, host, port, ssl_context=ssl_context).start()

    def advance(self, seconds):
        def run(node):
            node.clock.advance(seconds)
            return node.tick()

        return self.client.call(run)

    def tick(self):
        return self.client.call(lambda node: node.tick())

    def kill_switch(self):
        return self.client.call(lambda node: node.engage_kill_switch())

    def report(self):
        return self.client.call(node_report)

    def wait_disconnected(self):
        self.client.join(5)

    def finish(self):
        if self.client is not None:
            self.client.stop()


class ConformanceGateway:
    """Listens on localhost and plays cases, one connection each."""

    def __init__(self, host="127.0.0.1", port=0, *, ssl_context=None):
        self.host = host
        self.ssl_context = ssl_context
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind((host, port))
        self.listener.listen(8)
        self.port = self.listener.getsockname()[1]

    def accept(self, timeout=FRAME_TIMEOUT_SECONDS):
        self.listener.settimeout(timeout)
        conn, _ = self.listener.accept()
        if self.ssl_context is not None:
            conn = self.ssl_context.wrap_socket(conn, server_side=True)
        conn.setblocking(True)
        return conn

    def play_case(self, case, hook, *, home=None, fingerprint=None) -> CaseResult:
        conn = self.accept()
        driver = SocketDriver(conn, case["version"], hook)
        play(case, driver)
        driver.collect()
        report = driver.report()
        driver.close()
        failures = check(
            case,
            driver.frames,
            report,
            driver.checker,
            policy_fingerprint=fingerprint or report.get("policy_fingerprint"),
            home=home,
        )
        return CaseResult(case["name"], case["version"], failures)

    def close(self):
        self.listener.close()


def run_case_over_socket(gateway, case, *, home, worktree_base=None, client_ssl=None):
    """One case against the reference node over the gateway's socket."""
    hook = InProcessHook(case, home=home, worktree_base=worktree_base)
    hook.connect(gateway.host, gateway.port, ssl_context=client_ssl)
    try:
        return gateway.play_case(case, hook, home=home, fingerprint=hook.fingerprint)
    finally:
        hook.finish()


# --- the external hook (a separately running node) -----------------------


class ExternalHook:
    """The gateway side of the JSON-lines hook socket (see module doc)."""

    def __init__(self, path):
        self.path = path
        if os.path.exists(path):
            os.unlink(path)
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(path)
        os.chmod(path, 0o600)
        self.listener.listen(1)
        self.conn = None
        self._buffer = b""

    def wait_for_node(self, timeout=60):
        self.listener.settimeout(timeout)
        self.conn, _ = self.listener.accept()
        self.conn.settimeout(FRAME_TIMEOUT_SECONDS)

    def _op(self, body):
        self.conn.sendall((json.dumps(body) + "\n").encode("utf-8"))
        while b"\n" not in self._buffer:
            data = self.conn.recv(65536)
            if not data:
                raise GatewayError("the hook connection closed")
            self._buffer += data
        line, self._buffer = self._buffer.split(b"\n", 1)
        return json.loads(line)

    def prepare(self, case):
        return self._op({"op": "prepare", "case": case})

    def advance(self, seconds):
        return self._op({"op": "advance", "seconds": seconds})

    def tick(self):
        return self._op({"op": "tick"})

    def kill_switch(self):
        return self._op({"op": "kill_switch"})

    def report(self):
        return self._op({"op": "report"})

    def wait_disconnected(self):
        self._op({"op": "wait_disconnected"})

    def close(self):
        for sock in (self.conn, self.listener):
            if sock is not None:
                sock.close()
        if os.path.exists(self.path):
            os.unlink(self.path)


class HookClient:
    """The reference node's side of the external hook: for each `prepare`
    it builds a node for the case and connects it to the gateway. Used by
    the tests to prove the external path end to end; the Swift
    `Conformance` build implements the same ops (`#if CONFORMANCE`)."""

    def __init__(
        self, hook_path, gateway_host, gateway_port, *, home, worktree_base=None, ssl_context=None
    ):
        self.hook_path = hook_path
        self.gateway = (gateway_host, gateway_port)
        self.home = home
        self.worktree_base = worktree_base
        self.ssl_context = ssl_context
        self.inner = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.error = None

    def start(self):
        self._thread.start()
        return self

    def join(self, timeout=10):
        self._thread.join(timeout)

    def _answer(self, op):
        name = op["op"]
        if name == "prepare":
            if self.inner is not None:
                self.inner.finish()
            self.inner = InProcessHook(op["case"], home=self.home, worktree_base=self.worktree_base)
            self.inner.connect(*self.gateway, ssl_context=self.ssl_context)
            return {"ok": True, "policy_fingerprint": self.inner.fingerprint}
        if name == "report":
            return self.inner.report()
        if name == "advance":
            self.inner.advance(op["seconds"])
        elif name == "tick":
            self.inner.tick()
        elif name == "kill_switch":
            self.inner.kill_switch()
        elif name == "wait_disconnected":
            self.inner.wait_disconnected()
        return {"ok": True}

    def _run(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(self.hook_path)
            buffer = b""
            while True:
                data = sock.recv(65536)
                if not data:
                    return
                buffer += data
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    answer = self._answer(json.loads(line))
                    sock.sendall((json.dumps(answer) + "\n").encode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - surfaced to the test
            self.error = exc
        finally:
            sock.close()
            if self.inner is not None:
                self.inner.finish()


def play_external(gateway, hook: ExternalHook, case) -> CaseResult:
    """One case against an external node: prepare it through the hook, then
    accept its connection and play."""
    prepared = hook.prepare(case)
    return gateway.play_case(case, hook, fingerprint=prepared.get("policy_fingerprint"))
