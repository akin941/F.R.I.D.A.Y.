"""The Phase 12 go/no-go data (D20): "the owner keeps the morning digest on
after two weeks".

Per workspace with a `morning_digest` schedule: the first enable date, the
days the digest has been enabled since (summed over enabled intervals),
every enable/disable transition (audit `proactive.schedule.enabled` /
`.disabled` with `kind=morning_digest`), and the digests produced, opened
and spoken (web / `node_voice`). Reads only: counts, dates and channels,
never digest text.
"""

from django.utils import timezone

from audit.models import AuditLog
from proactive.models import DigestRun, ProactiveSchedule

ENABLED = "proactive.schedule.enabled"
DISABLED = "proactive.schedule.disabled"


def _transitions(workspace_id):
    rows = (
        AuditLog.objects.filter(
            workspace_id=workspace_id,
            action__in=(ENABLED, DISABLED),
            metadata__kind=ProactiveSchedule.Kind.MORNING_DIGEST,
        )
        .order_by("created_at", "id")
        .values_list("action", "created_at")
    )
    return [("enabled" if action == ENABLED else "disabled", at) for action, at in rows]


def _enabled_seconds(transitions, now):
    total, since = 0.0, None
    for event, at in transitions:
        if event == "enabled" and since is None:
            since = at
        elif event == "disabled" and since is not None:
            total += (at - since).total_seconds()
            since = None
    if since is not None:
        total += (now - since).total_seconds()
    return total


def workspace_checkpoint(schedule, *, now=None):
    now = now or timezone.now()
    transitions = _transitions(schedule.workspace_id)
    first_enabled = next((at for event, at in transitions if event == "enabled"), None)
    digests = DigestRun.objects.filter(workspace_id=schedule.workspace_id)
    return {
        "workspace": str(schedule.workspace_id),
        "workspace_name": schedule.workspace.name,
        "enabled_now": schedule.enabled,
        "first_enabled_at": first_enabled.isoformat() if first_enabled else None,
        "days_enabled": round(_enabled_seconds(transitions, now) / 86400, 1),
        "transitions": [{"event": event, "at": at.isoformat()} for event, at in transitions],
        "digests": digests.count(),
        "composed_by_model": digests.filter(composed_by=DigestRun.ComposedBy.MODEL).count(),
        "opened": digests.filter(opened_at__isnull=False).count(),
        "spoken_web": digests.filter(spoken_channel=DigestRun.SpokenChannel.WEB).count(),
        "spoken_node_voice": digests.filter(
            spoken_channel=DigestRun.SpokenChannel.NODE_VOICE
        ).count(),
    }


def build_checkpoint(*, now=None):
    schedules = (
        ProactiveSchedule.objects.filter(kind=ProactiveSchedule.Kind.MORNING_DIGEST)
        .select_related("workspace")
        .order_by("created_at")
    )
    return [workspace_checkpoint(schedule, now=now) for schedule in schedules]
