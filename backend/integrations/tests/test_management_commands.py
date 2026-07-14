from unittest import mock

import pytest
from django.core.management import CommandError, call_command

pytestmark = pytest.mark.django_db


def test_register_telegram_webhook_requires_bot_token_and_secret(settings):
    settings.TELEGRAM_BOT_TOKEN = ""
    settings.TELEGRAM_WEBHOOK_SECRET = ""
    with pytest.raises(CommandError):
        call_command("register_telegram_webhook", "https://example.com")


def test_register_telegram_webhook_calls_set_webhook_with_the_right_url(settings):
    settings.TELEGRAM_BOT_TOKEN = "token"
    settings.TELEGRAM_WEBHOOK_SECRET = "secret"
    with mock.patch(
        "integrations.telegram_client.set_webhook", return_value={"ok": True}
    ) as set_webhook:
        call_command("register_telegram_webhook", "https://example.com/")

    set_webhook.assert_called_once_with(
        "https://example.com/api/v1/integrations/telegram/webhook", "secret"
    )


def test_sweep_telegram_link_codes_command_runs_the_sweep():
    with mock.patch(
        "integrations.management.commands.sweep_telegram_link_codes.sweep_expired_link_codes",
        return_value=3,
    ) as sweep:
        call_command("sweep_telegram_link_codes")

    sweep.assert_called_once()
