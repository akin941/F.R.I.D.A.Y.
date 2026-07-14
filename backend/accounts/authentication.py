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

from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from accounts.models import PersonalAccessToken
from core.channels import CHANNEL_CLI, ChannelPrincipal

TOKEN_PREFIX = "jpat_"


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


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
            PersonalAccessToken.objects.filter(
                token_hash=hash_token(raw_token), revoked_at__isnull=True
            )
            .select_related("user")
            .first()
        )
        # A deactivated user's still-unrevoked PAT is rejected exactly like
        # a revoked or unknown one — JWTAuthentication's own user lookup
        # enforces this already; a bespoke authenticator gets nothing for
        # free and must check explicitly.
        if token is None or not token.user.is_active:
            raise AuthenticationFailed("Invalid or revoked token.")

        token.last_used_at = timezone.now()
        token.save(update_fields=["last_used_at"])

        return (
            token.user,
            ChannelPrincipal(channel=CHANNEL_CLI, bound_workspace_id=str(token.workspace_id)),
        )
