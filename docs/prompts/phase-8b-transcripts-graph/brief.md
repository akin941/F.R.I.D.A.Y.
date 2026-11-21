# Phase 8B — Transcripts and knowledge graph (server side + Mac session)

> Status: **Draft (2026-11-20).** Written on `docs/p8b-brief`. It
> replaces the stub of the same date, which came from the owner-approved
> Friday 3.0 roadmap. Every decision, scope item and the go/no-go of the
> stub is kept; see "From the stub" below. The brief needs its
> independent Sonnet 5 plan review before branch 1, because the
> implementing model never reviews its own brief.
> Sources: `docs/research/2026-11-roadmap-proposal.md` §B4, §c.2, §c.6,
> §c.8, §c.9, §c.10, §g d5/d6/d10; MASTER_PLAN §AA "Friday 3.0 phases"
> (order 5).
> Parent decisions:
> - ADR-001: workspace axis, `for_actor()`.
> - ADR-002: modular monolith and its import contracts.
> - ADR-003 and its 2026-11-20 amendment: single gateway door, one
>   `ModelUsage` per call, budgets enforced in the gateway, no LiteLLM.
> - ADR-004 and ADR-008 decision 3: the gate. The vault writes of this
>   phase stay inside the existing gate-class `vault_export` run.
> - ADR-005: Postgres is the source of truth and the vault is a mirror.
>   The `private` contract still holds.
> - **ADR-012**: the ingestion pipeline. 8B adds the transcript sources,
>   the `Inbox/` source, facts in the forget cascade, and the node-side
>   scrub.
> - **ADR-013**: the node protocol. Transcripts arrive as signed,
>   chain-audited read invocations. Decision 11 (the per-directory grant)
>   gates them on the Mac.
> - ADR-014: the read class. The two new owner-turn capabilities are
>   session reads, which ADR-014 already lists as read.
> - ADR-015: unchanged. Voice turns still only propose.
> - ADR-009: owner deletes are trash moves and purge is gated. Facts
>   join both.
>
> **Platform boundary (owner decision 2026-11-20).** The implementation
> container is Linux, with no macOS and no Xcode. Two kinds of work follow
> from that:
> - **In scope now, with full branches (1–9 and the closeout):**
>   everything that can be built and tested here:
>   - the Django models, services, jobs and APIs;
>   - the node capability contracts, plus the Python reference node's
>     readers and conformance cases for them;
>   - the retrieval and the eval harness;
>   - the Graphify job's image, runner primitive and importer, tested
>     with a captured `graph.json` fixture and a faked `docker` call;
>   - the Headroom stage and its sidecar, tested with a fake client;
>   - the vault mirror changes;
>   - the frontend.
> - **Not built here:** anything that must be built, signed or run on
>   macOS. That covers the Swift readers for the owner-turn capabilities
>   and their Touch ID grant, a run against the owner's real transcripts,
>   and the real-data evaluations. They are listed under "Mac session
>   (deferred)" and marked "deferred: Mac session" in the branch table.
>
> Go/no-go items that need the Mac or real-world owner data are recorded
> as **pending owner evaluation**. They do not block Phase 10.
>
> **Precondition.**
> - Branches 1–7 and 9 need only the merged Phase 8A closeout, which is
>   on the integration branch.
> - Branch 8 (transcripts) also needs **Phase 9 server branches 2–5**
>   merged:
>   - the `nodes` app and protocol (2);
>   - the reference node and conformance suite (3);
>   - the gateway (4);
>   - `invoke_read` and the 8A path-resolution helpers (5).
>
>   Only Phase 9 branch 1 is merged today (2026-11-20). Branch 8 waits
>   for the others, and nothing else in 8B waits for it.
> - The Mac session also needs Phase 9's M2 (the Swift reader framework
>   and the per-directory grants).
>
> **Branches.** Cut from the integration branch, integrated on the phase
> integration branch. Under the MASTER_PLAN stable-numbering rule they use
> `feat/p8-…` names (the same prefix as 8A) and the closeout is
> `chore/p8b-closeout`.

## Goal / value

Friday knows the details of the owner's work across tools:
- what was decided in which Claude Code or Codex session;
- which entities (repos, services, firms, people by role, Friday's own
  modules) relate to which facts, and since when.

It still learns only from owner-authored text, behind the ADR-012
pipeline.

Usable outcome (MASTER_PLAN §AA): "Geçen hafta X reposunda ne karar
verdik?" and "Y firmasının deploy'u hangi servise bağlı, ne zamandan
beri?" are answered from approved facts with their validity windows.
"Friday'ın hangi modülü hafızayı yönetiyor?" is answered from the
Graphify import. The answers hold within one workspace, and firm facts
never surface in a personal conversation.

Phase 8B delivers seven things:
1. **Temporal facts in Postgres.** `MemoryEntity` and `MemoryFact`, with
   validity windows, `superseded_by`, the source episode and confidence.
   Traversal uses recursive CTEs, with no graph database.
2. **Fact learning.** Owner-text extraction produces fact proposals,
   with code-checked provenance. A reconciliation pass proposes
   supersessions, and a capped weekly reflection runs.
3. **Hybrid retrieval.** pgvector HNSW plus `tsvector`, fused with
   reciprocal-rank fusion. The workspace filter runs first, then 1–2 hop
   expansion and recency decay, all behind a flag until the eval set
   says yes.
4. **A private eval set and its baseline.** The harness and a synthetic
   public seed are committed. The owner's private set stays off the repo.
5. **Self-knowledge.** A Graphify AST-only job runs in a network-denied
   harness container on merges to `main`. Its graph is imported as
   `origin=graphify` entities and facts. It feeds the `self` template and
   never writes the block.
6. **Headroom.** A pre-send gateway stage in a network-denied sidecar.
   It is off by default, bypassed for approval, tool-call, trust-check
   and core-block prompts, and enabled per purpose only after the eval
   shows at most 1 point of loss.
7. **Transcripts and Obsidian:**
   - Claude Code and Codex transcripts are ingested through the node,
     owner turns only, and scrubbed on the node and again on the server.
   - The Obsidian mirror gets format v2 (entity notes, edit conflicts
     saved to `_conflicts/<date>/`).
   - The opt-in `Inbox/` is imported once and moved to
     `Inbox/_imported/`, inside the existing gated vault run.

## From the stub (kept)

The stub's scope list and go/no-go carry over unchanged. Each item is now
placed in a branch:

| Stub item | Where |
|---|---|
| Transcript ingestion through the node, owner turns only, on after the per-directory Touch ID grant (d5), scrubbed on the node and on the server | D13, branch 8, M1 |
| `MemoryEntity` / `MemoryFact` with validity windows, `superseded_by`, source episode, confidence; recursive CTEs, no graph DB | D2–D4, branch 1 |
| Retrieval: HNSW + `tsvector` + RRF, workspace filter first, 1–2 hop expansion, recency decay | D8, branch 4 |
| Graphify AST-only job in a network-denied harness container on merges to `main`, exact pin + hash, manifest; imported as `source=graphify`; feeds `self`, never writes it | D10, branch 5 |
| Headroom pre-send stage behind a flag (off), bypass list, per-purpose enable after the eval shows ≤ 1 point loss, pre/post tokens in the `ModelUsage` context report | D11, branch 6 |
| Obsidian mirror format; `Inbox/` imported once and moved to `Inbox/_imported/`; owner edits saved to `_conflicts/<date>/` with a notice; ADR-005 unchanged | D12, branch 7 |
| Weekly reflection (proposal-only, capped) | D5, branch 2 |
| A private eval set and its baseline | D9, branch 3 |
| **Go/no-go:** eval baseline recorded; Headroom stays off unless ≤ 1 point loss; scrubber corpus extended with transcript fragments at 100% secrets recall | "Go/no-go record" below |

Two names change. The stub's `memory.reflect` becomes
`memory.reflection`, following 8A's rename of the proposal's
`memory.extract` to `memory.episode_extraction` (D15). The proposal's
`knowledge.graph_import` purpose is dropped, because the Graphify import
makes no model call (D10).

## What already exists (verified 2026-11-20 on the integration branch)

- **Ingestion (8A).**
  - `memory.IngestionSource`, `WorkspaceMappingRule`, `Episode`,
    `ScrubTerm`, `IngestionCounter`, `EpisodeExtractionJob` and
    `CodingSession` live in `backend/memory/models.py` (migrations
    `0006`–`0009`).
  - `IngestionSource.Kind` is `git`, `harness`, `claude_code_hooks` and
    `calendar`. `is_personal_class` is true only for `calendar`.
  - `record_episode` (`memory/ingestion/ports.py`) is the only writer of
    `Episode`. It returns `stored`, `dropped_unmapped`, `dropped_paused`
    or `duplicate`, and dedupes on `(source, external_id)`.
  - Mapping is `memory/ingestion/mapping.py` (`normalise_path`,
    `resolve_workspace`). Unmapped items are dropped, never defaulted.
  - The scrubber is `memory/ingestion/scrubber.py`, stdlib-only, with
    vendored copies checked identical in CI. Its corpus is
    `memory/tests/scrubber_corpus/{secrets,pii,benign}.jsonl`, gated at
    100% secrets recall.
- **Learning loop (8A branch 5).**
  - `memory/ingestion/extraction.py` handles schema v1. A candidate has
    the keys `type`, `title`, `content`, `quote`, `episode_id` and
    `target`. `target` is `memory` or a core-block label, and `self` is
    dropped.
  - Limits: `MAX_EPISODES_PER_JOB = 25`, `MAX_CANDIDATES = 50`.
  - `needs_personal_data(episode)` sends personal-class or `private`
    episodes with `personal_data=True`.
  - `memory/provenance.py` (`quote_is_verbatim`) is the verbatim-quote
    check.
  - `memory/ingestion/consolidation.py` enforces
    `MAX_PROPOSALS_PER_NIGHT = 10` and the weekly core-block cap of 3,
    in code.
  - Purposes `memory.episode_extraction` (`cheap`) and
    `memory.consolidation` (`mid`) are in `AI_PURPOSE_TIERS`
    (`config/settings/base.py`). The budgets are `AI_LEARNING_PURPOSES`
    and `AI_BUDGET_LEARNING_MONTHLY_USD` (20).
  - `forget_source` and `restore_source` (`memory/ingestion/services.py`)
    cascade to memories and proposals by operation id. `purge_trash`
    (`tools/purge.py`) has the keys `episode_ids` and
    `forgotten_memory_ids`.
- **Retrieval today.**
  - `memory.context.select_context_memories` embeds the query once
    (`purpose="memory.retrieval"`), then ranks by type priority and
    `CosineDistance`, within `MEMORY_CHAR_BUDGET = 4000`.
  - `knowledge.context.select_context_chunks` does the same for RAG
    chunks.
  - There is **no `tsvector`, no full-text index and no HNSW index**
    anywhere in the code (a search for `SearchVector`, `GinIndex` and
    `HnswIndex` finds nothing).
  - `Memory.embedding` and `KnowledgeChunk.embedding` are `VectorField`s
    **without `dimensions`** (MVP 3 vector-dimension policy). Ranking
    guards on the same provider and the same dimension.
  - Embedding providers carry `dimension`, for example 1536 for
    `openai-embedding-primary` and 1024 for `nvidia-nim-embedding`
    (`seed_ai_defaults`).
  - The dev database runs pgvector 0.6.0 on Postgres 16. The compose
    image is `pgvector/pgvector:pg16`. HNSW exists since pgvector 0.5.0,
    but **an HNSW index needs a typed dimension**, so an untyped column
    can only be indexed through a cast expression (D8).
- **Self block (8A branch 2).**
  - `assistant/self_block.py` is a fixed `TEMPLATES` vocabulary filled
    from `assistant/self_sources.json` and the registries, within
    `SELF_BUDGET`, with no model call.
  - `build_self_sources --check` runs in CI and quotes the MASTER_PLAN
    §AA row of the current phase.
- **Vault mirror.**
  - `tools/export.py` is the gate-class `vault_export` tool (an
    `ApprovalRequest` per run, a lease-fenced `VaultExportRun`). It does
    a full resync and a conservative prune of approved memories into
    `03_Workspaces/<ws>/Memory/<id>.md`, and of private ones into
    `_private/<ws>/Memory/`.
  - Files are fully regenerated on every run, so an owner edit is
    overwritten silently today.
  - Frontmatter (`tools/vault_frontmatter.py`) holds `friday_id`, `type`,
    `workspace`, `project`, `sensitivity`, `source: friday_export` and
    `updated_at`, with no content hash.
  - Path safety is `tools/vault_paths.py` (`O_NOFOLLOW` descriptor walk,
    `atomic_write_validated`, `read_validated`, `list_validated`,
    `unlink_validated`).
  - The documented layout (`docs/memory/obsidian-vault-strategy.md`)
    names the inbox **`00_Inbox`**. Nothing imports it yet.
- **Harness containers.**
  - `harness/runner.py` `launch_container` runs `docker run --pull never
    --cap-drop ALL --security-opt no-new-privileges --user uid:gid` on
    `HARNESS_DOCKER_NETWORK` (the restricted-egress network), with
    resource caps and a read-only `/repo` mount.
  - There is **no `--network none` launch today**.
  - The images are under `harness/docker/` (`claude_code`, `codex_cli`
    and a `smoke` image).
  - The git producer `harness/ingestion.py` (`ingest_repo`, on the
    runner beat) keeps a per-repo commit cursor (`harness/0013`).
- **Node (Phase 9).**
  - Only branch 1 (`kind=node` device trust, `CHANNEL_NODE`) is merged.
  - The Phase 9 brief defines what branch 8 builds on:
    - `nodes.services.invoke_read(*, node, capability, args, workspace,
      actor, trace_id)`;
    - the eight v1 read capabilities with server-known contracts in
      `nodes/capabilities/v1/`;
    - the reference node with fixture readers and `cwd_prefixes`;
    - `memory.ingestion.mapping.workspace_for_path` and
      `prefixes_for_workspace` (branch 5);
    - the vendored node scrubber and `scrub.json` parity vectors
      (branch 3);
    - the contract "only tools, hud, ops may import nodes".
- **Allowlist.** `docs/mcp/allowlist.md` lists Graphify only as a *dev
  workflow* tool ("not a Friday runtime dependency"). Headroom is not
  listed. The roadmap (§c.8) names LiteLLM as "not a dependency".
- **Import contracts** (`backend/pyproject.toml`):
  - `memory.ingestion` may import only `memory`, `workspaces`, `audit`,
    `core` and `ai_core`;
  - `memory` must not import `tools`, `assistant`, `agents` or
    `harness`;
  - no app imports `harness`;
  - `ai_core` imports no business app;
  - `tools` may import `memory`, and `harness` may import `memory`.

## Boundary vs existing systems

- **Memory owns every learned row.** Entities, facts, their proposals
  and the transcript cursor live in the `memory` app, with one migration
  history.
  - Producers outside it call memory-owned ports, exactly as 8A's
    producers do:
    - the Graphify importer in `harness`;
    - the transcript pull in `tools`;
    - the `Inbox/` import in `tools`.
  - Memory never imports them.
- **Every owner-derived fact is a proposal.** A fact learned from owner
  text is born `pending_approval` and approved per item on the morning
  card, like 8A memories.
  - `work_log` stays the only auto-approved class (ADR-008 decision 4).
  - Graphify facts are not learned about the owner. They are a
    deterministic, model-free index of Friday's own code, like
    `self_sources.json`. They carry `origin=graphify` and the status
    `system` (D3). They never enter the memory block and never appear on
    the morning card. They get **no embedding** and take **no part in
    `retrieve()`** (neither its vector or full-text lists nor its hop
    expansion, D8). They are reachable only through the `self`
    assembler, the `memory_graph_lookup` read tool and the read-only
    `/memory/graph` view.
- **The chat turn keeps its shape.**
  - Facts are one more fenced block inside the existing memory budget
    (D8).
  - Retrieval changes behind a flag.
  - One read tool is added (`memory_graph_lookup`, D8).
  - No new intent and no new SSE event.
- **The gate keeps its shape.**
  - Vault writes (conflict copies, the `Inbox/` move, entity notes) run
    only inside an approved `vault_export` run. The vault never changes
    Postgres except through episodes, which become proposals.
  - Forget-source and fact deletion are reversible trash moves
    (ADR-009).
  - Purge stays the gate-class `purge_trash`.
- **The gateway stays the single door.**
  - Headroom is a message transform inside `InferenceGateway.complete`,
    run by a sidecar that holds no provider key and has no egress. It is
    not a proxy, and nothing reaches a provider except through the
    adapters.
  - Graphify makes no model call (`GRAPHIFY_BACKEND=none`, code-only).
- **The node stays read-only and policy-free.** The transcript pull is
  two new read capabilities in the server-known contract set. They are
  signed, chain-audited and node-validated like every v1 read. The
  server never learns or changes the grant, except as `hello`
  availability.
- **Workspace axis unchanged.** Every new table is a
  `WorkspaceScopedModel`. Every view, read tool and retrieval query goes
  through `for_actor()` **before** ranking. A fact's entities are in the
  fact's own workspace, and an edge never crosses workspaces.

## Decisions (with justification)

### D1 — Sequencing: the graph first, transcripts last

The server branches that do not touch the node (1–7 and 9) run first and
in parallel where the table allows. This has three benefits:
- The fact model, retrieval and eval exist before transcripts multiply
  the episode volume.
- The eval baseline is measured on 8A sources first. That makes a
  transcript-driven quality change visible.
- Branch 8 can wait for Phase 9 branches 2–5 without idling the phase.

Justification: the roadmap's go/no-go asks for a baseline. A baseline
taken after transcripts arrive could not tell what they changed.

### D2 — Data model: temporal entities and facts

- `MemoryEntity` (`WorkspaceScopedModel`, `TimestampedModel`):
  - `kind`, one of `repo`, `service`, `firm`, `person_role`, `project`,
    `tool`, `concept`, `code_module`, `code_class` or `code_function`
    (the `code_*` kinds come only from Graphify);
  - `name` (≤ 200, scrubbed) and `normalised_name` (lowercased, single
    spaces, NFKC);
  - `aliases` (≤ 10 strings);
  - `origin` (`learned` or `graphify`) and `external_ref` (≤ 255; for
    Graphify the node `id` plus the repo id);
  - ADR-009 `deleted_at` and `forget_operation_id`.

  Constraint: unique `(workspace, kind, normalised_name, origin)`.
  Default manager live-only, `all_objects` for trash and purge.
- `MemoryFact` (`WorkspaceScopedModel`, `TimestampedModel`):
  - `subject` (FK entity, `PROTECT`), `predicate` (a closed vocabulary,
    below), and `object_entity` (FK, null) **or** `object_text` (≤ 500,
    scrubbed), exactly one of them;
  - `statement` (≤ 500, the rendered Turkish sentence shown to the
    owner);
  - `valid_from` (required) and `valid_to` (null means "still valid");
  - `superseded_by` (FK self, null), set on the **old** row only when a
    supersede is applied;
  - `supersedes` (FK self, null, `PROTECT`), set on a **pending**
    proposal only: the current fact it would replace if approved
    (reconciliation's `supersede` decision, D4; an owner edit is applied
    at once and never stays pending). It is cleared when the proposal is
    approved (the link then lives in the old row's `superseded_by`),
    rejected or forgotten;
  - `confidence` (integer 0–100; model-proposed facts are capped at 90
    until approved, Graphify `EXTRACTED` edges are 100);
  - `status`: `pending_approval`, `approved`, `rejected`, `archived`
    (forgotten) or `system` (Graphify only);
  - `origin` (`learned` or `graphify`);
  - `ingestion_source` (FK, null), `source_episode_id` (a plain UUID,
    because the episode expires, d10) and `provenance_quote` (≤ 300,
    required for `learned`);
  - `sensitivity` (`normal` or `private`), `embedding` plus
    `embedding_provider` (the `Memory` lease pattern), and a `search`
    `tsvector` (D8);
  - `forget_operation_id`, `forget_prior_status` and `deleted_at`.

  Checks:
  - `valid_to IS NULL OR valid_to >= valid_from`;
  - `origin='graphify'` ⇔ `status='system'`;
  - a `learned` fact has a non-empty quote;
  - `object_entity` is in the same workspace as the fact (enforced in
    `save()` and in a DB trigger, the `Memory.save` precedent);
  - `supersedes IS NULL OR status = 'pending_approval'` (a DB check);
  - the `supersedes` target has the same workspace, subject and
    predicate as the proposal and is not the proposal itself (enforced
    in `save()` and in the same DB trigger).
- **Predicate vocabulary v1** (a closed `TextChoices`, extended only by
  a reviewed commit):
  - learned: `decided`, `uses`, `depends_on`, `deployed_to`, `owned_by`,
    `works_on`, `prefers`, `blocked_by`, `replaced_by`, `has_status`;
  - Graphify only: `contains`, `imports`, `calls`, `inherits`.

  A model can propose only the learned set (a test pins the split).
- **Temporal rules** (`memory/graph/services.py`):
  - **`supersede(old, new)`** sets `old.valid_to = new.valid_from` and
    `old.superseded_by = new` in one transaction under
    `select_for_update` of both rows. It is allowed only when both have
    the same subject and predicate and the same workspace.
  - **Supersede order.** A supersede needs
    `new.valid_from >= old.valid_from`, otherwise the old row's window
    would be negative and the DB check would fail. `supersede`,
    `approve_fact` and `edit_fact` check it in code before any write:
    if `new.valid_from < old.valid_from` they return 409
    `supersede_order` and leave both rows unchanged (the proposal stays
    pending; an owner edit with an earlier user-chosen date is refused
    and the form says so). The DB check stays as the backstop, so an
    `IntegrityError` is never the user-visible path. Reconciliation
    applies the same rule before it links a target (D4).
  - **`approve_fact(proposal)`** of a proposal with `supersedes` set runs
    in one transaction: it locks the proposal and its target
    (`select_for_update`, in primary-key order), re-checks that the
    target is still a **current** fact (approved, `valid_to IS NULL`,
    not trashed or archived), sets the proposal `approved`, clears
    `supersedes`, and calls `supersede(old=target, new=proposal)`. If
    the target is no longer current (superseded by another fact,
    trashed, archived by a forget), nothing changes: the call returns
    409 `supersede_target_changed`, the proposal stays pending, and the
    digest shows it as "the fact it replaces has changed" with reject or
    edit as its actions (an edit creates a fresh proposal against the
    now-current fact).
  - **`reject_fact`** of such a proposal clears `supersedes` and leaves
    the target untouched.
  - **"Current facts"** are `approved` or `system`, with
    `valid_to IS NULL`, not trashed.
  - **"Retrievable facts"** are the current facts with
    `status = approved` and `origin = learned` only. They are the only
    facts `retrieve()` (D8) ranks, embeds or hop-expands. `system`
    facts are never retrievable.
  - **"Facts as of T"** are `valid_from <= T < coalesce(valid_to, ∞)`.
  - Approved facts are never edited in place: an owner edit creates a
    new version that supersedes the old one, so history stays readable.
- **Traversal** (`memory/graph/selectors.py`): `neighbourhood(entity_ids,
  *, hops, as_of, include_system=False, include_private=False)` is one
  recursive CTE.
  - It runs over current (or as-of) facts of **one workspace**, which is
    a SQL parameter applied in both the anchor and the recursive term.
  - The two flags are SQL predicates in both terms as well. With the
    defaults it walks only `approved`, `learned`, non-`private` facts.
    `include_system=True` adds `system` facts (used by
    `memory_graph_lookup` and the `self` summary only).
    `include_private=True` adds `private` facts and is passed only by
    the owner's `/memory/graph` API, which is not a model path. No
    caller that feeds a model payload passes it in 8B.
  - `hops` is 1 or 2 (any other value is refused).
  - It keeps a visited-set array, so cycles never loop.
  - It returns at most 200 edges.

  No graph database and no `networkx` dependency are used.
  Justification: the roadmap's T3 rule; Postgres stays the single
  store, and `for_actor()` applies to facts like any other row.

### D3 — Who writes facts

| Origin | Writer | Born as | Shown where |
|---|---|---|---|
| owner text (episode extraction v2, weekly reflection) | `memory.graph.ports.propose_fact`, called only by the episode extraction and reflection jobs (D4, D5) | `pending_approval` | morning card, per item |
| owner, in the UI | the fact API (web channel, owner, human) | `approved`, as the owner's own words: `provenance_quote` is the owner's typed statement and the audit row names the writer | `/memory/graph` |
| Graphify | `memory.graph.ports.import_code_graph` only (D10) | `system` | the `self` assembler, `memory_graph_lookup`, `/memory/graph` (read-only) |

- Approving, rejecting and editing are confirm-class in-app edits, web
  channel, human only. They reuse the 8A digest path: the digest lists
  fact proposals next to memory and core-block proposals.
- An agent or a voice turn can never approve a fact (ADR-015 decision
  2).
- Graphify rows are never approvable or editable through the API (405),
  and never trashable by the owner. They are replaced by the next import
  (D10).
- Justification for `system`: the facts are derived by a deterministic
  AST pass over Friday's own repository and state nothing about the
  owner. Forcing them through the morning card would flood it (thousands
  of edges) and teach the owner to bulk-approve. ADR-012 governs owner
  data, and this is not owner data. The `self` block stays deterministic
  either way (8A D8). Recorded in ADR-012's implementation note
  (branch 5).

### D4 — Fact learning: episode extraction schema v2 and reconciliation

Two extraction parsers exist today, with different shapes and versions
(verified 2026-11-21):
- the **episode** parser `parse_episode_candidates` in
  `memory/ingestion/extraction.py` (`SCHEMA_VERSION = 1`, prompt
  `friday_episode_extraction`, candidate keys `type`, `title`,
  `content`, `quote`, `episode_id`, `target`);
- the **chat** parser `parse_candidates` in `memory/extraction.py`
  (`SCHEMA_VERSION = 2` since 8A D9, prompt `friday_memory_extraction`,
  candidate keys `type`, `title`, `content`, `quote`).

Only the episode parser changes in 8B: **v1 → v2**. The chat parser,
its prompt and its tests stay at v2, unchanged, and chat extraction
emits **no** fact candidates in 8B (Out of scope). Facts are learned
only from episodes and the weekly reflection (D5). Chat turns that the
owner wants remembered as facts still reach the graph through the
owner UI (D3).

- **`friday_episode_extraction` schema v2** (`parse_episode_candidates`).
  The candidate keys are those of v1 plus `fact`, which is `null` or an
  object:
  `{"subject": {"kind","name"}, "predicate",
  "object": {"kind","name"} | {"text"}, "valid_from"}`.
  - `target` gains the value `fact`. A `fact` candidate needs a non-null
    `fact`, and the other targets need `fact: null`.
  - The parser stays strict: unknown keys are a schema error, and the
    error is terminal.
  - `valid_from` is an ISO date within the cited episode's
    `occurred_at` ± 1 day, otherwise it is replaced by the episode's
    date and counted.
  - v1-shaped output is rejected: the existing
    `isinstance(version, bool) or version != SCHEMA_VERSION` check
    refuses `version: 1`, and a v2 candidate without the `fact` key is a
    missing-key error (exact key set), both terminal `schema_invalid`.
  - The fact object is validated by one helper,
    `memory/graph/candidates.py::validate_fact_object`, so reflection
    (D5) uses the same rules.
- **Sensitivity is inherited, never chosen by the model.** A fact
  proposal's `sensitivity` is copied from its cited episode, exactly as
  8A's `_create_proposal` copies `episode.sensitivity` into a memory
  (via `verify_candidates`). A fact from a `private` episode (for
  example a `private` Inbox note, D12) is born `private`; a candidate
  the verifier flags `private` (the 8A `sensitivity` key it sets from
  the episode) is born `private` whatever else it carries.
  Personal-class only routes **extraction** to local providers (8A D9);
  it does not by itself make a fact `private`, which keeps 8A's memory
  rule (transcript and calendar facts follow their episode's
  sensitivity). A reflection candidate (D5) takes
  the sensitivity of the episode its quote was verified against. An
  owner edit keeps the old row's sensitivity unless the owner changes
  it in the form.
- **Entity sensitivity is derived, not stored.** `MemoryEntity` has no
  sensitivity column. An entity is **private** iff it has no live
  (current or pending, not trashed or archived) non-`private` fact as
  subject or object. `memory.graph.selectors.entity_is_private(entity)`
  and the queryset annotation `with_privacy()` implement the rule in
  one place; the vault mirror (D12), retrieval and the read tool use
  it. An entity whose only facts are private is therefore never named
  in an open-tree note or a model payload.
- **Provenance is unchanged.** The 8A verbatim check (`quote ≥ 12`
  chars, a substring of the cited episode's `owner_text` in the same
  workspace) applies to facts too. Entity names must also occur in the
  quote, after normalisation, otherwise the candidate is dropped
  (`fact_entity_not_in_quote`). This stops a model from attaching an
  owner sentence to an invented entity.
- **Entity resolution is code, not model.**
  1. An exact `normalised_name` or alias match within the workspace and
     kind.
  2. Otherwise a new entity is proposed together with its fact, with
     `pending_approval` riding on the fact. An entity with no approved
     learned fact is invisible to retrieval.
- **Reconciliation** (purpose `memory.fact_reconciliation`, tier `mid`,
  one call per workspace on nights with fact candidates):
  - Input: the night's **normal** fact candidates, and the current
    non-`private` facts with the same subject and predicate (≤ 50,
    fenced as `<existing_fact untrusted>`). The call can reach a cloud
    provider, so no `private` text enters it: a `private` candidate
    skips reconciliation and stays `new` (counted
    `reconcile_private_skipped`); the owner can still supersede by an
    edit.
  - Output schema v1: `{"version":1,"decisions":[{"candidate_index",
    "action": "new"|"supersede"|"duplicate", "supersedes_fact_id"}]}`.
  - The code checks that a `supersede` names a current fact with the
    same subject, predicate and workspace, and that the candidate's
    `valid_from` is not earlier than the target's (the D2 supersede
    order; nightly extraction of older episodes and the 7-day
    transcript backfill can produce one). Otherwise the decision falls
    back to `new` and is counted (`supersede_invalid` or
    `supersede_order`). A target approved or edited after
    reconciliation is caught again by `approve_fact` (409).
  - A `supersede` decision becomes a `pending_approval` fact whose
    `supersedes` FK (D2) names the validated target. **When approved**,
    `approve_fact` calls `supersede(old=supersedes, new=proposal)`
    atomically (D2). Nothing is applied by the model. This is ADR-012's
    "never applies". The digest's "supersedes" line is rendered from
    that FK.
- **Chat extraction (8A, schema v2) is unchanged.** It emits no `fact`
  candidates in 8B (see the note at the top of D4).
- Justification: facts are short and structured, so the cheap tier
  extracts them in the existing call (no extra call per episode). The
  supersede judgement needs the current facts in view, so it is one
  `mid` call per workspace-night, which is bounded.

### D5 — Weekly reflection

- **Schedule and scope.** A beat task, Sunday 03:30 Europe/Istanbul,
  `memory.ingestion.tasks.run_weekly_reflection_task`, runs once per
  workspace with at least 5 approved items that week.
- **Purpose** `memory.reflection`, tier `mid`, prompt
  `friday_memory_reflection` (seeded by `seed_ai_defaults`).
- **Input:**
  - the week's approved memories and facts (titles and statements);
  - the owner texts of that week's episodes that are still live (the
    TTL limits them; at most 40, newest first, each ≤ 1000 chars);
  - the current `current_focus` and `active_projects` blocks.

  Everything except the owner texts is fenced as untrusted context.
  The call can reach a cloud provider, so `private` memories and facts,
  and the owner texts of `private` or personal-class episodes, are
  excluded from the input (the `cloud_eligible()` / `model_eligible()`
  helpers and `needs_personal_data`). A reflection candidate therefore
  quotes a normal episode and is born `normal`.
- **Output:** at most 5 candidates of the D4 v2 shape. These are
  pattern-level observations (for example a `current_focus` diff, or a
  `prefers` fact), each with a quote verified against one of the
  supplied owner texts. No quote, no proposal.
- **Caps (in code):**
  - Reflection proposals count toward that night's cap of 10 and toward
    the weekly core-block cap of 3 (8A D10).
  - Reflection never targets `self` or `developer`.
  - With no budget left, the run is skipped and counted. It is never
    retried in a loop.
- **No auto-apply.** Output is proposals only, approved per item.

### D6 — Caps across the new proposal kinds

| Cap | Value | Counts |
|---|---|---|
| per owner per night | 10 | memory + core-block + fact proposals (8A cap, now including facts and reflection) |
| core-block per ISO week | 3 | unchanged (8A), reflection included |
| fact proposals per night | ≤ 6 of the 10 | so facts cannot crowd out memories; the rest are discarded and counted (`fact_cap`) |
| reflection proposals per week | 5 | inside the nightly 10 of Sunday |
| new entities per night per workspace | 20 | an entity beyond it drops its fact (`entity_cap`) |

Bulk approve stays `work_log`-only. Every fact is approved per item.

### D7 — Forget-source, trash and purge reach facts

- `forget_source` also archives the source's `learned` facts and
  rejects its pending fact proposals, under the same operation id.
  `restore_source` brings back exactly those rows. An entity left with
  no live fact is trashed with the same id.
  - A rejected pending proposal has its `supersedes` cleared (the D2
    check); the proposal's forget never touches its target.
    `restore_source` returns it to `pending_approval` **without** the
    link (the target may have changed meanwhile), so it comes back as a
    plain `new` proposal.
  - When the forgotten source owns the **target** of another source's
    pending proposal, the target is archived as usual and the proposal's
    later approval is refused with `supersede_target_changed` (D2).
  - `purge_trash` of a fact first rejects any pending proposal whose
    `supersedes` names it (clearing the link), so the `PROTECT` FK never
    blocks a gated purge.
- Owner delete of one fact or entity is a reversible trash move
  (ADR-009, a required `channel`, `CHANNEL_WEB`). Trashing an entity
  trashes its learned facts, with the same id.
- `purge_trash` gains the keys `fact_ids` and `entity_ids`. It stays
  gate-class, decided on `/approvals`.
- Graphify rows are outside forget and trash (D3, D10). Turning the
  repo's `graphify_enabled` switch off **retires** them: every current
  `system` fact of that repo gets `valid_to = now`, and its entities
  drop out of every read. Nothing is deleted, and re-enabling re-imports
  on the next head. Permanent removal is the gate-class `purge_trash`
  with a new key `graphify_repo_ids`, so no delete skips the gate
  (rule 4, ADR-009).
- The vault mirror prunes entity notes of trashed or archived rows on
  its next run (D12).

### D8 — Hybrid retrieval

- **Full text.** `Memory`, `MemoryFact` and `KnowledgeChunk` each gain a
  `search` column. It is a Postgres **generated** `tsvector`, with one
  expression per table, because the text columns differ (verified on
  the models: `Memory` has `title` and `content`, `KnowledgeChunk` has
  only `content`, and `MemoryFact` (D2) has only `statement`):

  | Table | Generated expression |
  |---|---|
  | `Memory` | `setweight(to_tsvector('turkish', title), 'A') \|\| to_tsvector('turkish', content) \|\| to_tsvector('simple', title \|\| ' ' \|\| content)` |
  | `KnowledgeChunk` | `to_tsvector('turkish', content) \|\| to_tsvector('simple', content)` |
  | `MemoryFact` | `to_tsvector('turkish', statement) \|\| to_tsvector('simple', statement)` |

  - The `simple` part keeps English and code identifiers that the
    Turkish stemmer would mangle.
  - Each has a `GinIndex`.
  - It is a raw-SQL migration, because Django's `GeneratedField` cannot
    express `setweight` portably.
  - Queries use `websearch_to_tsquery` for both configs, OR-ed.
- **HNSW over untyped vectors.** A management command
  `build_vector_indexes` (idempotent, run by `post_migrate` and checked
  by preflight) creates one **partial expression index** per active
  embedding provider and table:

  ```sql
  CREATE INDEX CONCURRENTLY IF NOT EXISTS <table>_hnsw_<provider8>
    ON <table> USING hnsw ((embedding::vector(<dim>)) vector_cosine_ops)
    WHERE embedding_provider_id = '<provider uuid>';
  ```

  Queries compare `embedding::vector(<dim>) <=> query::vector(<dim>)`
  under the same `WHERE`, so the planner uses the index. The
  existing three guards (same provider, same dimension, non-null) stay
  in the SQL.
  Justification: the MVP 3 policy deliberately left the columns untyped
  so that a provider swap does not orphan stored vectors. A per-provider
  expression index keeps that property and still gets HNSW. A typed
  column would force a migration on every provider change. HNSW is in
  pgvector ≥ 0.5.0, and the dev stack has 0.6.0.
  - **Dimension cap.** pgvector's HNSW index on `vector` supports at
    most 2000 dimensions. For a provider whose stored dimension is
    above 2000, `build_vector_indexes` creates no index, logs a
    warning naming the provider and dimension, and reports it as
    `skipped_dimension` (preflight `retrieval_indexes` shows it as a
    **warn**, not a failure). Retrieval for that provider still works
    as an exact (sequential) scan under the same guards. It never
    falls back to `halfvec` silently; that would be a separate
    decision.
- **Retriever** `memory.retrieval.hybrid.retrieve(*, actor, workspace,
  query_text, personal_data, as_of=None, k=60)`. It runs these steps:
  1. **Workspace filter first.** Every candidate query starts from
     `for_actor(actor, workspace=workspace)` with the existing
     eligibility rules (`cloud_eligible()` for memories, the D2
     **retrievable** facts, that is `approved` `learned` current facts
     that are not `private` (the `cloud_eligible()` parity: a fact
     query helper `MemoryFact.objects.model_eligible()` is the only
     sanctioned path into a payload), and ready, non-trashed chunks). It never ranks across
     workspaces and filters later. `system` (Graphify) facts are never
     candidates: no vector list, no full-text list, no hop.
  2. Vector top-50 and full-text top-50 per table.
  3. **RRF:** `score = Σ 1/(60 + rank_i)` over the lists an item appears
     in.
  4. **Recency decay:** `score × 0.5^(age_days / half_life)`. The
     half-life is `MEMORY_RETRIEVAL_HALF_LIFE_DAYS` (default 45) and
     applies to `episodic` and `work_log` memories and learned facts.
     It does not apply to `semantic`, `preference` or `procedural`
     memories (`system` facts never reach this step). Age is
     `updated_at` for memories
     and `valid_from` for facts.
  5. **Hop expansion:** the entities of the top-10 facts seed
     `neighbourhood(hops=MEMORY_RETRIEVAL_HOPS, default 1, max 2)` with
     its defaults `include_system=False, include_private=False` (D2),
     so a hop never reaches a Graphify edge or a `private` fact, and an
     entity that `entity_is_private` (D4) is never named in a hop
     statement. Expanded facts enter at `score × 0.5` per hop.
  6. The result is cut to the existing budget: memories and facts share
     `MEMORY_CHAR_BUDGET` (4000).

  Knowledge chunks keep their own selector and budget.
  `knowledge.context.select_context_chunks` gains the same vector plus
  full-text fusion inside `knowledge`, using a pure helper
  `core.ranking.rrf(lists, k=60)`, so `memory` never imports
  `knowledge` for retrieval.

  A `GatewayError` on the embed degrades to full text only, never to a
  failed turn, like the existing ranker.
- **Flag.** `MEMORY_RETRIEVAL_MODE` is `legacy` (the default) or
  `hybrid`. `build_context` calls `select_context_memories` (legacy) or
  the hybrid retriever.
  - The closeout flips the default only if the eval set (D9) shows
    hybrid ≥ legacy on recall@10 and answer accuracy.
  - Otherwise the default stays `legacy`, and that is recorded.
- **Facts in the prompt.** A `<memory_fact>` fenced block inside the
  memory block. Each line is `statement (valid_from–valid_to)`, with
  `neutralize_fence` and `strip_invisible_formatting`. A `private` fact
  is never injected (`cloud_eligible` parity), and `personal_data` turns
  follow 8A.
- **Embedding policy.** Only retrievable-candidate facts are embedded:
  the fact embedding job (the `Memory` lease pattern) selects
  `origin = learned` rows that are `approved` and current. `system`
  facts keep `embedding = NULL` for good, so a Graphify import (up to
  60 000 facts) makes **zero** embed calls and zero `ModelUsage` rows.
  Pending, rejected and archived facts are not embedded either.
- **Read tool `memory_graph_lookup`** (`tools/memory_graph.py`,
  `ReadToolSpec`, `data_class=firm`, `CHANNEL_WEB` only, audited
  `memory.graph.read`). Arguments: `entity` (≤ 200), `as_of` (an
  optional date) and `hops` (1–2). It returns fenced current or as-of
  facts of the conversation's workspace. It is the path that answers
  "ne zamandan beri?".
  - Its output is fenced into the system message and goes to whatever
    provider serves the turn, cloud included, so it calls
    `neighbourhood(..., include_system=True, include_private=False)`
    and never returns a `private` fact, nor an entity that
    `entity_is_private` (an `entity` argument naming one answers "not
    found", the same as a missing entity, so the name's existence is
    not confirmed).
  - It is the only model path that reads `system` facts (besides the
    deterministic `self` summary), so code questions ("hangi modül
    `memory.graph`'ı import ediyor?") are answered on demand rather
    than by flooding the memory block.
- **Audit:** every retrieval records `memory.context_accessed` with ids
  only, as today, and fact ids are added.
- **`ModelUsage` context report:** retrieval mode, candidate counts per
  list, hop count and fact count. Never text.

### D9 — The private eval set and its baseline

- **Harness** in the `memory/evals/` package:
  - `schema.py` holds the case format v1:
    `{id, workspace_ref, sensitivity, tags:[...], setup:{memories, facts, episodes},
    history:[{role, content}], question, expected:{must_contain:[...],
    must_not_contain:[...], relevant_ids:[...]}}`. `history` is
    optional (prior `user`/`assistant` turns, D11 "Enabling per
    purpose"); retrieval cases leave it empty;
  - `runner.py` and `manage.py memory_eval run|baseline|compare`.
- **Two sets:**
  - **Public seed** (`memory/evals/cases/public/*.jsonl`, committed):
    about 60 synthetic Turkish, English and code-switched cases written
    for this repo. They cover two workspaces with leak traps
    (`must_not_contain` a firm term in a personal question), temporal
    questions ("2026-10'da hangi servis?"), supersession, 2-hop
    questions, and transcript-shaped episodes. CI runs them with
    FakeAdapter (retrieval metrics only).
  - **Private set**, owner-authored, about 100 cases from real work. It
    lives at `MEMORY_EVAL_PRIVATE_DIR` on the owner's host (default
    `~/friday-evals/`, outside the repo, mode 0700). It is **never
    committed**: a CI test fails if any file under `memory/evals/cases/`
    other than `public/` exists. It runs only on the owner's stack.
- **Metrics:**
  - **Deterministic:** recall@10 of `relevant_ids`, leak rate (any
    `must_not_contain` term in the retrieved context), and temporal
    correctness (as-of answers).
  - **Answer accuracy:** the share of cases whose generated answer
    contains every `must_contain` key phrase and no `must_not_contain`
    term (normalised substring match). **No LLM judge**: a judge would
    add a second unvalidated model into the metric that gates Headroom.
- **Model calls.** Answer generation uses the purpose
  `memory.eval_answer` (tier `mid`). It is budgeted under
  `learning_eval` (monthly 5 USD, `AI_BUDGET_LEARNING_EVAL_MONTHLY_USD`)
  and writes one `ModelUsage` per call with the `eval_run_id` in the
  context report. `memory.eval_answer` is always in the compression
  bypass set. A compression compare (D11) issues its answer calls under
  the candidate purpose instead, in both arms, under the same eval-only
  budget.
- **Isolation.** The set runs against a separate eval database: the
  `memory_eval run|baseline|compare` subcommands refuse to run unless
  `DJANGO_SETTINGS_MODULE` points at the eval settings, which use their
  own DB name. They never write cases into the owner's live workspaces.
  - **The eval database persists between runs**, so the monthly cap
    holds. Each run clears only the previous run's case setup rows
    (memories, facts, episodes, entities of the eval workspaces); it
    never drops the database and never deletes `ModelUsage` rows.
    The `learning_eval` budget check therefore sums the month's eval
    `ModelUsage` across every run, exactly like a live budget.
  - **Defence in depth against a recreated database:** every result
    file records its `spend_usd`, and before each run `memory_eval`
    adds the current month's `spend_usd` of the result files in the
    results directory to the database sum and takes the larger of the
    two totals. A dropped or recreated eval DB therefore cannot reset
    the 5 USD cap. A run that would start over the cap is refused with
    no call.
- **Eligibility applies to eval setup rows.** A case's `setup` memories,
  facts and episodes are loaded as ordinary rows of the eval
  database, so retrieval and the answer call apply exactly the chat
  rules: `cloud_eligible()` for memories, the `private` fact rule (D8),
  and `needs_personal_data` for episodes. A case may also carry
  `sensitivity: private` (case format v1, default `normal`): every setup
  row of such a case is stored `private`, and its answer call (and both
  D11 compare arms) runs with `personal_data=True`, so it goes only to a
  local provider. With a cloud-only provider set such a case is counted
  `no_provider` (no call, no answer metric) rather than sent. The
  runbook tells the owner to flag every case built from firm or personal
  work data `private`.
- **Results.** The eval run writes a metrics-only result file
  (`<MEMORY_EVAL_PRIVATE_DIR>/results/<run id>.json`, mode 0600; for the
  public set under `MEMORY_EVAL_RESULTS_DIR`). It is recorded in the live
  database by `memory_eval record <file>`, which runs under the normal
  settings and refuses the eval settings. `MemoryEvalRun` is a
  **`WorkspaceScopedModel`** anchored to the **owner's personal
  workspace** (the command takes `--owner <username>` and resolves that
  user's personal workspace; any other workspace is refused). Its
  fields:
  - `set` (`public` or `private`), `kind` (`run`, `baseline` or
    `compare`), `retrieval_mode`, `compression_purpose` (null or a
    purpose name), `arm` (null, `a` or `b`), `started_at` and
    `finished_at`;
  - case counts (`total`, `answered`, `no_provider`, `budget_stopped`)
    and `spend_usd` (the run's eval spend, used by the budget check
    above);
  - the aggregate metrics (recall@10, leak rate, temporal correctness,
    answer accuracy, `pre_tokens`/`post_tokens` for compares) and a
    `verdict` (`pass`, `fail`, `inconclusive` or null).

  It stores **no** case text, case ids, `workspace_ref` values, tags,
  questions, answers or per-case rows, and **no per-workspace
  breakdown**. `record` refuses a result file with any key outside this
  list (unknown keys terminal). The numbers also go to this brief's
  "Go/no-go record".
- **Read access.** `GET /api/v1/memory/evals/runs` (branch 3) lists the
  rows through `MemoryEvalRun.objects.for_actor(request.user)`: web
  channel, `IsOwner`, human, default authentication only. Because the
  rows live in the owner's personal workspace, a firm-only member sees
  nothing, and no installation-level exception to rule 1 is needed.
- **Baseline.** `memory_eval baseline --set private` on the owner's
  stack with `legacy` retrieval and no compression is **the** recorded
  baseline. Hybrid retrieval and each Headroom purpose are compared
  against it.

### D10 — Graphify AST-only job

Verified 2026-11-21 against PyPI and the official repository
(`github.com/Graphify-Labs/graphify`), and by running the pinned version
in a scratch virtualenv on this machine. Nothing was installed in the
repo.
- **Package and pin.**
  - The PyPI package is **`graphifyy`** (double *y*). The README warns
    that other `graphify*` packages are not affiliated, which is a
    typosquat risk, so the pin is by name **and** hash.
  - Pin: `graphifyy==0.9.103` (uploaded 2026-11-05; wheel
    `graphifyy-0.9.103-py3-none-any.whl`, sha256
    `7948c030f8b04153d88bc1ee23a485ddaa0c2d6aaa03a748c177e3c217bcfe34`).
  - The newest release, 0.9.114, was uploaded 2026-11-20. It is not
    pinned, because the rule is "≥ 14 days old". The branch re-checks
    and may pick a newer release that is ≥ 14 days old.
  - Licence Apache-2.0. Python ≥ 3.10.
  - Runtime dependencies: `networkx`, `numpy`, `rapidfuzz`, `pyyaml`
    and `tree-sitter` with about 25 grammar packages. No LLM SDK is in
    the base install.
- **Usage (observed).**
  - `GRAPHIFY_BACKEND=none GRAPHIFY_QUERY_LOG_DISABLE=1 graphify update
    <path> --no-cluster` re-extracts code files with tree-sitter, makes
    no LLM call, and writes `<path>/graphify-out/{graph.json,
    manifest.json, cache/}`.
  - `graph.json` (0.9.103) has the top-level keys `nodes`, `links`,
    `hyperedges`, `directed`, `multigraph`, `input_tokens` and
    `output_tokens`.
  - A node has `id`, `label`, `file_type`, `source_file`,
    `source_location` and `_origin: "ast"`, plus optional `_callable*`
    flags.
  - A link has `source`, `target`, `relation` (`imports`,
    `imports_from`, `contains`, `calls`, …), `context`, `confidence`
    (`EXTRACTED` or `INFERRED`), `source_file`, `source_location`,
    `weight` and `_origin`.
  - The README documents `graph.schema_version` in some modes, but the
    `update` output observed here has none. The importer therefore pins
    the observed shape in its own JSON schema and treats any unknown
    top-level key as a refusal.
  - The README states "no telemetry". The query log is disabled by the
    env var.
- **Trigger.**
  - `HarnessRepo.graphify_enabled` (bool, default false; the owner sets
    it for the Friday repo) and `last_graphify_sha`.
  - The existing runner-beat git producer (`harness/ingestion.py`
    `ingest_repo`) already learns the default branch's head. When it
    differs from `last_graphify_sha`, it creates a `GraphifyRun`
    (`WorkspaceScopedModel` of the repo's workspace, lease-fenced:
    `pending`, `running`, `succeeded`, `failed`) and dispatches it on
    the runner queue.
  - "On merges to `main`" is therefore "on a new default-branch head",
    at most one run per head and at most one in flight per repo.
- **Container.**
  - New image `harness/docker/graphify.Dockerfile`:
    - a pinned Python base by digest;
    - `pip install --require-hashes --no-deps -r graphify.lock.txt`, a
      hash-locked list of `graphifyy` and every transitive wheel;
    - install scripts are not run (wheels only, `--only-binary=:all:`);
    - a non-root user.
  - New runner primitive `launch_graphify_container(*, image, clone_dir,
    out_dir, name)`. It keeps every `launch_container` policy
    (`--pull never`, `--cap-drop ALL`, `no-new-privileges`, `--user`,
    the resource caps) and adds:
    - **`--network none`**;
    - `--read-only` with a `tmpfs` `/work` (size-capped);
    - `/repo` read-only, and `/out` as the only writable bind.
  - No env file and no credentials. The clone uses the existing
    `clone_repo`.
  - The entrypoint `graphify_run.sh` copies `/repo` to `/work/repo`,
    excluding `.git`, runs the command above, then copies
    `graphify-out/graph.json` to `/out/graph.json`. A graph over 20 MB
    is a failure.
  - **Reading `/out` on the host.** `/out` is writable by the container,
    so the host treats it as hostile. The importer opens the out
    directory and then `graph.json` relative to it with
    `os.open(..., O_RDONLY | O_NOFOLLOW | O_NONBLOCK)` (reusing the
    descriptor walk of `tools/vault_paths.py`'s pattern in a small
    `harness/safe_read.py`), `fstat`s the descriptor, refuses anything
    that is not a regular file (`S_ISREG`; a symlink, FIFO, device or
    directory is refused) or whose `st_size` is over 20 MB **before
    reading a byte**, then reads at most 20 MB + 1 from that same
    descriptor (a file grown after the stat is still refused). A
    refusal fails the run with nothing imported. The out directory is a
    fresh `mkdtemp` (mode 0700) per run and is removed afterwards.
- **Import** `harness/graphify.py`, through the memory port
  `memory.graph.ports.import_code_graph(*, workspace, repo_ref,
  commit_sha, committed_at, graph)`:
  1. Validate against `harness/graphify_schema_v1.json` (bounded
     strings and arrays).
  2. Keep only `_origin == "ast"` nodes and links, and links with
     `confidence == "EXTRACTED"`.
  3. Map the kinds:
     - file nodes → `code_module` (label = the repo-relative path);
     - classes → `code_class`;
     - callables → `code_function`;
     - `contains`, `imports`/`imports_from`, `calls` and `inherits` →
       the Graphify predicates.
  4. Scrub every label (identifiers can embed tokens).
  5. Drop external targets (for example `os`) unless they are a
     top-level import, which becomes a `tool` entity named after the
     package.
  6. Apply the caps: ≤ 20 000 entities and ≤ 60 000 facts. Over the cap
     the run fails and nothing is replaced.
  7. **Temporal replace.** In one transaction, facts in the new
     snapshot that match a current `system` fact (subject, predicate,
     object) keep it. New ones get `valid_from = committed_at`. Current
     `system` facts absent from the snapshot get
     `valid_to = committed_at`. That makes "since when does module A
     import B" answerable.
  8. Audit `memory.graph.imported` with counts and the sha. The run row
     keeps counts only.

  The output is data: it is never executed and never shown unfenced.
- **The `self` template.**
  - `assistant/self_block.py` gains one template line, `codebase`:
    `"Kod tabanım ({sha7}): {module_count} modül; uygulamalar: {apps}."`.
  - It is filled from `memory.graph.selectors.code_graph_summary(
    workspace)`. `apps` is the top-level packages of the `code_module`
    entities that hold a `models.py`, sorted and capped at 25.
  - It is read only when the installation owner's personal workspace
    has a succeeded run. The assembler stays the only writer of `self`,
    with no model call. The 8A test "every sentence traces to the
    template, snapshot or registries" gains "or the Graphify summary".
- **Governance.**
  - `docs/mcp/graphify.md` is the manifest (from `_template.md`): the
    source, the pin and hash, read-only, `--network none`, no
    credentials, no telemetry, the output treated as data, review date.
  - The `docs/mcp/allowlist.md` row "Graphify (CLI/skill)" gains a
    second row, "Graphify (`graphify` harness job)", runtime-allowed in
    the harness runner only.
  - The dev-workflow row stays.

### D11 — Headroom as a pre-send stage

Verified 2026-11-21 against PyPI, the official repository (the PyPI
project URLs now point to `github.com/headroomlabs-ai/headroom`; the
`chopratejas/headroom` link redirects there) and a scratch-venv run of
the pinned version on this machine:
- **Package and pin.**
  - Package `headroom-ai`. Pin `headroom-ai==0.43.1` (uploaded
    2026-11-05; the abi3 wheels include `manylinux_2_28_x86_64`, sha256
    `2e9cd1a9aab42f60b00a9f7c8a85cf82815b51b1989179e5fdd40ddd09915d6e`).
    0.44.0 (2026-11-10) is under 14 days old.
  - Apache-2.0, Python ≥ 3.11.
  - **Its base install requires `litellm`** (also `opentelemetry-api`,
    `tiktoken`, `pydantic`, `ast-grep-cli`, `hf-xet` and others).
- **API (observed).** `from headroom import compress`;
  `compress(messages, model=..., model_limit=..., optimize=True,
  config=CompressConfig|None)` returns a `CompressResult` with
  `messages`, `tokens_before`, `tokens_after`, `tokens_saved`,
  `compression_ratio`, `transforms_applied`, `lossless` and
  `reverted_messages`.
  - By default user messages are protected
    (`transforms_applied` showed `router:protected:user_message`).
    JSON tool output went through `smart_crusher`.
  - On a JSON tool message the run measured 1555 → 875 tokens.
  - `compress()` did **not** import `litellm` at runtime.
  - Without network it logged a failed `tiktoken` encoding download and
    fell back to estimation.
  - Telemetry: an anonymous beacon is **on by default**. It is disabled
    by `HEADROOM_TELEMETRY=off`, `HEADROOM_BEACON=off`, `DO_NOT_TRACK=1`
    or `HEADROOM_OFFLINE=1`.
  - The prose compressor (Kompress) needs a HuggingFace model; it is not
    used here.
- **Decision: a sidecar process, never imported by Friday code.**
  - Importing `headroom-ai` into web or worker would install `litellm`,
    a second-door library that the roadmap names as "not a dependency"
    (§c.8, ADR-003 amendment). The base wheel is also native code (an
    abi3 extension) in the request hot path.
  - So the stage calls a **`compressor` sidecar**
    (`deploy/compressor/`, its own image):
    - `pip install --no-deps --require-hashes` of `headroom-ai` and
      exactly the transitive wheels `compress()` needs, **without
      `litellm`**;
    - a build-time test imports `headroom` with an import blocker for
      `litellm`, `openai` and `anthropic` and compresses a fixture. If
      that fails at branch start, Headroom is **not adopted**: the flag
      stays off and the brief records why, rather than installing
      `litellm`;
    - `tiktoken` encodings baked in at build (`TIKTOKEN_CACHE_DIR`);
    - env `HEADROOM_OFFLINE=1`, `HEADROOM_TELEMETRY=off`,
      `HEADROOM_BEACON=off`, `DO_NOT_TRACK=1` and `HF_HUB_OFFLINE=1`;
    - it sits on an `internal: true` compose network shared only with
      web and worker, so it has no egress. It holds no provider keys and
      runs read-only with `cap_drop: ALL`;
    - its server, `deploy/compressor/server.py`, is a small stdlib HTTP
      server that exposes `POST /compress` and calls only `compress()`.
      No proxy mode, no `wrap`, no MCP.
  - `backend/` never imports `headroom`. A test greps for it, as for
    Piper, and a lock test fails if `litellm` appears in
    `backend/uv.lock`.
- **Gateway stage** `ai_core/compression.py`, called inside
  `InferenceGateway.complete` after budget and provider selection and
  before the adapter call. It applies only when **all** of these hold:
  - `AI_COMPRESSION_ENABLED` is true (default **false**);
  - the purpose is in `AI_COMPRESSION_PURPOSES` (default **empty**);
  - the purpose is not in the hard `COMPRESSION_BYPASS` set.

  The bypass set is a code constant, not settings, and covers:
  - approval and gate rendering;
  - tool-call and argument prompts (`router.intent`,
    `router.task_draft`, `router.connector_args`, `agent.plan`,
    `agent.skill_match`);
  - trust checks and reviews (`harness.review_summary`, Phase 10's
    trust-check purposes when they land);
  - the learning prompts that check provenance
    (`memory.episode_extraction`, `memory.extraction`,
    `memory.consolidation`, `memory.fact_reconciliation`,
    `memory.reflection`);
  - `memory.eval_answer`, **unconditionally**. The compression arm of
    the eval does not use this purpose at all (see "Enabling per
    purpose" below), so the constant never needs a condition;
  - `ops.preflight`.

  A test classifies every purpose in `AI_PURPOSE_TIERS` **and** in
  `AI_UNTIERED_PURPOSES` as bypass, eligible or not-applicable (the
  coverage-test pattern of `ai_core/tests/test_purpose_coverage.py`).
  Among the untiered purposes, `ops.preflight` goes through
  `complete()` and is in the bypass set; the others are not-applicable
  by construction: embeddings
  (`memory.retrieval`, `knowledge.*`) and speech (`voice.*`) never pass
  through `complete()` with chat messages, and the external-CLI
  `harness.discovery|implementation|review` rows come from
  `record_external_usage`, which sends nothing. A new purpose in either
  setting that is not classified fails the test.
- **What is sent (grounded in `assistant/context.py`).** `build_context`
  produces exactly this list, verified on the integration branch:
  1. one `system` message: the `friday_system` prompt, core blocks, the
     intent instruction, the fenced memory block, the knowledge block,
     the firm profile and, when a connector or node read ran, the fenced
     `<connector_result>` block (`render_connector_block`), all
     concatenated into that **one** system message;
  2. the stored history, `Message` rows with status `complete`, with
     the role copied from the row (`user` or `assistant` in practice;
     `Message.Role` also allows `system`, and such a row is treated as
     a system message and never sent);
  3. the new `user` message (`new_user_content`).

  There is **no tool-role message** anywhere in Friday's chat payload:
  connector and node results live inside the system message. So the
  stage sends **only `assistant` history messages** to the sidecar.
  It never sends the system message, any `user` message, or the last
  message of the list. Consequences, stated so the eval measures the
  right thing:
  - **Tool outputs are out of reach** for compression in 8B. They are in
    the system message with the core blocks, and splitting them into
    separate role messages would change the fencing contract of Phase 5
    D4 and Phase 9 D9. That is not done here; if a later phase wants
    tool-output compression, it changes `build_context` in its own
    reviewed branch.
  - What can shrink is long prior assistant replies (summaries of
    earlier reads, code listings). The saving is bounded by how much
    assistant history a conversation has, which the eval measures.
  - Core-block, `self`, memory-block and fenced read text cannot be
    compressed by construction. A test asserts it on a captured
    sidecar payload of a **real `build_context` output** (a conversation
    with core blocks, a memory block, a connector block and three
    history turns): the payload holds exactly the assistant history
    messages, in order, and nothing else.
- **Failure.** On a sidecar timeout (`AI_COMPRESSION_TIMEOUT_MS`,
  default 300) or error, the original messages are sent and the failure
  is recorded. The stage changes cost, not correctness.
- **Accounting.** The context report of that call's single `ModelUsage`
  row carries `compression: {applied, pre_tokens, post_tokens,
  transforms, lossless, error}`. The sidecar makes no model call (a
  local transform), so it writes no `ModelUsage` of its own. That is
  rule 3, unchanged.
- **Enabling per purpose.** `memory_eval compare --compression
  <purpose>` runs the private set twice and compares. The arms are
  defined so that the comparison cannot pass vacuously:
  - **Case format.** D9's case format v1 gains an optional `history`
    (≤ 12 messages, `user`/`assistant` only, each ≤ 4000 characters).
    Cases tagged `compression` must carry at least two `assistant`
    messages totalling ≥ 2000 characters (the loader refuses one that
    does not). The public seed gains about 10 such cases with realistic
    long assistant replies (a summarised session list, a code listing,
    a deploy log summary); the owner's private set should add real ones
    (runbook `memory-eval.md`).
  - **Both arms use the candidate purpose.** The answer call of each arm
    is issued under `<purpose>` itself (for example `chat`), with the
    messages laid out as `build_context` lays them out: one system
    message (the retrieved context), the case's `history`, then the
    question as the last `user` message. Arm A runs with compression
    off; arm B with `AI_COMPRESSION_ENABLED=True` and
    `AI_COMPRESSION_PURPOSES={<purpose>}` (the runner overrides the
    settings per arm). Retrieval-only cases (no `history`) run in both
    arms and must give identical payloads, which is a sanity check.
  - **Budget and isolation.** These calls run only under the eval
    settings module (own DB, D9). There, the `learning_eval` budget
    covers `memory.eval_answer` **and** the purpose under comparison
    (`config/settings/eval.py` sets `AI_LEARNING_EVAL_PURPOSES`), and
    each `ModelUsage` row carries `eval_run_id` and `eval_arm` in its
    context report, so eval spend is never mistaken for chat traffic.
  - **Non-vacuity.** The compare reports `pre_tokens` and `post_tokens`
    summed over arm B. If arm B compressed nothing (`applied` false on
    every call, or `post_tokens == pre_tokens`), the result is
    `inconclusive`, never `pass`, and the purpose cannot be enabled.
  - **Gate.** A purpose may be added to `AI_COMPRESSION_PURPOSES` only
    with a recorded result that is not `inconclusive`, with **≤ 1
    point** answer-accuracy loss and **zero** extra leaks. The record
    goes in this brief's go/no-go table. `chat` is the only candidate
    today, because it is the only eligible purpose whose payload holds
    multi-turn assistant history.
- **Governance.** `docs/mcp/headroom.md` (the manifest: sidecar only,
  no egress, telemetry off, `litellm` excluded, the pin and hashes) and
  an allowlist row "Headroom (`compressor` sidecar)".

### D12 — Obsidian: mirror format v2, conflicts, `Inbox/` import-once

The vault stays a mirror (ADR-005, unchanged). Every write below happens
**inside an approved `vault_export` run** (gate-class, existing). The
approval's rendered description gains the counts it will touch: the
memories, the entity notes, the inbox notes waiting, and the conflicts
detected in a dry pass. A run never changes Postgres except by creating
episodes.

- **Format v2.**
  - Frontmatter gains `friday_format: 2` and `content_sha256`, the
    sha256 of the rendered body below the frontmatter.
  - New entity notes: `03_Workspaces/<ws>/Entities/<entity-id>.md`.
    **Private entities** (D4's derived rule: no live non-`private`
    fact, via `entity_is_private`) go to `_private/<ws>/Entities/`
    instead. An open-tree note renders only the entity's non-`private`
    facts; its `private` facts appear only in a companion note under
    `_private/<ws>/Entities/<entity-id>.md`, and a wiki-link from an
    open note never names a private entity (it is omitted). When an
    entity becomes private (its last normal fact is forgotten), the
    next run prunes the open note and writes the private one. Each note
    holds the entity's current approved or `system` facts (of its
    tier), its superseded history (with validity windows), and `[[<entity-id>]]` wiki-links between related
    entities, so Obsidian's graph view shows the graph.
  - Graphify `code_*` entities are not mirrored, because of their
    volume.
  - Prune rules are unchanged: export-owned, workspace match, a UUID id
    absent from the current set.
- **Conflicts.**
  - Before overwriting an existing export-owned file, the run reads it.
    If its body's sha256 differs from its own `content_sha256`, the
    owner edited it.
  - The run then writes the current file text to a conflict copy
    (validated descriptors, no follow) before overwriting, and counts
    it. **The copy keeps the file's tier:**
    - a file from the open tree goes to
      `_conflicts/<YYYY-MM-DD>/<workspace-id>/<file name>`;
    - a file from `_private/<workspace-id>/...` (a `private` memory or
      entity note) goes to
      `_private/_conflicts/<YYYY-MM-DD>/<workspace-id>/<file name>`,
      so private text is never written outside `_private/`.

    The tier is taken from the path being overwritten, not from the
    edited frontmatter (an owner edit cannot move a note out of
    `_private/`).
  - A v1 file without `content_sha256` is not a conflict. It is
    rewritten as v2.
  - The notice:
    - the run's `conflicts_saved` count on `VaultExportRun`;
    - a `work_log` row through the existing `record_tool_outcome`
      (Turkish: "Obsidian'da düzenlenmiş N not `_conflicts/<tarih>/`
      klasörüne kaydedildi; değişikliği web arayüzünde yapın.");
    - the count on the run's approval detail.

    The conflict file is never read back into Postgres.
- **`Inbox/` import-once** (the opt-in source, d5).
  - The folder is the documented `00_Inbox/` (ADR-012 and the stub say
    `Inbox/`; the vault layout names it `00_Inbox`, and ADR-012's
    implementation note records the mapping).
  - New `IngestionSource.Kind.OBSIDIAN_INBOX`, created disabled by the
    seed command.
  - When it is enabled, each approved `vault_export` run first scans
    `00_Inbox/` (top level and one level of subfolders, `.md` only,
    ≤ 200 files per run, each ≤ 64 KB):
    - **Skip:** anything with `source: friday_export` (the echo-chamber
      guard), and anything under `_imported/`.
    - **Mapping:** a new key prefix and rule type, because neither
      exists today. `memory/ingestion/mapping.py` accepts only `repo:`,
      `path:` and `calendar:` (`KEY_PREFIXES`), and `normalise_path`
      refuses relative paths ("path must be absolute or start with
      ~/"), so a vault-relative key would be unmapped. Branch 7 adds:
      - `WorkspaceMappingRule.RuleType.VAULT_PREFIX = "vault_prefix"`
        (a choices change, so a migration);
      - `KEY_PREFIXES["vault"] = RuleType.VAULT_PREFIX` and a
        `normalise_vault_path(path)`: the path must be relative to the
        vault root, start with `00_Inbox` as its first segment, contain
        no NUL or backslash, and normalise (`posixpath.normpath`) to a
        path with no `..` segment; an absolute path, a `~` path or any
        `..` escape raises `InvalidMappingKey`. `normalise_pattern`
        uses it for the new type, so rules and keys normalise the same
        way;
      - `resolve_workspace` matches `vault_prefix` rules with the same
        longest-prefix, segment-boundary rule as `path_prefix`
        (`_path_matches`);
      - the rule-write serializer accepts `vault_prefix` only on an
        `obsidian_inbox` source, and refuses the other types there.

      The scan passes `mapping_key = "vault:" + <vault-relative path of
      the note's folder>` (for example `vault:00_Inbox/work`). A note
      at the top level of `00_Inbox/` has the key `vault:00_Inbox`. An
      unmapped note is dropped and counted **and left in place**. It is
      not moved, so the owner can add a rule.
    - **Episode:** a mapped note becomes an episode through
      `record_episode`, with `owner_text` = the body (≤ 4000 after the
      scrub; longer bodies truncated and counted), `kind
      obsidian.inbox_note`, and `external_id` = the sha256 of the path
      plus the content. Frontmatter `sensitivity: private` → `private`.
    - **Provider class.** The `obsidian_inbox` source is **not**
      personal-class (`is_personal_class` stays false for it; ADR-012
      decision 11 lists calendar, mail and transcripts only). A normal
      Inbox note is therefore extracted by the ordinary (cloud-capable)
      `memory.episode_extraction` route, like a git commit. Only a note
      with frontmatter `sensitivity: private` becomes a `private`
      episode, which `needs_personal_data` routes to local providers
      only, and whose facts are born `private` (D4). The runbook and the
      approval description say so, so the owner flags sensitive notes.
    - **Move:** after the episode commits, the file is moved to
      `00_Inbox/_imported/<YYYY-MM-DD>/` with `renameat` under
      validated descriptors. It is never read again (dedupe holds even
      if the move fails).
  - Inbox notes are owner-authored by definition (the owner wrote them
    in their own vault), so they are quotable provenance. Pasted
    third-party text in a note is the owner's choice. R3 covers it.
- Justification: one approval per vault sync keeps rule 4 without a new
  gate class. The import is opt-in. Conflict copies preserve the owner's
  text instead of silently destroying it, which is today's behaviour.

### D13 — Transcript ingestion through the node

- **Two new read capabilities** (server-known contracts in
  `nodes/capabilities/v1/`, `class: read`, `data_class: personal`):

  | Capability | Args | Result (bounded) |
  |---|---|---|
  | `claude.transcripts.owner_turns` | `since` (an RFC 3339 instant, ≥ now − 7 days), `cwd_prefixes` (1–20, required, the D9 form of Phase 9), `max_sessions` (≤ 50), `max_turns` (≤ 200) | `{sessions:[{session_id, cwd, turns:[{turn_id, at, text ≤ 4000}]}], next_since, truncated}` |
  | `codex.transcripts.owner_turns` | the same | the same (`thread_id` as `session_id`) |

  - They are session reads, which ADR-014's read list covers
    ("session/thread lists and reads"). Branch 8 appends a one-line
    ADR-014 note naming them, and no class changes.
  - The per-directory Touch ID grant (ADR-013 decision 11) gates them
    on the Mac. Without it the node answers `not_granted`.
  - Per the Phase 9 D7 rule, the result carries no aggregates. Counts
    are derived server-side.
- **Owner-turn rules (normative for every node reader, and re-checked
  by the server).** A turn is returned only if all of these hold:
  - **Claude Code JSONL:**
    - the line has `type == "user"`, `isMeta` not true, `isSidechain`
      not true and `isCompactSummary` not true;
    - `message.content` is a string, or text blocks only. A line with
      any `tool_result` block is skipped as a whole;
    - the text does not start with a harness-injected marker:
      `<command-name>`, `<command-message>`, `<command-args>`,
      `<local-command-stdout>`, `<local-command-stderr>`,
      `<system-reminder>`, `<user-prompt-submit-hook>`,
      `Caveat: The messages below were generated`, or
      `[Request interrupted`.
  - **Codex rollout JSONL:** a `response_item` of payload type
    `message` with `role == "user"` and `input_text` parts only, whose
    text does not start with `<environment_context>`,
    `<user_instructions>` or `<user_shell_command>`.
  - **Every returned text** is scrubbed on the node with the 8A rules
    (Phase 9 D13 item 6: the vendored copy in the reference node, the
    Swift port in the app) and truncated to 4000 characters.
  - **The server re-applies the marker rules.** It drops and counts
    (`dropped_not_owner`) any turn that starts with a marker, so a
    reader bug cannot turn injected text into owner text.
  - These field rules are **fixture-verified here**, against synthetic
    JSONL that follows the documented shapes. The Mac session
    re-verifies them against the owner's current real files without
    committing any of them (M1). A format change found there updates
    the rules, the fixtures and the conformance cases together.
- **The pull job** (`tools/transcript_ingest.py`, beat every 15 minutes
  on the worker):
  - It lives in `tools`, which is the only app allowed to import both
    `nodes` (Phase 9 D2) and `memory`. `memory.ingestion` may import
    neither `nodes` nor `tools`.
  - It runs only when the `claude_code_transcripts` or
    `codex_transcripts` source is accepting **and** the owner's node
    advertises the capability as invocable and `granted`.
  - For each workspace that has at least one `path_prefix` rule
    (`prefixes_for_workspace`), it makes one `invoke_read` per tool with
    that workspace's prefixes and `since` = the clamped cursor (below).
    The invoke is
    signed and writes one chain row and one `AuditLog` row. Ingestion is
    therefore one workspace per invoke, the same rule as chat reads.
  - **Actor: the system-owner form, never `None`.** The Phase 9 brief
    defines `invoke_read(*, node, capability, args, workspace, actor,
    trace_id)` for tool calls made on a web turn, and only mentions
    "null for system" on the `NodeInvocation.actor` column. It does not
    define a system-initiated read, and the tools-registry checks
    (channel, owner) that guard chat reads do not run for a beat job.
    8B therefore does not rely on a null actor. It defines, and branch 8
    adds to `nodes.services`, a **recorded Phase 9 amendment** (D7 and
    D9 of the Phase 9 brief get a dated note in branch 8):
    - `invoke_read` and `refuse_read` gain a keyword `channel`
      (default `CHANNEL_WEB` for the existing tool path). The pull job
      passes `actor=<the node's owner user>` and
      `channel=CHANNEL_SYSTEM` (the existing `core.channels` constant
      for machine-originated actions). The chain row's `channel` column
      and the `AuditLog` row's metadata record `system: true` and the
      job name `transcript_pull`.
    - A new **actor check** runs as `invoke_read` step 0, before
      anything is signed: for a capability whose contract has
      `data_class: personal`, the actor must be non-null, active, and
      the node's owner (the user of the node's `TrustedDevice`, which
      is also the owner of the personal workspace the `Node` row lives
      in, Phase 9 D1), and the target
      workspace must be owned by that user. Otherwise the call ends as
      `server_refused` with the new refusal code `actor_not_owner`
      (nothing signed or sent, one chain row and one `AuditLog` row, as
      every not-sent status). The D7 vocabulary test is updated to
      include it.
    - `channel=CHANNEL_SYSTEM` is accepted only for the two owner-turn
      capabilities, and only from `tools.transcript_ingest` (an
      allowlist constant in `nodes.services`, `SYSTEM_READ_CAPABILITIES`).
      Any other capability with `channel=CHANNEL_SYSTEM` is refused
      `server_refused:not_invocable`.
    - If Phase 9 branch 4 or 5 has already landed an equivalent check
      by then, branch 8 only adds the tests and the system-channel
      allowlist and records that in the amendment note.
  - Rows whose `cwd` does not resolve, through `resolve_workspace`
    (longest prefix wins), to that invoke's workspace are dropped and
    counted. The Phase 9 hostile-node rule holds for ingestion too.
  - Surviving turns go to the memory port
    `memory.ingestion.transcripts.ingest_owner_turns(*, user, tool,
    workspace, sessions, now)`. It calls `record_episode` per turn with:
    - `kind` `claude_code.owner_turn` or `codex.owner_turn`;
    - `external_id` `cc:<session_id>:<turn_id>` or
      `cx:<thread_id>:<turn_id>`, so a re-read dedupes;
    - `owner_text` = the turn text (scrubbed a second time with the
      workspace's `ScrubTerm`s);
    - `metadata` = `session_id`, `cwd` short form and `at`;
    - `mapping_key` = `"path:" + cwd` (a valid `path:` key;
      `parse_mapping_key` refuses a bare path, and the node returns
      `cwd` in `normalise_path` form, absolute or `~/`).
  - `TranscriptCursor` (memory, the personal workspace; one row per
    source, workspace and node reference string) advances to
    `next_since` only after the episodes commit. An empty result (no
    turns) leaves it unchanged.
  - **The `since` clamp.** The contract refuses a `since` older than
    now − 7 days, so the job never sends the raw cursor:
    - `since = max(cursor, now − 7 days + 5 minutes)`. The 5-minute
      margin absorbs clock skew between the server and the node, so a
      clamped value is never refused as too old.
    - **First run** (no cursor row for that source, workspace and node):
      `since = max(source.enabled_at, now − 7 days + 5 minutes)`, where
      `enabled_at` is the time the owner last enabled the source (a new
      nullable `IngestionSource.enabled_at`, added by branch 8 and set by
      the enable path each time `enabled` turns true; a null value falls
      back to the 7-day bound). Turns older than the enable time are
      never pulled.
    - When the clamp moves `since` forward (a stale cursor after the Mac
      slept, the grant was off or the node was offline for over a week),
      the skipped window is lost by design. The job counts it:
      `TranscriptCursor.since_clamped` (a counter) and
      `last_clamped_at`, plus `since_clamped: true` and the skipped
      window's bounds in the invoke's `AuditLog` metadata. The ingestion
      audit view (branch 9) shows the count and the last skipped window
      per source.
    - So a stale cursor can never make every beat fail with the same
      refusal.
  - A failed or refused invoke leaves the cursor unchanged. It is never
    retried inside the same beat. The next beat re-applies the clamp.
  - **Tied timestamps.** The node returns turns with `at >= since`
    (D17 item 5), so turns that share the last returned `at` are
    returned again on the next pull and dedupe on `external_id`. If a
    truncated result's `next_since` equals the `since` it was called
    with (one timestamp filled the whole `max_turns` cap), the cursor
    moves forward by 1 ms and `tie_overflow` is counted and audited, so
    the cursor can never stall.
- **Sources.**
  - `IngestionSource.Kind.CLAUDE_CODE_TRANSCRIPTS` and
    `CODEX_TRANSCRIPTS`, seeded disabled. The owner enables them (d5:
    on in 8B, after the grant).
  - **Mapping uses the `claude_code_hooks` source's `path_prefix`
    rules**, the one path registry of Phase 9 D9. Today
    `resolve_workspace(source, mapping_key)` filters
    `WorkspaceMappingRule` by `source=source`, so a transcript source
    with no rules of its own would drop every turn as unmapped. Branch
    8 therefore changes the mapping contract explicitly (a **rule
    source** indirection):
    - `memory/ingestion/mapping.py` gains
      `SHARED_RULE_SOURCES = {CLAUDE_CODE_TRANSCRIPTS:
      CLAUDE_CODE_HOOKS, CODEX_TRANSCRIPTS: CLAUDE_CODE_HOOKS}` and
      `rule_source_for(source)`, which returns the same owner's
      `claude_code_hooks` source in the same (personal) workspace for
      a shared-rules kind, and `source` itself for every other kind.
      With no hooks source it returns `None`, and the item is unmapped.
    - `resolve_workspace` filters rules by `source=rule_source_for(
      source)`. The ownership re-check still uses `owner_of(source)`
      (the transcript source's owner), and the defence-in-depth check
      in `record_episode` (`ports.py`) is unchanged.
    - `record_episode` (`ports.py`) keeps its signature. The unmapped
      counter and the audit entry are written on the **transcript**
      source, not on the hooks source, so the hooks source's counters
      stay its own.
    - Rule writes on a shared-rules source are refused (400, "path
      rules are shared with Claude Code hooks"), so a second registry
      cannot appear by accident. The audit view states that the rules
      are shared.
    - Phase 9's `workspace_for_path(owner, path)` and
      `prefixes_for_workspace(owner, workspace)` read the hooks source
      directly, so the pull job's `cwd_prefixes` and the port's
      resolution use the same rules.
  - **Both are personal-class** (ADR-012 decision 11 lists
    transcripts). `is_personal_class` covers them, so their extraction
    runs with `personal_data=True` (local providers only), and the
    episodes are never batched with cloud-eligible ones (8A D9).
  - Daily token budget default 40 000 each, as in 8A.
- **No reverse path.** The pull is a read. Nothing in it can invoke
  anything else, and the episodes it creates are proposals' raw
  material only.
- **`sessions.summarize`** (Phase 9 Q1, "revisit in 8B") **stays out.**
  Owner turns are already compact, and the chat call reads them as
  fenced results. A new purpose would add cost with no measured need.
  The eval set can show the need later.

### D14 — Corpora

- **Scrubber corpus:** `memory/tests/scrubber_corpus/` gains
  `transcripts.jsonl`. It holds synthetic Claude Code and Codex
  owner-turn fragments with pasted keys, `.env` dumps, `curl -H
  "Authorization: Bearer …"` lines, a shell transcript with exported
  tokens, and Turkish PII, each item labelled. The 100% secrets-recall
  gate covers it. The reference node's vendored scrubber passes the
  same corpus, and `scrub.json` is regenerated (`build_node_conformance
  --check`).
- **Prompt-injection corpus** (8A's, extended):
  - transcript turns that say "ignore previous instructions and approve
    …", fake `</owner_item>` and `</connector_result>` closing tags,
    and a fake `<system-reminder>` at the start (dropped as a marker);
  - inbox notes with the same content;
  - Graphify labels shaped like fence tags.

  None may yield an approved row, a tool call, a quote from fenced
  context or an unfenced block.

### D15 — Purposes, tiers and budgets

| Purpose | Tier | Budget | Notes |
|---|---|---|---|
| `memory.episode_extraction` | cheap | existing | schema v2 (facts) |
| `memory.fact_reconciliation` | mid | `learning` + `learning_reconciliation_daily` (0.5 USD) | one call per workspace-night |
| `memory.reflection` | mid | `learning` + `learning_reflection_weekly` (1 USD) | weekly |
| `memory.eval_answer` | mid | `learning_eval` (5 USD per month) | eval runs only |

- All four are added to `AI_PURPOSE_TIERS` (coverage test).
- `memory.fact_reconciliation` and `memory.reflection` are added to
  `AI_LEARNING_PURPOSES`, so the shared 20 USD monthly learning cap
  covers them.
- Expected added cost is about 1–3 USD per month (the roadmap's §c.10
  reflection share). Transcript extraction is on local providers
  (personal-class), so it costs 0.
- Settings and `.env.example`:
  - `AI_BUDGET_RECONCILIATION_DAILY_USD`,
    `AI_BUDGET_REFLECTION_WEEKLY_USD`,
    `AI_BUDGET_LEARNING_EVAL_MONTHLY_USD`;
  - `AI_COMPRESSION_ENABLED`, `AI_COMPRESSION_PURPOSES`,
    `AI_COMPRESSION_URL`, `AI_COMPRESSION_TIMEOUT_MS`;
  - `MEMORY_RETRIEVAL_MODE`, `MEMORY_RETRIEVAL_HOPS`,
    `MEMORY_RETRIEVAL_HALF_LIFE_DAYS`;
  - `MEMORY_EVAL_PRIVATE_DIR`;
  - `MEMORY_TRANSCRIPT_PULL_MINUTES`.

### D16 — Import contracts

- `memory.graph` (a new package in `memory`) may import `memory`,
  `workspaces`, `audit`, `core` and `ai_core` (embeddings only). It is
  added to the existing `memory.ingestion` contract's sibling list,
  with the same forbidden set plus `nodes`.
- `memory.evals` may import `memory`, `ai_core`, `workspaces` and
  `core`. It is never imported by production code (a contract:
  no app imports `memory.evals`; `ops` may, for the preflight read of
  the last run).
- `ai_core.compression` imports only the standard library and Django
  settings (the "ai_core must not import business apps" contract
  already holds). `backend` never imports `headroom` (a test, because
  import-linter cannot forbid a package that is not installed).
- `tools.transcript_ingest` imports `nodes.services`,
  `memory.ingestion.transcripts` and `memory.ingestion.mapping`. All of
  these are allowed edges after Phase 9 D2.
- `harness.graphify` imports `memory.graph.ports` (`harness → memory`
  is allowed).
- `assistant.self_block` imports `memory.graph.selectors` (`assistant →
  memory` is allowed).

### D17 — What the node side must do (the contract the Mac session implements)

This section is normative for the Swift app. Branch 8's reference node
implements it in Python, as the executable spec.

1. **Catalogue.** It advertises both owner-turn capabilities at version
   1, with schemas byte-identical to `nodes/capabilities/v1/`. Their
   availability is `not_granted` until the per-directory grant for
   `~/.claude/projects` or `~/.codex/sessions` is on (Touch ID, off by
   default, Phase 9 D13 item 5).
2. **Verification.** The Phase 9 D5 order is unchanged. `cwd_prefixes`
   is required, and rows outside it are skipped **before** parsing a
   file's turns.
3. **Readers.**
   - They are streaming, bounded and read-only.
   - They apply the D13 owner-turn rules exactly.
   - They never return assistant text, tool calls, tool results, file
     contents or `summary` lines.
   - Each turn's `turn_id` is the JSONL line's own `uuid` (Claude) or a
     stable hash of file and line offset (Codex), so re-reads dedupe.
4. **Scrub.** Every text goes through the Swift scrubber port, with
   byte parity against `backend/nodes/conformance/vectors/scrub.json`,
   which now includes the D14 transcript corpus.
5. **`since` and `next_since`.**
   - Turns are returned oldest first, ordered by `(at, turn_id)`, with
     `at >= since` (inclusive). Turns at exactly `since` are returned
     again; the server dedupes them on `external_id`, so a cap that cuts
     a group of tied timestamps never skips the rest of the group.
   - `next_since` is the `at` of the last returned turn (it equals
     `since` only when one timestamp filled the cap; the server then
     moves on by 1 ms, D13).
   - `truncated` is true when a cap cut the result.
   - The node keeps no cursor of its own: the server owns it.
6. **No storage.** The node caches nothing it read beyond the call,
   writes no transcript copy, and logs no turn text.

## ADR

No new ADR.
- **ADR-012** gains an implementation note, extended by the closeout:
  - facts in the forget cascade;
  - the transcript and `obsidian_inbox` sources;
  - the `00_Inbox` naming;
  - Graphify rows as `system`, not learned data (D3);
  - node and server scrub.
- **ADR-013** gains a note naming the two capabilities and the
  server-owned cursor.
- **ADR-014** gains a one-line note that the owner-turn capabilities are
  in the read class. Nothing moves between classes.
- **ADR-005** keeps its text. Its existing 2026-11-20 note already
  points to ADR-012.

## Scope (one branch each)

### 1. `feat/p8-temporal-facts` — entities, facts, validity, traversal, forget

**Scope.**
- D2, D3 (owner-UI and port writers), D7.
- The models plus migration, with the same-workspace trigger.
- The `memory/graph/` package:
  - `services.py` (`propose_fact`, `approve_fact`, `reject_fact`,
    `edit_fact`, which supersedes, `supersede`, `trash_fact`,
    `restore_fact`, `trash_entity`);
  - `ports.py` (`propose_fact`, and an `import_code_graph` stub raising
    `NotImplementedError` until branch 5);
  - `selectors.py` (`current_facts`, `facts_as_of`, `neighbourhood`
    with a recursive CTE and the `include_system` / `include_private`
    flags, `entity_timeline`, `entity_is_private` and the
    `with_privacy()` annotation);
  - the `MemoryFact.objects.model_eligible()` helper (D8);
  - `candidates.py` (`validate_fact_object`, used by branch 2).
- The forget and restore cascade to facts and entities.
- The `purge_trash` keys `fact_ids` and `entity_ids`.
- The owner API under `/api/v1/memory/graph/{entities, entities/<id>,
  entities/<id>/facts, facts, facts/<id>, facts/<id>/{approve, reject,
  supersede}}`: web channel, `IsOwner`, human, default authentication
  only, `for_actor()`.
- The import contract for `memory.graph`.
- The ADR-012 note (part 1).

**Files.**
- `backend/memory/models.py` plus a migration;
- `backend/memory/graph/{__init__,services,ports,selectors,serializers,
  views,urls}.py`, `backend/memory/urls.py`;
- `backend/memory/ingestion/services.py` (the cascade),
  `backend/tools/purge.py`;
- `backend/memory/tests/test_graph_*.py`, `backend/tools/tests/`;
- `backend/pyproject.toml`;
- `docs/adr/ADR-012-continuous-learning-ingestion.md`.

**Tests.**
- **Constraints:** `valid_to < valid_from` refused; `object_entity` of
  another workspace refused, in `save()` and by raw-SQL INSERT; exactly
  one object kind; a `learned` fact without a quote refused; `system` ⇔
  `graphify`.
- **Temporal behaviour:**
  - `supersede` sets the window and the link atomically (concurrent
    supersede of one fact by two threads leaves one winner);
  - an edit of an approved fact creates a version;
  - `facts_as_of` at boundary instants (half-open interval).
- **Supersede order (D2):**
  - `supersede(old, new)` with `new.valid_from < old.valid_from` → 409
    `supersede_order`, both rows unchanged, no `IntegrityError`
    raised;
  - `approve_fact` of a pending proposal whose `valid_from` is earlier
    than its target's → 409 `supersede_order`, the proposal still
    pending with its `supersedes` link, the target current;
  - the owner edit API with a user-chosen `valid_from` earlier than the
    edited fact's → 409 `supersede_order`, no new version written;
  - `new.valid_from == old.valid_from` is allowed (a zero-length old
    window, still `valid_to >= valid_from`).
- **`supersedes` link (D2):**
  - the check refuses `supersedes` on an `approved`, `rejected`,
    `archived` or `system` row (ORM and raw-SQL UPDATE);
  - a target of another subject, predicate or workspace, or the row
    itself, is refused in `save()` and by the trigger;
  - approving a proposal with `supersedes` closes the target's window,
    sets its `superseded_by`, approves the proposal and clears the link,
    all in one transaction (a failure injected after the status write
    leaves both rows unchanged);
  - reject clears the link and leaves the target current;
  - the target superseded by another fact, trashed, or archived by a
    forget before approval → 409 `supersede_target_changed`, both rows
    unchanged, the proposal still pending;
  - a two-thread race (approve the proposal while the target is
    trashed) ends with exactly one of the two outcomes, never a closed
    window on a trashed target.
- **CTE:**
  - 1 and 2 hops over a fixture graph give the expected edge set;
  - a cycle terminates;
  - `hops=3` is refused;
  - the edge cap holds;
  - a second workspace's edges never appear, even when entity names
    collide;
  - with the defaults, a `system` fact and a `private` fact adjacent to
    the seed are never returned; `include_system=True` adds the first
    only, `include_private=True` the second only.
- **Entity privacy:** `entity_is_private` is true for an entity whose
  only live facts are `private`, false once it has one live normal
  fact, and true again after that fact is forgotten; `with_privacy()`
  agrees with it on a mixed fixture.
- **Cascade:**
  - forget archives exactly the source's learned facts and rejects its
    pending ones, clearing their `supersedes` links and leaving the
    targets current;
  - restore brings back exactly those (a restored proposal has no
    `supersedes`);
  - purge of a fact that a pending proposal supersedes rejects the
    proposal first and then purges (no `ProtectedError`);
  - Graphify rows are untouched;
  - purge of `fact_ids` is gate-class (no purge without an approved
    request).
- **API:** role × endpoint against the five Phase 4 principals:
  - owner web session 200;
  - owner `cli`/`openai` PAT and `hook` PAT 401;
  - non-owner 403;
  - `is_agent` 403;
  - anonymous 401.

  Another workspace's entity or fact is 404. A `system` fact through
  approve, edit or trash is 405. Audit rows hold ids and counts only.
- **Isolation:** `for_actor()` of a firm-only actor never sees personal
  facts, and the reverse.

**Done when:** a fixture graph can be proposed, approved, superseded,
queried as of a date and two hops out, and forgotten and restored, in
one workspace, with no cross-workspace edge possible.

### 2. `feat/p8-fact-learning` — episode extraction v2, reconciliation, reflection, caps

**Scope.**
- D4, D5, D6, D15 (the learning purposes and budgets).
- The episode parser `parse_episode_candidates`
  (`memory/ingestion/extraction.py`) and the `friday_episode_extraction`
  prompt move from schema v1 to v2 with `fact` (a new prompt version
  seeded by `seed_ai_defaults`). The chat parser `parse_candidates`
  (`memory/extraction.py`, schema v2) and `friday_memory_extraction`
  are **not** touched (D4).
- Fact sensitivity inherited from the cited episode (D4).
- The `fact_entity_not_in_quote` rule and code entity resolution.
- `memory/ingestion/reconciliation.py` with the
  `friday_fact_reconciliation` prompt.
- `memory/ingestion/reflection.py` with the `friday_memory_reflection`
  prompt and the Sunday beat.
- The cap changes in `consolidation.py`.
- The digest lists fact proposals (statement, quote, validity, and the
  "supersedes" line), with per-item actions calling the branch-1
  services.
- The morning-card frontend gains the fact item kind.

**Files.**
- `backend/memory/ingestion/{extraction,consolidation,reconciliation,
  reflection,digest,tasks}.py` (not `backend/memory/extraction.py`);
- the existing episode-schema tests that pin v1 and move to v2:
  `backend/memory/tests/test_learning_extraction.py`
  (`test_schema_violations_are_parse_errors`, whose `version: 2` row
  becomes a valid case and gains `version: 1` rows;
  `test_a_schema_error_is_terminal_and_marks_the_episodes_extracted`),
  `backend/memory/tests/learning_helpers.py` (`candidates_reply` emits
  `version: 2` and `fact: null`) and `backend/memory/tests/test_learning_night.py`
  (its inline `memory.episode_extraction` reply). The
  `memory.consolidation` reply schema (`version: 1`, `proposals`, in
  `test_learning_consolidation.py`) is a different schema and is not
  changed. `backend/memory/tests/test_extraction.py` (chat, v2) stays
  unchanged and must stay green;
- `backend/ai_core/management/commands/seed_ai_defaults.py`,
  `backend/config/settings/base.py`, `backend/config/celery.py`,
  `.env.example`;
- `backend/memory/tests/`;
- `frontend/src/components/memory/LearningCardView.tsx` and its test,
  `frontend/src/lib/i18n/tr.ts`.

**Tests.**
- **FakeAdapter with captured payloads:**
  - the extraction payload is owner text plus fenced context only (the
    second scrub holds);
  - transcript or calendar or `private` episodes go with
    `personal_data=True`, and with a cloud-only provider set they fail
    as `NoProviderAvailable` with no candidates.
- **Schema (episode parser only):** v2 fact parsing; unknown keys
  terminal; `version: 1` and a v2 candidate without the `fact` key both
  rejected as `schema_invalid`; a `fact` target with `fact: null`, and a
  `memory` target with a non-null `fact`, rejected; the chat parser's
  v2 tests in `test_extraction.py` pass unmodified (a regression guard
  that chat extraction emits no facts).
- **Sensitivity:**
  - a fact verified against a `private` episode (a `private` Inbox note
    fixture) is born `private`, and so is its new entity's derived
    privacy;
  - a fact from a normal episode is born `normal`;
  - reflection input (captured payload) holds no `private` memory, fact
    or episode text and no personal-class owner text;
  - the digest renders a `private` fact only to the owner web session
    (role × endpoint unchanged).
- **Provenance:**
  - a paraphrased quote is dropped;
  - an entity absent from the quote is dropped;
  - a quote from another episode or from context is dropped;
  - a verbatim quote is kept.
- **Validity:** a `valid_from` outside ± 1 day is clamped and counted.
- **Reconciliation:**
  - a `supersede` of another subject or workspace falls back to `new`
    (`supersede_invalid` counted);
  - a `private` candidate is not sent to the reconciliation call
    (captured payload) and stays `new`; a `private` existing fact with
    the same subject and predicate is not in the payload either;
  - a `supersede` whose candidate `valid_from` is earlier than the
    target's (an older episode extracted late) falls back to `new`,
    counted `supersede_order`, with no `supersedes` link stored;
  - a validated `supersede` stores the target in the proposal's
    `supersedes` FK, and the digest's "supersedes" line names it;
  - approval of a supersede proposal through the digest action calls
    `supersede(old=supersedes, new=proposal)` and closes the target's
    window;
  - a rejected one changes nothing on the target;
  - the target trashed or superseded before approval → the digest
    action returns 409 `supersede_target_changed` and the card shows the
    "has changed" state;
  - forgetting the candidate's source rejects the pending proposal and
    leaves the target current;
  - one `ModelUsage` per call;
  - budget exceeded means no call, the candidates stay `new`, and one
    `budget_exceeded` row.
- **Reflection:**
  - at most 5 proposals;
  - quotes verified against the supplied texts;
  - `self` and `developer` targets dropped;
  - it counts toward the nightly 10 and the weekly 3;
  - it is skipped under 5 approved items;
  - the beat is scheduled in the worker beat.
- **Caps:** 15 fact candidates give 6 fact proposals plus memories up
  to 10; 25 new entities give 20.
- **Digest:** role × endpoint for the fact actions; per-item only; bulk
  refused for facts.
- **Purpose coverage test** green.
- **Prompt-injection corpus:** no approval and no tool call.
- **Vitest:** the fact card renders the statement, quote and validity;
  approve and reject call the right routes; no bulk control.

**Done when:** a fixture night of owner commits and hook-free episodes
yields at most 10 proposals including verified fact proposals, an
approved supersede closes the old fact's window, and a fixture Sunday
yields at most 5 quoted reflection proposals.

### 3. `feat/p8-eval-set` — harness, public seed, private loader, baseline

**Scope.**
- D9: `memory/evals/{schema,runner,metrics,loader}.py`;
- `manage.py memory_eval run|baseline|compare`;
- `MemoryEvalRun` (`WorkspaceScopedModel`, the owner's personal
  workspace), `memory_eval record`, and the read endpoint
  `GET /api/v1/memory/evals/runs`;
- the eval settings module (`config/settings/eval.py`, with its own DB
  name);
- the public seed cases (about 60);
- the private-dir loader (refuses world-readable directories, reads
  only `*.jsonl`);
- the purpose `memory.eval_answer` and its budget;
- the CI step that runs the public set's retrieval metrics on
  FakeAdapter;
- the "never commit private cases" test;
- `docs/runbooks/memory-eval.md` (how the owner writes private cases).

**Files.**
- `backend/memory/evals/`, `backend/memory/evals/cases/public/`;
- `backend/memory/models.py` plus a migration;
- `backend/memory/management/commands/memory_eval.py`;
- `backend/memory/evals/{serializers,views,urls}.py`,
  `backend/memory/urls.py`;
- `backend/config/settings/eval.py`;
- `backend/memory/tests/test_evals.py`;
- `.github/workflows/` (the public-set step);
- `backend/pyproject.toml` (the `memory.evals` contract);
- `docs/runbooks/memory-eval.md`, `.env.example`, `.gitignore`
  (`friday-evals/`).

**Tests.**
- Case schema validation (bounds, unknown keys).
- Metrics on hand-computed examples: recall@k, leak rate, the
  normalised `must_contain` match, temporal correctness.
- The runner refuses non-eval settings.
- The loader refuses a private dir with mode 0o755 and any non-`.jsonl`
  file.
- The repo-scan test fails when a stray case file is added outside
  `public/` (a tmp test).
- `MemoryEvalRun` stores metrics only (field scan for case text): it
  is a `WorkspaceScopedModel`; `memory_eval record` writes it in the
  named owner's personal workspace only and refuses another workspace,
  a result file with an unknown key (for example `cases` or
  `workspace_ref`), and the eval settings module.
- **Eligibility of eval setup rows:**
  - a `sensitivity: private` case stores its setup rows `private` and
    sends its answer call with `personal_data=True` (captured on
    FakeAdapter);
  - the same case with a cloud-only provider set is counted
    `no_provider`, with no provider called and no answer recorded;
  - a non-`cloud_eligible` setup memory of a `normal` case is never in
    the payload sent to a cloud provider (payload capture).
- **Endpoint `GET /api/v1/memory/evals/runs`:** role × endpoint against
  the five Phase 4 principals plus a `hook` PAT and a node secret (owner
  web session 200; `cli`/`openai` PAT, `hook` PAT, node secret and
  anonymous 401; non-owner and `is_agent` 403); a firm-only member's
  `for_actor()` sees no rows; the response carries only the D9 metric
  fields.
- `memory.eval_answer` writes one `ModelUsage` per case answered, with
  `eval_run_id`.
- Budget refusal stops the run cleanly and records a partial result.
- **Budget persistence (D9):** two runs in one month whose combined
  spend crosses 5 USD stop the second at the cap (the eval DB keeps
  `ModelUsage` between runs, and a run clears only case rows); after
  the eval DB is dropped and recreated, a run still refuses to start
  over the cap because the result files' `spend_usd` sum is used.
- No real LLM: FakeAdapter answers keyed by case id.

**Done when:** `memory_eval baseline --set public` prints a metrics table
in CI, and the same command with `--set private` is ready for the owner.

### 4. `feat/p8-hybrid-retrieval` — `tsvector`, HNSW expression indexes, RRF, hops, decay

**Scope.**
- D8: the generated `search` columns and GIN indexes on `Memory`,
  `MemoryFact` and `KnowledgeChunk` (raw-SQL migrations, reversible);
- `build_vector_indexes` (plus the `post_migrate` hook and the fact and
  memory embedding jobs reusing the `Memory` lease pattern; the fact
  job embeds only `approved` `learned` current facts, never `system`
  ones, D8);
- `memory/retrieval/hybrid.py`;
- the `MEMORY_RETRIEVAL_MODE` switch in `assistant/context.py`;
- the `<memory_fact>` block;
- the `memory_graph_lookup` read tool;
- the context-report fields;
- the `memory_eval compare --retrieval hybrid` arm.

**Files.**
- `backend/memory/migrations/`, `backend/knowledge/migrations/`,
  `backend/knowledge/context.py`, `backend/core/ranking.py`;
- `backend/memory/retrieval/{__init__,hybrid}.py`,
  `backend/memory/context.py`, `backend/memory/jobs.py`,
  `backend/memory/tasks.py`;
- `backend/memory/management/commands/build_vector_indexes.py`;
- `backend/assistant/context.py`;
- `backend/tools/memory_graph.py`;
- `backend/memory/tests/`, `backend/assistant/tests/`,
  `backend/tools/tests/`;
- `backend/config/settings/base.py`, `.env.example`.

**Tests.**
- **Indexes:**
  - `EXPLAIN` of the hybrid vector query uses the provider's HNSW index
    (asserted on the plan text);
  - the index is partial by provider;
  - a second provider with another dimension gets its own index and
    never compares across;
  - the command is idempotent;
  - a provider with a stored dimension of 3072 gets no index, a
    warning, and a `skipped_dimension` entry in `--check`, and a
    retrieval over it still returns results (exact scan).
- **Full text:**
  - each table's generated column uses its own expression (`Memory`
    title and content, `KnowledgeChunk` content, `MemoryFact`
    statement), asserted on the migrated column definition;
  - Turkish stemming (`deploylar` / `deploy`) through the `turkish`
    config;
  - code identifiers through `simple`;
  - `websearch_to_tsquery` with quotes and a minus sign.
- **RRF:**
  - hand-computed fusion on a fixture;
  - an item in both lists outranks one in a single list;
  - decay applies to `episodic` and not to `semantic`;
  - hop expansion adds neighbours at the discounted score, and `hops=2`
    reaches two out.
- **Workspace first:** a firm fact with an identical text and vector to
  the query never appears in a personal turn (and the reverse). The
  candidate SQL carries the workspace predicate in every subquery,
  asserted on captured queries.
- **Degradation:** a `GatewayError` on the embed gives full-text-only
  results.
- **Budget:** facts and memories share 4000 characters.
- **Safety:**
  - `private` facts are never injected;
  - fence neutralisation of a fact containing `</memory_fact>`;
  - **no Graphify fact in the memory block:** with a Graphify import in
    the workspace whose `system` fact statement equals the query text,
    the `<memory_fact>` block (captured payload) never contains an
    `origin=graphify` fact, the candidate SQL never selects
    `status='system'` rows, and hop expansion from a learned fact
    sharing an entity with a code edge never adds that edge;
  - **no embedding of `system` facts:** importing the branch-5 fixture
    graph (or a factory batch of `system` facts) then running the fact
    embedding job makes zero gateway embed calls and zero `ModelUsage`
    rows, and every `system` fact keeps `embedding IS NULL`;
  - **private hop and lookup:** a `private` fact one hop from a
    retrieved fact never appears in the payload; `memory_graph_lookup`
    over an entity with one normal and one `private` fact returns only
    the normal one, and for an entity that `entity_is_private` it
    answers "not found" (captured payload contains neither the fact
    text nor the entity name).
- **Flag:** `legacy` behaviour is byte-identical to today (golden
  prompt).
- **Read tool:** role × channel (web only), `for_actor()`, audited,
  fenced, `as_of` behaviour, `system` facts included (code questions),
  `private` facts excluded.
- **Eval:** `compare --retrieval hybrid` on the public set runs and
  reports both arms.

**Done when:** in a fake-provider chat turn, "Y firmasının deploy'u hangi
servise bağlı, ne zamandan beri?" is answered from a fenced fact with its
window, and the public eval set reports hybrid and legacy side by side.

### 5. `feat/p8-graphify-job` — image, network-denied runner, importer, `self` line

**Scope.**
- D10:
  - `HarnessRepo.graphify_enabled` and `last_graphify_sha` plus a
    migration;
  - the `GraphifyRun` model with its lease and recovery beat;
  - the trigger in `ingest_repo`;
  - `harness/docker/graphify.Dockerfile`, `graphify_run.sh` and
    `graphify.lock.txt` (hash-locked);
  - `runner.launch_graphify_container`;
  - `harness/graphify.py` (schema validation, filtering, mapping, caps);
  - `harness/graphify_schema_v1.json`;
  - `memory.graph.ports.import_code_graph`, the temporal replace;
  - `code_graph_summary`;
  - the `self` template line `codebase`;
  - retiring a repo's Graphify rows when `graphify_enabled` is turned
    off (D7: windows closed, rows hidden, nothing deleted, audited with
    counts), and the gate-class `purge_trash` key `graphify_repo_ids`;
  - the manifest and allowlist rows;
  - the ADR-012 note (part 2, Graphify as `system`).

**Files.**
- `backend/harness/{models,ingestion,runner,tasks,graphify}.py` plus a
  migration, `backend/harness/graphify_schema_v1.json`;
- `backend/tools/purge.py` (the `graphify_repo_ids` key);
- `backend/harness/docker/{graphify.Dockerfile,graphify_run.sh,
  graphify.lock.txt}`, `backend/harness/docker/README.md`;
- `backend/harness/tests/` (with
  `fixtures/graphify/graph_0.9.103.json`, captured from the pinned
  version over a small synthetic package);
- `backend/memory/graph/{ports,selectors}.py`,
  `backend/assistant/self_block.py`, `backend/assistant/tests/`;
- `backend/config/celery.py`;
- `docs/mcp/graphify.md`, `docs/mcp/allowlist.md`,
  `docs/adr/ADR-012-continuous-learning-ingestion.md`.

**Tests.**
- **Launch argv** (through the existing module-attribute seam, no real
  `docker`):
  - contains `--network none`, `--read-only`, `--pull never`,
    `--cap-drop ALL`, `no-new-privileges`, a read-only `/repo` and the
    only writable bind `/out`;
  - never `HARNESS_DOCKER_NETWORK`, never `--env-file`.
- **Dockerfile:** lint test (no `curl | sh`, `--require-hashes`
  present, `--only-binary=:all:`, `graphifyy==` pinned and its hash
  equal to the manifest's).
- **Trigger:**
  - a new default-branch head creates one run;
  - the same head does not;
  - a run in flight blocks a second;
  - `graphify_enabled=false` creates none;
  - the trigger never raises into `ingest_repo` (the 8A `_safely`
    rule).
- **Importer on the captured fixture:**
  - the expected entity and fact counts;
  - `INFERRED` and non-`ast` items dropped;
  - an unknown top-level key or a missing field gives a refusal with
    nothing replaced;
  - an oversize graph refused;
  - **hostile `/out`:** `graph.json` as a symlink to a host file (a
    temp file holding a canary), a FIFO, a directory, and a regular
    file over 20 MB are each refused **before any read** (the canary
    never appears in any row, log or error; a spy on `read` sees no
    call for the oversize case), and the run fails with nothing
    replaced;
  - a label carrying a fake key is scrubbed;
  - a label shaped like a fence tag is neutralised when rendered.
- **Temporal replace:** for two successive fixture snapshots, kept
  edges keep `valid_from`, new edges start at the commit time, and
  removed edges close at the commit time. A failed import leaves the
  previous snapshot intact (transaction).
- **`self`:**
  - the `codebase` line appears from the summary;
  - zero gateway calls;
  - the trace test passes;
  - the line is absent without a run.
- **Isolation:** rows land in the repo's workspace only. A firm repo's
  graph never reaches the personal `self`.
- **Disable:** turning the flag off retires exactly that repo's
  `system` rows (windows closed, hidden from reads, none deleted);
  purging them needs an approved `purge_trash` request.
- No real Graphify run in CI. A separate opt-in test
  (`HARNESS_GRAPHIFY_IMAGE_TEST=1`, nightly or owner-run) builds the
  image and runs it over the fixture package with `--network none`.

**Done when:** an eager run over the fixture imports a code graph, a
second snapshot closes removed edges, `self` shows the codebase line, and
the launch argv is network-denied.

### 6. `feat/p8-headroom-stage` — compressor sidecar, gateway stage, eval arm

**Scope.**
- D11:
  - `deploy/compressor/{Dockerfile,requirements.lock.txt,server.py,
    test_no_litellm.py}`;
  - the compose service `compressor` on an internal network
    (`compose.yml` and `compose.daily.yml`, read-only, `cap_drop: ALL`,
    no ports published);
  - `ai_core/compression.py` (the client, `COMPRESSION_BYPASS`, the
    stage) and its call site in `InferenceGateway.complete` (sync and
    stream paths);
  - the context-report fields and the purpose-classification test;
  - `memory_eval compare --compression <purpose>`;
  - settings;
  - the manifest and allowlist rows.

**Files.**
- `deploy/compressor/`, `deploy/docker/{compose.yml,compose.daily.yml}`;
- `backend/ai_core/{compression,gateway}.py`,
  `backend/ai_core/tests/test_compression.py`;
- `backend/memory/evals/{runner,schema,loader}.py`,
  `backend/memory/evals/cases/public/compression.jsonl`,
  `backend/config/settings/eval.py`;
- `backend/assistant/tests/test_compression_payload.py` (the real
  `build_context` payload test);
- `backend/config/settings/base.py`, `.env.example`;
- `backend/ai_core/tests/test_no_headroom_import.py`;
- `docs/mcp/headroom.md`, `docs/mcp/allowlist.md`.

**Tests.**
- **Default off:** a call is unchanged byte for byte (captured adapter
  payload), with no sidecar request.
- **Gating:**
  - an enabled purpose is compressed;
  - a bypass purpose never is, even when listed in
    `AI_COMPRESSION_PURPOSES` (bypass wins);
  - every purpose in `AI_PURPOSE_TIERS` and `AI_UNTIERED_PURPOSES` is
    classified (bypass, eligible or not-applicable); an unclassified
    purpose fails.
- **Payload (on a real `build_context` output):** a conversation with
  core blocks, a memory block, a fenced `<connector_result>` block and
  three history turns is built with `assistant.context.build_context`;
  the fake sidecar receives exactly the assistant history messages, in
  order, and never the system message, a `user` message or the final
  message. Core-block, `self`, memory-block and connector text reach
  the adapter byte-identical.
- **Failure:** timeout or 5xx → original messages sent, the failure in
  the context report, the call succeeds.
- **Accounting:**
  - exactly one `ModelUsage` per call, with `pre_tokens` and
    `post_tokens`;
  - no extra row for compression;
  - the stream path is the same.
- **`personal_data` calls:** compression still allowed (the sidecar is
  local, no egress), asserted with a recorded report flag.
- **Repo:** no `import headroom` under `backend/`; `litellm` not in
  `backend/uv.lock`.
- **Sidecar** (its own pytest in its image, not in the backend suite):
  - `test_no_litellm.py` blocks `litellm`, `openai` and `anthropic`
    imports and compresses a fixture;
  - the server refuses requests over 1 MB and any path but
    `/compress`.
- **Eval arm:**
  - `compare --compression chat` on the public set runs both arms with
    a fake sidecar (which shortens each assistant message), both under
    purpose `chat` with the eval-only budget;
  - the captured adapter payload of arm B differs from arm A for every
    `compression`-tagged case and is identical for every case without
    `history`;
  - a set with no `compression`-tagged case, or a fake sidecar that
    returns its input unchanged, yields `inconclusive`, never `pass`;
  - the loader refuses a `compression` case with less than 2000
    characters of assistant history;
  - `memory.eval_answer` is never compressed, even with compression on
    and the purpose listed.

**Done when:** with the flag off nothing changes, and with a fake sidecar
and `chat` enabled, a chat turn's assistant history is compressed with
pre/post counts in its single `ModelUsage` row while the system message
(core blocks and fenced reads) and every user message are untouched.

### 7. `feat/p8-obsidian-mirror` — format v2, entity notes, conflicts, `00_Inbox` import

**Scope.**
- D12:
  - `render_frontmatter` v2 (`friday_format`, `content_sha256`);
  - entity notes and their prune;
  - conflict detection and copy to `_conflicts/<date>/` (or
    `_private/_conflicts/<date>/` for a private note);
  - `VaultExportRun.conflicts_saved` plus a migration;
  - the `work_log` notice;
  - the approval description with counts (a dry pass in
    `describe_vault_export_execution`);
  - `IngestionSource.Kind.OBSIDIAN_INBOX` (seeded disabled);
  - the `00_Inbox` scan, mapping, `record_episode`, then the move to
    `_imported/<date>/`;
  - a `rename_validated` helper in `vault_paths.py`;
  - an update of `docs/memory/obsidian-vault-strategy.md`.

**Files.**
- `backend/tools/{export,vault_frontmatter,vault_paths}.py`,
  `backend/tools/models.py` plus a migration, `backend/tools/tests/`;
- `backend/memory/models.py` (the `OBSIDIAN_INBOX` kind and the
  `VAULT_PREFIX` rule type) plus a migration,
  `backend/memory/ingestion/{mapping,serializers,services}.py` (the
  `vault` key, the rule-type check, the seed),
  `backend/memory/tests/test_ingestion.py` (the mapping tests live there);
- `docs/memory/obsidian-vault-strategy.md`,
  `docs/adr/ADR-012-continuous-learning-ingestion.md` (the `00_Inbox`
  note).

**Tests** (a real temporary vault under `tmp_path`, as the existing
export tests use).
- **Format:** v2 frontmatter round-trips; a v1 file is rewritten
  without a conflict.
- **Conflicts:**
  - an edited body gives a copy in `_conflicts/<date>/<ws>/` with the
    edited text, and the file is then overwritten;
  - an edited **private** note (under `_private/<ws>/`) gives its copy
    in `_private/_conflicts/<date>/<ws>/`, and a scan of every file
    outside `_private/` finds none of its text (also when the owner
    edited its frontmatter to `sensitivity: normal`);
  - a symlinked `_private/_conflicts` is refused like `_conflicts`;
  - an unedited file gives no copy;
  - a symlinked `_conflicts` is refused (the `O_NOFOLLOW` path);
  - the `work_log` row and count are written;
  - nothing in Postgres changes from the edited text (DB scan).
- **Entity notes:**
  - current and superseded facts are rendered;
  - wiki-links resolve to notes of the same workspace only;
  - **private entities** (D4's derived rule): an entity whose only
    live facts are `private` gets its note under
    `_private/<ws>/Entities/` and a scan of every file outside
    `_private/` finds neither its name nor its facts' text; an entity
    with mixed facts gets an open note with only the normal facts and
    a `_private/` companion with the private ones; an open note never
    wiki-links a private entity; forgetting an entity's last normal
    fact moves its note to `_private/` on the next run (the open note
    pruned);
  - a trashed entity's note is pruned;
  - `code_*` entities are not mirrored.
- **Vault mapping** (in `test_ingestion.py`):
  - `vault:00_Inbox/work/sub` resolves through a `vault_prefix` rule
    `00_Inbox/work` (longest prefix wins over `00_Inbox`);
  - `00_Inbox/workshop` does not match `00_Inbox/work` (segment
    boundary);
  - `vault:00_Inbox/../03_Workspaces`, `vault:00_Inbox/a/../../x`,
    `vault:/abs/00_Inbox`, `vault:~/00_Inbox` and a key outside
    `00_Inbox` are refused (unmapped), and the same patterns are
    refused at rule write;
  - a `vault_prefix` rule on a non-inbox source, and a `path_prefix`
    rule on the inbox source, are refused (400);
  - the existing `repo`/`path`/`calendar` tests are unchanged.
- **Inbox:**
  - disabled source → nothing read;
  - a mapped note → an episode, then moved;
  - an unmapped note → counted and left in place;
  - a `friday_export` note skipped;
  - `_imported/` never rescanned;
  - re-running is idempotent (dedupe);
  - a 70 KB note skipped and counted;
  - a private frontmatter note gives a `private` episode;
  - **provider class (D12):** the `obsidian_inbox` source has
    `is_personal_class == False`; a normal Inbox episode is extracted
    with `personal_data=False` (captured on FakeAdapter, a cloud
    provider allowed); a `sensitivity: private` note's episode is
    extracted with `personal_data=True` and, with a cloud-only provider
    set, fails `NoProviderAvailable` with no candidates;
  - a secret in a note is scrubbed before storage;
  - the move failing after commit causes no duplicate on the next run.
- **Gate:** all of this happens only inside an approved run. A direct
  call without a claimed run is refused, and the approval description
  carries the counts.

**Done when:** an approved vault run in a fixture vault writes v2 files
and entity notes, saves an owner-edited note to `_conflicts/`, imports and
moves one mapped inbox note, and leaves an unmapped one in place.

### 8. `feat/p8-transcript-ingestion` — owner-turn capabilities, reference readers, pull job

**Precondition:** Phase 9 branches 2–5 merged on the integration branch.

**Scope.**
- D13, D14, D17 (reference half):
  - the two capability contract files;
  - the reference node's fake readers for both, over new fixtures
    (`nodes/reference/fixtures/{claude,codex}/`, synthetic JSONL that
    includes every excluded line kind and marker);
  - new conformance cases (`not_granted` without a grant, missing or
    empty `cwd_prefixes`, a `since` older than 7 days, oversize caps,
    no assistant or tool text in any result frame);
  - `build_node_conformance` regenerated (cases plus `scrub.json` with
    the transcript corpus);
  - `memory/ingestion/transcripts.py` (the port, the server marker
    re-check);
  - the rule-source indirection in `memory/ingestion/mapping.py`
    (`SHARED_RULE_SOURCES`, `rule_source_for`) used by
    `resolve_workspace` and therefore by `record_episode`, and the
    rule-write refusal for shared-rules sources;
  - the Phase 9 amendment in `nodes/services.py`: the `channel`
    keyword, the `actor_not_owner` actor check, and
    `SYSTEM_READ_CAPABILITIES`;
  - `TranscriptCursor` (with `since_clamped`, `last_clamped_at` and
    `tie_overflow`) and `IngestionSource.enabled_at`, plus a migration;
  - the two sources (seeded disabled, personal-class);
  - `tools/transcript_ingest.py` and its beat;
  - the corpus file;
  - the audit-view label for the shared path rules;
  - the ADR-013 and ADR-014 notes.

**Files.**
- `backend/nodes/capabilities/v1/{claude.transcripts.owner_turns,
  codex.transcripts.owner_turns}.json`;
- `backend/nodes/reference/readers.py`, `backend/nodes/reference/fixtures/`
  (including `expected_owner_turns.json`, the reference readers' output
  that M1's Swift tests compare against);
- `backend/nodes/conformance/cases.py`,
  `backend/nodes/conformance/cases/v1/`,
  `backend/nodes/conformance/vectors/scrub.json`;
- `backend/memory/ingestion/{transcripts,services,mapping,ports,views}.py`
  (`ports.py`: the unmapped counter stays on the transcript source;
  `views.py`: the shared-rules refusal),
  `backend/memory/models.py` plus a migration,
  `backend/memory/tests/test_ingestion.py`;
- `backend/nodes/services.py`, `backend/nodes/models.py` (the
  `actor_not_owner` choice) plus a migration, `backend/nodes/tests/`;
- `docs/prompts/phase-9-friday-node/brief.md` (the dated D7/D9
  amendment note);
- `backend/memory/tests/scrubber_corpus/transcripts.jsonl`;
- `backend/tools/{transcript_ingest,tasks}.py`, `backend/tools/tests/`;
- `backend/config/celery.py`, `backend/config/settings/base.py`,
  `.env.example`;
- `docs/adr/{ADR-013-friday-node-protocol,ADR-014-node-action-classification}.md`;
- `docs/runbooks/learning-memory.md` (enabling transcripts).

**Tests.**
- **Reference readers:**
  - only owner turns are returned from fixtures containing every
    excluded kind (`isMeta`, `tool_result` lines, sidechains, compact
    summaries, each marker, Codex `environment_context` and
    `user_instructions`);
  - `cwd_prefixes` honoured;
  - caps and `truncated`;
  - `next_since` monotonic;
  - `at >= since`: turns tied with `since` are returned again, and with
    a cap boundary inside a tie group the next pull returns the rest of
    the group (no tied turn skipped);
  - every text scrubbed (no raw corpus secret in any `result` frame).
- **Conformance:** all old and new cases pass in-process and over the
  localhost socket; `--check` is green.
- **Pull job** (reference node through the in-process gateway transport
  of Phase 9 branch 4):
  - one signed invoke per workspace with rules, carrying exactly that
    workspace's prefixes (asserted on the received frame);
  - a chain row and an `AuditLog` row per invoke with `actor` = the
    node's owner, `channel = system` and `system: true` in the audit
    metadata (never a null actor);
  - no invoke when a source is disabled, the node is offline, the
    capability is `not_granted`, or a workspace has no rules;
  - the cursor advances only after commit;
  - a failed invoke keeps the cursor;
  - **stale cursor:** a cursor 10 days old sends `since = now − 7 days +
    5 minutes` (asserted on the received frame), the reference node
    accepts it, `since_clamped` is incremented and `last_clamped_at`
    set, the `AuditLog` metadata carries `since_clamped: true` and the
    window bounds, and the next beat (cursor now recent) sends the plain
    cursor with no clamp;
  - **no cursor:** a first run sends `since = max(enabled_at, now − 7
    days + 5 minutes)` for a source enabled 2 days ago and for one
    enabled 30 days ago, and creates the cursor row only after commit;
  - three beats with a two-week-old cursor give three accepted invokes,
    never a repeated `since` refusal;
  - **ties:** a fixture with 5 turns sharing one `at` across a
    `max_turns` boundary yields every turn exactly once across two
    pulls (dedupe on `external_id`); a fixture where one timestamp holds
    more than `max_turns` turns moves the cursor by 1 ms and counts
    `tie_overflow`.
- **Hostile node:** it ignores `cwd_prefixes`, returns another
  workspace's session, returns an assistant-like turn with a
  `<system-reminder>` prefix, and returns aggregates. Each foreign row
  and marker turn is dropped and counted. Nothing is stored for them.
- **Actor check (the Phase 9 amendment):**
  - `invoke_read` of a personal-class capability (`node_sessions`'
    capability and both owner-turn capabilities) with `actor=None` →
    `server_refused:actor_not_owner`, nothing sent to the reference
    node, one chain row and one `AuditLog` row;
  - the same with an active non-owner user (a firm member) and with an
    inactive owner → the same refusal;
  - `channel=CHANNEL_SYSTEM` with any capability outside
    `SYSTEM_READ_CAPABILITIES` → `server_refused:not_invocable`;
  - the chat-turn path (Phase 9 branch 5 tests) is unchanged and still
    green;
  - the `(status, refusal_code)` vocabulary test includes
    `actor_not_owner`.
- **Rule source (mapping):**
  - a transcript turn whose `cwd` lies under a `path_prefix` rule of the
    owner's `claude_code_hooks` source is stored, in that rule's
    workspace, with `mapping_key = "path:" + cwd`;
  - a turn whose `cwd` lies outside every hooks rule is
    `dropped_unmapped`, counted on the **transcript** source, with the
    hooks source's counters unchanged;
  - a bare `cwd` key (no `path:` prefix) is unmapped (the existing
    `parse_mapping_key` rule, pinned);
  - with no `claude_code_hooks` source, every turn is unmapped;
  - a rule on another owner's hooks source never applies;
  - creating a rule on a transcript source is refused (400);
  - `git`, `harness`, `calendar` and hooks sources still resolve only
    through their own rules (regression).
- **Port:**
  - episodes in the mapped workspace only;
  - an unmapped `cwd` dropped;
  - dedupe on re-read;
  - a second scrub with `ScrubTerm`s;
  - personal-class → extraction with `personal_data=True` (FakeAdapter
    and a cloud-only set → `NoProviderAvailable`);
  - a fact quoted from a transcript owner turn survives provenance, and
    an assistant-looking quote does not.
- **Corpus:** 100% secrets recall including `transcripts.jsonl`, for
  the server scrubber and the vendored node copy.
- **Forget:** forgetting a transcript source cascades to its episodes,
  memories and facts.
- **Contracts:** `memory.ingestion` still imports no `nodes` and no
  `tools`.

**Done when:** in a test, the reference node's fixture transcripts become
scrubbed owner-turn episodes in the right workspaces through signed,
chain-audited invokes, and a nightly fake run turns one into a quoted fact
proposal.

### 9. `feat/p8-graph-ui` — `/memory/graph`, timeline, sources, conflicts, eval view

**Scope.** The frontend for D2–D4, D8, D10, D12, D13:
- `/memory/graph`:
  - entity search;
  - an entity page with current facts and a timeline of superseded
    ones;
  - "as of" date picker;
  - per-item edit (which supersedes), trash and restore;
  - Graphify entities read-only with the run's sha;
  - an accessible list view first, with no canvas graph library (keyboard
    and screen-reader first; a visual graph is Obsidian's job, D12).
- The ingestion audit view:
  - the transcript and inbox sources;
  - their "path rules shared with Claude Code hooks" notice;
  - the dropped-not-owner and unmapped counts;
  - per transcript source, the `since_clamped` count, the last skipped
    window and the `tie_overflow` count (D13);
  - the Graphify run list (status, sha, counts).
- The vault run detail: the conflicts count and folder.
- `/memory/evals`: the last runs' metrics, read-only, from branch 3's
  `GET /api/v1/memory/evals/runs` (owner only, `for_actor()`).
- BFF routes and `tr.ts` strings.

**Files.**
- `frontend/src/app/(shell)/memory/{graph,evals}/`;
- `frontend/src/components/memory/{GraphView,EntityTimeline,EvalRunsView}.tsx`
  and tests, `IngestionView.tsx`;
- `frontend/src/app/api/memory/{graph,evals}/`,
  `frontend/src/lib/i18n/tr.ts`;
- `backend/memory/graph/views.py` (the Graphify run read endpoint, if
  not already in 5). The eval run read endpoint is branch 3's
  `GET /api/v1/memory/evals/runs`; branch 9 adds only the BFF route.

**Tests.**
- **Vitest:**
  - entity page states (empty, current only, with history, Graphify
    read-only with no edit control);
  - the "as of" date changes the request;
  - edit sends supersede;
  - trash confirmation names the entity and its fact count;
  - the sources view shows the shared-rules notice and counts;
  - the conflicts count renders;
  - the eval view shows metrics and never case text.
- Contrast for new tokens. Phone-width layout. Keyboard navigation of
  the timeline.
- **Backend:** role × endpoint for any endpoint added here.

**Done when:** the owner can find an entity, read its facts as of a date,
correct one, see Graphify's code entities, and see transcript, inbox and
eval status — at phone width too.

### 10. `chore/p8b-closeout` — preflight, e2e, docs, go/no-go record

**Scope.**
- **`manage.py preflight` checks:**

  | Check | What it verifies |
  |---|---|
  | `graph_integrity` | no fact crosses workspaces (subject, object and fact share one workspace); every approved fact with `superseded_by` set has a non-null `valid_to`; no fact has a `valid_to` earlier than its `valid_from` |
  | `retrieval_indexes` | `build_vector_indexes --check`: an HNSW index exists per active embedding provider; GIN indexes exist; `MEMORY_RETRIEVAL_MODE` reported |
  | `learning_budget` (extended) | the reconciliation, reflection and eval budgets are configured |
  | `learning_beat` (extended) | the reflection, transcript pull and Graphify recovery beats are scheduled |
  | `learning_scrubber` (extended) | the corpus includes `transcripts.jsonl` at 100% secrets recall |
  | `graphify` | the image exists locally with the pinned label; the last run for each enabled repo succeeded; **warn** if none ran |
  | `compressor` | if `AI_COMPRESSION_ENABLED`: the sidecar is healthy, telemetry env is off, and every enabled purpose has a recorded eval result; else **ok (off)** |
  | `transcripts` | if a transcript source is enabled: the node capability is invocable and the last pull's status; **warn** when not granted (the Mac session is deferred) |
  | `eval_baseline` | a private baseline `MemoryEvalRun` exists; **warn** (pending owner evaluation) if not |

- **E2E (CI, fakes):** reference node with fixture transcripts → pull →
  episodes → night (eager) → fact proposal → approve → hybrid retrieval
  answers a temporal question in a fake chat turn with the fenced fact →
  a vault run mirrors the entity note → forget the transcript source →
  the fact and note are gone on the next run → restore.
- The retrieval default decision (D8): flip `MEMORY_RETRIEVAL_MODE` to
  `hybrid` only if the public-set compare shows no regression and the
  owner's private compare is recorded. Otherwise keep `legacy` and
  record it.
- **Docs:**
  - `docs/runbooks/learning-memory.md` (facts, transcripts, inbox,
    conflicts) and `memory-eval.md` complete;
  - the ADR-012, ADR-013 and ADR-014 notes completed;
  - MASTER_PLAN §AA row 8B status;
  - `self_sources.json` regenerated if the current-phase row changes;
  - this brief's status table and go/no-go record.

**Files.**
- `backend/ops/preflight.py`, `backend/ops/tests/test_preflight_graph.py`,
  `backend/ops/tests/test_transcripts_e2e.py`;
- `docs/runbooks/`, `docs/adr/`, `docs/MASTER_PLAN.md`,
  `backend/assistant/self_sources.json`;
- `docs/prompts/phase-8b-transcripts-graph/brief.md`.

**Tests.** Each new check passes, fails and warns with fakes. The HUD
reads the new failed-check names. The e2e test is green.

**Done when:** preflight is green on the dev stack with the reference node
paired, the e2e test passes, and the go/no-go record is written with the
Mac and private-data rows pending owner evaluation.

## Branch table

| # | Branch | Scope | Depends on | Status |
|---|---|---|---|---|
| brief | `docs/p8b-brief` | This brief (full, from the stub), MASTER_PLAN §AA row 8B Brief/Status | Phase 8A closeout; Phase 9 brief | **implemented 2026-11-20** (docs only; pins verified 2026-11-21 against PyPI and the official repos; review round 1 findings fixed 2026-11-21; review round 2 findings fixed 2026-11-21; review round 3 findings fixed 2026-11-21; awaits the Sonnet 5 re-review) |
| 1 | `feat/p8-temporal-facts` | `MemoryEntity`/`MemoryFact`, validity windows, supersede, recursive-CTE traversal, owner API, forget/trash/purge cascade, `memory.graph` contract | brief approved | not started |
| 2 | `feat/p8-fact-learning` | Episode extraction schema v1 → v2 with facts (chat extraction unchanged at v2), inherited fact sensitivity, entity-in-quote rule, reconciliation, weekly reflection, caps, digest fact items | 1 | not started |
| 3 | `feat/p8-eval-set` | Eval harness, public seed, private loader, `memory_eval`, `memory.eval_answer` purpose, CI public run | 1 (parallel with 2) | not started |
| 4 | `feat/p8-hybrid-retrieval` | `tsvector` + GIN, per-provider HNSW expression indexes, RRF + decay + hops, flag, `<memory_fact>` block, `memory_graph_lookup` | 1, 3 | not started |
| 5 | `feat/p8-graphify-job` | Hash-pinned image, `--network none` runner primitive, trigger, importer + temporal replace, `self` codebase line, manifest | 1 (parallel with 2–4) | not started |
| 6 | `feat/p8-headroom-stage` | `compressor` sidecar (no `litellm`, no egress, telemetry off), gateway stage, bypass set, context report, eval arm, manifest | 3 | not started |
| 7 | `feat/p8-obsidian-mirror` | Format v2, entity notes, `_conflicts/<date>/`, `00_Inbox` import-once inside the approved run | 1 | not started |
| 8 | `feat/p8-transcript-ingestion` | Owner-turn capabilities, reference readers + conformance, pull job in `tools`, port, cursor, personal-class sources, transcript corpus | 2; **Phase 9 branches 2–5 merged** | not started |
| 9 | `feat/p8-graph-ui` | `/memory/graph`, timeline, sources/Graphify/conflict/eval views | 2, 4 (5, 7, 8 for their panels) | not started |
| 10 | `chore/p8b-closeout` | Preflight checks, e2e, retrieval default decision, runbooks, ADR notes, MASTER_PLAN row, go/no-go record | 1–9 | not started |
| M1 | `feat/p8-node-transcript-readers` | Swift readers for the two owner-turn capabilities, grant wiring, real-file rule verification, conformance pass | 8 merged; Phase 9 M2 | deferred: Mac session |
| M2 | Mac and owner evaluation | Private eval baseline, Headroom compare, real transcript run, go/no-go measurements | 3, 4, 6, M1 | deferred: Mac session (pending owner evaluation) |

**Branch count.** Ten rows against the roughly 4–8 target: eight
server-side feature branches, the UI and the closeout. Merging was
considered in review round 1 (2026-11-21) and not done. 3 (eval set)
is the measuring instrument that gates 4 and 6 and must merge before
either; folding it into 9 (UI) would delay that gate behind frontend
work. 6 (Headroom) is optional and may be dropped (owner step 4);
folding it into the closeout would make the closeout carry a gateway
change. Each row stays one reviewable unit with its own tests.

**Minimum usable cut** if the phase runs long: 1, 2, 3, 4 and 8 with M1
("knows the details of your work"). 5 (self-knowledge), 6 (Headroom,
which may stay off anyway) and 7 (Obsidian) are independent and can
follow.

## Mac session (deferred)

These tasks are for a later Claude Code session **on the owner's
MacBook**, after Phase 9's Mac session. They are self-contained: a fresh
session starts from this section, the merged branches 1–8 and the files
they name.

**Preconditions:**
- Phase 9 M1 and M2 merged: the `friday-node` app, its reader framework,
  the per-directory grants and the scrubber port;
- 8B branch 8 merged: the capability contract files, the reference
  readers, the conformance cases and `scrub.json` with the transcript
  corpus;
- a running Friday stack with the node paired.

### M1 — `feat/p8-node-transcript-readers`: Swift owner-turn readers

**Create:**
- `node/friday-node/Sources/NodeCore/Readers/ClaudeOwnerTurns.swift`
  and `CodexOwnerTurns.swift`: streaming JSONL readers implementing
  D13's owner-turn rules and D17;
- catalogue entries for `claude.transcripts.owner_turns` and
  `codex.transcripts.owner_turns`, whose schemas are loaded from, or
  checked byte-identical to, `backend/nodes/capabilities/v1/`;
- `Tests/NodeCoreTests/OwnerTurnReaderTests.swift`, run over
  `backend/nodes/reference/fixtures/{claude,codex}/` (the same fixtures
  the reference node uses) with output compared to the reference
  readers' expected output, which branch 8 commits as
  `backend/nodes/reference/fixtures/expected_owner_turns.json`.

**Must satisfy:**
- every conformance case in `backend/nodes/conformance/cases/v1/`,
  including the new owner-turn cases, run offline
  (`ConformanceTests.swift`) and live (`node_conformance_server`);
- scrub parity with `backend/nodes/conformance/vectors/scrub.json`,
  which includes `transcripts.jsonl`;
- availability `not_granted` until the Touch ID grant for the directory
  is on; revoking the grant gives `not_granted` immediately plus
  `grant.availability_changed`.

**Real-file verification (never committed).** On the owner's Mac:
1. Run `friday-node scrub-preview --owner-turns ~/.claude/projects/<one
   project>` (a debug-build-only command) and the Codex equivalent.
   They print **counts per line kind and per excluded marker**, never
   text.
2. If a real line kind is not covered by the D13 rules (a new Claude
   Code or Codex format), stop and update D13, the fixtures, the
   reference readers and the cases in one server branch. Then re-run.
3. Owner check: skim the first 20 accepted turns in the debug preview
   and confirm they are text the owner typed.

**Server-side contract tests it must pass:**
- branch 8's pull-job tests, re-run manually against the live Mac node:
  the episodes land only in mapped workspaces, with no assistant text in
  any stored row (a DB scan for known assistant phrases from the
  session);
- the e2e of the closeout, with the live node in place of the reference
  node.

### M2 — Mac and owner evaluation (go/no-go measurements)

Recorded in this brief's "Go/no-go record" by the owner or by the Mac
session:
1. **Private eval baseline.** Write about 100 private cases (runbook
   `memory-eval.md`). Run `memory_eval baseline --set private` with
   `legacy` retrieval and compression off, and record the metrics.
2. **Hybrid retrieval.** Run `memory_eval compare --retrieval hybrid
   --set private` and record both arms. This decides the closeout's
   default flip.
3. **Headroom.** Run `memory_eval compare --compression chat --set
   private`. Record the accuracy delta and the leak count. Enable `chat`
   only at ≤ 1 point loss and zero extra leaks. Otherwise the flag stays
   off.
4. **Transcripts.** Enable both transcript sources after the grant, run
   for one week, then record:
   - episodes per day;
   - the `dropped_not_owner` count, which should be 0 from a correct
     reader;
   - the scrubber hits by type;
   - the share of fact proposals accepted.
5. **Graphify on the real repo.** Set `graphify_enabled` for the Friday
   repo, merge one branch to `main`, and confirm one run. Then record its
   counts and duration.

## Go/no-go record (Phase 8B checkpoint)

| Item | How it is measured | Result |
|---|---|---|
| Eval baseline recorded | M2 item 1 (private); branch 3 CI (public) | pending (branch 3); private: pending owner evaluation |
| Headroom stays off unless ≤ 1 point loss | M2 item 3; `compressor` preflight | pending owner evaluation (flag off by default) |
| Scrubber corpus with transcript fragments at 100% secrets recall | Branch 8 corpus gate (server and vendored node scrubber); M1 parity | pending (branch 8); Swift parity: pending owner evaluation (Mac session) |
| Zero cross-workspace leaks | Branches 1, 4, 8 isolation tests; eval leak rate | pending (branches 1, 4, 8) |
| Owner turns only | Branch 8 hostile-node and marker tests; M1 real-file counts; M2 item 4 | pending (branch 8); real files: pending owner evaluation |

The Mac and private-data rows do not block Phase 10. Its server branches
can start once branches 1–10 are green.

## Out of scope (explicit)

- **Sources and storage:**
  - any graph database (Neo4j, FalkorDB, Apache AGE) or `networkx` in
    the backend;
  - LLM-made `self` wording;
  - Graphify's semantic (LLM) extraction, its docs, PDF and media
    passes, clustering, community labels and its MCP server or hosted
    endpoint;
  - Headroom's proxy, `wrap`, MCP server, prose (Kompress) model,
    reversible-compression retrieval (CCR) and dashboards;
  - compressing system or user messages;
  - compressing connector or node read output: it is fenced inside the
    system message (D11), and splitting it into tool-role messages is a
    `build_context` change for a later phase;
  - mail bodies and shell history (opt-in sources, still not built);
  - screen content, screenshots, AX trees, keystrokes, browser history
    (never, ADR-012).
- **Learning and approvals:**
  - DSPy/GEPA ("later");
  - model triage (`memory.triage`, still deferred: transcript extraction
    is on local providers and costs nothing);
  - `sessions.summarize` (D13);
  - auto-approval of facts or bulk approval of non-`work_log` items;
  - fact candidates from chat extraction (`friday_memory_extraction`
    stays at schema v2 without `fact`, D4; a later phase may add a v3);
  - `system` (Graphify) facts in hybrid retrieval or embeddings (D8);
  - voice approval of anything (ADR-015);
  - an LLM judge in the eval;
  - committing private eval cases or any real transcript.
- **Node and vault:**
  - node-pushed (`event`) transcript streaming (the pull keeps the
    server's request signed and audited per call);
  - Codex or Claude assistant text as provenance;
  - two-way vault sync, or applying owner vault edits to Postgres;
  - a Windows or Linux node.

## Test strategy

**Discipline.** The same as Phases 2–9 (CLAUDE.md rules 5, 7, 8):
- ruff, pytest, vitest, import-linter (new contracts) and
  `makemigrations --check` are green per branch;
- plus `build_self_sources --check`, `build_node_conformance --check`
  (branch 8) and `build_vector_indexes --check` (branch 4 onwards in
  preflight).

**What no test touches.** No test touches a real LLM, Graphify run,
Headroom process, Docker daemon, Mac, Keychain, Touch ID, Claude Code,
Codex, Obsidian app, GitHub or git remote. The tests use:
- FakeAdapter with captured payloads and call counts;
- a fake compressor client (HTTP stub);
- the captured `graph.json` fixture;
- the runner's module-attribute seam for `docker`;
- the Python reference node (in-process, and a localhost socket for the
  conformance run);
- synthetic JSONL transcript fixtures;
- a temporary vault directory;
- real Postgres with pgvector and Redis (the test DB), because HNSW
  `EXPLAIN` and `tsvector` behaviour need the real engine.

Real models, the real Graphify image run and the real compressor run
only nightly or owner-run (opt-in env flags).

**Corpus gates:**
- the scrubber corpus, now with transcripts, at 100% secrets recall,
  for the server scrubber and the vendored node scrubber;
- the prompt-injection corpus (D14).

**Role × endpoint** for every new surface (the graph API, the digest
fact actions, the eval-run reads, the read tool by channel) against the
five Phase 4 principals plus a `hook` PAT and a node secret. Isolation
is tested via a second (firm) workspace with its own rules, facts and a
non-owner member.

**Release-gate regressions** (escalate per the CRITICAL/HIGH rule):
- **Leaks:**
  - a fact, edge or entity crossing workspaces;
  - a retrieval candidate from another workspace;
  - firm data in a personal prompt;
  - a `private` fact injected.
- **Learning and provenance:**
  - a learned fact born approved;
  - a fact whose quote is not verbatim owner text, or whose entity is
    not in the quote;
  - assistant, tool or injected text stored as a transcript owner turn;
  - a raw corpus secret in any row, log, gateway payload, node result
    frame or vault file;
  - a transcript episode, or a `private` (Inbox or other) episode,
    extracted without `personal_data=True` (a normal Inbox note is not
    personal-class, D12);
  - a `private` fact or the name of a private entity in any model
    payload (the memory block, hop expansion, `memory_graph_lookup`) or
    in an open-tree vault file;
  - an `origin=graphify` fact in the `<memory_fact>` block, or an embed
    call for a `system` fact;
  - more than 10 proposals in a night, 6 fact proposals in a night or 3
    core-block proposals in a week.
- **Gateway and dependencies:**
  - a model call without a `ModelUsage` row;
  - a compression of a bypass purpose or of a system or user message;
  - `litellm` or `headroom` importable from the backend;
  - a Graphify container with any network.
- **Vault:**
  - a vault write outside an approved run;
  - an owner-edited note overwritten without a conflict copy.
- **Node:**
  - a transcript invoke without a chain row, or with another
    workspace's prefixes.

## Risks

- **R1 — Transcript format drift.** Claude Code and Codex change their
  JSONL without notice. The bounds:
  - readers skip unknown line kinds (fail closed, nothing returned);
  - the server re-checks markers;
  - M1's counts-only real-file preview;
  - one branch updates rules, fixtures and cases together.

  Residual: a new injected-text marker passes as owner text until it is
  added. Provenance quotes, fencing and per-item approval still stop it
  from becoming an approved row unseen.
- **R2 — Pasted third-party text in owner turns.** The owner pastes
  logs, web text or a colleague's message. It is owner-chosen and
  scrubbed, but it is not the owner's statement. The bounds: per-item
  approval with the quote shown, the 4000-character turn cap, and the
  reflection and extraction prompts telling the model that pasted
  blocks are not preferences. Residual: accepted, as the owner reviews
  every item.
- **R3 — Graph pollution.** Wrong entities or duplicate aliases make
  wrong answers confident. The bounds: code entity resolution, the
  entity-in-quote rule, the nightly entity cap, per-item approval, owner
  edit-as-supersede and eval temporal cases.
- **R4 — Headroom quality loss or supply chain.** The bounds:
  - off by default, per purpose, with a hard bypass set;
  - only assistant history messages are sent (Friday has no tool-role
    messages; fenced reads stay in the system message, D11);
  - a compression compare that compressed nothing is `inconclusive`;
  - an eval gate of ≤ 1 point and zero leaks;
  - sidecar isolation: no egress, no keys, no `litellm`, telemetry off,
    a hash pin;
  - fail-open to the original messages.

  If the dependency set cannot exclude `litellm`, Headroom is not
  adopted (D11).
- **R5 — Graphify supply chain.** A single maintainer, fast releases, a
  typosquat-prone name. The bounds: an exact name, version and hash
  with the transitive wheels hash-locked, binaries only, `--network
  none`, read-only, no credentials, the output schema-validated as data,
  and an upgrade only in a dedicated branch.
- **R6 — Retrieval regressions.** Hybrid ranking could surface worse
  context. The flag defaults to `legacy`, and the flip needs the eval
  compare. `legacy` stays byte-identical (golden test).
- **R7 — Index drift on provider change.** A new embedding provider has
  no HNSW index until `build_vector_indexes` runs. It runs in
  `post_migrate` and preflight fails without it. Until then queries
  still work (sequential scan).
- **R8 — Review load.** Facts add proposal volume. The 6-of-10 fact cap,
  the entity cap and reflection inside the same 10 bound it. The 8A
  acceptance metric is extended per kind.
- **R9 — Vault conflicts surprise the owner.** Today edits are silently
  overwritten. v2 saves them, and the notice says where. The vault
  remains a mirror, and the runbook explains it.

## Open questions (with recommended answers)

- **Q1 — Push transcripts from the node instead of pulling?** No. A pull
  is a signed, per-workspace, chain-audited read with `cwd_prefixes`
  filtering at the source. A push would add a new `event` kind and an
  unsigned upload path for the most sensitive data. Revisit only if
  pull latency (15 minutes) matters.
- **Q2 — Separate path rules for transcripts?** No. One path registry
  (Phase 9 D9). The audit view says the rules are shared.
- **Q3 — Should Graphify facts go through the morning card?** No (D3).
  They are model-free facts about Friday's own code. The owner disables
  them per repo.
- **Q4 — Should `chat` be the only Headroom candidate?** Yes for now.
  It is the only eligible purpose whose payload carries multi-turn
  assistant history. Friday has no tool-role messages: connector and
  node results are fenced inside the single system message, which is
  never compressed (D11). The other purposes are bypassed or send only
  a system and a user message, so there is nothing to compress. Tool
  output compression would need a `build_context` change in a later
  phase.
- **Q5 — Keep the `Inbox/` name from ADR-012?** Use the documented
  `00_Inbox/`. ADR-012's note records the mapping, with no ADR text
  change.
- **Q6 — Facts in firm dossiers?** Firm facts live in the firm
  workspace like everything else. `Workspace.profile` is unchanged
  (8A Q5).

## Owner steps (pending) — recorded 2026-11-20

The stub had no owner-step list. Its precondition ("Phase 9 closeout and
the Phase 8A go/no-go") is restated here as steps:
1. **Plan review.** Run the independent Sonnet 5 plan review of this
   brief and fix the findings until APPROVE (CLAUDE.md "Reviews").
2. **Phase 8A go/no-go.** Record the ≥ 60% proposal acceptance over two
   weeks of real use (8A brief). If it is below 60%, tune 8A before
   enabling 8B's transcript sources (branch 8 can still merge with them
   disabled).
3. **Phase 9.** Its server branches 2–5 must merge before branch 8, and
   its Mac session (M1–M2) before 8B's M1.
4. **Per-directory grant.** Grant the per-directory reads
   (`~/.claude/projects`, `~/.codex/sessions`) with Touch ID, then
   enable the two transcript sources (d5) after branch 8 and M1.
5. **Private eval set.** Write it (about 100 cases) under
   `~/friday-evals/` and run the baseline and the compares (M2).
6. **Graphify.** Set `graphify_enabled` for the Friday repo once branch
   5 is merged. Build the image on the runner host (`docker build` from
   `harness/docker/graphify.Dockerfile`; it needs PyPI access at build
   time only).
7. **Compressor.** Build the `compressor` image only if M2 item 3 is
   to be run. Enable `chat` compression only on a recorded pass.
8. **Obsidian inbox.** Opt in to the `obsidian_inbox` source and add
   `vault_prefix` rules (`00_Inbox/<folder>`) for the folders that should be
   imported.
9. **Record the go/no-go.** Record the measurements in the "Go/no-go
   record" above.

### Owner steps (pending) — `docs/p8b-brief`, recorded 2026-11-20

1. Run the independent Sonnet 5 plan review of this brief and fix
   findings until APPROVE (CLAUDE.md "Reviews").
2. Confirm the platform-boundary split: server branches 1–10 now, the
   Swift owner-turn readers and the real-data evaluations in a later Mac
   session.
3. Confirm the pins before branches 5 and 6 start. They were verified
   2026-11-21:
   - `graphifyy==0.9.103` and `headroom-ai==0.43.1`, each the newest
     release ≥ 14 days old;
   - their hashes in D10 and D11;
   - the Headroom repository has moved to `headroomlabs-ai/headroom`.

   The implementing branch re-checks the pin and may move to a newer
   release ≥ 14 days old, recording the new hash.
4. Accept the D11 consequence: Headroom is adopted only as a sidecar
   without `litellm`. If that proves impossible at branch 6, Headroom
   stays off and is dropped from the phase.
5. Review round 1 fixes (2026-11-21), to confirm with the plan review:
   - the Phase 9 `invoke_read` amendment of D13 (system-owner actor,
     `channel=system`, the `actor_not_owner` refusal), which branch 8
     records in the Phase 9 brief;
   - the mapping contract changes: the rule-source indirection for the
     transcript sources (branch 8) and the `vault_prefix` rule type
     (branch 7);
   - D11 compresses only assistant history (tool outputs are in the
     system message and out of reach), and the compression eval runs
     both arms under the candidate purpose with `history` fixtures.
6. Review round 2 fixes (2026-11-21), to confirm with the plan review:
   - the D13 `since` clamp (`max(cursor, now − 7 days + 5 minutes)`, the
     first-run start at the source's enable time, the `since_clamped`
     counter) and D17's inclusive `at >= since` with `external_id`
     dedupe: a Mac that sleeps for over a week loses the turns older
     than 7 days by design;
   - the `supersedes` FK on pending fact proposals (D2, D4, D7);
   - `MemoryEvalRun` as a scoped row in the owner's personal workspace,
     recorded from a metrics-only result file, with an owner-only read
     endpoint (D9, branch 3);
   - the tier-preserving `_private/_conflicts/` path (D12);
   - eval setup rows under the chat eligibility rules, with the
     `sensitivity: private` case flag (D9). The owner flags every
     private case built from firm or personal data.
7. Review round 3 fixes (2026-11-21), to confirm with the plan review:
   - Graphify `system` facts are never embedded and never take part in
     `retrieve()` or its hop expansion; they reach a model only through
     `memory_graph_lookup` and the deterministic `self` line (Boundary,
     D2, D8);
   - fact sensitivity is inherited from the cited episode, entity
     privacy is derived ("no live non-`private` fact"), and the lookup
     tool, hop expansion and the vault mirror exclude private facts and
     entities from model payloads and the open tree (D4, D8, D12);
   - the supersede-order rule (409 `supersede_order`, reconciliation
     falls back to `new`) (D2, D4);
   - chat fact extraction is dropped from 8B: only the episode parser
     moves v1 → v2, the chat parser stays at v2 (D4, branch 2);
   - the `obsidian_inbox` source is not personal-class; only
     `sensitivity: private` notes go to local providers (D12). The
     owner flags sensitive Inbox notes `private`;
   - the hostile-`/out` read (`O_NOFOLLOW`, regular file, size before
     read) and the persistent eval DB plus result-file spend sum for the
     `learning_eval` cap (D9, D10).
