"""The one sync facade over the official MCP Python SDK (ADR-010 decision 3,
brief D1/D2). Every MCP operation Jarvis performs goes through here.

- **Sync over async, one loop per operation.** Django and Celery are sync;
  the SDK is async. Each operation runs inside its own `anyio.run()` and
  opens a fresh connection (per-call session setup, accepted at owner
  volume, R8). Call sites are all sync: reads in the chat request thread,
  writes, pin checks and health checks in Celery tasks. Nesting inside a
  running event loop is refused, never attempted:
  `ConnectorError("event_loop_active")`.
- **Streamable HTTP only, to a manifest-named env URL.** No stdio or SSE
  transport is ever built here (an import-linter contract forbids them);
  a stdio-only server runs in its own sidecar behind
  `connectors/docker/stdio_bridge.py`.
- **No redirects.** The SDK itself follows same-origin 307/308 redirects up
  to the HTTP client's `max_redirects`, whatever `follow_redirects` says
  (verified against `mcp` 2.2.0, `mcp.shared._httpx_utils.
  stream_within_origin`), so the client is built with `max_redirects=0`
  as well as `follow_redirects=False`: a redirect is an error.
- **No client capabilities.** No sampling, elicitation or roots callback is
  ever passed, so none is advertised; `input_required_max_rounds=0`, so an
  `InputRequiredResult` fails the call instead of re-sending it.
- **No proxies from the environment** (`trust_env=False`): the sidecars
  are on an internal network.
- **Closed error vocabulary.** Failures surface as `ConnectorError(code)`;
  logs carry the connector, the operation, the code and the exception
  class name -- never exception text, arguments or results.

The HTTP client is built by `_connect_http`, a module-attribute seam: tests
substitute an ASGI transport onto the SDK's own in-memory server, keeping
every other line of this module on the production path.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import anyio
import httpx2
from mcp.client.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import Implementation, TextContent

from connectors.manifest import schema_hash

logger = logging.getLogger(__name__)

# `list_tools` pagination bound: a server that keeps handing out cursors is
# broken or hostile.
MAX_LIST_PAGES = 10

CLIENT_INFO = Implementation(name="jarvis-os", version="1")


class ConnectorError(Exception):
    """A connector operation failed. `code` is one of `CODES` -- safe to
    store, audit and show; the underlying exception is logged by class name
    only."""

    CODES = frozenset(
        {
            "event_loop_active",
            "not_configured",
            "timeout",
            "transport_error",
            "protocol_error",
            "tool_missing",
            "pin_mismatch",
        }
    )

    def __init__(self, code, *, listing=None):
        if code not in self.CODES:
            raise ValueError(f"unknown connector error code {code!r}")
        self.code = code
        # The tools/list result the failure was detected against, when there
        # is one (a pin mismatch), so the caller can record the new status.
        self.listing = listing
        # `call_tool` only: whether the `tools/call` request had been handed
        # to the transport when the failure happened. After that point the
        # server may have acted -- a write then ends `unknown_outcome`,
        # never `failed`, and is never re-sent (D6).
        self.sent = False
        super().__init__(code)


@dataclass(frozen=True)
class Listing:
    """What `tools/list` said, reduced to what a pin check needs: server
    identity (display-only), the negotiated protocol version and each
    offered tool's schema pin. Server descriptions are dropped here and
    never travel further."""

    server_name: str
    server_version: str
    protocol_version: str
    tool_hashes: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CallResult:
    """A `tools/call` result with only text and `structuredContent` kept --
    images, audio, resources and resource links are dropped here (D4)."""

    texts: tuple
    structured: Any
    is_error: bool
    dropped_blocks: int
    listing: Listing


def ensure_no_running_loop():
    """D2: the facade never nests inside a running event loop (it would
    either deadlock or run the SDK on a loop it does not own)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise ConnectorError("event_loop_active")


def server_url(manifest):
    """The manifest's server URL from its env var: http(s), no userinfo.
    Anything else is `not_configured` -- never a guess."""
    url = os.environ.get(manifest.url_env or "", "").strip()
    if not url:
        raise ConnectorError("not_configured")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or "@" in parts.netloc:
        raise ConnectorError("not_configured")
    return url


def build_http_client(*, timeout, transport=None):
    """The hardened HTTP client every MCP connection uses (see module
    docstring). `transport` is for tests only."""
    return httpx2.AsyncClient(
        timeout=httpx2.Timeout(timeout),
        follow_redirects=False,
        max_redirects=0,
        trust_env=False,
        transport=transport,
    )


@asynccontextmanager
async def _connect_http(url, *, timeout):
    async with build_http_client(timeout=timeout) as client:
        yield client


@asynccontextmanager
async def _session(manifest, *, timeout):
    url = server_url(manifest)
    async with _connect_http(url, timeout=timeout) as http:
        client = Client(
            streamable_http_client(url, http_client=http),
            mode="auto",
            cache=None,
            read_timeout_seconds=timeout,
            client_info=CLIENT_INFO,
            input_required_max_rounds=0,
        )
        async with client:
            yield client


async def _listing(client):
    hashes = {}
    cursor = None
    for _ in range(MAX_LIST_PAGES):
        page = await client.list_tools(cursor=cursor, cache_mode="bypass")
        for tool in page.tools:
            hashes[tool.name] = schema_hash(tool.input_schema)
        cursor = page.next_cursor
        if not cursor:
            break
    info = client.server_info
    return Listing(
        server_name=_label(info.name if info else "", 100),
        server_version=_label(info.version if info else "", 50),
        protocol_version=_label(client.protocol_version or "", 20),
        tool_hashes=hashes,
    )


def _label(value, limit):
    """A server-supplied identifier reduced to printable characters and
    bounded -- display-only bookkeeping, never prompt text."""
    return "".join(ch for ch in str(value) if ch.isprintable())[:limit]


def _leaves(exc):
    if isinstance(exc, BaseExceptionGroup):
        for inner in exc.exceptions:
            yield from _leaves(inner)
    else:
        yield exc


def _classify(exc):
    leaves = list(_leaves(exc))
    for leaf in leaves:
        if isinstance(leaf, ConnectorError):
            return leaf
    if any(isinstance(leaf, TimeoutError) for leaf in leaves):
        return ConnectorError("timeout")
    if any(isinstance(leaf, httpx2.HTTPError | OSError) for leaf in leaves):
        return ConnectorError("transport_error")
    return ConnectorError("protocol_error")


def _run(manifest, operation, coro_fn, *args):
    ensure_no_running_loop()
    try:
        return anyio.run(coro_fn, *args)
    except Exception as exc:  # noqa: BLE001 -- mapped to a closed vocabulary
        error = _classify(exc)
        leaf = next(_leaves(exc), exc)
        logger.warning(
            "connector %s %s failed: %s (%s)",
            manifest.name,
            operation,
            error.code,
            type(leaf).__name__,
        )
        raise error from None


def list_tools(manifest, *, timeout):
    """`tools/list` (all pages) -> `Listing`."""

    async def op():
        with anyio.fail_after(timeout):
            async with _session(manifest, timeout=timeout) as client:
                return await _listing(client)

    return _run(manifest, "list_tools", op)


def call_tool(manifest, *, server_tool, arguments, schema_sha256, timeout):
    """One `tools/call`, pin-checked in the same session first: the tool
    must be listed and its `inputSchema` hash must equal the manifest pin,
    else `tool_missing` / `pin_mismatch` and the call is never sent. The
    arguments are sent exactly as given. Returns a `CallResult`; a result
    with `isError` is returned (not raised) so the caller can record it.
    A raised `ConnectorError` carries `sent` (see its docstring)."""
    state = {"sent": False}

    async def op():
        with anyio.fail_after(timeout):
            async with _session(manifest, timeout=timeout) as client:
                listing = await _listing(client)
                actual = listing.tool_hashes.get(server_tool)
                if actual is None:
                    raise ConnectorError("tool_missing", listing=listing)
                if actual != schema_sha256:
                    raise ConnectorError("pin_mismatch", listing=listing)
                state["sent"] = True
                result = await client.call_tool(server_tool, arguments)
                return _reduce(result, listing)

    try:
        return _run(manifest, "call_tool", op)
    except ConnectorError as exc:
        exc.sent = state["sent"]
        raise


def _reduce(result, listing):
    texts, dropped = [], 0
    for block in result.content or []:
        if isinstance(block, TextContent):
            texts.append(block.text)
        else:
            dropped += 1
    return CallResult(
        texts=tuple(texts),
        structured=result.structured_content,
        is_error=bool(result.is_error),
        dropped_blocks=dropped,
        listing=listing,
    )
