"""Manually runnable expiry sweep (MVP 5 §1) -- the same logic the Celery
beat periodic task runs, callable directly with no broker/worker required.
"""

from django.core.management.base import BaseCommand

from approvals.tasks import sweep_expired_approval_requests


class Command(BaseCommand):
    help = "Expire stale pending ApprovalRequest rows past APPROVAL_REQUEST_TTL_HOURS."

    def handle(self, *args, **options):
        count = sweep_expired_approval_requests()
        self.stdout.write(self.style.SUCCESS(f"Expired {count} stale approval request(s)."))
