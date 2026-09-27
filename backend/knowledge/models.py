"""Knowledge store (MVP 3 §2): user-supplied documents, chunked and
(eventually) embedded for RAG retrieval. Postgres is the source of truth;
documents are workspace-scoped, auditable and deletable, mirroring the trust
model `memory` (MVP 2) built.
"""

import hashlib
import re

from django.conf import settings
from django.db import models
from pgvector.django import VectorField

from core.db import WorkspaceScopedModel, WorkspaceScopedQuerySet
from core.models import TimestampedModel

_WHITESPACE = re.compile(r"\s+")


def compute_content_hash(text):
    """Cheap textual dedup key, same shape as `memory.compute_fingerprint`:
    lowercase, collapsed whitespace, sha256."""
    normalized = _WHITESPACE.sub(" ", text.strip().lower())
    return hashlib.sha256(normalized.encode()).hexdigest()


class KnowledgeDocumentQuerySet(WorkspaceScopedQuerySet):
    pass


class KnowledgeDocumentManager(models.Manager.from_queryset(KnowledgeDocumentQuerySet)):
    """Unfiltered: the trash view, restore, purge and the ingestion worker
    (which keeps processing a trashed document -- ADR-009: its chunks stay,
    excluded from retrieval, so a restore needs no re-ingestion)."""


class LiveKnowledgeDocumentManager(KnowledgeDocumentManager):
    """Default manager: hides trashed documents (ADR-009)."""

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class KnowledgeDocument(WorkspaceScopedModel, TimestampedModel):
    class SourceType(models.TextChoices):
        PASTED_TEXT = "pasted_text"
        FILE_UPLOAD = "file_upload"
        URL = "url"

    class Status(models.TextChoices):
        PENDING = "pending"
        PROCESSING = "processing"
        READY = "ready"
        FAILED = "failed"

    class Sensitivity(models.TextChoices):
        NORMAL = "normal"
        PRIVATE = "private"

    source_type = models.CharField(max_length=20, choices=SourceType.choices)
    title = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    sensitivity = models.CharField(
        max_length=20, choices=Sensitivity.choices, default=Sensitivity.NORMAL
    )
    mime_type = models.CharField(max_length=100, blank=True, default="")
    byte_size = models.PositiveIntegerField(default=0)
    # `url` source only; blank for pasted_text/file_upload.
    source_url = models.URLField(blank=True, default="", max_length=2000)
    # Both nullable: a `url` document is created before fetch succeeds, so
    # neither value exists yet at creation time. pasted_text/file_upload
    # documents populate both immediately since their content is already in
    # hand.
    # null=True is deliberate here (not the Django-conventional empty-string
    # default): null means "content not fetched yet" (a pending `url`
    # document), which is a distinct state from "fetched but empty".
    raw_text = models.TextField(null=True, blank=True)  # noqa: DJ001
    content_hash = models.CharField(  # noqa: DJ001
        max_length=64, null=True, blank=True, editable=False
    )
    error_message = models.CharField(max_length=500, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="knowledge_documents_created",
    )
    # Lease fencing fields (branch 3): one embedding/fetch attempt per
    # document, no separate job model needed (unlike memory's per-message-
    # range extraction, which genuinely needs range bookkeeping). Unused
    # until branch 3's worker.
    claim_token = models.UUIDField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    # Retryable ingestion failures (branch 3) increment this and return to
    # `pending`; the attempt that reaches MAX_INGESTION_RETRIES (knowledge.jobs)
    # transitions to `failed` instead — mirrors
    # `MemoryExtractionJob.retry_count` so a document can never stay
    # indefinitely recoverable.
    retry_count = models.PositiveIntegerField(default=0)
    # Soft delete (ADR-009): see tasks.Task.deleted_at.
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    objects = LiveKnowledgeDocumentManager()
    all_objects = KnowledgeDocumentManager()

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            # Partial index: Postgres unique indexes ignore NULL, so any
            # number of pending `url` documents (still content_hash=null) can
            # coexist in one workspace without colliding; the uniqueness
            # guarantee only kicks in once a hash actually exists. Trashed
            # documents (ADR-009) are outside it too: re-uploading content
            # whose earlier copy sits in the trash is allowed; restoring
            # that copy then refuses the duplicate instead.
            models.UniqueConstraint(
                fields=["workspace", "content_hash"],
                condition=models.Q(content_hash__isnull=False, deleted_at__isnull=True),
                name="unique_knowledge_content_hash",
            )
        ]
        indexes = [
            models.Index(fields=["workspace", "status"]),
        ]

    def __str__(self):
        return f"{self.source_type} document {self.id} ({self.status})"


class KnowledgeChunkQuerySet(WorkspaceScopedQuerySet):
    def cloud_eligible(self):
        """The only sanctioned path by which KnowledgeChunk rows may enter a
        gateway payload (mirrors `Memory.objects.cloud_eligible()`). This is
        a second, independent gate behind the ingestion-side one in
        `services.py` — a private document's chunks never reach a non-null
        `embedding` in the first place, but retrieval must never trust that
        alone.
        """
        return self.filter(
            document__sensitivity=KnowledgeDocument.Sensitivity.NORMAL,
            document__status=KnowledgeDocument.Status.READY,
            # ADR-009: a trashed document's chunks stay but never reach
            # retrieval.
            document__deleted_at__isnull=True,
            embedding__isnull=False,
        )


class KnowledgeChunkManager(models.Manager.from_queryset(KnowledgeChunkQuerySet)):
    pass


class KnowledgeChunk(WorkspaceScopedModel, TimestampedModel):
    """`workspace` is denormalized from `document.workspace` at creation time
    (same pattern `MemoryExtractionJob` uses) so `for_actor()` can filter
    chunks directly without a join through `document`.
    """

    document = models.ForeignKey(KnowledgeDocument, on_delete=models.CASCADE, related_name="chunks")
    ordinal = models.PositiveIntegerField()
    content = models.TextField()
    token_count = models.PositiveIntegerField(default=0)
    # No fixed `dimensions` kwarg (MVP 3 §1 vector dimension policy):
    # comparison operators only require the two operand vectors to match
    # each other's dimension at query time, not the column's declared
    # dimension. Retrieval (branch 4) must filter to one `embedding_provider`
    # before ever ordering by the `<=>` operator.
    embedding = VectorField(null=True, blank=True)
    embedding_provider = models.ForeignKey(
        "ai_core.ModelProvider", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    objects = KnowledgeChunkManager()

    class Meta:
        ordering = ["document", "ordinal"]
        constraints = [
            models.UniqueConstraint(
                fields=["document", "ordinal"], name="unique_knowledge_chunk_ordinal"
            )
        ]

    def __str__(self):
        return f"chunk {self.ordinal} of document {self.document_id}"
