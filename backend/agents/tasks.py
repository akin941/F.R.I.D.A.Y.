"""The `agents` execution-engine Celery task (MVP 7 §3). Autodiscovered by
`config.celery.app.autodiscover_tasks()`. One task invocation processes
exactly one `AgentStep` -- the task's own `soft_time_limit`/`time_limit`
IS the per-step timeout brake (brief §3, brake #2a), mirroring
`tools.tasks.run_vault_export`'s identical shape one level more granular
(there, one task processes one whole run; here, one task processes one
step, chaining itself for the next one via `agents.execution.
_after_step_resolved`'s own `transaction.on_commit` dispatch)."""

import logging

from celery import shared_task
from django.conf import settings

from agents import execution

logger = logging.getLogger(__name__)


@shared_task(
    soft_time_limit=settings.AGENT_STEP_TIMEOUT_SECONDS,
    time_limit=settings.AGENT_STEP_TIMEOUT_SECONDS + 10,
)
def advance_agent_step(agent_id):
    """Thin wrapper: all the actual logic (claim, brakes, dispatch
    chaining) lives in `agents.execution.advance_agent_step`, kept
    Celery-agnostic so it can be unit-tested without a broker/worker.
    A broad safety net beyond the specific `SoftTimeLimitExceeded` handling
    `execution.advance_agent_step` already does internally -- mirrors
    `tools.tasks.run_vault_export`'s identical outer `except Exception`
    backstop for anything truly unexpected (a bug, a transient DB error)."""
    try:
        execution.advance_step(agent_id)
    except Exception:
        logger.exception("Unexpected failure advancing agent %s", agent_id)
