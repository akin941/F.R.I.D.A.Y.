"""Authenticators.

`DeviceJWTAuthentication` (Phase 4 branch 2) is the sole
`DEFAULT_AUTHENTICATION_CLASSES` entry: the web session of a trusted device,
the only way to reach `core.channels.CHANNEL_WEB`.

`PersonalAccessTokenAuthentication` (MVP 9 §1) is the CLI channel's
low-trust bearer credential; it accepts only `cli`-kind tokens.
`FacadeTokenAuthentication` (Phase 4 branch 6) is the same credential
check for the OpenAI-compatible facade and accepts only `openai`-kind
tokens. A valid token of the other kind is refused with a 403
(`token_kind_forbidden`), never silently authenticated. Neither is ever
added to `DEFAULT_AUTHENTICATION_CLASSES`: only the specific view classes
MVP 9 allowlists (assistant/agents/approvals) set the PAT class explicitly
in their own `authentication_classes`, ordered BEFORE
`DeviceJWTAuthentication`; the facade views use `FacadeTokenAuthentication`
alone. `NodeVoiceTokenAuthentication` (Phase 11 D3) accepts `cli` and
`node_voice` tokens and replaces the PAT class on the chat turn and the
voice routes. `HookTokenAuthentication` (Phase 8A D6) is the same check for the
Claude Code hook relay and accepts only `hook`-kind tokens; only the
hook-event ingest view uses it. `NodeCredentialAuthentication` (Phase 9
spike S9.0) accepts only `jnd_` Friday Node secrets -> CHANNEL_NODE; only
the node credential rotation view uses it. DRF's authentication loop stops at the first
authenticator that raises `AuthenticationFailed` — `JWTAuthentication`
raises `InvalidToken` (an `AuthenticationFailed` subclass) for any
non-JWT-shaped string, including a `jpat_...` token, so if it ran first on
an allowlisted view every PAT request would be rejected before this
authenticator ever saw it. Do not reorder this without re-testing a real
PAT request end-to-end — the bug does not show up in a unit test that only
exercises JWT.
"""

import hashlib
import uuid

from django.core.cache import cache
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework_simplejwt.authentication import JWTAuthentication

from accounts.models import PersonalAccessToken, TrustedDevice
from audit.models import AuditLog
from audit.services import record_audit
from core.channels import (
    CHANNEL_CLI,
    CHANNEL_HOOK,
    CHANNEL_NODE,
    CHANNEL_NODE_VOICE,
    CHANNEL_OPENAI,
    ChannelPrincipal,
    WebSessionPrincipal,
)
from core.client_ip import trusted_client_ip

TOKEN_PREFIX = "jpat_"

# Failed PAT authentications are audited (Phase 4 branch 1), but a client
# hammering the endpoint with garbage must not be able to flood the audit
# log: at most this many `accounts.pat.auth_failed` rows per window,
# install-wide (fixed-window counter in the default cache).
AUTH_FAILURE_AUDIT_LIMIT = 10
AUTH_FAILURE_AUDIT_WINDOW_SECONDS = 60
_AUTH_FAILURE_AUDIT_KEY = "accounts:pat-auth-failure-audits"
# The audited identifier: the fixed prefix plus 4 characters of the random
# part -- enough to tell tokens apart in an investigation, never enough to
# help reconstruct one.
_AUDITED_PREFIX_LENGTH = len(TOKEN_PREFIX) + 4


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


# The channel each token kind authenticates as.
KIND_CHANNELS = {
    PersonalAccessToken.Kind.CLI: CHANNEL_CLI,
    PersonalAccessToken.Kind.OPENAI: CHANNEL_OPENAI,
    PersonalAccessToken.Kind.HOOK: CHANNEL_HOOK,
    PersonalAccessToken.Kind.NODE_VOICE: CHANNEL_NODE_VOICE,
}


def _audit_auth_failure(raw_token, reason, surface):
    cache.add(_AUTH_FAILURE_AUDIT_KEY, 0, timeout=AUTH_FAILURE_AUDIT_WINDOW_SECONDS)
    if cache.incr(_AUTH_FAILURE_AUDIT_KEY) > AUTH_FAILURE_AUDIT_LIMIT:
        return
    record_audit(
        action="accounts.pat.auth_failed",
        actor=None,
        actor_type=AuditLog.ActorType.SYSTEM,
        metadata={
            "token_prefix": raw_token[:_AUDITED_PREFIX_LENGTH],
            "reason": reason,
            "surface": surface,
        },
    )


class PersonalAccessTokenAuthentication(BaseAuthentication):
    # The token kind this authenticator accepts, and the surface name the
    # audit log records for a failure against it.
    accepted_kind = PersonalAccessToken.Kind.CLI
    surface = "cli"
    # Phase 11 D3: the kinds accepted, each authenticating as its own
    # `KIND_CHANNELS` channel. None = `{accepted_kind}`, so every existing
    # single-kind subclass behaves exactly as before.
    accepted_kinds = None

    def get_accepted_kinds(self):
        if self.accepted_kinds is not None:
            return frozenset(self.accepted_kinds)
        return frozenset({self.accepted_kind})

    def check_token(self, token, raw_token, now):
        """Kind-specific re-checks after the generic ones (Phase 11 D3);
        raise to refuse. The base class has none."""

    def authenticate_header(self, request):
        # DRF's `get_authenticate_header` only ever consults the FIRST
        # configured authenticator (rest_framework.views.APIView) -- since
        # this class is ordered first on every allowlisted view, leaving
        # this unimplemented (BaseAuthentication's default `None`) would
        # silently downgrade every unauthenticated 401 on those views to a
        # 403 (DRF's `handle_exception` treats a missing header as "can't
        # challenge, so don't claim 401"). Returning a real challenge value
        # here restores the pre-MVP-9 401 behavior those views already had.
        return "Bearer"

    def authenticate(self, request):
        header = request.headers.get("Authorization", "")
        scheme, _, raw_token = header.partition(" ")
        if scheme.lower() != "bearer" or not raw_token.startswith(TOKEN_PREFIX):
            return None  # not a PAT-shaped credential — fall through

        token = (
            PersonalAccessToken.objects.filter(token_hash=hash_token(raw_token))
            .select_related("user")
            .first()
        )
        now = timezone.now()
        # A deactivated user's still-unrevoked PAT is rejected exactly like
        # a revoked or unknown one — JWTAuthentication's own user lookup
        # enforces this already; a bespoke authenticator gets nothing for
        # free and must check explicitly. One generic message for every
        # reason (no oracle); the reason goes to the audit log only.
        if token is None:
            reason = "unknown"
        elif token.revoked_at is not None:
            reason = "revoked"
        elif token.expires_at <= now:
            reason = "expired"
        elif not token.user.is_active:
            reason = "inactive_user"
        else:
            reason = None
        if reason is not None:
            _audit_auth_failure(raw_token, reason, self.surface)
            raise AuthenticationFailed("Invalid, expired or revoked token.")
        if token.kind not in self.get_accepted_kinds():
            # A genuine token presented to the wrong surface: 403, not 401 --
            # the credential is fine, this surface is not for its kind. Never
            # touches `last_used_at`.
            _audit_auth_failure(raw_token, "wrong_kind", self.surface)
            raise PermissionDenied(
                "This token's kind cannot be used on this endpoint.", code="token_kind_forbidden"
            )

        self.check_token(token, raw_token, now)

        token.last_used_at = now
        token.save(update_fields=["last_used_at"])

        return (
            token.user,
            ChannelPrincipal(
                channel=KIND_CHANNELS[token.kind],
                bound_workspace_id=str(token.workspace_id),
                token_id=str(token.pk),
            ),
        )


class FacadeTokenAuthentication(PersonalAccessTokenAuthentication):
    """The OpenAI-compatible facade's only authenticator (Phase 4 branch 6):
    `openai`-kind tokens -> CHANNEL_OPENAI. Bearer-only; the facade never
    accepts a web session."""

    accepted_kind = PersonalAccessToken.Kind.OPENAI
    surface = "openai"


class HookTokenAuthentication(PersonalAccessTokenAuthentication):
    """The Claude Code hook relay's only authenticator (Phase 8A D6):
    `hook`-kind tokens -> CHANNEL_HOOK. Used by exactly one view, the
    append-only hook-event ingest (injected by `config/urls.py`, since
    `memory` does not import `accounts`). Every other authenticator refuses a
    hook token (wrong kind -> 403, or not a JWT -> 401), and this one refuses
    every other kind with a 403."""

    accepted_kind = PersonalAccessToken.Kind.HOOK
    surface = "hook"


class NodeVoiceTokenAuthentication(PersonalAccessTokenAuthentication):
    """Phase 11 D3/D4: the PAT authenticator of the routes the node voice
    credential reaches -- the chat turn (`ConversationMessageListView`
    only) and the `voice` routes (injected by `config/urls.py`). It
    REPLACES the base PAT authenticator there (the base one claims every
    `jpat_` token and 403s a foreign kind, so it could not sit next to
    this one): a `cli` PAT keeps authenticating as CHANNEL_CLI exactly as
    before, a `node_voice` token authenticates as CHANNEL_NODE_VOICE, and
    `openai`/`hook` tokens keep their 403 `token_kind_forbidden`.

    For a `node_voice` token only, the minting node device is re-checked on
    EVERY request (`accounts.devices.node_device_refusal`): revoking the
    node, engaging the kill switch or `revoke_all_devices` kills the voice
    credential on its next request even before the token row itself is
    revoked."""

    accepted_kinds = frozenset({PersonalAccessToken.Kind.CLI, PersonalAccessToken.Kind.NODE_VOICE})
    surface = "node_voice"

    def check_token(self, token, raw_token, now):
        if token.kind != PersonalAccessToken.Kind.NODE_VOICE:
            return
        from accounts.devices import node_device_refusal

        device = (
            TrustedDevice.objects.select_related("user").filter(pk=token.node_device_id).first()
        )
        reason = node_device_refusal(device, now)
        if reason is None and device.user_id != token.user_id:
            reason = "device_owner_mismatch"
        if reason is not None:
            _audit_auth_failure(raw_token, f"node_{reason}", self.surface)
            raise AuthenticationFailed("Invalid, expired or revoked token.")


class NodeVoicePolicyAuthentication(NodeVoiceTokenAuthentication):
    """`GET /api/v1/auth/nodes/voice-policy` only (Phase 11 D3/D11):
    `node_voice` tokens alone (a `cli` PAT is a deliberate 403
    `token_kind_forbidden`). Mute-tolerant by design: once branch 5 adds
    the storm mute's 423 to `NodeVoiceTokenAuthentication`, this subclass
    keeps answering so the policy can report `storm_muted` in its
    reasons."""

    accepted_kinds = frozenset({PersonalAccessToken.Kind.NODE_VOICE})
    surface = "node_voice_policy"


class DeviceJWTAuthentication(JWTAuthentication):
    """The high-trust web channel (Phase 4 branch 2). On top of simplejwt's
    access-token validation and active-user lookup, every request checks:

    - the token carries the `did` claim (a JWT without it -- e.g. one
      minted before device trust existed -- is rejected outright);
    - `did` names a TrustedDevice of the token's user that is neither
      revoked nor idle-expired (one indexed primary-key lookup per request,
      so revoking a device kills its outstanding access tokens with the
      next request);
    - the user is the installation owner and not an agent.

    Success sets `request.auth` to a `WebSessionPrincipal` -- the only way
    `core.channels.request_channel()` ever returns CHANNEL_WEB.

    "Next request" is the guarantee: an already-open chat SSE stream is not
    re-checked mid-stream; it ends with its reply (bounded by the gateway
    timeout).
    """

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None
        user, validated_token = result

        from accounts.devices import DEVICE_CLAIM, device_session_is_valid, touch_device

        raw_device_id = validated_token.get(DEVICE_CLAIM)
        if not raw_device_id:
            raise AuthenticationFailed(
                "Session is not bound to a trusted device.", code="device_missing"
            )
        try:
            device_id = uuid.UUID(str(raw_device_id))
        except ValueError as exc:
            raise AuthenticationFailed("Invalid device.", code="device_invalid") from exc
        device = TrustedDevice.objects.filter(pk=device_id).first()
        if not device_session_is_valid(device, user):
            raise AuthenticationFailed("This device is no longer trusted.", code="device_untrusted")
        touch_device(device, client_ip=trusted_client_ip(request))
        return user, WebSessionPrincipal(device_id=str(device.pk))


class NodeCredentialAuthentication(BaseAuthentication):
    """A Friday Node's credential (Phase 9 spike S9.0, ADR-013 decision 2):
    `Authorization: Bearer jnd_...` of a usable `kind=node` device of the
    active, human owner whose kill switch is released
    (`accounts.devices.authenticate_node_secret`, which audits failures under
    the PAT flood cap). Sets `request.auth` to a
    `ChannelPrincipal(channel=CHANNEL_NODE, bound_workspace_id=<owner's
    personal workspace>, token_id=<device id>)` -- low-trust, never web.

    Used by exactly one DRF view, the node credential rotation endpoint
    (pinned by config/tests/test_node_credential_surfaces.py); the node
    WebSocket handshake calls the service directly. Every other
    authenticator refuses a node secret (not a JWT -> 401; not a PAT
    prefix -> falls through), and this one ignores every other credential
    shape, so a web session or PAT on the rotate endpoint is unauthenticated
    (401)."""

    def authenticate_header(self, request):
        return "Bearer"

    def authenticate(self, request):
        from accounts.devices import (
            NODE_SECRET_PREFIX,
            NodeCredentialRejected,
            authenticate_node_secret,
        )
        from workspaces.models import Workspace

        header = request.headers.get("Authorization", "")
        scheme, _, raw_secret = header.partition(" ")
        if scheme.lower() != "bearer" or not raw_secret.startswith(NODE_SECRET_PREFIX):
            return None  # not a node-shaped credential
        try:
            credential = authenticate_node_secret(
                raw_secret=raw_secret, client_ip=trusted_client_ip(request), surface="api"
            )
        except NodeCredentialRejected as exc:
            raise AuthenticationFailed("Invalid, expired or revoked node credential.") from exc
        device = credential.device
        workspace_id = (
            Workspace.objects.filter(owner=device.user, type=Workspace.Type.PERSONAL)
            .values_list("pk", flat=True)
            .first()
        )
        if workspace_id is None:
            raise AuthenticationFailed("Invalid, expired or revoked node credential.")
        return (
            device.user,
            ChannelPrincipal(
                channel=CHANNEL_NODE,
                bound_workspace_id=str(workspace_id),
                token_id=str(device.pk),
            ),
        )
