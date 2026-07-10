"""The `memory.extraction` Celery task + its periodic recovery scan
(MVP 2 §4). Autodiscovered by `config.celery.app.autodiscover_tasks()`.
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Q
from django.utils import timezone

from ai_core.exceptions import GatewayError
from ai_core.gateway import InferenceGateway
from ai_core.models import PromptTemplate
from memory.extraction import ExtractionParseError, parse_candidates
from memory.jobs import (
    CLAIM_LEASE_SECONDS,
    claim_extraction_job,
    dispatch_extraction_job,
    finalize_extraction_failure,
    finalize_extraction_success,
    message_range,
)
from memory.models import MemoryExtractionJob

logger = logging.getLogger(__name__)

# A pending job still unclaimed after this long since its last update
# suggests its original on_commit dispatch was lost (e.g. broker was down at
# commit time) — long enough to never race a normal, near-instant dispatch.
PENDING_RECOVERY_GRACE_SECONDS = 60

_EXTRACTION_PROMPT_NAME = "jarvis_memory_extraction"

# Gateway worst case (see memory.jobs.CLAIM_LEASE_SECONDS): 2 providers *
# up to 3 attempts * AI_TIMEOUT_READ (default 60s) ~= 360s. Soft/hard limits
# sit ABOVE that (so a legitimately slow-but-still-retrying call isn't
# hard-killed mid-flight) and strictly BELOW CLAIM_LEASE_SECONDS (so a truly
# hung task still can't straddle a takeover by another worker).
_GATEWAY_WORST_CASE_SECONDS = 360
_SOFT_TIME_LIMIT = _GATEWAY_WORST_CASE_SECONDS + 30
_TIME_LIMIT = CLAIM_LEASE_SECONDS - 10


def _extraction_prompt_version():
    try:
        template = PromptTemplate.objects.get(name=_EXTRACTION_PROMPT_NAME)
    except PromptTemplate.DoesNotExist as exc:
        raise ImproperlyConfigured(
            f"{_EXTRACTION_PROMPT_NAME} prompt template is not configured"
        ) from exc
    if template.active_version_id is None:
        raise ImproperlyConfigured(
            f"{_EXTRACTION_PROMPT_NAME} prompt template has no active version"
        )
    return template.active_version


@shared_task(soft_time_limit=_SOFT_TIME_LIMIT, time_limit=_TIME_LIMIT)
def run_extraction_job(job_id):
    """Claim `job_id`, distill memory candidates from its message range via
    the gateway, and write terminal state. A safe no-op if the job is not
    claimable (already handled, or a live worker holds it) — redelivery of
    an already-succeeded job is expected, not an error.
    """
    claimed = claim_extraction_job(job_id)
    if claimed is None:
        return
    claimed_job_id, claim_token = claimed

    try:
        _process_claimed_job(claimed_job_id, claim_token)
    except Exception:
        # A claimed job must never be silently abandoned mid-flight: any
        # unexpected failure here (a bug, a missing prompt config, a DB
        # blip) still finalizes the claim as a retryable failure — bounded
        # by MAX_EXTRACTION_RETRIES like any other retryable outcome —
        # rather than leaking a `running` row that only the lease timeout
        # would eventually free. The exception's own text is NEVER persisted
        # (unlike GatewayError/ExtractionParseError, whose messages are
        # secret-safe by construction, an arbitrary exception here could
        # carry a DB DSN, a stack of local values, or other sensitive
        # detail) — the full traceback goes to the logger only.
        logger.exception("Unexpected failure processing extraction job %s", claimed_job_id)
        finalize_extraction_failure(
            claimed_job_id,
            claim_token,
            error_type="unexpected",
            error_message="Unexpected error — see worker logs for details.",
            retryable=True,
        )


def _process_claimed_job(claimed_job_id, claim_token):
    job = MemoryExtractionJob.objects.select_related("conversation").get(pk=claimed_job_id)
    prompt_version = _extraction_prompt_version()
    messages = [{"role": "system", "content": prompt_version.content}]
    messages.extend(
        {"role": role, "content": content}
        for role, content in message_range(job).values_list("role", "content")
    )

    gateway = InferenceGateway()
    try:
        result = gateway.complete(
            purpose="memory.extraction",
            messages=messages,
            actor=None,
            workspace=job.workspace,
            prompt_version=prompt_version,
            stream=False,
        )
    except GatewayError as exc:
        finalize_extraction_failure(
            claimed_job_id,
            claim_token,
            error_type=exc.error_type,
            error_message=str(exc),
            retryable=exc.retryable,
        )
        return

    try:
        candidates = parse_candidates(result.text)
    except ExtractionParseError as exc:
        # Schema-validation failure is always terminal — never assumed to be
        # a transient provider hiccup the model might self-correct on
        # identical input.
        finalize_extraction_failure(
            claimed_job_id,
            claim_token,
            error_type="schema_invalid",
            error_message=str(exc),
            retryable=False,
        )
        return

    finalize_extraction_success(claimed_job_id, claim_token, candidates)


@shared_task
def recover_stale_extraction_jobs():
    """Periodic scan (Celery beat, embedded via the worker's `-B` flag — see
    deploy/docker/compose.yml) for jobs whose original dispatch may have been
    lost: `pending` jobs stuck past the grace window, and `running` jobs
    whose lease has expired. Re-dispatching a job that is in fact still
    being processed by a live worker is safe — `claim_extraction_job` simply
    no-ops if the lease hasn't actually expired.

    Also runnable manually via `manage.py recover_extraction_jobs` (calls
    this function directly, not `.delay()` — no broker/worker required).
    """
    now = timezone.now()
    pending_grace = now - timedelta(seconds=PENDING_RECOVERY_GRACE_SECONDS)
    stale_ids = list(
        MemoryExtractionJob.objects.filter(
            Q(status=MemoryExtractionJob.Status.PENDING, updated_at__lt=pending_grace)
            | Q(status=MemoryExtractionJob.Status.RUNNING, lease_expires_at__lt=now)
        ).values_list("id", flat=True)
    )
    for job_id in stale_ids:
        dispatch_extraction_job(job_id)
    return len(stale_ids)
