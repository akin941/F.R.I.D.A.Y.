"""The eval-run read shape (brief D9): the metric fields only."""

from memory.models import MemoryEvalRun

RUN_FIELDS = (
    "id",
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


def _plain(value):
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)  # UUID, Decimal


# Stored as "" (no NULL strings), read back as the result file's null.
_NULLABLE_STRINGS = {"compression_purpose", "arm", "verdict"}


def serialize_run(run: MemoryEvalRun):
    data = {name: _plain(getattr(run, name)) for name in RUN_FIELDS}
    for name in _NULLABLE_STRINGS:
        data[name] = data[name] or None
    return data
