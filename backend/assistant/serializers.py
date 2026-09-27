from rest_framework import serializers

from assistant.models import Conversation, Message


class ConversationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Conversation
        fields = ["id", "workspace", "title", "created_by", "created_at", "private"]
        read_only_fields = fields


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
    """PATCH body for the private-flag-only update endpoint (Phase 1
    p1-work-log). `private` is one-way (`False -> True` only, see
    `Conversation.private`'s field docstring) — the serializer rejects a
    `false` value outright as a validation error; the service layer
    (`mark_conversation_private`) enforces the same rule structurally by
    never accepting a value at all, so this is belt-and-suspenders, not the
    only guard.
    """

    private = serializers.BooleanField()

    def validate_private(self, value):
        if not value:
            raise serializers.ValidationError("private can only be set to true (one-way).")
        return value


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
        ]
        read_only_fields = fields


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
