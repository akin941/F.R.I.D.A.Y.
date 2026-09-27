from rest_framework import serializers

from tasks.models import Task


class TaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = Task
        fields = [
            "id",
            "workspace",
            "title",
            "description",
            "status",
            "priority",
            "due_date",
            "source",
            "created_by",
            "created_at",
            "updated_at",
            "deleted_at",
        ]
        read_only_fields = fields


class TaskCreateSerializer(serializers.Serializer):
    # `status`, `created_by` and `source` are all server-controlled (see
    # services.create_task); `workspace` is resolved through `for_actor()`
    # before this service runs, never trusted raw. `source` in particular is
    # never accepted directly here — a client asserting "this one's
    # ai_suggested" would defeat the entire point of the field (MVP 6 §3);
    # see `draft_message_id` below, the only path that can produce it.
    workspace = serializers.UUIDField()
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, default="", allow_blank=True)
    priority = serializers.ChoiceField(choices=Task.Priority.choices, default=Task.Priority.NORMAL)
    due_date = serializers.DateField(required=False, allow_null=True, default=None)
    # Optional REFERENCE (never a claim) to the assistant.Message this task
    # was drafted from (MVP 6 §3). The view resolves and verifies it (same
    # actor, same workspace, intent="task_draft") before deciding `source` —
    # this field only carries the id, it never itself asserts provenance.
    draft_message_id = serializers.UUIDField(required=False, allow_null=True, default=None)

    def validate_title(self, value):
        if not value.strip():
            raise serializers.ValidationError("title must not be empty.")
        return value


class TaskEditSerializer(serializers.Serializer):
    """Field edits only -- status never moves through this serializer, only
    the dedicated transition endpoints."""

    title = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    priority = serializers.ChoiceField(choices=Task.Priority.choices, required=False)
    due_date = serializers.DateField(required=False, allow_null=True)

    def validate_title(self, value):
        if not value.strip():
            raise serializers.ValidationError("title must not be empty.")
        return value
