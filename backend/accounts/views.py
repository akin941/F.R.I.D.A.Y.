import secrets
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from accounts.authentication import TOKEN_PREFIX, hash_token
from accounts.models import PersonalAccessToken, User
from accounts.serializers import (
    LogoutSerializer,
    PersonalAccessTokenCreateSerializer,
    PersonalAccessTokenSerializer,
    SignupSerializer,
    UserSerializer,
    WorkspaceSummarySerializer,
)
from accounts.services import register_user
from audit.services import record_audit
from workspaces.models import Workspace, WorkspaceMember


def _token_pair(user):
    refresh = RefreshToken.for_user(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


class AuditedLoginView(TokenObtainPairView):
    """TokenObtainPairView + an audit record on failed credentials.

    Behaviour is unchanged (401 on bad credentials); we only observe. The email
    is recorded to support abuse investigation; the password never is.

    Throttled by the "login" scope -- effectively a global rate, since
    Django only ever sees the BFF's address (config/settings/base.py).
    """

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request, *args, **kwargs):
        try:
            return super().post(request, *args, **kwargs)
        except (AuthenticationFailed, InvalidToken):
            record_audit(
                action="auth.login_failed",
                actor=None,
                metadata={
                    "email": str(request.data.get("email", ""))[:254],
                    "reason": "invalid_credentials",
                },
            )
            raise


class ThrottledTokenRefreshView(TokenRefreshView):
    """simplejwt's refresh (rotation + blacklist-after-rotation) under the
    "refresh" throttle scope."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "refresh"


class LogoutView(APIView):
    """Ends a web session server-side (Phase 4 branch 1): blacklists the
    refresh token in the body, so it can never mint another access token.
    Called by the BFF's logout route. The outstanding access token simply
    runs out (ACCESS_TOKEN_LIFETIME).

    Possession of a valid refresh token is the credential here -- an
    access token may already be expired at logout -- so no authentication
    is required. The view keeps the default (JWT-only) authenticators, so
    a CLI personal access token is refused outright (401): this is a web
    channel endpoint. When a valid access token IS sent, it must belong to
    the same user as the refresh token.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "refresh"

    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            token = RefreshToken(serializer.validated_data["refresh"])
        except TokenError:
            # Invalid, expired or already blacklisted: nothing left to end.
            return Response(
                {"detail": "Invalid or expired refresh token."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        user_id = token.get(jwt_settings.USER_ID_CLAIM)
        if request.user.is_authenticated and str(request.user.pk) != str(user_id):
            return Response(
                {"detail": "Refresh token belongs to another user."},
                status=status.HTTP_403_FORBIDDEN,
            )
        token.blacklist()
        record_audit(
            action="auth.logout",
            actor=User.objects.filter(pk=user_id).first(),
            metadata={"jti": token.get(jwt_settings.JTI_CLAIM, "")},
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class SignupView(APIView):
    permission_classes = [AllowAny]
    # Same anonymous, password-setting shape as login (and off by default).
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        if not settings.SIGNUP_ENABLED:
            return Response(
                {"detail": "Signup is disabled."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = SignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = register_user(**serializer.validated_data)
        return Response(
            {"user": UserSerializer(user).data, "tokens": _token_pair(user)},
            status=status.HTTP_201_CREATED,
        )


class MeView(APIView):
    def get(self, request):
        memberships = WorkspaceMember.objects.filter(user=request.user).select_related("workspace")
        workspaces = [
            {
                "id": m.workspace_id,
                "name": m.workspace.name,
                "type": m.workspace.type,
                "role": m.role,
            }
            for m in memberships
        ]
        return Response(
            {
                "user": UserSerializer(request.user).data,
                "workspaces": WorkspaceSummarySerializer(workspaces, many=True).data,
            }
        )


class PersonalAccessTokenListCreateView(APIView):
    """Mint/list a CLI bearer credential (MVP 9 §1). Web/JWT-authenticated
    only — minting a token is itself a sensitive, human-decided action,
    never done by a channel that's already low-trust (no PAT auth
    allowlisted here)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        # A PAT is a per-user credential, not a shared workspace resource
        # (round-5 P2 fix) -- filter by user, not only workspace membership,
        # so one company-workspace member can never see another's tokens.
        tokens = PersonalAccessToken.objects.filter(user=request.user)
        return Response(PersonalAccessTokenSerializer(tokens, many=True).data)

    def post(self, request):
        serializer = PersonalAccessTokenCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        workspace = Workspace.objects.for_actor(
            request.user, workspace=serializer.validated_data["workspace"]
        ).first()
        if workspace is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        ttl_days = serializer.validated_data.get("expires_in_days") or (
            settings.PAT_DEFAULT_TTL_DAYS
        )
        raw_token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        token = PersonalAccessToken.objects.create(
            workspace=workspace,
            user=request.user,
            name=serializer.validated_data["name"],
            token_hash=hash_token(raw_token),
            expires_at=timezone.now() + timedelta(days=ttl_days),
        )
        record_audit(
            action="accounts.pat.created",
            actor=request.user,
            workspace=workspace,
            resource=token,
            metadata={"via": "web", "expires_at": token.expires_at.isoformat()},
        )
        data = PersonalAccessTokenSerializer(token).data
        data["token"] = raw_token  # only ever appears in this one response
        return Response(data, status=status.HTTP_201_CREATED)


class PersonalAccessTokenRevokeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, token_id):
        # user=request.user: only the requester's own tokens are ever
        # revocable, regardless of who else shares the workspace.
        token = PersonalAccessToken.objects.filter(id=token_id, user=request.user).first()
        if token is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if token.revoked_at is None:
            token.revoked_at = timezone.now()
            token.save(update_fields=["revoked_at"])
            record_audit(
                action="accounts.pat.revoked",
                actor=request.user,
                workspace=token.workspace,
                resource=token,
            )
        return Response(PersonalAccessTokenSerializer(token).data)
