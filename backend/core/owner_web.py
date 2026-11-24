"""The shared owner-over-the-web check (Phase 12 D3).

`require_owner_web(actor=, workspace=, channel=)`: the decision is made by
the installation owner, a person, over the high-trust web channel, in a
workspace they own. Used by `proactive` (schedules, pause), and later by
`skills` and the wake-route alias services, none of which may import
`memory` for 8A's own helper (`memory.ingestion.digest.require_owner_web`
stays where it is).

Pure: it compares the channel string, the actor's attributes and the
workspace's owner id. It imports nothing outside `core` and the standard
library, so the "core must stay independent of kernel apps" contract holds.
"""

from core.channels import CHANNEL_WEB


class OwnerWebRequired(PermissionError):
    """Refused: not the owner, not a person, not the web channel, or not
    the owner of this workspace. `code` is a closed token."""

    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def require_owner_web(*, actor, workspace, channel) -> None:
    if channel != CHANNEL_WEB:
        raise OwnerWebRequired("channel_forbidden", "Only the owner's web session can do this.")
    if actor is None or not getattr(actor, "is_authenticated", False):
        raise OwnerWebRequired("owner_required", "Only the owner can do this.")
    if getattr(actor, "is_agent", False):
        raise OwnerWebRequired("agent_forbidden", "An agent can never do this.")
    if not getattr(actor, "is_active", False) or not getattr(actor, "is_owner", False):
        raise OwnerWebRequired("owner_required", "Only the owner can do this.")
    owner_id = getattr(workspace, "owner_id", None)
    if workspace is None or owner_id is None or str(owner_id) != str(actor.pk):
        raise OwnerWebRequired("owner_required", "Only the workspace owner can do this.")
