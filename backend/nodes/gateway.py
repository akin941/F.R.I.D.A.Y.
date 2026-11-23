"""The node gateway (Phase 9 brief D3, D6; Phase 10 brief D7, D12).

Two layers:

- **`NodeSession`** is the transport-agnostic, synchronous core of one live
  node socket. It touches the database and Redis and never blocks for long:
  the socket app calls it through
  `asgiref.sync.sync_to_async(thread_sensitive=True)`; the Phase 10 tests
  drive it directly against the reference node.
- **`NodeSocketApp`** is the raw-ASGI WebSocket application that
  `config.asgi_nodes` mounts on `/ws/node/v1` (served by `uvicorn --ws
  wsproto` in the `node-gateway` process): the handshake, `hello`, the
  heartbeat re-check, the per-node inbox loop, the revocation and kill
  switch fan-out, the limits and the logging.

`NodeSession`:

- `hello(frame)` records the `CapabilitySnapshot` for the session's
  negotiated version (written only when the catalogue hash or the version
  changed), stores the node's reported versions, marks the node present
  (the presence value is the session version) and returns the accepted
  (invocable) entry names; `hello_ack(accepted)` builds the answer.
- `heartbeat(frame)` refreshes presence, writes `last_heartbeat_at` at most
  once a minute (`nodes.presence`) and returns `heartbeat.ack`;
  `device_refusal()` re-reads the device row, the kill switch and the
  credential age and returns the `bye` reason when any fails.
- `place(envelope)` routes one inbox envelope: an envelope for another node
  is dropped (a read's caller is resolved `server_refused:node_mismatch`,
  an action's execution ends the same way); a v1-class `read` joins **this
  socket's** read in-flight set; an action envelope that belongs to no
  in-flight execution is dropped and counted; everything else is returned
  for forwarding. `pull_invokes()` drains the inbox through it.
- `receive(frame)` accepts `result` frames only for invocations in this
  socket's in-flight sets (per-socket routing): a read result is validated
  against the server's `result_schema` for that capability and pushed to
  `nodes:result:<invocation_id>` (TTL-bound, `nodes.bus.push_result`); a
  frame for another socket's, an unknown or a finished invocation is
  discarded and counted (`discarded`), never applied and never pushed.
  `confirmation` and `output` frames move Phase 10 action executions
  (scrubbed server-side through `nodes.output`).
- `tick(now)`: the action watchdog (deadline = `expires_at` + the
  contract's `max_runtime_seconds` + 30 s -> `timeout`), and read entries
  past their envelope's expiry leave the in-flight set (a later result is
  discarded).
- `disconnect()`: every in-flight read is resolved `node_disconnected`,
  every in-flight action ends `node_disconnected`, presence is cleared
  (unless a newer socket of the same node replaced this one). Nothing is
  retried (ADR-013 decision 6).

Frame bodies are never logged: ids, types, sizes and codes only.
"""

import asyncio
import json
import logging
import threading
import time
import uuid
from collections import Counter, deque
from dataclasses import dataclass, field

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import close_old_connections

from accounts.devices import NodeCredentialRejected, authenticate_node_secret, node_device_refusal
from accounts.models import TrustedDevice
from core.revocation import ALL_DEVICES, REVOCATION_CHANNEL, parse_revocation
from nodes import bus, presence, services
from nodes.capabilities import get_contract
from nodes.models import Node
from nodes.output import OutputStreamWriter, OutputTransit, scrubber_for, utf8_len
from nodes.protocol.messages import (
    HEARTBEAT_SECONDS,
    MAX_EVENT_BATCH,
    MAX_FRAME_BYTES,
    ProtocolError,
    catalogue_sha256,
    decode_frame,
    negotiate_subprotocol,
    validate,
)

logger = logging.getLogger(__name__)

ACTION_FRAME_TYPES = frozenset({"confirmation", "output", "result"})

# D6 limits and timers.
HELLO_TIMEOUT_SECONDS = 10
IDLE_TIMEOUT_SECONDS = 45  # three missed heartbeats
MAX_FRAMES_PER_SECOND = 20
FRAME_ERROR_LIMIT = 3
FRAME_ERROR_WINDOW_SECONDS = 60
TICK_SECONDS = 1.0
INBOX_POLL_SECONDS = 1
REVOCATION_CLOSE_SECONDS = 2  # the fan-out's promise (D1, D6)

# WebSocket close codes (D6). 1000 follows a `bye`.
CLOSE_NORMAL = 1000
CLOSE_TOO_BIG = 1009
CLOSE_TRY_AGAIN = 1013
CLOSE_BAD_REQUEST = 4400  # no common subprotocol, or a protocol error
CLOSE_UNAUTHORIZED = 4401
CLOSE_FORBIDDEN_ORIGIN = 4403
CLOSE_TIMEOUT = 4408  # no `hello` within 10 s, or silence beyond 45 s
CLOSE_RATE = 4429

# `node_device_refusal` reason -> `bye` reason.
BYE_FOR_REFUSAL = {
    "revoked": "revoked",
    "kill_switch": "kill_switch",
    "credential_expired": "credential_expired",
    "idle_expired": "credential_expired",
}


def bye_reason_for(refusal) -> str:
    """The `bye` reason for a device refusal (anything else: `revoked`)."""
    return BYE_FOR_REFUSAL.get(refusal, "revoked")


def _message_id() -> str:
    return str(uuid.uuid4())


# --- the session core --------------------------------------------------


@dataclass
class InFlight:
    """One forwarded Phase 10 action invocation of this socket."""

    invocation_id: str
    execution_id: object
    deadline: int | None
    transit: OutputTransit
    output_bytes: int = 0

    @property
    def emitted_bytes(self) -> int:
        """Scrubbed bytes handed to the stream (a count, never the text)."""
        return self.transit.emitted_bytes


@dataclass
class ReadInFlight:
    """One forwarded read invocation of this socket (D6 per-socket
    routing). Holds no args and no output."""

    invocation_id: str
    capability: str
    expires_at: int


@dataclass
class NodeSession:
    node: object
    version: int
    clock: object = None
    in_flight: dict = field(default_factory=dict)
    reads: dict = field(default_factory=dict)
    discarded: int = 0
    frame_errors: int = 0
    closed: bool = False

    def __post_init__(self):
        if self.version not in (1, 2):
            raise ValueError("unsupported protocol version")
        self.clock = self.clock or (lambda: int(time.time()))
        self.node_id = str(self.node.pk)
        self._heartbeat_seq = 0

    # --- session ---------------------------------------------------------

    def hello(self, frame) -> dict:
        """Record the advertised catalogue and mark the node present.
        Returns the accepted (invocable) entry names and the snapshot."""
        validate(frame, "node_to_server", self.version)
        snapshot = services.record_capability_snapshot(
            node=self.node,
            catalogue=frame["catalogue"],
            catalogue_sha256=catalogue_sha256(frame["catalogue"]),
            protocol_version=self.version,
            node_version=frame.get("node_version", ""),
            os_version=frame.get("os_version", ""),
        )
        presence.record_hello(
            self.node,
            node_version=frame.get("node_version", ""),
            os_version=frame.get("os_version", ""),
        )
        bus.mark_present(self.node_id, version=self.version)
        return {
            "accepted": [entry["name"] for entry in snapshot.entries if entry.get("invocable")],
            "snapshot": snapshot,
        }

    def hello_ack(self, accepted) -> dict:
        frame = {
            "v": self.version,
            "type": "hello.ack",
            "id": _message_id(),
            "node_id": self.node_id,
            "heartbeat_seconds": HEARTBEAT_SECONDS,
            "rotate_after_days": int(settings.NODE_CREDENTIAL_ROTATE_AFTER_DAYS),
            "server_time": int(self.clock()),
            "accepted": sorted(set(accepted)),
        }
        return validate(frame, "server_to_node", self.version)

    def heartbeat(self, frame) -> dict:
        """Refresh presence (and, at most once a minute, the row); returns
        the `heartbeat.ack`."""
        validate(frame, "node_to_server", self.version)
        bus.mark_present(self.node_id, version=self.version)
        presence.record_heartbeat(
            self.node, spool_count=frame["spool_count"], spool_bytes=frame["spool_bytes"]
        )
        ack = {
            "v": self.version,
            "type": "heartbeat.ack",
            "id": _message_id(),
            "seq": frame["seq"],
            "server_time": int(self.clock()),
        }
        return validate(ack, "server_to_node", self.version)

    def device_refusal(self) -> str | None:
        """The per-heartbeat re-check (D6): the device row, the owner's kill
        switch and the credential age, read fresh. Returns the `bye`
        reason, or None while the node may stay connected."""
        device = TrustedDevice.objects.select_related("user").filter(pk=self.node.device_id).first()
        refusal = node_device_refusal(device)
        return None if refusal is None else bye_reason_for(refusal)

    def bye(self, reason) -> dict:
        frame = {"v": self.version, "type": "bye", "id": _message_id(), "reason": reason}
        return validate(frame, "server_to_node", self.version)

    def event_ack(self, frame) -> dict:
        """D10 with no sink registered (branch 6 wires the sink): the batch
        is acknowledged `dropped`, stored nowhere. Counts only."""
        ack = {
            "v": self.version,
            "type": "event.ack",
            "id": _message_id(),
            "event_id": frame["id"],
            "stored": 0,
            "duplicate": 0,
            "dropped": min(len(frame["batch"]), MAX_EVENT_BATCH),
        }
        return validate(ack, "server_to_node", self.version)

    # --- inbox -----------------------------------------------------------

    def pull_invokes(self) -> list:
        """Drain the node's inbox; the envelopes to forward now."""
        if self.closed:
            return []
        forward = []
        for envelope in bus.drain_inbox(self.node_id):
            placed = self.place(envelope)
            if placed is not None:
                forward.append(placed)
        return forward

    def place(self, envelope) -> dict | None:
        """Route one inbox envelope (see module doc). Returns it when it is
        to be forwarded to this socket, else None."""
        if self.closed or not isinstance(envelope, dict):
            return None
        invocation_id = envelope.get("invocation_id")
        is_read = envelope.get("class") == "read"
        if str(envelope.get("node_id")) != self.node_id:
            self.discarded += 1
            logger.warning("node gateway dropped a misrouted envelope node=%s", self.node_id)
            if is_read:
                self._resolve_read(invocation_id, {"verdict": "node_mismatch"})
            else:
                services.refuse_misrouted(invocation_id)
            return None
        if is_read:
            return self._place_read(envelope)
        execution = services.execution_for_session(
            node_id=self.node_id, invocation_id=invocation_id
        )
        if execution is None:
            self.discarded += 1
            return None
        scrub = scrubber_for(execution.workspace)
        self.in_flight[str(invocation_id)] = InFlight(
            invocation_id=str(invocation_id),
            execution_id=execution.pk,
            deadline=services.watchdog_deadline(execution),
            transit=OutputTransit(scrub, OutputStreamWriter(str(invocation_id))),
        )
        return envelope

    def _place_read(self, envelope):
        invocation_id = str(envelope.get("invocation_id") or "")
        if envelope.get("v") != self.version:
            # Signed for an earlier session of another version: that socket
            # is gone, so the caller is resolved as such.
            self.discarded += 1
            self._resolve_read(invocation_id, {"verdict": "node_disconnected"})
            return None
        if get_contract(envelope.get("capability")) is None or not invocation_id:
            self.discarded += 1
            return None
        expires_at = envelope.get("expires_at")
        self.reads[invocation_id] = ReadInFlight(
            invocation_id=invocation_id,
            capability=envelope["capability"],
            expires_at=expires_at if isinstance(expires_at, int) else 0,
        )
        return envelope

    def _resolve_read(self, invocation_id, payload) -> bool:
        if not invocation_id:
            return False
        try:
            bus.push_result(invocation_id, payload)
        except bus.BusUnavailable:
            logger.warning("node gateway could not push a read result node=%s", self.node_id)
            return False
        return True

    # --- frames from the node --------------------------------------------

    def receive(self, frame) -> str | None:
        """Apply one node frame. Returns what happened (`applied`,
        `discarded`, `ignored`, `reported`, `frame_error`), never raises for
        a hostile frame."""
        if self.closed:
            return "ignored"
        try:
            validate(frame, "node_to_server", self.version)
        except ProtocolError:
            self.frame_errors += 1
            return "frame_error"
        kind = frame["type"]
        if kind not in ACTION_FRAME_TYPES:
            return "ignored"
        invocation_id = frame.get("invocation_id")
        if kind == "result" and invocation_id is None:
            # The node's report of a server frame it could not accept (D5
            # checks 1-4): a code, never a payload.
            logger.warning(
                "node reported a frame error node=%s code=%s",
                self.node_id,
                frame.get("refusal_code"),
            )
            return "reported"
        if kind == "result" and str(invocation_id) in self.reads:
            return self._read_result(self.reads.pop(str(invocation_id)), frame)
        flight = self.in_flight.get(str(invocation_id)) if invocation_id else None
        if flight is None:
            # Another socket's, an unknown or a finished invocation.
            self.discarded += 1
            return "discarded"
        if kind == "output":
            return self._output(flight, frame)
        if kind == "confirmation":
            moved = services.record_gated_progress(
                node_id=self.node_id,
                invocation_id=flight.invocation_id,
                confirmation=frame["state"],
            )
            if moved is None:
                self._forget(flight)
                self.discarded += 1
                return "discarded"
            return "applied"
        self._flush(flight)
        finished = services.record_gated_progress(
            node_id=self.node_id,
            invocation_id=flight.invocation_id,
            result=frame,
            output_bytes=flight.output_bytes,
        )
        flight.transit.end(finished)
        self._forget(flight)
        if finished is None:
            self.discarded += 1
            return "discarded"
        return "applied"

    def _read_result(self, flight, frame):
        """Validate a read result against the server's own contract and push
        it to the caller's result key (D6). An invalid output is replaced by
        the gateway's verdict (`result_schema` / `result_too_large`): nothing
        of it is pushed."""
        payload = {
            "status": frame["status"],
            "refusal_code": frame.get("refusal_code"),
            "truncated": bool(frame.get("truncated")),
            "duration_ms": frame.get("duration_ms", 0),
        }
        if frame["status"] == "ok":
            contract = get_contract(flight.capability)
            output = frame.get("output")
            try:
                if utf8_len(json.dumps(output, ensure_ascii=False)) > int(
                    settings.NODE_MAX_RESULT_BYTES
                ):
                    raise ProtocolError("result_too_large")
                contract.validate_result(output)
            except ProtocolError as exc:
                logger.warning(
                    "node read result refused node=%s capability=%s code=%s",
                    self.node_id,
                    flight.capability,
                    exc.code,
                )
                payload = {"verdict": exc.code}
            else:
                payload["output"] = output
        self._resolve_read(flight.invocation_id, payload)
        return "applied"

    def _output(self, flight, frame):
        data = frame["data"]
        flight.output_bytes += utf8_len(data)
        # Only the scrubber's emitted text reaches `nodes:output:<id>`.
        flight.transit.push(frame["stream"], data, truncated=frame.get("truncated"))
        return "applied"

    def _flush(self, flight):
        flight.transit.flush()

    def _forget(self, flight):
        self.in_flight.pop(flight.invocation_id, None)

    # --- the clock and the socket ----------------------------------------

    def tick(self, now=None) -> list:
        """The action watchdog: in-flight actions past their deadline end
        `timeout`; returns their invocation ids. Reads past their envelope's
        expiry leave the read set (their caller has given up)."""
        now = int(now if now is not None else self.clock())
        for read in list(self.reads.values()):
            if now > read.expires_at:
                self.reads.pop(read.invocation_id, None)
        timed_out = []
        for flight in list(self.in_flight.values()):
            if flight.deadline is not None and now > flight.deadline:
                self._flush(flight)
                finished = services.record_gated_progress(
                    node_id=self.node_id,
                    invocation_id=flight.invocation_id,
                    terminal="timeout",
                    output_bytes=flight.output_bytes,
                )
                flight.transit.end(finished)
                self._forget(flight)
                timed_out.append(flight.invocation_id)
        return timed_out

    def disconnect(self, *, clear_presence=True) -> None:
        """Fail closed: every in-flight read is resolved
        `node_disconnected`, every in-flight action ends `node_disconnected`
        (carry scrubbed and flushed first); presence is cleared unless a
        newer socket of the same node holds it (`clear_presence=False`)."""
        if self.closed:
            return
        self.closed = True
        for read in list(self.reads.values()):
            self._resolve_read(read.invocation_id, {"verdict": "node_disconnected"})
        self.reads.clear()
        for flight in list(self.in_flight.values()):
            self._flush(flight)
            finished = services.record_gated_progress(
                node_id=self.node_id,
                invocation_id=flight.invocation_id,
                terminal="node_disconnected",
                output_bytes=flight.output_bytes,
            )
            flight.transit.end(finished)
            self._forget(flight)
        if not clear_presence:
            return
        try:
            bus.clear_presence(self.node_id)
        except bus.BusUnavailable:
            logger.warning("node gateway could not clear presence node=%s", self.node_id)


# --- the socket app ----------------------------------------------------


def _with_fresh_connection(fn):
    """Run `fn` with Django's stale-connection hygiene: the gateway is a
    long-lived process outside the request cycle that normally does it."""

    def run(*args, **kwargs):
        close_old_connections()
        return fn(*args, **kwargs)

    return run


async def _db(fn, *args, **kwargs):
    return await sync_to_async(_with_fresh_connection(fn), thread_sensitive=True)(*args, **kwargs)


def authenticate_handshake(headers, client_ip=None):
    """The handshake's credential check (D6): `Authorization: Bearer jnd_…`
    of a usable node device of the active owner (kill switch released),
    with its `Node` row. Returns the Node, or None (closed 4401). Failures
    are audited by `accounts` under the flood cap."""
    raw = headers.get(b"authorization", b"").decode("latin-1")
    scheme, _, secret = raw.partition(" ")
    if scheme.lower() != "bearer" or not secret:
        return None
    try:
        credential = authenticate_node_secret(
            raw_secret=secret.strip(), client_ip=client_ip, surface="gateway"
        )
    except NodeCredentialRejected:
        return None
    device = credential.device
    return (
        Node.objects.select_related("owner", "device", "workspace")
        .filter(pk=device.pk, device=device, owner=device.user)
        .first()
    )


@dataclass
class Connection:
    """One accepted node socket in this process."""

    node: object
    session: NodeSession
    send: object
    version: int
    connection_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    closed: bool = False
    close_code: int | None = None
    replaced: bool = False
    last_frame_at: float = field(default_factory=time.monotonic)
    frame_times: deque = field(default_factory=deque)
    frame_error_times: deque = field(default_factory=deque)
    tasks: list = field(default_factory=list)

    @property
    def node_id(self) -> str:
        return str(self.node.pk)

    @property
    def device_id(self) -> str:
        return str(self.node.device_id)

    @property
    def owner_id(self) -> str:
        return str(self.node.owner_id)


class NodeSocketApp:
    """The `/ws/node/v1` WebSocket application (D3, D6). One instance per
    gateway process; it keeps the process's live sockets by node id (one
    per node: a second connection replaces the first) and one revocation
    subscriber.

    `pubsub=False` turns the revocation subscriber off (tests of the
    heartbeat re-check alone). `counters` holds the drop/close counts the
    tests and logs read (never bodies)."""

    def __init__(
        self,
        *,
        pubsub=True,
        hello_timeout=HELLO_TIMEOUT_SECONDS,
        idle_timeout=IDLE_TIMEOUT_SECONDS,
        tick_seconds=TICK_SECONDS,
        max_frames_per_second=MAX_FRAMES_PER_SECOND,
    ):
        self.pubsub = pubsub
        self.hello_timeout = hello_timeout
        self.idle_timeout = idle_timeout
        self.tick_seconds = tick_seconds
        self.max_frames_per_second = max_frames_per_second
        self.connections: dict = {}
        self.counters: Counter = Counter()
        self.subscriber_ready = threading.Event()
        self._subscriber = None

    # --- ASGI ------------------------------------------------------------

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "websocket":
            raise ValueError("NodeSocketApp serves WebSocket scopes only")
        message = await receive()
        if message.get("type") != "websocket.connect":
            return
        await self._serve(scope, receive, send)

    async def _refuse(self, send, code, subprotocol=None, reason=""):
        """Accept-then-close, so the node sees the D6 close code (a close
        before accept would only be an HTTP 403)."""
        self.counters[f"refused_{code}"] += 1
        logger.info("node handshake refused code=%s reason=%s", code, reason)
        accept = {"type": "websocket.accept"}
        if subprotocol:
            accept["subprotocol"] = subprotocol
        await send(accept)
        await send({"type": "websocket.close", "code": code})

    async def _serve(self, scope, receive, send):
        headers = {bytes(k).lower(): bytes(v) for k, v in scope.get("headers") or ()}
        if b"origin" in headers:
            await self._refuse(send, CLOSE_FORBIDDEN_ORIGIN, reason="origin")
            return
        negotiated = negotiate_subprotocol(scope.get("subprotocols") or ())
        if negotiated is None:
            await self._refuse(send, CLOSE_BAD_REQUEST, reason="subprotocol")
            return
        version, subprotocol = negotiated
        if not await self._redis_up():
            await self._refuse(send, CLOSE_TRY_AGAIN, subprotocol, reason="bus_unavailable")
            return
        client = scope.get("client") or (None, None)
        node = await _db(authenticate_handshake, headers, client[0])
        if node is None:
            await self._refuse(send, CLOSE_UNAUTHORIZED, subprotocol, reason="credential")
            return
        await send({"type": "websocket.accept", "subprotocol": subprotocol})
        conn = Connection(node=node, session=NodeSession(node, version), send=send, version=version)
        logger.info(
            "node connected node=%s conn=%s version=%s", conn.node_id, conn.connection_id, version
        )
        await self._register(conn)
        try:
            await self._run(conn, receive)
        finally:
            await self._teardown(conn)

    # --- registry and fan-out --------------------------------------------

    async def _register(self, conn):
        self._ensure_subscriber()
        previous = self.connections.get(conn.node_id)
        self.connections[conn.node_id] = conn
        if previous is not None and not previous.closed:
            previous.replaced = True
            self.counters["replaced"] += 1
            await self._close(previous, CLOSE_NORMAL, bye="replaced")

    def _ensure_subscriber(self):
        if not self.pubsub or self._subscriber is not None:
            return
        self._subscriber = asyncio.get_running_loop().create_task(self._subscribe())

    async def _subscribe(self):
        import redis.asyncio as aioredis

        while True:
            client = None
            try:
                client = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=1)
                pubsub = client.pubsub()
                await pubsub.subscribe(REVOCATION_CHANNEL)
                self.subscriber_ready.set()
                while True:
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    if message is not None and message.get("type") == "message":
                        await self.on_revocation(message.get("data"))
            except asyncio.CancelledError:
                raise
            except Exception:
                self.subscriber_ready.clear()
                self.counters["subscriber_errors"] += 1
                logger.warning("node gateway revocation subscriber lost Redis; retrying")
                await asyncio.sleep(1)
            finally:
                if client is not None:
                    try:
                        await client.aclose()
                    except Exception:  # noqa: BLE001 - already broken
                        pass

    async def on_revocation(self, raw) -> int:
        """One `friday:device-revoked` message (D1, D6). A socket matches
        when the payload's `device_id` is its node's device, or when it is
        "*" **and** `user_id` is its node's owner (the kill switch). A
        payload that does not parse, lacks `user_id` or carries "*" without
        a user closes nothing and is counted. Returns the sockets closed."""
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        parsed = parse_revocation(raw)
        if parsed is None:
            self.counters["revocation_unparsed"] += 1
            return 0
        device_id, user_id = parsed
        closed = 0
        for conn in list(self.connections.values()):
            if conn.closed:
                continue
            if device_id == ALL_DEVICES:
                matches, reason = user_id == conn.owner_id, "kill_switch"
            else:
                matches, reason = device_id == conn.device_id, "revoked"
            if matches:
                await self._close(conn, CLOSE_NORMAL, bye=reason)
                closed += 1
        self.counters["revocation_closed"] += closed
        return closed

    async def startup(self):
        """Lifespan startup: subscribe to the revocation channel now rather
        than at the first connection."""
        self._ensure_subscriber()

    async def shutdown(self):
        """Lifespan shutdown: `bye{server_shutdown}` to every socket."""
        for conn in list(self.connections.values()):
            await self._close(conn, CLOSE_NORMAL, bye="server_shutdown")
        if self._subscriber is not None:
            self._subscriber.cancel()
            self._subscriber = None

    # --- one socket ------------------------------------------------------

    async def _redis_up(self) -> bool:
        try:
            return await sync_to_async(bus.ping, thread_sensitive=False)()
        except bus.BusUnavailable:
            return False

    async def _send_frame(self, conn, frame) -> bool:
        if conn.closed:
            return False
        text = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
        try:
            await conn.send({"type": "websocket.send", "text": text})
        except Exception:  # noqa: BLE001 - the peer is gone
            return False
        logger.debug(
            "node frame out node=%s type=%s bytes=%d",
            conn.node_id,
            frame.get("type"),
            utf8_len(text),
        )
        return True

    async def _close(self, conn, code, *, bye=None):
        if conn.closed:
            return
        if bye is not None:
            await self._send_frame(conn, conn.session.bye(bye))
        conn.closed = True
        conn.close_code = code
        logger.info(
            "node socket closed node=%s conn=%s code=%s bye=%s",
            conn.node_id,
            conn.connection_id,
            code,
            bye or "",
        )
        try:
            await conn.send({"type": "websocket.close", "code": code})
        except Exception:  # noqa: BLE001 - the peer is gone
            pass

    async def _receive_text(self, conn, receive, timeout=None):
        """The next text message, or None when the socket is gone or closed
        (`websocket.disconnect`, or our own close)."""
        while not conn.closed:
            try:
                if timeout is None:
                    message = await receive()
                else:
                    message = await asyncio.wait_for(receive(), timeout)
            except TimeoutError:
                raise
            kind = message.get("type")
            if kind == "websocket.disconnect":
                conn.closed = True
                return None
            if kind != "websocket.receive":
                continue
            text = message.get("text")
            if text is None:
                data = message.get("bytes") or b""
                if len(data) > MAX_FRAME_BYTES:
                    return data
                return b""  # a binary frame: a frame error below
            return text
        return None

    def _rate_exceeded(self, conn) -> bool:
        now = time.monotonic()
        conn.last_frame_at = now
        conn.frame_times.append(now)
        while conn.frame_times and conn.frame_times[0] <= now - 1.0:
            conn.frame_times.popleft()
        return len(conn.frame_times) > self.max_frames_per_second

    async def _frame_error(self, conn, code) -> None:
        """D5/D6: count a bad node frame; three within 60 s close the
        session with `bye{protocol_error}`."""
        now = time.monotonic()
        conn.frame_error_times.append(now)
        while conn.frame_error_times and (
            conn.frame_error_times[0] <= now - FRAME_ERROR_WINDOW_SECONDS
        ):
            conn.frame_error_times.popleft()
        self.counters["frame_errors"] += 1
        logger.warning("node frame error node=%s code=%s", conn.node_id, code)
        if len(conn.frame_error_times) >= FRAME_ERROR_LIMIT:
            await self._close(conn, CLOSE_BAD_REQUEST, bye="protocol_error")

    def _decode(self, conn, raw):
        """Checks 1-4 of one inbound frame: (frame, None) or (None, code)."""
        if isinstance(raw, bytes):
            return None, ("too_large" if len(raw) > MAX_FRAME_BYTES else "malformed")
        try:
            return decode_frame(raw, "node_to_server", conn.version), None
        except ProtocolError as exc:
            return None, exc.code

    async def _run(self, conn, receive):
        try:
            raw = await self._receive_text(conn, receive, timeout=self.hello_timeout)
        except TimeoutError:
            await self._close(conn, CLOSE_TIMEOUT)
            return
        if raw is None:
            return
        frame, code = self._decode(conn, raw)
        if code == "too_large":
            await self._close(conn, CLOSE_TOO_BIG)
            return
        if frame is None or frame["type"] != "hello":
            logger.warning("node first frame is not a hello node=%s", conn.node_id)
            await self._close(conn, CLOSE_BAD_REQUEST, bye="protocol_error")
            return
        accepted = (await _db(conn.session.hello, frame))["accepted"]
        conn.last_frame_at = time.monotonic()
        await self._send_frame(conn, conn.session.hello_ack(accepted))
        loop = asyncio.get_running_loop()
        conn.tasks = [
            loop.create_task(self._inbox_loop(conn)),
            loop.create_task(self._tick_loop(conn)),
        ]
        while not conn.closed:
            raw = await self._receive_text(conn, receive)
            if raw is None:
                return
            await self._handle(conn, raw)

    async def _handle(self, conn, raw):
        size = len(raw) if isinstance(raw, bytes) else utf8_len(raw)
        if self._rate_exceeded(conn):
            self.counters["rate_closed"] += 1
            await self._close(conn, CLOSE_RATE)
            return
        if size > MAX_FRAME_BYTES:
            self.counters["oversize_closed"] += 1
            await self._close(conn, CLOSE_TOO_BIG)
            return
        frame, code = self._decode(conn, raw)
        if frame is None:
            await self._frame_error(conn, code)
            return
        kind = frame["type"]
        logger.debug("node frame in node=%s type=%s bytes=%d", conn.node_id, kind, size)
        if kind == "heartbeat":
            ack = await _db(conn.session.heartbeat, frame)
            await self._send_frame(conn, ack)
            reason = await _db(conn.session.device_refusal)
            if reason is not None:
                self.counters["heartbeat_closed"] += 1
                await self._close(conn, CLOSE_NORMAL, bye=reason)
            return
        if kind == "event":
            await self._send_frame(conn, conn.session.event_ack(frame))
            return
        if kind == "hello":
            await self._frame_error(conn, "unexpected_hello")
            return
        outcome = await _db(conn.session.receive, frame)
        if outcome == "discarded":
            self.counters["discarded"] += 1
            await self._frame_error(conn, "unknown_invocation")
        elif outcome == "frame_error":
            await self._frame_error(conn, "schema")

    async def _inbox_loop(self, conn):
        """The per-node inbox consumer (D6): `BLPOP nodes:inbox:<node_id>`,
        placed through the session, forwarded to this socket."""
        import redis.asyncio as aioredis

        client = aioredis.from_url(
            settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=INBOX_POLL_SECONDS + 2
        )
        key = bus.inbox_key(conn.node_id)
        try:
            while not conn.closed:
                try:
                    popped = await client.blpop([key], timeout=INBOX_POLL_SECONDS)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - Redis down: invokes fail closed
                    self.counters["inbox_errors"] += 1
                    await asyncio.sleep(INBOX_POLL_SECONDS)
                    continue
                if popped is None:
                    continue
                try:
                    envelope = json.loads(popped[1])
                except (TypeError, ValueError):
                    continue
                forward = await _db(conn.session.place, envelope)
                if forward is not None and not await self._send_frame(conn, forward):
                    break
        finally:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass

    async def _tick_loop(self, conn):
        while not conn.closed:
            await asyncio.sleep(self.tick_seconds)
            if conn.closed:
                return
            if time.monotonic() - conn.last_frame_at > self.idle_timeout:
                self.counters["idle_closed"] += 1
                await self._close(conn, CLOSE_TIMEOUT)
                return
            await _db(conn.session.tick)

    async def _teardown(self, conn):
        conn.closed = True
        for task in conn.tasks:
            task.cancel()
        for task in conn.tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        current = self.connections.get(conn.node_id)
        if current is conn:
            del self.connections[conn.node_id]
        superseded = current is not None and current is not conn
        await _db(conn.session.disconnect, clear_presence=not superseded)
        logger.info("node disconnected node=%s conn=%s", conn.node_id, conn.connection_id)
