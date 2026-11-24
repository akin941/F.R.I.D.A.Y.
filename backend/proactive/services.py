"""Owner-side services (Phase 12 D3, D5): the only writers of schedules,
the pause and notice read state.

Every writer starts with `core.owner_web.require_owner_web` (the owner, a
person, over the web, in a workspace they own) and refuses inside an
unattended context. Nothing else -- no tool, read tool, router intent,
skill, connector manifest, harness path or run -- reaches these functions
("cron cannot create cron"; the import contracts keep every app but `ops`
from importing this module at all).
"""

from datetime import time

from django.db import transaction
from django.utils import timezone

from audit.services import record_audit
from core.owner_web import OwnerWebRequired, require_owner_web
from core.unattended import deny_if_unattended
from proactive import timing
from proactive.models import DigestRun, ProactiveNotice, ProactivePause, ProactiveSchedule


class ProactiveError(Exception):
    """Base of the service refusals; `code` is a closed token."""

    code = "invalid"


class ProactivePermissionDenied(ProactiveError):
    code = "owner_required"

    def __init__(self, message, code=None):
        if code:
            self.code = code
        super().__init__(message)


class ProactiveInvalid(ProactiveError):
    code = "invalid"


def _require_owner_web(*, actor, workspace, channel):
    try:
        require_owner_web(actor=actor, workspace=workspace, channel=channel)
    except OwnerWebRequired as exc:
        raise ProactivePermissionDenied(str(exc), code=exc.code) from None


def _audit(action, *, actor, workspace, resource=None, **metadata):
    record_audit(
        action=action, actor=actor, workspace=workspace, resource=resource, metadata=metadata
    )


QUIET_FIELDS = frozenset({"quiet_start", "quiet_end"})


def _validated_fields(kind, fields):
    """The closed per-kind field set. Unknown kinds and fields of the other
    kind are refused, never ignored."""
    digest_fields = {"local_time", "weekdays", "speak_on_open"}
    heartbeat_fields = {"interval_minutes", "quiet_start", "quiet_end"}
    if kind == ProactiveSchedule.Kind.MORNING_DIGEST:
        allowed, foreign = digest_fields, heartbeat_fields
    elif kind == ProactiveSchedule.Kind.HEARTBEAT:
        allowed, foreign = heartbeat_fields, digest_fields
    else:
        raise ProactiveInvalid("Unknown schedule kind.")
    given = {key for key, value in fields.items() if value is not None}
    if given & foreign:
        raise ProactiveInvalid("These fields do not belong to this schedule kind.")
    clean = {key: fields[key] for key in allowed if fields.get(key) is not None}
    # Quiet hours are clearable: an explicit null for a bound clears it
    # (null for both removes quiet hours). Whether the pair is complete is
    # checked in `save_schedule` against the merged row, not the payload.
    for key in QUIET_FIELDS & allowed:
        if key in fields and fields[key] is None:
            clean[key] = None
    if "weekdays" in clean and not (0 < int(clean["weekdays"]) <= timing.ALL_WEEKDAYS):
        raise ProactiveInvalid("Pick at least one weekday.")
    if (
        "interval_minutes" in clean
        and int(clean["interval_minutes"]) not in timing.HEARTBEAT_INTERVALS
    ):
        raise ProactiveInvalid("The heartbeat interval is 15, 30, 60 or 120 minutes.")
    for key in ("local_time", "quiet_start", "quiet_end"):
        if clean.get(key) is not None and not isinstance(clean[key], time):
            raise ProactiveInvalid("Times are HH:MM.")
    return clean


def save_schedule(*, actor, channel, workspace, kind, enabled, **fields):
    """Create or update the workspace's one schedule of `kind`. Audits
    `proactive.schedule.created` / `.updated`, and on an enabled-state
    change `proactive.schedule.enabled` / `.disabled` (the D20 checkpoint
    data)."""
    deny_if_unattended("schedule_write")
    _require_owner_web(actor=actor, workspace=workspace, channel=channel)
    clean = _validated_fields(kind, fields)
    now = timezone.now()
    with transaction.atomic():
        schedule = (
            ProactiveSchedule.objects.select_for_update()
            .filter(workspace=workspace, kind=kind)
            .first()
        )
        created = schedule is None
        was_enabled = False if created else schedule.enabled
        if created:
            schedule = ProactiveSchedule(workspace=workspace, kind=kind, created_by=actor)
        for key, value in clean.items():
            setattr(schedule, key, value)
        if (schedule.quiet_start is None) != (schedule.quiet_end is None):
            raise ProactiveInvalid("Quiet hours need both a start and an end.")
        schedule.enabled = bool(enabled)
        if schedule.enabled:
            if kind == ProactiveSchedule.Kind.MORNING_DIGEST and schedule.local_time is None:
                raise ProactiveInvalid("The morning digest needs a time.")
            if kind == ProactiveSchedule.Kind.HEARTBEAT and schedule.interval_minutes is None:
                raise ProactiveInvalid("The heartbeat needs an interval.")
        schedule.next_run_at = timing.next_run(schedule, after=now)
        schedule.save()
        _audit(
            "proactive.schedule.created" if created else "proactive.schedule.updated",
            actor=actor,
            workspace=workspace,
            resource=schedule,
            kind=kind,
            channel=channel,
        )
        if schedule.enabled != was_enabled and (schedule.enabled or not created):
            _audit(
                "proactive.schedule.enabled" if schedule.enabled else "proactive.schedule.disabled",
                actor=actor,
                workspace=workspace,
                resource=schedule,
                kind=kind,
                channel=channel,
            )
    return schedule


def is_paused(workspace) -> bool:
    return ProactivePause.objects.filter(workspace=workspace).exists()


def set_pause(*, actor, channel, workspace, paused):
    """Pause or resume every schedule of the workspace. Idempotent; audits
    `proactive.paused` / `proactive.resumed` on a state change only."""
    deny_if_unattended("pause_write")
    _require_owner_web(actor=actor, workspace=workspace, channel=channel)
    with transaction.atomic():
        current = ProactivePause.objects.select_for_update().filter(workspace=workspace).first()
        if paused and current is None:
            ProactivePause(workspace=workspace, paused_by=actor).save()
            _audit("proactive.paused", actor=actor, workspace=workspace, channel=channel)
        elif not paused and current is not None:
            current.delete()
            _audit("proactive.resumed", actor=actor, workspace=workspace, channel=channel)
    return bool(paused)


def mark_notice_read(*, actor, channel, notice):
    deny_if_unattended("notice_write")
    _require_owner_web(actor=actor, workspace=notice.workspace, channel=channel)
    updated = ProactiveNotice.objects.filter(pk=notice.pk, read_at__isnull=True).update(
        read_at=timezone.now()
    )
    notice.refresh_from_db()
    return bool(updated)


def dismiss_all_notices(*, actor, channel, workspace):
    deny_if_unattended("notice_write")
    _require_owner_web(actor=actor, workspace=workspace, channel=channel)
    count = ProactiveNotice.objects.filter(
        workspace=workspace, read_at__isnull=True, dismissed_at__isnull=True
    ).update(dismissed_at=timezone.now())
    if count:
        _audit(
            "proactive.notices.dismissed",
            actor=actor,
            workspace=workspace,
            channel=channel,
            count=count,
        )
    return count


DIGEST_ACK_EVENTS = ("opened", "spoken")


def ack_digest(*, actor, channel, digest, event):
    """D6: the web's `opened` / `spoken` acknowledgement -- the digest's
    own telemetry, set once (idempotent: a repeat changes nothing). Owner
    over the web only. Returns True when this call set the timestamp."""
    deny_if_unattended("digest_write")
    _require_owner_web(actor=actor, workspace=digest.workspace, channel=channel)
    if event not in DIGEST_ACK_EVENTS:
        raise ProactiveInvalid("event is opened or spoken.")
    now = timezone.now()
    rows = DigestRun.objects.filter(pk=digest.pk)
    if event == "opened":
        updated = rows.filter(opened_at__isnull=True).update(opened_at=now)
    else:
        updated = rows.filter(spoken_at__isnull=True).update(
            spoken_at=now, spoken_channel=DigestRun.SpokenChannel.WEB
        )
    digest.refresh_from_db()
    return bool(updated)
