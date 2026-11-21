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
