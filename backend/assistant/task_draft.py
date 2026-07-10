"""Structured output contract for the `router.task_draft` gateway call
(MVP 6 §3).

A second, focused extraction call made ONLY after `router.intent` has
already classified a message as `task_draft` (`assistant.intent`) — that
call decides *that* a message describes a task; this one decides *what*
the task should contain, mirroring `memory.extraction`'s own
"classify, then extract" shape.

The extraction prompt instructs the model to return exactly one JSON object
of this shape:

    {"title": "short label", "due_date": "YYYY-MM-DD" | null}

`due_date` is null whenever the message names no date, or names one that
cannot be resolved without a reference "today" the caller couldn't supply
(see `assistant.services.extract_task_draft`'s `client_local_date` handling)
— a `null` due date the user can fill in on `/tasks` after confirming is a
safe degradation; a wrong day guessed against server time is not.
"""

import json
from datetime import date

MAX_TITLE_CHARS = 255  # matches Task.title's max_length
_TOP_LEVEL_KEYS = {"title", "due_date"}


class TaskDraftParseError(Exception):
    """The extractor's output did not conform to the schema. Callers must
    treat this exactly like a `router.intent` classification failure —
    degrade the whole turn to `chat`, never raise it to the user (see
    `assistant.services.classify_intent`)."""


def parse_task_draft(raw_text):
    """Strictly parse and validate a `router.task_draft` response.

    Returns a `{"title": str, "due_date": date | None}` dict. Raises
    `TaskDraftParseError` on the first violation found.
    """
    try:
        payload = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise TaskDraftParseError(f"invalid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise TaskDraftParseError("top-level payload must be a JSON object")
    if set(payload.keys()) != _TOP_LEVEL_KEYS:
        raise TaskDraftParseError(f"unexpected top-level keys: {sorted(payload.keys())}")

    title = payload["title"]
    if not isinstance(title, str) or not title.strip():
        raise TaskDraftParseError("title is empty/invalid")
    if len(title) > MAX_TITLE_CHARS:
        raise TaskDraftParseError(f"title exceeds {MAX_TITLE_CHARS} chars")

    raw_due_date = payload["due_date"]
    if raw_due_date is None:
        due_date = None
    elif isinstance(raw_due_date, str):
        try:
            due_date = date.fromisoformat(raw_due_date)
        except ValueError as exc:
            raise TaskDraftParseError(f"due_date is not a valid ISO date: {exc}") from exc
    else:
        raise TaskDraftParseError("due_date must be a string or null")

    return {"title": title, "due_date": due_date}
