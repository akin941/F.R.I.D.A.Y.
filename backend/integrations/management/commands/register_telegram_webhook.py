"""One-time Telegram setWebhook registration (MVP 9 §4) -- an operator
action run once per deploy target, not something Celery beat runs
periodically.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from integrations import telegram_client

_WEBHOOK_PATH = "/api/v1/integrations/telegram/webhook"


class Command(BaseCommand):
    help = "Register this server's Telegram webhook URL with the Bot API (setWebhook)."

    def add_arguments(self, parser):
        parser.add_argument(
            "base_url", help="Public HTTPS base URL, e.g. https://jarvis.example.com"
        )

    def handle(self, *args, **options):
        if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_WEBHOOK_SECRET:
            raise CommandError(
                "TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_SECRET must both be set first."
            )
        webhook_url = options["base_url"].rstrip("/") + _WEBHOOK_PATH
        result = telegram_client.set_webhook(webhook_url, settings.TELEGRAM_WEBHOOK_SECRET)
        self.stdout.write(self.style.SUCCESS(f"setWebhook({webhook_url!r}) -> {result}"))
