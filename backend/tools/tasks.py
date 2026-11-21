"""The `vault_export` Celery task + its periodic recovery scan (MVP 5
branch 3). Autodiscovered by `config.celery.app.autodiscover_tasks()`.
Mirrors `memory.tasks`/`knowledge.tasks` exactly."""

import logging
from datetime import timedelta

from celery import shared_task
from django.db.models import Q
from django.utils import timezone

from tools.export import (
    _SOFT_TIME_LIMIT,
    _TIME_LIMIT,
    PENDING_RECOVERY_GRACE_SECONDS,
    claim_vault_export_run,
    dispatch_vault_export_run,
    finalize_vault_export_failure,
    process_claimed_vault_export_run,
)
from tools.models import VaultExportRun

logger = logging.getLogger(__name__)


@shared_task(soft_time_limit=_SOFT_TIME_LIMIT, time_limit=_TIME_LIMIT)
def run_vault_export(run_id):
    """Claim `run_id`, export the workspace's approved memories, and write
    terminal state. A safe no-op if the run is not claimable (already
    handled, or a live worker holds it)."""
    claimed = claim_vault_export_run(run_id)
    if claimed is None:
        return
    claimed_id, claim_token = claimed
    try:
        process_claimed_vault_export_run(claimed_id, claim_token)
    except Exception:
        # A claimed run must never be silently abandoned mid-flight -- see
        # memory.tasks.run_extraction_job's identical comment on why the
        # raw exception text is never persisted (only logged): it could
        # carry a DB DSN, the real vault path, or other sensitive detail.
        logger.exception("Unexpected failure processing vault export run %s", claimed_id)
        finalize_vault_export_failure(
            claimed_id, claim_token, error_type=VaultExportRun.ErrorType.UNKNOWN
        )


@shared_task
def recover_stale_vault_export_runs():
    """Periodic scan (Celery beat, embedded via the worker's `-B` flag) for
    runs whose original dispatch may have been lost: `pending` runs stuck
    past the grace window, and `running` runs whose lease expired. Mirrors
    `memory.tasks.recover_stale_extraction_jobs`/`knowledge.tasks.
    recover_stale_knowledge_documents` exactly.

    Also runnable manually via `manage.py recover_vault_export_runs` (calls
    this function directly, not `.delay()` -- no broker/worker required).
    """
    now = timezone.now()
    pending_grace = now - timedelta(seconds=PENDING_RECOVERY_GRACE_SECONDS)
    stale_ids = list(
        VaultExportRun.objects.filter(
            Q(status=VaultExportRun.Status.PENDING, updated_at__lt=pending_grace)
            | Q(status=VaultExportRun.Status.RUNNING, lease_expires_at__lt=now)
        ).values_list("id", flat=True)
    )
    for run_id in stale_ids:
        dispatch_vault_export_run(str(run_id))
    return len(stale_ids)


@shared_task
def pull_transcripts_task():
    """Phase 8B branch 8 (brief D13): the transcript pull, on the worker
    beat every MEMORY_TRANSCRIPT_PULL_MINUTES. A no-op while both transcript
    sources are disabled or no node is reachable."""
    from tools.transcript_ingest import pull_transcripts

    report = pull_transcripts()
    return {"invokes": report.invokes, "stored": report.stored, "failed": report.failed}
