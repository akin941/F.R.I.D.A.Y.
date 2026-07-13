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

# The recovery scan (execution.recover_stale_agent_execution) only reclaims a
# `running` AgentStep once it's older than AGENT_STEP_RECOVERY_GRACE_SECONDS
# -- this must stay comfortably above the Celery task's own hard time_limit
# below, or the scan could reclaim a step that is still legitimately within
# its own timeout window (mirrors knowledge.tasks' identical
# _SOFT_TIME_LIMIT < _TIME_LIMIT fail-fast-at-import-time discipline).
assert settings.AGENT_STEP_RECOVERY_GRACE_SECONDS > settings.AGENT_STEP_TIMEOUT_SECONDS + 10, (
    "AGENT_STEP_RECOVERY_GRACE_SECONDS must exceed AGENT_STEP_TIMEOUT_SECONDS + 10 (the "
    "Celery hard time_limit buffer below) or the recovery scan could reclaim a step that "
    "is still legitimately in flight."
)


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
    backstop for anything truly unexpected (a bug, a transient DB error).

    Also resolves any step this run may have already claimed to `running`
    before the exception hit (Codex round-7 fix) -- otherwise it would sit
    `running` for the full recovery-scan grace window instead of failing
    immediately."""
    try:
        execution.advance_step(agent_id)
    except Exception:
        logger.exception("Unexpected failure advancing agent %s", agent_id)
        execution.resolve_running_step_after_unexpected_error(agent_id)


@shared_task
def recover_stale_agents():
    """Periodic scan (Celery beat, embedded via the worker's `-B` flag --
    see deploy/docker/compose.yml) for Agents/AgentSteps whose execution
    got stuck with no live worker left to unstick them (Codex round-2 fix,
    branch 3). Mirrors `tools.tasks.recover_stale_vault_export_runs`/
    `knowledge.tasks.recover_stale_knowledge_documents` exactly.

    Also runnable manually via `manage.py recover_agents` (calls
    `execution.recover_stale_agent_execution` directly, not `.delay()` --
    no broker/worker required)."""
    result = execution.recover_stale_agent_execution()
    return result
