import uuid

from django.conf import settings
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.settings import api_settings as jwt_settings

from accounts.models import DevicePairingCode, PersonalAccessToken, TrustedDevice, User


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
            "workspace",
            "last_used_at",
            "revoked_at",
            "expires_at",
            "created_at",
        ]
        read_only_fields = fields


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


class TrustedDeviceSerializer(serializers.ModelSerializer):
    # Never includes the secret hash.
    is_current = serializers.SerializerMethodField()
    idle_expires_at = serializers.SerializerMethodField()

    class Meta:
        model = TrustedDevice
        fields = [
            "id",
            "name",
            "enrolled_via",
            "enrolled_ip",
            "user_agent",
            "created_at",
            "last_seen_at",
            "last_seen_ip",
            "revoked_at",
            "revoke_reason",
            "idle_expires_at",
            "is_current",
        ]
        read_only_fields = fields

    def get_is_current(self, device):
        return str(device.pk) == str(self.context.get("current_device_id") or "")

    def get_idle_expires_at(self, device):
        return device.idle_expires_at()


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
