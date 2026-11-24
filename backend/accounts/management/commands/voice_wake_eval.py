"""`manage.py voice_wake_eval` (Phase 11 brief D11): record and list the
false-accept / false-reject evaluations of a wake model -- the only writer
of `accounts.VoiceWakeEvaluation`.

    manage.py voice_wake_eval record --model-sha256 <hex> --kind household \\
        --threshold 0.5 --refractory-ms 2000 --hours 24.5 --false-accepts 3
    manage.py voice_wake_eval record --model-sha256 <hex> --kind positives \\
        --threshold 0.5 --refractory-ms 2000 --positives 60 --false-rejects 4
    manage.py voice_wake_eval list

An owner-shell command: the rows land in the installation owner's personal
workspace, recorded by the owner, audited (`accounts.voice_wake_eval.
recorded`). Append-only: a newer row supersedes the older one for the same
model and kind (the gate judges the latest row only); nothing is edited or
deleted. Impossible or inconsistent numbers are refused (negative values,
FAR not equal to false accepts / hours, FRR not equal to false rejects /
positives). Numbers only: no audio, no transcript, no file name.
`--from-report <json>` (the `friday-voice wake-eval` report) lands with
branch 6 (`feat/p11-wake-eval-harness`).
"""

from django.core.management.base import BaseCommand, CommandError

from accounts import voice_live
from accounts.models import User, VoiceWakeEvaluation


class Command(BaseCommand):
    help = "Record or list wake-model evaluations (the always-on false-accept gate)."

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="action", required=True)
        record = sub.add_parser("record", help="append one evaluation row")
        record.add_argument("--model-sha256", required=True)
        record.add_argument(
            "--kind", required=True, choices=[str(kind) for kind in VoiceWakeEvaluation.Kind]
        )
        record.add_argument("--threshold", required=True)
        record.add_argument("--refractory-ms", required=True, type=int)
        record.add_argument("--hours", default=None)
        record.add_argument("--false-accepts", default=None, type=int)
        record.add_argument("--far-per-hour", default=None)
        record.add_argument("--positives", default=None, type=int)
        record.add_argument("--false-rejects", default=None, type=int)
        record.add_argument("--frr", default=None)
        record.add_argument("--report-sha256", default="")
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
        try:
            row = voice_live.record_evaluation(
                owner=owner,
                model_sha256=options["model_sha256"],
                kind=options["kind"],
                threshold=options["threshold"],
                refractory_ms=options["refractory_ms"],
                hours=options["hours"],
                false_accepts=options["false_accepts"],
                far_per_hour=options["far_per_hour"],
                positives=options["positives"],
                false_rejects=options["false_rejects"],
                frr=options["frr"],
                report_sha256=options["report_sha256"],
                recorded_by=owner,
            )
        except (voice_live.EvaluationRefused, TypeError, ValueError) as exc:
            raise CommandError(f"refused: {exc}") from exc
        rate = f"far_per_hour={row.far_per_hour}" if row.frr is None else f"frr={row.frr}"
        self.stdout.write(
            f"recorded {row.kind} evaluation {row.pk} for {row.model_sha256[:12]}… "
            f"at threshold {row.threshold}, refractory {row.refractory_ms} ms: {rate}"
        )

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
