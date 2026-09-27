"""PAT bearer authentication (MVP 9 §1) — CLI channel credential.

Never added to `DEFAULT_AUTHENTICATION_CLASSES`: only the specific view
classes this MVP allowlists (assistant/agents/approvals) set this explicitly
in their own `authentication_classes`, with this class ordered BEFORE
`JWTAuthentication`. DRF's authentication loop stops at the first
authenticator that raises `AuthenticationFailed` — `JWTAuthentication`
raises `InvalidToken` (an `AuthenticationFailed` subclass) for any
non-JWT-shaped string, including a `jpat_...` token, so if it ran first on
an allowlisted view every PAT request would be rejected before this
authenticator ever saw it. Do not reorder this without re-testing a real
PAT request end-to-end — the bug does not show up in a unit test that only
exercises JWT.
"""

import hashlib

from django.core.cache import cache
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from accounts.models import PersonalAccessToken
from audit.models import AuditLog
from audit.services import record_audit
from core.channels import CHANNEL_CLI, ChannelPrincipal

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


def _audit_auth_failure(raw_token, reason):
    cache.add(_AUTH_FAILURE_AUDIT_KEY, 0, timeout=AUTH_FAILURE_AUDIT_WINDOW_SECONDS)
    if cache.incr(_AUTH_FAILURE_AUDIT_KEY) > AUTH_FAILURE_AUDIT_LIMIT:
        return
    record_audit(
        action="accounts.pat.auth_failed",
        actor=None,
        actor_type=AuditLog.ActorType.SYSTEM,
        metadata={"token_prefix": raw_token[:_AUDITED_PREFIX_LENGTH], "reason": reason},
    )


class PersonalAccessTokenAuthentication(BaseAuthentication):
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
            _audit_auth_failure(raw_token, reason)
            raise AuthenticationFailed("Invalid, expired or revoked token.")

        token.last_used_at = now
        token.save(update_fields=["last_used_at"])

        return (
            token.user,
            ChannelPrincipal(channel=CHANNEL_CLI, bound_workspace_id=str(token.workspace_id)),
        )
