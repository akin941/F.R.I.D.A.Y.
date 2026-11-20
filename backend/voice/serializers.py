from django.conf import settings
from rest_framework import serializers

from voice.models import CLIENT_STAGES, MAX_STAGE_MS


class TranscriptionCreateSerializer(serializers.Serializer):
    """`POST /api/v1/voice/transcriptions` (multipart): the utterance WAV
    and the conversation it belongs to. The file's own declared content type
    and name are ignored -- the bytes are validated (D3)."""

    conversation = serializers.UUIDField()
    audio = serializers.FileField(allow_empty_file=False)


class StageTimingsSerializer(serializers.Serializer):
    """Client-reported stage timings (A5), milliseconds. Unknown keys are
    dropped by DRF; nothing here is free text."""

    def get_fields(self):
        return {
            name: serializers.IntegerField(required=False, min_value=0, max_value=MAX_STAGE_MS)
            for name in CLIENT_STAGES
        }


class SpeechCreateSerializer(serializers.Serializer):
    """`POST /api/v1/voice/speech` (JSON): one reply sentence (D7). The
    text is free -- the reply is not persisted until its stream ends -- and
    capped at `VOICE_TTS_MAX_CHARS`. `language` selects the TTS route among
    the configured languages; the provider is the gateway's choice (A2)."""

    conversation = serializers.UUIDField()
    text = serializers.CharField(trim_whitespace=False)
    language = serializers.CharField(required=False, allow_blank=True, default="")
    timings = StageTimingsSerializer(required=False)

    def validate_text(self, value):
        if not value.strip():
            raise serializers.ValidationError("text must not be empty.")
        if len(value) > settings.VOICE_TTS_MAX_CHARS:
            raise serializers.ValidationError(
                f"text exceeds {settings.VOICE_TTS_MAX_CHARS} characters."
            )
        return value

    def validate_language(self, value):
        if not value:
            return value
        known = set(getattr(settings, "VOICE_TTS_ROUTES", None) or {}) | {
            settings.VOICE_STT_LANGUAGE
        }
        if value not in known:
            raise serializers.ValidationError("Unsupported language.")
        return value


class ReceiptQuerySerializer(serializers.Serializer):
    """`GET /api/v1/voice/receipt` query: the conversation (scoped through
    `for_actor()` by the view) and, optionally, the turn's trace id."""

    conversation = serializers.UUIDField()
    trace = serializers.UUIDField(required=False)
