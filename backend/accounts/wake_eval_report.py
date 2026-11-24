"""Reading a `friday-voice wake-eval` report (Phase 11 brief D13, D11):
the input of `manage.py voice_wake_eval record --from-report`.

The report (`node/friday-voice/friday_voice/wake_eval.py`, schema
`friday-voice/wake-eval/v1`) carries numbers and hashes only. Before it
becomes a `VoiceWakeEvaluation` row it is refused unless:

- it is exactly the schema (no unknown key anywhere: a file name, a path or
  a transcript has nowhere to hide);
- `report_sha256` is the sha256 of the canonical JSON of everything else,
  and each section's `manifest_sha256` that of its file list (a number
  edited by hand is a hash mismatch);
- its model hash is well-formed and, when the owner names the model they
  meant to record (`--model-sha256`), equal to it;
- it is internally consistent: `hours` is the files' total length, the
  false accepts are the files' detections, FAR is false accepts / hours,
  the positives are the files, the false rejects are the files without a
  detection, FRR is false rejects / positives; no file appears twice.

Pure: no database, no settings. `accounts.voice_live.record_evaluation`
re-validates the numbers and writes the row.
"""

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation

REPORT_SCHEMA = "friday-voice/wake-eval/v1"
MAX_REPORT_BYTES = 4 * 1024 * 1024
MAX_FILES = 100_000
HOURS_QUANTUM = Decimal("0.001")
RATE_QUANTUM = Decimal("0.0001")
MS_PER_HOUR = 3_600_000
SHA_RE = re.compile(r"[0-9a-f]{64}")
DECIMAL_RE = re.compile(r"[0-9]{1,6}\.[0-9]{3,4}")

TOP_KEYS = {"schema", "model_sha256", "threshold", "refractory_ms", "frame_ms", "report_sha256"}
NEGATIVE_KEYS = {"files", "manifest_sha256", "hours", "false_accepts", "far_per_hour"}
POSITIVES_KEYS = {"files", "manifest_sha256", "positives", "false_rejects", "frr"}
FILE_KEYS = {"sha256", "ms", "detections"}
NEGATIVE_KINDS = ("household", "negative_set")
POSITIVES_KIND = "positives"


class ReportRefused(ValueError):
    """The report is malformed, tampered or inconsistent: nothing is written."""


def canonical(document) -> bytes:
    """The bytes the report's hashes are taken over (byte-identical to
    `friday_voice.wake_eval.canonical`)."""
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "ascii"
    )


def _sha(document) -> str:
    return hashlib.sha256(canonical(document)).hexdigest()


def _int(name, value, *, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ReportRefused(f"{name} must be an integer >= {minimum}")
    return value


def _decimal_text(name, value, places):
    if not isinstance(value, str) or not DECIMAL_RE.fullmatch(value):
        raise ReportRefused(f"{name} must be a decimal string")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:  # pragma: no cover -- the regex already holds
        raise ReportRefused(f"{name} must be a decimal string") from exc
    if number != number.quantize(Decimal(1).scaleb(-places)) or len(value.split(".")[1]) != places:
        raise ReportRefused(f"{name} must have exactly {places} decimals")
    return number


def _sha_field(name, value):
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise ReportRefused(f"{name} must be 64 lowercase hex characters")
    return value


def _keys(name, section, expected):
    if not isinstance(section, dict):
        raise ReportRefused(f"{name} must be an object")
    if set(section) != expected:
        raise ReportRefused(f"{name} has unexpected or missing fields")


def _files(name, files):
    if not isinstance(files, list) or not files:
        raise ReportRefused(f"{name}.files must be a non-empty list")
    if len(files) > MAX_FILES:
        raise ReportRefused(f"{name}.files is too long")
    hashes = []
    for entry in files:
        _keys(f"{name}.files[]", entry, FILE_KEYS)
        hashes.append(_sha_field(f"{name}.files[].sha256", entry["sha256"]))
        _int(f"{name}.files[].ms", entry["ms"], minimum=1)
        _int(f"{name}.files[].detections", entry["detections"])
    if len(set(hashes)) != len(hashes):
        raise ReportRefused(f"{name}: the same recording appears twice")
    if hashes != sorted(hashes):
        raise ReportRefused(f"{name}.files must be sorted by sha256")
    return files


def _manifest(name, section):
    if _sha_field(f"{name}.manifest_sha256", section["manifest_sha256"]) != _sha(section["files"]):
        raise ReportRefused(f"{name}.manifest_sha256 does not match its files (tampered)")


def _negative(section):
    _keys("negative", section, NEGATIVE_KEYS)
    files = _files("negative", section["files"])
    _manifest("negative", section)
    hours = _decimal_text("negative.hours", section["hours"], 3)
    total_ms = sum(entry["ms"] for entry in files)
    if hours != (Decimal(total_ms) / MS_PER_HOUR).quantize(HOURS_QUANTUM):
        raise ReportRefused("negative.hours is not the files' total length")
    if hours <= 0:
        raise ReportRefused("negative.hours must be positive")
    accepts = _int("negative.false_accepts", section["false_accepts"])
    if accepts != sum(entry["detections"] for entry in files):
        raise ReportRefused("negative.false_accepts is not the files' detections")
    far = _decimal_text("negative.far_per_hour", section["far_per_hour"], 4)
    if far != (Decimal(accepts) / hours).quantize(RATE_QUANTUM):
        raise ReportRefused("negative.far_per_hour is not false_accepts / hours")
    return {"hours": str(hours), "false_accepts": accepts, "far_per_hour": str(far)}


def _positives(section):
    _keys("positives", section, POSITIVES_KEYS)
    files = _files("positives", section["files"])
    _manifest("positives", section)
    count = _int("positives.positives", section["positives"], minimum=1)
    if count != len(files):
        raise ReportRefused("positives.positives is not the number of files")
    rejects = _int("positives.false_rejects", section["false_rejects"])
    if rejects != sum(1 for entry in files if entry["detections"] == 0):
        raise ReportRefused("positives.false_rejects is not the files without a detection")
    frr = _decimal_text("positives.frr", section["frr"], 4)
    if frr != (Decimal(rejects) / Decimal(count)).quantize(RATE_QUANTUM):
        raise ReportRefused("positives.frr is not false_rejects / positives")
    return {"positives": count, "false_rejects": rejects, "frr": str(frr)}


def load(raw: bytes):
    if len(raw) > MAX_REPORT_BYTES:
        raise ReportRefused("the report is too large")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ReportRefused("the report is not JSON") from exc


def evaluation_fields(report, kind, *, expected_model_sha256=None) -> dict:
    """The `record_evaluation` keyword arguments for one row of `kind` from
    a validated report (household / negative_set from its `negative`
    section, positives from its `positives` section)."""
    if not isinstance(report, dict):
        raise ReportRefused("the report must be a JSON object")
    sections = {"negative", "positives"} & set(report)
    if (
        not sections
        or set(report) - TOP_KEYS - {"negative", "positives"}
        or not TOP_KEYS <= set(report)
    ):
        raise ReportRefused("the report has unexpected or missing fields")
    if report["schema"] != REPORT_SCHEMA:
        raise ReportRefused(f"the report schema is not {REPORT_SCHEMA}")
    body = {key: value for key, value in report.items() if key != "report_sha256"}
    if _sha_field("report_sha256", report["report_sha256"]) != _sha(body):
        raise ReportRefused("report_sha256 does not match the report (tampered)")
    model = _sha_field("model_sha256", report["model_sha256"])
    if expected_model_sha256 is not None and model != expected_model_sha256:
        raise ReportRefused("the report measured another model than --model-sha256")
    threshold = _decimal_text("threshold", report["threshold"], 4)
    if not Decimal(0) < threshold <= Decimal(1):
        raise ReportRefused("threshold must be in (0, 1]")
    refractory_ms = _int("refractory_ms", report["refractory_ms"])
    _int("frame_ms", report["frame_ms"], minimum=1)
    fields = {
        "model_sha256": model,
        "kind": kind,
        "threshold": str(threshold),
        "refractory_ms": refractory_ms,
        "report_sha256": report["report_sha256"],
    }
    # Every section present is validated, whichever row is written.
    negative = _negative(report["negative"]) if "negative" in report else None
    positives = _positives(report["positives"]) if "positives" in report else None
    if kind in NEGATIVE_KINDS:
        if negative is None:
            raise ReportRefused(f"a {kind} row needs the report's negative section")
        return fields | negative
    if kind == POSITIVES_KIND:
        if positives is None:
            raise ReportRefused("a positives row needs the report's positives section")
        return fields | positives
    raise ReportRefused("unknown evaluation kind")
