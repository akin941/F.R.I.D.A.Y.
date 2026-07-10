"""Manually runnable recovery scan (MVP 3 §3) — the same logic the Celery
beat periodic task runs, callable directly with no broker/worker required.
Useful for ops: re-dispatch stale pending/expired-lease documents on demand.
"""

from django.core.management.base import BaseCommand

from knowledge.tasks import recover_stale_knowledge_documents


class Command(BaseCommand):
    help = "Re-dispatch stale KnowledgeDocument rows (lost dispatch or expired lease)."

    def handle(self, *args, **options):
        count = recover_stale_knowledge_documents()
        self.stdout.write(self.style.SUCCESS(f"Re-dispatched {count} stale knowledge document(s)."))
