"""Extraction job allocation, dispatch, claiming and finalization (MVP 2 §3-4).

Range allocation (branch 3) creates jobs as inert `pending` rows. This
branch adds: dispatch (on_commit, non-propagating on broker failure),
lease-fenced claiming (so a worker that outlives its lease can never write a
stale result), and the two ways a job can end — success or
retryable/terminal failure.
"""

import logging
import uuid
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from memory.models import Memory, MemoryExtractionJob, compute_fingerprint

logger = logging.getLogger(__name__)

# A claim is valid for this long before another worker may take it over.
# Must comfortably exceed the gateway's WORST-CASE call duration, not a
# single attempt: InferenceGateway tries up to 2 providers
# (`_select_providers()[:2]`), each with up to `AI_MAX_RETRIES + 1` attempts
# (default 3) at up to `AI_TIMEOUT_READ` seconds (default 60) — roughly
# 2 * 3 * 60 = 360s worst case. A lease shorter than that would let a second
# worker take over a call that is still legitimately in flight, wasting the
# first worker's (correctly fenced-out, so not incorrect — just wasted) work
# on every slow/retried call. Task soft/hard time limits (memory/tasks.py)
# sit between that worst case and this lease, so a genuinely slow-but-still-
# retrying call isn't hard-killed early, while a truly hung task still can't
# straddle a takeover.
CLAIM_LEASE_SECONDS = 420

# Retryable failures increment retry_count and return the job to pending
# until retry_count reaches this limit; the attempt that reaches it
# transitions to `failed` instead — a job can never stay indefinitely
# recoverable.
MAX_EXTRACTION_RETRIES = 3


@transaction.atomic
def create_extraction_job(*, conversation, through_message):
    """Allocate the next contiguous range for `conversation`, create its
    extraction job, and dispatch it once the creating transaction commits.

    Callers MUST already hold a `select_for_update` lock on `conversation`,
    taken BEFORE `through_message` was created, inside the same transaction
    (see `assistant.services._persist_assistant_message`). Locking before
    insertion means two concurrent persists serialize in insertion order, so
    `conversation.last_extracted_message` — the range's lower bound — can
    never be read stale and two jobs can never claim overlapping ranges.
    This function only reads the cursor and advances it; it does not
    re-acquire the lock itself, and the `@transaction.atomic` here is a cheap
    savepoint, not a substitute for the caller's lock.
    """
    if (
        through_message.conversation_id != conversation.id
        or through_message.workspace_id != conversation.workspace_id
    ):
        # Defense-in-depth, mirroring Memory.save()'s own provenance
        # invariant: normal application code always creates through_message
        # from this exact conversation/workspace, but nothing at the DB
        # level enforces that — and a job whose range end silently pointed
        # at a foreign message would both leak a foreign FK reference and
        # corrupt the cursor (later allocations would compare against the
        # foreign message's timestamp instead of this conversation's own).
        raise ValueError("through_message must belong to the given conversation and workspace.")
    after_message = conversation.last_extracted_message
    job = MemoryExtractionJob.objects.create(
        workspace=conversation.workspace,
        conversation=conversation,
        after_message=after_message,
        through_message=through_message,
        status=MemoryExtractionJob.Status.PENDING,
    )
    conversation.last_extracted_message = through_message
    conversation.save(update_fields=["last_extracted_message"])
    transaction.on_commit(lambda: dispatch_extraction_job(job.id))
    return job


def dispatch_extraction_job(job_id):
    """Publish `job_id` to the broker. A broker PUBLICATION failure (e.g.
    Redis down) must never propagate into the caller — the SSE reply must
    not fail because the broker is unreachable. The job stays `pending`;
    the recovery scan re-dispatches it later.

    This must NOT swallow the task's own exceptions — under
    `CELERY_TASK_ALWAYS_EAGER` (tests only; see backend/conftest.py),
    `.delay()` executes the task body inline, so a blanket `except
    Exception` here would also catch task-body bugs and defeat
    `CELERY_TASK_EAGER_PROPAGATES`. Eager mode never touches a real broker,
    so there is nothing broker-shaped to catch in that mode in the first
    place — the try/except below applies only when NOT eager.
    """
    from django.conf import settings

    from memory.tasks import run_extraction_job

    if settings.CELERY_TASK_ALWAYS_EAGER:
        run_extraction_job.delay(str(job_id))
        return
    try:
        run_extraction_job.delay(str(job_id))
    except Exception:
        logger.exception("Failed to publish extraction job %s to the broker", job_id)


def message_range(job):
    """The job's fixed `(created_at, id)`-ordered message queryset — a
    keyset range so ties in `created_at` (possible at sub-microsecond
    precision) never include/exclude a message ambiguously.

    Goes through `job.conversation.messages` (the reverse FK accessor) so
    this module never needs to import `assistant.models.Message` directly —
    `memory` must not import `assistant` (one-way dependency, see the
    pyproject import-linter contract); `job.after_message`/`through_message`
    are already-resolved model instances via Django's string FK labels.
    """
    # workspace_id filter is defense-in-depth against a stray cross-workspace
    # Message row, mirroring assistant.context.build_context's own comment.
    qs = job.conversation.messages.filter(workspace_id=job.workspace_id)
    if job.after_message_id is not None:
        after = job.after_message
        qs = qs.filter(
            Q(created_at__gt=after.created_at) | Q(created_at=after.created_at, id__gt=after.id)
        )
    through = job.through_message
    qs = qs.filter(
        Q(created_at__lt=through.created_at) | Q(created_at=through.created_at, id__lte=through.id)
    )
    return qs.order_by("created_at", "id")


def claim_extraction_job(job_id):
    """Atomically claim `job_id` for this attempt: `pending`, or `running`
    with an expired lease (takeover). Returns `(job_id, claim_token)` on
    success, `None` if the job is not claimable (already succeeded/failed,
    or currently claimed by a live worker) — always a safe no-op, never an
    error, since redelivery of an already-handled job is expected.

    Returns primitives, not the model instance: the gateway call that
    follows a successful claim runs OUTSIDE any transaction (it can take
    seconds), so nothing here should be treated as still "held".
    """
    now = timezone.now()
    with transaction.atomic():
        job = (
            MemoryExtractionJob.objects.select_for_update(skip_locked=True)
            .filter(
                Q(pk=job_id)
                & (
                    Q(status=MemoryExtractionJob.Status.PENDING)
                    | Q(status=MemoryExtractionJob.Status.RUNNING, lease_expires_at__lt=now)
                )
            )
            .first()
        )
        if job is None:
            return None
        token = uuid.uuid4()
        job.status = MemoryExtractionJob.Status.RUNNING
        job.claim_token = token
        job.lease_expires_at = now + timedelta(seconds=CLAIM_LEASE_SECONDS)
        job.save(update_fields=["status", "claim_token", "lease_expires_at", "updated_at"])
    return job_id, token


def _fenced(job_id, claim_token):
    """Re-lock the job and verify this attempt still owns it. Returns the
    locked job, or `None` if the token was rotated out from under a stale
    worker — every post-claim write goes through this, so a worker that
    outlived its lease discards whatever it was about to write, success or
    failure alike (its ModelUsage row, written by the gateway itself, stands
    as the cost record of the wasted attempt — that is the one intentional
    exception to this fencing)."""
    job = (
        MemoryExtractionJob.objects.select_for_update()
        .filter(pk=job_id, status=MemoryExtractionJob.Status.RUNNING, claim_token=claim_token)
        .first()
    )
    return job


@transaction.atomic
def finalize_extraction_success(job_id, claim_token, candidates):
    """Create the candidates (born `pending_approval`, `source=ai_suggested`)
    and mark the job succeeded, in one fenced transaction. Fingerprint
    conflicts are absorbed via `ignore_conflicts` rather than failing the
    whole batch — a near-duplicate suggestion is silently skipped, not an
    error. Returns `False` if this attempt was fenced out (discard, no-op)."""
    job = _fenced(job_id, claim_token)
    if job is None:
        return False
    memories = [
        Memory(
            workspace=job.workspace,
            memory_type=c["type"],
            sensitivity=Memory.Sensitivity.NORMAL,
            status=Memory.Status.PENDING_APPROVAL,
            source=Memory.Source.AI_SUGGESTED,
            title=c["title"],
            content=c["content"],
            fingerprint=compute_fingerprint(c["content"]),
            source_conversation=job.conversation,
        )
        for c in candidates
    ]
    if memories:
        Memory.objects.bulk_create(memories, ignore_conflicts=True)
    job.status = MemoryExtractionJob.Status.SUCCEEDED
    job.claim_token = None
    job.lease_expires_at = None
    job.error_type = ""
    job.error_message = ""
    job.save(
        update_fields=[
            "status",
            "claim_token",
            "lease_expires_at",
            "error_type",
            "error_message",
            "updated_at",
        ]
    )
    return True


@transaction.atomic
def finalize_extraction_failure(job_id, claim_token, *, error_type, error_message, retryable):
    """A gateway failure (retryable or not) or a schema-validation failure
    (always terminal — pass `retryable=False`). Retryable failures increment
    `retry_count` on every attempt and return to `pending` while it remains
    below `MAX_EXTRACTION_RETRIES`; the attempt whose increment REACHES the
    limit transitions to `failed` instead (so the final `retry_count` on a
    failed job equals `MAX_EXTRACTION_RETRIES` exactly) — a job can never
    stay indefinitely recoverable. Returns `False` if this attempt was
    fenced out."""
    job = _fenced(job_id, claim_token)
    if job is None:
        return False
    if retryable:
        job.retry_count += 1
        job.status = (
            MemoryExtractionJob.Status.PENDING
            if job.retry_count < MAX_EXTRACTION_RETRIES
            else MemoryExtractionJob.Status.FAILED
        )
    else:
        job.status = MemoryExtractionJob.Status.FAILED
    job.claim_token = None
    job.lease_expires_at = None
    job.error_type = error_type
    job.error_message = error_message[:500]
    job.save(
        update_fields=[
            "status",
            "retry_count",
            "claim_token",
            "lease_expires_at",
            "error_type",
            "error_message",
            "updated_at",
        ]
    )
    return True
