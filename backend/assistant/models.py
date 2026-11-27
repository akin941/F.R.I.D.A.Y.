from django.conf import settings
from django.db import models

from core.db import WorkspaceScopedModel
from core.models import TimestampedModel
from core.unattended import deny_if_unattended


class MessageChannel(models.TextChoices):
    """The channel a message (and, Phase 11 D5, a conversation's opening)
    arrived over; `Message.Channel` is this class."""

    WEB = "web"
    CLI = "cli"
    TELEGRAM = "telegram"
    # The OpenAI-compatible facade (Phase 4 branch 6).
    OPENAI = "openai"
    # The node's voice child (Phase 11 D4): a live voice turn.
    NODE_VOICE = "node_voice"


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
    # Closes MASTER_PLAN §O's private-conversation-flag half (Phase 1
    # p1-work-log). One-way: False -> True only, enforced structurally by
    # `assistant.services.mark_conversation_private()` (the only writer of
    # this field — it never accepts a value, only ever sets True), not just
    # a serializer-level convention. Reversibility would reopen a skipped
    # extraction window: `last_extracted_message` advances independently of
    # this flag (memory/jobs.py's create_extraction_job), so un-privating
    # would let the next job's range re-cross the interval that was skipped
    # while private.
    private = models.BooleanField(default=False)
    # Phase 5 branch 4 (ADR-010 decision 5, brief D5): personal-class
    # connector data (mail, calendar, home, a sensitive repo) entered this
    # conversation. One-way, set only by `assistant.services.
    # mark_conversation_personal_data` -- which also marks it `private` --
    # in the turn of the read, before the completion that consumes it. From
    # then on every gateway call of this conversation is made with
    # `personal_data=True` (read fresh off this row before each call), so
    # only providers with `personal_data_allowed` can ever see it.
    personal_data = models.BooleanField(default=False)
    # Phase 11 D5: the non-web channel that opened this conversation
    # (`node_voice` for a live voice session, `openai` for the facade), ""
    # for the web, the CLI and every row older than the column. Written once
    # by `assistant.services.create_conversation(channel=...)`, never
    # updated, never writable through a serializer. The node voice channel
    # reaches only conversations it opened (404 otherwise, D4).
    opened_via = models.CharField(
        max_length=20, blank=True, default="", choices=MessageChannel.choices
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
        # Phase 5 branch 4 (brief D4): one connector read this turn.
        CONNECTOR_READ = "connector_read"
        # Phase 13b D10/D10a (ADR-018): one direct Home Assistant media call
        # this turn. Code-only: the model router can never return it
        # (`assistant.intent.CODE_ONLY_INTENTS`); only the `home.control`
        # route and the typed home step build it, with an in-process
        # provenance marker the chat view checks.
        HOME_DIRECT = "home_direct"

    Channel = MessageChannel

    class InputMode(models.TextChoices):
        # Phase 6 D9: how the owner produced this turn's text -- typed, or
        # spoken and transcribed (`POST /api/v1/voice/transcriptions`).
        TEXT = "text"
        VOICE = "voice"

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
    # Provenance for the narrow cross-workspace overlay (Phase 1
    # p1-personal-overlay, MASTER_PLAN §O): {memory_id: source_workspace_id}
    # string-to-string mapping, populated ONLY for overlay-sourced entries
    # of THIS reply's memories_used -- default {} means "no overlay
    # contribution", not "unknown". Deliberately a separate field rather
    # than redefining memories_used's own contract (an unchanged ordered
    # UUID list elsewhere in the codebase) -- a future UI surface can render
    # "from Workspace X" without any other consumer of memories_used
    # needing to change.
    overlay_source_workspaces = models.JSONField(default=dict, blank=True)
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
    # Which channel this turn arrived over (MVP 9 §1) -- stamped on BOTH the
    # user message and the assistant's reply for the same turn, resolved
    # once from core.channels.request_channel(request) and threaded through
    # the one existing call chain (assistant.views -> build_gateway_stream/
    # stream_assistant_reply -> _persist_assistant_message), never
    # independently re-derived for the reply. Immutable once written; blank
    # default for every pre-MVP-9 row reads as "web" via the field default,
    # not a distinct unknown value.
    channel = models.CharField(max_length=20, choices=Channel.choices, default=Channel.WEB)
    # Connector reads supplied as context for this assistant reply (Phase 5
    # branch 4, brief D4): `[{"tool", "source", "data_class", "size",
    # "truncated"}]` -- bookkeeping only, never the content. Same contract
    # as `memories_used`/`chunks_used`: "context supplied", not proof of use.
    connector_reads = models.JSONField(default=list, blank=True)
    # Provenance, not trust (Phase 6 D9, ADR-011/ADR-015): `voice` when the
    # user message's text came from the microphone. Client-asserted, so it
    # never widens anything -- it only ever NARROWS a turn (the ADR-015
    # capability cap, `assistant.services.apply_voice_capability_cap`) and
    # drives the mic badge. Forced to `text` for every non-web channel.
    # Stamped on the USER row only (the reply is never "spoken input").
    input_mode = models.CharField(max_length=10, choices=InputMode.choices, default=InputMode.TEXT)
    # Phase 12 D8: the wake-phrase route that answered this voice turn
    # (`digest.today`, ...), stamped on both the user message and the reply
    # of a routed turn; null for every turn the normal router handled.
    # Audit and test bookkeeping only, immutable once written.
    route = models.CharField(max_length=64, null=True, blank=True, default=None)  # noqa: DJ001
    # Phase 12 D11: on an async read's follow-up reply, the user message whose
    # turn started the read; null for every other row. Written once by
    # `assistant.followups`, never through a serializer.
    followup_of = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="followups",
    )

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["conversation", "created_at"]),
            models.Index(fields=["workspace", "created_at"]),
        ]

    def __str__(self):
        return f"{self.role} message {self.id}"


class WakeRouteAlias(WorkspaceScopedModel, TimestampedModel):
    """Phase 12 D8: an owner's own phrase for one code-defined wake route.

    Created, changed and deleted only by the workspace owner over the web
    (`assistant.services.create_wake_alias` / `update_wake_alias` /
    `delete_wake_alias`); no voice turn, tool, skill, model, connector or
    proactive run has a path here, and a write inside an unattended run is
    refused (`alias_write`). `phrase` is stored normalized
    (`assistant.wake_routes.normalize_utterance`), unique per workspace."""

    route = models.CharField(max_length=64)
    phrase = models.CharField(max_length=64)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        ordering = ["route", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "phrase"], name="assistant_wake_alias_unique_phrase"
            )
        ]
        indexes = [models.Index(fields=["workspace", "route"])]

    def save(self, *args, **kwargs):
        deny_if_unattended("alias_write")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        deny_if_unattended("alias_write")
        return super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.route}: {self.phrase}"


class PendingFollowUp(WorkspaceScopedModel, TimestampedModel):
    """Phase 12 D11: an owner's turn started an async read; when the read
    ends, one follow-up message is appended to the conversation.

    Created only by the chat turn that started the read (`assistant.
    followups.create_followup`), never by a view, tool or model. `handle_id`
    / `tool_name` are the read's opaque `tools.registry.AsyncReadHandle`
    (assistant never imports the connector that owns it). Life:
    `pending` -> `done` (one `chat.followup` completion appended) /
    `failed` (the read failed or timed out, or the completion was refused:
    a Friday template appended, no model text) / `dropped` (the
    conversation is gone: nothing appended, no model call). Every state but
    `pending` is final: a follow-up is never replayed.

    `conversation` and `source_message` are `SET_NULL` so a deleted
    conversation leaves its follow-up behind as `dropped`, never a
    cascade that would let a late result look for a new home."""

    class Status(models.TextChoices):
        PENDING = "pending"
        DONE = "done"
        FAILED = "failed"
        DROPPED = "dropped"

    TERMINAL = frozenset({Status.DONE, Status.FAILED, Status.DROPPED})

    conversation = models.ForeignKey(
        Conversation,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pending_followups",
    )
    source_message = models.ForeignKey(
        Message,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pending_followups",
    )
    followup_message = models.ForeignKey(
        Message,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    channel = models.CharField(max_length=20, choices=MessageChannel.choices)
    tool_name = models.CharField(max_length=64)
    # Friday-authored label of the read tool ("Sunucu durumu"), for the
    # template replies and the `assistant.followups_ready` notice.
    tool_label = models.CharField(max_length=60)
    source = models.CharField(max_length=40, blank=True, default="")
    data_class = models.CharField(max_length=10)
    handle_id = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    # A closed code when the follow-up is a template (`timed_out`,
    # `budget_refused`, ...), never server or exception text.
    outcome = models.CharField(max_length=40, blank=True, default="")
    # Set (atomically, `pending` and unclaimed only) by the one composer that
    # may make the follow-up's completion: a second dispatch never makes a
    # second call. A claim older than the recovery window ends `failed`.
    claimed_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["workspace", "status"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self):
        return f"follow-up {self.id} ({self.tool_name}, {self.status})"
