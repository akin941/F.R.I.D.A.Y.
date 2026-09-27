from rest_framework import serializers

from assistant.models import Conversation, Message


class ConversationSerializer(serializers.ModelSerializer):
    # The channel of the conversation's first message (web/cli/telegram/...)
    # -- the "channel badge" source (Phase 4 branch 4). Read from the
    # `origin_channel` annotation (`assistant.views.with_origin_channel`);
    # null for a conversation with no messages yet or an unannotated row.
    origin_channel = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = [
            "id",
            "workspace",
            "title",
            "created_by",
            "created_at",
            "private",
            "personal_data",
            "origin_channel",
        ]
        read_only_fields = fields

    def get_origin_channel(self, obj):
        return getattr(obj, "origin_channel", None)


class ConversationCreateSerializer(serializers.Serializer):
    workspace = serializers.UUIDField()
    title = serializers.CharField(max_length=255, required=False, default="", allow_blank=True)
    # Codex round-2 P1 on feat/p1-work-log: without this, a client wanting a
    # conversation private from message one had to create-then-PATCH,
    # leaving a race window where the first exchange could already be
    # extraction-eligible before the follow-up PATCH landed. Creation-time
    # `private` needs no cascade (nothing has been extracted from a
    # brand-new row yet) — unlike the PATCH path, this is not "one-way from
    # an existing False", it's just the row's initial value.
    private = serializers.BooleanField(required=False, default=False)


class ConversationUpdateSerializer(serializers.Serializer):
    """PATCH body for the conversation update endpoint: `title` (rename,
    Phase 4 branch 4) and/or `private` (Phase 1 p1-work-log); at least one.

    `private` is one-way (`False -> True` only, see `Conversation.private`'s
    field docstring) — the serializer rejects a `false` value outright as a
    validation error; the service layer (`mark_conversation_private`)
    enforces the same rule structurally by never accepting a value at all,
    so this is belt-and-suspenders, not the only guard. A blank `title` is
    allowed: it clears the name back to the "untitled" default.
    """

    title = serializers.CharField(max_length=255, required=False, allow_blank=True)
    private = serializers.BooleanField(required=False)

    def validate_private(self, value):
        if not value:
            raise serializers.ValidationError("private can only be set to true (one-way).")
        return value

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Provide title and/or private.")
        return attrs


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = [
            "id",
            "conversation",
            "role",
            "content",
            "status",
            "model_usage",
            "created_by",
            "created_at",
            "memories_used",
            "chunks_used",
            "overlay_source_workspaces",
            "intent",
            "intent_payload",
            "channel",
            "connector_reads",
            "confirmed_task",
        ]
        read_only_fields = fields

    # Phase 4 branch 4: the id of the task confirmed from this (task-draft)
    # message, via `Task.source_message` -- lets a reloaded chat render the
    # draft as "confirmed" instead of offering a second confirm. Read from
    # the `confirmed_task_ref` annotation the message list view adds (one
    # LEFT JOIN, no per-row query); null when unannotated or unconfirmed.
    confirmed_task = serializers.SerializerMethodField()

    def get_confirmed_task(self, obj):
        task_id = getattr(obj, "confirmed_task_ref", None)
        return str(task_id) if task_id else None


class MessageCreateSerializer(serializers.Serializer):
    # DRF's default trim_whitespace=True would silently rewrite meaningful
    # leading/trailing whitespace (e.g. indented code); reject whitespace-only
    # content explicitly instead, preserving anything else as submitted.
    content = serializers.CharField(trim_whitespace=False)
    # Optional client-local calendar date (YYYY-MM-DD), mirroring `/today`'s
    # own `localDateString()` convention (MVP 6 §3) -- the reference date a
    # `task_draft` extraction resolves relative dates ("tomorrow", "Friday")
    # against. Deliberately a plain CharField, not a DateField: a malformed
    # value here must degrade the SAME way a missing one does (extraction
    # resolves only absolute dates, leaves relative ones null) rather than
    # 400-ing the entire chat POST over a diagnostic-only field the model
    # never has to have. See assistant.task_draft for how this is consumed.
    client_local_date = serializers.CharField(
        required=False, allow_null=True, allow_blank=True, default=None
    )

    def validate_content(self, value):
        if not value.strip():
            raise serializers.ValidationError("content must not be empty.")
        return value
