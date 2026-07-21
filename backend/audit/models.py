import uuid

from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    """Append-only audit trail (ADR-004).

    Deliberately not a TimestampedModel: audit rows are never updated, so there
    is no `updated_at`. Immutability is enforced at the database level by a
    trigger (see migration 0002) — admin/read-only is convenience, the trigger
    is the guarantee.

    `metadata` must never contain passwords, tokens, API keys or other secrets.
    """

    class ActorType(models.TextChoices):
        HUMAN = "human"
        AGENT = "agent"
        SYSTEM = "system"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_entries",
    )
    actor_type = models.CharField(max_length=10, choices=ActorType.choices, default=ActorType.HUMAN)
    workspace = models.ForeignKey(
        "workspaces.Workspace",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_entries",
    )
    action = models.CharField(max_length=100)
    resource_type = models.CharField(max_length=100, blank=True, default="")
    resource_id = models.CharField(max_length=64, blank=True, default="")
    request_id = models.CharField(max_length=64, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["workspace", "created_at"]),
            models.Index(fields=["action", "created_at"]),
        ]

    def __str__(self):
        return f"{self.action} @ {self.created_at:%Y-%m-%d %H:%M:%S}"
