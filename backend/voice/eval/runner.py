"""`voice_eval` orchestration (Phase 6 brief A4/A5, branch 3a): targets,
the WER run, the TTS listening-test sheet, the latency read of
`VoiceTurnTrace`, and the result files.

Every model call goes through the gateway (`replay.GatewaySTT` /
`GatewayTTS`), in the owner's personal workspace. Result files are written
outside the repo, mode 0600 in a 0700 directory, and never overwritten.
"""

import csv
import io
import json
import os
import secrets
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from voice.eval import replay
from voice.eval.manifest import expand
from voice.eval.scorer import NO_ERRORS, WordErrors
from voice.models import STAGES, VoiceTurnTrace
from workspaces.models import Workspace

# A4 exit targets (initial; re-settable after the first measurement -- any
# change is recorded in the Phase 6 brief "A9 — exit criteria record").
# Group -> (conditions pooled, maximum corpus WER). English is reported
# with no target.
WER_GROUPS = {
    "tr-quiet": (("tr-quiet",), 0.12),
    "tr-noise": (("tr-noise",), 0.18),
    "code-switched": (("cs-quiet", "cs-noise"), 0.15),
    "en": (("en-quiet", "en-noise"), None),
}
TTS_MIN_RATING = 4.0  # out of 5, for both intelligibility and naturalness
# A5 phase-exit latency target, end of owner speech -> first audio (ms).
LATENCY_TARGET_P50_MS = 1500
LATENCY_TARGET_P95_MS = 2500
TTS_SHEET_COLUMNS = ("item", "condition", "text", "intelligibility", "naturalness")

PASS, FAIL, INCOMPLETE = "pass", "fail", "incomplete"


class EvalError(ValueError):
    """A refused run (no owner, bad file, unusable directory)."""


# --- owner and workspace -------------------------------------------------------


def resolve_owner(email=None):
    """The installation owner and their personal workspace (brief 3a: the
    eval's ModelUsage rows belong to the owner's personal workspace)."""
    users = get_user_model().objects.filter(is_owner=True, is_active=True, is_agent=False)
    if email:
        users = users.filter(email__iexact=email)
    owner = users.order_by("created_at", "pk").first()
    if owner is None:
        raise EvalError(
            f"{email} is not the active installation owner" if email else "no installation owner"
        )
    # The oldest personal workspace, as `workspaces.services.
    # ensure_personal_workspace` picks it (read-only here: never created).
    workspace = (
        Workspace.objects.filter(owner=owner, type=Workspace.Type.PERSONAL)
        .order_by("created_at", "pk")
        .first()
    )
    if workspace is None:
        raise EvalError("the owner has no personal workspace")
    return owner, workspace


def new_run_id():
    return timezone.now().strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(3)


# --- files ---------------------------------------------------------------------


def repo_roots():
    """The checkout this code runs from: `backend/` and, when it is the
    repository (it holds CLAUDE.md), its parent. Inside the `web` image
    `backend/` is `/app` and its parent is `/`, which is not the repo."""
    backend = Path(settings.BASE_DIR).resolve()
    roots = [backend]
    if (backend.parent / "CLAUDE.md").exists():
        roots.append(backend.parent)
    return roots


def refuse_inside_repo(path, what):
    """Reports, TTS audio and the baseline never land in the repository."""
    resolved = Path(os.path.abspath(expand(path)))
    try:
        resolved = resolved.resolve()
    except OSError:
        pass
    for root in repo_roots():
        if resolved == root or root in resolved.parents:
            raise EvalError(f"{what} {path} is inside the repository; use a directory outside it")
    return path


def ensure_private_dir(directory):
    """Create `directory` (and missing parents) mode 0700; refuse an
    existing one others can read, and one inside the repository."""
    directory = expand(directory)
    refuse_inside_repo(directory, "directory")
    missing = []
    current = directory
    while not current.exists() and not current.is_symlink():
        missing.append(current)
        if current.parent == current:
            break
        current = current.parent
    for path in reversed(missing):
        path.mkdir(mode=0o700, exist_ok=True)
        os.chmod(path, 0o700)
    if directory.is_symlink() or not directory.is_dir():
        raise EvalError(f"{directory} is not a real directory")
    if os.stat(directory).st_mode & 0o077:
        raise EvalError(f"{directory} is readable by others; run `chmod 700` on it")
    return directory


def write_private(path, data):
    """A new 0600 file (never overwritten, never through a symlink)."""
    path = Path(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    return path


def write_json(path, payload):
    return write_private(path, (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode())


def replace_private(path, data):
    """Write `data` to `path` atomically (0600): the baseline is replaced."""
    path = Path(path)
    ensure_private_dir(path.parent)
    temp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    write_private(temp, data)
    os.replace(temp, path)
    return path


# --- WER -----------------------------------------------------------------------


def _errors(results):
    total = NO_ERRORS
    for r in results:
        total = total + WordErrors(r.reference_words, r.substitutions, r.deletions, r.insertions)
    return total


def _wer_row(results, target=None):
    errors = _errors(results)
    return {
        "clips": len(results),
        "reference_words": errors.reference_words,
        "substitutions": errors.substitutions,
        "deletions": errors.deletions,
        "insertions": errors.insertions,
        "failed": sum(1 for r in results if r.error),
        "wer": round(errors.wer, 4) if results else None,
        "target": target,
    }


def wer_summary(results):
    """Per condition and per target group, plus the verdict: `fail` if a
    targeted group is above its target, `incomplete` if one has no clips
    (or a clip failed), else `pass`."""
    by_condition = {}
    for r in results:
        by_condition.setdefault(r.condition, []).append(r)
    conditions = {c: _wer_row(rows) for c, rows in sorted(by_condition.items())}
    groups, verdict = {}, PASS
    for name, (members, target) in WER_GROUPS.items():
        rows = [r for c in members for r in by_condition.get(c, [])]
        row = _wer_row(rows, target)
        if target is not None:
            if not rows:
                row["verdict"] = INCOMPLETE
            elif row["wer"] > target:
                row["verdict"] = FAIL
            elif row["failed"]:
                row["verdict"] = INCOMPLETE
            else:
                row["verdict"] = PASS
            if row["verdict"] == FAIL:
                verdict = FAIL
            elif row["verdict"] == INCOMPLETE and verdict == PASS:
                verdict = INCOMPLETE
        groups[name] = row
    return {"conditions": conditions, "groups": groups, "verdict": verdict}


def run_wer(
    *,
    clips,
    owner,
    workspace,
    run_id,
    tts=True,
    stt_provider=None,
    tts_provider=None,
    baseline=None,
    pipeline=None,
):
    """The replay run over `[(Entry, wav)]` -> the result payload (numbers,
    per-clip rows, latency, baseline comparison, verdict)."""
    stt = tts_port = None
    if pipeline is None:
        stt = replay.GatewaySTT(
            actor=owner,
            workspace=workspace,
            run_id=run_id,
            language=settings.VOICE_STT_LANGUAGE,
            provider=stt_provider,
        )
        if tts:
            tts_port = replay.GatewayTTS(
                actor=owner,
                workspace=workspace,
                run_id=run_id,
                provider=tts_provider,
                max_seconds=settings.VOICE_TTS_MAX_SECONDS,
            )
        pipeline = replay.Pipeline(stt=stt, tts=tts_port)
    results = replay.replay(pipeline, clips)
    summary = wer_summary(results)
    latency = replay.latency_stats(results, replay.LATENCY_STAGES)
    regressions = replay.compare_to_baseline(latency, baseline) if baseline else []
    verdict = summary["verdict"]
    if regressions:
        verdict = FAIL
    return {
        "kind": "voice_eval",
        "run_id": run_id,
        "created_at": timezone.now().isoformat(),
        "workspace": str(workspace.pk),
        "stt_language": settings.VOICE_STT_LANGUAGE,
        "stt_providers": sorted(getattr(stt, "providers", ()) or ()),
        "tts_providers": sorted(getattr(tts_port, "providers", ()) or ()),
        "clips": len(results),
        "conditions": summary["conditions"],
        "groups": summary["groups"],
        "wer_verdict": summary["verdict"],
        "latency": latency,
        "baseline_run_id": (baseline or {}).get("run_id"),
        "regressions": [
            {"stage": s, "baseline_p50": b, "current_p50": c} for s, b, c in regressions
        ],
        "verdict": verdict,
        "results": [r.as_dict() for r in results],
    }


# --- the TTS listening test (A2/A4) ------------------------------------------------


def tts_sheet_items(entries, limit):
    """Distinct Turkish (and code-switched) references, manifest order."""
    seen, items = set(), []
    for entry in entries:
        if entry.language != "tr" or entry.reference in seen:
            continue
        seen.add(entry.reference)
        items.append(entry)
        if len(items) >= limit:
            break
    return items


def write_tts_sheet(*, entries, owner, workspace, run_id, results_dir, provider=None, limit=20):
    """Synthesize each item through `voice.eval_tts` (language `tr`, the
    A2 route unless `provider` pins a row) into `<results>/tts-<run>/`,
    plus `ratings.csv` for the owner to fill in (1-5 per column)."""
    items = tts_sheet_items(entries, limit)
    if not items:
        raise EvalError("the manifest has no Turkish reference to synthesize")
    directory = ensure_private_dir(Path(expand(results_dir)) / f"tts-{run_id}")
    port = replay.GatewayTTS(
        actor=owner,
        workspace=workspace,
        run_id=run_id,
        provider=provider,
        max_seconds=settings.VOICE_TTS_MAX_SECONDS,
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(TTS_SHEET_COLUMNS)
    for number, entry in enumerate(items, start=1):
        text = entry.reference[: settings.VOICE_TTS_MAX_CHARS]
        wav, _ms = port(text, language="tr", condition=entry.condition)
        name = f"{number:03d}.wav"
        write_private(directory / name, wav)
        writer.writerow([name, entry.condition, text, "", ""])
    sheet = write_private(directory / "ratings.csv", buffer.getvalue().encode("utf-8"))
    return sheet, sorted(port.providers)


def score_tts_sheet(path):
    """The owner's filled `ratings.csv` -> means and the >= 4/5 verdict."""
    try:
        text = Path(expand(path)).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise EvalError(f"{path}: cannot be read") from exc
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows or tuple(rows[0].keys()) != TTS_SHEET_COLUMNS:
        raise EvalError(f"{path}: not a voice_eval ratings sheet")
    scores = {"intelligibility": [], "naturalness": []}
    for number, row in enumerate(rows, start=2):
        for column, values in scores.items():
            value = (row.get(column) or "").strip()
            if value not in {"1", "2", "3", "4", "5"}:
                raise EvalError(f"{path}: line {number}: {column} must be 1-5")
            values.append(int(value))
    means = {column: round(sum(v) / len(v), 2) for column, v in scores.items()}
    verdict = PASS if all(m >= TTS_MIN_RATING for m in means.values()) else FAIL
    return {"items": len(rows), **means, "target": TTS_MIN_RATING, "verdict": verdict}


# --- A5 latency from the live turns' traces -----------------------------------------


def trace_latency(*, owner, workspace, days=30, source=None):
    """p50/p95 per stage over the owner's `VoiceTurnTrace` rows (through
    `for_actor()`), and the A5 verdict on `playback_start_ms` (end of
    speech -> first audio): p50 <= 1.5 s and p95 <= 2.5 s."""
    rows = VoiceTurnTrace.objects.for_actor(owner, workspace=workspace.pk).filter(
        created_at__gte=timezone.now() - timedelta(days=days)
    )
    if source:
        rows = rows.filter(source=source)
    values = list(rows.values(*STAGES))
    stats = replay.latency_stats(values, STAGES)
    headline = stats["playback_start_ms"]
    if not headline["n"]:
        verdict = INCOMPLETE
    elif headline["p50"] <= LATENCY_TARGET_P50_MS and headline["p95"] <= LATENCY_TARGET_P95_MS:
        verdict = PASS
    else:
        verdict = FAIL
    return {
        "kind": "voice_eval_traces",
        "days": days,
        "source": source or "all",
        "turns": len(values),
        "stages": stats,
        "target_p50_ms": LATENCY_TARGET_P50_MS,
        "target_p95_ms": LATENCY_TARGET_P95_MS,
        "verdict": verdict,
    }
