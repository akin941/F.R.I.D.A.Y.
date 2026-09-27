from django.db import transaction

from audit.services import record_audit
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
