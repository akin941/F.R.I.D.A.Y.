"""`manage.py voice_eval` -- the Turkish evaluation set (Phase 6 brief A4/A5,
branch 3a; runbook `docs/runbooks/voice-setup.md` §8).

- `voice_eval --set <dir>`: replay every recorded clip of the manifest
  through the gateway (`voice.eval_stt`, and `voice.eval_tts` for the
  first sentence unless `--stt-only`), print WER per condition and target
  group and the latency p50/p95, compare with the stored latency baseline
  (> 20% p50 regression fails), write the full report (0600) to
  `--out` or `<VOICE_EVAL_RESULTS_DIR>/voice-eval-<run>.json`.
  `--write-baseline` stores this run's p50s as the new baseline -- refused
  for a `fail` verdict, a regression, `--limit` or `--stt-only` unless
  `--force`. Reports, sheets and the baseline are refused inside the repo.
- `voice_eval --set <dir> --fill-hashes`: write the sha256 of each newly
  recorded clip into the manifest (never changes an existing hash; the
  manifest is replaced atomically).
- `voice_eval --check-set <dir>`: dry run, no database and no model call:
  the directory's mode and uid, whether it passes the 0700 check, the WAV
  count and each manifest row's hash status (for a bind mount).
- `voice_eval --tts-sheet`: synthesize the Turkish references for the
  owner's listening test; `voice_eval --score-tts <ratings.csv>` scores it
  (>= 4/5 intelligibility and naturalness).
- `voice_eval --traces [--days 30]`: A5 latency from the owner's live
  `VoiceTurnTrace` rows (p50 <= 1.5 s, p95 <= 2.5 s end of speech -> audio).

Exit status is non-zero when a verdict is `fail`. Never prints a
transcript or a reference text.
"""

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from voice.eval import replay, runner
from voice.eval.manifest import (
    ManifestError,
    expand,
    fill_hashes,
    load_set,
    parse_manifest,
    set_status,
    write_manifest_atomic,
)
from voice.eval.manifest import replace_table as manifest_replace_table
from voice.models import SOURCES


def default_manifest():
    if settings.VOICE_EVAL_MANIFEST:
        return expand(settings.VOICE_EVAL_MANIFEST)
    return Path(settings.BASE_DIR).parent / "docs" / "voice" / "eval-manifest.md"


def _fmt(value, percent=False):
    if value is None:
        return "-"
    return f"{value * 100:.1f}%" if percent else str(value)


class Command(BaseCommand):
    help = "Score the Turkish voice evaluation set (WER, TTS sheet, latency)."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--fill-hashes", action="store_true")
        mode.add_argument("--tts-sheet", action="store_true")
        mode.add_argument("--score-tts", metavar="RATINGS_CSV")
        mode.add_argument("--traces", action="store_true")
        mode.add_argument(
            "--check-set",
            metavar="DIR",
            help="Dry run: directory mode, uid, file count and hash status only.",
        )
        parser.add_argument("--set", dest="set_dir", default=None)
        parser.add_argument("--manifest", default=None)
        parser.add_argument("--owner", default=None, help="The installation owner's e-mail.")
        parser.add_argument("--out", default=None, help="Report file (must not exist).")
        parser.add_argument("--results-dir", default=None)
        parser.add_argument("--baseline", default=None)
        parser.add_argument("--write-baseline", action="store_true")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Write the baseline even from a failed, regressed or partial run.",
        )
        parser.add_argument("--stt-only", action="store_true")
        parser.add_argument("--stt-provider", default=None, help="Pin one STT row by name.")
        parser.add_argument("--tts-provider", default=None, help="Pin one TTS row by name.")
        parser.add_argument("--allow-missing", action="store_true")
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--tts-limit", type=int, default=20)
        parser.add_argument("--days", type=int, default=30)
        parser.add_argument("--source", choices=[s for s, _ in SOURCES], default=None)

    def handle(self, *args, **options):
        try:
            if options["check_set"]:
                return self._check_set(options)
            if options["score_tts"]:
                return self._score_tts(options)
            if options["fill_hashes"]:
                return self._fill_hashes(options)
            if options["tts_sheet"]:
                return self._tts_sheet(options)
            if options["traces"]:
                return self._traces(options)
            return self._run(options)
        except (ManifestError, runner.EvalError, ValueError, OSError) as exc:
            raise CommandError(str(exc)) from exc
        except replay.ReplayAborted as exc:
            raise CommandError(f"run aborted: {exc} (no report written)") from exc

    # --- helpers ---------------------------------------------------------------

    def _manifest(self, options):
        path = expand(options["manifest"]) if options["manifest"] else default_manifest()
        return path, parse_manifest(path.read_text(encoding="utf-8"))

    def _set_dir(self, options):
        return expand(options["set_dir"] or settings.VOICE_EVAL_SET_DIR)

    def _results_dir(self, options):
        return expand(options["results_dir"] or settings.VOICE_EVAL_RESULTS_DIR)

    def _verdict(self, label, verdict):
        self.stdout.write(f"{label}: {verdict}")
        if verdict == runner.FAIL:
            raise CommandError(f"{label}: fail")

    # --- modes -----------------------------------------------------------------

    def _fill_hashes(self, options):
        path, entries = self._manifest(options)
        updated, filled = fill_hashes(self._set_dir(options), entries)
        if filled:
            text = path.read_text(encoding="utf-8")
            write_manifest_atomic(path, manifest_replace_table(text, updated))
        self.stdout.write(f"filled {len(filled)} hash(es) in {path}")
        for name in filled:
            self.stdout.write(f"  {name}")

    def _run(self, options):
        _path, entries = self._manifest(options)
        check = load_set(self._set_dir(options), entries)
        if check.missing and not options["allow_missing"]:
            raise CommandError(
                "recorded in the manifest but missing from the set: "
                + ", ".join(e.file for e in check.missing)
                + " (--allow-missing to run without them)"
            )
        clips = check.clips[: options["limit"]] if options["limit"] else check.clips
        if not clips:
            raise CommandError("no recorded clip to replay (run --fill-hashes after recording)")
        owner, workspace = runner.resolve_owner(options["owner"])
        baseline_path = expand(options["baseline"] or settings.VOICE_EVAL_BASELINE)
        baseline = replay.read_baseline(baseline_path)
        run_id = runner.new_run_id()
        report = runner.run_wer(
            clips=clips,
            owner=owner,
            workspace=workspace,
            run_id=run_id,
            tts=not options["stt_only"],
            stt_provider=options["stt_provider"],
            tts_provider=options["tts_provider"],
            baseline=baseline,
        )
        report["set"] = {
            "replayed": len(clips),
            "unrecorded": len(check.unrecorded),
            "missing": len(check.missing),
            "unlisted": len(check.unlisted),
        }
        if options["out"]:
            out = expand(options["out"])
            runner.ensure_private_dir(out.parent)
        else:
            out = runner.ensure_private_dir(self._results_dir(options)) / (
                f"voice-eval-{run_id}.json"
            )
        runner.write_json(out, report)
        self._print_run(report)
        if options["write_baseline"]:
            refusal = self._baseline_refusal(report, options)
            if refusal and not options["force"]:
                self.stdout.write(f"Baseline NOT written: {refusal} (--force to override)")
            else:
                data = replay.make_baseline(
                    report["latency"], run_id=run_id, created_at=report["created_at"]
                )
                runner.replace_private(baseline_path, (json.dumps(data, indent=2) + "\n").encode())
                self.stdout.write(f"Baseline written: {baseline_path}")
        self.stdout.write(f"Report: {out}")
        self._verdict("verdict", report["verdict"])

    @staticmethod
    def _baseline_refusal(report, options):
        """Why this run must not become the latency baseline (A5): a baseline
        from a failed, regressed or partial run would hide the next
        regression."""
        if report["verdict"] == runner.FAIL:
            return "the run's verdict is fail"
        if report["regressions"]:
            return "the run regressed against the stored baseline"
        if options["limit"]:
            return "--limit replays only part of the set"
        if options["stt_only"]:
            return "--stt-only measures no TTS stage"
        return ""

    def _check_set(self, options):
        _path, entries = self._manifest(options)
        status = set_status(options["check_set"], entries)
        self.stdout.write(f"set directory: {status['directory']}")
        if not status["exists"]:
            raise CommandError("the set directory does not exist")
        self.stdout.write(
            f"mode {status['mode']}, uid {status['uid']} (this process: uid "
            f"{status['process_uid']}); private check: {status['private_check']}"
        )
        if status["is_dir"]:
            self.stdout.write(
                f"{status['wav_files']} WAV file(s), {status['manifest_rows']} manifest row(s): "
                f"{status['matched']} hash ok, {status['mismatched']} hash differs, "
                f"{status['missing']} missing, {status['unrecorded']} unrecorded, "
                f"{status['unreadable']} unreadable, {status['unlisted']} unlisted"
            )
        if status["private_check"] != "pass" or status.get("mismatched"):
            raise CommandError("the set would be refused by a real run")

    def _print_run(self, report):
        s = report["set"]
        self.stdout.write(
            f"voice_eval run {report['run_id']}: {s['replayed']} clip(s) replayed, "
            f"{s['unrecorded']} unrecorded, {s['missing']} missing, {s['unlisted']} unlisted; "
            f"STT {', '.join(report['stt_providers']) or '-'}, "
            f"TTS {', '.join(report['tts_providers']) or '-'}"
        )
        header = f"{'condition':<16}{'clips':>6}{'words':>7}{'S':>5}{'D':>5}{'I':>5}{'WER':>8}"
        self.stdout.write(header)
        for name, row in report["conditions"].items():
            self.stdout.write(self._wer_line(name, row))
        self.stdout.write(f"{header}{'target':>9}  verdict")
        for name, row in report["groups"].items():
            target = _fmt(row["target"], percent=True) if row["target"] is not None else "-"
            self.stdout.write(
                f"{self._wer_line(name, row)}{target:>9}  {row.get('verdict', 'info')}"
            )
        self.stdout.write(f"{'latency (ms)':<16}{'n':>6}{'p50':>8}{'p95':>8}")
        for stage, row in report["latency"].items():
            self.stdout.write(f"{stage:<16}{row['n']:>6}{_fmt(row['p50']):>8}{_fmt(row['p95']):>8}")
        if report["baseline_run_id"]:
            self.stdout.write(f"baseline: run {report['baseline_run_id']}")
        else:
            self.stdout.write("baseline: none stored (use --write-baseline)")
        for r in report["regressions"]:
            self.stdout.write(
                f"REGRESSION {r['stage']}: p50 {r['current_p50']} ms > {r['baseline_p50']} ms + 20%"
            )
        failed = [r for r in report["results"] if r["error"]]
        for r in failed:
            self.stdout.write(f"failed: {r['file']} ({r['error']})")

    @staticmethod
    def _wer_line(name, row):
        return (
            f"{name:<16}{row['clips']:>6}{row['reference_words']:>7}{row['substitutions']:>5}"
            f"{row['deletions']:>5}{row['insertions']:>5}{_fmt(row['wer'], percent=True):>8}"
        )

    def _tts_sheet(self, options):
        _path, entries = self._manifest(options)
        owner, workspace = runner.resolve_owner(options["owner"])
        run_id = runner.new_run_id()
        sheet, providers = runner.write_tts_sheet(
            entries=entries,
            owner=owner,
            workspace=workspace,
            run_id=run_id,
            results_dir=self._results_dir(options),
            provider=options["tts_provider"],
            limit=options["tts_limit"],
        )
        self.stdout.write(f"TTS {', '.join(providers) or '-'}: listen and rate in {sheet}")

    def _score_tts(self, options):
        result = runner.score_tts_sheet(options["score_tts"])
        self.stdout.write(
            f"TTS rating over {result['items']} item(s): intelligibility "
            f"{result['intelligibility']}, naturalness {result['naturalness']} "
            f"(target >= {result['target']})"
        )
        self._verdict("tts verdict", result["verdict"])

    def _traces(self, options):
        owner, workspace = runner.resolve_owner(options["owner"])
        result = runner.trace_latency(
            owner=owner, workspace=workspace, days=options["days"], source=options["source"]
        )
        self.stdout.write(
            f"VoiceTurnTrace, last {result['days']} day(s), source {result['source']}: "
            f"{result['turns']} turn(s)"
        )
        self.stdout.write(f"{'stage':<22}{'n':>6}{'p50':>8}{'p95':>8}")
        for stage, row in result["stages"].items():
            self.stdout.write(f"{stage:<22}{row['n']:>6}{_fmt(row['p50']):>8}{_fmt(row['p95']):>8}")
        self.stdout.write(
            f"target playback_start_ms p50 <= {result['target_p50_ms']}, "
            f"p95 <= {result['target_p95_ms']}"
        )
        self._verdict("latency verdict", result["verdict"])
