"""Skill registry (MVP 8 §1, brief).

A plain Python dict, not a database model -- mirrors `tools/registry.py`'s
established pattern deliberately, not `ai_core.PromptTemplate`/
`PromptVersion`'s: the latter is "immutable-by-discipline" but not
code-enforced (no override prevents editing an existing `PromptVersion`
row). A code-only registry gives a *stronger* self-modification guarantee,
since there is no runtime data structure for an agent to mutate at all --
changing a skill requires a git commit + PR review + deploy, never a
database write.

`SkillStep.kind`/`payload` are validated by this module's own per-kind shape
checks (`_validate_step_shape`, below), duplicated from -- not imported
from -- `agents.planner._parse_step`. Deliberately duplicated: `skills` must
not import `agents` (import-linter contract), the same one-way-dependency
tradeoff already used elsewhere in this codebase for the same reason (e.g.
`agents/planner_service.py`'s own `_TOOL_DESCRIPTIONS` duplicates rather
than imports `assistant`'s tool vocabulary). This branch's tests include
parity cases proving this validator and `agents.planner`'s accept/reject
the exact same set of shapes, so the two implementations can't silently
drift apart.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

MAX_DESCRIPTION_CHARS = 2000
MAX_TITLE_CHARS = 255

_ALLOWED_KINDS = frozenset({"read", "tool_request", "task_draft"})
_TOOL_REQUEST_PAYLOAD_KEYS = {"tool_name"}
_TASK_DRAFT_PAYLOAD_KEYS = {"title", "due_date"}


class SkillValidationError(Exception):
    """Raised by `register_skill` when a `SkillSpec`'s step shapes don't
    conform to the same per-kind payload contract `agents.planner.parse_plan`
    enforces for a freeform LLM plan. A skill fails loud at registration
    time, never silently reaches an `Agent` with a malformed step."""


def _validate_step_shape(step: "SkillStep") -> None:
    """Mirrors `agents.planner._parse_step`'s payload-shape validation
    (duplicated, not imported -- see module docstring). `kind` and
    `description` are already type/value-constrained by `SkillStep`'s own
    dataclass fields at construction; this only re-validates the parts that
    mirror the LLM-plan schema, so registration-time and plan-parse-time
    failures stay provably identical (this branch's parity tests)."""
    if step.kind not in _ALLOWED_KINDS:
        raise SkillValidationError(f"unrecognized step kind: {step.kind!r}")
    if not isinstance(step.description, str) or not step.description.strip():
        raise SkillValidationError("step description is empty/invalid")
    if len(step.description) > MAX_DESCRIPTION_CHARS:
        raise SkillValidationError(f"step description exceeds {MAX_DESCRIPTION_CHARS} chars")

    payload = dict(step.payload)

    if step.kind == "read":
        if payload != {}:
            raise SkillValidationError("read step payload must be empty")
    elif step.kind == "tool_request":
        if set(payload.keys()) != _TOOL_REQUEST_PAYLOAD_KEYS:
            raise SkillValidationError(
                f"unexpected tool_request payload keys: {sorted(payload.keys())}"
            )
        tool_name = payload["tool_name"]
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise SkillValidationError("tool_request payload requires a non-empty tool_name")
    else:  # task_draft
        if set(payload.keys()) != _TASK_DRAFT_PAYLOAD_KEYS:
            raise SkillValidationError(
                f"unexpected task_draft payload keys: {sorted(payload.keys())}"
            )
        title = payload["title"]
        if not isinstance(title, str) or not title.strip():
            raise SkillValidationError("task_draft payload title is empty/invalid")
        if len(title) > MAX_TITLE_CHARS:
            raise SkillValidationError(f"task_draft payload title exceeds {MAX_TITLE_CHARS} chars")
        raw_due_date = payload["due_date"]
        if raw_due_date is not None:
            if not isinstance(raw_due_date, str) or not _ISO_DATE_RE.match(raw_due_date):
                raise SkillValidationError(
                    "task_draft payload due_date must be a YYYY-MM-DD string or null"
                )
            try:
                date.fromisoformat(raw_due_date)
            except ValueError as exc:
                raise SkillValidationError(f"due_date is not a valid ISO date: {exc}") from exc


@dataclass(frozen=True)
class SkillStep:
    kind: str
    description: str
    payload: dict

    def __post_init__(self):
        # Codex round-2 P2 fix: `dict(self.payload)` below coerces ANY
        # iterable (a list, a list of pairs, ...) into a dict -- silently
        # accepting a shape `agents.planner._parse_step` would reject
        # outright (it requires `isinstance(payload, dict)` before doing
        # anything else). Check the type BEFORE coercion, so a non-dict
        # payload fails the same way in both validators (parity tests).
        if not isinstance(self.payload, dict):
            raise SkillValidationError(
                f"SkillStep payload must be a dict, got {type(self.payload).__name__}"
            )
        # Defensive: a plain dict is mutable even on a frozen dataclass
        # (frozen only blocks reassigning the *attribute*, not mutating the
        # dict it points to) -- wrap it read-only so nothing can mutate a
        # step's payload after construction and silently invalidate the
        # SkillSpec-level content_hash (below) that was computed from it.
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@dataclass(frozen=True)
class SkillSpec:
    name: str
    description: str
    trigger_hint: str
    steps: tuple[SkillStep, ...]
    content_hash: str = field(init=False, default="")
    # NEVER supplied by a skill's own definition module -- a skill author
    # writes SkillSpec(name=..., description=..., trigger_hint=...,
    # steps=(...)) with no content_hash argument at all (see the two example
    # skills). __post_init__ below computes and fills it in before the
    # object is ever handed to register_skill().

    def __post_init__(self):
        # Same class of bug as SkillStep.payload above: `steps` is only
        # annotated `tuple[...]`, which Python does not enforce -- a caller
        # passing a list would let later external mutation silently change
        # the step template without changing content_hash. Normalize before
        # hashing, and store the normalized tuple back onto the instance.
        steps = tuple(self.steps)
        object.__setattr__(self, "steps", steps)
        computed = hashlib.sha256(
            json.dumps(
                {
                    "name": self.name,
                    "description": self.description,
                    "trigger_hint": self.trigger_hint,
                    "steps": [
                        {
                            "kind": s.kind,
                            "description": s.description,
                            "payload": dict(s.payload),
                        }
                        for s in steps
                    ],
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        # Standard pattern for a derived field on a frozen dataclass -- NOT
        # dataclasses.replace(), which cannot target an init=False field.
        object.__setattr__(self, "content_hash", computed)


_REGISTRY: dict[str, SkillSpec] = {}


def register_skill(spec: SkillSpec) -> None:
    """Validates every step's per-kind shape (fail loud, before the spec
    ever reaches the registry) then stores it. `content_hash` is already
    set by `SkillSpec.__post_init__` -- no further mutation happens here."""
    for step in spec.steps:
        _validate_step_shape(step)
    _REGISTRY[spec.name] = spec


def unregister_skill(name: str) -> None:
    """Test-only. Production registration happens once, at import time, and
    is permanent for the life of the process."""
    _REGISTRY.pop(name, None)


def get_skill(name: str) -> SkillSpec | None:
    return _REGISTRY.get(name)


def list_skill_names() -> list[str]:
    return sorted(_REGISTRY.keys())


def list_skill_prompt_entries() -> list[tuple[str, str, str]]:
    """(name, description, trigger_hint) per registered skill, for building
    the `agent.skill_match` prompt's vocabulary -- mirrors
    `tools.registry.list_tool_names()`'s role for the router. Sorted for a
    deterministic prompt/test ordering, not registration order."""
    return [
        (spec.name, spec.description, spec.trigger_hint)
        for spec in (_REGISTRY[name] for name in sorted(_REGISTRY.keys()))
    ]


def skill_steps_to_plan_dicts(spec: SkillSpec) -> list[dict]:
    """JSON-safe conversion of a `SkillSpec`'s step template to the same
    `{"kind", "description", "payload"}` dict shape `agents.planner.
    parse_plan()` already produces for a freeform LLM plan (MVP 8 §3) --
    the exact shape `agents.services.finalize_plan` bulk-creates `AgentStep`
    rows from. Callers must go through this rather than reading
    `SkillStep.payload` directly: that field is a `MappingProxyType`
    (immutable, by design -- see `SkillStep.__post_init__`), which is not
    JSON-serializable and would raise inside `AgentStep.payload`'s
    `JSONField` otherwise."""
    return [
        {"kind": step.kind, "description": step.description, "payload": dict(step.payload)}
        for step in spec.steps
    ]
