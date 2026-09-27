import secrets
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from accounts import devices
from accounts.authentication import TOKEN_PREFIX, hash_token
from accounts.models import DevicePairingCode, PersonalAccessToken, TrustedDevice, User
from accounts.serializers import (
    DeviceRefreshSerializer,
    DeviceSessionStartSerializer,
    EnrollSerializer,
    LogoutSerializer,
    PairingCodeCreateSerializer,
    PersonalAccessTokenCreateSerializer,
    PersonalAccessTokenSerializer,
    TrustedDeviceSerializer,
    UserSerializer,
    WorkspaceSummarySerializer,
)
from audit.services import record_audit
from core.channels import request_device_id
from core.client_ip import trusted_client_ip
from core.permissions import IsOwner, IsWebChannel
from core.throttling import EnrollGlobalThrottle, EnrollPerSourceThrottle, ScopedRateThrottle
from workspaces.models import Workspace, WorkspaceMember

# Web-only, owner-only surfaces (device trust management).
_OWNER_WEB_PERMISSIONS = [IsAuthenticated, IsWebChannel, IsOwner]

# How long a remote-key enrollment is surfaced as a "new device enrolled
# remotely" banner on every device (MeView.security_notices).
_REMOTE_ENROLLMENT_NOTICE_DAYS = 7


def _session_payload(device, tokens):
    return {**tokens, "device": TrustedDeviceSerializer(device).data}


class EnrollView(APIView):
    """`POST /api/v1/auth/enroll` -- anonymous device enrollment with a
    pairing code or the remote access key (Phase 4 branch 2). Returns the
    device secret (once) plus a first JWT pair; the BFF stores all three in
    HttpOnly cookies and never hands them to the browser's JavaScript.

    Two-tier throttle: per BFF-validated client IP, plus an install-wide
    ceiling (core/throttling.py)."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [EnrollPerSourceThrottle, EnrollGlobalThrottle]

    def post(self, request):
        serializer = EnrollSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        client_ip = trusted_client_ip(request)
        try:
            device, raw_secret = devices.enroll_device(
                raw_code=serializer.validated_data["code"],
                device_name=serializer.validated_data["device_name"],
                client_ip=client_ip,
                user_agent=serializer.validated_data.get("user_agent", ""),
            )
        except devices.PinNetworkRefused:
            return Response(
                {
                    "detail": "PIN codes are accepted only from the local network; "
                    "use a strong code or the remote access key.",
                    "code": "pin_network_refused",
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        except (devices.InvalidPairingCode, devices.OwnerNotConfigured):
            # One answer for every reason (unknown, expired, used, burned,
            # no owner yet) -- the reason is in the audit log only.
            return Response(
                {"detail": "Invalid or expired code.", "code": "invalid_code"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        payload = _session_payload(device, devices.issue_session_tokens(device))
        payload["device_secret"] = raw_secret  # only ever appears in this response
        return Response(payload, status=status.HTTP_201_CREATED)


class DeviceSessionView(APIView):
    """`POST /api/v1/auth/devices/session` -- mint a fresh JWT pair from a
    device secret: the BFF's silent re-mint when a refresh fails, and the
    session start on a fresh page load. A rejected secret is 401 (the
    browser goes to /enroll)."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "refresh"

    def post(self, request):
        serializer = DeviceSessionStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            device, tokens = devices.start_device_session(
                raw_secret=serializer.validated_data["device_secret"],
                client_ip=trusted_client_ip(request),
            )
        except devices.DeviceRejected:
            return Response(
                {"detail": "This device is not trusted.", "code": "device_rejected"},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        return Response(_session_payload(device, tokens))


class DeviceTokenRefreshView(TokenRefreshView):
    """simplejwt's refresh (rotation + blacklist-after-rotation) under the
    "refresh" throttle scope, refusing a token whose device is revoked or
    idle-expired, or that has no device at all (401)."""

    serializer_class = DeviceRefreshSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "refresh"


class LogoutView(APIView):
    """Ends a web session server-side (Phase 4 branch 1): blacklists the
    refresh token in the body, so it can never mint another access token.
    Called by the BFF's logout route ("Çıkış"). The device stays trusted --
    "Bu cihazı unut" (`devices/current/revoke`) is the one that revokes it.

    Possession of a valid refresh token is the credential here, so no
    access token is required (the BFF never sends one on logout). The view
    keeps the default (device-JWT-only) authenticators, so any bearer that
    IS sent is authenticated first: a CLI personal access token -- or an
    expired/invalid access token -- is refused outright (401) before the
    refresh token is looked at. A valid access token must belong to the
    same user as the refresh token.
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


class DeviceListView(APIView):
    """`GET /api/v1/auth/devices` -- the owner's devices, current one
    marked."""

    permission_classes = _OWNER_WEB_PERMISSIONS

    def get(self, request):
        items = TrustedDevice.objects.filter(user=request.user)
        context = {"current_device_id": request_device_id(request)}
        return Response(TrustedDeviceSerializer(items, many=True, context=context).data)


class DeviceRevokeView(APIView):
    """`POST /api/v1/auth/devices/<id>/revoke` -- immediate: the device's
    outstanding access tokens fail on their next request."""

    permission_classes = _OWNER_WEB_PERMISSIONS

    def post(self, request, device_id):
        device = TrustedDevice.objects.filter(pk=device_id, user=request.user).first()
        if device is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        device = devices.revoke_device(device=device, actor=request.user)
        context = {"current_device_id": request_device_id(request)}
        return Response(TrustedDeviceSerializer(device, context=context).data)


class CurrentDeviceRevokeView(APIView):
    """`POST /api/v1/auth/devices/current/revoke` -- "Bu cihazı unut": the
    requesting device revokes itself."""

    permission_classes = _OWNER_WEB_PERMISSIONS

    def post(self, request):
        device = TrustedDevice.objects.filter(
            pk=request_device_id(request), user=request.user
        ).first()
        if device is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        devices.revoke_device(
            device=device, actor=request.user, reason=TrustedDevice.RevokeReason.FORGOTTEN
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class PairingCodeCreateView(APIView):
    """`POST /api/v1/auth/pairing-codes` -- "Yeni cihaz eşleştir": a trusted
    session issues a PIN (default) or a strong code, shown once."""

    permission_classes = _OWNER_WEB_PERMISSIONS

    def post(self, request):
        serializer = PairingCodeCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        issuing_device = TrustedDevice.objects.filter(
            pk=request_device_id(request), user=request.user
        ).first()
        raw, code = devices.issue_pairing_code(
            owner=request.user,
            kind=serializer.validated_data["kind"],
            issued_via=DevicePairingCode.IssuedVia.WEB,
            issued_by_device=issuing_device,
        )
        return Response(
            {"code": raw, "kind": code.kind, "expires_at": code.expires_at},
            status=status.HTTP_201_CREATED,
        )


def _remote_key_state(owner):
    key = devices.active_remote_key(owner)
    if key is None:
        return {"active": False, "created_at": None, "last_used_at": None}
    return {"active": True, "created_at": key.created_at, "last_used_at": key.last_used_at}


class RemoteAccessKeyView(APIView):
    """`GET /api/v1/auth/remote-key` -- whether remote enrollment is on;
    `POST` creates or rotates the key (returned once)."""

    permission_classes = _OWNER_WEB_PERMISSIONS

    def get(self, request):
        return Response(_remote_key_state(request.user))

    def post(self, request):
        device = TrustedDevice.objects.filter(
            pk=request_device_id(request), user=request.user
        ).first()
        raw, _ = devices.rotate_remote_key(owner=request.user, via="web", device=device)
        return Response(
            {**_remote_key_state(request.user), "key": raw}, status=status.HTTP_201_CREATED
        )


class RemoteAccessKeyRevokeView(APIView):
    """`POST /api/v1/auth/remote-key/revoke` -- turn remote enrollment off."""

    permission_classes = _OWNER_WEB_PERMISSIONS

    def post(self, request):
        devices.revoke_remote_key(owner=request.user, via="web")
        return Response(_remote_key_state(request.user))


def _security_notices(user):
    """Recent remote-key enrollments, surfaced as a "new device enrolled
    remotely" banner on every device (Phase 4 branch 2)."""
    since = timezone.now() - timedelta(days=_REMOTE_ENROLLMENT_NOTICE_DAYS)
    recent = TrustedDevice.objects.filter(
        user=user, enrolled_via=TrustedDevice.EnrolledVia.REMOTE_KEY, created_at__gte=since
    )
    return [
        {
            "kind": "remote_enrollment",
            "device_id": str(device.pk),
            "device_name": device.name,
            "created_at": device.created_at.isoformat(),
        }
        for device in recent
    ]


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
                "security_notices": _security_notices(request.user),
                # ADR-009: a delete's confirmation dialog states how long the
                # row stays restorable in the trash before the retention purge.
                "trash_retention_days": settings.TRASH_RETENTION_DAYS,
            }
        )


class PersonalAccessTokenListCreateView(APIView):
    """Mint/list a CLI bearer credential (MVP 9 §1). Web channel only —
    minting a token is itself a sensitive, human-decided action, never done
    by a channel that's already low-trust (no PAT auth allowlisted here,
    and IsWebChannel on top)."""

    permission_classes = [IsAuthenticated, IsWebChannel]

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
    permission_classes = [IsAuthenticated, IsWebChannel]

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
