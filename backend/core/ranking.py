"""Reciprocal rank fusion (Phase 8B branch 4, brief D8).

A pure helper with no Django or app import, shared by `memory.retrieval`
and `knowledge.context` so neither imports the other for retrieval.

`rrf(lists, k=60)` fuses ranked lists of hashable keys:
`score(item) = sum(1 / (k + rank_i))` over the lists the item appears in,
ranks counted from 1. An item listed twice in one list counts once, at its
best rank. The result is ordered by score (highest first); ties break on
the item's first appearance across the lists in order, so the output is
deterministic for deterministic inputs.
"""

DEFAULT_K = 60


def rrf(lists, k=DEFAULT_K):
    """Return `[(key, score), ...]`, best first."""
    if k < 0:
        raise ValueError("k must be >= 0")
    scores = {}
    first_seen = {}
    position = 0
    for ranked in lists:
        seen_here = set()
        for rank, key in enumerate(ranked, start=1):
            if key in seen_here:
                continue
            seen_here.add(key)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
            if key not in first_seen:
                first_seen[key] = position
                position += 1
    return sorted(scores.items(), key=lambda item: (-item[1], first_seen[item[0]]))
