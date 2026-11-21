"""Fact reconciliation (Phase 8B brief D4): does a night's fact candidate
state something new, repeat a current fact, or replace one?

Per workspace and data class (the consolidation claim it runs inside), the
night's provenance-checked `fact` candidates go through:

1. **Deterministic dedupe** (no model): a candidate whose subject,
   predicate and object equal another candidate's or a live (current or
   pending) fact's is dropped and counted `duplicate`.
2. **The model** (`memory.fact_reconciliation`, tier `mid`): it sees the
   normal candidates and, fenced as `<existing_fact untrusted>`, the
   current `model_eligible()` facts with the same subject and predicate
   (<= MAX_EXISTING_FACTS). No `private` text enters the call: a `private`
   candidate skips it and stays `new` (`reconcile_private_skipped`), and a
   `private` existing fact is never listed. With no existing fact to
   compare against, no call is made at all.
3. **Code checks** every `supersede`: the named fact must be one listed
   for that candidate (current, same workspace, subject and predicate) and
   start no later than the candidate (the D2 supersede order). Otherwise
   the decision falls back to `new`, counted `supersede_invalid` or
   `supersede_order`.

It **never applies** anything: a validated `supersede` only becomes the
pending proposal's `supersedes` link, and `approve_fact` performs the
supersede when -- and if -- the owner approves. A budget refusal, a gateway
error or a schema error leaves every candidate `new` (counted); nothing is
retried in a loop.
"""

import json
import logging
import uuid
from collections import Counter, defaultdict
from datetime import date

from django.db.models import Q

from ai_core.exceptions import BudgetExceeded, GatewayError
from ai_core.gateway import InferenceGateway
from memory.context import neutralize_fence, strip_invisible_formatting
from memory.graph.candidates import normalise_name
from memory.graph.services import as_instant, find_entity
from memory.ingestion import services
from memory.ingestion.extraction import prompt_version
from memory.models import IngestionSource, MemoryFact

logger = logging.getLogger(__name__)

PROMPT_NAME = "friday_fact_reconciliation"
PURPOSE = "memory.fact_reconciliation"
SCHEMA_VERSION = 1
MAX_EXISTING_FACTS = 50
MAX_FACT_CANDIDATES = 50
ACTION_NEW = "new"
ACTION_SUPERSEDE = "supersede"
ACTION_DUPLICATE = "duplicate"
_ACTIONS = {ACTION_NEW, ACTION_SUPERSEDE, ACTION_DUPLICATE}
_DECISION_KEYS = {"candidate_index", "action", "supersedes_fact_id"}
FENCE_WORDS = ("fact_candidate", "existing_fact")


class ReconciliationParseError(Exception):
    """Schema v1 violation; the candidates fall back to `new`. The message
    never embeds model text."""


def parse_reconciliation(raw_text, candidate_count):
    """Strict schema v1: `{"version": 1, "decisions": [{"candidate_index",
    "action", "supersedes_fact_id"}]}`. Returns `{index: (action,
    fact_id_or_None)}`; a candidate the model left out is `new`."""
    try:
        payload = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        raise ReconciliationParseError("invalid JSON") from None
    if not isinstance(payload, dict) or set(payload) != {"version", "decisions"}:
        raise ReconciliationParseError("unexpected top-level shape")
    version = payload["version"]
    if isinstance(version, bool) or version != SCHEMA_VERSION:
        raise ReconciliationParseError("unsupported schema version")
    decisions = payload["decisions"]
    if not isinstance(decisions, list) or len(decisions) > candidate_count:
        raise ReconciliationParseError("'decisions' must be a list no longer than the candidates")
    parsed = {}
    for position, item in enumerate(decisions):
        if not isinstance(item, dict) or set(item) != _DECISION_KEYS:
            raise ReconciliationParseError(f"decision {position} has unexpected keys")
        index = item["candidate_index"]
        if isinstance(index, bool) or not isinstance(index, int):
            raise ReconciliationParseError(f"decision {position} has a non-integer index")
        if not 0 <= index < candidate_count or index in parsed:
            raise ReconciliationParseError(f"decision {position} has an invalid index")
        action = item["action"]
        if action not in _ACTIONS:
            raise ReconciliationParseError(f"decision {position} has an unknown action")
        target = item["supersedes_fact_id"]
        if action == ACTION_SUPERSEDE:
            if not isinstance(target, str) or not target.strip() or len(target) > 64:
                raise ReconciliationParseError(f"decision {position} names no fact")
        elif target is not None:
            raise ReconciliationParseError(f"decision {position} names a fact without supersede")
        parsed[index] = (action, target.strip() if action == ACTION_SUPERSEDE else None)
    return parsed


# --- keys and lookups --------------------------------------------------------


def _object_key(obj):
    if "text" in obj:
        return ("text", normalise_name(obj["text"]))
    return (obj["kind"], normalise_name(obj["name"]))


def fact_key(candidate):
    fact = candidate["fact"]
    return (
        fact["subject"]["kind"],
        normalise_name(fact["subject"]["name"]),
        fact["predicate"],
        _object_key(fact["object"]),
    )


def _subject(workspace, candidate, terms):
    subject = candidate["fact"]["subject"]
    return find_entity(workspace=workspace, kind=subject["kind"], name=subject["name"], terms=terms)


def _live_identical_exists(workspace, candidate, subject, terms):
    """A live (current or pending) fact with the same subject, predicate and
    object already exists."""
    if subject is None:
        return False
    fact = candidate["fact"]
    live = MemoryFact.objects.filter(
        workspace=workspace, subject=subject, predicate=fact["predicate"]
    )
    live = live.filter(
        Q(status=MemoryFact.Status.PENDING_APPROVAL)
        | Q(status=MemoryFact.Status.APPROVED, valid_to__isnull=True)
    )
    obj = fact["object"]
    if "text" in obj:
        wanted = normalise_name(obj["text"])
        return any(
            normalise_name(text) == wanted
            for text in live.filter(object_entity__isnull=True).values_list(
                "object_text", flat=True
            )
        )
    entity = find_entity(workspace=workspace, kind=obj["kind"], name=obj["name"], terms=terms)
    return entity is not None and live.filter(object_entity=entity).exists()


def dedupe_facts(workspace, candidates):
    """Deterministic dedupe (no model). Returns `(kept, drops)`."""
    terms = services.scrub_terms_for(workspace)
    seen, kept, drops = set(), [], defaultdict(Counter)
    for candidate in candidates:
        key = fact_key(candidate)
        duplicate = key in seen
        seen.add(key)
        if not duplicate:
            duplicate = _live_identical_exists(
                workspace, candidate, _subject(workspace, candidate, terms), terms
            )
        if duplicate:
            drops[candidate["source_id"]]["duplicate"] += 1
            continue
        kept.append(candidate)
    return kept, drops


def existing_facts(workspace, subject, predicate):
    """The current non-`private` learned facts a candidate may supersede:
    `model_eligible()` only (ADR-005, D4)."""
    if subject is None:
        return []
    return list(
        MemoryFact.objects.filter(workspace=workspace, subject=subject, predicate=predicate)
        .model_eligible()
        .select_related("subject", "object_entity")
        .order_by("-valid_from", "id")[:MAX_EXISTING_FACTS]
    )


# --- the call ----------------------------------------------------------------


def _fence(text):
    return neutralize_fence(strip_invisible_formatting(str(text or "")), *FENCE_WORDS)


def _object_line(obj):
    if "text" in obj:
        return f"text: {_fence(obj['text'])}"
    return f"{obj['kind']}: {_fence(obj['name'])}"


def build_reconciliation_messages(prompt_content, candidates, existing_by_index):
    lines = ["<fact_candidates>"]
    for index, candidate in enumerate(candidates):
        fact = candidate["fact"]
        lines.append(
            f'<fact_candidate index="{index}" predicate="{fact["predicate"]}" '
            f'valid_from="{fact["valid_from"]}">'
        )
        lines.append(f"subject {fact['subject']['kind']}: {_fence(fact['subject']['name'])}")
        lines.append(f"object {_object_line(fact['object'])}")
        lines.append(f"statement: {_fence(candidate['content'])}")
        lines.append("</fact_candidate>")
    lines.append("</fact_candidates>")
    lines.append("<existing_facts>")
    listed = set()
    for index in sorted(existing_by_index):
        for fact in existing_by_index[index]:
            if (index, fact.pk) in listed:
                continue
            listed.add((index, fact.pk))
            lines.append(
                f'<existing_fact untrusted id="{fact.pk}" candidate_index="{index}" '
                f'valid_from="{fact.valid_from.date().isoformat()}">'
            )
            lines.append(_fence(fact.statement))
            lines.append("</existing_fact>")
    lines.append("</existing_facts>")
    return [
        {"role": "system", "content": prompt_content},
        {"role": "user", "content": "\n".join(lines)},
    ]


def _all_new(candidates, drops, reason=""):
    for candidate in candidates:
        if reason:
            drops[candidate["source_id"]][reason] += 1
    return [(candidate, None) for candidate in candidates]


def _validated_target(candidate, fact_id, listed):
    """The fact a `supersede` may name: one listed for this candidate (so
    current, same workspace, subject and predicate, non-private), starting
    no later than the candidate. Returns `(fact, reason)`."""
    try:
        wanted = uuid.UUID(str(fact_id))
    except ValueError:
        return None, "supersede_invalid"
    target = next((f for f in listed if f.pk == wanted), None)
    if target is None:
        return None, "supersede_invalid"
    valid_from = as_instant(date.fromisoformat(candidate["fact"]["valid_from"]))
    if valid_from < target.valid_from:
        return None, "supersede_order"
    return target, None


def reconcile(workspace, candidates, personal_data):
    """Decide each fact candidate. Returns `(decisions, drops)`:
    `decisions` is `[(candidate, supersedes_fact_id_or_None)]` in input
    order (duplicates left out), `drops` maps source id -> Counter."""
    drops = defaultdict(Counter)
    if not candidates:
        return [], drops
    candidates, dedupe_drops = dedupe_facts(workspace, candidates)
    for source_id, reasons in dedupe_drops.items():
        drops[source_id].update(reasons)
    private = [c for c in candidates if c.get("sensitivity") == MemoryFact.Sensitivity.PRIVATE]
    normal = [c for c in candidates if c.get("sensitivity") != MemoryFact.Sensitivity.PRIVATE]
    decided = dict.fromkeys(map(id, private))
    for candidate in private:
        drops[candidate["source_id"]]["reconcile_private_skipped"] += 1
    # Beyond the call's bound a candidate stays `new` (the nightly fact cap
    # keeps at most a handful anyway).
    decided.update(dict.fromkeys(map(id, normal[MAX_FACT_CANDIDATES:])))
    normal = normal[:MAX_FACT_CANDIDATES]
    terms = services.scrub_terms_for(workspace)
    existing_by_index = {}
    for index, candidate in enumerate(normal):
        rows = existing_facts(
            workspace, _subject(workspace, candidate, terms), candidate["fact"]["predicate"]
        )
        if rows:
            existing_by_index[index] = rows
    if normal and existing_by_index:
        for candidate, target in _decide_with_model(
            workspace, normal, existing_by_index, personal_data, prompt_version(PROMPT_NAME), drops
        ):
            decided[id(candidate)] = target
    else:
        for candidate in normal:
            decided[id(candidate)] = None
    decisions = [(c, decided[id(c)]) for c in candidates if id(c) in decided]
    return decisions, drops


def _decide_with_model(workspace, normal, existing_by_index, personal_data, version, drops):
    sources = IngestionSource.objects.filter(pk__in={c["source_id"] for c in normal})
    try:
        result = InferenceGateway().complete(
            purpose=PURPOSE,
            messages=build_reconciliation_messages(version.content, normal, existing_by_index),
            actor=None,
            workspace=workspace,
            prompt_version=version,
            context_report={
                "candidate_count": len(normal),
                "existing_count": sum(len(v) for v in existing_by_index.values()),
                "source_kinds": sorted({s.kind for s in sources}),
                "personal_data": personal_data,
            },
            personal_data=personal_data,
        )
    except BudgetExceeded:
        return _all_new(normal, drops, "reconcile_budget_exceeded")
    except GatewayError:
        return _all_new(normal, drops, "reconcile_failed")
    try:
        parsed = parse_reconciliation(result.text, len(normal))
    except ReconciliationParseError:
        return _all_new(normal, drops, "reconcile_schema_invalid")
    out = []
    for index, candidate in enumerate(normal):
        action, fact_id = parsed.get(index, (ACTION_NEW, None))
        if action == ACTION_DUPLICATE:
            drops[candidate["source_id"]]["reconcile_duplicate"] += 1
            continue
        if action == ACTION_SUPERSEDE:
            target, reason = _validated_target(candidate, fact_id, existing_by_index.get(index, []))
            if reason:
                drops[candidate["source_id"]][reason] += 1
                out.append((candidate, None))
                continue
            out.append((candidate, str(target.pk)))
            continue
        out.append((candidate, None))
    return out
