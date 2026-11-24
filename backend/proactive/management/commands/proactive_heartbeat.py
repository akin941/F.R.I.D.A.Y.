"""`manage.py proactive_heartbeat [--at ISO8601]`: run one heartbeat tick
now (or as of `--at`), exactly what the 60 s beat entry
`proactive.tasks.heartbeat` does -- due owner schedules only, each claimed
and run inside its unattended context. An operator/e2e convenience: it
creates and edits no schedule (only the owner's web views do, D3)."""

import json

from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime

from proactive.tasks import run_due_schedules


class Command(BaseCommand):
    help = "Run one proactive heartbeat tick (due schedules only)."

    def add_arguments(self, parser):
        parser.add_argument("--at", default="", help="Run as of this aware ISO 8601 instant.")

    def handle(self, *args, **options):
        now = None
        if options["at"]:
            now = parse_datetime(options["at"])
            if now is None or now.tzinfo is None:
                raise CommandError("--at must be an aware ISO 8601 datetime.")
        summary = run_due_schedules(now=now)
        self.stdout.write(json.dumps(summary, default=str))
