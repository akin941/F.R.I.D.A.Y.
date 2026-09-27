"""One call surface for both connector kinds (ADR-010 decision 3, brief D3/D8).

Reads, writes and pin checks call `list_tools` / `call_tool` here and never
care whether the manifest is an MCP server (`connectors.mcp_client`, the
SDK facade) or a native REST bridge (`connectors/bridges/<name>.py`). Both
speak the same contract -- a `Listing` for the pin check, a `CallResult`
for a call, `ConnectorError(code)` (closed vocabulary, `sent` on a call)
for every failure -- so the gate, the at-most-once execution, the pins and
the audit rows are the framework's, not re-implemented per bridge.

A native manifest's bridge is looked up by the manifest's name in
`NATIVE_BRIDGES`, a code-defined map: a native manifest without a bridge
cannot be called (`not_configured`), never falls back to anything else.
"""

from importlib import import_module

from connectors import mcp_client
from connectors.manifest import KIND_NATIVE

NATIVE_BRIDGES = {
    "home_assistant": "connectors.bridges.home_assistant",
}


def bridge_for(manifest):
    path = NATIVE_BRIDGES.get(manifest.name)
    if path is None:
        raise mcp_client.ConnectorError("not_configured")
    return import_module(path)


def list_tools(manifest, *, timeout):
    if manifest.kind == KIND_NATIVE:
        return bridge_for(manifest).list_tools(manifest, timeout=timeout)
    return mcp_client.list_tools(manifest, timeout=timeout)


def call_tool(manifest, *, server_tool, arguments, schema_sha256, timeout):
    if manifest.kind == KIND_NATIVE:
        return bridge_for(manifest).call_tool(
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
