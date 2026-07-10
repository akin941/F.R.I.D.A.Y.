"""Workspace scoping primitives.

Architectural rule (ADR-001): Workspace is the data axis. Every business table
carries a workspace_id, and every queryset that reaches a view/tool MUST pass
through `for_actor()`. No endpoint or agent tool may query business data without
this scope filter. Views return 404 (not 403) for out-of-scope objects so that
existence is never leaked.
"""

from django.db import models


class WorkspaceScopedQuerySet(models.QuerySet):
    """Base queryset for models that carry a `workspace` foreign key."""

    def for_actor(self, actor, workspace=None):
        """Return only rows the actor (human or agent) may see.

        Filters by workspace membership. Agents (`user.is_agent`) get the same
        membership filter — an agent can never see more than the user context it
        runs under; future MVPs may narrow this further, never widen it.
        """
        if actor is None or not getattr(actor, "is_authenticated", False):
            return self.none()
        qs = self.filter(workspace__members__user=actor)
        if workspace is not None:
            qs = qs.filter(workspace=workspace)
        return qs.distinct()


class WorkspaceScopedManager(models.Manager.from_queryset(WorkspaceScopedQuerySet)):
    """Default manager for workspace-scoped models."""


class WorkspaceScopedModel(models.Model):
    """Abstract base for business models: workspace FK + scoped manager.

    Concrete models arriving in later MVPs (conversations, notes, files, ...)
    inherit this. Models that need a custom related_name (e.g. WorkspaceMember)
    can declare their own FK and reuse WorkspaceScopedManager directly.
    """

    workspace = models.ForeignKey(
        "workspaces.Workspace",
        on_delete=models.CASCADE,
        related_name="%(class)s_set",
    )

    objects = WorkspaceScopedManager()

    class Meta:
        abstract = True
