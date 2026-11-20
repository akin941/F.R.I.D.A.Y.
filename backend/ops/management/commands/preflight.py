"""`manage.py preflight` -- live end-to-end checks against the running stack
(Phase 5 closeout). Prints one line per check and a final summary line
`N pass / N fail / N warn`; exits non-zero when any check fails. A service
that is not configured here is a warning, never a failure.

`--no-model-calls` skips the one minimal gateway call per provider (they
are real, possibly paid calls, each recorded as `ModelUsage` with purpose
`ops.preflight`); those providers then report `warn`."""

from django.core.management.base import BaseCommand

from ops import preflight

_LABELS = {preflight.PASS: "PASS", preflight.FAIL: "FAIL", preflight.WARN: "WARN"}


class Command(BaseCommand):
    help = "Live end-to-end checks of the running stack; non-zero exit on any failure."

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-model-calls",
            action="store_true",
            help="Skip the one minimal gateway call per provider (no ModelUsage rows).",
        )

    def handle(self, *args, **options):
        results = preflight.run_preflight(model_calls=not options["no_model_calls"])
        styles = {
            preflight.PASS: self.style.SUCCESS,
            preflight.FAIL: self.style.ERROR,
            preflight.WARN: self.style.WARNING,
        }
        for result in results:
            label = styles[result.status](_LABELS[result.status])
            detail = f"  {result.detail}" if result.detail else ""
            self.stdout.write(f"{label}  {result.name}{detail}")
        summary = preflight.summarize(results)
        failed = any(r.status == preflight.FAIL for r in results)
        self.stdout.write(self.style.ERROR(summary) if failed else self.style.SUCCESS(summary))
        if failed:
            raise SystemExit(1)
