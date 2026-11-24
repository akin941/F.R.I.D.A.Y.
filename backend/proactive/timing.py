"""Schedule arithmetic (D3): the next occurrence strictly after an instant.

Pure functions over aware datetimes, in `TIME_ZONE` (Europe/Istanbul).
Missed runs are never caught up: the runner always asks for the next
occurrence after *now*, so after downtime only one run happens.
"""

from datetime import datetime, time, timedelta

from django.utils import timezone

KIND_MORNING_DIGEST = "morning_digest"
KIND_HEARTBEAT = "heartbeat"
HEARTBEAT_INTERVALS = (15, 30, 60, 120)
ALL_WEEKDAYS = 0b1111111


def _aware(day, at: time) -> datetime:
    return timezone.make_aware(datetime.combine(day, at))


def in_quiet_hours(instant: datetime, start: time | None, end: time | None) -> bool:
    if start is None or end is None or start == end:
        return False
    local = timezone.localtime(instant).time()
    if start < end:
        return start <= local < end
    return local >= start or local < end


def _next_local_time_after(instant: datetime, at: time) -> datetime:
    local = timezone.localtime(instant)
    candidate = _aware(local.date(), at)
    if candidate <= instant:
        candidate = _aware(local.date() + timedelta(days=1), at)
    return candidate


def next_digest_run(*, local_time: time, weekdays: int, after: datetime) -> datetime | None:
    if not local_time or not (weekdays & ALL_WEEKDAYS):
        return None
    local = timezone.localtime(after)
    for offset in range(8):
        day = local.date() + timedelta(days=offset)
        if not weekdays & (1 << day.weekday()):
            continue
        candidate = _aware(day, local_time)
        if candidate > after:
            return candidate
    return None


def next_heartbeat_run(
    *, interval_minutes: int, quiet_start: time | None, quiet_end: time | None, after: datetime
) -> datetime | None:
    if interval_minutes not in HEARTBEAT_INTERVALS:
        return None
    candidate = after + timedelta(minutes=interval_minutes)
    if in_quiet_hours(candidate, quiet_start, quiet_end):
        candidate = _next_local_time_after(candidate, quiet_end)
    return candidate


def next_run(schedule, *, after: datetime) -> datetime | None:
    """The schedule's next occurrence after `after`, or None when it is
    disabled or incomplete."""
    if not schedule.enabled:
        return None
    if schedule.kind == KIND_MORNING_DIGEST:
        return next_digest_run(
            local_time=schedule.local_time, weekdays=schedule.weekdays, after=after
        )
    if schedule.kind == KIND_HEARTBEAT:
        return next_heartbeat_run(
            interval_minutes=schedule.interval_minutes,
            quiet_start=schedule.quiet_start,
            quiet_end=schedule.quiet_end,
            after=after,
        )
    return None
