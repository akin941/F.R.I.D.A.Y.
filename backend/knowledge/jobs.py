"""Knowledge document dispatch, claiming and finalization (MVP 3 §3).

Mirrors `memory.jobs` exactly: lease-fenced claim/finalize, `on_commit`
dispatch, non-propagating broker-publish failure. One embedding/fetch
attempt per document, not one per message-range like memory's extraction
jobs — no separate job model is needed, since `KnowledgeDocument` already
carries its own `claim_token`/`lease_expires_at`/`status`/`retry_count`.
"""

import logging
import uuid
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from knowledge.chunking import chunk_text, estimate_token_count
from knowledge.models import KnowledgeChunk, KnowledgeDocument, compute_content_hash

logger = logging.getLogger(__name__)

# A claim is valid this long before another worker may take it over. Must
# comfortably exceed `knowledge.tasks._SOFT_TIME_LIMIT` (this task's own
# worst case: an optional URL fetch plus EVERY `gateway.embed()` batch a
# max-size document could need, computed from actual settings — including
# AI_TIMEOUT_CONNECT/READ, AI_MAX_RETRIES and KNOWLEDGE_URL_MAX_REDIRECTS,
# not just the batch count — see that module for the exact arithmetic; with
# the shipped defaults this comes out to ~9100s). Generous relative to
# `memory.jobs.CLAIM_LEASE_SECONDS` (420s, one gateway call) since this task
# can do meaningfully more work. `knowledge.tasks` asserts at import time
# that this stays above its computed soft limit — if that assertion ever
# fires, raise this constant to match.
CLAIM_LEASE_SECONDS = 9400

# Retryable failures increment retry_count and return the document to
# `pending` until retry_count reaches this limit; the attempt that reaches it
# transitions to `failed` instead — a document can never stay indefinitely
# recoverable.
MAX_INGESTION_RETRIES = 3


def dispatch_knowledge_document(document_id):
    """Publish `document_id` to the broker. Mirrors
    `memory.jobs.dispatch_extraction_job` exactly: a broker PUBLICATION
    failure must never propagate into the caller (the creating request must
    not fail because the broker is unreachable) — the document stays
    `pending`; the recovery scan re-dispatches it later. Must NOT swallow the
    task's own exceptions; see that function's docstring for why the eager-
    mode branch is separate.
    """
    from django.conf import settings

    from knowledge.tasks import process_knowledge_document

    if settings.CELERY_TASK_ALWAYS_EAGER:
        process_knowledge_document.delay(str(document_id))
        return
    try:
        process_knowledge_document.delay(str(document_id))
    except Exception:
        logger.exception("Failed to publish knowledge document %s to the broker", document_id)


def claim_document(document_id):
    """Atomically claim `document_id`: `pending`, or `processing` with an
    expired lease (takeover). Returns `(document_id, claim_token)` on
    success, `None` if not claimable — a safe no-op (redelivery of an
    already-`ready`/`failed` document, or one a live worker still holds, is
    expected, not an error).

    Returns a primitive, not the model instance: the fetch/embed work that
    follows a successful claim runs OUTSIDE any transaction (it can take
    seconds to minutes), so nothing here should be treated as still "held".
    """
    now = timezone.now()
    with transaction.atomic():
        document = (
            KnowledgeDocument.objects.select_for_update(skip_locked=True)
            .filter(
                Q(pk=document_id)
                & (
                    Q(status=KnowledgeDocument.Status.PENDING)
                    | Q(status=KnowledgeDocument.Status.PROCESSING, lease_expires_at__lt=now)
                )
            )
            .first()
        )
        if document is None:
            return None
        token = uuid.uuid4()
        document.status = KnowledgeDocument.Status.PROCESSING
        document.claim_token = token
        document.lease_expires_at = now + timedelta(seconds=CLAIM_LEASE_SECONDS)
        document.save(update_fields=["status", "claim_token", "lease_expires_at", "updated_at"])
    return document_id, token


def _fenced(document_id, claim_token):
    """Re-lock the document and verify this attempt still owns it. Mirrors
    `memory.jobs._fenced` — a worker that outlived its lease discards
    whatever it was about to write, success or failure alike (any
    `ModelUsage` rows from `gateway.embed()` calls it already made stand as
    the cost record of the wasted attempt, the one intentional exception to
    this fencing, same as memory)."""
    return (
        KnowledgeDocument.objects.select_for_update()
        .filter(
            pk=document_id,
            status=KnowledgeDocument.Status.PROCESSING,
            claim_token=claim_token,
        )
        .first()
    )


@transaction.atomic
def finalize_ingestion_success(document_id, claim_token, *, content=None, embeddings=None):
    """Mark the document `ready`, in one fenced transaction. Returns `False`
    if this attempt was fenced out (discard, no-op).

    `content` (only for a successfully-fetched `url` document; `None` for
    `pasted_text`/`file_upload`, whose content already exists) populates
    `raw_text`/`content_hash`/`byte_size`/`mime_type` and creates the
    `KnowledgeChunk` rows HERE, inside the fence — not right after the fetch
    — so a worker that gets fenced out never leaves an orphaned or duplicate
    chunk set behind; the whole batch (chunk creation and embedding writes
    alike) commits or discards together.

    `embeddings` (list[tuple[list[float], ModelProvider]], ordinal-aligned
    with the document's chunks, or `None`) is the (vector, provider) pairs to
    write — one pair per chunk, each tagged with the provider that actually
    produced ITS vector, not a single document-level provider. A batched
    `gateway.embed()` call can fall back to a different provider than an
    earlier batch; storing per-chunk provenance here is what lets retrieval's
    filter-by-provider-before-comparing-vectors invariant hold even when one
    document's chunks span more than one provider. `None` covers two distinct
    cases that both skip the embed step entirely: a private document (§2's
    ingestion-side gate — `gateway.embed()` was never called) and an empty
    chunk set."""
    document = _fenced(document_id, claim_token)
    if document is None:
        return False

    if content is not None:
        document.raw_text = content
        document.content_hash = compute_content_hash(content)
        document.byte_size = len(content.encode())
        document.mime_type = "text/plain"
        pieces = chunk_text(content)
        chunk_list = [
            KnowledgeChunk(
                workspace=document.workspace,
                document=document,
                ordinal=index,
                content=piece,
                token_count=estimate_token_count(piece),
            )
            for index, piece in enumerate(pieces)
        ]
        KnowledgeChunk.objects.bulk_create(chunk_list)
    else:
        chunk_list = list(document.chunks.order_by("ordinal"))

    if embeddings is not None:
        if len(embeddings) != len(chunk_list):
            # Invariant guarded by the caller (knowledge.tasks): every
            # `gateway.embed()` batch is built from exactly these chunks'
            # content, in this same order. A mismatch here means a caller
            # bug, not a runtime condition to recover from.
            raise ValueError("embeddings length must match chunk count")
        for chunk, (vector, provider) in zip(chunk_list, embeddings, strict=True):
            chunk.embedding = vector
            chunk.embedding_provider = provider
        if chunk_list:
            KnowledgeChunk.objects.bulk_update(chunk_list, ["embedding", "embedding_provider"])

    document.status = KnowledgeDocument.Status.READY
    document.claim_token = None
    document.lease_expires_at = None
    document.error_message = ""
    update_fields = ["status", "claim_token", "lease_expires_at", "error_message", "updated_at"]
    if content is not None:
        update_fields += ["raw_text", "content_hash", "byte_size", "mime_type"]
    document.save(update_fields=update_fields)
    return True


@transaction.atomic
def finalize_ingestion_failure(document_id, claim_token, *, error_message, retryable):
    """A fetch failure (always terminal — pass `retryable=False`) or a
    gateway failure (retryable per `GatewayError.retryable`). Retryable
    failures increment `retry_count` on every attempt and return to `pending`
    while it remains below `MAX_INGESTION_RETRIES`; the attempt whose
    increment REACHES the limit transitions to `failed` instead (so the
    final `retry_count` on a failed document equals `MAX_INGESTION_RETRIES`
    exactly) — mirrors `memory.jobs.finalize_extraction_failure`. Returns
    `False` if this attempt was fenced out."""
    document = _fenced(document_id, claim_token)
    if document is None:
        return False
    if retryable:
        document.retry_count += 1
        document.status = (
            KnowledgeDocument.Status.PENDING
            if document.retry_count < MAX_INGESTION_RETRIES
            else KnowledgeDocument.Status.FAILED
        )
    else:
        document.status = KnowledgeDocument.Status.FAILED
    document.claim_token = None
    document.lease_expires_at = None
    document.error_message = error_message[:500]
    document.save(
        update_fields=[
            "status",
            "retry_count",
            "claim_token",
            "lease_expires_at",
            "error_message",
            "updated_at",
        ]
    )
    return True
