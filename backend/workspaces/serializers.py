from rest_framework import serializers

from workspaces.models import Workspace


class WorkspaceSerializer(serializers.ModelSerializer):
    role = serializers.CharField(read_only=True)

    class Meta:
        model = Workspace
        fields = ["id", "name", "type", "enabled_modules", "role", "created_at"]
        read_only_fields = fields
