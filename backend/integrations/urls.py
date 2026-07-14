from django.urls import path

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
]
