"""The metrics-only result file (brief D9).

A run writes one JSON object (a compare writes one per arm) to its results
directory, mode 0600, with exactly `RESULT_KEYS`: counts, aggregate rates,
enums and timestamps. No case text, case ids, `workspace_ref` values, tags,
questions, answers, per-case rows or per-workspace breakdown are ever
written. `memory_eval record` refuses a file with any other key (unknown
keys are terminal), so nothing a later version adds can slip into the live
database unreviewed.

`month_spend()` is D9's defence in depth: the `spend_usd` of this month's
result files, which survives a dropped or recreated eval database.
"""

import json
import os
import stat
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.utils import timezone

from memory.models import MemoryEvalRun

RESULT_KEYS = (
    "run_id",
    "set",
    "kind",
    "retrieval_mode",
    "compression_purpose",
    "arm",
    "started_at",
    "finished_at",
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
COUNT_KEYS = ("total", "answered", "no_provider", "budget_stopped")
RATE_KEYS = ("recall_at_10", "leak_rate", "temporal_correctness", "answer_accuracy")
TOKEN_KEYS = ("pre_tokens", "post_tokens")
MAX_RESULT_BYTES = 64 * 1024
MAX_COUNT = 1_000_000


class ResultError(ValueError):
    """A result file is malformed or carries a key outside `RESULT_KEYS`."""


def ensure_dir(directory):
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return directory


def file_name(run_id, arm=None):
    return f"{run_id}-{arm}.json" if arm else f"{run_id}.json"


def write_result(directory, result):
    """Write `result` (exactly `RESULT_KEYS`) as a new 0600 file; returns
    its path. Never overwrites."""
    if set(result) != set(RESULT_KEYS):
        raise ResultError("a result must carry exactly the D9 keys")
    path = ensure_dir(directory) / file_name(result["run_id"], result["arm"])
    data = json.dumps({key: result[key] for key in RESULT_KEYS}, indent=2).encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data + b"\n")
    return path


def read_result_file(path):
    """Read a result file without following a symlink, size-capped."""
    path = Path(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise ResultError(f"{path.name}: cannot be opened ({exc.strerror})") from None
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RESULT_BYTES:
            raise ResultError(f"{path.name}: not a regular file of at most {MAX_RESULT_BYTES} B")
        raw = handle.read(MAX_RESULT_BYTES + 1)
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ResultError(f"{path.name}: not valid JSON") from None


def _when(value, key):
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise ResultError(f"{key}: not an ISO datetime") from None
    if parsed.tzinfo is None:
        raise ResultError(f"{key}: must carry a UTC offset")
    return parsed


def _choice(value, key, choices, *, nullable=False):
    if value is None and nullable:
        return None
    if value not in choices:
        raise ResultError(f"{key}: must be one of {', '.join(choices)}")
    return value


def validate_result(data):
    """The `MemoryEvalRun` field values of a result file, or `ResultError`."""
    if not isinstance(data, dict):
        raise ResultError("a result file must hold one JSON object")
    unknown = sorted(set(data) - set(RESULT_KEYS))
    if unknown:
        raise ResultError(f"unknown key(s) {', '.join(map(repr, unknown))}")
    missing = sorted(set(RESULT_KEYS) - set(data))
    if missing:
        raise ResultError(f"missing key(s) {', '.join(map(repr, missing))}")
    try:
        run_id = uuid.UUID(str(data["run_id"]))
    except ValueError:
        raise ResultError("run_id: not a UUID") from None
    values = {
        "run_id": run_id,
        "set": _choice(data["set"], "set", MemoryEvalRun.Set.values),
        "kind": _choice(data["kind"], "kind", MemoryEvalRun.Kind.values),
        "arm": _choice(data["arm"], "arm", MemoryEvalRun.Arm.values, nullable=True),
        "verdict": _choice(data["verdict"], "verdict", MemoryEvalRun.Verdict.values, nullable=True),
        "started_at": _when(data["started_at"], "started_at"),
        "finished_at": _when(data["finished_at"], "finished_at"),
    }
    mode = data["retrieval_mode"]
    if (
        not isinstance(mode, str)
        or not mode
        or len(mode) > MemoryEvalRun.MAX_MODE_CHARS
        or not mode.replace("_", "").isalnum()
    ):
        raise ResultError("retrieval_mode: must be a short identifier")
    values["retrieval_mode"] = mode
    purpose = data["compression_purpose"]
    if purpose is not None and (
        not isinstance(purpose, str)
        or not purpose
        or len(purpose) > MemoryEvalRun.MAX_PURPOSE_CHARS
        or not purpose.replace(".", "").replace("_", "").isalnum()
    ):
        raise ResultError("compression_purpose: must be null or a purpose name")
    values["compression_purpose"] = purpose or ""
    for key in COUNT_KEYS:
        value = data[key]
        if type(value) is not int or not 0 <= value <= MAX_COUNT:
            raise ResultError(f"{key}: must be a non-negative integer")
        values[key] = value
    for key in TOKEN_KEYS:
        value = data[key]
        if value is not None and (type(value) is not int or value < 0):
            raise ResultError(f"{key}: must be null or a non-negative integer")
        values[key] = value
    for key in RATE_KEYS:
        value = data[key]
        if value is not None and (type(value) not in (int, float) or not 0 <= float(value) <= 1):
            raise ResultError(f"{key}: must be null or a number between 0 and 1")
        values[key] = None if value is None else float(value)
    try:
        spend = Decimal(str(data["spend_usd"]))
    except InvalidOperation:
        raise ResultError("spend_usd: not a decimal amount") from None
    if not spend.is_finite() or spend < 0 or isinstance(data["spend_usd"], bool):
        raise ResultError("spend_usd: must be a non-negative amount")
    values["spend_usd"] = spend
    if values["finished_at"] < values["started_at"]:
        raise ResultError("finished_at: earlier than started_at")
    if values["arm"] is not None and values["kind"] != MemoryEvalRun.Kind.COMPARE:
        raise ResultError("arm: only a compare has arms")
    # The model stores a result file's null strings as "".
    values["arm"] = values["arm"] or ""
    values["verdict"] = values["verdict"] or ""
    counted = values["answered"] + values["no_provider"] + values["budget_stopped"]
    if counted > values["total"]:
        raise ResultError("answered + no_provider + budget_stopped exceeds total")
    return values


def month_spend(directories, now=None):
    """Sum `spend_usd` over the result files of the current month (by
    `started_at`, local time) in `directories`. A file that cannot be read
    or parsed adds nothing (it is reported by `record`, not here)."""
    now = timezone.localtime(now or timezone.now())
    month = (now.year, now.month)
    total = Decimal("0")
    for directory in directories:
        directory = Path(directory)
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            try:
                data = read_result_file(path)
                started = timezone.localtime(_when(data["started_at"], "started_at"))
                spend = Decimal(str(data["spend_usd"]))
            except (ResultError, KeyError, TypeError, InvalidOperation):
                continue
            if (started.year, started.month) == month and spend.is_finite() and spend > 0:
                total += spend
    return total
