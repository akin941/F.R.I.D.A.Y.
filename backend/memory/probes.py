"""The `memory` probes (Phase 12 D1, branch 2): `memory.calendar_today`,
`memory.learning_card` and `memory.sessions_waiting`.

Label rules (D1):

- **Calendar.** An event's title is a label only when the owner organised
  it: `connectors.ingestion` stores that title as `owner_text`; a
  third-party title lives in `context_text` (attacker-authorable) and is
  never read here. Such an event counts and shows its start time with the
  Friday label "takvim öğesi".
- **Learning card.** Counts by kind only (pending learned memories,
  core-block proposals, facts created since `window_start(1)`), with no
  web check and no item text; the digest points to `/memory/learning`,
  where the owner decides each item (ADR-012).
- **Sessions.** A coding session waiting for the owner is labelled with
  its project (the mapped directory's name) -- never its
  `last_notification`, which is tool/model text.
"""

from datetime import datetime, timedelta
from datetime import time as dt_time

from django.conf import settings
from django.utils import timezone

from core import probes
from memory.ingestion.digest import window_start
from memory.ingestion.hooks import stale_after
from memory.models import (
    CodingSession,
    CoreBlockProposal,
    Episode,
    IngestionSource,
    Memory,
    MemoryFact,
)

CALENDAR_TODAY = "memory.calendar_today"
LEARNING_CARD = "memory.learning_card"
SESSIONS_WAITING = "memory.sessions_waiting"
CALENDAR_EVENT_KIND = "calendar.event"
CALENDAR_FALLBACK_LABEL = "takvim öğesi"
ALL_DAY_LABEL = "tüm gün"


def _max_keys():
    return int(settings.PROACTIVE_PROBE_MAX_KEYS)


def _local_day_bounds(now):
    tz = timezone.get_current_timezone()
    day = timezone.localdate(now)
    start = datetime.combine(day, dt_time.min, tzinfo=tz)
    return start, start + timedelta(days=1)


def _calendar_label(episode):
    meta = episode.metadata if isinstance(episode.metadata, dict) else {}
    when = (
        ALL_DAY_LABEL
        if meta.get("all_day")
        else timezone.localtime(episode.occurred_at).strftime("%H:%M")
    )
    # Only an owner-organised event carries `owner_text`; anything else is
    # the generic Friday label (its title stays in context_text, unread).
    return probes.safe_label(episode.owner_text, CALENDAR_FALLBACK_LABEL, prefix=f"{when} ")


def collect_calendar_today(workspace, owner, now) -> probes.ProbeResult:
    start, end = _local_day_bounds(now)
    events = Episode.objects.for_actor(owner, workspace=workspace).filter(
        source__kind=IngestionSource.Kind.CALENDAR,
        kind=CALENDAR_EVENT_KIND,
        occurred_at__gte=start,
        occurred_at__lt=end,
    )
    rows = list(
        events.order_by("occurred_at", "id").only("id", "owner_text", "occurred_at", "metadata")[
            : _max_keys()
        ]
    )
    items = tuple(
        probes.ProbeItem(key=str(row.pk), label=_calendar_label(row))
        for row in rows[: probes.MAX_ITEMS]
    )
    return probes.ProbeResult(
        count=events.count(),
        data_class=probes.data_class_of(workspace),
        items=items,
        item_keys=tuple(str(row.pk) for row in rows),
    )


# Learning-card kinds: (key, Friday label template).
_LEARNING_KINDS = (
    ("memory", "{n} anı önerisi"),
    ("core-block", "{n} çekirdek blok önerisi"),
    ("fact", "{n} olgu önerisi"),
)


def learning_counts(workspace, owner, now):
    since = window_start(1, now)
    memories = Memory.objects.for_actor(owner, workspace=workspace).filter(
        ingestion_source__isnull=False,
        status=Memory.Status.PENDING_APPROVAL,
        created_at__gte=since,
    )
    proposals = CoreBlockProposal.objects.for_actor(owner, workspace=workspace).filter(
        ingestion_source__isnull=False,
        status=CoreBlockProposal.Status.PENDING,
        created_at__gte=since,
    )
    facts = MemoryFact.objects.for_actor(owner, workspace=workspace).filter(
        ingestion_source__isnull=False,
        origin=MemoryFact.Origin.LEARNED,
        status=MemoryFact.Status.PENDING_APPROVAL,
        created_at__gte=since,
    )
    return {"memory": memories.count(), "core-block": proposals.count(), "fact": facts.count()}


def collect_learning_card(workspace, owner, now) -> probes.ProbeResult:
    counts = learning_counts(workspace, owner, now)
    items = tuple(
        probes.ProbeItem(key=key, label=template.format(n=counts[key]), link="/memory/learning")
        for key, template in _LEARNING_KINDS
        if counts[key]
    )
    return probes.ProbeResult(
        count=sum(counts.values()),
        data_class=probes.data_class_of(workspace),
        items=items,
        item_keys=tuple(item.key for item in items),
    )


def collect_sessions_waiting(workspace, owner, now) -> probes.ProbeResult:
    waiting = CodingSession.objects.for_actor(owner, workspace=workspace).filter(
        status=CodingSession.Status.WAITING_FOR_OWNER,
        source__forgotten_at__isnull=True,
        last_event_at__gt=now - stale_after(),
    )
    rows = list(
        waiting.order_by("-last_event_at", "-id").values_list("id", "project")[: _max_keys()]
    )
    items = tuple(
        probes.ProbeItem(
            key=str(pk),
            label=probes.safe_label(project, "Claude Code", prefix="Oturum: "),
            link="/sessions",
        )
        for pk, project in rows[: probes.MAX_ITEMS]
    )
    return probes.ProbeResult(
        count=waiting.count(),
        data_class=probes.data_class_of(workspace),
        items=items,
        item_keys=tuple(str(pk) for pk, _ in rows),
    )


SPECS = (
    probes.ProbeSpec(
        name=CALENDAR_TODAY,
        owner_app="memory",
        collect=collect_calendar_today,
        digest_section="calendar",
        link="/today",
    ),
    probes.ProbeSpec(
        name=LEARNING_CARD,
        owner_app="memory",
        collect=collect_learning_card,
        digest_section="learning",
        link="/memory/learning",
    ),
    probes.ProbeSpec(
        name=SESSIONS_WAITING,
        owner_app="memory",
        collect=collect_sessions_waiting,
        digest_section="sessions",
        notice_kind=SESSIONS_WAITING,
        link="/sessions",
        notice_template="{label} izin bekliyor",
    ),
)


def register():
    for spec in SPECS:
        probes.register_probe(spec)
