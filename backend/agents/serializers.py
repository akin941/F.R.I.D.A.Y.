from rest_framework import serializers

from agents.models import AgentStep


class AgentStepSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentStep
        fields = [
            "id",
            "agent",
            "order",
            "kind",
            "description",
            "status",
            "payload",
            "result",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields
