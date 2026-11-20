"""Voice routes. No module-level `urlpatterns`: the composition root
(`config/urls.py`) calls `build_urlpatterns` with the authentication
classes, because `voice` may not import `accounts` (D11) while a PAT must
reach the views to be refused with a 403 (see `voice/views.py`)."""

from django.urls import path

from voice.views import SpeechCreateView, TranscriptionCreateView


def build_urlpatterns(*, authentication_classes):
    auth = {"authentication_classes": list(authentication_classes)}
    return [
        path(
            "voice/transcriptions",
            TranscriptionCreateView.as_view(**auth),
            name="voice-transcription-create",
        ),
        path("voice/speech", SpeechCreateView.as_view(**auth), name="voice-speech-create"),
    ]
