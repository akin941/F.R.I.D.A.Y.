from rest_framework import serializers

from nodes.models import Node


class NodePairSerializer(serializers.Serializer):
    """`POST /api/v1/nodes/pair` body (D4)."""

    code = serializers.CharField(max_length=128, trim_whitespace=True)
    node_name = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    protocol_versions = serializers.ListField(
        child=serializers.IntegerField(min_value=1, max_value=1000),
        min_length=1,
        max_length=8,
    )
    # Phase 13 D3: optional; absent = macOS, so the existing Swift app pairs
    # unchanged. Recorded once, never edited afterwards.
    platform = serializers.ChoiceField(
        choices=Node.Platform.choices, required=False, default=Node.Platform.MACOS
    )


class NodeLabelSerializer(serializers.Serializer):
    """`PATCH /api/v1/nodes/<id>` body (Phase 13 D3): any of `name`,
    `room_id` (null clears it), `role`, `aliases`. Transport bounds only:
    the label rules are `nodes.services.relabel_node`'s. Unknown fields --
    `platform` among them -- are refused, not ignored."""

    name = serializers.CharField(
        max_length=200, trim_whitespace=False, allow_blank=True, required=False
    )
    room_id = serializers.UUIDField(allow_null=True, required=False)
    role = serializers.CharField(max_length=32, required=False)
    aliases = serializers.ListField(
        child=serializers.CharField(max_length=200, trim_whitespace=False, allow_blank=True),
        max_length=20,
        required=False,
    )

    def validate(self, attrs):
        initial = self.initial_data if isinstance(self.initial_data, dict) else {}
        unknown = set(initial) - set(self.fields)
        if unknown:
            raise serializers.ValidationError(f"Unknown fields: {', '.join(sorted(unknown))}.")
        if not attrs:
            raise serializers.ValidationError("Provide `name`, `room_id`, `role` or `aliases`.")
        return attrs


class NodeActionCreateSerializer(serializers.Serializer):
    """`POST /api/v1/nodes/<node_id>/actions` body (Phase 10 D8). The args
    are validated against the server contract by the service, not here."""

    capability = serializers.CharField(max_length=64)
    args = serializers.DictField()
    source_message_id = serializers.CharField(
        max_length=64, required=False, allow_blank=True, default=""
    )


class NodeActionExecutionSerializer(serializers.Serializer):
    """An execution's status (D8): codes, times and counts -- never args,
    never output (the tail is the terminal panel's, branch 5)."""

    id = serializers.UUIDField()
    node_id = serializers.UUIDField()
    capability = serializers.CharField()
    capability_class = serializers.CharField()
    status = serializers.CharField()
    refusal_code = serializers.CharField()
    confirm_state = serializers.CharField()
    exit_code = serializers.IntegerField(allow_null=True)
    output_bytes = serializers.IntegerField()
    truncated = serializers.BooleanField()
    approval_id = serializers.UUIDField(allow_null=True)
    created_at = serializers.DateTimeField()
    started_at = serializers.DateTimeField(allow_null=True)
    finished_at = serializers.DateTimeField(allow_null=True)
