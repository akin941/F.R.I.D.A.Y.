"""The two static beat entries of `proactive` (D3, D5).

- `heartbeat` (every 60 s, worker beat): select due schedules, claim each
  with `select_for_update(skip_locked=True)`, advance `next_run_at`
  **before** running (a crash skips one run, never repeats it), then run
  each claimed schedule inside its unattended context. Missed runs are not
  caught up: the next occurrence is always computed after *now*. A paused
  workspace or an inactive owner skips the schedule (`skipped:paused` /
  `skipped:owner_inactive`), audited once per transition.
- `prune` (daily): read or dismissed notices older than
  `PROACTIVE_NOTICE_RETENTION_DAYS` are hard-deleted.
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from audit.models import AuditLog
from audit.services import record_audit
from proactive import timing
from proactive.models import ProactiveNotice, ProactivePause, ProactiveSchedule
from proactive.runs import run_schedule

logger = logging.getLogger(__name__)

SKIPPED_PAUSED = "skipped:paused"
SKIPPED_OWNER_INACTIVE = "skipped:owner_inactive"
SKIPPED_QUIET_HOURS = "skipped:quiet_hours"


def _skip_reason(schedule, paused_ids):
    if schedule.workspace_id in paused_ids:
        return ProactiveSchedule.SkipReason.PAUSED
    owner = schedule.workspace.owner
    if owner is None or not owner.is_active:
        return ProactiveSchedule.SkipReason.OWNER_INACTIVE
    return ProactiveSchedule.SkipReason.NONE


def claim_due_schedules(now):
    """Claim every due schedule; returns (schedules to run, summary)."""
    summary = {"ran": 0, SKIPPED_PAUSED: 0, SKIPPED_OWNER_INACTIVE: 0, SKIPPED_QUIET_HOURS: 0}
    to_run = []
    with transaction.atomic():
        due = list(
            ProactiveSchedule.objects.select_for_update(skip_locked=True, of=("self",))
            .filter(enabled=True, next_run_at__isnull=False, next_run_at__lte=now)
            .select_related("workspace", "workspace__owner")
            .order_by("next_run_at")
        )
        paused_ids = set(
            ProactivePause.objects.filter(
                workspace_id__in=[s.workspace_id for s in due]
            ).values_list("workspace_id", flat=True)
        )
        for schedule in due:
            reason = _skip_reason(schedule, paused_ids)
            update = {"next_run_at": timing.next_run(schedule, after=now)}
            if reason != schedule.last_skip_reason:
                update["last_skip_reason"] = reason
                if reason:
                    record_audit(
                        action="proactive.schedule.skipped",
                        actor=None,
                        actor_type=AuditLog.ActorType.SYSTEM,
                        workspace=schedule.workspace,
                        resource=schedule,
                        metadata={"kind": schedule.kind, "reason": str(reason)},
                    )
            ProactiveSchedule.objects.filter(pk=schedule.pk).update(**update)
            if reason == ProactiveSchedule.SkipReason.PAUSED:
                summary[SKIPPED_PAUSED] += 1
            elif reason == ProactiveSchedule.SkipReason.OWNER_INACTIVE:
                summary[SKIPPED_OWNER_INACTIVE] += 1
            elif schedule.kind == ProactiveSchedule.Kind.HEARTBEAT and timing.in_quiet_hours(
                now, schedule.quiet_start, schedule.quiet_end
            ):
                summary[SKIPPED_QUIET_HOURS] += 1
            else:
                to_run.append(schedule)
    return to_run, summary


def run_due_schedules(now=None):
    now = now or timezone.now()
    to_run, summary = claim_due_schedules(now)
    for schedule in to_run:
        try:
            run_schedule(schedule, now=now)
            summary["ran"] += 1
        except Exception as exc:
            logger.warning("Proactive schedule %s failed (%s)", schedule.pk, exc.__class__.__name__)
    return summary


@shared_task(name="proactive.tasks.heartbeat")
def heartbeat():
    return run_due_schedules()


def prune_notices(now=None):
    now = now or timezone.now()
    cutoff = now - timedelta(days=int(settings.PROACTIVE_NOTICE_RETENTION_DAYS))
    deleted, _ = ProactiveNotice.objects.filter(
        Q(read_at__lt=cutoff) | Q(dismissed_at__lt=cutoff)
    ).delete()
    return deleted


@shared_task(name="proactive.tasks.prune")
def prune():
    return prune_notices()
