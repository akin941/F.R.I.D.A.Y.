"""Generic approval gate (ADR-004, amended by ADR-008 decision 3).

Write/delete/deploy/external-send actions go through an `ApprovalRequest`
before a tool ever runs. Postgres is the source of truth; every decision is
auditable. In a single-owner system (ADR-008) the gate is not a two-person
rule — it is an explicit, separate, audited owner decision: machine actors
(`origin` agent/harness) can request but never decide, and decisions are
only accepted from the high-trust web channel — enforced in
`approvals.services`, not here; this model only carries the data.
"""

from django.conf import settings
from django.db import models

from core.db import WorkspaceScopedModel
from core.models import TimestampedModel


class ApprovalRequest(WorkspaceScopedModel, TimestampedModel):
    class Status(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"
        EXPIRED = "expired"

    class Origin(models.TextChoices):
        # Who *produced* the request (ADR-008 decision 3). `requested_by`
        # stays the accountable human either way -- an agent acts on behalf
        # of the user who created it -- but origin records whether a machine
        # actor generated the request, which the audit trail preserves and
        # future policy (e.g. harness runs) can key on. Machine origins can
        # never decide: deciding requires the high-trust web channel, which
        # no machine caller has.
        HUMAN = "human"
        AGENT = "agent"
        HARNESS = "harness"

    class RequestedChannel(models.TextChoices):
        # The channel the request arrived over (Phase 4 branch 1) -- the
        # companion of `origin`: a CLI-typed request is origin=human but
        # channel=cli. `system` = machine callers (agent engine, harness
        # gates); `unknown` = rows created before this was recorded.
        WEB = "web"
        CLI = "cli"
        TELEGRAM = "telegram"
        SYSTEM = "system"
        UNKNOWN = "unknown"

    # Which registered tool (tools.registry, MVP 5 branch 2) this gates.
    tool_name = models.CharField(max_length=100)
    # Opaque, tool-specific arguments. Never used to resolve scope -- a
    # tool's execution reads workspace exclusively from this row's
    # `workspace` field, never from a key inside `payload` (services.py).
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    origin = models.CharField(max_length=20, choices=Origin.choices, default=Origin.HUMAN)
    # The model default only covers rows written outside `create_request`
    # (legacy rows, test fixtures); the service always sets it explicitly.
    requested_channel = models.CharField(
        max_length=20, choices=RequestedChannel.choices, default=RequestedChannel.UNKNOWN
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approval_requests_made",
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approval_requests_decided",
    )
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["workspace", "status"]),
        ]

    def __str__(self):
        return f"approval request {self.id} ({self.tool_name}, {self.status})"
