"""The sync MCP facade (brief D1/D2) against the SDK's own server."""

import asyncio
import json

import httpx2
import pytest

from connectors import mcp_client
from connectors.manifest import schema_hash
from connectors.mcp_client import ConnectorError
from connectors.tests import fakes


def test_list_tools_reports_pins_and_identity_but_no_descriptions(fake_manifest, server):
    listing = mcp_client.list_tools(fake_manifest, timeout=5)
    assert listing.tool_hashes == {
        "fake_search": schema_hash(fakes.SEARCH_SCHEMA),
        "fake_create_note": schema_hash(fakes.NOTE_SCHEMA),
        "fake_merge": schema_hash(fakes.MERGE_SCHEMA),
    }
    assert listing.server_name == "fake-mcp"
    assert listing.server_version == "9.9.9"
    assert listing.protocol_version == "2026-07-28"
    assert fakes.SERVER_DESCRIPTION_CANARY not in repr(listing)


def test_call_keeps_text_and_structured_and_drops_images(fake_manifest, server):
    server.search_blocks = [fakes.IMAGE_BLOCK, fakes.TextContent(text="hello")]
    server.search_structured = {"items": [1, 2]}
    result = mcp_client.call_tool(
        fake_manifest,
        server_tool="fake_search",
        arguments={"query": "x"},
        schema_sha256=schema_hash(fakes.SEARCH_SCHEMA),
        timeout=5,
    )
    assert result.texts == ("hello",)
    assert result.structured == {"items": [1, 2]}
    assert result.dropped_blocks == 1
    assert server.calls == [("fake_search", {"query": "x"})]


def test_a_changed_schema_is_never_called(fake_manifest, server):
    server.schemas["fake_search"]["properties"]["query"]["type"] = "array"
    with pytest.raises(ConnectorError) as info:
        mcp_client.call_tool(
            fake_manifest,
            server_tool="fake_search",
            arguments={"query": "x"},
            schema_sha256=schema_hash(fakes.SEARCH_SCHEMA),
            timeout=5,
        )
    assert info.value.code == "pin_mismatch"
    assert info.value.sent is False
    assert info.value.listing is not None
    assert server.calls == []


def test_a_tool_the_server_stopped_offering_is_never_called(fake_manifest, server):
    del server.schemas["fake_search"]
    with pytest.raises(ConnectorError) as info:
        mcp_client.call_tool(
            fake_manifest,
            server_tool="fake_search",
            arguments={"query": "x"},
            schema_sha256=schema_hash(fakes.SEARCH_SCHEMA),
            timeout=5,
        )
    assert info.value.code == "tool_missing"
    assert server.calls == []


def test_missing_or_credentialed_url_is_not_configured(fake_manifest, server, monkeypatch, caplog):
    monkeypatch.delenv(fakes.URL_ENV)
    with pytest.raises(ConnectorError) as info:
        mcp_client.list_tools(fake_manifest, timeout=5)
    assert info.value.code == "not_configured"
    monkeypatch.setenv(fakes.URL_ENV, "http://user:hunter2-secret@127.0.0.1:8765/mcp")
    with pytest.raises(ConnectorError) as info:
        mcp_client.list_tools(fake_manifest, timeout=5)
    assert info.value.code == "not_configured"
    monkeypatch.setenv(fakes.URL_ENV, "file:///etc/passwd")
    with pytest.raises(ConnectorError):
        mcp_client.list_tools(fake_manifest, timeout=5)
    assert "hunter2-secret" not in caplog.text
    assert server.http_requests == []


def test_no_client_capability_is_advertised(fake_manifest, server):
    mcp_client.list_tools(fake_manifest, timeout=5)
    bodies = [json.loads(r["body"]) for r in server.http_requests if r["body"]]
    capability_sets = [
        body.get("params", {}).get("capabilities")
        for body in bodies
        if isinstance(body, dict) and body.get("params", {}).get("capabilities") is not None
    ]
    for caps in capability_sets:
        assert not {"sampling", "elicitation", "roots"} & set(caps)
    # No credential or auth header is ever sent by Friday: the sidecar holds it.
    for request in server.http_requests:
        assert "authorization" not in {k.lower() for k in request["headers"]}


def test_redirects_are_never_followed(fake_manifest, monkeypatch):
    """Even a same-origin 307 -- which the SDK would follow on its own up to
    `max_redirects` -- is an error: the hardened client allows none."""
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx2.Response(307, headers={"Location": "http://127.0.0.1:8765/elsewhere"})

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def connect(url, *, timeout):
        transport = httpx2.MockTransport(handler)
        async with mcp_client.build_http_client(timeout=timeout, transport=transport) as client:
            yield client

    monkeypatch.setenv(fakes.URL_ENV, fakes.SERVER_URL)
    monkeypatch.setattr(mcp_client, "_connect_http", connect)
    with pytest.raises(ConnectorError):
        mcp_client.list_tools(fake_manifest, timeout=5)
    assert requests
    assert all(url == fakes.SERVER_URL for url in requests)


def test_hardened_client_settings():
    client = mcp_client.build_http_client(timeout=3)
    assert client.max_redirects == 0
    assert client.follow_redirects is False


def test_refuses_to_run_inside_a_running_event_loop(fake_manifest, server):
    async def inside():
        mcp_client.list_tools(fake_manifest, timeout=5)

    with pytest.raises(ConnectorError) as info:
        asyncio.run(inside())
    assert info.value.code == "event_loop_active"
    assert server.http_requests == []


def test_timeout_is_a_closed_code(fake_manifest, server):
    import anyio

    async def slow(name):
        await anyio.sleep(5)

    server.on_call = slow
    with pytest.raises(ConnectorError) as info:
        mcp_client.call_tool(
            fake_manifest,
            server_tool="fake_search",
            arguments={"query": "x"},
            schema_sha256=schema_hash(fakes.SEARCH_SCHEMA),
            timeout=0.5,
        )
    assert info.value.code == "timeout"
    assert info.value.sent is True


def test_runs_from_a_celery_task_in_eager_mode(fake_manifest, server):
    """The worker call site (prefork, sync): a pin check task."""
    from celery import shared_task

    @shared_task
    def probe():
        return sorted(mcp_client.list_tools(fake_manifest, timeout=5).tool_hashes)

    # Eager mode runs the task inline; `disable_sync_subtasks=False` keeps
    # Celery's process-global "inside a task" flag, which an earlier eager
    # task in the same session can leave set, from failing this `.get()`.
    result = probe.delay().get(disable_sync_subtasks=False)
    assert result == ["fake_create_note", "fake_merge", "fake_search"]


def test_oversized_response_is_cut_off_while_streaming(fake_manifest, monkeypatch, settings):
    """D4: a response body past CONNECTOR_RESPONSE_MAX_BYTES aborts as it
    streams in -- nothing oversized is buffered or parsed -- and maps to
    the closed `protocol_error`."""
    from contextlib import asynccontextmanager

    settings.CONNECTOR_RESPONSE_MAX_BYTES = 1024
    body = b'{"jsonrpc": "2.0", "id": 1, "result": {"pad": "' + b"x" * 10_000 + b'"}}'

    def handler(request):
        return httpx2.Response(200, headers={"content-type": "application/json"}, content=body)

    @asynccontextmanager
    async def connect(url, *, timeout):
        transport = httpx2.MockTransport(handler)
        async with mcp_client.build_http_client(timeout=timeout, transport=transport) as client:
            yield client

    monkeypatch.setenv(fakes.URL_ENV, fakes.SERVER_URL)
    monkeypatch.setattr(mcp_client, "_connect_http", connect)
    with pytest.raises(ConnectorError) as excinfo:
        mcp_client.list_tools(fake_manifest, timeout=5)
    assert excinfo.value.code == "protocol_error"


def test_capped_stream_passes_bodies_within_the_cap():
    import anyio

    async def collect():
        stream = mcp_client._CappedStream(httpx2.ByteStream(b"a" * 100), 100)
        return b"".join([chunk async for chunk in stream])

    assert anyio.run(collect) == b"a" * 100

    async def over():
        stream = mcp_client._CappedStream(httpx2.ByteStream(b"a" * 101), 100)
        return b"".join([chunk async for chunk in stream])

    with pytest.raises(mcp_client.ResponseTooLarge):
        anyio.run(over)
