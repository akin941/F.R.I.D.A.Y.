"""The HTTP relay sidecar mode (Phase 12 D9): Friday's real MCP client ->
the in-process relay (`connectors.docker.http_relay.build_app`) -> the
SDK's in-memory server shaped like n8n's MCP Server Trigger.

Only allowlisted tools are listed or called; an unlisted call never reaches
upstream; the bearer goes only upstream (never to Friday, never in a log
line); redirects are refused; no client capabilities are offered upstream;
resources and prompts are refused; the environment is validated."""

import logging

import anyio
import pytest

from connectors import mcp_client
from connectors.docker.http_relay import RelayConfigError, build_relay, relay_config
from connectors.manifest import get_manifest, schema_hash
from connectors.manifests.n8n import REFERENCE_SCHEMAS
from connectors.mcp_client import ConnectorError
from connectors.tests import fakes

N8N = get_manifest("n8n")


def _call(server_tool, arguments, *, schema=None):
    return mcp_client.call_tool(
        N8N,
        server_tool=server_tool,
        arguments=arguments,
        schema_sha256=schema_hash(schema or REFERENCE_SCHEMAS.get(server_tool, {})),
        timeout=5,
    )


def test_only_allowlisted_tools_cross_the_relay(n8n):
    listing = mcp_client.list_tools(N8N, timeout=5)
    assert set(listing.tool_hashes) == {fakes.N8N_STATUS_TOOL, fakes.N8N_BACKUP_TOOL}
    for name in (fakes.N8N_STATUS_TOOL, fakes.N8N_BACKUP_TOOL):
        assert listing.tool_hashes[name] == schema_hash(REFERENCE_SCHEMAS[name])


def test_an_allowlisted_call_goes_through_unchanged(n8n):
    result = _call(fakes.N8N_STATUS_TOOL, {"scope": "summary"})
    assert result.texts == (n8n.status_text,)
    assert n8n.calls == [(fakes.N8N_STATUS_TOOL, {"scope": "summary"})]


@pytest.mark.parametrize("name", [fakes.N8N_UNLISTED_TOOL, "search_workflows"])
def test_an_unlisted_tool_is_unreachable_even_by_name(n8n, name):
    with pytest.raises(ConnectorError) as info:
        _call(name, {"path": "/"}, schema=n8n.schemas[name])
    assert info.value.code == "tool_missing"
    assert n8n.calls == []


def test_the_relay_refuses_an_unlisted_call_without_contacting_upstream(n8n):
    """Even a client that skips Friday's own listing check gets a refusal
    from the relay, and nothing is sent upstream."""
    from mcp.client.client import Client
    from mcp.client.streamable_http import streamable_http_client

    async def raw_call():
        async with mcp_client._connect_http(fakes.N8N_RELAY_URL, timeout=5) as http:
            async with Client(
                streamable_http_client(fakes.N8N_RELAY_URL, http_client=http),
                mode="auto",
                cache=None,
                input_required_max_rounds=0,
            ) as client:
                return await client.call_tool(fakes.N8N_UNLISTED_TOOL, {"path": "/"})

    result = anyio.run(raw_call)
    assert result.is_error
    assert n8n.calls == []
    assert not any("tools/call" in r["body"] for r in n8n.upstream_requests)


def test_the_bearer_goes_only_upstream(n8n, caplog):
    caplog.set_level(logging.DEBUG)
    _call(fakes.N8N_STATUS_TOOL, {"scope": "full"})
    assert n8n.upstream_requests
    for request in n8n.upstream_requests:
        assert request["headers"]["authorization"] == f"Bearer {fakes.N8N_BEARER}"
    for request in n8n.relay_requests:
        assert "authorization" not in {k.lower() for k in request["headers"]}
        assert fakes.N8N_BEARER not in request["response"]
        assert fakes.N8N_BEARER not in str(request["response_headers"])
    assert fakes.N8N_BEARER not in caplog.text


def test_a_wrong_bearer_fails_closed_without_leaking_it(monkeypatch, n8n_state, caplog):
    caplog.set_level(logging.DEBUG)
    n8n_state.expected_bearer = "something-else"
    fakes.install_n8n(monkeypatch, n8n_state)
    with pytest.raises(ConnectorError):
        mcp_client.list_tools(N8N, timeout=5)
    assert fakes.N8N_BEARER not in caplog.text
    for request in n8n_state.relay_requests:
        assert fakes.N8N_BEARER not in request["response"]


def test_redirects_are_refused(n8n):
    n8n.redirect = True
    with pytest.raises(ConnectorError):
        _call(fakes.N8N_STATUS_TOOL, {"scope": "summary"})
    assert n8n.redirect_target_hits == 0
    assert n8n.calls == []


def test_no_client_capabilities_are_offered_upstream(n8n):
    _call(fakes.N8N_STATUS_TOOL, {"scope": "summary"})
    bodies = " ".join(r["body"] for r in n8n.upstream_requests)
    for capability in ("sampling", "elicitation", "roots"):
        assert f'"{capability}"' not in bodies


@pytest.mark.parametrize("method", ["list_resources", "list_prompts"])
def test_resources_and_prompts_are_refused(n8n, method):
    from mcp.client.client import Client
    from mcp.client.streamable_http import streamable_http_client

    async def ask():
        async with mcp_client._connect_http(fakes.N8N_RELAY_URL, timeout=5) as http:
            async with Client(
                streamable_http_client(fakes.N8N_RELAY_URL, http_client=http),
                mode="auto",
                cache=None,
                input_required_max_rounds=0,
            ) as client:
                return await getattr(client, method)()

    with pytest.raises(BaseException):  # noqa: B017,PT011 -- any refusal will do
        anyio.run(ask)
    assert not any(
        "resources/" in r["body"] or "prompts/" in r["body"] for r in n8n.upstream_requests
    )


@pytest.mark.parametrize(
    "change",
    [
        {"RELAY_UPSTREAM_URL": ""},
        {"RELAY_UPSTREAM_URL": "ftp://n8n.test/mcp"},
        {"RELAY_UPSTREAM_URL": "http://user:pw@n8n.test/mcp"},
        {"RELAY_BEARER_TOKEN": ""},
        {"RELAY_BEARER_TOKEN": "two words"},
        {"BRIDGE_ALLOWED_TOOLS": ""},
        {"RELAY_TIMEOUT_SECONDS": "soon"},
    ],
)
def test_the_relay_environment_is_validated(change):
    environ = {**fakes.relay_environ(), **change}
    with pytest.raises(RelayConfigError) as info:
        relay_config(environ)
    assert fakes.N8N_BEARER not in str(info.value)


def test_the_relay_refuses_an_empty_allowlist():
    with pytest.raises(ValueError):
        build_relay(lambda: None, allowed_tools=[])


def test_the_relay_configuration_reads_the_allowlist():
    url, token, allowed, timeout = relay_config(fakes.relay_environ())
    assert url == fakes.N8N_UPSTREAM_URL
    assert token == fakes.N8N_BEARER
    assert allowed == {fakes.N8N_STATUS_TOOL, fakes.N8N_BACKUP_TOOL}
    assert timeout == 10
