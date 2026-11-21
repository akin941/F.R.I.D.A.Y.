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
        if workspace is not None:
            # One named workspace (Phase 8B branch 4): the same membership
            # rule as an uncorrelated EXISTS instead of a join, so no
            # DISTINCT is needed (a row belongs to exactly one workspace and
            # `unique_workspace_member` holds). Rows are identical to the
            # join form; the planner can then use an ordered index scan
            # (HNSW, GIN) under the membership gate.
            # The member model is reached through this model's own
            # `workspace` FK (the `workspace__members__user` path below),
            # so `core` still imports no kernel app.
            workspace_model = self.model._meta.get_field("workspace").related_model
            member_model = workspace_model._meta.get_field("members").related_model
            return self.filter(workspace=workspace).filter(
                models.Exists(member_model._default_manager.filter(workspace=workspace, user=actor))
            )
        return self.filter(workspace__members__user=actor).distinct()


class WorkspaceScopedManager(models.Manager.from_queryset(WorkspaceScopedQuerySet)):
    """Default manager for workspace-scoped models."""


class LiveWorkspaceScopedManager(WorkspaceScopedManager):
    """Default manager for soft-deletable (trash-able) business models
    (ADR-009: knowledge documents, tasks): hides rows with `deleted_at` set,
    so every `Model.objects...for_actor()` consumer excludes trashed rows
    without having to remember to. The trash view, restore and purge go
    through the model's unfiltered `all_objects` manager explicitly."""

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


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
