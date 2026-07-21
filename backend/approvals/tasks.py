"""Approval-request expiry sweep (MVP 5 §1) -- the periodic backstop for
`APPROVAL_REQUEST_TTL_HOURS`. `approvals.services._check_not_expired` is the
primary enforcement point (checked at decision time, so a stale request can
never be approved by accident regardless of the sweep's timing); this scan
exists so a `pending` request nobody ever tries to decide still stops
appearing actionable in the list once it's stale, rather than sitting
`pending` forever. Autodiscovered by `config.celery.app.autodiscover_tasks()`.
"""

from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from approvals.models import ApprovalRequest
from approvals.services import record_expired


@shared_task
def sweep_expired_approval_requests():
    """Periodic scan (Celery beat, embedded via the worker's `-B` flag --
    see deploy/docker/compose.yml). Transitions `pending` requests older
    than the TTL to `expired`, one row-lock at a time so a concurrent
    decide (which locks the same row in `approvals.services._locked`)
    always wins the race rather than being silently overwritten by the scan.

    Also runnable manually via `manage.py sweep_approval_requests` (calls
    this function directly, not `.delay()` -- no broker/worker required).
    """
    cutoff = timezone.now() - timedelta(hours=settings.APPROVAL_REQUEST_TTL_HOURS)
    stale_ids = list(
        ApprovalRequest.objects.filter(
            status=ApprovalRequest.Status.PENDING, created_at__lt=cutoff
        ).values_list("id", flat=True)
    )
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
