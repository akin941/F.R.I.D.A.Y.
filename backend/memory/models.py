"""Curated memory store (ADR-005).

Postgres is the source of truth; memories are workspace-scoped, auditable,
editable, forgettable and exportable. AI-suggested memories are born
`pending_approval` and only reach model context after human approval. The
one deliberately narrow exception (ADR-008 k.4, Phase 1 p1-work-log):
`source=system_generated` rows are born `approved` — machine-authored
activity summaries, not conversation-derived content a human hasn't
reviewed. See `Source.SYSTEM_GENERATED` and
`memory.services.record_work_log()`, its only creation path.
Scope (personal vs company) is carried by the workspace row itself — a
personal workspace's memories are personal memories; there is no separate
scope field.
"""

import hashlib
import re

from django.conf import settings
from django.db import models
from pgvector.django import VectorField

from core.db import WorkspaceScopedModel, WorkspaceScopedQuerySet
from core.models import TimestampedModel

_WHITESPACE = re.compile(r"\s+")


def compute_fingerprint(content: str) -> str:
    """Cheap textual dedup key: lowercase, collapsed whitespace, sha256.

    No embeddings — semantic dedup is MVP 3. The fingerprint is unique per
    workspace across *all* rows (including rejected ones, so a rejected
    suggestion cannot immediately resurface); the slot frees only on hard
    delete.
    """
    normalized = _WHITESPACE.sub(" ", content.strip().lower())
    return hashlib.sha256(normalized.encode()).hexdigest()


class MemoryQuerySet(WorkspaceScopedQuerySet):
    def cloud_eligible(self):
        """The only sanctioned path by which Memory rows may enter a gateway
        payload (ADR-005: `private` records never reach cloud models).
        Private title/content must never be read out of any other queryset
        into model context, extraction prompts, context reports or
        `memories_used`.
        """
        return self.filter(status=Memory.Status.APPROVED, sensitivity=Memory.Sensitivity.NORMAL)


class MemoryManager(models.Manager.from_queryset(MemoryQuerySet)):
    pass


class Memory(WorkspaceScopedModel, TimestampedModel):
    class Type(models.TextChoices):
        SEMANTIC = "semantic"
        EPISODIC = "episodic"
        PREFERENCE = "preference"
        PROCEDURAL = "procedural"
        # System-generated activity summaries (ADR-008 k.4, Phase 1
        # p1-work-log) — auto-approved ONLY when paired with
        # `source=system_generated` (see `Source` below and
        # `memory.services.record_work_log()`, the sole creation path for
        # that pairing). A conversation-derived work_log candidate carries
        # this same type but `source=ai_suggested`, and stays
        # `pending_approval` like any other extraction candidate.
        WORK_LOG = "work_log"

    class Sensitivity(models.TextChoices):
        NORMAL = "normal"
        PRIVATE = "private"

    class Status(models.TextChoices):
        PENDING_APPROVAL = "pending_approval"
        APPROVED = "approved"
        REJECTED = "rejected"
        ARCHIVED = "archived"

    class Source(models.TextChoices):
        AI_SUGGESTED = "ai_suggested"
        USER_CREATED = "user_created"
        # Machine-provenance rows (Phase 1 p1-work-log, ADR-008 k.4) — only
        # `memory.services.record_work_log()` ever writes this value, and it
        # always pairs it with `type=work_log` and `status=approved`. This
        # is the ONLY auto-approved provenance; no other Source value may
        # ever be born approved.
        SYSTEM_GENERATED = "system_generated"

    memory_type = models.CharField(max_length=20, choices=Type.choices)
    sensitivity = models.CharField(
        max_length=20, choices=Sensitivity.choices, default=Sensitivity.NORMAL
    )
    status = models.CharField(max_length=20, choices=Status.choices)
    source = models.CharField(max_length=20, choices=Source.choices)
    title = models.CharField(max_length=255)
    content = models.TextField()
    fingerprint = models.CharField(max_length=64, editable=False)
    # Provenance, not an ACL owner. It feeds exactly one authorization
    # predicate (members manage `source=user_created AND created_by=actor`
    # rows); AI-suggested rows are admin-managed at every stage.
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="memories_created",
    )
    # String label: no Python import of assistant (dependency direction is
    # assistant → memory once context injection lands).
    source_conversation = models.ForeignKey(
        "assistant.Conversation",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="memories_extracted",
    )
    # Durable marker (Phase 1 p1-work-log, Codex round-3 P2): set once,
    # permanently, by memory.services.privatize_conversation_memories()
    # when it archives an approved memory as part of the private-
    # conversation cascade. `restore_memory()` checks THIS, not
    # `source_conversation.private` — the FK above is nullable
    # (on_delete=SET_NULL), so deleting the source conversation would
    # silently defeat an FK-based check and let the row be restored back
    # into cloud_eligible(), undoing the privacy decision. Never cleared
    # once set; there is no path back from it.
    privacy_locked = models.BooleanField(default=False)
    # Semantic retrieval (Phase 1 branch p1-memory-embeddings). No fixed
    # `dimensions` kwarg (MVP 3 §1 vector dimension policy, mirrored from
    # `knowledge.KnowledgeChunk.embedding`) — comparison operators only
    # require the two operand vectors to match each other's dimension at
    # query time, not the column's declared dimension. Eligibility is
    # `cloud_eligible()` only (status=approved, sensitivity=normal); a
    # private or unapproved row's embedding is always `None` — see
    # `memory.services`'s embedding-lifecycle reconciliation, the only
    # sanctioned writer of these five fields.
    embedding = VectorField(null=True, blank=True)
    embedding_provider = models.ForeignKey(
        "ai_core.ModelProvider", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    # Lease-fencing: one embedding attempt in flight at a time per row (not
    # embed-exactly-once — a row can be legitimately re-embedded many times
    # over its life, see memory.services). Same field shape as
    # `knowledge.KnowledgeDocument`'s (document-level attempt, not a
    # separate per-range job model like `MemoryExtractionJob` — a memory
    # row's embedding target is always "this row's current title+content").
    claim_token = models.UUIDField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    # Retryable embedding failures increment this; once it reaches
    # memory.jobs.MAX_EMBEDDING_RETRIES the row simply stays unembedded
    # (retrieval already falls back to deterministic ordering for a row
    # with no embedding — there is no separate terminal "failed" state to
    # reach, unlike KnowledgeDocument.status).
    retry_count = models.PositiveIntegerField(default=0)

    objects = MemoryManager()

    def save(self, *args, **kwargs):
        # Same-workspace provenance is an invariant, not a per-caller courtesy:
        # a memory may only reference a conversation in its own workspace, or a
        # cross-workspace conversation UUID would leak to this workspace's
        # readers. Enforced on every write path (user API today, extraction
        # pipeline in later branches).
        if (
            self.source_conversation_id is not None
            and self.source_conversation.workspace_id != self.workspace_id
        ):
            raise ValueError("source_conversation must belong to the memory's workspace.")
        super().save(*args, **kwargs)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "fingerprint"], name="unique_memory_fingerprint"
            )
        ]
        indexes = [
            models.Index(fields=["workspace", "status"]),
            models.Index(fields=["workspace", "memory_type"]),
        ]
        verbose_name_plural = "memories"

    def __str__(self):
        return f"{self.memory_type} memory {self.id} ({self.status})"


class MemoryExtractionJob(WorkspaceScopedModel, TimestampedModel):
    """One distillation attempt over a contiguous, non-overlapping message
    range of a conversation (MVP 2 §3-4).

    Range allocation (branch 3, this model) and claiming/execution (branch 4)
    are deliberately split: this branch creates jobs as inert `pending` rows
    with no dispatch wired up yet, so merged `main` never publishes an
    unregistered Celery task. `claim_token`/`lease_expires_at` exist now so
    the branch-4 migration doesn't need a second schema change, but nothing
    in this branch reads or writes them.

    String FK labels (`assistant.Conversation`/`assistant.Message`): memory
    must not import assistant (one-way dependency, see pyproject contract).
    """

    class Status(models.TextChoices):
        PENDING = "pending"
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"

    conversation = models.ForeignKey(
        "assistant.Conversation", on_delete=models.CASCADE, related_name="extraction_jobs"
    )
    # Exclusive lower bound of the range (the prior cursor position); null
    # for a conversation's first job. on_delete=RESTRICT (see
    # Conversation.last_extracted_message for the full rationale): extraction
    # job history is meant to be as immutable as the audit log — a standalone
    # message delete must never silently widen or corrupt an
    # already-allocated range — but deleting the whole Conversation (which
    # cascades to this job via `conversation`) must still work.
    after_message = models.ForeignKey(
        "assistant.Message",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="+",
    )
    # Inclusive upper bound AND the job's target — there is no separate
    # "target_message" field, the range end IS the target.
    through_message = models.ForeignKey(
        "assistant.Message", on_delete=models.RESTRICT, related_name="+"
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    retry_count = models.PositiveIntegerField(default=0)
    # Lease fencing fields (branch 4): a per-attempt token + expiry so a
    # worker that outlives its lease can be safely superseded rather than
    # allowed to write stale results.
    claim_token = models.UUIDField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    error_type = models.CharField(max_length=50, blank=True, default="")
    error_message = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "through_message"],
                name="unique_extraction_job_range_end",
            )
        ]
        indexes = [
            models.Index(fields=["workspace", "status"]),
        ]

    def __str__(self):
        return f"extraction job {self.id} ({self.status})"
