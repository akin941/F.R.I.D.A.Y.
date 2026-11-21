"""The `memory.extraction` Celery task + its periodic recovery scan
(MVP 2 §4), plus the `memory.embedding` Celery task + its periodic scan
(Phase 1 `p1-memory-embeddings` — the same scan both backfills pre-existing
rows and recovers expired leases, see `recover_stale_memory_embeddings`).
Autodiscovered by `config.celery.app.autodiscover_tasks()`.
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Q
from django.utils import timezone

from ai_core.exceptions import GatewayError
from ai_core.gateway import InferenceGateway
from ai_core.models import PromptTemplate
from memory.context import neutralize_fence, strip_invisible_formatting
from memory.extraction import ExtractionParseError, parse_candidates
from memory.ingestion import tasks as _ingestion_tasks  # noqa: F401 (Phase 8A beat tasks)
from memory.jobs import (
    CLAIM_LEASE_SECONDS,
    EMBEDDING_CLAIM_LEASE_SECONDS,
    MAX_EMBEDDING_RETRIES,
    claim_extraction_job,
    claim_fact_for_embedding,
    claim_memory_for_embedding,
    clear_ineligible_fact_embeddings,
    dispatch_extraction_job,
    dispatch_fact_embedding,
    dispatch_memory_embedding,
    fact_eligible_q,
    finalize_embedding_failure,
    finalize_embedding_success,
    finalize_extraction_failure,
    finalize_extraction_success,
    finalize_fact_embedding_failure,
    finalize_fact_embedding_success,
    message_range,
)
from memory.models import Memory, MemoryExtractionJob, MemoryFact
from memory.provenance import quote_is_verbatim

logger = logging.getLogger(__name__)

# A pending job still unclaimed after this long since its last update
# suggests its original on_commit dispatch was lost (e.g. broker was down at
# commit time) — long enough to never race a normal, near-instant dispatch.
PENDING_RECOVERY_GRACE_SECONDS = 60

_EXTRACTION_PROMPT_NAME = "friday_memory_extraction"

# Gateway worst case (see memory.jobs.CLAIM_LEASE_SECONDS): 2 providers *
# up to 3 attempts * AI_TIMEOUT_READ (default 60s) ~= 360s. Soft/hard limits
# sit ABOVE that (so a legitimately slow-but-still-retrying call isn't
# hard-killed mid-flight) and strictly BELOW CLAIM_LEASE_SECONDS (so a truly
# hung task still can't straddle a takeover by another worker).
_GATEWAY_WORST_CASE_SECONDS = 360
_SOFT_TIME_LIMIT = _GATEWAY_WORST_CASE_SECONDS + 30
_TIME_LIMIT = CLAIM_LEASE_SECONDS - 10

# Derived from actual gateway settings (Codex round-2 P2 — knowledge.tasks's
# derivation, not a fixed guess like the extraction constants above): 2
# providers, each up to AI_MAX_RETRIES + 1 attempts, each up to
# AI_TIMEOUT_CONNECT + AI_TIMEOUT_READ seconds, PLUS the backoff sleep after
# every retryable failure (time.sleep(AI_RETRY_BACKOFF_SECONDS * attempt) —
# a triangular-number sum). A fixed constant would silently stop covering
# the real worst case the moment an operator raised AI_MAX_RETRIES/
# AI_TIMEOUT_READ/AI_RETRY_BACKOFF_SECONDS, letting embed_memory be
# hard-killed before finalize_embedding_failure ever clears the claim — the
# recovery scan would then just re-dispatch the same row into the same
# hard-kill loop after every lease expiry.
_EMBEDDING_GATEWAY_ATTEMPTS_PER_PROVIDER = settings.AI_MAX_RETRIES + 1
_EMBEDDING_GATEWAY_BACKOFF_WORST_CASE_SECONDS = (
    settings.AI_RETRY_BACKOFF_SECONDS
    * _EMBEDDING_GATEWAY_ATTEMPTS_PER_PROVIDER
    * (_EMBEDDING_GATEWAY_ATTEMPTS_PER_PROVIDER + 1)
    / 2
)
_EMBEDDING_GATEWAY_WORST_CASE_SECONDS = 2 * (
    _EMBEDDING_GATEWAY_ATTEMPTS_PER_PROVIDER
    * (settings.AI_TIMEOUT_CONNECT + settings.AI_TIMEOUT_READ)
    + _EMBEDDING_GATEWAY_BACKOFF_WORST_CASE_SECONDS
)
_EMBEDDING_SOFT_TIME_LIMIT = _EMBEDDING_GATEWAY_WORST_CASE_SECONDS + 30
_EMBEDDING_TIME_LIMIT = EMBEDDING_CLAIM_LEASE_SECONDS - 10
# Fail fast at import time (mirrors knowledge.tasks's identical guard): if
# this ever inverts, Celery would hard-kill (SIGKILL) the task before the
# catchable soft-limit exception fires, so finalize_embedding_failure never
# runs — the row sits claimed until the lease expires, then gets
# redispatched into the same hard-kill, looping without ever incrementing
# retry_count.
assert _EMBEDDING_SOFT_TIME_LIMIT < _EMBEDDING_TIME_LIMIT, (
    "memory.tasks._EMBEDDING_SOFT_TIME_LIMIT must stay below "
    "EMBEDDING_CLAIM_LEASE_SECONDS - 10 (memory.jobs) — raise "
    "EMBEDDING_CLAIM_LEASE_SECONDS to match the configured AI_MAX_RETRIES/"
    "AI_TIMEOUT_CONNECT/AI_TIMEOUT_READ/AI_RETRY_BACKOFF_SECONDS budget."
)


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
    if job.conversation.private:
        # Private-conversation gate, common-case fast path (Phase 1
        # p1-work-log, enforcement point (b) of 4): skip the gateway call
        # entirely for a conversation already known to be private — avoids
        # spending a prompt/gateway round-trip on content that can never
        # produce a usable candidate. finalize_extraction_success's own
        # re-check under lock (memory/jobs.py) is the actual race-closing
        # guarantee for the narrower window where the flag flips DURING
        # this call.
        finalize_extraction_success(claimed_job_id, claim_token, [])
        return
    prompt_version = _extraction_prompt_version()
    rows = list(message_range(job).values_list("role", "content"))
    messages = build_chat_extraction_messages(prompt_version.content, rows)
    user_texts = [content for role, content in rows if role == "user"]

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

    # Schema v2 (Phase 8A D9): a candidate survives only when its quote is
    # the owner's own words -- a `user`-role message of this range -- never
    # assistant text, which the model saw only inside the untrusted fence.
    candidates = [c for c in candidates if quote_is_verbatim(c["quote"], *user_texts)]
    finalize_extraction_success(claimed_job_id, claim_token, candidates)


_CHAT_FENCE_WORDS = ("untrusted_context",)


def build_chat_extraction_messages(prompt_content, rows):
    """Chat extraction v2 payload (Phase 8A D9): the system prompt, then the
    range in order -- `user` messages verbatim (the only quotable text),
    every other role's content inside an `untrusted_context` fence whose
    tag cannot be closed from inside (`neutralize_fence`)."""
    messages = [{"role": "system", "content": prompt_content}]
    for role, content in rows:
        if role == "user":
            messages.append({"role": "user", "content": content})
            continue
        fenced = neutralize_fence(strip_invisible_formatting(content or ""), *_CHAT_FENCE_WORDS)
        messages.append(
            {
                "role": role,
                "content": (f'<untrusted_context role="{role}">\n{fenced}\n</untrusted_context>'),
            }
        )
    return messages


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


@shared_task(soft_time_limit=_EMBEDDING_SOFT_TIME_LIMIT, time_limit=_EMBEDDING_TIME_LIMIT)
def embed_memory(memory_id):
    """Claim `memory_id`, embed its title+content via the gateway, and write
    the result. A safe no-op if the row is not claimable (no longer
    eligible, already embedded, retry budget exhausted, or a live worker
    holds it) — redelivery of an already-handled row is expected, not an
    error.
    """
    claimed = claim_memory_for_embedding(memory_id)
    if claimed is None:
        return
    claimed_id, claim_token = claimed

    try:
        _process_claimed_memory(claimed_id, claim_token)
    except Exception:
        # Same non-propagating-exception-text discipline as
        # run_extraction_job/process_knowledge_document: an unexpected
        # failure here still finalizes the claim (bounded by
        # MAX_EMBEDDING_RETRIES) rather than leaking a permanently-claimed
        # row that only the lease timeout would eventually free.
        logger.exception("Unexpected failure embedding memory %s", claimed_id)
        finalize_embedding_failure(claimed_id, claim_token, retryable=True)


def _process_claimed_memory(memory_id, claim_token):
    memory = Memory.objects.get(pk=memory_id)
    # Re-check eligibility right before sending anything to the gateway
    # (Codex round-1 P1): the claim happened in its own short transaction,
    # but this task's own network call can take real time — if the row
    # turned private, was forgotten, or was superseded by another claim
    # (edit_memory resets claim_token, memory.services._reconcile_embedding)
    # in that window, `_fenced_memory` at finalize time only stops the
    # VECTOR from being stored; it does not stop this plain fetch from
    # already having sent now-ineligible title/content to an external
    # provider. Bail out before building `text` at all — a no-op, not an
    # error, mirroring every other "fenced out" outcome in this module.
    #
    # Known accepted residual (Codex round-3 P1, re-flagged after the
    # round-1 fix above): this check still cannot close the race to zero.
    # Between this read-and-check completing and the `gateway.embed()` call
    # actually reaching the network, a concurrent edit could still flip the
    # row's eligibility in that (now CPU-instruction-scale, not
    # queue-duration-scale) gap. Eliminating it entirely would require
    # holding this row's lock across the gateway call itself — which
    # `claim_extraction_job`/`claim_document` explicitly do NOT do either,
    # for the same reason: a lock held across a call that can take up to
    # ~60s per attempt (memory.jobs.CLAIM_LEASE_SECONDS's own worst-case
    # math) would block every other transaction touching the row for that
    # whole duration, a strictly worse availability/contention trade than
    # the microsecond-scale window accepted here. This re-check is the
    # practical minimum: it collapses the exposure from "the entire queue
    # wait plus lease duration" (pre-round-1) down to "the last few CPU
    # instructions before the network call fires" — not zero, but as close
    # as this codebase's no-lock-across-gateway-calls design gets anywhere.
    if (
        memory.claim_token != claim_token
        or memory.status != Memory.Status.APPROVED
        or memory.sensitivity != Memory.Sensitivity.NORMAL
    ):
        return
    # Title + content concatenated — matches the cost accounting
    # memory.context.select_context_memories already uses for both fields
    # together, so either field changing invalidates the vector (see
    # memory.services._reconcile_embedding).
    text = f"{memory.title}\n\n{memory.content}"

    gateway = InferenceGateway()
    try:
        result = gateway.embed(purpose="memory.retrieval", texts=[text], workspace=memory.workspace)
    except GatewayError as exc:
        finalize_embedding_failure(memory_id, claim_token, retryable=exc.retryable)
        return

    finalize_embedding_success(
        memory_id, claim_token, vector=result.vectors[0], provider=result.usage.provider
    )


@shared_task
def recover_stale_memory_embeddings():
    """Periodic scan (Celery beat) that both backfills pre-existing
    `cloud_eligible()` rows missing an embedding (any row whose `claim_token`
    has never been set) and re-dispatches rows whose lease expired —
    re-dispatching a row a live worker still holds is safe,
    `claim_memory_for_embedding` simply no-ops if the lease hasn't actually
    expired.

    Also runnable manually via `manage.py recover_memory_embeddings` (calls
    this function directly, not `.delay()` — no broker/worker required).
    """
    now = timezone.now()
    stale_ids = list(
        Memory.objects.filter(
            status=Memory.Status.APPROVED,
            sensitivity=Memory.Sensitivity.NORMAL,
            embedding__isnull=True,
            retry_count__lt=MAX_EMBEDDING_RETRIES,
        )
        .filter(Q(claim_token__isnull=True) | Q(lease_expires_at__lt=now))
        .values_list("id", flat=True)
    )
    for memory_id in stale_ids:
        dispatch_memory_embedding(memory_id)
    return len(stale_ids)


@shared_task(soft_time_limit=_EMBEDDING_SOFT_TIME_LIMIT, time_limit=_EMBEDDING_TIME_LIMIT)
def embed_fact(fact_id):
    """Phase 8B branch 4 (D8): claim a retrievable fact, embed its
    statement (`memory.retrieval`, one `ModelUsage` row via the gateway) and
    store the vector. A safe no-op for a fact that is not claimable -- a
    Graphify `system` fact never is, so it is never sent anywhere."""
    claimed = claim_fact_for_embedding(fact_id)
    if claimed is None:
        return
    claimed_id, claim_token = claimed
    try:
        _process_claimed_fact(claimed_id, claim_token)
    except Exception:
        logger.exception("Unexpected failure embedding fact %s", claimed_id)
        finalize_fact_embedding_failure(claimed_id, claim_token, retryable=True)


def _process_claimed_fact(fact_id, claim_token):
    fact = MemoryFact.objects.filter(pk=fact_id).filter(fact_eligible_q()).first()
    # Re-check right before the gateway call (the `_process_claimed_memory`
    # rule): a fact that stopped being retrievable sends nothing.
    if fact is None or fact.claim_token != claim_token:
        return
    try:
        result = InferenceGateway().embed(
            purpose="memory.retrieval", texts=[fact.statement], workspace=fact.workspace
        )
    except GatewayError as exc:
        finalize_fact_embedding_failure(fact_id, claim_token, retryable=exc.retryable)
        return
    finalize_fact_embedding_success(
        fact_id, claim_token, vector=result.vectors[0], provider=result.usage.provider
    )


@shared_task
def recover_stale_fact_embeddings():
    """Periodic scan: backfill retrievable facts missing a vector, re-dispatch
    expired leases, and clear the vectors of facts that are no longer
    retrievable (superseded, trashed, archived, private)."""
    clear_ineligible_fact_embeddings()
    now = timezone.now()
    stale_ids = list(
        MemoryFact.objects.filter(fact_eligible_q())
        .filter(embedding__isnull=True, retry_count__lt=MAX_EMBEDDING_RETRIES)
        .filter(Q(claim_token__isnull=True) | Q(lease_expires_at__lt=now))
        .values_list("id", flat=True)
    )
    for fact_id in stale_ids:
        dispatch_fact_embedding(fact_id)
    return len(stale_ids)
