"""Channel-trust primitive (MVP 9 §1).

Lives in `core`, not `integrations`: `approvals` needs to ask "did this
request arrive over a low-trust channel?" without importing `integrations`,
and every kernel app may already depend on `core` (one-way, per the
"core must stay independent of kernel apps" contract) -- so this keeps
`integrations` and `approvals` mutually unaware of each other.
"""

from dataclasses import dataclass

CHANNEL_WEB = "web"  # default: password-login JWT, trusted
CHANNEL_CLI = "cli"  # low-trust: bearer PAT
CHANNEL_TELEGRAM = "telegram"  # low-trust: relayed, no direct request at all


@dataclass(frozen=True)
class ChannelPrincipal:
    """Set as `request.auth` by a low-trust authenticator (accounts'
    `PersonalAccessTokenAuthentication`). Absence of this (e.g. a plain
    JWTAuthentication auth object) means the request is CHANNEL_WEB --
    default-trusted, not default-denied, since every pre-MVP-9
    authenticator keeps working unmodified.

    `channel` is CHANNEL_CLI today; CHANNEL_TELEGRAM never appears here --
    Telegram has no direct request/response cycle with our API at all (it's
    relayed via a webhook + Celery task), so there is no request to
    authenticate as Telegram in the first place.
    """

    channel: str
    bound_workspace_id: str


def request_channel(request) -> str:
    auth = getattr(request, "auth", None)
    return auth.channel if isinstance(auth, ChannelPrincipal) else CHANNEL_WEB


def is_low_trust(request) -> bool:
    return request_channel(request) != CHANNEL_WEB


def scoped_for_actor(request, queryset, workspace=None):
    """Drop-in replacement for `<Model>.objects.for_actor(request.user,
    workspace=...)` in every view allowlisted for PAT auth (MVP 9 §2).

    For a CHANNEL_WEB request, behavior is unchanged (whatever `workspace`
    the view already computed -- from a query param, a body field, or None
    for a bare by-id lookup -- passes through as-is). For a
    ChannelPrincipal-authenticated request, `workspace` is always forced to
    `bound_workspace_id`, regardless of what the client supplied or
    omitted -- a by-id lookup for an object in a different workspace then
    returns nothing (404, never leaking existence), and a create with a
    mismatched or absent `workspace` is silently corrected to the bound one.
    """
    auth = getattr(request, "auth", None)
    if isinstance(auth, ChannelPrincipal):
        workspace = auth.bound_workspace_id
    return queryset.for_actor(request.user, workspace=workspace)
