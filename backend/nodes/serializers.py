from rest_framework import serializers


class NodePairSerializer(serializers.Serializer):
    """`POST /api/v1/nodes/pair` body (D4)."""

    code = serializers.CharField(max_length=128, trim_whitespace=True)
    node_name = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    protocol_versions = serializers.ListField(
        child=serializers.IntegerField(min_value=1, max_value=1000),
        min_length=1,
        max_length=8,
    )


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
