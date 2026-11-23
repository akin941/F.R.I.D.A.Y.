"""`manage.py memory_eval run|baseline|compare|record` (Phase 8B branch 3,
brief D9; runbook `docs/runbooks/memory-eval.md`).

- `run --set public|private [--retrieval-mode legacy] [--retrieval-only]`:
  one run with the chosen retrieval mode.
- `baseline --set public|private [--retrieval-only]`: **the** baseline,
  `legacy` retrieval and no compression (D9). With `--set private` on the
  owner's stack it is the recorded baseline every compare is measured
  against.
- `compare --set public|private --retrieval-mode <candidate>`: arm A legacy,
  arm B the candidate (branch 4 registers `hybrid`); verdict on arm B.
- `compare --set public|private --compression <purpose>` (branch 6, D11):
  both arms answer under `<purpose>` (e.g. `chat`) with `legacy` retrieval,
  arm A with compression off, arm B with it on for that purpose only. The
  verdict is `inconclusive` when arm B compressed nothing, `fail` at more
  than 1 point accuracy loss or any extra leak, else `pass`.

These three run **only** under `DJANGO_SETTINGS_MODULE=config.settings.eval`
(own database) and print a metrics table. Each writes a metrics-only result
file (mode 0600): the private set under `<MEMORY_EVAL_PRIVATE_DIR>/results/`,
the public set under `MEMORY_EVAL_RESULTS_DIR`.

- `record <file> --owner <email> [--workspace <id>]`: store a result file
  as a `MemoryEvalRun` in the LIVE database, in that owner's personal
  workspace only; refused under the eval settings, for another workspace,
  and for a file with any key outside the D9 list.
"""

from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from audit.services import record_audit
from memory.evals.loader import (
    PUBLIC_DIR,
    RESULTS_DIRNAME,
    LoaderError,
    load_private_cases,
    load_public_cases,
    private_dir,
)
from memory.evals.results import ResultError, read_result_file, validate_result
from memory.evals.retrieval import BASELINE_MODE
from memory.evals.runner import (
    EvalRefused,
    RunConfig,
    require_eval_settings,
    run_cases,
    run_compare,
    run_compression_compare,
)
from memory.models import MemoryEvalRun
from workspaces.models import Workspace

_TABLE_KEYS = (
    "total",
    "answered",
    "no_provider",
    "budget_stopped",
    "spend_usd",
    "recall_at_10",
    "leak_rate",
    "temporal_correctness",
    "answer_accuracy",
    "pre_tokens",
    "post_tokens",
    "verdict",
)


def _dirs():
    private = private_dir(settings.MEMORY_EVAL_PRIVATE_DIR)
    return {
        "private_cases": private,
        "private": private / RESULTS_DIRNAME,
        "public": Path(private_dir(settings.MEMORY_EVAL_RESULTS_DIR)),
    }


def _column(result):
    if result.get("compression_purpose"):
        label = f"{result['compression_purpose']} compression " + (
            "on" if result["arm"] == "b" else "off"
        )
    else:
        label = f"{result['retrieval_mode']}"
    return label + (f" (arm {result['arm']})" if result["arm"] else "")


class Command(BaseCommand):
    help = "Run the memory eval set (eval settings only) or record a result file."

    def add_arguments(self, parser):
        actions = parser.add_subparsers(dest="action", required=True)
        for name in ("run", "baseline", "compare"):
            sub = actions.add_parser(name)
            sub.add_argument("--set", dest="set_name", choices=("public", "private"), required=True)
            sub.add_argument(
                "--retrieval-only",
                action="store_true",
                help="Retrieval metrics only: no answer call (CI on FakeAdapter).",
            )
            if name == "run":
                sub.add_argument("--retrieval-mode", default=BASELINE_MODE)
            if name == "compare":
                # `--retrieval hybrid` (brief D8) is the same option as
                # `--retrieval-mode`; `--compression` is the D11 arm.
                arm = sub.add_mutually_exclusive_group(required=True)
                arm.add_argument("--retrieval-mode", "--retrieval")
                arm.add_argument(
                    "--compression",
                    metavar="PURPOSE",
                    help="D11: both arms answer under PURPOSE, arm B with compression on.",
                )
        record = actions.add_parser("record")
        record.add_argument("file")
        record.add_argument("--owner", required=True, help="The installation owner's e-mail.")
        record.add_argument("--workspace", help="Must be that owner's personal workspace.")

    def handle(self, *args, **options):
        action = options["action"]
        if action == "record":
            return self._record(options)
        try:
            require_eval_settings()
            cases = self._cases(options["set_name"])
            return self._run(action, cases, options)
        except (EvalRefused, LoaderError, ValueError) as exc:
            raise CommandError(str(exc)) from exc

    # --- runs ---------------------------------------------------------------

    def _cases(self, set_name):
        if set_name == "public":
            return load_public_cases(PUBLIC_DIR)
        return load_private_cases(_dirs()["private_cases"])

    def _run(self, action, cases, options):
        dirs = _dirs()
        results_dir = dirs[options["set_name"]]
        spend_dirs = (dirs["private"], dirs["public"])
        if action == "compare" and options.get("compression"):
            if options["retrieval_only"]:
                raise EvalRefused(
                    "a compression compare needs the answer calls: drop --retrieval-only"
                )
            arms, paths = run_compression_compare(
                cases,
                set_name=options["set_name"],
                purpose=options["compression"],
                results_dir=results_dir,
                spend_dirs=spend_dirs,
            )
            self._table(arms)
        elif action == "compare":
            arms, paths = run_compare(
                cases,
                set_name=options["set_name"],
                candidate_mode=options["retrieval_mode"],
                retrieval_only=options["retrieval_only"],
                results_dir=results_dir,
                spend_dirs=spend_dirs,
            )
            self._table(arms)
        else:
            config = RunConfig(
                set_name=options["set_name"],
                kind=action,
                retrieval_mode=(
                    BASELINE_MODE if action == "baseline" else options["retrieval_mode"]
                ),
                retrieval_only=options["retrieval_only"],
            )
            result, path = run_cases(cases, config, results_dir=results_dir, spend_dirs=spend_dirs)
            self._table([result])
            paths = [path]
        for path in paths:
            self.stdout.write(f"Result file: {path}")

    def _table(self, results):
        headers = ["metric"] + [_column(r) for r in results]
        first = results[0]
        self.stdout.write(
            f"memory_eval {first['kind']} --set {first['set']} (run {first['run_id']})"
        )
        rows = [headers] + [
            [key] + ["-" if r[key] is None else str(r[key]) for r in results] for key in _TABLE_KEYS
        ]
        widths = [max(len(row[i]) for row in rows) for i in range(len(headers))]
        for number, row in enumerate(rows):
            self.stdout.write("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
            if number == 0:
                self.stdout.write("  ".join("-" * w for w in widths))

    # --- record -------------------------------------------------------------

    def _record(self, options):
        if getattr(settings, "MEMORY_EVAL_MODE", False):
            raise CommandError(
                "memory_eval record writes the LIVE database: run it under the normal "
                "settings, not config.settings.eval."
            )
        owner = (
            get_user_model()
            .objects.filter(
                email__iexact=options["owner"], is_owner=True, is_agent=False, is_active=True
            )
            .first()
        )
        if owner is None:
            raise CommandError("--owner must name the installation owner.")
        workspace = (
            Workspace.objects.filter(owner=owner, type=Workspace.Type.PERSONAL)
            .order_by("created_at")
            .first()
        )
        if workspace is None:
            raise CommandError("The owner has no personal workspace.")
        if options.get("workspace") and options["workspace"] != str(workspace.id):
            raise CommandError("Eval runs are recorded only in the owner's personal workspace.")
        try:
            values = validate_result(read_result_file(options["file"]))
        except ResultError as exc:
            raise CommandError(f"refused: {exc}") from exc
        with transaction.atomic():
            run, created = MemoryEvalRun.objects.get_or_create(
                run_id=values["run_id"],
                arm=values["arm"],
                defaults={**values, "workspace": workspace},
            )
            if not created and run.workspace_id != workspace.id:
                raise CommandError("This run is already recorded elsewhere.")
            if created:
                record_audit(
                    action="memory.eval.recorded",
                    actor=owner,
                    workspace=workspace,
                    resource=run,
                    metadata={"set": run.set, "kind": run.kind, "arm": run.arm},
                )
        self.stdout.write(
            f"{'Recorded' if created else 'Already recorded'}: {run.set} {run.kind} run "
            f"{run.run_id}" + (f" arm {run.arm}" if run.arm else "")
        )
