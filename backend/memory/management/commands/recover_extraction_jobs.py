"""Manually runnable recovery scan (MVP 2 §4) — the same logic the Celery
beat periodic task runs, callable directly with no broker/worker required.
Useful for ops: re-dispatch stale pending jobs and expired-lease running
jobs on demand.
"""

from django.core.management.base import BaseCommand

from memory.tasks import recover_stale_extraction_jobs


class Command(BaseCommand):
    help = "Re-dispatch stale MemoryExtractionJob rows (lost dispatch or expired lease)."

    def handle(self, *args, **options):
        count = recover_stale_extraction_jobs()
        self.stdout.write(self.style.SUCCESS(f"Re-dispatched {count} stale extraction job(s)."))
