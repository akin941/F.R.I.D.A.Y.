"""Channel-trust primitive (MVP 9 §1).

Lives in `core`, not `integrations`: `approvals` needs to ask "did this
request arrive over a low-trust channel?" without importing `integrations`,
and every kernel app may already depend on `core` (one-way, per the
"core must stay independent of kernel apps" contract) -- so this keeps
`integrations` and `approvals` mutually unaware of each other.
"""

from dataclasses import dataclass

# The high-trust channel. A request is CHANNEL_WEB iff its authenticator set
# `request.auth` to a `WebSessionPrincipal` -- which only
# `accounts.authentication.DeviceJWTAuthentication` does, after checking
# (1) a valid access JWT carrying the `did` claim, (2) that `did` names a
# non-revoked, non-idle-expired TrustedDevice of the token's user, (3) the
# user is active, the owner and not an agent; (4) the request came through
# the BFF, whose Origin/Content-Type checks are the CSRF boundary (Phase 4
# branch 2). Nothing else is ever web.
CHANNEL_WEB = "web"
CHANNEL_CLI = "cli"  # low-trust: bearer PAT of kind `cli`
# Low-trust: bearer PAT of kind `openai`, used only by the OpenAI-compatible
# facade (Phase 4 branch 6, ADR-008 decision 6: chat only, no tools, never
# decides anything).
CHANNEL_OPENAI = "openai"
# Low-trust: bearer PAT of kind `hook` (Phase 8A D6), used only by the
# Claude Code hook relay (`friday-hook`) on the append-only hook-event
# ingest endpoint. Can write episodes through the ingestion port and read
# nothing.
CHANNEL_HOOK = "hook"
# Low-trust: a Friday Node's `jnd_` credential (a `kind=node`
# TrustedDevice, Phase 9 spike S9.0), produced only by
# `accounts.authentication.NodeCredentialAuthentication`. Reaches only the
# node credential rotation endpoint and (through `nodes`) the node
# WebSocket. Never mints a JWT, never decides or confirms anything.
CHANNEL_NODE = "node"
# Low-trust: a `node_voice` PAT (Phase 11 D3/D4), minted only by a node
# secret and handed to the node's voice child. Produced only by
# `accounts.authentication.NodeVoiceTokenAuthentication` (and its policy
# subclass). Reaches exactly: the chat turn of a conversation opened via
# `node_voice` (input_mode forced to `voice`, so the ADR-015 cap always
# applies), the three voice endpoints, the live-sessions endpoint and the
# voice-policy read. Never decides, confirms or approves anything.
CHANNEL_NODE_VOICE = "node_voice"
CHANNEL_TELEGRAM = "telegram"  # low-trust: relayed, no direct request at all
# Not a request channel: machine-originated actions (agent engine, harness
# gates, sweeps) that record "which channel" in audit metadata or on
# `ApprovalRequest.requested_channel` (Phase 4 branch 1). Never trusted for
# a decision -- only CHANNEL_WEB is.
CHANNEL_SYSTEM = "system"
# Default-deny (Phase 4 branch 2): any authenticated request whose auth
# object is neither a WebSessionPrincipal nor a ChannelPrincipal -- e.g. a
# future authenticator that forgot to state its channel -- is low-trust.
CHANNEL_UNKNOWN = "unknown"


@dataclass(frozen=True)
class WebSessionPrincipal:
    """`request.auth` for a trusted device's web session. Carries the
    TrustedDevice id so device-scoped actions ("Bu cihazı unut", marking
    the current device in the list) know which device is asking."""

    device_id: str

    @property
    def channel(self):
        return CHANNEL_WEB


@dataclass(frozen=True)
class ChannelPrincipal:
    """Set as `request.auth` by a low-trust authenticator (accounts'
    `PersonalAccessTokenAuthentication` / `FacadeTokenAuthentication`).

    `channel` is CHANNEL_CLI, CHANNEL_OPENAI, CHANNEL_HOOK or
    CHANNEL_NODE_VOICE (the token's `kind`), or CHANNEL_NODE (a node
    credential);
    `token_id` names the PersonalAccessToken, for per-token limits (the
    facade's throttle and stream cap) -- for CHANNEL_NODE it names the
    node's TrustedDevice. CHANNEL_TELEGRAM never appears here --
    Telegram has no direct request/response cycle with our API at all (it's
    relayed via a webhook + Celery task), so there is no request to
    authenticate as Telegram in the first place.
    """

    channel: str
    bound_workspace_id: str
    token_id: str | None = None


def request_channel(request) -> str:
    """CHANNEL_WEB only for a WebSessionPrincipal; a ChannelPrincipal's own
    (low-trust) channel; CHANNEL_UNKNOWN for anything else (default-deny)."""
    auth = getattr(request, "auth", None)
    if isinstance(auth, WebSessionPrincipal):
        return CHANNEL_WEB
    if isinstance(auth, ChannelPrincipal):
        # A ChannelPrincipal is low-trust by definition; one claiming "web"
        # is a bug, never trust.
        return auth.channel if auth.channel != CHANNEL_WEB else CHANNEL_UNKNOWN
    return CHANNEL_UNKNOWN


def request_device_id(request):
    """The TrustedDevice id of a web session, or None."""
    auth = getattr(request, "auth", None)
    return auth.device_id if isinstance(auth, WebSessionPrincipal) else None


def is_low_trust(request) -> bool:
    return request_channel(request) != CHANNEL_WEB


def scoped_for_actor(request, queryset, workspace=None):
    """Drop-in replacement for `<Model>.objects.for_actor(request.user,
    workspace=...)` in every view allowlisted for PAT auth (MVP 9 §2).

    For a request without a ChannelPrincipal, behavior is unchanged (whatever `workspace`
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
