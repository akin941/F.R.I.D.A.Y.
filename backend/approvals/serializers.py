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
    # Phase 3 branch 6: the tool's own safe, whitelisted summary of what is
    # being decided (`ToolSpec.describe_request`), or `None`. The raw
    # `payload` is still never serialized.
    display_summary = serializers.SerializerMethodField()

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
            "requested_channel",
            "decided_by",
            "decided_at",
            "created_at",
            "updated_at",
            "execution",
            "display_summary",
        ]
        read_only_fields = [
            "id",
            "workspace",
            "tool_name",
            "status",
            "requested_by",
            "requested_channel",
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

    def get_display_summary(self, obj):
        """Only for a curator (owner/admin) reading over the high-trust web
        channel -- the same audience that can decide the request (Phase 3
        branch 6 review P2: a summary can carry a sensitive repo's goal,
        file names, diff and review text, which the harness API itself
        limits to curators). Everyone else, and any serialization without a
        request in context, gets `None`."""
        from approvals.services import _CURATOR_ROLES, _resolve_tool, get_role
        from core.channels import CHANNEL_WEB, request_channel

        request = self.context.get("request")
        if request is None or request_channel(request) != CHANNEL_WEB:
            return None
        roles = self.context.setdefault("_curator_roles_by_workspace", {})
        if obj.workspace_id not in roles:
            roles[obj.workspace_id] = get_role(request.user, obj.workspace) in _CURATOR_ROLES
        if not roles[obj.workspace_id]:
            return None
        tool = _resolve_tool(obj.tool_name)
        describe = getattr(tool, "describe_request", None) if tool is not None else None
        return describe(obj) if describe is not None else None


class ApprovalRequestCreateSerializer(serializers.Serializer):
    # `status`, `requested_by`, `decided_by`, `decided_at` are
    # server-controlled (see services.create_request); `workspace` is
    # resolved through `for_actor()` before this service runs, never
    # trusted raw.
    workspace = serializers.UUIDField()
    tool_name = serializers.CharField(max_length=100)
    payload = serializers.JSONField(required=False, default=dict)
