"""Shared DRF permissions (Phase 4 branch 2)."""

from rest_framework.permissions import BasePermission

from core.channels import CHANNEL_WEB, request_channel


class IsWebChannel(BasePermission):
    """Only a trusted device's web session (`core.channels.request_channel`
    == CHANNEL_WEB). Guards every web-only surface -- approval decisions,
    PAT mint/revoke, Telegram link codes, pairing codes, the remote key,
    devices, firm creation, owner deletes, draft confirmation -- on top of
    the service-level checks, so a view that later gains a low-trust
    authenticator stays closed to it."""

    message = "This action is only available from a trusted device's web session."
    code = "channel_forbidden"

    def has_permission(self, request, view):
        return request_channel(request) == CHANNEL_WEB


class IsOwner(BasePermission):
    """The installation owner (`User.is_owner`), human and active."""

    message = "Only the installation owner can do this."
    code = "owner_required"

    def has_permission(self, request, view):
        user = request.user
        return bool(
            getattr(user, "is_authenticated", False)
            and getattr(user, "is_owner", False)
            and user.is_active
            and not getattr(user, "is_agent", False)
        )
