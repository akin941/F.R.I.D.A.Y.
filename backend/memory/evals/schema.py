"""Eval case format v1 (brief D9, D11 "Enabling per purpose").

One JSON object per JSONL line:

    {id, workspace_ref, sensitivity?, tags?, setup?: {memories?, facts?,
     episodes?}, history?, question, expected: {must_contain?,
     must_not_contain?, relevant_ids?}}

Validation is strict: an unknown key at any level, a wrong type, an
out-of-bounds length or count, a dangling reference or a duplicate `ref`
is a `CaseError`. Error messages name the case id, the field path and the
rule, never the case text (a private case's content stays out of logs).

Setup rows carry a case-local `ref` (`m1`, `f-old`, ...); `relevant_ids`
and a fact's `superseded_by` name refs, and the runner maps them to the
rows it creates. A setup row may name its own `workspace_ref` (a leak trap:
a firm row next to a personal question); it defaults to the case's.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from memory.models import LEARNED_ENTITY_KINDS, LEARNED_PREDICATES, IngestionSource, Memory

CASE_FORMAT_VERSION = 1

MAX_ID_CHARS = 64
MAX_REF_CHARS = 40
MAX_TAGS = 10
MAX_TAG_CHARS = 40
MAX_QUESTION_CHARS = 2000
MAX_PHRASES = 10
MAX_NOT_PHRASES = 20
MAX_PHRASE_CHARS = 200
MAX_RELEVANT_IDS = 20
MAX_MEMORIES = 40
MAX_FACTS = 40
MAX_EPISODES = 20
MAX_TITLE_CHARS = 255
MAX_CONTENT_CHARS = 4000
MAX_ENTITY_NAME_CHARS = 200
MAX_STATEMENT_CHARS = 500
MAX_QUOTE_CHARS = 300
MAX_EPISODE_KIND_CHARS = 40
MAX_EPISODE_TEXT_CHARS = 4000
# D11: optional multi-turn history before the question.
MAX_HISTORY_MESSAGES = 12
MAX_HISTORY_CHARS = 4000
# D11: a `compression` case must give the compressor something to compress.
COMPRESSION_TAG = "compression"
COMPRESSION_MIN_ASSISTANT_MESSAGES = 2
COMPRESSION_MIN_ASSISTANT_CHARS = 2000
TEMPORAL_TAG = "temporal"

PERSONAL_REF = "personal"

_ID = re.compile(rf"^[a-z0-9][a-z0-9._-]{{0,{MAX_ID_CHARS - 1}}}$")
_SLUG = re.compile(rf"^[a-z0-9][a-z0-9_-]{{0,{MAX_REF_CHARS - 1}}}$")
_TAG = re.compile(rf"^[a-z0-9][a-z0-9_-]{{0,{MAX_TAG_CHARS - 1}}}$")

SENSITIVITIES = ("normal", "private")
MEMORY_TYPES = tuple(Memory.Type.values)
EPISODE_SOURCES = tuple(IngestionSource.Kind.values)
FACT_PREDICATES = tuple(sorted(str(p) for p in LEARNED_PREDICATES))
ENTITY_KINDS = tuple(sorted(str(k) for k in LEARNED_ENTITY_KINDS))

_CASE_KEYS = {
    "id",
    "workspace_ref",
    "sensitivity",
    "tags",
    "setup",
    "history",
    "question",
    "expected",
}
_CASE_REQUIRED = {"id", "workspace_ref", "question", "expected"}
_SETUP_KEYS = {"memories", "facts", "episodes"}
_EXPECTED_KEYS = {"must_contain", "must_not_contain", "relevant_ids"}
_MEMORY_KEYS = {"ref", "type", "title", "content", "sensitivity", "workspace_ref", "updated_at"}
_MEMORY_REQUIRED = {"ref", "type", "title", "content"}
_FACT_KEYS = {
    "ref",
    "subject",
    "predicate",
    "object",
    "object_text",
    "statement",
    "valid_from",
    "valid_to",
    "superseded_by",
    "sensitivity",
    "workspace_ref",
    "quote",
}
_FACT_REQUIRED = {"ref", "subject", "predicate", "statement", "valid_from"}
_ENTITY_KEYS = {"name", "kind"}
_EPISODE_KEYS = {
    "ref",
    "source",
    "kind",
    "occurred_at",
    "owner_text",
    "context_text",
    "sensitivity",
    "workspace_ref",
}
_EPISODE_REQUIRED = {"ref", "source", "kind", "occurred_at", "owner_text"}
_HISTORY_KEYS = {"role", "content"}
_HISTORY_ROLES = ("user", "assistant")


class CaseError(ValueError):
    """A case failed validation. The message never quotes case text."""


@dataclass(frozen=True)
class EntityRef:
    name: str
    kind: str


@dataclass(frozen=True)
class SetupMemory:
    ref: str
    type: str
    title: str
    content: str
    sensitivity: str
    workspace_ref: str
    updated_at: datetime | date | None


@dataclass(frozen=True)
class SetupFact:
    ref: str
    subject: EntityRef
    predicate: str
    object: EntityRef | None
    object_text: str
    statement: str
    valid_from: datetime | date
    valid_to: datetime | date | None
    superseded_by: str | None
    sensitivity: str
    workspace_ref: str
    quote: str


@dataclass(frozen=True)
class SetupEpisode:
    ref: str
    source: str
    kind: str
    occurred_at: datetime | date
    owner_text: str
    context_text: str
    sensitivity: str
    workspace_ref: str


@dataclass(frozen=True)
class Expected:
    must_contain: tuple[str, ...] = ()
    must_not_contain: tuple[str, ...] = ()
    relevant_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvalCase:
    id: str
    workspace_ref: str
    sensitivity: str
    tags: tuple[str, ...]
    memories: tuple[SetupMemory, ...]
    facts: tuple[SetupFact, ...]
    episodes: tuple[SetupEpisode, ...]
    history: tuple[dict, ...]
    question: str
    expected: Expected
    workspace_refs: frozenset[str] = field(default=frozenset())

    @property
    def is_private(self):
        return self.sensitivity == "private"

    @property
    def is_temporal(self):
        return TEMPORAL_TAG in self.tags


class _Checker:
    def __init__(self, case_id):
        self.case_id = case_id

    def fail(self, path, rule):
        raise CaseError(f"case {self.case_id!r}: {path}: {rule}")

    def obj(self, value, path, allowed, required):
        if not isinstance(value, dict):
            self.fail(path, "must be an object")
        unknown = sorted(set(value) - allowed)
        if unknown:
            self.fail(path, f"unknown key(s) {', '.join(map(repr, unknown))}")
        missing = sorted(required - set(value))
        if missing:
            self.fail(path, f"missing key(s) {', '.join(map(repr, missing))}")
        return value

    def text(self, value, path, max_chars, *, allow_empty=False):
        if not isinstance(value, str):
            self.fail(path, "must be a string")
        if not allow_empty and not value.strip():
            self.fail(path, "must not be empty")
        if len(value) > max_chars:
            self.fail(path, f"longer than {max_chars} characters")
        if "\x00" in value:
            self.fail(path, "contains a NUL character")
        return value

    def pattern(self, value, path, regex, what):
        if not isinstance(value, str) or not regex.match(value):
            self.fail(path, f"must be {what}")
        return value

    def choice(self, value, path, choices):
        if not isinstance(value, str) or value not in choices:
            self.fail(path, f"must be one of {', '.join(choices)}")
        return value

    def items(self, value, path, max_items):
        if not isinstance(value, list):
            self.fail(path, "must be a list")
        if len(value) > max_items:
            self.fail(path, f"more than {max_items} items")
        return value

    def when(self, value, path):
        """An ISO date (`2026-10-01`) or an aware ISO datetime."""
        if not isinstance(value, str):
            self.fail(path, "must be an ISO date or datetime string")
        try:
            if len(value) == 10:
                return date.fromisoformat(value)
            parsed = datetime.fromisoformat(value)
        except ValueError:
            self.fail(path, "must be an ISO date or datetime string")
        if parsed.tzinfo is None:
            self.fail(path, "a datetime must carry a UTC offset")
        return parsed


def _instant(value):
    """Comparable form of a date or datetime (dates at midnight UTC; only
    used to order a validity window inside one case)."""
    if isinstance(value, datetime):
        return value.timestamp()
    return datetime(value.year, value.month, value.day).timestamp()


def validate_case(raw):
    """Validate one decoded JSON case and return an `EvalCase`."""
    if not isinstance(raw, dict):
        raise CaseError("a case must be a JSON object")
    case_id = raw.get("id")
    if not isinstance(case_id, str) or not _ID.match(case_id):
        raise CaseError("case id: must be a lowercase slug of at most 64 characters")
    c = _Checker(case_id)
    c.obj(raw, "case", _CASE_KEYS, _CASE_REQUIRED)
    workspace_ref = c.pattern(raw["workspace_ref"], "workspace_ref", _SLUG, "a lowercase slug")
    sensitivity = c.choice(raw.get("sensitivity", "normal"), "sensitivity", SENSITIVITIES)
    tags = c.items(raw.get("tags", []), "tags", MAX_TAGS)
    for index, tag in enumerate(tags):
        c.pattern(tag, f"tags[{index}]", _TAG, "a lowercase slug")
    if len(set(tags)) != len(tags):
        c.fail("tags", "duplicate tag")
    question = c.text(raw["question"], "question", MAX_QUESTION_CHARS)

    setup = c.obj(raw.get("setup", {}), "setup", _SETUP_KEYS, set())
    refs = {}
    workspace_refs = {workspace_ref}

    def claim_ref(value, path, kind):
        ref = c.pattern(value, path, _SLUG, "a lowercase slug")
        if ref in refs:
            c.fail(path, "duplicate ref")
        refs[ref] = kind
        return ref

    def row_workspace(row, path):
        ref = c.pattern(row.get("workspace_ref", workspace_ref), path, _SLUG, "a lowercase slug")
        workspace_refs.add(ref)
        return ref

    def row_sensitivity(row, path):
        value = c.choice(row.get("sensitivity", "normal"), path, SENSITIVITIES)
        # D9: every setup row of a `private` case is stored `private`.
        return "private" if sensitivity == "private" else value

    memories = []
    for index, row in enumerate(c.items(setup.get("memories", []), "memories", MAX_MEMORIES)):
        path = f"setup.memories[{index}]"
        c.obj(row, path, _MEMORY_KEYS, _MEMORY_REQUIRED)
        memories.append(
            SetupMemory(
                ref=claim_ref(row["ref"], f"{path}.ref", "memory"),
                type=c.choice(row["type"], f"{path}.type", MEMORY_TYPES),
                title=c.text(row["title"], f"{path}.title", MAX_TITLE_CHARS),
                content=c.text(row["content"], f"{path}.content", MAX_CONTENT_CHARS),
                sensitivity=row_sensitivity(row, f"{path}.sensitivity"),
                workspace_ref=row_workspace(row, f"{path}.workspace_ref"),
                updated_at=(
                    c.when(row["updated_at"], f"{path}.updated_at") if "updated_at" in row else None
                ),
            )
        )

    def entity(value, path):
        c.obj(value, path, _ENTITY_KEYS, _ENTITY_KEYS)
        return EntityRef(
            name=c.text(value["name"], f"{path}.name", MAX_ENTITY_NAME_CHARS),
            kind=c.choice(value["kind"], f"{path}.kind", ENTITY_KINDS),
        )

    facts = []
    for index, row in enumerate(c.items(setup.get("facts", []), "facts", MAX_FACTS)):
        path = f"setup.facts[{index}]"
        c.obj(row, path, _FACT_KEYS, _FACT_REQUIRED)
        has_object, has_text = "object" in row, "object_text" in row
        if has_object == has_text:
            c.fail(path, "needs exactly one of 'object' and 'object_text'")
        statement = c.text(row["statement"], f"{path}.statement", MAX_STATEMENT_CHARS)
        valid_from = c.when(row["valid_from"], f"{path}.valid_from")
        valid_to = c.when(row["valid_to"], f"{path}.valid_to") if "valid_to" in row else None
        if valid_to is not None and _instant(valid_to) < _instant(valid_from):
            c.fail(f"{path}.valid_to", "earlier than valid_from")
        superseded_by = None
        if "superseded_by" in row:
            superseded_by = c.pattern(
                row["superseded_by"], f"{path}.superseded_by", _SLUG, "a lowercase slug"
            )
            if valid_to is None:
                c.fail(f"{path}.superseded_by", "a superseded fact needs a valid_to")
        facts.append(
            SetupFact(
                ref=claim_ref(row["ref"], f"{path}.ref", "fact"),
                subject=entity(row["subject"], f"{path}.subject"),
                predicate=c.choice(row["predicate"], f"{path}.predicate", FACT_PREDICATES),
                object=entity(row["object"], f"{path}.object") if has_object else None,
                object_text=(
                    c.text(row["object_text"], f"{path}.object_text", MAX_STATEMENT_CHARS)
                    if has_text
                    else ""
                ),
                statement=statement,
                valid_from=valid_from,
                valid_to=valid_to,
                superseded_by=superseded_by,
                sensitivity=row_sensitivity(row, f"{path}.sensitivity"),
                workspace_ref=row_workspace(row, f"{path}.workspace_ref"),
                quote=(
                    c.text(row["quote"], f"{path}.quote", MAX_QUOTE_CHARS)
                    if "quote" in row
                    else statement[:MAX_QUOTE_CHARS]
                ),
            )
        )

    by_ref = {f.ref: f for f in facts}
    for index, fact in enumerate(facts):
        if fact.superseded_by is None:
            continue
        path = f"setup.facts[{index}].superseded_by"
        newer = by_ref.get(fact.superseded_by)
        if newer is None or newer is fact:
            c.fail(path, "must name another fact of the case")
        if (newer.workspace_ref, newer.subject, newer.predicate) != (
            fact.workspace_ref,
            fact.subject,
            fact.predicate,
        ):
            c.fail(path, "must name a fact of the same workspace, subject and predicate")

    episodes = []
    for index, row in enumerate(c.items(setup.get("episodes", []), "episodes", MAX_EPISODES)):
        path = f"setup.episodes[{index}]"
        c.obj(row, path, _EPISODE_KEYS, _EPISODE_REQUIRED)
        episodes.append(
            SetupEpisode(
                ref=claim_ref(row["ref"], f"{path}.ref", "episode"),
                source=c.choice(row["source"], f"{path}.source", EPISODE_SOURCES),
                kind=c.text(row["kind"], f"{path}.kind", MAX_EPISODE_KIND_CHARS),
                occurred_at=c.when(row["occurred_at"], f"{path}.occurred_at"),
                owner_text=c.text(row["owner_text"], f"{path}.owner_text", MAX_EPISODE_TEXT_CHARS),
                context_text=c.text(
                    row.get("context_text", ""),
                    f"{path}.context_text",
                    MAX_EPISODE_TEXT_CHARS,
                    allow_empty=True,
                ),
                sensitivity=row_sensitivity(row, f"{path}.sensitivity"),
                workspace_ref=row_workspace(row, f"{path}.workspace_ref"),
            )
        )

    history = []
    for index, message in enumerate(
        c.items(raw.get("history", []), "history", MAX_HISTORY_MESSAGES)
    ):
        path = f"history[{index}]"
        c.obj(message, path, _HISTORY_KEYS, _HISTORY_KEYS)
        history.append(
            {
                "role": c.choice(message["role"], f"{path}.role", _HISTORY_ROLES),
                "content": c.text(message["content"], f"{path}.content", MAX_HISTORY_CHARS),
            }
        )
    if COMPRESSION_TAG in tags:
        assistant = [m["content"] for m in history if m["role"] == "assistant"]
        if (
            len(assistant) < COMPRESSION_MIN_ASSISTANT_MESSAGES
            or sum(len(text) for text in assistant) < COMPRESSION_MIN_ASSISTANT_CHARS
        ):
            c.fail(
                "history",
                f"a '{COMPRESSION_TAG}' case needs at least "
                f"{COMPRESSION_MIN_ASSISTANT_MESSAGES} assistant messages totalling "
                f"{COMPRESSION_MIN_ASSISTANT_CHARS} characters",
            )

    expected_raw = c.obj(raw["expected"], "expected", _EXPECTED_KEYS, set())

    def phrases(key, max_items):
        values = c.items(expected_raw.get(key, []), f"expected.{key}", max_items)
        return tuple(
            c.text(v, f"expected.{key}[{i}]", MAX_PHRASE_CHARS) for i, v in enumerate(values)
        )

    relevant = c.items(
        expected_raw.get("relevant_ids", []), "expected.relevant_ids", MAX_RELEVANT_IDS
    )
    for index, ref in enumerate(relevant):
        c.pattern(ref, f"expected.relevant_ids[{index}]", _SLUG, "a lowercase slug")
        if ref not in refs:
            c.fail(f"expected.relevant_ids[{index}]", "names no setup row of the case")
    if len(set(relevant)) != len(relevant):
        c.fail("expected.relevant_ids", "duplicate ref")
    expected = Expected(
        must_contain=phrases("must_contain", MAX_PHRASES),
        must_not_contain=phrases("must_not_contain", MAX_NOT_PHRASES),
        relevant_ids=tuple(relevant),
    )

    return EvalCase(
        id=case_id,
        workspace_ref=workspace_ref,
        sensitivity=sensitivity,
        tags=tuple(tags),
        memories=tuple(memories),
        facts=tuple(facts),
        episodes=tuple(episodes),
        history=tuple(history),
        question=question,
        expected=expected,
        workspace_refs=frozenset(workspace_refs),
    )
