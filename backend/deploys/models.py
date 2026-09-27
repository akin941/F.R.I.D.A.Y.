"""Deploy targets and deploy runs (Phase 5 branch 6, ADR-010 decision 7,
brief D10).

`DeployTarget` is the allowlist entry: an explicit, owner-created row (the
split `HarnessRepo` keeps from `profile.repos[]` --
`Workspace.profile.deploy_targets` stays dossier metadata). Nothing here is a
secret: the SSH private key is env-only (`DEPLOY_SSH_KEY_PATH`), the host
key is public and pinned.

`DeployRun` is the lease-fenced execution record of one approved `deploy`
request (mirrors `tools.models.VaultExportRun`'s claim/finalize fencing).
One active run per target, enforced by a partial unique constraint -- the
backstop behind `create_execution`'s own `target_busy` check.

String FK label for `approvals.ApprovalRequest` (the `VaultExportRun` shape),
although `deploys` may import `approvals` (ADR-010): it keeps the model
module free of the approvals app's import graph.
"""

import hashlib
import json

from django.conf import settings
from django.db import models
from django.db.models import Q

from core.db import WorkspaceScopedModel
from core.models import TimestampedModel

# The fields that define what a deploy to this target does. Any change to
# one of them changes `config_hash`, and an approved request snapshotted
# under the old hash is refused (`target_changed`) instead of running
# against a target the owner never decided on -- the lesson of
# `HarnessImplementationJob.repo_git_url`'s snapshot. `enabled` and
# `current_sha` are state, not configuration, and are checked separately.
CONFIG_HASH_FIELDS = (
    "name",
    "environment",
    "ssh_host",
    "ssh_port",
    "ssh_user",
    "host_public_key",
    "remote_app",
    "repo_git_url",
    "tracked_branch",
    "health_url",
    "health_timeout_seconds",
    "require_ci_success",
    "auto_rollback",
)


def compute_config_hash(values) -> str:
    """sha256 over the canonical JSON of `CONFIG_HASH_FIELDS` taken from a
    mapping or an object."""
    if isinstance(values, dict):
        snapshot = {field: values.get(field) for field in CONFIG_HASH_FIELDS}
    else:
        snapshot = {field: getattr(values, field) for field in CONFIG_HASH_FIELDS}
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


class DeployTarget(WorkspaceScopedModel, TimestampedModel):
    name = models.CharField(max_length=100)
    # A label ("production", "staging"), shown in the deploy dialog.
    environment = models.CharField(max_length=100)
    ssh_host = models.CharField(max_length=253)
    ssh_port = models.PositiveIntegerField(default=22)
    ssh_user = models.CharField(max_length=32)
    # Pinned `<type> <base64>`; written to a per-run known_hosts with
    # `StrictHostKeyChecking=yes` (no trust on first use).
    host_public_key = models.TextField()
    # The per-app config name on the VPS (`/etc/jarvis-deploy/apps/<app>.conf`).
    remote_app = models.CharField(max_length=63)
    repo_git_url = models.CharField(max_length=2000)
    tracked_branch = models.CharField(max_length=200, default="main")
    health_url = models.CharField(max_length=2000, blank=True, default="")
    health_timeout_seconds = models.PositiveIntegerField(default=120)
    require_ci_success = models.BooleanField(default=True)
    # ADR-010 decision 7: the bounded automatic rollback is part of the
    # decided plan only while this is on; snapshotted into every request.
    auto_rollback = models.BooleanField(default=True)
    # Off until the owner has run the first `status` and switched it on.
    enabled = models.BooleanField(default=False)
    # What Jarvis believes is live: set by a `status` refresh and by a
    # finished run. A request snapshots it as `expected_current_sha`; the
    # run's drift check compares the remote's real HEAD against that.
    current_sha = models.CharField(max_length=40, blank=True, default="")
    last_status_at = models.DateTimeField(null=True, blank=True)
    # Closed vocabulary (`DeployRun.ErrorCode` values), "" when the last
    # status refresh succeeded.
    last_status_error = models.CharField(max_length=40, blank=True, default="")
    config_hash = models.CharField(max_length=64, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="deploy_targets_created",
    )

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "name"], name="deploys_target_unique_name_per_workspace"
            ),
        ]

    def save(self, *args, **kwargs):
        self.config_hash = compute_config_hash(self)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "config_hash" not in update_fields:
            kwargs["update_fields"] = [*update_fields, "config_hash"]
        super().save(*args, **kwargs)

    @property
    def ssh_destination(self):
        suffix = "" if self.ssh_port == 22 else f":{self.ssh_port}"
        return f"{self.ssh_user}@{self.ssh_host}{suffix}"

    def __str__(self):
        return f"deploy target {self.name} ({self.environment})"


class DeployRun(WorkspaceScopedModel, TimestampedModel):
    class Status(models.TextChoices):
        # Active (at most one per target):
        PENDING = "pending"
        CHECKING = "checking"  # `status` verb + drift/self-repo checks
        DEPLOYING = "deploying"  # the `deploy` verb has been sent
        HEALTH_CHECKING = "health_checking"
        ROLLING_BACK = "rolling_back"  # `rollback` verb + its health checks
        # Terminal, after the deploy verb (ADR-010 decision 7):
        SUCCEEDED = "succeeded"
        SUCCEEDED_DEGRADED = "succeeded_degraded"
        ROLLED_BACK = "rolled_back"
        ROLLBACK_FAILED = "rollback_failed"
        HEALTH_FAILED = "health_failed"
        UNKNOWN_OUTCOME = "unknown_outcome"
        # Terminal, nothing live was changed:
        REFUSED = "refused"  # refused at approval time, nothing contacted
        FAILED = "failed"  # stopped by the runner before the deploy verb took effect
        CANCELLED = "cancelled"

    class ErrorCode(models.TextChoices):
        """Closed vocabulary: remote output and exception text never land
        in a field the UI treats as a status -- only one of these."""

        TARGET_BUSY = "target_busy"
        TARGET_CHANGED = "target_changed"
        TARGET_DISABLED = "target_disabled"
        SELF_REPO_UNCONFIGURED = "self_repo_unconfigured"
        SSH_KEY_MISSING = "ssh_key_missing"
        SSH_FAILED = "ssh_failed"
        STATUS_FAILED = "status_failed"
        STATUS_UNPARSEABLE = "status_unparseable"
        REPO_MISMATCH = "repo_mismatch"
        SELF_REPO = "self_repo"
        TARGET_DRIFTED = "target_drifted"
        DEPLOY_REFUSED = "deploy_refused"
        REMOTE_BUSY = "remote_busy"
        REMOTE_UNAVAILABLE = "remote_unavailable"
        BUILD_FAILED = "build_failed"
        DEPLOY_COMMAND_FAILED = "deploy_command_failed"
        HEALTH_URL_FAILED = "health_url_failed"
        HEALTH_CHECKS_FAILED = "health_checks_failed"
        HEALTH_WINDOW_ELAPSED = "health_window_elapsed"
        ROLLBACK_COMMAND_FAILED = "rollback_command_failed"
        ROLLBACK_OUTCOME_UNKNOWN = "rollback_outcome_unknown"
        ROLLBACK_HEALTH_FAILED = "rollback_health_failed"
        RUNNER_LOST = "runner_lost"
        UNEXPECTED_ERROR = "unexpected_error"

    ACTIVE_STATUSES = (
        Status.PENDING,
        Status.CHECKING,
        Status.DEPLOYING,
        Status.HEALTH_CHECKING,
        Status.ROLLING_BACK,
    )
    # States the lost-runner rule turns into `unknown_outcome`: the deploy
    # verb may have run. Never retried (brief D10).
    POST_DEPLOY_ACTIVE_STATUSES = (Status.DEPLOYING, Status.HEALTH_CHECKING, Status.ROLLING_BACK)

    approval_request = models.OneToOneField(
        "approvals.ApprovalRequest", on_delete=models.CASCADE, related_name="deploy_run"
    )
    target = models.ForeignKey(DeployTarget, on_delete=models.PROTECT, related_name="runs")
    sha = models.CharField(max_length=40)
    expected_current_sha = models.CharField(max_length=40)
    rollback_sha = models.CharField(max_length=40)
    # The decided payload's snapshot (target fields, config_hash, health
    # plan, auto_rollback) -- the runner acts on this, never on live target
    # fields, and refuses if the target's config_hash no longer matches it.
    plan = models.JSONField(default=dict)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    error_code = models.CharField(max_length=40, choices=ErrorCode.choices, blank=True, default="")
    claim_token = models.UUIDField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    # Set, committed, BEFORE the deploy verb is sent: from here on the run
    # can no longer be cancelled and a lost runner means `unknown_outcome`.
    deploy_started_at = models.DateTimeField(null=True, blank=True)
    rollback_started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    # [{"phase": "deploy"|"rollback", "at": iso, "verb_ok": bool,
    #   "url_ok": bool|None}] -- booleans only, never remote text.
    health_checks = models.JSONField(default=list)
    # Bounded (16 KB), redacted remote output. Low-trust text: shown as
    # data, never interpreted.
    output = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "lease_expires_at"]),
            models.Index(fields=["workspace", "status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["target"],
                condition=Q(
                    status__in=[
                        "pending",
                        "checking",
                        "deploying",
                        "health_checking",
                        "rolling_back",
                    ]
                ),
                name="deploys_one_active_run_per_target",
            ),
        ]

    @property
    def is_active(self):
        return self.status in self.ACTIVE_STATUSES

    def __str__(self):
        return f"deploy run {self.id} ({self.status})"
