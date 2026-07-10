from django.conf import settings
from django.db import models

from core.db import WorkspaceScopedModel
from core.models import TimestampedModel


class Conversation(WorkspaceScopedModel, TimestampedModel):
    title = models.CharField(max_length=255, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="conversations_created",
    )
    # Authoritative extraction cursor (MVP 2 §3): the most recent message a
    # MemoryExtractionJob range already covers. Advanced only under a
    # select_for_update lock on this row taken BEFORE the next assistant
    # message is created, so two concurrent persists serialize in insertion
    # order — the cursor can never move backward and job ranges can never
    # overlap. `related_name="+"` because nothing needs the reverse lookup.
    #
    # on_delete=RESTRICT (not SET_NULL, not PROTECT): a `SET_NULL` cursor
    # would silently reset to `None` if this message were ever deleted (e.g.
    # via Django admin), and the next job would then start allocating from
    # the beginning of the conversation again — overlapping every surviving
    # job's already-covered range. `PROTECT` closes that hole but is too
    # strict: it also blocks deleting the *whole* Conversation (a legitimate,
    # cascading operation — Message.conversation is CASCADE), because
    # Django's collector checks PROTECT unconditionally. `RESTRICT` gives the
    # same "no silent corruption" guarantee for a standalone message delete
    # while still permitting deletion when this row (the FK holder) is
    # itself part of the same cascading collection — i.e. when the whole
    # Conversation goes.
    last_extracted_message = models.ForeignKey(
        "assistant.Message",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="+",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["workspace", "created_at"]),
        ]

    def __str__(self):
        return self.title or f"Conversation {self.id}"


class Message(WorkspaceScopedModel, TimestampedModel):
    class Role(models.TextChoices):
        USER = "user"
        ASSISTANT = "assistant"
        SYSTEM = "system"

    class Status(models.TextChoices):
        COMPLETE = "complete"
        PARTIAL = "partial"
        FAILED = "failed"

    class Intent(models.TextChoices):
        CHAT = "chat"
        MEMORY_QUERY = "memory_query"
        KNOWLEDGE_QUERY = "knowledge_query"
        TASK_DRAFT = "task_draft"
        TOOL_REQUEST = "tool_request"

    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="messages"
    )
    role = models.CharField(max_length=20, choices=Role.choices)
    content = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.COMPLETE)
    model_usage = models.OneToOneField(
        "ai_core.ModelUsage",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assistant_message",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="messages_created",
    )
    # Ordered UUID list (strings) of memories supplied to the model for this
    # assistant reply — "memory context", not proof the model used them.
    # Immutable once written; the sanitized public face of the injection
    # (ModelUsage.context_report is never exposed to the frontend).
    memories_used = models.JSONField(default=list, blank=True)
    # Same contract as memories_used, for RAG knowledge chunks (MVP 3 §4):
    # ordered UUID list of KnowledgeChunk rows supplied as context, not proof
    # of use.
    chunks_used = models.JSONField(default=list, blank=True)
    # Classification result for this message (MVP 6 §1) — set only on
    # USER-role rows, since classification interprets the user's own
    # message, never the assistant's reply. Blank for every pre-MVP-6 row
    # and for ASSISTANT/SYSTEM rows. Immutable once written.
    intent = models.CharField(max_length=32, choices=Intent.choices, blank=True, default="")
    # Frozen classification/extraction output, shape depends on `intent`
    # (see assistant/intent.py for the classification schema). Immutable
    # once written; never holds a downstream created resource's id (a
    # Task/ApprovalRequest a confirmed draft produced) — those are visible
    # through their own existing surfaces (/tasks, /approvals), not linked
    # back to this row.
    intent_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["conversation", "created_at"]),
            models.Index(fields=["workspace", "created_at"]),
        ]

    def __str__(self):
        return f"{self.role} message {self.id}"
