from django.db import transaction

from audit.services import record_audit
from core.channels import CHANNEL_WEB
from workspaces.models import Workspace, WorkspaceMember

# Role policy for the profile-only update surface (brief §2): owner/admin
# may write the firm dossier; member/viewer are read-only (mirrors
# tasks.services's _CURATOR_ROLES).
_CURATOR_ROLES = {WorkspaceMember.Role.OWNER, WorkspaceMember.Role.ADMIN}


class WorkspacePermissionDenied(Exception):
    """Actor's role does not allow the action -> 403."""

    code = "workspace.permission_denied"


def get_role(actor, workspace):
    membership = (
        WorkspaceMember.objects.for_actor(actor, workspace=workspace).filter(user=actor).first()
    )
    return membership.role if membership else None


@transaction.atomic
def ensure_personal_workspace(user):
    """Return the user's personal workspace, creating it (and the owner
    membership) if missing. Idempotent — safe to call repeatedly."""
    workspace = (
        Workspace.objects.filter(type=Workspace.Type.PERSONAL, owner=user)
        .order_by("created_at")
        .first()
    )
    if workspace is None:
        workspace = Workspace.objects.create(
            name=f"{user.name or user.email} — Personal",
            type=Workspace.Type.PERSONAL,
            owner=user,
        )
        record_audit(
            action="workspace.created",
            actor=user,
            workspace=workspace,
            resource=workspace,
            metadata={"type": workspace.type},
        )
    WorkspaceMember.objects.get_or_create(
        workspace=workspace,
        user=user,
        defaults={"role": WorkspaceMember.Role.OWNER},
    )
    return workspace


@transaction.atomic
def create_firm_workspace(*, actor, name, profile=None, channel):
    """Create a company (firm) workspace (Phase 4 branch 2). Owner-only
    (`User.is_owner`, human) and web-channel only -- `channel` is required,
    never defaulted, like the approval decision services. The actor becomes
    the workspace's owner member and nobody else is added (single-owner
    system; no invites). Audited exactly like `ensure_personal_workspace`.
    Firm deletion/archival stays out of scope (gate-class)."""
    if channel != CHANNEL_WEB:
        raise WorkspacePermissionDenied("Firm workspaces can only be created over the web.")
    if not (
        getattr(actor, "is_owner", False)
        and actor.is_active
        and not getattr(actor, "is_agent", False)
    ):
        raise WorkspacePermissionDenied("Only the installation owner can create firm workspaces.")
    workspace = Workspace.objects.create(
        name=name,
        type=Workspace.Type.COMPANY,
        owner=actor,
        profile=profile or {},
    )
    WorkspaceMember.objects.create(workspace=workspace, user=actor, role=WorkspaceMember.Role.OWNER)
    record_audit(
        action="workspace.created",
        actor=actor,
        workspace=workspace,
        resource=workspace,
        metadata={"type": workspace.type},
    )
    return workspace


@transaction.atomic
def rename_workspace(*, workspace, actor, name):
    """Rename a workspace; owner/admin only, same lock discipline as
    `update_profile`. A no-op rename writes nothing."""
    role = get_role(actor, workspace)
    if role not in _CURATOR_ROLES:
        raise WorkspacePermissionDenied("Role does not allow renaming the workspace.")
    locked = (
        Workspace.objects.filter(pk=workspace.pk, members__user=actor)
        .select_for_update(of=("self",))
        .first()
    )
    if locked is None:
        raise WorkspacePermissionDenied("Workspace no longer exists.")
    if locked.name == name:
        return locked
    locked.name = name
    locked.save(update_fields=["name", "updated_at"])
    record_audit(action="workspace.renamed", actor=actor, workspace=locked, resource=locked)
    return locked


@transaction.atomic
def update_profile(*, workspace, actor, profile):
    """Replace `workspace.profile` wholesale (not deep-merged) with an
    already-validated dossier (`WorkspaceProfileSerializer`). Owner/admin
    only; the lock query re-enforces workspace scope against `actor` itself
    (mirrors `tasks.services._locked`), so a stale or forged instance can
    never reach the write regardless of what the view already checked."""
    role = get_role(actor, workspace)
    if role not in _CURATOR_ROLES:
        raise WorkspacePermissionDenied("Role does not allow updating the firm profile.")
    locked = (
        Workspace.objects.filter(pk=workspace.pk, members__user=actor)
        .select_for_update(of=("self",))
        .first()
    )
    if locked is None:
        raise WorkspacePermissionDenied("Workspace no longer exists.")
    locked.profile = profile
    locked.save(update_fields=["profile", "updated_at"])
    # Audit payload never carries profile content (brief §2 / CLAUDE.md) --
    # only the fact that a mutation happened.
    record_audit(
        action="workspace.profile_updated",
        actor=actor,
        workspace=locked,
        resource=locked,
    )
    return locked
