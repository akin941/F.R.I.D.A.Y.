"""Approval-request expiry sweep (MVP 5 §1) -- the periodic backstop for
`APPROVAL_REQUEST_TTL_HOURS`. `approvals.services._check_not_expired` is the
primary enforcement point (checked at decision time, so a stale request can
never be approved by accident regardless of the sweep's timing); this scan
exists so a `pending` request nobody ever tries to decide still stops
appearing actionable in the list once it's stale, rather than sitting
`pending` forever. Autodiscovered by `config.celery.app.autodiscover_tasks()`.
"""

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from approvals.models import ApprovalRequest
from approvals.services import effective_approval_ttl, global_approval_ttl, record_expired
from tools.registry import tools_with_approval_ttl


@shared_task
def sweep_expired_approval_requests():
    """Periodic scan (Celery beat, embedded via the worker's `-B` flag --
    see deploy/docker/compose.yml). Transitions `pending` requests older
    than their TTL (the global `APPROVAL_REQUEST_TTL_HOURS`, or a tool's
    shorter `approval_ttl_seconds`, Phase 10 D6) to `expired`, one
    row-lock at a time so a concurrent
    decide (which locks the same row in `approvals.services._locked`)
    always wins the race rather than being silently overwritten by the scan.

    Also runnable manually via `manage.py sweep_approval_requests` (calls
    this function directly, not `.delay()` -- no broker/worker required).
    """
    now = timezone.now()
    pending = ApprovalRequest.objects.filter(status=ApprovalRequest.Status.PENDING)
    stale = pending.filter(created_at__lt=now - global_approval_ttl())
    # Phase 10 D6: a tool with its own (shorter) TTL expires on it -- every
    # minute (beat), still only a backstop to the decision-time check.
    for tool_name, ttl_seconds in tools_with_approval_ttl().items():
        stale = stale | pending.filter(
            tool_name=tool_name, created_at__lt=now - effective_approval_ttl(ttl_seconds)
        )
    stale_ids = list(stale.values_list("id", flat=True).distinct())
    return sum(_expire_one(request_id) for request_id in stale_ids)


@transaction.atomic
def _expire_one(request_id):
    request = (
        ApprovalRequest.objects.select_for_update(skip_locked=True)
        .filter(pk=request_id, status=ApprovalRequest.Status.PENDING)
        .first()
    )
    if request is None:
        return 0
    request.status = ApprovalRequest.Status.EXPIRED
    request.save(update_fields=["status", "updated_at"])
    record_expired(request)
    return 1
