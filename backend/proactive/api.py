"""The public surface of `proactive` (D19).

`assistant` and `hud` may import this module and nothing else of
`proactive` (import-linter). Every read is `for_actor()`-scoped to one
workspace; notice titles are Friday templates over D1 labels only, and a
digest carries its facts (D1 labels) and its number-checked spoken text.

The one write here is `mark_digest_spoken`: the digest's own `spoken_at` /
`spoken_channel` telemetry (the D20 checkpoint data), set once, for the
`digest.today` wake route (branch 3) and the web ack. It refuses inside an
unattended context and touches no other row.
"""

from django.db.models import Q
from django.utils import timezone

from core.unattended import deny_if_unattended
from proactive.models import DigestRun, ProactiveNotice, ProactiveSchedule

NOTICE_SECTION_LIMIT = 5
SPOKEN_CHANNELS = frozenset(DigestRun.SpokenChannel.values) - {""}


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


def _iso(value):
    return value.isoformat() if value else None


def digest_payload(row, *, speak_on_open=False):
    """One digest as the web, the HUD and the wake route see it."""
    return {
        "id": str(row.id),
        "workspace": str(row.workspace_id),
        "created_at": _iso(row.created_at),
        "spoken_text": row.spoken_text,
        "sections": row.sections,
        "composed_by": row.composed_by,
        "data_class": row.data_class,
        "opened_at": _iso(row.opened_at),
        "spoken_at": _iso(row.spoken_at),
        "spoken_channel": row.spoken_channel,
        "speak_on_open": bool(speak_on_open),
    }


def _speak_on_open(workspace):
    return (
        ProactiveSchedule.objects.filter(
            workspace=workspace, kind=ProactiveSchedule.Kind.MORNING_DIGEST, enabled=True
        )
        .values_list("speak_on_open", flat=True)
        .first()
        or False
    )


def digests_for(actor, workspace):
    return DigestRun.objects.for_actor(actor, workspace=workspace)


def latest_digest_row(*, actor, workspace):
    return digests_for(actor, workspace).order_by("-created_at", "-id").first()


def latest_digest(*, actor, workspace):
    """The workspace's newest digest as a payload, or None."""
    row = latest_digest_row(actor=actor, workspace=workspace)
    if row is None:
        return None
    return digest_payload(row, speak_on_open=_speak_on_open(workspace))


def digest_section(*, actor, workspace):
    """The HUD digest card: the newest digest's id, text, time and state --
    the facts stay on `/today`."""
    row = latest_digest_row(actor=actor, workspace=workspace)
    if row is None:
        return None
    return {
        "id": str(row.id),
        "created_at": _iso(row.created_at),
        "spoken_text": row.spoken_text,
        "opened": row.opened_at is not None,
        "spoken": row.spoken_at is not None,
        "speak_on_open": _speak_on_open(workspace),
    }


def mark_digest_spoken(*, actor, workspace, digest_id, channel, now=None):
    """Set `spoken_at` / `spoken_channel` once (idempotent). Returns True
    when this call set them, False when they were set already, None when
    the digest is not in this workspace for this actor."""
    deny_if_unattended("digest_write")
    if channel not in SPOKEN_CHANNELS:
        raise ValueError("unknown spoken channel")
    row = digests_for(actor, workspace).filter(pk=digest_id).first()
    if row is None:
        return None
    updated = DigestRun.objects.filter(
        Q(spoken_at__isnull=True), pk=row.pk, workspace=workspace
    ).update(spoken_at=now or timezone.now(), spoken_channel=channel)
    return bool(updated)
