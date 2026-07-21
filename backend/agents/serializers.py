from rest_framework import serializers

from agents.models import Agent, AgentStep


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


class AgentSerializer(serializers.ModelSerializer):
    """List/create/cancel response shape -- no nested steps (kept light for
    the list view; `AgentDetailSerializer` below adds them for the one
    agent a user is actually looking at)."""

    class Meta:
        model = Agent
        fields = [
            "id",
            "workspace",
            "goal",
            "status",
            "failure_reason",
            "created_by",
            "gateway_calls_used",
            "consecutive_failures",
            "skill_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class AgentDetailSerializer(AgentSerializer):
    steps = AgentStepSerializer(many=True, read_only=True)

    class Meta(AgentSerializer.Meta):
        fields = [*AgentSerializer.Meta.fields, "steps"]
        read_only_fields = fields


class AgentCreateSerializer(serializers.Serializer):
    # `status`/`created_by`/`failure_reason` are all server-controlled (see
    # services.start_agent/planner_service.create_agent_from_goal);
    # `workspace` is resolved through `for_actor()` before this service
    # runs, never trusted raw (mirrors TaskCreateSerializer/
    # ApprovalRequestCreateSerializer's identical shape).
    workspace = serializers.UUIDField()
    goal = serializers.CharField(max_length=2000)

    def validate_goal(self, value):
        if not value.strip():
            raise serializers.ValidationError("goal must not be empty.")
        return value
