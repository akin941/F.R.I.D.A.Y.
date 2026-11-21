"""The temporal knowledge graph (Phase 8B, brief D2/D3/D7).

- `models`: `memory.models.MemoryEntity` / `MemoryFact` (one migration
  history with the rest of `memory`);
- `candidates`: the fact-object validator shared by episode extraction v2
  and the weekly reflection (branch 2);
- `services`: every write (propose, approve, reject, edit/supersede, trash,
  restore, the forget-source and purge cascades);
- `selectors`: current / as-of reads, the recursive-CTE `neighbourhood`,
  entity timelines and the derived entity privacy;
- `ports`: what producers outside `memory` may call;
- `views` / `urls`: the owner API under `/api/v1/memory/graph/`.

Import contract (pyproject): `memory.graph` may import only `memory`,
`workspaces`, `audit`, `core` and `ai_core`, like `memory.ingestion`.
"""
