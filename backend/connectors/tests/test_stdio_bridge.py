"""The stdio<->HTTP bridge (brief D1): only allowlisted tools are listed or
callable, only tools/list and tools/call are forwarded, and the child's
environment is the passthrough list only. The "child" here is the fake
server in-process; in the sidecar it is a real stdio subprocess."""

import json
from contextlib import asynccontextmanager

import pytest

from connectors import mcp_client
from connectors.docker.stdio_bridge import build_bridge, child_parameters
from connectors.manifest import schema_hash
from connectors.mcp_client import ConnectorError
from connectors.tests import fakes


@pytest.fixture
def bridged(monkeypatch, fake_manifest, fake_state):
    """Jarvis's real client -> bridge (Streamable HTTP) -> child session ->
    fake server."""
    from mcp.client.client import Client

    @asynccontextmanager
    async def child():
        async with Client(fakes.build_server(fake_state), mode="legacy") as client:
            yield client.session

    def app_factory(state):
        bridge = build_bridge(child, allowed_tools=["fake_search", "fake_create_note"])
        return bridge.streamable_http_app(stateless_http=True, json_response=True)

    monkeypatch.setenv(fakes.URL_ENV, fakes.SERVER_URL)
    monkeypatch.setattr(
        mcp_client, "_connect_http", fakes.asgi_connector(fake_state, app_factory=app_factory)
    )
    return fake_state


def test_only_allowlisted_tools_cross_the_bridge(fake_manifest, bridged):
    listing = mcp_client.list_tools(fake_manifest, timeout=5)
    assert set(listing.tool_hashes) == {"fake_search", "fake_create_note"}
    assert listing.tool_hashes["fake_search"] == schema_hash(fakes.SEARCH_SCHEMA)


def test_a_call_goes_through_unchanged(fake_manifest, bridged):
    result = mcp_client.call_tool(
        fake_manifest,
        server_tool="fake_search",
        arguments={"query": "abc"},
        schema_sha256=schema_hash(fakes.SEARCH_SCHEMA),
        timeout=5,
    )
    assert result.texts == ("3 results for you",)
    assert bridged.calls == [("fake_search", {"query": "abc"})]


def test_an_unlisted_tool_is_unreachable_even_by_name(fake_manifest, bridged):
    with pytest.raises(ConnectorError) as info:
        mcp_client.call_tool(
            fake_manifest,
            server_tool="fake_merge",
            arguments={"pr": 1},
            schema_sha256=schema_hash(fakes.MERGE_SCHEMA),
            timeout=5,
        )
    assert info.value.code == "tool_missing"
    assert bridged.calls == []


def test_bridge_refuses_an_empty_allowlist():
    with pytest.raises(ValueError):
        build_bridge(lambda: None, allowed_tools=[])


def test_child_environment_is_the_passthrough_only():
    params = child_parameters(
        {
            "BRIDGE_COMMAND": json.dumps(["/server/bin", "stdio", "--tools=a"]),
            "BRIDGE_ENV_PASSTHROUGH": "GITHUB_PERSONAL_ACCESS_TOKEN",
            "GITHUB_PERSONAL_ACCESS_TOKEN": "tok",
            "PATH": "/usr/bin",
            "DJANGO_SECRET_KEY": "never",
            "OTHER": "never",
        }
    )
    assert params.command == "/server/bin"
    assert params.args == ["stdio", "--tools=a"]
    assert params.env == {"PATH": "/usr/bin", "GITHUB_PERSONAL_ACCESS_TOKEN": "tok"}


@pytest.mark.parametrize("command", ["[]", '"sh -c x"', "[1]"])
def test_bridge_command_must_be_an_argv_list(command):
    with pytest.raises(ValueError):
        child_parameters({"BRIDGE_COMMAND": command})
