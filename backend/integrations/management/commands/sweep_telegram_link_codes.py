"""Manually runnable expiry sweep (MVP 9 §5) -- the same logic the Celery
beat periodic task runs, callable directly with no broker/worker required.
"""

from django.core.management.base import BaseCommand

from integrations.tasks import sweep_expired_link_codes


class Command(BaseCommand):
    help = "Delete expired, unconsumed TelegramLinkCode rows."

    def handle(self, *args, **options):
        count = sweep_expired_link_codes()
        self.stdout.write(self.style.SUCCESS(f"Deleted {count} expired Telegram link code(s)."))
