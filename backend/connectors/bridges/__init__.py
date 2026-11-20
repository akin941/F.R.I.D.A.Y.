"""Native bridges next to MCP manifests (ADR-010, brief D8/D9).

A `native` manifest has no MCP server: Friday calls the vendor's REST API
itself through a bridge object the manifest module exports as `BRIDGE`.
The read and write paths (`connectors.reads`, `connectors.writes`) stay the
same for both kinds -- serializer, scope check, snapshot, gate, audit --
and only the last hop differs: `call_tool` below dispatches to the MCP
client for `mcp` manifests and to the bridge for `native` ones.

A bridge implements:

- `call_tool(manifest, *, server_tool, arguments, schema_sha256, timeout)`
  -> `mcp_client.CallResult` (the same reduced shape an MCP call returns;
  `listing` is `None`), raising `mcp_client.ConnectorError` with `sent`
  set exactly like the MCP client (a write whose request reached the
  transport ends `unknown_outcome`, never re-sent);
- `check_health(manifest, *, timeout)` -> `NativeHealth`: the per-tool
  availability the pin beat records (`connectors.services`).
"""

from dataclasses import dataclass, field
from importlib import import_module

from connectors import mcp_client
from connectors.manifest import KIND_NATIVE


@dataclass(frozen=True)
class NativeHealth:
    """What a native bridge's health check found. `tools` maps every
    manifest tool name to a status (`ok` or a non-sticky refusal such as
    `scope_missing` / `scope_extra`); `error` is a closed-vocabulary code
    (`reconnect_required`, `not_configured`, ...) when the check itself
    failed, in which case `tools` is empty and nothing is re-enabled."""

    server_name: str
    server_version: str
    tools: dict = field(default_factory=dict)
    error: str = ""
    # Non-secret facts worth showing the owner (e.g. granted OAuth scopes).
    details: dict = field(default_factory=dict)


def get_bridge(manifest):
    """The bridge object of a native manifest (its module's `BRIDGE`)."""
    if manifest.kind != KIND_NATIVE:
        return None
    return getattr(import_module(f"connectors.manifests.{manifest.name}"), "BRIDGE", None)


def call_tool(manifest, *, server_tool, arguments, schema_sha256, timeout):
    if manifest.kind == KIND_NATIVE:
        bridge = get_bridge(manifest)
        if bridge is None:
            raise mcp_client.ConnectorError("not_configured")
        return bridge.call_tool(
            manifest,
            server_tool=server_tool,
            arguments=arguments,
            schema_sha256=schema_sha256,
            timeout=timeout,
        )
    return mcp_client.call_tool(
        manifest,
        server_tool=server_tool,
        arguments=arguments,
        schema_sha256=schema_sha256,
        timeout=timeout,
    )
