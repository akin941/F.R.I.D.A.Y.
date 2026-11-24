"""Streamable HTTP relay (Phase 12 D9, ADR-010 amendment 2026-11).

Runs ONLY inside a connector sidecar (compose service `n8n-relay`, the same
image as the stdio bridge) -- never in web, worker or on the harness
runner. Some MCP servers (n8n's MCP Server Trigger) require a credential
on **every** request; ADR-010 decision 3 keeps credentials out of web and
worker, so Friday talks to this relay on the internal `connectors`
network, and the relay alone adds the bearer on its way upstream.

What it forwards: `tools/list` and `tools/call`, nothing else -- no
resources, prompts, sampling, elicitation or roots (a request for them is
"method not found"). `BRIDGE_ALLOWED_TOOLS` (required, comma-separated) is
the sidecar-side ceiling below the Friday manifest: anything else is
neither listed nor callable, and an unlisted call never reaches upstream.
Upstream, the relay is a hardened client: one fixed URL
(`RELAY_UPSTREAM_URL`), no redirects (`follow_redirects=False`,
`max_redirects=0`), no proxies from the environment, no client
capabilities (no sampling/elicitation/roots callback is ever passed;
`input_required_max_rounds=0`).

The bearer (`RELAY_BEARER_TOKEN`) is read from the sidecar environment at
start, sent only in the upstream request's `Authorization` header, never
returned to Friday (results are rebuilt from their content blocks), never
logged: failures are logged by exception class name only and answered with
a fixed message.

Configuration (environment of the sidecar's compose service):

- `RELAY_UPSTREAM_URL`  -- the MCP endpoint, e.g. n8n's MCP Server Trigger
  production URL (`https://n8n.home.arpa/mcp/<path>`)
- `RELAY_BEARER_TOKEN`  -- the trigger's bearer credential
- `BRIDGE_ALLOWED_TOOLS` -- comma-separated server tool names
  (`manage.py n8n_relay_env` prints the line from the n8n allowlist)
- `BRIDGE_HOST` / `BRIDGE_PORT` -- listen address (default 0.0.0.0:8080;
  no published port, only the internal network reaches it)
- `RELAY_TIMEOUT_SECONDS` -- upstream timeout (default 150)

Entry point: `python -m connectors.docker.http_relay`.
"""

import logging
import os
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx2
from mcp.client.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server import Server
from mcp.types import CallToolResult, Implementation, ListToolsResult, TextContent

from connectors.docker.common import allowed_tool_set, csv_list

logger = logging.getLogger("friday.http_relay")

CLIENT_INFO = Implementation(name="friday-relay", version="1")
MAX_LIST_PAGES = 10
DEFAULT_TIMEOUT_SECONDS = 150
UNAVAILABLE_TEXT = "This tool is not available."


class RelayConfigError(ValueError):
    """The sidecar environment is incomplete or unsafe; the message never
    carries the credential."""


class RelayUpstreamError(RuntimeError):
    """An upstream failure, answered to Friday with a fixed message."""


def relay_config(environ):
    """`(upstream_url, bearer, allowed_tools, timeout)` from the sidecar
    environment, or `RelayConfigError`."""
    url = (environ.get("RELAY_UPSTREAM_URL") or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or "@" in parts.netloc:
        raise RelayConfigError("RELAY_UPSTREAM_URL must be an http(s) URL without userinfo.")
    token = (environ.get("RELAY_BEARER_TOKEN") or "").strip()
    if not token:
        raise RelayConfigError("RELAY_BEARER_TOKEN is required.")
    if any(ch.isspace() or not ch.isprintable() for ch in token):
        raise RelayConfigError("RELAY_BEARER_TOKEN must be one printable token.")
    try:
        allowed = allowed_tool_set(csv_list(environ.get("BRIDGE_ALLOWED_TOOLS")))
    except ValueError as exc:
        raise RelayConfigError(str(exc)) from None
    try:
        timeout = int(environ.get("RELAY_TIMEOUT_SECONDS") or DEFAULT_TIMEOUT_SECONDS)
    except ValueError:
        raise RelayConfigError("RELAY_TIMEOUT_SECONDS must be an integer.") from None
    return url, token, allowed, max(timeout, 1)


def upstream_http_client(token, *, timeout, transport=None):
    """The hardened upstream HTTP client: the bearer as a default header,
    no redirects, no environment proxies. `transport` is for tests."""
    inner = transport if transport is not None else httpx2.AsyncHTTPTransport(trust_env=False)
    return httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx2.Timeout(timeout),
        follow_redirects=False,
        max_redirects=0,
        trust_env=False,
        transport=inner,
    )


def upstream_connector(url, token, *, timeout, transport=None):
    """An async-context-manager factory yielding an MCP `Client` to the
    upstream server, one fresh connection per relayed request."""

    @asynccontextmanager
    async def connect():
        async with upstream_http_client(token, timeout=timeout, transport=transport) as http:
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

    return connect


def build_relay(upstream, *, allowed_tools, name="friday-http-relay"):
    """The relay server. `upstream()` is an async context manager yielding
    an MCP `Client` to the upstream server (in tests: through an ASGI
    transport onto an in-memory server)."""
    allowed = allowed_tool_set(allowed_tools)

    async def list_tools(ctx, params):
        try:
            tools, cursor = [], None
            async with upstream() as client:
                for _ in range(MAX_LIST_PAGES):
                    page = await client.list_tools(cursor=cursor, cache_mode="bypass")
                    tools.extend(tool for tool in page.tools if tool.name in allowed)
                    cursor = page.next_cursor
                    if not cursor:
                        break
        except Exception as exc:  # noqa: BLE001 -- a fixed answer, class name logged
            logger.warning("relay tools/list failed: %s", type(exc).__name__)
            raise RelayUpstreamError("upstream unavailable") from None
        return ListToolsResult(tools=tools)

    async def call_tool(ctx, params):
        if params.name not in allowed:
            return CallToolResult(content=[TextContent(text=UNAVAILABLE_TEXT)], is_error=True)
        try:
            async with upstream() as client:
                result = await client.call_tool(params.name, dict(params.arguments or {}))
        except Exception as exc:  # noqa: BLE001 -- a fixed answer, class name logged
            logger.warning("relay tools/call %s failed: %s", params.name, type(exc).__name__)
            raise RelayUpstreamError("upstream unavailable") from None
        return CallToolResult(
            content=list(result.content or []),
            structured_content=result.structured_content,
            is_error=bool(result.is_error),
        )

    return Server(name, on_list_tools=list_tools, on_call_tool=call_tool)


def build_app(environ, *, transport=None):
    """The relay's Streamable HTTP ASGI app from a sidecar environment."""
    url, token, allowed, timeout = relay_config(environ)
    server = build_relay(
        upstream_connector(url, token, timeout=timeout, transport=transport),
        allowed_tools=allowed,
    )
    return server.streamable_http_app(stateless_http=True, json_response=True, host="0.0.0.0")


def main():  # pragma: no cover -- exercised in the sidecar, not in tests
    import uvicorn

    environ = dict(os.environ)
    app = build_app(environ)
    uvicorn.run(
        app,
        host=environ.get("BRIDGE_HOST", "0.0.0.0"),
        port=int(environ.get("BRIDGE_PORT", "8080")),
        log_level="warning",
    )


if __name__ == "__main__":  # pragma: no cover
    main()
