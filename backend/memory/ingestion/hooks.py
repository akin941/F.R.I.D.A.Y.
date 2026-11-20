"""Claude Code hook-event ingest (Phase 8A brief D6, D12).

`friday-hook` (`integrations/cli/friday_hook.py`) posts batches of Claude
Code hook events to `POST /api/v1/memory/ingest/hook-events` with a `hook`
PAT. This module is everything behind that endpoint except authentication:

- a **strict per-event schema**: only the allow-listed event names
  (`SessionStart`, `Notification`, `Stop`, `SessionEnd`) are kept, unknown
  fields are dropped, every kept field is type- and length-checked;
- each kept event goes through the one door, `record_episode` (D1): the
  source's enabled state, `cwd` path-prefix mapping (D4: drop, never
  default), dedupe, scrubbing and counters all happen there;
- a stored event upserts the `CodingSession` read model (D12) in the
  workspace the episode landed in, from the episode's **scrubbed** fields.

The token's bound workspace only authenticates the principal and names the
owner's source; the mapping rules alone decide where each event lands, and
only into workspaces the owner owns (resolved by `record_episode`).

Hook events carry no owner-authored text in 8A (D5): `owner_text` stays
empty; Claude's notification message is tool/model text and goes to
`context_text` (<= 200 chars after scrubbing).
"""

import hashlib
import posixpath
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from memory.ingestion.mapping import InvalidMappingKey, normalise_path
from memory.ingestion.ports import STORED, record_episode
from memory.ingestion.scrubber import scrub_text
from memory.ingestion.services import ttl_days_for
from memory.models import CodingSession, Episode, IngestionSource
from workspaces.models import Workspace

MAX_EVENTS_PER_BATCH = 50
MAX_BODY_BYTES = 64 * 1024

# Claude Code hook event name -> episode kind.
EVENT_KINDS = {
    "SessionStart": "hook.session_start",
    "Notification": "hook.notification",
    "Stop": "hook.stop",
    "SessionEnd": "hook.session_end",
}
# Notification types that mean "the session is blocked on the owner"
# (a permission prompt, the idle prompt, an MCP elicitation dialog). A
# notification without a type is treated the same way: in Claude Code a
# notification is an attention request.
WAITING_NOTIFICATION_TYPES = frozenset({"permission_prompt", "idle_prompt", "elicitation_dialog"})

MAX_CWD_CHARS = 1000
# Raw message bound before scrubbing (the scrubber caps its own input too);
# the stored text is cut to CodingSession.MAX_NOTIFICATION_CHARS after.
MAX_RAW_MESSAGE_CHARS = 2000
# A client `occurred_at` in the future is clamped to now; one older than
# MAX_EVENT_AGE is refused (no spool in 8A, so a legitimate event is seconds
# old).
MAX_EVENT_AGE = timedelta(hours=24)

_SESSION_ID = re.compile(r"\A[A-Za-z0-9_-]{1,128}\Z")
_EVENT_ID = re.compile(r"\A[A-Za-z0-9_-]{8,64}\Z")
_SHORT_TOKEN = re.compile(r"\A[a-z0-9_]{1,40}\Z")

IGNORED = "ignored"  # not an allow-listed event name
INVALID = "invalid"  # an allow-listed event that fails the schema

COUNT_KEYS = (
    "received",
    "stored",
    "duplicate",
    "dropped_unmapped",
    "dropped_paused",
    IGNORED,
    INVALID,
)


@dataclass(frozen=True)
class HookEvent:
    name: str
    session_id: str
    cwd: str
    occurred_at: datetime
    event_id: str = ""
    notification_type: str = ""
    message: str = ""
    start_source: str = ""
    end_reason: str = ""

    @property
    def kind(self):
        return EVENT_KINDS[self.name]


class EventRejected(ValueError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _optional_token(raw, key):
    value = raw.get(key)
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or not _SHORT_TOKEN.match(value):
        raise EventRejected(INVALID)
    return value


def _occurred_at(raw, now):
    value = raw.get("occurred_at")
    if value in (None, ""):
        return now
    if not isinstance(value, str) or len(value) > 40:
        raise EventRejected(INVALID)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise EventRejected(INVALID) from exc
    if timezone.is_naive(parsed):
        raise EventRejected(INVALID)
    if parsed > now:
        # Client clock ahead of ours: clamp, never store a future event.
        return now
    if parsed < now - MAX_EVENT_AGE:
        raise EventRejected(INVALID)
    return parsed


def parse_event(raw, *, now=None) -> HookEvent:
    """One raw event -> a `HookEvent`, keeping only the allow-listed fields.
    Raises `EventRejected(IGNORED)` for an event name outside the allow
    list and `EventRejected(INVALID)` for a schema failure."""
    now = now or timezone.now()
    if not isinstance(raw, dict):
        raise EventRejected(INVALID)
    name = raw.get("hook_event_name")
    if not isinstance(name, str):
        raise EventRejected(INVALID)
    if name not in EVENT_KINDS:
        raise EventRejected(IGNORED)
    session_id = raw.get("session_id")
    if not isinstance(session_id, str) or not _SESSION_ID.match(session_id):
        raise EventRejected(INVALID)
    cwd = raw.get("cwd")
    if not isinstance(cwd, str) or not cwd or len(cwd) > MAX_CWD_CHARS:
        raise EventRejected(INVALID)
    try:
        cwd = normalise_path(cwd)
    except InvalidMappingKey as exc:
        raise EventRejected(INVALID) from exc
    event_id = raw.get("event_id") or ""
    if event_id and (not isinstance(event_id, str) or not _EVENT_ID.match(event_id)):
        raise EventRejected(INVALID)
    message = ""
    if name == "Notification":
        message = raw.get("message") or ""
        if not isinstance(message, str):
            raise EventRejected(INVALID)
        message = message[:MAX_RAW_MESSAGE_CHARS]
    return HookEvent(
        name=name,
        session_id=session_id,
        cwd=cwd,
        occurred_at=_occurred_at(raw, now),
        event_id=event_id,
        notification_type=_optional_token(raw, "notification_type")
        if name == "Notification"
        else "",
        message=message,
        start_source=_optional_token(raw, "source") if name == "SessionStart" else "",
        end_reason=_optional_token(raw, "reason") if name == "SessionEnd" else "",
    )


def next_status(current, event: HookEvent):
    """The session status after `event` (D12): start -> running; an
    attention notification -> waiting_for_owner; the end of a turn (`Stop`)
    -> running again (no longer blocked; Claude Code sends an idle
    notification if the owner stays away); `SessionEnd` -> stopped."""
    Status = CodingSession.Status
    if event.name == "SessionStart":
        return Status.RUNNING
    if event.name == "Notification":
        if not event.notification_type or event.notification_type in WAITING_NOTIFICATION_TYPES:
            return Status.WAITING_FOR_OWNER
        return current or Status.RUNNING
    if event.name == "Stop":
        return Status.RUNNING
    if event.name == "SessionEnd":
        return Status.STOPPED
    return current or Status.RUNNING


def _safe_identifier(value):
    """A validated identifier that the scrubber would touch (a high-entropy
    run) is replaced by a short digest, so no raw secret can ride in
    through the session key and two sessions never collide on a
    placeholder."""
    if scrub_text(value).hits:
        return "h-" + hashlib.sha256(value.encode()).hexdigest()[:40]
    return value


def _external_id(event: HookEvent):
    if event.event_id:
        return f"hook:{_safe_identifier(event.session_id)}:{event.event_id}"
    digest = hashlib.sha256(
        "\x00".join(
            [event.session_id, event.name, event.occurred_at.isoformat(), event.message]
        ).encode("utf-8", "replace")
    ).hexdigest()[:40]
    return f"hook:{digest}"


def _metadata(event: HookEvent):
    metadata = {
        "event": event.name,
        "session_id": _safe_identifier(event.session_id),
        "project": posixpath.basename(event.cwd)[: CodingSession.MAX_PROJECT_CHARS],
    }
    if event.notification_type:
        metadata["notification_type"] = event.notification_type
    if event.start_source:
        metadata["start_source"] = event.start_source
    if event.end_reason:
        metadata["end_reason"] = event.end_reason
    return metadata


def hook_source_for(user, bound_workspace_id):
    """The owner's `claude_code_hooks` source in the token's bound
    (personal) workspace, through `for_actor()`. None when the user is not
    the installation owner, is an agent, or has no such source -- then
    every event is dropped (fail closed)."""
    if not getattr(user, "is_owner", False) or getattr(user, "is_agent", False):
        return None
    return (
        IngestionSource.objects.for_actor(user, workspace=bound_workspace_id)
        .filter(kind=IngestionSource.Kind.CLAUDE_CODE_HOOKS, workspace__owner=user)
        .select_related("workspace")
        .first()
    )


@transaction.atomic
def _upsert_session(source, episode: Episode, event: HookEvent):
    metadata = episode.metadata or {}
    session_id = str(metadata.get("session_id") or "")[:128]
    if not session_id:
        return None
    session, created = CodingSession.objects.select_for_update().get_or_create(
        workspace_id=episode.workspace_id,
        tool=CodingSession.Tool.CLAUDE_CODE,
        session_id=session_id,
        defaults={
            "source": source,
            "project": str(metadata.get("project") or "")[: CodingSession.MAX_PROJECT_CHARS],
            "status": next_status(None, event),
            "started_at": event.occurred_at,
            "last_event_at": event.occurred_at,
            "last_event": event.name,
            "last_notification": (
                episode.context_text[: CodingSession.MAX_NOTIFICATION_CHARS]
                if event.name == "Notification"
                else ""
            ),
            "event_count": 1,
        },
    )
    if created:
        return session
    session.event_count += 1
    fields = ["event_count", "updated_at"]
    if event.occurred_at < session.started_at:
        session.started_at = event.occurred_at
        fields.append("started_at")
    # Out-of-order delivery: an older event never overwrites newer state.
    if event.occurred_at >= session.last_event_at:
        session.status = next_status(session.status, event)
        session.last_event_at = event.occurred_at
        session.last_event = event.name
        session.project = str(metadata.get("project") or session.project)[
            : CodingSession.MAX_PROJECT_CHARS
        ]
        fields += ["status", "last_event_at", "last_event", "project"]
        if event.name == "Notification":
            session.last_notification = episode.context_text[: CodingSession.MAX_NOTIFICATION_CHARS]
            fields.append("last_notification")
    session.save(update_fields=fields)
    return session


def ingest_hook_events(*, user, bound_workspace_id, events, now=None):
    """Ingest one validated-size batch. Returns counts only -- never any
    payload, row or id (the endpoint is append-only and reads nothing
    back)."""
    now = now or timezone.now()
    counts = dict.fromkeys(COUNT_KEYS, 0)
    counts["received"] = len(events)
    source = hook_source_for(user, bound_workspace_id)
    for raw in events:
        try:
            event = parse_event(raw, now=now)
        except EventRejected as exc:
            counts[exc.reason] += 1
            continue
        if source is None:
            counts["dropped_paused"] += 1
            continue
        result = record_episode(
            source=source,
            kind=event.kind,
            external_id=_external_id(event),
            occurred_at=event.occurred_at,
            mapping_key=f"path:{event.cwd}",
            owner_text="",
            context_text=event.message,
            metadata=_metadata(event),
            sensitivity=Episode.Sensitivity.NORMAL,
        )
        counts[result.status] += 1
        if result.status == STORED:
            episode = Episode.all_objects.get(pk=result.episode_id)
            # The notification text is cut to the session field's bound
            # after scrubbing (the port already bounded it to 4000).
            if len(episode.context_text) > CodingSession.MAX_NOTIFICATION_CHARS:
                episode.context_text = episode.context_text[: CodingSession.MAX_NOTIFICATION_CHARS]
                episode.save(update_fields=["context_text"])
            _upsert_session(source, episode, event)
    return counts


def stale_after():
    return timedelta(hours=settings.MEMORY_CODING_SESSION_STALE_HOURS)


def effective_status(session, now=None):
    """`stale` for a session that is not stopped and has had no event for
    MEMORY_CODING_SESSION_STALE_HOURS (D12); else its stored status."""
    now = now or timezone.now()
    if session.status != CodingSession.Status.STOPPED and (
        session.last_event_at <= now - stale_after()
    ):
        return "stale"
    return session.status


def purge_idle_coding_sessions(now=None):
    """TTL for the read model (d10): a session idle past its workspace's
    episode TTL is deleted with the episodes it was built from."""
    now = now or timezone.now()
    deleted = 0
    for workspace_type in Workspace.Type.values:
        days = ttl_days_for(Workspace(type=workspace_type))
        count, _ = CodingSession.objects.filter(
            workspace__type=workspace_type, last_event_at__lte=now - timedelta(days=days)
        ).delete()
        deleted += count
    return deleted
