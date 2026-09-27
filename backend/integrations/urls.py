from django.urls import path

from integrations.openai_facade import FacadeChatCompletionsView, FacadeModelsView
from integrations.views import (
    TelegramLinkCodeCreateView,
    TelegramLinkStatusView,
    TelegramUnlinkView,
    TelegramWebhookView,
)

urlpatterns = [
    path(
        "integrations/telegram/link-code",
        TelegramLinkCodeCreateView.as_view(),
        name="telegram-link-code",
    ),
    path(
        "integrations/telegram/link-status",
        TelegramLinkStatusView.as_view(),
        name="telegram-link-status",
    ),
    path("integrations/telegram/link", TelegramUnlinkView.as_view(), name="telegram-unlink"),
    path("integrations/telegram/webhook", TelegramWebhookView.as_view(), name="telegram-webhook"),
    # OpenAI-compatible facade (Phase 4 branch 6); public as /v1/... via the
    # BFF passthrough routes (frontend/src/app/v1/).
    path(
        "openai/chat/completions",
        FacadeChatCompletionsView.as_view(),
        name="openai-chat-completions",
    ),
    path("openai/models", FacadeModelsView.as_view(), name="openai-models"),
]
