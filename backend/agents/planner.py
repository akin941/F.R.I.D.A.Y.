"""Structured output contract for the `agent.plan` gateway call (MVP 7 §2).

The planning prompt instructs the model to return exactly one JSON object
of this shape:

    {"steps": [
        {"kind": "read" | "tool_request" | "task_draft",
         "description": "short label",
         "payload": {...}}
    ]}

`payload`'s shape depends on `kind`:

- `read`: must be `{}` -- the step's `description` is everything the
  execution engine's summarization call needs (agents/planner_service.py,
  branch 3).
- `tool_request`: must be exactly `{"tool_name": "..."}`. This parser
  validates `tool_name`'s *shape* only (non-empty string) -- whether it
  names a tool that actually exists is deliberately NOT checked here,
  mirroring `assistant.intent.parse_intent`'s identical discipline. Real
  resolution against `tools.registry.list_tool_names()` happens in
  `agents.planner_service.create_agent_from_goal`, at planning time (brief
  §2) -- an unresolvable name fails the whole plan there, the same way a
  malformed shape fails it here.
- `task_draft`: must be exactly `{"title": "...", "due_date": "YYYY-MM-DD"
  | null}` -- mirrors `assistant.task_draft.parse_task_draft`'s schema.
  Duplicated here rather than imported: `agents` and `assistant` have no
  import relationship in either direction (pyproject.toml import-linter).

Anything else -- malformed JSON, wrong shape, unexpected keys, an
unrecognized `kind`, a payload shape that doesn't match its `kind` -- is a
parse failure. Per the brief, this always fails the WHOLE plan (`Agent.
status = failed`, `failure_reason = planner_error`) -- callers must catch
`PlanParseError` and never propagate it to the user as a raw exception.
"""

import json
from datetime import date

MAX_DESCRIPTION_CHARS = 2000
MAX_TITLE_CHARS = 255  # matches Task.title's max_length

_ALLOWED_KINDS = frozenset({"read", "tool_request", "task_draft"})
_TOP_LEVEL_KEYS = {"steps"}
_STEP_KEYS = {"kind", "description", "payload"}
_TOOL_REQUEST_PAYLOAD_KEYS = {"tool_name"}
_TASK_DRAFT_PAYLOAD_KEYS = {"title", "due_date"}


class PlanParseError(Exception):
    """The planner's output did not conform to the schema. Callers must
    fail the whole plan (see module docstring) -- never raise this to the
    user or leave the `Agent` in an ambiguous state."""


def _parse_step(raw_step):
    if not isinstance(raw_step, dict):
        raise PlanParseError("each step must be a JSON object")
    if set(raw_step.keys()) != _STEP_KEYS:
        raise PlanParseError(f"unexpected step keys: {sorted(raw_step.keys())}")

    kind = raw_step["kind"]
    if not isinstance(kind, str) or kind not in _ALLOWED_KINDS:
        raise PlanParseError(f"unrecognized step kind: {kind!r}")

    description = raw_step["description"]
    if not isinstance(description, str) or not description.strip():
        raise PlanParseError("step description is empty/invalid")
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise PlanParseError(f"step description exceeds {MAX_DESCRIPTION_CHARS} chars")

    payload = raw_step["payload"]
    if not isinstance(payload, dict):
        raise PlanParseError("step payload must be a JSON object")

    if kind == "read":
        if payload != {}:
            raise PlanParseError("read step payload must be empty")
    elif kind == "tool_request":
        if set(payload.keys()) != _TOOL_REQUEST_PAYLOAD_KEYS:
            raise PlanParseError(f"unexpected tool_request payload keys: {sorted(payload.keys())}")
        tool_name = payload["tool_name"]
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise PlanParseError("tool_request payload requires a non-empty tool_name")
    else:  # task_draft
        if set(payload.keys()) != _TASK_DRAFT_PAYLOAD_KEYS:
            raise PlanParseError(f"unexpected task_draft payload keys: {sorted(payload.keys())}")
        title = payload["title"]
        if not isinstance(title, str) or not title.strip():
            raise PlanParseError("task_draft payload title is empty/invalid")
        if len(title) > MAX_TITLE_CHARS:
            raise PlanParseError(f"task_draft payload title exceeds {MAX_TITLE_CHARS} chars")
        raw_due_date = payload["due_date"]
        if raw_due_date is not None:
            if not isinstance(raw_due_date, str):
                raise PlanParseError("task_draft payload due_date must be a string or null")
            try:
                date.fromisoformat(raw_due_date)
            except ValueError as exc:
                raise PlanParseError(f"due_date is not a valid ISO date: {exc}") from exc

    return {"kind": kind, "description": description, "payload": payload}


def parse_plan(raw_text):
    """Strictly parse and validate an `agent.plan` response.

    Returns an ordered list of `{"kind", "description", "payload"}` dicts
    (possibly empty -- the caller decides what an empty plan means, see
    `agents.services.finalize_plan`). Raises `PlanParseError` on the first
    violation found.
    """
    try:
        payload = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise PlanParseError(f"invalid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise PlanParseError("top-level payload must be a JSON object")
    if set(payload.keys()) != _TOP_LEVEL_KEYS:
        raise PlanParseError(f"unexpected top-level keys: {sorted(payload.keys())}")

    steps = payload["steps"]
    if not isinstance(steps, list):
        raise PlanParseError("steps must be a JSON array")

    return [_parse_step(raw_step) for raw_step in steps]
