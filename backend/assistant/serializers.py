from rest_framework import serializers

from assistant.models import Conversation, Message


class ConversationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Conversation
        fields = ["id", "workspace", "title", "created_by", "created_at"]
        read_only_fields = fields


class ConversationCreateSerializer(serializers.Serializer):
    workspace = serializers.UUIDField()
    title = serializers.CharField(max_length=255, required=False, default="", allow_blank=True)


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
