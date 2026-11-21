"""The hybrid retriever (Phase 8B branch 4, brief D8).

`retrieve(*, actor, workspace, query_text, personal_data, as_of=None, k=60)`
returns the memories and facts a chat turn injects when
`MEMORY_RETRIEVAL_MODE=hybrid`:

1. **Workspace filter first.** Every candidate query starts from
   `for_actor(actor, workspace=workspace)` with the existing eligibility
   rules: `cloud_eligible()` memories and `model_eligible()` facts (current
   `approved` `learned` non-`private`; as of `as_of` when given). Nothing
   is ranked across workspaces and filtered later. `system` (Graphify)
   facts are never candidates: not in a vector list, a full-text list or a
   hop.
2. Vector top-50 and full-text top-50 per table.
3. RRF (`core.ranking.rrf`): `score = sum(1 / (k + rank_i))`.
4. Recency decay `score * 0.5 ** (age_days / MEMORY_RETRIEVAL_HALF_LIFE_DAYS)`
   on `episodic` and `work_log` memories (age from `updated_at`) and on
   facts (age from `valid_from`); never on `semantic`, `preference` or
   `procedural` memories.
5. Hop expansion: the entities of the top-10 facts seed
   `neighbourhood(hops=MEMORY_RETRIEVAL_HOPS)` with its model-path
   defaults (`include_system=False, include_private=False`); a reached
   fact enters at `seed score * 0.5 ** hop`, and a fact naming an entity
   that `entity_is_private` is dropped (`private_entity_ids`).
6. Memories and facts share `MEMORY_CHAR_BUDGET` (4000).

A `GatewayError` on the query embed degrades to full text only, never to
a failed turn. Every call records `memory.context_accessed` with ids only
(memory and fact ids). The returned `report` is the context report's
retrieval part: mode, candidate counts per list, hops and fact count,
never text.
"""

import math
from dataclasses import dataclass, field

from django.conf import settings
from django.utils import timezone

from ai_core.exceptions import GatewayError
from ai_core.gateway import InferenceGateway
from audit.services import record_audit
from core.ranking import DEFAULT_K, rrf
from memory.context import (
    MAX_CONTEXT_MEMORIES,
    MEMORY_CHAR_BUDGET,
    MEMORY_CONTENT_MAX_CHARS,
    render_fact_line,
)
from memory.graph.selectors import neighbourhood
from memory.models import Memory, MemoryEntity, MemoryFact
from memory.retrieval.search import fulltext_ranked, vector_ranked

MODE = "hybrid"
LIST_LIMIT = 50
HOP_SEEDS = 10
HOP_DISCOUNT = 0.5
MAX_CONTEXT_FACTS = 20
MAX_HOPS = 2
DECAYED_MEMORY_TYPES = frozenset({Memory.Type.EPISODIC, Memory.Type.WORK_LOG})

MEMORY_TABLE = "memory_memory"
FACT_TABLE = "memory_memoryfact"


@dataclass
class HybridResult:
    memories: list = field(default_factory=list)
    facts: list = field(default_factory=list)
    report: dict = field(default_factory=dict)
    # Memories and facts in final rank order (the eval's recall@10).
    ranked: list = field(default_factory=list)

    @property
    def ranked_ids(self):
        return [row.id for row in self.ranked]


def configured_hops():
    hops = int(getattr(settings, "MEMORY_RETRIEVAL_HOPS", 1))
    return max(0, min(hops, MAX_HOPS))


def half_life_days():
    return float(getattr(settings, "MEMORY_RETRIEVAL_HALF_LIFE_DAYS", 45))


def decay_factor(age_days, half_life=None):
    half_life = half_life_days() if half_life is None else half_life
    if half_life <= 0:
        return 1.0
    return math.pow(0.5, max(0.0, age_days) / half_life)


def _age_days(instant, reference):
    if instant is None:
        return 0.0
    return max(0.0, (reference - instant).total_seconds() / 86400.0)


def memory_base(actor, workspace):
    return Memory.objects.for_actor(actor, workspace=workspace).cloud_eligible()


def fact_base(actor, workspace, as_of=None):
    """The retrievable facts (D2) of the workspace, current or as of."""
    scoped = MemoryFact.objects.for_actor(actor, workspace=workspace)
    if as_of is None:
        return scoped.model_eligible()
    return scoped.model_eligible_as_of(as_of)


def _embed(*, actor, workspace, query_text, personal_data):
    try:
        result = InferenceGateway().embed(
            purpose="memory.retrieval",
            texts=[query_text],
            actor=actor,
            workspace=workspace,
            personal_data=personal_data,
        )
    except GatewayError:
        return None, None
    return result.vectors[0], result.usage.provider_id


def _ids(queryset):
    return list(queryset.values_list("id", flat=True))


def candidate_lists(*, actor, workspace, query_text, query_vector, provider_id, as_of=None):
    """The four ranked id lists (memory/fact x vector/full text)."""
    memories = memory_base(actor, workspace)
    facts = fact_base(actor, workspace, as_of)
    lists = {"memory_vector": [], "fact_vector": [], "memory_fulltext": [], "fact_fulltext": []}
    if query_vector is not None:
        lists["memory_vector"] = _ids(
            vector_ranked(
                memories, query_vector=query_vector, provider_id=provider_id, limit=LIST_LIMIT
            )
        )
        lists["fact_vector"] = _ids(
            vector_ranked(
                facts, query_vector=query_vector, provider_id=provider_id, limit=LIST_LIMIT
            )
        )
    lists["memory_fulltext"] = _ids(
        fulltext_ranked(memories, table=MEMORY_TABLE, query_text=query_text, limit=LIST_LIMIT)
    )
    lists["fact_fulltext"] = _ids(
        fulltext_ranked(facts, table=FACT_TABLE, query_text=query_text, limit=LIST_LIMIT)
    )
    return lists


def _keyed(lists):
    return [
        [("memory", i) for i in lists["memory_vector"]],
        [("memory", i) for i in lists["memory_fulltext"]],
        [("fact", i) for i in lists["fact_vector"]],
        [("fact", i) for i in lists["fact_fulltext"]],
    ]


def _decayed(score, row, kind, reference):
    if kind == "memory":
        if row.memory_type not in DECAYED_MEMORY_TYPES:
            return score
        return score * decay_factor(_age_days(row.updated_at, reference))
    return score * decay_factor(_age_days(row.valid_from, reference))


def private_entity_ids(workspace, entity_ids):
    """The ids among `entity_ids` (of `workspace`) that `entity_is_private`
    (D4), in one workspace-scoped query (`with_privacy()`, the queryset
    form of the same predicate)."""
    if not entity_ids:
        return set()
    workspace_id = getattr(workspace, "pk", workspace)
    return set(
        MemoryEntity.all_objects.filter(workspace_id=workspace_id, pk__in=list(entity_ids))
        .with_privacy()
        .filter(is_private=True)
        .values_list("pk", flat=True)
    )


def expand_hops(seeds, *, workspace, hops, as_of, known):
    """`{fact_id: (fact, score)}` reached from `seeds` (`[(fact, score)]`)
    within `hops`, at `seed score * 0.5 ** hop` (the best seed wins). A
    reached fact naming an entity that `entity_is_private` is dropped."""
    reached = {}
    if hops <= 0:
        return reached
    for seed, seed_score in seeds:
        entity_ids = [pk for pk in (seed.subject_id, seed.object_entity_id) if pk]
        for fact in neighbourhood(entity_ids, workspace=workspace, hops=hops, as_of=as_of):
            if fact.pk in known:
                continue
            if fact.sensitivity != MemoryFact.Sensitivity.NORMAL or (
                fact.status != MemoryFact.Status.APPROVED
                or fact.origin != MemoryFact.Origin.LEARNED
            ):
                continue  # the neighbourhood defaults already exclude these
            score = seed_score * math.pow(HOP_DISCOUNT, fact.hop)
            if fact.pk not in reached or reached[fact.pk][1] < score:
                reached[fact.pk] = (fact, score)
    named = {pk for f, _ in reached.values() for pk in (f.subject_id, f.object_entity_id) if pk}
    private = private_entity_ids(workspace, named)
    return {
        pk: (f, score)
        for pk, (f, score) in reached.items()
        if f.subject_id not in private and f.object_entity_id not in private
    }


def _memory_cost(memory):
    return len(memory.title) + min(len(memory.content), MEMORY_CONTENT_MAX_CHARS)


def _select_within_budget(ranked):
    """Memories and facts share `MEMORY_CHAR_BUDGET`; skip what does not
    fit and keep scanning (the legacy selector's deterministic rule)."""
    budget = MEMORY_CHAR_BUDGET
    selected = []
    memory_count = fact_count = 0
    for kind, row in ranked:
        if kind == "memory":
            if memory_count >= MAX_CONTEXT_MEMORIES:
                continue
            cost = _memory_cost(row)
        else:
            if fact_count >= MAX_CONTEXT_FACTS:
                continue
            cost = len(render_fact_line(row))
        if cost > budget:
            continue
        budget -= cost
        selected.append((kind, row))
        if kind == "memory":
            memory_count += 1
        else:
            fact_count += 1
    return selected


def retrieve(*, actor, workspace, query_text, personal_data, as_of=None, k=DEFAULT_K):
    reference = as_of or timezone.now()
    hops = configured_hops()
    query_vector = provider_id = None
    embed_degraded = False
    lists = {"memory_vector": [], "fact_vector": [], "memory_fulltext": [], "fact_fulltext": []}
    if query_text:
        query_vector, provider_id = _embed(
            actor=actor, workspace=workspace, query_text=query_text, personal_data=personal_data
        )
        embed_degraded = query_vector is None
        lists = candidate_lists(
            actor=actor,
            workspace=workspace,
            query_text=query_text,
            query_vector=query_vector,
            provider_id=provider_id,
            as_of=as_of,
        )

    fused = rrf(_keyed(lists), k=k)
    memory_ids = [key[1] for key, _ in fused if key[0] == "memory"]
    fact_ids = [key[1] for key, _ in fused if key[0] == "fact"]
    # Re-read through the same scoped, eligible querysets: a row is never
    # loaded from outside them.
    memories = {m.pk: m for m in memory_base(actor, workspace).filter(pk__in=memory_ids)}
    facts = {
        f.pk: f
        for f in fact_base(actor, workspace, as_of)
        .filter(pk__in=fact_ids)
        .select_related("subject", "object_entity")
    }

    scored = []
    for order, (key, score) in enumerate(fused):
        kind, pk = key
        row = memories.get(pk) if kind == "memory" else facts.get(pk)
        if row is None:
            continue
        scored.append((_decayed(score, row, kind, reference), order, kind, row))
    scored.sort(key=lambda item: (-item[0], item[1]))

    seeds = [(row, score) for score, _order, kind, row in scored if kind == "fact"][:HOP_SEEDS]
    reached = expand_hops(seeds, workspace=workspace, hops=hops, as_of=as_of, known=set(facts))
    next_order = len(scored)
    for fact_id in sorted(reached, key=str):
        fact, score = reached[fact_id]
        scored.append((score, next_order, "fact", fact))
        next_order += 1
    scored.sort(key=lambda item: (-item[0], item[1]))

    selected = _select_within_budget([(kind, row) for _score, _order, kind, row in scored])
    selected_memories = [row for kind, row in selected if kind == "memory"]
    selected_facts = [row for kind, row in selected if kind == "fact"]

    record_audit(
        action="memory.context_accessed",
        actor=actor,
        workspace=workspace,
        metadata={
            "memory_ids": [str(m.id) for m in selected_memories],
            "fact_ids": [str(f.id) for f in selected_facts],
            "count": len(selected_memories) + len(selected_facts),
            "retrieval_mode": MODE,
        },
    )
    report = {
        "retrieval_mode": MODE,
        "retrieval_candidates": {name: len(ids) for name, ids in lists.items()},
        "retrieval_hops": hops,
        "retrieval_hop_facts": len(reached),
        "retrieval_embed_degraded": embed_degraded,
        "fact_count": len(selected_facts),
        "fact_ids": [str(f.id) for f in selected_facts],
    }
    return HybridResult(
        memories=selected_memories,
        facts=selected_facts,
        report=report,
        ranked=[row for _kind, row in selected],
    )
