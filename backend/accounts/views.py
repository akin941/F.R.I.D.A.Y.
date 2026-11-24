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

from accounts import devices, voice_live
from accounts.authentication import (
    TOKEN_PREFIX,
    NodeCredentialAuthentication,
    NodeVoicePolicyAuthentication,
    hash_token,
)
from accounts.models import (
    DevicePairingCode,
    NodeKillSwitch,
    PersonalAccessToken,
    TrustedDevice,
    User,
)
from accounts.serializers import (
    DeviceRefreshSerializer,
    DeviceSessionStartSerializer,
    EnrollSerializer,
    LogoutSerializer,
    NodeVoicePolicyQuerySerializer,
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
from core.permissions import IsNodeChannel, IsNodeVoiceChannel, IsOwner, IsWebChannel
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
        try:
            raw, code = devices.issue_pairing_code(
                owner=request.user,
                kind=serializer.validated_data["kind"],
                issued_via=DevicePairingCode.IssuedVia.WEB,
                issued_by_device=issuing_device,
                device_kind=serializer.validated_data["device_kind"],
            )
        except devices.NodeKillSwitchEngaged:
            return Response(
                {
                    "detail": "The node kill switch is engaged; release it before pairing a node.",
                    "code": "conflict",
                },
                status=status.HTTP_409_CONFLICT,
            )
        return Response(
            {
                "code": raw,
                "kind": code.kind,
                "device_kind": code.device_kind,
                "expires_at": code.expires_at,
            },
            status=status.HTTP_201_CREATED,
        )


class NodeCredentialRotateView(APIView):
    """`POST /api/v1/auth/nodes/credential/rotate` (Phase 9 D1) -- a Friday
    Node rotates its `jnd_` secret without re-pairing. Authenticated ONLY by
    the current node secret (`NodeCredentialAuthentication`, plus
    `IsNodeChannel` so no other principal -- not even a forced web session
    -- gets through). Returns the new secret exactly once; the previous one
    stays valid for NODE_ROTATION_GRACE_SECONDS or until the new one is
    first used. A rotation presented with the previous secret is 403."""

    authentication_classes = [NodeCredentialAuthentication]
    permission_classes = [IsAuthenticated, IsNodeChannel]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "node_rotate"

    def post(self, request):
        _, _, raw_secret = request.headers.get("Authorization", "").partition(" ")
        try:
            device, new_secret = devices.rotate_node_secret(
                raw_secret=raw_secret, client_ip=trusted_client_ip(request)
            )
        except devices.NodeCredentialRejected:
            return Response(
                {
                    "detail": "Invalid, expired or revoked node credential.",
                    "code": "not_authenticated",
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )
        except devices.NodeRotationRefused:
            return Response(
                {
                    "detail": "Rotate with the current node secret.",
                    "code": "permission_denied",
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(
            {
                "node_id": str(device.pk),
                "node_secret": new_secret,  # only ever appears in this response
                "secret_issued_at": device.secret_issued_at,
                "previous_valid_until": device.previous_secret_valid_until,
                "credential_expires_at": device.credential_expires_at(),
                "rotate_after_days": settings.NODE_CREDENTIAL_ROTATE_AFTER_DAYS,
            }
        )


class NodeVoiceTokenMintView(APIView):
    """`POST /api/v1/auth/nodes/voice-token` (Phase 11 D3) -- the node mints
    the short-lived credential it hands its voice child over the stdin
    control pipe. Authenticated ONLY by the node secret
    (`NodeCredentialAuthentication` + `IsNodeChannel`, the rotation
    endpoint's pattern): a web session, a PAT of any kind or a voice token
    is refused. A revoked node or an engaged kill switch never gets here
    (the authenticator refuses it). Returns the raw token exactly once; the
    device's previous token keeps only the rotation grace."""

    authentication_classes = [NodeCredentialAuthentication]
    permission_classes = [IsAuthenticated, IsNodeChannel]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "node_voice_mint"

    def post(self, request):
        device = TrustedDevice.objects.filter(
            pk=request.auth.token_id, user=request.user, kind=TrustedDevice.Kind.NODE
        ).first()
        try:
            if device is None:
                raise devices.NodeCredentialRejected("unknown")
            minted = devices.mint_node_voice_token(device=device)
        except (devices.NodeCredentialRejected, devices.NodeVoiceMintRefused):
            return Response(
                {
                    "detail": "Invalid, expired or revoked node credential.",
                    "code": "not_authenticated",
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )
        response = Response(
            {
                "token": minted.raw,  # only ever appears in this response
                "token_id": str(minted.token.pk),
                "expires_at": minted.token.expires_at,
                "previous_valid_until": minted.previous_valid_until,
            },
            status=status.HTTP_201_CREATED,
        )
        response["Cache-Control"] = "no-store"
        return response


class NodeVoicePolicyView(APIView):
    """`GET /api/v1/auth/nodes/voice-policy` (Phase 11 D3/D11) -- may the
    voice child arm always-on wake mode for the wake model it loaded? Node
    voice token only (`NodeVoicePolicyAuthentication`: a `cli` PAT is a 403
    `token_kind_forbidden`; mute-tolerant: a storm-muted device reads
    `storm_muted` in the reasons instead of a 423). The server can only say
    no (ADR-013 decision 5). The verdict (D11, `accounts.voice_live`):
    `VOICE_LIVE_ALWAYS_ON_ENABLED` on, and for this model sha256 the latest
    `household` row (>= 24 h, FAR within budget) and the latest `positives`
    row (FRR within budget), both measured at exactly this threshold and
    refractory period."""

    authentication_classes = [NodeVoicePolicyAuthentication]
    permission_classes = [IsAuthenticated, IsNodeVoiceChannel]

    def get(self, request):
        serializer = NodeVoicePolicyQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        token = PersonalAccessToken.objects.filter(pk=request.auth.token_id).first()
        verdict = voice_live.policy_verdict(
            request.user,
            request.auth.bound_workspace_id,
            serializer.validated_data["model_sha256"],
            serializer.validated_data["threshold"],
            serializer.validated_data["refractory_ms"],
            device_id=token.node_device_id if token else None,
        )
        response = Response(verdict.as_dict())
        response["Cache-Control"] = "no-store"
        return response


def _evaluation_row(row):
    if row is None:
        return None
    return {
        "id": str(row.pk),
        "kind": row.kind,
        "hours": str(row.hours),
        "false_accepts": row.false_accepts,
        "far_per_hour": None if row.far_per_hour is None else str(row.far_per_hour),
        "positives": row.positives,
        "false_rejects": row.false_rejects,
        "frr": None if row.frr is None else str(row.frr),
        "threshold": str(row.threshold),
        "refractory_ms": row.refractory_ms,
        "recorded_at": row.recorded_at,
    }


class NodeVoiceLiveView(APIView):
    """`GET /api/v1/auth/nodes/voice-live` (Phase 11 D12/D15) -- the `/voice`
    page's "Canlı ses (Mac)" panel: the always-on setting and budgets, per
    node device of the owner its engaged storm mute (if any), and per
    measured wake model its latest evaluation rows with their verdict. Web
    session, owner only; everything is read through `for_actor()` in the
    owner's personal workspace, so another user's devices, mutes or rows
    never appear."""

    permission_classes = _OWNER_WEB_PERMISSIONS

    def get(self, request):
        workspace_id = voice_live.personal_workspace_id(request.user)
        nodes = []
        node_devices = TrustedDevice.objects.filter(
            user=request.user, kind=TrustedDevice.Kind.NODE, revoked_at__isnull=True
        ).order_by("created_at")
        for device in node_devices:
            mute = voice_live.engaged_mute(request.user, workspace_id, device.pk)
            nodes.append(
                {
                    "device_id": str(device.pk),
                    "name": device.name,
                    "mute": None
                    if mute is None
                    else {"engaged_at": mute.engaged_at, "reason": mute.reason},
                }
            )
        far_budget, frr_budget = voice_live.budgets()
        models_out = []
        for entry in voice_live.model_verdicts(request.user, workspace_id):
            rows = entry["rows"]
            models_out.append(
                {
                    "model_sha256": entry["model_sha256"],
                    "threshold": None if entry["threshold"] is None else str(entry["threshold"]),
                    "refractory_ms": entry["refractory_ms"],
                    "permitted": entry["verdict"].permitted,
                    "reasons": entry["verdict"].reasons,
                    "household": _evaluation_row(rows.get("household")),
                    "positives": _evaluation_row(rows.get("positives")),
                    "negative_set": _evaluation_row(rows.get("negative_set")),
                }
            )
        response = Response(
            {
                "always_on_enabled": voice_live.always_on_enabled(),
                "max_far_per_hour": str(far_budget),
                "max_frr": str(frr_budget),
                "min_household_hours": str(voice_live.MIN_HOUSEHOLD_HOURS),
                "storm_max_stt_per_minute": settings.VOICE_LIVE_STORM_MAX_STT_PER_MINUTE,
                "any_model_permitted": any(m["permitted"] for m in models_out),
                "nodes": nodes,
                "models": models_out,
            }
        )
        response["Cache-Control"] = "no-store"
        return response


class NodeVoiceMuteView(APIView):
    """`DELETE /api/v1/auth/nodes/{device_id}/voice-mute` (Phase 11 D12) --
    release a node device's storm mute. Only on screen: web session, owner
    (a `node_voice` token, a PAT or the node secret never reach it). The
    device is resolved through the owner's own node devices, so another
    user's device id is a 404 that confirms nothing; audited
    `accounts.voice_live_mute.released`."""

    permission_classes = _OWNER_WEB_PERMISSIONS

    def delete(self, request, device_id):
        device = TrustedDevice.objects.filter(
            pk=device_id, user=request.user, kind=TrustedDevice.Kind.NODE
        ).first()
        if device is None:
            return Response(
                {"detail": "Not found.", "code": "not_found"}, status=status.HTTP_404_NOT_FOUND
            )
        released = voice_live.release_mute(
            owner=request.user, device=device, via_device_id=str(request_device_id(request) or "")
        )
        return Response({"device_id": str(device.pk), "released": released is not None})


class NodeKillSwitchView(APIView):
    """`GET/POST/DELETE /api/v1/auth/nodes/kill-switch` (Phase 9 D1) -- the
    owner's node kill switch: state, engage, release. Web channel, owner,
    human only. Engaging closes every live node socket of the owner and
    refuses every node authentication and node pairing until released;
    releasing restores the nodes without re-pairing."""

    permission_classes = _OWNER_WEB_PERMISSIONS

    def _device(self, request):
        return TrustedDevice.objects.filter(
            pk=request_device_id(request), user=request.user
        ).first()

    def get(self, request):
        return Response(devices.node_kill_switch_state(request.user))

    def post(self, request):
        devices.engage_node_kill_switch(
            owner=request.user, via=NodeKillSwitch.Via.WEB, device=self._device(request)
        )
        return Response(devices.node_kill_switch_state(request.user))

    def delete(self, request):
        devices.release_node_kill_switch(
            owner=request.user, via=NodeKillSwitch.Via.WEB, device=self._device(request)
        )
        return Response(devices.node_kill_switch_state(request.user))


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
        if serializer.validated_data["kind"] == PersonalAccessToken.Kind.HOOK and not (
            request.user.is_owner
            and not request.user.is_agent
            and workspace.type == Workspace.Type.PERSONAL
            and workspace.owner_id == request.user.id
        ):
            # Phase 8A D6: the hook relay's token belongs to the installation
            # owner and is bound to their personal workspace (the binding
            # only authenticates; D4 mapping rules route each event).
            return Response(
                {
                    "code": "invalid",
                    "detail": "A hook token can only be minted by the owner, "
                    "bound to their personal workspace.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        ttl_days = serializer.validated_data.get("expires_in_days") or (
            settings.PAT_DEFAULT_TTL_DAYS
        )
        raw_token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        token = PersonalAccessToken.objects.create(
            workspace=workspace,
            user=request.user,
            name=serializer.validated_data["name"],
            kind=serializer.validated_data["kind"],
            token_hash=hash_token(raw_token),
            expires_at=timezone.now() + timedelta(days=ttl_days),
        )
        record_audit(
            action="accounts.pat.created",
            actor=request.user,
            workspace=workspace,
            resource=token,
            metadata={
                "via": "web",
                "kind": token.kind,
                "expires_at": token.expires_at.isoformat(),
            },
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
                metadata={"kind": token.kind},
            )
        return Response(PersonalAccessTokenSerializer(token).data)
