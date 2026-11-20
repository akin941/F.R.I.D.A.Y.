"""The calendar metadata producer for the Phase 8A ingestion port (brief D5,
branch 4). `memory` may not import `connectors`, so this beat task pushes
calendar items through `memory.ingestion.ports.record_episode` (connectors
may import memory, ADR-010).

For every enabled `google` binding whose `calendar_agenda` tool is healthy
(the same availability rule as the chat read), it reads **yesterday and
today** with the bridge's metadata read and records one episode per event:

- `owner_text` is the event title **only** when the owner organises the
  event (`organizer.self`); anyone else's title is `context_text`
  (never quotable by extraction);
- metadata is start/end, the all-day flag, the attendee **count** and the
  organiser-is-owner flag -- never a description, location, attendee
  address or conference link (the bridge does not even copy them out);
- the mapping key is `calendar:<binding id>`, so a rule decides the
  workspace (a firm calendar binding lands in the firm workspace, an
  unmapped one is dropped and counted);
- the data is personal-class (`IngestionSource.is_personal_class`): branch
  5's extraction sends it only with `personal_data=True`.

Nothing is read while the calendar source is disabled or paused. A failed
read or write is logged and never raised (one binding's failure never stops
another); every run leaves one `connector.ingest_read` audit row per binding
with counts only.
"""

import logging
from datetime import datetime, timedelta
from datetime import time as dt_time
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from audit.models import AuditLog
from audit.services import record_audit
from connectors.bridges import google
from connectors.manifest import get_manifest
from connectors.mcp_client import ConnectorError
from connectors.models import ConnectorBinding
from connectors.services import tool_available
from memory.ingestion import ports
from memory.models import IngestionSource

logger = logging.getLogger(__name__)

CALENDAR = IngestionSource.Kind.CALENDAR
CONNECTOR = "google"
AGENDA_TOOL = "calendar_agenda"
WINDOW_DAYS = 2  # yesterday and today


def _occurred_at(value, fallback):
    value = str(value or "")
    try:
        if "T" in value:
            parsed = datetime.fromisoformat(value)
            return parsed if timezone.is_aware(parsed) else fallback
        day = datetime.fromisoformat(value).date()
    except ValueError:
        return fallback
    return datetime.combine(day, dt_time.min, tzinfo=ZoneInfo(settings.TIME_ZONE))


def _record_event(binding, event, now):
    organiser = bool(event.get("organizer_self"))
    title = event.get("summary", "")
    return ports.record_episode(
        source=CALENDAR,
        kind="calendar.event",
        external_id=f"gcal:{binding.id}:{event['id']}",
        occurred_at=_occurred_at(event.get("start"), now),
        mapping_key=f"calendar:{binding.id}",
        owner_text=title if organiser else "",
        context_text=""
        if organiser
        else (f"Event title (not owner-authored): {title}" if title else ""),
        metadata={
            "start": event.get("start", ""),
            "end": event.get("end", ""),
            "all_day": bool(event.get("all_day")),
            "attendee_count": int(event.get("attendee_count") or 0),
            "organizer_is_owner": organiser,
            "binding_id": str(binding.id),
            "data_class": "personal",
        },
    )


def _ingest_binding(binding, *, start_date, now):
    events = google.read_calendar_metadata(
        start_date=start_date,
        days=WINDOW_DAYS,
        timeout=settings.CONNECTOR_READ_TIMEOUT_SECONDS,
    )
    results = {}
    for event in events:
        try:
            with transaction.atomic():
                status = _record_event(binding, event, now).status
        except Exception:
            logger.exception("Calendar episode for binding %s failed", binding.pk)
            status = "error"
        results[status] = results.get(status, 0) + 1
    return results


def ingest_calendar_metadata(*, now=None):
    """The beat entry point (`connectors.tasks.ingest_calendar_metadata`).
    Returns `{"bindings": n, "failed": n, "results": {status: count}}`."""
    now = now or timezone.now()
    empty = {"bindings": 0, "failed": 0, "results": {}}
    if not ports.source_accepting(CALENDAR):
        return empty
    manifest = get_manifest(CONNECTOR)
    if manifest is None or manifest.blocked:
        return empty
    policy = manifest.get_tool(AGENDA_TOOL)
    start_date = timezone.localdate(now) - timedelta(days=1)
    totals, done, failed = {}, 0, 0
    bindings = ConnectorBinding.objects.filter(connector=CONNECTOR, enabled=True).select_related(
        "workspace"
    )
    for binding in bindings.order_by("created_at"):
        if not tool_available(binding, policy):
            continue
        outcome, results = "ok", {}
        try:
            results = _ingest_binding(binding, start_date=start_date, now=now)
            done += 1
        except ConnectorError as exc:
            outcome = exc.code
            failed += 1
        except Exception:
            logger.exception("Calendar ingestion for binding %s failed", binding.pk)
            outcome = "error"
            failed += 1
        for status, count in results.items():
            totals[status] = totals.get(status, 0) + count
        record_audit(
            action="connector.ingest_read",
            actor=None,
            actor_type=AuditLog.ActorType.SYSTEM,
            workspace=binding.workspace,
            resource=binding,
            metadata={
                "connector": CONNECTOR,
                "tool": AGENDA_TOOL,
                "outcome": outcome,
                "days": WINDOW_DAYS,
                "results": results,
            },
        )
    return {"bindings": done, "failed": failed, "results": totals}
