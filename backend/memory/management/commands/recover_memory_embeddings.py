"""Manually runnable recovery scan (Phase 1 `p1-memory-embeddings`) — the
same logic the Celery beat periodic task runs, callable directly with no
broker/worker required. Also serves as the one-time backfill for rows that
predate this branch: any `cloud_eligible()` row missing an embedding gets
picked up here, not just expired-lease retries.
"""

from django.core.management.base import BaseCommand

from memory.tasks import recover_stale_memory_embeddings


class Command(BaseCommand):
    help = "Re-dispatch Memory rows needing embedding (never claimed, or expired lease)."

    def handle(self, *args, **options):
        count = recover_stale_memory_embeddings()
        self.stdout.write(self.style.SUCCESS(f"Re-dispatched {count} memory row(s) for embedding."))
