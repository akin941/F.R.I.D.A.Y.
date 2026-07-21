"""Manually runnable vault-export recovery scan (MVP 5 branch 3) -- the same
logic the Celery beat periodic task runs, callable directly with no
broker/worker required.
"""

from django.core.management.base import BaseCommand

from tools.tasks import recover_stale_vault_export_runs


class Command(BaseCommand):
    help = "Re-dispatch stale pending/running VaultExportRun rows."

    def handle(self, *args, **options):
        count = recover_stale_vault_export_runs()
        self.stdout.write(self.style.SUCCESS(f"Re-dispatched {count} stale vault export run(s)."))
