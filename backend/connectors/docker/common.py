"""Shared sidecar-side helpers of the connector bridges (ADR-010 decision 3):
the stdio<->HTTP bridge (`stdio_bridge`) and the HTTP relay
(`http_relay`, Phase 12 D9). Standard library only -- these run inside the
sidecar image, never in web, worker or the harness runner."""


def csv_list(value):
    """`"a, b,,c"` -> `["a", "b", "c"]` (env var lists)."""
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def allowed_tool_set(names):
    """The sidecar-side tool ceiling (`BRIDGE_ALLOWED_TOOLS`): required and
    non-empty -- a bridge with no allowlist refuses to start."""
    allowed = frozenset(names or ())
    if not allowed:
        raise ValueError("BRIDGE_ALLOWED_TOOLS must name at least one tool.")
    return allowed
