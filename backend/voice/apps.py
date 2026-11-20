from django.apps import AppConfig


class VoiceConfig(AppConfig):
    """Phase 6 branch 2 (ADR-011 decision 6, brief D11): the voice
    transport -- two web-only endpoints around the unchanged chat turn
    (audio -> transcript, reply sentence -> audio) and the per-turn
    timing trace. A leaf kernel app: it may import `core`, `workspaces`,
    `ai_core` and `assistant` (conversation lookup, `Message.InputMode`);
    nothing imports it (import-linter contracts in `pyproject.toml`)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "voice"
