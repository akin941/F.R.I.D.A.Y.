"""Voice routes. No module-level `urlpatterns`: the composition root
(`config/urls.py`) calls `build_urlpatterns` with the authentication
classes, because `voice` may not import `accounts` (D11) while a PAT must
reach the views to be refused with a 403 (see `voice/views.py`)."""

from django.urls import path

from voice.views import (
    LiveSessionCreateView,
    SpeechCreateView,
    TranscriptionCreateView,
    UsageReceiptView,
)


def build_urlpatterns(
    *, authentication_classes, node_voice_permissions=(), transcription_permissions=()
):
    """Phase 11 D11/D12/D16: extra DRF permission classes from `accounts`,
    injected here because `voice` may not import `accounts`.
    `node_voice_permissions` (the trigger backstop) is appended to the
    transcription and live-sessions routes; `transcription_permissions`
    (the storm backstop) to the transcription route only, after them."""
    auth = {"authentication_classes": list(authentication_classes)}
    extra = list(node_voice_permissions)
    guarded_transcriptions = {
        **auth,
        "permission_classes": [
            *TranscriptionCreateView.permission_classes,
            *extra,
            *transcription_permissions,
        ],
    }
    guarded_sessions = {
        **auth,
        "permission_classes": [*LiveSessionCreateView.permission_classes, *extra],
    }
    return [
        path(
            "voice/transcriptions",
            TranscriptionCreateView.as_view(**guarded_transcriptions),
            name="voice-transcription-create",
        ),
        path("voice/speech", SpeechCreateView.as_view(**auth), name="voice-speech-create"),
        path("voice/receipt", UsageReceiptView.as_view(**auth), name="voice-usage-receipt"),
        path(
            "voice/live/sessions",
            LiveSessionCreateView.as_view(**guarded_sessions),
            name="voice-live-session-create",
        ),
    ]
