"""Connector state in Postgres (ADR-010 decision 3, brief D3/D6).

Only two things live here: whether a workspace has a connector switched on
(`ConnectorBinding`) and the at-most-once execution record of each approved
write (`ConnectorWriteExecution`). What a connector *is* -- its tools, pins,
channels, data class -- is code (`connectors/manifests/`), never a row.
Neither model holds a credential, a server URL or connector output: no
column exists that could.

String FK label for `approvals.ApprovalRequest`: `connectors` must not import
`approvals` (ADR-010 contract) -- the same shape as `tools.VaultExportRun`.
"""

from django.db import models

from core.db import WorkspaceScopedModel
from core.models import TimestampedModel


class ConnectorBinding(WorkspaceScopedModel, TimestampedModel):
    """One manifest switched on (or not) for one workspace. `enabled`
    defaults False: creating a binding row never enables anything; the
    owner enables it explicitly over the web (audited, D3).

    `last_health` is the most recent pin/health report, Friday-authored
    bookkeeping only: `checked_at`, `server_name`/`server_version`
    (server-supplied identifiers, bounded and stripped, display-only),
    `protocol_version`, `error` (closed vocabulary) and `tools` --
    `{tool_name: "ok" | "mismatch" | "missing" | "unchecked"}`. A tool is
    callable only while its status is `ok`; a failed check never turns one
    back on. Server descriptions are never stored."""

    connector = models.CharField(max_length=40)
    enabled = models.BooleanField(default=False)
    last_health = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "connector"], name="connectors_binding_unique_per_workspace"
            ),
        ]
        indexes = [models.Index(fields=["connector", "enabled"])]

    def __str__(self):
        return f"{self.connector} binding {self.id} ({'on' if self.enabled else 'off'})"

    def tool_status(self, tool_name):
        return (self.last_health or {}).get("tools", {}).get(tool_name, "unchecked")


class ConnectorWriteExecution(WorkspaceScopedModel, TimestampedModel):
    """The execution record of one approved connector write (brief D6),
    lease-fenced like `tools.VaultExportRun` -- with one deliberate
    difference: a write is **never retried**. Only a `pending` row is ever
    claimed; a `running` row whose lease expired is not taken over but moved
    to `unknown_outcome` (the worker was lost mid-call: the server may or may
    not have acted), shown to the owner and never re-sent. A new attempt is
    a new request and a new decision."""

    class Status(models.TextChoices):
        PENDING = "pending"
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"
        UNKNOWN_OUTCOME = "unknown_outcome"

    class ErrorType(models.TextChoices):
        """Closed vocabulary: never the exception's or the server's own
        text (which can carry URLs, tokens or attacker-authored content)."""

        CONNECTOR_CHANGED = "connector_changed"
        PIN_MISMATCH = "pin_mismatch"
        NOT_CONFIGURED = "not_configured"
        TIMEOUT = "timeout"
        TRANSPORT_ERROR = "transport_error"
        PROTOCOL_ERROR = "protocol_error"
        TOOL_ERROR = "tool_error"
        # A native bridge found the target gone before sending (D8: a Home
        # Assistant entity removed after the decision) -- failed closed.
        TARGET_MISSING = "target_missing"
        WORKER_LOST = "worker_lost"
        # A write's precondition (Phase 5 branch 5): the PR's remote head
        # is no longer the SHA the owner approved -- nothing was sent.
        HEAD_MOVED = "head_moved"
        UNKNOWN = "unknown"

    TERMINAL = frozenset({Status.SUCCEEDED, Status.FAILED, Status.UNKNOWN_OUTCOME})

    approval_request = models.OneToOneField(
        "approvals.ApprovalRequest",
        on_delete=models.CASCADE,
        related_name="connector_write_execution",
    )
    binding = models.ForeignKey(
        ConnectorBinding,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="write_executions",
    )
    connector = models.CharField(max_length=40)
    tool = models.CharField(max_length=40)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    claim_token = models.UUIDField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    # Set in the same transaction that claims the row, i.e. the moment the
    # one send may start; a row with `started_at` set is never sent again.
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error_type = models.CharField(max_length=30, choices=ErrorType.choices, blank=True, default="")
    # Sanitized, bounded result (text parts stripped and capped); low-trust
    # server output, never rendered as instructions and never a memory.
    result = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "lease_expires_at"]),
            models.Index(fields=["workspace", "status"]),
        ]

    def __str__(self):
        return f"{self.connector}.{self.tool} write {self.id} ({self.status})"
