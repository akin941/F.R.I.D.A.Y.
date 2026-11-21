"""The fact object of a learned candidate (brief D4), validated in one place.

Episode extraction schema v2 and the weekly reflection (branch 2) both emit
`fact` objects of the shape::

    {"subject": {"kind", "name"}, "predicate",
     "object": {"kind", "name"} | {"text"}, "valid_from"}

`validate_fact_object` is strict: unknown or missing keys, a Graphify-only
predicate or entity kind, an over-long name or a malformed date are all
`FactCandidateInvalid` (with a stable `reason`), never silently repaired.
The +-1 day clamp of `valid_from` against the cited episode is the caller's
(it needs the episode); this module is pure.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

from memory.models import (
    LEARNED_ENTITY_KINDS,
    LEARNED_PREDICATES,
    MemoryEntity,
    MemoryFact,
)

_WHITESPACE = re.compile(r"\s+")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

FACT_KEYS = frozenset({"subject", "predicate", "object", "valid_from"})
ENTITY_KEYS = frozenset({"kind", "name"})
TEXT_OBJECT_KEYS = frozenset({"text"})


class FactCandidateInvalid(ValueError):
    """A fact object that does not match the schema. `reason` is a stable
    counter key (`fact_schema`, `fact_predicate`, `fact_kind`, ...)."""

    def __init__(self, reason, message=""):
        super().__init__(message or reason)
        self.reason = reason


def normalise_name(name):
    """The `normalised_name` form (D2): NFKC, lowercased, single spaces."""
    text = unicodedata.normalize("NFKC", str(name or ""))
    return _WHITESPACE.sub(" ", text).strip().lower()


@dataclass(frozen=True)
class EntityRef:
    kind: str
    name: str


@dataclass(frozen=True)
class FactObject:
    subject: EntityRef
    predicate: str
    object_entity: EntityRef | None
    object_text: str
    valid_from: date


def _entity(value, where):
    if not isinstance(value, dict) or set(value) != ENTITY_KEYS:
        raise FactCandidateInvalid("fact_schema", f"`{where}` must have exactly kind and name.")
    kind, name = value["kind"], value["name"]
    if not isinstance(kind, str) or kind not in LEARNED_ENTITY_KINDS:
        raise FactCandidateInvalid("fact_kind", f"`{where}.kind` is not a learned entity kind.")
    if not isinstance(name, str):
        raise FactCandidateInvalid("fact_schema", f"`{where}.name` must be a string.")
    name = _WHITESPACE.sub(" ", name).strip()
    if not name or len(name) > MemoryEntity.MAX_NAME_CHARS:
        raise FactCandidateInvalid("fact_name", f"`{where}.name` is empty or too long.")
    return EntityRef(kind=kind, name=name)


def _valid_from(value):
    if not isinstance(value, str) or not _ISO_DATE.match(value):
        raise FactCandidateInvalid("fact_valid_from", "`valid_from` must be an ISO date.")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise FactCandidateInvalid("fact_valid_from", "`valid_from` is not a date.") from exc


def validate_fact_object(value):
    """Return a `FactObject` or raise `FactCandidateInvalid`."""
    if not isinstance(value, dict) or set(value) != FACT_KEYS:
        raise FactCandidateInvalid("fact_schema", "A fact needs exactly the v2 keys.")
    subject = _entity(value["subject"], "subject")
    predicate = value["predicate"]
    if not isinstance(predicate, str) or predicate not in LEARNED_PREDICATES:
        raise FactCandidateInvalid("fact_predicate", "`predicate` is not a learned predicate.")
    obj = value["object"]
    object_entity, object_text = None, ""
    if isinstance(obj, dict) and set(obj) == TEXT_OBJECT_KEYS:
        text = obj["text"]
        if not isinstance(text, str):
            raise FactCandidateInvalid("fact_schema", "`object.text` must be a string.")
        object_text = _WHITESPACE.sub(" ", text).strip()
        if not object_text or len(object_text) > MemoryFact.MAX_OBJECT_TEXT_CHARS:
            raise FactCandidateInvalid("fact_object_text", "`object.text` is empty or too long.")
    else:
        object_entity = _entity(obj, "object")
    return FactObject(
        subject=subject,
        predicate=predicate,
        object_entity=object_entity,
        object_text=object_text,
        valid_from=_valid_from(value["valid_from"]),
    )
