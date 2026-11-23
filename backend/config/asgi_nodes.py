"""The node gateway's ASGI process (Phase 9 brief D3; Phase 10 brief D7,
D12).

Served by `uvicorn --ws wsproto` as its own process (compose service
`node-gateway`, Phase 9 branch 4). This module is the composition root of
that process, and the **only** place that registers node sinks and
scrubbers (a source test pins it, Phase 10 D14):

- `register_node_ports()` hands `nodes.output` the 8A scrubber bound to a
  workspace's `ScrubTerm`s, so every `output` chunk and every `tail` the
  gateway handles is scrubbed server-side (never trusting the node's own
  scrub). `nodes` imports neither `memory` nor `config`, hence the port.
- `application` is a minimal raw-ASGI router (Phase 9 branch 4):
  - `websocket` on `/ws/node/v1` -> `nodes.gateway.NodeSocketApp` (one
    instance per process: the live sockets, the inbox loops and the
    revocation subscriber);
  - `GET /healthz` -> a liveness JSON: process up, Redis reachable, the
    number of connected nodes and the configured signing key id (never key
    material; the closeout's `node_signing_key` preflight compares it);
  - lifespan shutdown sends `bye{server_shutdown}` to every node;
  - everything else -> 404 (a WebSocket on another path is closed 4404).

Django is set up for ORM access; ORM calls go through
`asgiref.sync.sync_to_async(thread_sensitive=True)` inside `nodes.gateway`.
"""

import json
import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")
django.setup()

from memory.ingestion.scrubber import scrub_text  # noqa: E402
from memory.ingestion.services import scrub_terms_for  # noqa: E402
from nodes import bus  # noqa: E402
from nodes.gateway import NodeSocketApp  # noqa: E402
from nodes.keys import signing_key_id  # noqa: E402
from nodes.output import register_scrubber  # noqa: E402

GATEWAY_WS_PATH = "/ws/node/v1"
CLOSE_NOT_FOUND = 4404

node_app = NodeSocketApp()


def workspace_scrubber(workspace):
    """The 8A scrubber bound to `workspace`'s ScrubTerms (loaded once per
    invocation, not per chunk)."""
    terms = tuple(scrub_terms_for(workspace)) if workspace is not None else ()

    def scrub(text):
        return scrub_text(text, terms=terms).text if text else ""

    return scrub


def register_node_ports():
    register_scrubber(workspace_scrubber)


register_node_ports()


def _healthz():
    try:
        redis_ok = bus.ping()
    except bus.BusUnavailable:
        redis_ok = False
    return {
        "status": "ok",
        "redis": redis_ok,
        "connected_nodes": len(node_app.connections),
        "signing_key_id": signing_key_id(),
    }


async def application(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await node_app.startup()
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await node_app.shutdown()
                await send({"type": "lifespan.shutdown.complete"})
                return
    if scope["type"] == "websocket":
        if scope.get("path") == GATEWAY_WS_PATH:
            await node_app(scope, receive, send)
        else:
            await receive()  # websocket.connect
            await send({"type": "websocket.close", "code": CLOSE_NOT_FOUND})
        return
    if scope["type"] == "http" and scope.get("method") == "GET" and scope.get("path") == "/healthz":
        from asgiref.sync import sync_to_async

        body = json.dumps(await sync_to_async(_healthz, thread_sensitive=True)()).encode()
        status = 200
    else:
        body = b'{"detail":"Not found."}'
        status = 404
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})
