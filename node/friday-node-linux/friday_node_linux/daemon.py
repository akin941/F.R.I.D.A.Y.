"""The Linux node daemon (Phase 13 brief D10): the vendored reference node
over a pinned TLS socket, read-only, fail closed.

- **Session.** `LinuxNode` is the vendored `ReferenceNode` (the protocol's
  executable spec) with the Linux node's `node_version` / `os_version`. It
  offers **protocol v1 only** (`friday-node.v1`): the session carries read
  invokes only, the catalogue has no v2 (action) contract, and there is no
  executor (`NoExecutor` refuses to run anything).
- **Transport.** `PinnedNodeClient` is the vendored `NodeClient` whose
  connect step does the TLS handshake and checks the pins (`tls.Pins`)
  before the `Authorization` header (the node secret) is ever written.
- **Fail closed.** When the server goes away the node disconnects (nothing
  in flight runs, nothing late is sent), serves no invoke (there is no
  listener of any kind) and spools hook events in the bounded spool
  (10 MiB, 24 h). It reconnects with exponential backoff, 1 s -> 60 s with
  jitter (`Backoff`; a session that stayed up for `STABLE_SESSION_SECONDS`
  resets it). `bye{revoked|kill_switch|credential_expired}` ends the daemon
  (no reconnect; exit 0 so `Restart=on-failure` does not loop).
- **Local policy.** Read at start and on `SIGHUP` (only the same uid or
  root can signal the process). A reload closes the current session and
  reconnects with the new catalogue: the policy never changes *during* a
  session. Nothing the server sends can reach the policy file.
- **Hook relay.** `friday-node-linux hook` (Claude Code's command hook)
  writes to a 0600 Unix socket in a 0700 directory; the daemon checks the
  peer uid and relays the allow-listed, scrubbed event live or spools it.
- **Logs.** Only the node id, the server host, outcomes and codes. Never
  the node secret, a pairing code, args or read output.
"""

import logging
import os
import platform
import random
import re
import signal
import threading
import time
from dataclasses import dataclass, field

from wsproto import ConnectionType, WSConnection
from wsproto.events import Request

from friday_node_linux import __version__, api, paths
from friday_node_linux._vendor.nodes.protocol.messages import HEARTBEAT_SECONDS, SUBPROTOCOLS
from friday_node_linux._vendor.nodes.reference.hook_relay import HookRelay, RelayError
from friday_node_linux._vendor.nodes.reference.node import ReferenceNode
from friday_node_linux._vendor.nodes.reference.spool import Spool
from friday_node_linux._vendor.nodes.reference.transport import NodeClient
from friday_node_linux.credential import CredentialError, save_credential
from friday_node_linux.policy import LinuxPolicy, PolicyError
from friday_node_linux.readers_root import LinuxReaders

logger = logging.getLogger("friday_node_linux")

NODE_VERSION = f"friday-node-linux {__version__}"
OFFER = (SUBPROTOCOLS[1],)  # v1 only: read invokes, never an action
BACKOFF_MIN_SECONDS = 1
BACKOFF_MAX_SECONDS = 60
STABLE_SESSION_SECONDS = 2 * HEARTBEAT_SECONDS
POLL_SECONDS = 0.2
ROTATION_RETRY_SECONDS = 3600
EXIT_OK = 0
EXIT_ERROR = 1
_OS_CHARS = re.compile(r"[^A-Za-z0-9 ._+()-]")


def os_version(release=None) -> str:
    """`Linux <kernel release>`, within the hello schema's pattern."""
    release = platform.release() if release is None else release
    text = _OS_CHARS.sub("-", f"Linux {release}".strip())[:64]
    return text or "Linux"


class WallClock:
    def now(self) -> int:
        return int(time.time())


class NoExecutor:
    """The Linux node runs no action (D10: read-only). The v1 session
    refuses every non-read invoke before an executor could be reached; this
    is the defence in depth."""

    calls: list = []

    def start(self, *args, **kwargs):  # pragma: no cover - unreachable on v1
        raise RuntimeError("friday-node-linux executes nothing")


class LinuxNode(ReferenceNode):
    """The reference node with the Linux node's identity in `hello`."""

    def hello(self) -> dict:
        frame = super().hello()
        frame["node_version"] = NODE_VERSION
        frame["os_version"] = os_version()
        return frame


def build_node(*, node_id, pinned, readers: LinuxReaders, clock, spool=None, rng=None, **kwargs):
    """The one way a `LinuxNode` is built (the daemon and the conformance
    mode both use it): v1 read catalogue from the readers, the readers'
    local policy, no executor."""
    return LinuxNode(
        node_id=node_id,
        pinned=dict(pinned),
        policy=readers.inner.policy,
        executor=NoExecutor(),
        clock=clock,
        catalogue=readers.catalogue(),
        readers=readers,
        spool=spool,
        rng=rng or random.Random(),
        read_outputs={},
        **kwargs,
    )


class PinnedNodeClient(NodeClient):
    """`NodeClient` whose socket is TLS with the node's pins, checked before
    any byte of the WebSocket request is sent."""

    def __init__(self, node, host, port, *, pins, offer=OFFER, **kwargs):
        super().__init__(node, host, port, offer=offer, ssl_context=None, **kwargs)
        self.pins = pins
        self.accepted_at = None

    def _connect(self):
        sock = self.pins.connect(self.host, self.port)
        sock.setblocking(True)
        self._sock = sock
        self._ws = WSConnection(ConnectionType.CLIENT)
        request = Request(
            host=self.host if self.port == 443 else f"{self.host}:{self.port}",
            target=self.path,
            subprotocols=list(self.offer),
            extra_headers=[(b"authorization", f"Bearer {self.secret}".encode("ascii"))],
        )
        self._send_bytes(self._ws.send(request))

    def _handle_events(self) -> bool:
        before = self.version
        done = super()._handle_events()
        if before is None and self.version is not None:
            self.accepted_at = time.monotonic()
        return done


@dataclass
class Backoff:
    """Exponential reconnect backoff with jitter: attempt n waits a uniform
    delay in [1, min(60, 2**n)] seconds."""

    rng: random.Random = field(default_factory=random.SystemRandom)
    minimum: float = BACKOFF_MIN_SECONDS
    maximum: float = BACKOFF_MAX_SECONDS
    attempts: int = 0

    def next_delay(self) -> float:
        ceiling = min(self.maximum, self.minimum * 2**self.attempts)
        self.attempts += 1
        return self.rng.uniform(self.minimum, ceiling)

    def reset(self):
        self.attempts = 0


class Daemon:
    """`friday-node-linux run`."""

    def __init__(
        self,
        *,
        credential,
        policy_path,
        home,
        spool_dir,
        hook_socket,
        pid_path=None,
        clock=None,
        sleep=time.sleep,
        monotonic=time.monotonic,
        rng=None,
        client_factory=None,
        which=None,
        max_sessions=None,
        credential_path=None,
        rotate=None,
    ):
        self.credential = credential
        self.credential_path = credential_path
        self.rotate = rotate or api.rotate
        self.rotation_retry_at = None
        self.policy_path = policy_path
        self.home = home
        self.spool_dir = spool_dir
        self.hook_socket = hook_socket
        self.pid_path = pid_path
        self.clock = clock or WallClock()
        self.sleep = sleep
        self.monotonic = monotonic
        self.backoff = Backoff(rng or random.SystemRandom())
        self.client_factory = client_factory or self._client
        self.which = which
        self.max_sessions = max_sessions  # tests: stop after this many sessions
        self.stop_event = threading.Event()
        self.reload_event = threading.Event()
        self.node = None
        self.client = None
        self.relay = None
        self.sessions = 0
        self.delays: list = []
        self.policy_fingerprint = None
        self._node_lock = threading.Lock()

    # --- policy ------------------------------------------------------------

    def _readers(self, policy):
        kwargs = {} if self.which is None else {"which": self.which}
        return LinuxReaders(home=self.home, policy=policy, **kwargs)

    def load_policy(self):
        policy = LinuxPolicy.load(self.policy_path)
        readers = self._readers(policy)
        self.policy_fingerprint = policy.fingerprint()
        if self.node is None:
            spool = Spool(self.spool_dir, self.clock)
            self.node = build_node(
                node_id=self.credential.node_id,
                pinned=self.credential.pinned(),
                readers=readers,
                clock=self.clock,
                spool=spool,
            )
        else:
            # Between sessions only (the transport thread is gone).
            self.node.readers = readers
            self.node.policy = readers.inner.policy
            self.node.catalogue = readers.catalogue()
        logger.info(
            "local policy loaded: grants=%s repos=%d",
            ",".join(sorted(k for k, v in policy.grants.items() if v)) or "none",
            len(policy.repos),
        )

    def request_reload(self, *_):
        self.reload_event.set()

    def request_stop(self, *_):
        self.stop_event.set()

    # --- the hook relay ------------------------------------------------------

    def _deliver(self, event):
        with self._node_lock:
            client = self.client
            if client is not None and client.alive:
                client.call(lambda node: node.relay_hook_event(event))
            else:
                self.node.relay_hook_event(event)  # not connected: spooled

    def _relay_loop(self):
        while not self.stop_event.is_set():
            try:
                self.relay.handle_one(timeout=POLL_SECONDS)
            except OSError:
                if self.stop_event.is_set():
                    return
                logger.warning("hook relay error; continuing")
            except Exception:  # noqa: BLE001 - one bad event never stops the relay
                logger.warning("hook relay dropped an event")

    def start_relay(self):
        try:
            paths.ensure_private_dir(self.hook_socket.parent)
            self.relay = HookRelay(self.hook_socket, deliver=self._deliver, clock=self.clock)
            self.relay.start()
        except (RelayError, paths.UnsafePath, OSError) as exc:
            logger.warning("hook relay disabled: %s", exc.__class__.__name__)
            self.relay = None
            return
        threading.Thread(target=self._relay_loop, daemon=True, name="hook-relay").start()

    # --- sessions ------------------------------------------------------------------

    def _client(self, node):
        return PinnedNodeClient(
            node,
            self.credential.host,
            self.credential.port,
            pins=self.credential.pins,
            path=self.credential.gateway_path,
            secret=self.credential.node_secret,
        )

    def maybe_rotate(self):
        """Rotate the node secret when it is `rotate_after_days` old (Phase 9
        D1). The new credential is saved before it is first used (the old
        secret stays valid for the server's grace period, so a crash in
        between loses nothing). A failure keeps the current secret and is
        retried after `ROTATION_RETRY_SECONDS`; an expired secret ends in
        `bye{credential_expired}` and re-pairing."""
        now = self.clock.now()
        if self.credential_path is None or not self.credential.rotation_due(now):
            return
        if self.rotation_retry_at is not None and now < self.rotation_retry_at:
            return
        try:
            rotated = self.rotate(self.credential, now=now)
        except api.RotationError as exc:
            logger.warning("credential rotation failed: %s", exc)
            self.rotation_retry_at = now + ROTATION_RETRY_SECONDS
            return
        except CredentialError as exc:
            logger.warning("credential rotation failed: %s", exc)
            self.rotation_retry_at = now + ROTATION_RETRY_SECONDS
            return
        save_credential(self.credential_path, rotated)
        self.credential = rotated
        self.rotation_retry_at = None
        logger.info("node secret rotated (next in %d days)", rotated.rotate_after_days)

    def _session(self):
        self.maybe_rotate()
        with self._node_lock:
            self.client = self.client_factory(self.node)
            self.client.start()
        started = self.monotonic()
        while self.client.alive:
            if self.stop_event.is_set() or self.reload_event.is_set():
                self.client.stop()
                break
            self.sleep(POLL_SECONDS)
        client, self.sessions = self.client, self.sessions + 1
        if client.error is not None:
            logger.warning("session ended: %s", client.error.__class__.__name__)
        elif client.rejected:
            logger.warning("the gateway rejected the connection")
        else:
            logger.info("session ended: %s", self.node.closed_reason)
        accepted = getattr(client, "accepted_at", None)
        if accepted is not None and self.monotonic() - started >= STABLE_SESSION_SECONDS:
            self.backoff.reset()

    def _wait(self, seconds):
        deadline = self.monotonic() + seconds
        while not self.stop_event.is_set() and not self.reload_event.is_set():
            left = deadline - self.monotonic()
            if left <= 0:
                return
            self.sleep(min(POLL_SECONDS, left))

    def run(self) -> int:
        try:
            self.load_policy()
        except (PolicyError, paths.UnsafePath) as exc:
            logger.error("refusing to start: %s", exc)
            return EXIT_ERROR
        self._write_pid()
        self.start_relay()
        logger.info("node %s -> %s", self.credential.node_id, self.credential.host)
        try:
            while not self.stop_event.is_set():
                if self.reload_event.is_set():
                    self.reload_event.clear()
                    try:
                        self.load_policy()
                    except (PolicyError, paths.UnsafePath) as exc:
                        logger.error("policy reload refused, stopping: %s", exc)
                        return EXIT_ERROR
                self._session()
                if not self.node.reconnect_allowed:
                    logger.warning("not reconnecting: %s", self.node.closed_reason)
                    return EXIT_OK
                if self.max_sessions is not None and self.sessions >= self.max_sessions:
                    return EXIT_OK
                if self.reload_event.is_set() or self.stop_event.is_set():
                    continue
                delay = self.backoff.next_delay()
                self.delays.append(delay)
                logger.info("reconnecting in %.1f s", delay)
                self._wait(delay)
            return EXIT_OK
        finally:
            self.stop_event.set()
            if self.relay is not None:
                self.relay.close()
            self._remove_pid()

    def install_signal_handlers(self):
        signal.signal(signal.SIGHUP, self.request_reload)
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)

    # --- pid file (the policy CLI signals the daemon) ---------------------------------

    def _write_pid(self):
        if self.pid_path is not None:
            paths.write_private_atomic(self.pid_path, f"{os.getpid()}\n".encode("ascii"))

    def _remove_pid(self):
        if self.pid_path is None:
            return
        try:
            if paths.read_private(self.pid_path).strip() == str(os.getpid()).encode("ascii"):
                os.unlink(self.pid_path)
        except (OSError, paths.UnsafePath):
            pass


def signal_daemon(pid_path) -> bool:
    """Send SIGHUP to the running daemon of this user (the policy CLI, after
    a change). True when a daemon was signalled."""
    try:
        pid = int(paths.read_private(pid_path).strip())
    except (OSError, ValueError, paths.UnsafePath):
        return False
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as handle:
            cmdline = handle.read()
    except OSError:
        return False
    if b"friday-node-linux" not in cmdline and b"friday_node_linux" not in cmdline:
        return False  # a recycled pid: never signal a stranger
    try:
        os.kill(pid, signal.SIGHUP)
    except OSError:
        return False
    return True
