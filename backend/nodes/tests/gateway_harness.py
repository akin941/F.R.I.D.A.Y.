"""An in-process ASGI transport between `nodes.gateway.NodeSocketApp` and
the Python reference node (Phase 9 brief branch 4 tests).

The app runs on its own event loop in a background thread, exactly as under
uvicorn: its ORM calls go through `sync_to_async(thread_sensitive=True)`
into asgiref's single sync thread, and its Redis is the real test Redis.
Tests that use it are `django_db(transaction=True)`: the app's thread has
its own database connection, so rows must be committed to be seen.

`Harness.connect(...)` opens one WebSocket: the ASGI `receive` / `send`
callables are fed from an asyncio queue, and when a `ReferenceNode` is
attached the harness plays the node side -- `websocket.accept` ->
`node.connect(version)` (its `hello`), every `websocket.send` ->
`node.receive(text)`, every frame the node answers -> `websocket.receive`,
`websocket.close` -> `node.disconnect()` and `websocket.disconnect` back to
the app. The node is only ever touched on the loop thread
(`WsLink.node_call`).
"""

import asyncio
import json
import threading
import time
from dataclasses import dataclass, field

from nodes.protocol.messages import GATEWAY_PATH, SUBPROTOCOLS
from nodes.reference.executor import FakeExecutor
from nodes.reference.node import ReferenceNode

V1 = SUBPROTOCOLS[1]
V2 = SUBPROTOCOLS[2]
_VERSIONS = {name: version for version, name in SUBPROTOCOLS.items()}


class WallClock:
    """The reference node's clock on real time (envelopes are signed with
    `time.time()`)."""

    def __init__(self, offset=0):
        self.offset = offset

    def now(self) -> int:
        return int(time.time()) + self.offset

    def advance(self, seconds):
        self.offset += int(seconds)
        return self.now()


def reference_node(node_row, keyring, *, clock=None, catalogue=None, **kwargs):
    """A reference node for `node_row`, pinned to the server's key."""
    clock = clock or WallClock()
    return ReferenceNode(
        node_id=str(node_row.pk),
        pinned={keyring.key_id: keyring.active.public_key_bytes},
        policy=None,
        executor=FakeExecutor(clock),
        clock=clock,
        catalogue=catalogue,
        **kwargs,
    )


@dataclass
class WsLink:
    harness: "Harness"
    node: object = None
    server_frames: list = field(default_factory=list)
    accepted: bool = False
    subprotocol: str | None = None
    close_code: int | None = None
    closed_at: float | None = None
    client_closed: bool = False
    task: object = None

    def __post_init__(self):
        self.closed = threading.Event()
        self.incoming = None

    # --- ASGI side (loop thread) -----------------------------------------

    async def receive(self):
        return await self.incoming.get()

    async def send(self, message):
        kind = message["type"]
        if kind == "websocket.accept":
            self.accepted = True
            self.subprotocol = message.get("subprotocol")
            version = _VERSIONS.get(self.subprotocol)
            if self.node is not None and version is not None:
                self._deliver(self.node.connect(version))
        elif kind == "websocket.send":
            text = message["text"]
            self.server_frames.append(json.loads(text))
            if self.node is not None and self.node.connected:
                self._deliver(self.node.receive(text))
                if not self.node.connected and self.close_code is None:
                    self._client_close()
        elif kind == "websocket.close":
            self.close_code = message.get("code", 1000)
            self.closed_at = time.monotonic()
            if self.node is not None and self.node.connected:
                self.node.disconnect("socket_lost")
            self.incoming.put_nowait({"type": "websocket.disconnect", "code": self.close_code})
            self.closed.set()

    def _deliver(self, frames):
        for frame in frames or ():
            if isinstance(frame, dict):
                self.push_text(json.dumps(frame, ensure_ascii=False, separators=(",", ":")))

    def push_text(self, text):
        self.incoming.put_nowait({"type": "websocket.receive", "text": text})

    def _client_close(self):
        """The node closed the session itself (a `bye`, three frame errors)."""
        self.client_closed = True
        self.incoming.put_nowait({"type": "websocket.disconnect", "code": 1000})

    # --- test side (main thread) -----------------------------------------

    def node_call(self, fn):
        """Run `fn(node)` on the loop thread; deliver the frames it returns."""

        async def run():
            frames = fn(self.node)
            if isinstance(frames, list) and self.node.connected:
                self._deliver(frames)
            return frames

        return self.harness.run(run())

    def send_text(self, text):
        self.harness.run(self._async(lambda: self.push_text(text)))

    def send_frame(self, frame):
        self.send_text(json.dumps(frame, ensure_ascii=False, separators=(",", ":")))

    def drop(self):
        """The node's network goes away (no close frame)."""

        async def run():
            if self.node is not None and self.node.connected:
                self.node.disconnect("socket_lost")
            self.incoming.put_nowait({"type": "websocket.disconnect", "code": 1006})

        self.harness.run(run())

    async def _async(self, fn):
        return fn()

    def wait_closed(self, timeout=5.0) -> bool:
        """The server closed the socket (`close_code` is set)."""
        return self.closed.wait(timeout)

    def wait_done(self, timeout=5.0) -> bool:
        """The app's coroutine for this socket returned (teardown done)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.task is not None and self.task.done():
                return True
            time.sleep(0.01)
        return False

    def frames_of(self, kind) -> list:
        return [frame for frame in self.server_frames if frame.get("type") == kind]

    def wait_for(self, predicate, timeout=5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return False


class Harness:
    def __init__(self, app):
        self.app = app
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self._thread.start()
        self.links: list = []

    def run(self, coro, timeout=15.0):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def connect(
        self,
        *,
        secret=None,
        subprotocols=(V2, V1),
        origin=None,
        node=None,
        path=GATEWAY_PATH,
        headers=(),
        wait_hello_ack=True,
    ) -> WsLink:
        link = WsLink(self, node=node)
        raw_headers = [(b"host", b"gateway.test")]
        if secret is not None:
            raw_headers.append((b"authorization", f"Bearer {secret}".encode()))
        if origin is not None:
            raw_headers.append((b"origin", origin.encode()))
        raw_headers.extend(headers)
        scope = {
            "type": "websocket",
            "path": path,
            "headers": raw_headers,
            "subprotocols": list(subprotocols),
            "client": ("127.0.0.1", 50000),
        }

        async def start():
            link.incoming = asyncio.Queue()
            link.incoming.put_nowait({"type": "websocket.connect"})
            link.task = self.loop.create_task(self.app(scope, link.receive, link.send))

        self.run(start())
        self.links.append(link)
        if node is not None and wait_hello_ack:
            link.wait_for(lambda: link.frames_of("hello.ack") or link.close_code is not None)
        return link

    def close(self):
        for link in self.links:
            if link.task is not None and not link.task.done():
                try:
                    link.drop()
                except Exception:  # noqa: BLE001 - best effort teardown
                    pass
                link.wait_done(5.0)

        async def stop_subscriber():
            await self.app.shutdown()

        try:
            self.run(stop_subscriber())
        except Exception:  # noqa: BLE001
            pass

        async def close_db():
            from asgiref.sync import sync_to_async
            from django.db import connections

            await sync_to_async(connections.close_all, thread_sensitive=True)()

        try:
            self.run(close_db())
        except Exception:  # noqa: BLE001
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(5)
