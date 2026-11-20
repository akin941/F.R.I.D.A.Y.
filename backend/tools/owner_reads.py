"""Shared rules of the Phase 8A owner read tools (brief D12):
`coding_sessions_status` (`tools.coding_sessions`) and `harness_jobs_status`
(`harness.tools`, which may import `tools`).

Both are ungated `ReadToolSpec`s whose results list firm project names, so:
web channel only, a human installation owner who is a member of the
conversation's workspace; a bounded plain-text result that the chat turn
fences as untrusted data (`assistant.context.render_connector_block`); and
one `read_tool.read` audit row per call (every outcome) with ids and counts
only -- never the listed text.
"""

import json

from audit.services import record_audit
from core.channels import CHANNEL_WEB
from tools.registry import ReadToolError

MAX_RESULT_CHARS = 4000
AUDIT_ACTION = "read_tool.read"


def require_owner_web_reader(*, workspace, actor, channel):
    """Raises `ReadToolError` (closed vocabulary) unless the call runs over
    the web channel for a human installation owner who belongs to
    `workspace`."""
    if channel != CHANNEL_WEB:
        raise ReadToolError("channel_forbidden")
    if (
        actor is None
        or not getattr(actor, "is_authenticated", False)
        or not getattr(actor, "is_active", False)
        or getattr(actor, "is_agent", False)
        or not getattr(actor, "is_owner", False)
    ):
        raise ReadToolError("forbidden")
    if workspace is None or not workspace.members.filter(user=actor).exists():
        raise ReadToolError("forbidden")


def validate_args(serializer_class, args):
    """Strict argument validation: a dict, no unknown keys, the serializer's
    own rules. Returns the validated data or raises
    `ReadToolError("invalid_arguments")`."""
    if not isinstance(args, dict):
        raise ReadToolError("invalid_arguments")
    serializer = serializer_class(data=args)
    if set(args) - set(serializer.fields) or not serializer.is_valid():
        raise ReadToolError("invalid_arguments")
    return serializer.validated_data


def bounded_json_lines(rows, limit=MAX_RESULT_CHARS):
    """One JSON object per line, cut at a whole line so the text never ends
    mid-object. Returns `(text, size, truncated)`."""
    lines = [json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows]
    full = "\n".join(lines)
    if len(full) <= limit:
        return full, len(full), False
    kept, used = [], 0
    for line in lines:
        if used + len(line) + 1 > limit:
            break
        kept.append(line)
        used += len(line) + 1
    return "\n".join(kept), len(full), True


def record_read(*, tool, actor, workspace, channel, metadata):
    """The one audit row per read (ids and counts only)."""
    record_audit(
        action=AUDIT_ACTION,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        workspace=workspace,
        metadata={"tool": tool, "channel": channel, **metadata},
    )
