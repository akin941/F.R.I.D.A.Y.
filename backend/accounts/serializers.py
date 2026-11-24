import uuid

from django.conf import settings
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.settings import api_settings as jwt_settings

from accounts.models import (
    MINTABLE_PAT_KINDS,
    DevicePairingCode,
    PersonalAccessToken,
    TrustedDevice,
    User,
)


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "email", "name", "is_agent", "is_owner"]
        read_only_fields = fields


class WorkspaceSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    type = serializers.CharField()
    role = serializers.CharField()


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class PersonalAccessTokenCreateSerializer(serializers.Serializer):
    workspace = serializers.UUIDField()
    name = serializers.CharField(max_length=255)
    # Optional lifetime chosen at mint; omitted -> PAT_DEFAULT_TTL_DAYS.
    expires_in_days = serializers.IntegerField(required=False, min_value=1)
    # Phase 4 branch 6: which surface the token reaches (fixed at mint).
    # Phase 11 D3: `node_voice` is not mintable here (only a node secret
    # mints it, bound to its device) -> 400 `invalid_choice`.
    kind = serializers.ChoiceField(
        choices=[(kind.value, kind.label) for kind in MINTABLE_PAT_KINDS],
        default=PersonalAccessToken.Kind.CLI,
    )

    def validate_expires_in_days(self, value):
        # Validated here, not via max_value=, so the cap follows the live
        # setting rather than its value at import time.
        if value > settings.PAT_MAX_TTL_DAYS:
            raise serializers.ValidationError(
                f"Ensure this value is less than or equal to {settings.PAT_MAX_TTL_DAYS}."
            )
        return value


class PersonalAccessTokenSerializer(serializers.ModelSerializer):
    # Never includes the raw token or its hash — the raw value is returned
    # exactly once, in the mint response only (see views.py).
    class Meta:
        model = PersonalAccessToken
        fields = [
            "id",
            "name",
            "kind",
            "workspace",
            "last_used_at",
            "revoked_at",
            "expires_at",
            "created_at",
            # Phase 11 D3: the node device a `node_voice` token descends
            # from (null for every other kind). Read-only.
            "node_device",
        ]
        read_only_fields = fields


class NodeVoicePolicyQuerySerializer(serializers.Serializer):
    """`GET /api/v1/auth/nodes/voice-policy` query (Phase 11 D11): the wake
    model the child loaded and the threshold / refractory period it runs
    at. Bounded and optional in branch 1 (the gate is closed regardless);
    branch 5 judges them against the recorded evaluations."""

    model_sha256 = serializers.RegexField(r"^[0-9a-f]{64}$", required=False)
    threshold = serializers.DecimalField(
        max_digits=5, decimal_places=4, min_value=0, max_value=1, required=False
    )
    refractory_ms = serializers.IntegerField(min_value=0, max_value=60_000, required=False)


# --- device trust (Phase 4 branch 2) ---


class EnrollSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=200, trim_whitespace=True)
    device_name = serializers.CharField(max_length=100, trim_whitespace=True)
    # Informational only (shown in the device list); forwarded by the BFF.
    user_agent = serializers.CharField(max_length=1000, required=False, allow_blank=True)


class DeviceSessionStartSerializer(serializers.Serializer):
    device_secret = serializers.CharField(max_length=200)


class PairingCodeCreateSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(
        choices=DevicePairingCode.Kind.choices, default=DevicePairingCode.Kind.PIN
    )
    # Phase 9 D1: a code for a Friday Node ("Mac düğümü eşleştir").
    device_kind = serializers.ChoiceField(
        choices=TrustedDevice.Kind.choices, default=TrustedDevice.Kind.BROWSER
    )


class TrustedDeviceSerializer(serializers.ModelSerializer):
    # Never includes the secret hash (current or previous).
    is_current = serializers.SerializerMethodField()
    idle_expires_at = serializers.SerializerMethodField()
    # Phase 9 D1: a node's hard age cap (null for a browser).
    credential_expires_at = serializers.SerializerMethodField()

    class Meta:
        model = TrustedDevice
        fields = [
            "id",
            "name",
            "kind",
            "enrolled_via",
            "enrolled_ip",
            "user_agent",
            "created_at",
            "last_seen_at",
            "last_seen_ip",
            "revoked_at",
            "revoke_reason",
            "idle_expires_at",
            "credential_expires_at",
            "secret_issued_at",
            "is_current",
        ]
        read_only_fields = fields

    def get_is_current(self, device):
        return str(device.pk) == str(self.context.get("current_device_id") or "")

    def get_idle_expires_at(self, device):
        return device.idle_expires_at()

    def get_credential_expires_at(self, device):
        return device.credential_expires_at()


class DeviceRefreshSerializer(TokenRefreshSerializer):
    """simplejwt's refresh, plus the device check (Phase 4 branch 2): the
    refresh token must carry `did`, and that device must still be usable
    for the token's user -- a revoked or idle-expired device can never
    refresh (401). Rotation keeps the claim."""

    def validate(self, attrs):
        from accounts.devices import DEVICE_CLAIM, device_session_is_valid, touch_device

        refresh = self.token_class(attrs["refresh"])
        raw_device_id = refresh.payload.get(DEVICE_CLAIM)
        user = User.objects.filter(pk=refresh.payload.get(jwt_settings.USER_ID_CLAIM)).first()
        try:
            device_id = uuid.UUID(str(raw_device_id)) if raw_device_id else None
        except ValueError:
            device_id = None
        device = TrustedDevice.objects.filter(pk=device_id).first() if device_id else None
        if user is None or not device_session_is_valid(device, user):
            raise AuthenticationFailed("This device is no longer trusted.", "device_untrusted")
        data = super().validate(attrs)
        touch_device(device)
        return data
