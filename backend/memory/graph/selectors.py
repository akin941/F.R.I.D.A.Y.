"""Graph reads (brief D2, D4): current and as-of facts, the recursive-CTE
neighbourhood, entity timelines and derived entity privacy.

Callers scope first: every queryset a view or tool hands in comes from
`for_actor()`, and `neighbourhood` takes the workspace as a SQL parameter
applied in both the anchor and the recursive term, so a walk can never
leave it (ADR-001). No graph database, no `networkx`.
"""

from django.db import connection
from django.db.models import Q

from memory.models import MemoryEntity, MemoryFact, live_normal_fact_q

MAX_EDGES = 200
ALLOWED_HOPS = (1, 2)


class InvalidHops(ValueError):
    pass


def current_facts(queryset=None):
    """Current facts (D2) of an already-scoped queryset (default: all live
    rows -- system code only; views pass a `for_actor()` queryset)."""
    queryset = MemoryFact.objects.all() if queryset is None else queryset
    return queryset.current()


def facts_as_of(queryset, when):
    """Facts valid at `when`: `valid_from <= when < coalesce(valid_to, inf)`."""
    return queryset.as_of(when)


def entity_is_private(entity):
    """D4: an entity is private iff it has no live non-`private` fact as
    subject or object. `MemoryEntity.objects.with_privacy()` is the
    queryset form of the same predicate (`live_normal_fact_q`)."""
    return not (
        MemoryFact.all_objects.filter(live_normal_fact_q())
        .filter(Q(subject=entity) | Q(object_entity=entity))
        .exists()
    )


def with_privacy(queryset):
    return queryset.with_privacy()


def entity_timeline(entity, *, include_private=False, include_pending=False):
    """Every approved or system fact about `entity` (subject or object),
    superseded history included, oldest first; optionally the pending
    proposals too (the owner's `/memory/graph` view)."""
    statuses = [MemoryFact.Status.APPROVED, MemoryFact.Status.SYSTEM]
    if include_pending:
        statuses.append(MemoryFact.Status.PENDING_APPROVAL)
    queryset = MemoryFact.objects.filter(
        Q(subject=entity) | Q(object_entity=entity),
        workspace_id=entity.workspace_id,
        status__in=statuses,
    )
    if not include_private:
        queryset = queryset.filter(sensitivity=MemoryFact.Sensitivity.NORMAL)
    return queryset.select_related("subject", "object_entity").order_by("valid_from", "created_at")


def _edge_predicate(alias):
    """The per-edge SQL predicate, applied in BOTH CTE terms (D2): one
    workspace, live rows only, the status/origin/sensitivity flags and the
    time window. Placeholders are named; nothing user-supplied is
    interpolated."""
    f = alias
    return f"""
        {f}.workspace_id = %(workspace)s
        AND {f}.deleted_at IS NULL
        AND (
            ({f}.status = 'approved' AND {f}.origin = 'learned')
            OR (%(include_system)s AND {f}.status = 'system' AND {f}.origin = 'graphify')
        )
        AND ({f}.sensitivity = 'normal' OR %(include_private)s)
        AND (
            (%(as_of)s::timestamptz IS NULL AND {f}.valid_to IS NULL)
            OR (
                %(as_of)s::timestamptz IS NOT NULL
                AND {f}.valid_from <= %(as_of)s::timestamptz
                AND ({f}.valid_to IS NULL OR {f}.valid_to > %(as_of)s::timestamptz)
            )
        )
        AND NOT EXISTS (
            SELECT 1 FROM memory_memoryentity dead
            WHERE dead.id IN ({f}.subject_id, {f}.object_entity_id)
              AND (dead.deleted_at IS NOT NULL OR dead.workspace_id <> %(workspace)s)
        )
    """


_NEIGHBOURHOOD_SQL = """
WITH RECURSIVE walk(fact_id, frontier, depth, visited) AS (
    SELECT f.id,
           CASE WHEN f.subject_id = ANY(%(seeds)s::uuid[]) THEN f.object_entity_id
                ELSE f.subject_id END,
           1,
           %(seeds)s::uuid[]
    FROM memory_memoryfact f
    WHERE ({anchor})
      AND (f.subject_id = ANY(%(seeds)s::uuid[]) OR f.object_entity_id = ANY(%(seeds)s::uuid[]))
    UNION ALL
    SELECT g.id,
           CASE WHEN g.subject_id = w.frontier THEN g.object_entity_id ELSE g.subject_id END,
           w.depth + 1,
           w.visited || w.frontier
    FROM walk w
    JOIN memory_memoryfact g
      ON (g.subject_id = w.frontier OR g.object_entity_id = w.frontier)
    WHERE w.depth < %(hops)s
      AND w.frontier IS NOT NULL
      AND NOT (w.frontier = ANY(w.visited))
      AND g.id <> w.fact_id
      AND ({recursive})
)
SELECT fact_id, MIN(depth) AS hop
FROM walk
GROUP BY fact_id
ORDER BY hop, fact_id
LIMIT %(limit)s
"""


def neighbourhood(
    entity_ids,
    *,
    workspace,
    hops,
    as_of=None,
    include_system=False,
    include_private=False,
    limit=MAX_EDGES,
):
    """The facts within `hops` (1 or 2) of `entity_ids`, in one workspace,
    as one recursive CTE (D2). Defaults walk only `approved`, `learned`,
    non-`private`, current facts; `include_system` adds Graphify `system`
    facts (the lookup tool and the `self` summary only), `include_private`
    adds `private` ones (the owner's `/memory/graph` API only -- never a
    model path). A visited-set array stops cycles; at most `MAX_EDGES`
    edges come back. Returns MemoryFact rows, nearest first, each with a
    `hop` attribute."""
    if isinstance(hops, bool) or hops not in ALLOWED_HOPS:
        raise InvalidHops("hops must be 1 or 2.")
    workspace_id = getattr(workspace, "pk", workspace)
    seeds = [
        str(pk)
        for pk in MemoryEntity.objects.filter(
            workspace_id=workspace_id, pk__in=list(entity_ids)
        ).values_list("pk", flat=True)
    ]
    if not seeds:
        return []
    sql = _NEIGHBOURHOOD_SQL.format(anchor=_edge_predicate("f"), recursive=_edge_predicate("g"))
    params = {
        "workspace": workspace_id,
        "seeds": seeds,
        "hops": hops,
        "as_of": as_of,
        "include_system": bool(include_system),
        "include_private": bool(include_private),
        "limit": max(0, min(int(limit), MAX_EDGES)),
    }
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
    hop_of = {fact_id: hop for fact_id, hop in rows}
    facts = MemoryFact.objects.filter(
        workspace_id=workspace_id, pk__in=list(hop_of)
    ).select_related("subject", "object_entity")
    by_id = {fact.pk: fact for fact in facts}
    ordered = []
    for fact_id, hop in rows:
        fact = by_id.get(fact_id)
        if fact is not None:
            fact.hop = hop
            ordered.append(fact)
    return ordered
