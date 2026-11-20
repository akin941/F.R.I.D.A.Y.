from rest_framework import serializers

from memory.models import Memory


class MemorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Memory
        # `fingerprint` is internal dedup plumbing; not exposed.
        fields = [
            "id",
            "workspace",
            "memory_type",
            "sensitivity",
            "status",
            "source",
            "title",
            "content",
            "created_by",
            "source_conversation",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class MemoryCreateSerializer(serializers.Serializer):
    # `status`, `source`, `created_by`, `source_conversation` and the final
    # workspace object are server-controlled (see services.create_memory).
    workspace = serializers.UUIDField()
    title = serializers.CharField(max_length=255)
    content = serializers.CharField(trim_whitespace=False)
    memory_type = serializers.ChoiceField(choices=Memory.Type.choices)
    sensitivity = serializers.ChoiceField(
        choices=Memory.Sensitivity.choices, default=Memory.Sensitivity.NORMAL
    )
    # Phase 6 A6: "hatırla ki …" said by voice -> a `pending_approval`
    # proposal instead of an approved row. Can only weaken the row.
    proposal = serializers.BooleanField(default=False)

    def validate_content(self, value):
        if not value.strip():
            raise serializers.ValidationError("content must not be empty.")
        return value


class MemoryCurateSerializer(serializers.Serializer):
    """Optional curated fields for edit and approve-with-curation."""

    title = serializers.CharField(max_length=255, required=False)
    content = serializers.CharField(trim_whitespace=False, required=False)
    memory_type = serializers.ChoiceField(choices=Memory.Type.choices, required=False)
    sensitivity = serializers.ChoiceField(choices=Memory.Sensitivity.choices, required=False)

    def validate_content(self, value):
        if not value.strip():
            raise serializers.ValidationError("content must not be empty.")
        return value
