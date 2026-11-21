"""The transcript-source part of the D11 audit view (Phase 8B brief D13,
branch 9): per cursor, the `since_clamped` count, the last skipped window
and the `tie_overflow` count.

The pull job (`tools.transcript_ingest`) writes the cursor and one
`memory.transcript_pull.invoked` audit row per invoke; a clamped invoke's
row carries `since_clamped: true` and `skipped_window: {from, to}` (two
instants, never text). This module only reads them, so the audit view can
name the window the owner lost by design without a second source of truth.
The constants live here (memory owns them) and the pull job imports them.
"""

from audit.models import AuditLog
from memory.models import IngestionSource, TranscriptCursor

PULL_AUDIT_ACTION = "memory.transcript_pull.invoked"
CAPABILITY_BY_SOURCE_KIND = {
    IngestionSource.Kind.CLAUDE_CODE_TRANSCRIPTS: "claude.transcripts.owner_turns",
    IngestionSource.Kind.CODEX_TRANSCRIPTS: "codex.transcripts.owner_turns",
}


def _window(metadata):
    window = metadata.get("skipped_window") if isinstance(metadata, dict) else None
    if not isinstance(window, dict):
        return None
    start, end = window.get("from"), window.get("to")
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    return {"from": start[:40], "to": end[:40]}


def last_skipped_window(cursor, capability):
    """The `skipped_window` of the newest clamped invoke for this cursor
    (same target workspace, node and capability), or None."""
    row = (
        AuditLog.objects.filter(
            action=PULL_AUDIT_ACTION,
            workspace_id=cursor.target_workspace_id,
            metadata__since_clamped=True,
            metadata__capability=capability,
            metadata__node=cursor.node_ref,
        )
        .order_by("-created_at")
        .values_list("metadata", flat=True)
        .first()
    )
    return _window(row) if row is not None else None


def transcript_cursor_views(source):
    """One entry per cursor of a transcript source (empty for any other
    kind): counts, instants and the target workspace's id and name only."""
    capability = CAPABILITY_BY_SOURCE_KIND.get(source.kind)
    if capability is None:
        return []
    cursors = TranscriptCursor.objects.filter(source=source).select_related("target_workspace")
    return [
        {
            "target_workspace": str(cursor.target_workspace_id),
            "target_workspace_name": cursor.target_workspace.name,
            "node_ref": cursor.node_ref,
            "since": cursor.since.isoformat(),
            "since_clamped": cursor.since_clamped,
            "last_clamped_at": (
                cursor.last_clamped_at.isoformat() if cursor.last_clamped_at else None
            ),
            "last_skipped_window": (
                last_skipped_window(cursor, capability) if cursor.since_clamped else None
            ),
            "tie_overflow": cursor.tie_overflow,
        }
        for cursor in cursors
    ]
