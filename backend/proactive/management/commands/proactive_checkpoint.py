"""`manage.py proactive_checkpoint [--json]` (Phase 12 D20): the morning
digest's go/no-go data per workspace -- first enable date, days enabled,
every enable/disable, digests produced, opened and spoken (web /
node_voice). Reads only; no digest text is printed."""

import json

from django.core.management.base import BaseCommand

from proactive.checkpoint import build_checkpoint


class Command(BaseCommand):
    help = "Print the Phase 12 morning-digest checkpoint data per workspace."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="Print JSON.")

    def handle(self, *args, **options):
        rows = build_checkpoint()
        if options["json"]:
            self.stdout.write(json.dumps(rows, indent=2, ensure_ascii=False))
            return
        if not rows:
            self.stdout.write("No morning_digest schedule exists yet.")
            return
        for row in rows:
            self.stdout.write(f"Workspace {row['workspace_name']} ({row['workspace']})")
            self.stdout.write(f"  enabled now:       {'yes' if row['enabled_now'] else 'no'}")
            self.stdout.write(f"  first enabled at:  {row['first_enabled_at'] or '-'}")
            self.stdout.write(f"  days enabled:      {row['days_enabled']}")
            for item in row["transitions"]:
                self.stdout.write(f"    {item['at']}  {item['event']}")
            self.stdout.write(
                f"  digests: {row['digests']} (model {row['composed_by_model']}), "
                f"opened: {row['opened']}, spoken web: {row['spoken_web']}, "
                f"spoken node_voice: {row['spoken_node_voice']}"
            )
