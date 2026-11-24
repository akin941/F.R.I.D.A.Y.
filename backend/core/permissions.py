"""Shared DRF permissions (Phase 4 branch 2)."""

from rest_framework.permissions import BasePermission

from core.channels import CHANNEL_NODE, CHANNEL_NODE_VOICE, CHANNEL_WEB, request_channel


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


class IsNodeChannel(BasePermission):
    """Only a Friday Node credential (`request_channel` == CHANNEL_NODE,
    Phase 9 D1). Guards the node's own surfaces, so a web session (even a
    force-authenticated one) or a PAT never reaches them."""

    message = "This endpoint only accepts a Friday Node credential."
    code = "channel_forbidden"

    def has_permission(self, request, view):
        return request_channel(request) == CHANNEL_NODE


class IsWebOrNodeVoiceChannel(BasePermission):
    """The owner's web session or the node voice credential (Phase 11 D4):
    the three voice endpoints. Every other low-trust channel (a `cli` PAT,
    the facade, a hook token, a node secret) keeps the deliberate 403."""

    message = "This endpoint only accepts a web session or the node voice credential."
    code = "channel_forbidden"

    def has_permission(self, request, view):
        return request_channel(request) in (CHANNEL_WEB, CHANNEL_NODE_VOICE)


class IsNodeVoiceChannel(BasePermission):
    """Only the node voice credential (`request_channel` ==
    CHANNEL_NODE_VOICE, Phase 11 D4/D5): the live-sessions endpoint and the
    voice-policy read, which exist for the node's voice child alone."""

    message = "This endpoint only accepts the node voice credential."
    code = "channel_forbidden"

    def has_permission(self, request, view):
        return request_channel(request) == CHANNEL_NODE_VOICE


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
