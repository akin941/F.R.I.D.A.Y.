from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from accounts.models import PersonalAccessToken, User


class SignupSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    name = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value

    def validate_password(self, value):
        validate_password(value)
        return value


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "email", "name", "is_agent"]
        read_only_fields = fields


class WorkspaceSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    type = serializers.CharField()
    role = serializers.CharField()


class PersonalAccessTokenCreateSerializer(serializers.Serializer):
    workspace = serializers.UUIDField()
    name = serializers.CharField(max_length=255)


class PersonalAccessTokenSerializer(serializers.ModelSerializer):
    # Never includes the raw token or its hash — the raw value is returned
    # exactly once, in the mint response only (see views.py).
    class Meta:
        model = PersonalAccessToken
        fields = ["id", "name", "workspace", "last_used_at", "revoked_at", "created_at"]
        read_only_fields = fields
