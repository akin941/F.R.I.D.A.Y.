"""Connector Celery tasks (Phase 5 branch 4): the one-shot write, its
recovery scan and the pin beat. Autodiscovered by `config.celery`; all run
on the default queue (the compose worker) -- the MCP client only needs the
internal `connectors` network, no Docker socket and no runner host."""

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from connectors.manifest import get_manifest
from connectors.models import ConnectorBinding, ConnectorWriteExecution
from connectors.services import check_binding_pins
from connectors.writes import (
    PENDING_RECOVERY_GRACE_SECONDS,
    claim_write_execution,
    dispatch_write_execution,
    execute_claimed_write,
    fail_unexpected,
    mark_lost_executions,
)

logger = logging.getLogger(__name__)


def _time_limits():
    lease = settings.CONNECTOR_WRITE_LEASE_SECONDS
    return {"soft_time_limit": max(lease - 20, 10), "time_limit": max(lease - 10, 15)}


@shared_task(**_time_limits())
def run_connector_write(execution_id):
    """Claim a `pending` write and send it once. A no-op for anything not
    `pending` -- in particular a second delivery of the same message, or a
    re-dispatch after the row was already claimed, never sends again."""
    claim_token = claim_write_execution(execution_id)
    if claim_token is None:
        return
    try:
        execute_claimed_write(execution_id, claim_token)
    except Exception:
        # Whether the send happened is unknown: unknown_outcome, never a
        # retry. The exception text is logged, never stored.
        logger.exception("Unexpected failure in connector write %s", execution_id)
        fail_unexpected(execution_id, claim_token)


@shared_task
def recover_stale_connector_writes():
    """Periodic scan: re-publish `pending` writes whose dispatch was lost
    (never claimed, so never sent) and move `running` writes whose lease
    expired to `unknown_outcome` (their worker was lost; never re-sent)."""
    now = timezone.now()
    grace = now - timedelta(seconds=PENDING_RECOVERY_GRACE_SECONDS)
    pending_ids = list(
        ConnectorWriteExecution.objects.filter(
            status=ConnectorWriteExecution.Status.PENDING, updated_at__lt=grace
        ).values_list("id", flat=True)
    )
    for execution_id in pending_ids:
        dispatch_write_execution(str(execution_id))
    lost = mark_lost_executions(now)
    return {"redispatched": len(pending_ids), "unknown_outcome": lost}


@shared_task
def check_binding_pins_task(binding_id):
    check_binding_pins(binding_id)


@shared_task
def check_connector_pins():
    """The pin beat (D3, every 15 minutes): one pin/health check per enabled
    binding of an active manifest."""
    checked = 0
    for binding in ConnectorBinding.objects.filter(enabled=True).order_by("id"):
        manifest = get_manifest(binding.connector)
        if manifest is None or manifest.blocked:
            continue
        try:
            check_binding_pins(binding.id)
        except Exception:
            logger.exception("Pin check for binding %s failed unexpectedly", binding.id)
        checked += 1
    return checked
