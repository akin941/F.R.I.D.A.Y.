from django.db import transaction

from accounts.models import User
from audit.services import record_audit
from workspaces.services import ensure_personal_workspace


@transaction.atomic
def register_user(*, email, password, name=""):
    """Create a user together with their personal workspace.

    The single entry point for user creation in application flows (signup now,
    invites later). Explicit service call instead of signals, so the flow stays
    testable and visible.
    """
    user = User.objects.create_user(email=email, password=password, name=name)
    workspace = ensure_personal_workspace(user)
    record_audit(action="user.signup", actor=user, workspace=workspace, resource=user)
    return user
