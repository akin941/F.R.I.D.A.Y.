"""stdio <-> Streamable HTTP bridge (ADR-010 decision 3, brief D1).

Runs ONLY inside a connector sidecar container -- never in web, worker or on
the harness runner. A stdio-only MCP server is a subprocess of whoever
spawns it; spawned here, it shares a container with nothing but this bridge
and its own credential, and Friday reaches it the one way it reaches every
connector: Streamable HTTP over the internal `connectors` network.

What it forwards: `tools/list` and `tools/call`, nothing else (no resources,
prompts, sampling, elicitation or roots -- a server that asks for them gets
nothing). `BRIDGE_ALLOWED_TOOLS` (comma-separated server tool names,
required) is a second, sidecar-side ceiling below the Friday manifest:
anything else is neither listed nor callable. The child's environment is
exactly the names in `BRIDGE_ENV_PASSTHROUGH` (e.g. its token) plus
`PATH`/`HOME` -- nothing else of the container's environment leaks in.

Configuration (environment, set in the sidecar's compose service):

- `BRIDGE_COMMAND`  -- JSON argv of the stdio server, e.g.
  `["/server/github-mcp-server", "stdio", "--tools=get_commit"]`
- `BRIDGE_ALLOWED_TOOLS` -- comma-separated tool names
- `BRIDGE_ENV_PASSTHROUGH` -- comma-separated env var names for the child
- `BRIDGE_HOST` / `BRIDGE_PORT` -- listen address (default 0.0.0.0:8080;
  the container publishes no port, only the internal network reaches it)

Entry point: `python -m connectors.docker.stdio_bridge` inside the sidecar
image (`connectors/docker/Dockerfile.bridge`).
"""

import json
import os
from contextlib import asynccontextmanager

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.server import Server
from mcp.types import CallToolResult, ListToolsResult, PaginatedRequestParams, TextContent

_BASE_CHILD_ENV = ("PATH", "HOME")


def _csv(value):
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def child_parameters(environ):
    argv = json.loads(environ["BRIDGE_COMMAND"])
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        raise ValueError("BRIDGE_COMMAND must be a non-empty JSON list of strings.")
    names = [*_BASE_CHILD_ENV, *_csv(environ.get("BRIDGE_ENV_PASSTHROUGH"))]
    env = {name: environ[name] for name in names if name in environ}
    return StdioServerParameters(command=argv[0], args=argv[1:], env=env)


@asynccontextmanager
async def stdio_child(parameters):
    """A live `ClientSession` to the stdio server (initialize handshake:
    stdio servers speak the handshake-era protocol)."""
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            yield session


def build_bridge(child_factory, *, allowed_tools, name="friday-stdio-bridge"):
    """The bridge server. `child_factory()` is an async context manager
    yielding a `ClientSession` to the stdio server (in tests: to an
    in-process server)."""
    allowed = frozenset(allowed_tools)
    if not allowed:
        raise ValueError("BRIDGE_ALLOWED_TOOLS must name at least one tool.")

    @asynccontextmanager
    async def lifespan(_server):
        async with child_factory() as child:
            yield {"child": child}

    async def list_tools(ctx, params):
        child = ctx.lifespan_context["child"]
        tools, cursor = [], None
        for _ in range(10):
            page = await child.list_tools(
                params=PaginatedRequestParams(cursor=cursor) if cursor else None
            )
            tools.extend(tool for tool in page.tools if tool.name in allowed)
            cursor = page.next_cursor
            if not cursor:
                break
        return ListToolsResult(tools=tools)

    async def call_tool(ctx, params):
        if params.name not in allowed:
            return CallToolResult(
                content=[TextContent(text="This tool is not available.")], is_error=True
            )
        child = ctx.lifespan_context["child"]
        return await child.call_tool(params.name, dict(params.arguments or {}))

    return Server(name, lifespan=lifespan, on_list_tools=list_tools, on_call_tool=call_tool)


def main():  # pragma: no cover -- exercised in the sidecar, not in tests
    import uvicorn

    environ = dict(os.environ)
    parameters = child_parameters(environ)
    server = build_bridge(
        lambda: stdio_child(parameters), allowed_tools=_csv(environ.get("BRIDGE_ALLOWED_TOOLS"))
    )
    app = server.streamable_http_app(stateless_http=True, json_response=True, host="0.0.0.0")
    uvicorn.run(
        app,
        host=environ.get("BRIDGE_HOST", "0.0.0.0"),
        port=int(environ.get("BRIDGE_PORT", "8080")),
        log_level="warning",
    )


if __name__ == "__main__":  # pragma: no cover
    main()
