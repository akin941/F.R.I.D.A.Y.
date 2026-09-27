"""Extraction job allocation, dispatch, claiming and finalization (MVP 2 §3-4),
plus Memory embedding claim/dispatch/finalization (Phase 1
`p1-memory-embeddings`).

Range allocation (branch 3) creates jobs as inert `pending` rows. This
branch adds: dispatch (on_commit, non-propagating on broker failure),
lease-fenced claiming (so a worker that outlives its lease can never write a
stale result), and the two ways a job can end — success or
retryable/terminal failure.

The embedding functions below are a separate lease-fenced flow with no job
model of its own — the lease fields live directly on `Memory` (mirrors
`knowledge.KnowledgeDocument`'s document-level attempt, not a per-range job
model like `MemoryExtractionJob` above), since a memory row's embedding
target is always "this row's current title+content."
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

# Same worst-case rationale as CLAIM_LEASE_SECONDS above: one gateway.embed()
# call, up to 2 providers * (AI_MAX_RETRIES + 1) attempts * (AI_TIMEOUT_CONNECT
# + AI_TIMEOUT_READ), PLUS the backoff sleep after every retryable failure —
# memory.tasks derives the exact figure from settings at import time and
# asserts it stays below this constant (Codex round-2 P2 on
# feat/p1-memory-embeddings: the shipped defaults alone already come out to
# ~426s, above CLAIM_LEASE_SECONDS's own 420 — a fixed constant sized only
# for the simpler "attempts * READ_TIMEOUT" approximation silently stopped
# covering the real worst case once CONNECT timeout and backoff were counted
# properly; raise this if that assertion ever fires).
EMBEDDING_CLAIM_LEASE_SECONDS = 600

# Retryable embedding failures increment retry_count; once it reaches this
# limit the row simply stops being retried automatically (never a separate
# terminal "failed" state — a Memory row with no embedding already has a
# well-defined, handled fallback: memory.context falls back to deterministic
# ordering for it).
MAX_EMBEDDING_RETRIES = 3


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
    error. Returns `False` if this attempt was fenced out (discard, no-op).

    Private-conversation guard (Phase 1 p1-work-log, Codex round-3 HIGH):
    `memory.tasks._process_claimed_job`'s own pre-gateway-call check is
    only the common-case fast path — the conversation can still turn
    private while the extraction prompt is in flight. This is the actual
    race-closing guarantee: re-checks `conversation.private` under lock, in
    the SAME transaction, immediately before any candidate is created, and
    discards `candidates` entirely if it is now true. `select_for_update(
    of=("conversation",))` locks the related row via the job's own FK
    relation without this module importing `assistant.Conversation`
    directly (`memory` must not import `assistant`, one-way import-linter
    contract) — `job` itself is already locked by `_fenced()` above.
    """
    job = _fenced(job_id, claim_token)
    if job is None:
        return False
    if (
        MemoryExtractionJob.objects.select_for_update(of=("conversation",))
        .select_related("conversation")
        .get(pk=job.pk)
        .conversation.private
    ):
        candidates = []
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


def dispatch_memory_embedding(memory_id):
    """Publish `memory_id` to the broker for a (re-)embedding attempt.
    Mirrors `dispatch_extraction_job` exactly — a broker PUBLICATION
    failure must never propagate into the caller (the memory-mutating
    request must not fail because the broker is unreachable); the row
    stays unembedded and the recovery scan picks it up later."""
    from django.conf import settings

    from memory.tasks import embed_memory

    if settings.CELERY_TASK_ALWAYS_EAGER:
        embed_memory.delay(str(memory_id))
        return
    try:
        embed_memory.delay(str(memory_id))
    except Exception:
        logger.exception("Failed to publish memory %s embedding to the broker", memory_id)


def claim_memory_for_embedding(memory_id):
    """Atomically claim `memory_id` for an embedding attempt: still
    `cloud_eligible()` (status=approved, sensitivity=normal), missing an
    embedding, under the retry budget, and (unclaimed, or claimed with an
    expired lease — takeover). Returns `(memory_id, claim_token)` on
    success, `None` if not claimable — always a safe no-op: the row may
    already be embedded, no longer eligible, over the retry budget, or held
    by a live worker.

    Returns a primitive, not the model instance: the gateway call that
    follows a successful claim runs OUTSIDE any transaction, so nothing
    here should be treated as still "held"."""
    now = timezone.now()
    with transaction.atomic():
        memory = (
            Memory.objects.select_for_update(skip_locked=True)
            .filter(
                Q(pk=memory_id)
                & Q(status=Memory.Status.APPROVED)
                & Q(sensitivity=Memory.Sensitivity.NORMAL)
                & Q(embedding__isnull=True)
                & Q(retry_count__lt=MAX_EMBEDDING_RETRIES)
                & (Q(claim_token__isnull=True) | Q(lease_expires_at__lt=now))
            )
            .first()
        )
        if memory is None:
            return None
        token = uuid.uuid4()
        memory.claim_token = token
        memory.lease_expires_at = now + timedelta(seconds=EMBEDDING_CLAIM_LEASE_SECONDS)
        # `updated_at` deliberately excluded (Codex round-1 P2): a
        # background embedding lease is not a user curation event.
        # `Memory.Meta.ordering` and `select_context_memories`'s
        # deterministic fallback both sort by `-updated_at` — bumping it
        # here would let a claim/retry/backfill attempt silently reorder a
        # memory in the user's own list or in model context.
        memory.save(update_fields=["claim_token", "lease_expires_at"])
    return memory_id, token


def _fenced_memory(memory_id, claim_token):
    """Re-lock the memory and verify this attempt still owns it AND the row
    is still `cloud_eligible()`. The eligibility re-check (not just the
    claim_token match `knowledge`/extraction's `_fenced` use) is the actual
    guard here: unlike those models, nothing about becoming ineligible
    (forget/reject/a curated edit to private) necessarily clears
    `claim_token` itself (see `memory.services._reconcile_embedding` — it
    only ever clears the stored embedding fields for an ineligible row, not
    the lease), so a claim_token-only fence would let a stale in-flight
    embed attempt write a vector onto a row that turned private or archived
    while the gateway call was in flight. Every post-claim write goes
    through this."""
    return (
        Memory.objects.select_for_update()
        .filter(
            pk=memory_id,
            claim_token=claim_token,
            status=Memory.Status.APPROVED,
            sensitivity=Memory.Sensitivity.NORMAL,
        )
        .first()
    )


@transaction.atomic
def finalize_embedding_success(memory_id, claim_token, *, vector, provider):
    """Store the embedding and clear the lease, in one fenced transaction.
    Returns `False` if this attempt was fenced out (discard, no-op — the row
    is no longer eligible, or another worker's claim superseded this one)."""
    memory = _fenced_memory(memory_id, claim_token)
    if memory is None:
        return False
    memory.embedding = vector
    memory.embedding_provider = provider
    memory.claim_token = None
    memory.lease_expires_at = None
    memory.retry_count = 0
    # `updated_at` deliberately excluded — see claim_memory_for_embedding's
    # identical note; an embedding finishing must not reorder the row.
    memory.save(
        update_fields=[
            "embedding",
            "embedding_provider",
            "claim_token",
            "lease_expires_at",
            "retry_count",
        ]
    )
    return True


@transaction.atomic
def finalize_embedding_failure(memory_id, claim_token, *, retryable):
    """A gateway failure. Retryable failures increment `retry_count`; the
    attempt whose increment reaches `MAX_EMBEDDING_RETRIES` stops the row
    from being retried automatically (embedding stays `None` — a handled,
    fallback-covered state, not a terminal error state to surface anywhere).
    A non-retryable failure jumps straight to the retry ceiling. Returns
    `False` if this attempt was fenced out."""
    memory = _fenced_memory(memory_id, claim_token)
    if memory is None:
        return False
    memory.claim_token = None
    memory.lease_expires_at = None
    memory.retry_count = memory.retry_count + 1 if retryable else MAX_EMBEDDING_RETRIES
    # `updated_at` deliberately excluded — see claim_memory_for_embedding's
    # identical note; a failed embedding attempt must not reorder the row.
    memory.save(update_fields=["claim_token", "lease_expires_at", "retry_count"])
    return True
