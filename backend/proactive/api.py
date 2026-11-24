"""The public, read-only surface of `proactive` (D19).

`assistant` and `hud` may import this module and nothing else of
`proactive` (import-linter). Branch 1 ships the notice reads; the digest
reads (`latest_digest`, `mark_digest_spoken`) arrive with `DigestRun` in
branch 2. Every read is `for_actor()`-scoped to one workspace; nothing
here writes, and notice titles are Friday templates over D1 labels only.
"""

from proactive.models import ProactiveNotice

NOTICE_SECTION_LIMIT = 5


def _unread(actor, workspace):
    return ProactiveNotice.objects.for_actor(actor, workspace=workspace).filter(
        read_at__isnull=True, dismissed_at__isnull=True
    )


def unread_notices(*, actor, workspace, limit=NOTICE_SECTION_LIMIT):
    """The latest unread notices of one workspace (title, link, kind)."""
    rows = _unread(actor, workspace).order_by("-created_at")[: max(0, int(limit))]
    return [
        {
            "id": str(row.id),
            "kind": row.kind,
            "title": row.title,
            "count": row.count,
            "link": row.link,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


def notice_section(*, actor, workspace):
    """The HUD `notices` section: unread count and the five latest titles
    and links -- no deep content."""
    return {
        "unread": _unread(actor, workspace).count(),
        "latest": [
            {"title": item["title"], "link": item["link"]}
            for item in unread_notices(actor=actor, workspace=workspace)
        ],
    }
