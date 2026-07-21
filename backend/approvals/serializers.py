from rest_framework import serializers

from approvals.models import ApprovalRequest


class ApprovalRequestSerializer(serializers.ModelSerializer):
    # A run-status summary from the request's tool (brief §4 -- approval
    # and actual completion are two separate async moments), or `None` if
    # the tool has no `describe_execution` hook or nothing has run yet.
    # Computed, not a model field -- never listed in `read_only_fields`
    # below (a `SerializerMethodField` is already implicitly read-only;
    # DRF rejects it appearing in that option too).
    execution = serializers.SerializerMethodField()

    class Meta:
        model = ApprovalRequest
        # `payload` is deliberately NOT exposed here (brief §1): a tool's
        # arguments are not unconditionally safe to serialize back to every
        # role that can read the request list. A future tool may register a
        # `describe_payload()` safe-summary function in its `ToolSpec`; the
        # raw JSON itself is never returned.
        fields = [
            "id",
            "workspace",
            "tool_name",
            "status",
            "requested_by",
            "decided_by",
            "decided_at",
            "created_at",
            "updated_at",
            "execution",
        ]
        read_only_fields = [
            "id",
            "workspace",
            "tool_name",
            "status",
            "requested_by",
            "decided_by",
            "decided_at",
            "created_at",
            "updated_at",
        ]

    def get_execution(self, obj):
        # Local import, not module-level: mirrors `approvals.services.
        # _resolve_tool`'s own local `tools.registry` import -- tests
        # monkeypatch `approvals.services._resolve_tool` directly, which a
        # module-level `from ... import` here would miss (it would bind a
        # stale reference at import time instead of re-reading the current
        # attribute on every call).
        from approvals.services import _resolve_tool

        tool = _resolve_tool(obj.tool_name)
        describe = getattr(tool, "describe_execution", None) if tool is not None else None
        return describe(obj) if describe is not None else None


class ApprovalRequestCreateSerializer(serializers.Serializer):
    # `status`, `requested_by`, `decided_by`, `decided_at` are
    # server-controlled (see services.create_request); `workspace` is
    # resolved through `for_actor()` before this service runs, never
    # trusted raw.
    workspace = serializers.UUIDField()
    tool_name = serializers.CharField(max_length=100)
    payload = serializers.JSONField(required=False, default=dict)
