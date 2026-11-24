"""`manage.py voice_wake_eval` (Phase 11 brief D11, D13): record and list the
false-accept / false-reject evaluations of a wake model -- the only writer
of `accounts.VoiceWakeEvaluation`.

    manage.py voice_wake_eval record --model-sha256 <hex> --kind household \\
        --threshold 0.5 --refractory-ms 2000 --hours 24.5 --false-accepts 3
    manage.py voice_wake_eval record --model-sha256 <hex> --kind positives \\
        --threshold 0.5 --refractory-ms 2000 --positives 60 --false-rejects 4
    manage.py voice_wake_eval record --from-report report.json --kind household \\
        [--model-sha256 <hex>]
    manage.py voice_wake_eval list

An owner-shell command: the rows land in the installation owner's personal
workspace, recorded by the owner, audited (`accounts.voice_wake_eval.
recorded`). Append-only: a newer row supersedes the older one for the same
model and kind (the gate judges the latest row only); nothing is edited or
deleted. Impossible or inconsistent numbers are refused (negative values,
FAR not equal to false accepts / hours, FRR not equal to false rejects /
positives). Numbers only: no audio, no transcript, no file name.

`--from-report <json>` (branch 6) records exactly one row from a
`friday-voice wake-eval` report: `--kind household` or `negative_set` from
its negative section, `--kind positives` from its positives section. The
report is refused unless its hashes, its model hash (equal to
`--model-sha256` when given) and its numbers are consistent
(`accounts.wake_eval_report`); the row carries the report's
`report_sha256`. Explicit numbers cannot be mixed with a report.
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from accounts import voice_live, wake_eval_report
from accounts.models import User, VoiceWakeEvaluation

EXPLICIT = (
    "threshold",
    "refractory_ms",
    "hours",
    "false_accepts",
    "far_per_hour",
    "positives",
    "false_rejects",
    "frr",
)


class Command(BaseCommand):
    help = "Record or list wake-model evaluations (the always-on false-accept gate)."

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="action", required=True)
        record = sub.add_parser("record", help="append one evaluation row")
        record.add_argument("--model-sha256", default=None)
        record.add_argument(
            "--kind", required=True, choices=[str(kind) for kind in VoiceWakeEvaluation.Kind]
        )
        record.add_argument("--threshold", default=None)
        record.add_argument("--refractory-ms", default=None, type=int)
        record.add_argument("--hours", default=None)
        record.add_argument("--false-accepts", default=None, type=int)
        record.add_argument("--far-per-hour", default=None)
        record.add_argument("--positives", default=None, type=int)
        record.add_argument("--false-rejects", default=None, type=int)
        record.add_argument("--frr", default=None)
        record.add_argument("--report-sha256", default="")
        record.add_argument(
            "--from-report", default=None, help="a `friday-voice wake-eval` JSON report"
        )
        sub.add_parser("list", help="print the latest row per (model, kind) and its verdict")

    def _owner(self):
        owner = User.objects.filter(is_owner=True, is_active=True).first()
        if owner is None:
            raise CommandError("No active installation owner (run bootstrap_owner first).")
        return owner

    def handle(self, *args, **options):
        owner = self._owner()
        if options["action"] == "list":
            return self._list(owner)
        if options["from_report"]:
            fields = self._from_report(options)
        else:
            fields = self._explicit(options)
        try:
            row = voice_live.record_evaluation(owner=owner, recorded_by=owner, **fields)
        except (voice_live.EvaluationRefused, TypeError, ValueError) as exc:
            raise CommandError(f"refused: {exc}") from exc
        rate = f"far_per_hour={row.far_per_hour}" if row.frr is None else f"frr={row.frr}"
        self.stdout.write(
            f"recorded {row.kind} evaluation {row.pk} for {row.model_sha256[:12]}… "
            f"at threshold {row.threshold}, refractory {row.refractory_ms} ms: {rate}"
        )

    def _explicit(self, options):
        missing = [
            flag
            for flag, key in (
                ("--model-sha256", "model_sha256"),
                ("--threshold", "threshold"),
                ("--refractory-ms", "refractory_ms"),
            )
            if options[key] is None
        ]
        if missing:
            raise CommandError(f"refused: {', '.join(missing)} required without --from-report")
        return {key: options[key] for key in ("model_sha256", "kind", "report_sha256", *EXPLICIT)}

    def _from_report(self, options):
        if any(options[key] is not None for key in EXPLICIT) or options["report_sha256"]:
            raise CommandError("refused: --from-report takes no explicit numbers")
        try:
            raw = Path(options["from_report"]).read_bytes()
        except OSError as exc:
            raise CommandError("refused: cannot read the report") from exc
        try:
            return wake_eval_report.evaluation_fields(
                wake_eval_report.load(raw),
                options["kind"],
                expected_model_sha256=options["model_sha256"],
            )
        except wake_eval_report.ReportRefused as exc:
            raise CommandError(f"refused: {exc}") from exc

    def _list(self, owner):
        workspace_id = voice_live.personal_workspace_id(owner)
        entries = voice_live.model_verdicts(owner, workspace_id)
        if not entries:
            self.stdout.write("no evaluations recorded")
        for entry in entries:
            verdict = entry["verdict"]
            state = "permitted" if verdict.permitted else "refused: " + ", ".join(verdict.reasons)
            self.stdout.write(
                f"{entry['model_sha256']} threshold={entry['threshold']} "
                f"refractory_ms={entry['refractory_ms']} {state}"
            )
            for kind, row in sorted(entry["rows"].items()):
                rate = f"far={row.far_per_hour}" if row.frr is None else f"frr={row.frr}"
                self.stdout.write(f"  {kind}: {row.pk} recorded {row.recorded_at:%Y-%m-%d} {rate}")
