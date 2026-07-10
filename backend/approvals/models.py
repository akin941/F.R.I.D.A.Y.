"""Generic approval gate (ADR-004).

Write/delete/deploy/external-send actions go through an `ApprovalRequest`
before a tool ever runs. Postgres is the source of truth; every decision is
auditable. The requester and the decider must be different actors (ADR-004's
core premise) — enforced in `approvals.services`, not here; this model only
carries the data.
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

    # Which registered tool (tools.registry, MVP 5 branch 2) this gates.
    tool_name = models.CharField(max_length=100)
    # Opaque, tool-specific arguments. Never used to resolve scope -- a
    # tool's execution reads workspace exclusively from this row's
    # `workspace` field, never from a key inside `payload` (services.py).
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
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
