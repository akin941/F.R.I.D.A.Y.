from rest_framework import serializers


class TelegramLinkCodeCreateSerializer(serializers.Serializer):
    workspace = serializers.UUIDField()
