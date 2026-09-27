from rest_framework import serializers

from deploys import validation
from deploys.models import DeployRun, DeployTarget
from deploys.outcomes import ATTENTION_STATUSES


def _checked(fn):
    """Adapts a `deploys.validation` checker to a DRF field validator."""

    def validate(self, value):
        try:
            return fn(value)
        except validation.TargetFieldError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    return validate


class _TargetFieldsMixin(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    environment = serializers.CharField(max_length=100)
    ssh_host = serializers.CharField(max_length=253)
    ssh_port = serializers.IntegerField(default=22)
    ssh_user = serializers.CharField(max_length=32)
    host_public_key = serializers.CharField(max_length=4000)
    remote_app = serializers.CharField(max_length=63)
    repo_git_url = serializers.CharField(max_length=2000)
    tracked_branch = serializers.CharField(max_length=200, default="main")
    health_url = serializers.CharField(
        max_length=2000, required=False, allow_blank=True, default=""
    )
    health_timeout_seconds = serializers.IntegerField(default=120)
    require_ci_success = serializers.BooleanField(default=True)
    auto_rollback = serializers.BooleanField(default=True)
    enabled = serializers.BooleanField(default=False)

    validate_name = _checked(lambda v: validation.validate_label(v, "name"))
    validate_environment = _checked(lambda v: validation.validate_label(v, "environment"))
    validate_ssh_host = _checked(validation.validate_ssh_host)
    validate_ssh_port = _checked(validation.validate_ssh_port)
    validate_ssh_user = _checked(validation.validate_ssh_user)
    validate_host_public_key = _checked(validation.normalize_host_public_key)
    validate_remote_app = _checked(validation.validate_remote_app)
    validate_repo_git_url = _checked(validation.validate_repo_git_url)
    validate_tracked_branch = _checked(validation.validate_branch)
    validate_health_url = _checked(validation.validate_health_url)
    validate_health_timeout_seconds = _checked(validation.validate_health_timeout)


class DeployTargetCreateSerializer(_TargetFieldsMixin):
    workspace = serializers.UUIDField()
    # A new target is always created disabled: the owner runs the first
    # `status`, then enables it (docs/runbooks/deploy-target-setup.md).
    enabled = None


class DeployTargetEditSerializer(_TargetFieldsMixin):
    """PATCH: every field optional, no defaults applied."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, partial=True, **kwargs)
        for field in self.fields.values():
            field.required = False
            field.default = serializers.empty

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Nothing to change.")
        return attrs


class DeployTargetSerializer(serializers.ModelSerializer):
    ssh_destination = serializers.CharField(read_only=True)
    active_run = serializers.SerializerMethodField()

    class Meta:
        model = DeployTarget
        fields = [
            "id",
            "workspace",
            "name",
            "environment",
            "ssh_host",
            "ssh_port",
            "ssh_user",
            "ssh_destination",
            "host_public_key",
            "remote_app",
            "repo_git_url",
            "tracked_branch",
            "health_url",
            "health_timeout_seconds",
            "require_ci_success",
            "auto_rollback",
            "enabled",
            "current_sha",
            "last_status_at",
            "last_status_error",
            "config_hash",
            "active_run",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_active_run(self, obj):
        run = (
            obj.runs.filter(status__in=DeployRun.ACTIVE_STATUSES)
            .values_list("id", flat=True)
            .first()
        )
        return str(run) if run else None


class DeployRunSerializer(serializers.ModelSerializer):
    target_name = serializers.CharField(source="target.name", read_only=True)
    environment = serializers.CharField(source="target.environment", read_only=True)
    needs_attention = serializers.SerializerMethodField()
    health_checks_total = serializers.SerializerMethodField()

    class Meta:
        model = DeployRun
        fields = [
            "id",
            "workspace",
            "target",
            "target_name",
            "environment",
            "approval_request",
            "sha",
            "expected_current_sha",
            "rollback_sha",
            "status",
            "error_code",
            "needs_attention",
            "deploy_started_at",
            "rollback_started_at",
            "finished_at",
            "health_checks_total",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_needs_attention(self, obj):
        return obj.status in ATTENTION_STATUSES

    def get_health_checks_total(self, obj):
        return len(obj.health_checks or [])


class DeployRunDetailSerializer(DeployRunSerializer):
    """Adds the health-check booleans and the bounded, redacted remote
    output -- low-trust text the UI shows as data (`output_trust`)."""

    output_trust = serializers.SerializerMethodField()
    auto_rollback = serializers.SerializerMethodField()
    health_plan = serializers.SerializerMethodField()

    class Meta(DeployRunSerializer.Meta):
        fields = [
            *DeployRunSerializer.Meta.fields,
            "health_checks",
            "auto_rollback",
            "health_plan",
            "output",
            "output_trust",
        ]
        read_only_fields = fields

    def get_output_trust(self, obj):
        return "low"

    def get_auto_rollback(self, obj):
        return bool((obj.plan or {}).get("auto_rollback"))

    def get_health_plan(self, obj):
        plan = (obj.plan or {}).get("health_plan") or {}
        keys = (
            "window_seconds",
            "checks",
            "interval_seconds",
            "pass_threshold",
            "fail_threshold",
            "health_url",
        )
        return {key: plan.get(key) for key in keys}


class DeployRequestCreateSerializer(serializers.Serializer):
    target = serializers.UUIDField()
    sha = serializers.CharField(max_length=64)

    def validate_sha(self, value):
        try:
            return validation.normalize_sha(value)
        except validation.TargetFieldError as exc:
            raise serializers.ValidationError(str(exc)) from exc
