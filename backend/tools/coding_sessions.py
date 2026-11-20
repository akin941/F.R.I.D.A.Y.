"""`coding_sessions_status` -- the read tool behind "Claude Code oturumlarım
ne yapıyor?" (Phase 8A brief D12, branch 6).

An ungated `ReadToolSpec` over the `memory.CodingSession` read model (the
hook relay's upserts, branch 3). `tools` may import `memory`; `memory`
never imports `tools`. The read (shared rules in `tools.owner_reads`):

- runs over the trusted web channel only (the result lists firm project
  names), for a human installation owner who is a member of the
  conversation's workspace -- anything else is a `ReadToolError`, never a
  partial result;
- lists only the conversation's own workspace (`for_actor(actor,
  workspace=...)`): another workspace's sessions are never listed, so firm
  sessions never reach a personal conversation and vice versa (ADR-001);
- hides the rows of a forgotten source, like the sessions API;
- returns plain text the chat turn fences as untrusted connector data
  (`assistant.context.render_connector_block`): the last notification is
  tool/model text, never instructions;
- writes one `read_tool.read` audit row per call (every outcome), with ids
  and counts only -- never the session text.

No model call happens here: the normal `chat` completion answers from the
fenced result (no `sessions.summarize` purpose in 8A).
"""

from django.utils import timezone
from rest_framework import serializers

from core.channels import CHANNEL_WEB
from memory.ingestion.hooks import effective_status
from memory.models import CodingSession
from tools.owner_reads import (
    bounded_json_lines,
    record_read,
    require_owner_web_reader,
    validate_args,
)
from tools.registry import (
    DATA_CLASS_FIRM,
    ReadResult,
    ReadToolError,
    ReadToolSpec,
    register_read_tool,
)

TOOL_NAME = "coding_sessions_status"
SOURCE_LABEL = "friday_sessions"
DEFAULT_LIMIT = 10
MAX_LIMIT = 20


class CodingSessionsArgs(serializers.Serializer):
    """Strict arguments: an optional page size, nothing else."""

    limit = serializers.IntegerField(
        required=False, min_value=1, max_value=MAX_LIMIT, default=DEFAULT_LIMIT
    )


def _session_row(session, now):
    return {
        "tool": session.tool,
        "project": session.project,
        "status": effective_status(session, now),
        "started_at": session.started_at.isoformat(),
        "last_event_at": session.last_event_at.isoformat(),
        "last_event": session.last_event,
        # Scrubbed tool/model text (<= 200 chars): fenced as untrusted data
        # by the chat turn, never followed as an instruction.
        "last_notification": session.last_notification,
        "event_count": session.event_count,
    }


def visible_sessions(*, workspace, actor):
    return (
        CodingSession.objects.for_actor(actor, workspace=workspace)
        .filter(source__forgotten_at__isnull=True)
        .order_by("-last_event_at", "-id")
    )


def read_coding_sessions(*, workspace, actor, args, channel):
    audit = {"outcome": "ok", "count": 0, "size": 0}
    try:
        require_owner_web_reader(workspace=workspace, actor=actor, channel=channel)
        limit = validate_args(CodingSessionsArgs, args)["limit"]
        now = timezone.now()
        sessions = list(visible_sessions(workspace=workspace, actor=actor)[:limit])
        rows = [_session_row(s, now) for s in sessions]
        text, size, truncated = bounded_json_lines(rows)
        if not rows:
            text = "No coding sessions recorded in this workspace."
        audit.update(
            {
                "count": len(rows),
                "size": size,
                "truncated": truncated,
                "session_ids": [str(s.id) for s in sessions],
            }
        )
        return ReadResult(text=text, size=size, truncated=truncated)
    except ReadToolError as exc:
        audit["outcome"] = exc.code
        raise
    finally:
        record_read(
            tool=TOOL_NAME, actor=actor, workspace=workspace, channel=channel, metadata=audit
        )


def coding_sessions_available(workspace):
    """Offered to the router only where there is something to read: the
    workspace holds a session of a source that is not forgotten."""
    workspace_id = getattr(workspace, "id", None)
    if workspace_id is None:
        return False
    return CodingSession.objects.filter(
        workspace_id=workspace_id, source__forgotten_at__isnull=True
    ).exists()


SPEC = ReadToolSpec(
    name=TOOL_NAME,
    description=(
        "the owner's Claude Code coding sessions in this workspace: project, "
        "status (running, waiting for the owner, stopped, stale) and last activity"
    ),
    argument_serializer=CodingSessionsArgs,
    data_class=DATA_CLASS_FIRM,
    channels=frozenset({CHANNEL_WEB}),
    execute=read_coding_sessions,
    is_available=coding_sessions_available,
    source=SOURCE_LABEL,
)


def _register():
    register_read_tool(SPEC)
