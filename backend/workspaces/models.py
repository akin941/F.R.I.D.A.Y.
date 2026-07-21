from django.conf import settings
from django.db import models

from core.db import WorkspaceScopedManager
from core.models import TimestampedModel


class WorkspaceQuerySet(models.QuerySet):
    """Workspace is the scope itself, so it has its own for_actor."""

    def for_actor(self, actor, workspace=None):
        if actor is None or not getattr(actor, "is_authenticated", False):
            return self.none()
        qs = self.filter(members__user=actor)
        if workspace is not None:
            qs = qs.filter(pk=workspace.pk if hasattr(workspace, "pk") else workspace)
        return qs.distinct()


class Workspace(TimestampedModel):
    class Type(models.TextChoices):
        PERSONAL = "personal"
        COMPANY = "company"

    name = models.CharField(max_length=255)
    type = models.CharField(max_length=20, choices=Type.choices, default=Type.PERSONAL)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="owned_workspaces"
    )
    enabled_modules = models.JSONField(default=list, blank=True)
    model_policy = models.JSONField(default=dict, blank=True)
    settings = models.JSONField(default=dict, blank=True)
    # Firm dossier (Phase 0 §2): repos/services/deploy_targets/notes.
    # Schema-validated at the serializer level (workspaces.serializers).
    # `deploy_targets` is metadata only (name/host/notes) -- deploy
    # credentials/secrets must NEVER be stored here; secrets stay in env
    # per CLAUDE.md ("Secrets only via env").
    profile = models.JSONField(default=dict, blank=True)

    objects = WorkspaceQuerySet.as_manager()

    def __str__(self):
        return f"{self.name} ({self.type})"


class WorkspaceMember(TimestampedModel):
    class Role(models.TextChoices):
        OWNER = "owner"
        ADMIN = "admin"
        MEMBER = "member"
        VIEWER = "viewer"

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="workspace_memberships"
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.MEMBER)

    # WorkspaceMember carries a workspace FK, so the scoped manager applies as-is.
    objects = WorkspaceScopedManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["workspace", "user"], name="unique_workspace_member")
        ]

    def __str__(self):
        return f"{self.user} @ {self.workspace} ({self.role})"
