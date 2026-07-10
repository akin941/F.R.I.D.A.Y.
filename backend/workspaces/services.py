from django.db import transaction

from audit.services import record_audit
from workspaces.models import Workspace, WorkspaceMember


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
