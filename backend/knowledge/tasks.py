"""The knowledge ingestion Celery task + its periodic recovery scan
(MVP 3 §3). Autodiscovered by `config.celery.app.autodiscover_tasks()`.
"""

import logging
import math
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from ai_core.exceptions import GatewayError
from ai_core.gateway import InferenceGateway
from knowledge.chunking import chunk_text
from knowledge.jobs import (
    CLAIM_LEASE_SECONDS,
    claim_document,
    dispatch_knowledge_document,
    finalize_ingestion_failure,
    finalize_ingestion_success,
)
from knowledge.models import KnowledgeDocument, compute_content_hash
from knowledge.services import (
    InvalidUpload,
    content_hash_exists,
    purge_expired_trashed_documents,
    reject_nul_bytes,
)
from knowledge.url_fetch import UrlFetchError, fetch_url

logger = logging.getLogger(__name__)

# A pending document still unclaimed after this long since its last update
# suggests its original on_commit dispatch was lost (e.g. broker was down at
# commit time) — long enough to never race a normal, near-instant dispatch.
PENDING_RECOVERY_GRACE_SECONDS = 60

_EMBED_BATCH_SIZE = 100

# Gateway worst case (see memory.jobs.CLAIM_LEASE_SECONDS): 2 providers,
# each up to AI_MAX_RETRIES + 1 attempts (InferenceGateway's own retry loop:
# `range(1, max_retries + 2)`), each attempt up to AI_TIMEOUT_CONNECT +
# AI_TIMEOUT_READ, PLUS the backoff sleep between attempts
# (`time.sleep(AI_RETRY_BACKOFF_SECONDS * attempt)` after every retryable
# failure) — PER embed() batch. Derived from settings, not a fixed constant
# (review finding: hardcoded defaults silently stopped matching reality the
# moment an operator tuned AI_TIMEOUT_READ/CONNECT or AI_MAX_RETRIES,
# undercounting the real worst case and risking the same premature-hard-kill
# failure mode as the batch-count finding above; a second review round found
# the backoff sleeps themselves were still missing from this sum — small
# with the shipped defaults, but they compound per BATCH, not once, so they
# can matter well before the timeouts do at a large batch count).
_GATEWAY_ATTEMPTS_PER_PROVIDER = settings.AI_MAX_RETRIES + 1
# Sum of backoff_seconds * attempt for attempt in 1..attempts_per_provider
# (a sleep follows every retryable failure, including the last attempt of a
# provider before falling back) — the triangular-number closed form.
_GATEWAY_BACKOFF_WORST_CASE_SECONDS = (
    settings.AI_RETRY_BACKOFF_SECONDS
    * _GATEWAY_ATTEMPTS_PER_PROVIDER
    * (_GATEWAY_ATTEMPTS_PER_PROVIDER + 1)
    / 2
)
_GATEWAY_WORST_CASE_SECONDS = 2 * (
    _GATEWAY_ATTEMPTS_PER_PROVIDER * (settings.AI_TIMEOUT_CONNECT + settings.AI_TIMEOUT_READ)
    + _GATEWAY_BACKOFF_WORST_CASE_SECONDS
)
# Worst case for fetching one URL: KNOWLEDGE_URL_MAX_REDIRECTS + 1 hops, each
# up to AI_TIMEOUT_CONNECT + AI_TIMEOUT_READ for the connect/header-read —
# PLUS one more AI_TIMEOUT_READ for the FINAL (200) hop's body-streaming
# wall-clock deadline (`knowledge.url_fetch._fetch_one_hop`'s explicit
# `deadline = time.monotonic() + AI_TIMEOUT_READ`), which redirect hops never
# reach since they return before any streaming. Derived from settings for
# the same reason as the gateway budget above (review finding: the
# body-streaming deadline added in a prior review round was itself missing
# from this sum — a large-but-valid URL response could hit Celery's soft
# limit despite staying within the fetcher's own timeouts).
_FETCH_WORST_CASE_SECONDS = (settings.KNOWLEDGE_URL_MAX_REDIRECTS + 1) * (
    settings.AI_TIMEOUT_CONNECT + settings.AI_TIMEOUT_READ
) + settings.AI_TIMEOUT_READ
# Worst-case batch count for a max-size document, NOT a guessed constant
# (review finding: a fixed "3 batches" budget undercounted badly — with
# defaults, a 1MB document can produce 1000+ chunks, 10+ batches at 100
# chunks/batch). `chunk_text`'s overlap is clamped to at most half the
# chunk size (knowledge.chunking), so each chunk's forward step is always
# >= chunk_size / 2 — an upper bound on chunk count is therefore
# 2 * max_bytes / chunk_size, independent of the actual configured overlap.
_max_chunks_worst_case = (
    2 * (settings.KNOWLEDGE_MAX_UPLOAD_BYTES // max(settings.KNOWLEDGE_CHUNK_SIZE, 1)) + 1
)
_MAX_EMBED_BATCHES_BUDGETED = max(1, math.ceil(_max_chunks_worst_case / _EMBED_BATCH_SIZE))
# Soft/hard limits sit ABOVE this realistic worst case (a fetch plus every
# batch a max-size document could need, not just a handful — so a
# legitimately large document isn't soft-killed mid-flight) and strictly
# BELOW CLAIM_LEASE_SECONDS (so a truly hung task still can't straddle a
# takeover by another worker).
_SOFT_TIME_LIMIT = (
    _FETCH_WORST_CASE_SECONDS + _GATEWAY_WORST_CASE_SECONDS * _MAX_EMBED_BATCHES_BUDGETED + 30
)
_TIME_LIMIT = CLAIM_LEASE_SECONDS - 10
# CLAIM_LEASE_SECONDS (knowledge.jobs) is a fixed constant, NOT derived from
# these settings-computed budgets — review finding: raising
# KNOWLEDGE_MAX_UPLOAD_BYTES or lowering KNOWLEDGE_CHUNK_SIZE without also
# raising CLAIM_LEASE_SECONDS can silently invert this ordering. If soft
# exceeds hard, Celery hard-kills (SIGKILL) the task before the catchable
# soft-limit exception ever fires, so `finalize_ingestion_failure` never
# runs — the document sits `processing` until the lease expires, then gets
# redispatched and hits the same hard-kill again, looping without ever
# incrementing `retry_count`. Fail fast at import time instead of silently
# corrupting behavior in production.
assert _SOFT_TIME_LIMIT < _TIME_LIMIT, (
    "knowledge.tasks._SOFT_TIME_LIMIT must stay below CLAIM_LEASE_SECONDS - 10 "
    "(knowledge.jobs.CLAIM_LEASE_SECONDS) — raise CLAIM_LEASE_SECONDS to match "
    "the configured KNOWLEDGE_MAX_UPLOAD_BYTES/KNOWLEDGE_CHUNK_SIZE budget."
)


@shared_task(soft_time_limit=_SOFT_TIME_LIMIT, time_limit=_TIME_LIMIT)
def process_knowledge_document(document_id):
    """Claim `document_id`, fetch (url only) + chunk (url only) + embed it
    via the gateway, and write terminal state. A safe no-op if the document
    is not claimable (already handled, or a live worker holds it) —
    redelivery of an already-`ready` document is expected, not an error.
    """
    claimed = claim_document(document_id)
    if claimed is None:
        return
    claimed_id, claim_token = claimed

    try:
        _process_claimed_document(claimed_id, claim_token)
    except Exception:
        # A claimed document must never be silently abandoned mid-flight —
        # see memory.tasks.run_extraction_job's identical comment on why the
        # raw exception text is never persisted (only logged): it could
        # carry a DB DSN, local values, or other sensitive detail.
        logger.exception("Unexpected failure processing knowledge document %s", claimed_id)
        finalize_ingestion_failure(
            claimed_id,
            claim_token,
            error_message="Unexpected error — see worker logs for details.",
            retryable=True,
        )


def _process_claimed_document(document_id, claim_token):
    document = KnowledgeDocument.all_objects.get(pk=document_id)

    content = None
    if document.source_type == KnowledgeDocument.SourceType.URL:
        try:
            content = fetch_url(document.source_url)
        except UrlFetchError as exc:
            finalize_ingestion_failure(
                document_id, claim_token, error_message=str(exc), retryable=False
            )
            return
        if not content.strip():
            finalize_ingestion_failure(
                document_id,
                claim_token,
                error_message="Fetched content was empty.",
                retryable=False,
            )
            return
        try:
            reject_nul_bytes(content)
        except InvalidUpload as exc:
            finalize_ingestion_failure(
                document_id, claim_token, error_message=str(exc), retryable=False
            )
            return
        if content_hash_exists(document.workspace, compute_content_hash(content)):
            finalize_ingestion_failure(
                document_id,
                claim_token,
                error_message="An equivalent document already exists in this workspace.",
                retryable=False,
            )
            return

    if document.sensitivity == KnowledgeDocument.Sensitivity.PRIVATE:
        # Ingestion-side private gate (§2): a private document's text must
        # never reach `gateway.embed()` — checked before any `texts` list is
        # even constructed below, not merely before the call itself.
        finalize_ingestion_success(document_id, claim_token, content=content, embeddings=None)
        return

    if content is not None:
        texts = chunk_text(content)
    else:
        # pasted_text/file_upload: already chunked synchronously in branch 2.
        texts = list(document.chunks.order_by("ordinal").values_list("content", flat=True))

    gateway = InferenceGateway()
    embeddings = []
    for start in range(0, len(texts), _EMBED_BATCH_SIZE):
        batch = texts[start : start + _EMBED_BATCH_SIZE]
        try:
            result = gateway.embed(
                purpose="knowledge.embedding", texts=batch, workspace=document.workspace
            )
        except GatewayError as exc:
            finalize_ingestion_failure(
                document_id, claim_token, error_message=str(exc), retryable=exc.retryable
            )
            return
        # Each batch may fall back to a different provider than the last —
        # pair every vector with the provider that actually produced it, not
        # a single document-level value overwritten each iteration (review
        # finding: the previous single-`provider` variable mislabeled
        # earlier batches' chunks with a LATER batch's provider whenever a
        # fallback happened mid-document, corrupting the embedding_provider-
        # filter-before-comparison invariant retrieval depends on).
        batch_provider = result.usage.provider
        embeddings.extend((vector, batch_provider) for vector in result.vectors)

    finalize_ingestion_success(document_id, claim_token, content=content, embeddings=embeddings)


@shared_task
def recover_stale_knowledge_documents():
    """Periodic scan (Celery beat, embedded via the worker's `-B` flag — see
    deploy/docker/compose.yml) for documents whose original dispatch may
    have been lost: `pending` documents stuck past the grace window, and
    `processing` documents whose lease expired. Mirrors
    `memory.tasks.recover_stale_extraction_jobs` exactly, including its
    safety property: re-dispatching a document a live worker still holds is
    safe — `claim_document` simply no-ops if the lease hasn't actually
    expired.

    Also runnable manually via `manage.py recover_knowledge_documents` (calls
    this function directly, not `.delay()` — no broker/worker required).
    """
    now = timezone.now()
    pending_grace = now - timedelta(seconds=PENDING_RECOVERY_GRACE_SECONDS)
    stale_ids = list(
        KnowledgeDocument.all_objects.filter(
            Q(status=KnowledgeDocument.Status.PENDING, updated_at__lt=pending_grace)
            | Q(status=KnowledgeDocument.Status.PROCESSING, lease_expires_at__lt=now)
        ).values_list("id", flat=True)
    )
    for document_id in stale_ids:
        dispatch_knowledge_document(document_id)
    return len(stale_ids)


@shared_task
def purge_expired_trashed_documents_task():
    """Daily beat job (config/celery.py, ADR-009): permanently delete
    documents that have sat in the trash longer than TRASH_RETENTION_DAYS."""
    return purge_expired_trashed_documents()
