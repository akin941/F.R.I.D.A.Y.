"""Connector reads: ungated, audited, bounded (ADR-010 decision 4, brief D4).

Each read tool of an active manifest is registered as a
`tools.registry.ReadToolSpec` at startup, so the chat router (`assistant`)
discovers it through `list_read_tools()` without importing this app.
`perform_read` is the spec's `execute`:

1. refuse unless the channel is one of the manifest's read channels, the
   workspace has an enabled binding and the tool's last pin check said `ok`;
2. validate the arguments with the tool's strict serializer, then its scope
   check -- a refusal never touches the network;
3. call the tool through `mcp_client.call_tool` (pin re-checked in the same
   session, `CONNECTOR_READ_TIMEOUT_SECONDS`);
4. keep only text and `structuredContent` (images, audio, resources and
   links are dropped by the client), serialize them into one text and bound
   it to `CONNECTOR_RESULT_MAX_CHARS`.

Every attempt -- refused, failed or ok -- writes one `connector.read` audit
row: binding, connector, tool, sha256 of the canonical arguments, size,
truncation, duration, outcome and channel. Never the arguments or the
content themselves. Fencing the text for a prompt is the caller's job
(`assistant.context.render_connector_block`); connector text never becomes a
memory and never reaches the router.
"""

import hashlib
import json
import time

from django.conf import settings

from audit.services import record_audit
from connectors import bridges, mcp_client
from connectors.manifest import ScopeError
from connectors.services import enabled_binding, record_listing, tool_available
from tools.registry import (
    DATA_CLASS_PERSONAL,
    READ_CHANNELS,
    ReadResult,
    ReadToolError,
    ReadToolSpec,
    register_read_tool,
    unregister_read_tool,
)


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def arguments_sha256(arguments):
    return hashlib.sha256(canonical_json(arguments).encode("utf-8")).hexdigest()


def json_safe(value):
    """Validated serializer data -> plain JSON types (dates become ISO
    strings, decimals strings), exactly what goes over the wire."""
    return json.loads(json.dumps(value, default=str))


def validate_arguments(manifest, policy, workspace, arguments):
    """Serializer, then scope check. Returns the normalized arguments or
    raises `ReadToolError("invalid_arguments" | "out_of_scope")`. Error
    details name fields only -- the rejected values are the caller's data."""
    if not isinstance(arguments, dict):
        raise ReadToolError("invalid_arguments")
    serializer = policy.argument_serializer(data=arguments)
    if not serializer.is_valid():
        raise ReadToolError("invalid_arguments")
    clean = json_safe(serializer.validated_data)
    if policy.scope_check is not None:
        try:
            policy.scope_check(workspace, clean)
        except ScopeError as exc:
            raise ReadToolError("out_of_scope") from exc
    return clean


def compose_result_text(texts, structured):
    """Text blocks, then `structuredContent` as canonical JSON, one text.
    Returns `(bounded_text, size, truncated)`."""
    parts = [text for text in texts if text]
    if structured is not None:
        parts.append(json.dumps(structured, ensure_ascii=False, sort_keys=True, default=str))
    text = "\n\n".join(parts)
    size = len(text)
    limit = settings.CONNECTOR_RESULT_MAX_CHARS
    if size > limit:
        return text[:limit], size, True
    return text, size, False


def parse_json_result(result):
    """A server result as data: its `structuredContent`, else the JSON of
    its first text block, else `None` -- for Friday's own programmatic uses
    (a PR's number, a head SHA, CI runs), never for a prompt."""
    if result.structured is not None:
        return result.structured
    for text in result.texts:
        try:
            return json.loads(text)
        except (TypeError, ValueError):
            continue
    return None


def perform_read(manifest, policy, *, workspace, actor, args, channel, as_data=False):
    """One audited read. `as_data=True` (Friday-internal callers: the
    harness PR panel, the deploy snapshot) returns the parsed JSON in
    `ReadResult.structured` instead of bounded text; the same refusals,
    pin check and audit row apply."""
    started = time.monotonic()
    binding = enabled_binding(workspace, manifest.name)
    audit = {
        "connector": manifest.name,
        "tool": policy.name,
        "binding": str(binding.id) if binding else "",
        "channel": channel,
        "args_sha256": arguments_sha256(args if isinstance(args, dict) else {}),
        "size": 0,
        "truncated": False,
        "outcome": "ok",
    }
    try:
        if channel not in manifest.read_channels:
            raise ReadToolError("channel_forbidden")
        if not tool_available(binding, policy):
            raise ReadToolError("not_available")
        clean = validate_arguments(manifest, policy, workspace, args)
        audit["args_sha256"] = arguments_sha256(clean)
        data_class = policy.classify(workspace, clean) if policy.classify else ""
        if data_class:
            audit["data_class"] = data_class
        # A call classified personal is held to the personal channel set
        # (web, and node voice since Phase 11), whatever the connector's own
        # channels (D5).
        if data_class == DATA_CLASS_PERSONAL and channel not in READ_CHANNELS[data_class]:
            raise ReadToolError("channel_forbidden")
        try:
            result = bridges.call_tool(
                manifest,
                server_tool=policy.server_tool,
                arguments=clean,
                schema_sha256=policy.schema_sha256,
                timeout=settings.CONNECTOR_READ_TIMEOUT_SECONDS,
            )
        except mcp_client.ConnectorError as exc:
            if exc.listing is not None:
                record_listing(binding, manifest, exc.listing, only_tool=policy.name)
            raise ReadToolError(exc.code) from None
        if result.is_error:
            raise ReadToolError("tool_error")
        if as_data:
            data = parse_json_result(result)
            audit["size"] = len(canonical_json(data)) if data is not None else 0
            return ReadResult(text="", structured=data, data_class=data_class)
        text, size, truncated = compose_result_text(result.texts, result.structured)
        audit.update({"size": size, "truncated": truncated})
        return ReadResult(text=text, size=size, truncated=truncated, data_class=data_class)
    except ReadToolError as exc:
        audit["outcome"] = exc.code
        raise
    finally:
        audit["duration_ms"] = int((time.monotonic() - started) * 1000)
        record_audit(
            action="connector.read",
            actor=actor,
            workspace=workspace,
            resource=binding,
            metadata=audit,
        )


def _executor(manifest, policy):
    def execute(*, workspace, actor, args, channel):
        return perform_read(
            manifest, policy, workspace=workspace, actor=actor, args=args, channel=channel
        )

    return execute


def _availability(manifest, policy):
    def is_available(workspace):
        return tool_available(enabled_binding(workspace, manifest.name), policy)

    return is_available


def register_reads(manifest):
    if manifest.blocked:
        return
    for policy in manifest.reads:
        register_read_tool(
            ReadToolSpec(
                name=policy.name,
                description=policy.description,
                argument_serializer=policy.argument_serializer,
                data_class=manifest.data_class,
                channels=frozenset(manifest.read_channels),
                execute=_executor(manifest, policy),
                is_available=_availability(manifest, policy),
                source=manifest.name,
            )
        )


def unregister_reads(manifest):
    """Test-only."""
    for policy in manifest.reads:
        unregister_read_tool(policy.name)
