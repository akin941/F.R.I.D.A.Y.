"""`VaultExportRun` -- the lease-fenced execution record for the
`vault_export` tool (MVP 5 branch 3, brief §3). One row per approved
`ApprovalRequest`; mirrors `MemoryExtractionJob`/`KnowledgeDocument`'s
claim/finalize fencing exactly (`claim_token`/`lease_expires_at`,
`select_for_update(skip_locked=True)` claim, re-lock-and-verify-token before
every post-claim write).

String FK label (`approvals.ApprovalRequest`): `tools` must not import
`approvals` (one-way dependency, see pyproject import-linter contract) --
same shape as `memory.Memory.source_conversation` -> `assistant.Conversation`.
"""

from django.db import models

from core.db import WorkspaceScopedModel
from core.models import TimestampedModel


class VaultExportRun(WorkspaceScopedModel, TimestampedModel):
    class Status(models.TextChoices):
        PENDING = "pending"
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"

    class ErrorType(models.TextChoices):
        """Closed vocabulary (brief §3): filesystem exceptions routinely
        embed absolute paths and the host username in their message text --
        persisting one verbatim would leak `OBSIDIAN_VAULT_PATH`'s actual
        value. Only one of these four values, plus a fixed per-type
        message, is ever stored -- never the underlying exception's own
        text."""

        PATH_VALIDATION_FAILED = "path_validation_failed"
        PERMISSION_DENIED = "permission_denied"
        DISK_FULL = "disk_full"
        UNKNOWN = "unknown"

    approval_request = models.OneToOneField(
        "approvals.ApprovalRequest", on_delete=models.CASCADE, related_name="vault_export_run"
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    retry_count = models.PositiveIntegerField(default=0)
    claim_token = models.UUIDField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    error_type = models.CharField(max_length=30, choices=ErrorType.choices, blank=True, default="")
    error_message = models.CharField(max_length=500, blank=True, default="")
    written_count = models.PositiveIntegerField(default=0)
    updated_count = models.PositiveIntegerField(default=0)
    removed_count = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(fields=["status", "lease_expires_at"]),
            models.Index(fields=["workspace", "status"]),
        ]

    def __str__(self):
        return f"vault export run {self.id} ({self.status})"
