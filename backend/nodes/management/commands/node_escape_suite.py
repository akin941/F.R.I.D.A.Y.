"""The `srt` escape suite (Phase 10 brief D11, ADR-014 decision 3).

    manage.py node_escape_suite                       # fake sandbox (CI)
    manage.py node_escape_suite --executor srt        # real srt (Linux, opt-in)
    manage.py node_escape_suite --check-policy FILE   # static check of exported policies
    manage.py node_escape_suite --export-policy FILE  # the reference generator's policies

`--executor fake` models the generated policy and exercises the runner's
caps and verdicts; `--executor srt` runs every script under the real
sandbox and refuses to start when `srt` is not installed. A Linux `srt` run
is local evidence only: the ADR-014 evidence is the Mac run (Seatbelt,
Mac session M3). `--check-policy` reads documents in the
`--export-policy` format (the Swift test target exports the same shape
from its own SandboxPolicy, Mac session M2). Exits non-zero unless every
case is contained (or covered)."""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from nodes.conformance.escape.runner import (
    check_policy_file,
    export_policies,
    run_suite,
)
from nodes.reference.executor import SrtExecutor


class Command(BaseCommand):
    help = "Run the node sandbox escape suite."

    def add_arguments(self, parser):
        parser.add_argument("--executor", choices=("fake", "srt"), default="fake")
        parser.add_argument("--check-policy", default=None)
        parser.add_argument("--export-policy", default=None)

    def handle(self, *args, **options):
        if options["export_policy"]:
            Path(options["export_policy"]).write_text(
                json.dumps(export_policies(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            self.stdout.write(f"wrote {options['export_policy']}")
            return
        if options["check_policy"]:
            document = json.loads(Path(options["check_policy"]).read_text(encoding="utf-8"))
            results = check_policy_file(document)
            failed = [(case_id, reasons) for case_id, reasons in results if reasons]
            for case_id, reasons in results:
                self.stdout.write(f"  {case_id.ljust(32)} {'UNCOVERED' if reasons else 'covered'}")
                for reason in reasons:
                    self.stdout.write(f"      - {reason}")
            if failed:
                raise CommandError(f"{len(failed)} escape case(s) not covered by the policy")
            self.stdout.write(f"{len(results)}/{len(results)} covered")
            return
        if options["executor"] == "srt" and not SrtExecutor.available():
            raise CommandError("srt is not installed; the real escape run cannot start")
        report = run_suite(options["executor"])
        self.stdout.write(report.table())
        if not report.passed:
            raise CommandError("the escape suite failed")
