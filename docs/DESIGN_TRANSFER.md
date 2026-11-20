# Design Transfer Contract — Jarvis → external installations

> Status: accepted 2026-07-07, alongside ADR-006. Jarvis and any external
> workplace product are **independent codebases**. What transfers is the *pattern* —
> the design, its invariants and its hard-won review lessons — never the code.
> Whether any of these patterns ever become a shared library is an open
> question (MASTER_PLAN §O); until decided, independent re-implementation is
> the rule. Jarvis is the design donor: when a pattern below evolves in
> Jarvis, this file is the place to note whether the change is worth
> re-implementing downstream.

Format per entry: **Concept transferred** / **Source in Jarvis** /
**Independent re-implementation notes**.

## 1. InferenceGateway (ADR-003)

- **Concept transferred:** a single interception point for all LLM traffic —
  provider adapters, retry-then-fallback across providers, and a usage record
  (tokens, cost, latency, purpose, tenant scope, prompt version, context
  report) per call.
- **Source in Jarvis:** `backend/ai_core/gateway.py`, `backend/ai_core/adapters/`,
  `ModelUsage` / `ModelProvider` / `PromptVersion` in `backend/ai_core/models.py`;
  ADR-003.
- **Independent re-implementation notes:** the load-bearing invariant is
  *exactly one usage row per gateway call on every path* — success, failure,
  timeout, retry-then-success, fallback, streaming completion and client
  disconnect (attempt detail goes in a JSON report, not extra rows). Provider
  SDKs stay confined to adapter classes; everything is mockable at one seam
  (`get_adapter`). Embeddings are a *capability* of the same gateway and the
  same usage table, not a parallel gateway (MVP 3 lesson: a separate
  EmbeddingUsage table was rejected in review as an ADR-003 violation).

## 2. Lease-fenced Celery job pattern

- **Concept transferred:** async pipeline jobs claimed via lease fencing over
  fixed, non-overlapping input ranges, with an idempotent finalize step and a
  periodic recovery scan for expired leases — so duplicate delivery, worker
  death and beat restarts never double-process.
- **Source in Jarvis:** `backend/memory/jobs.py` (+ `memory/tasks.py`);
  reused by the knowledge ingestion worker (`backend/knowledge/jobs.py`,
  MVP 3 branch 3).
- **Independent re-implementation notes:** dispatch with
  `transaction.on_commit` and never let dispatch failures propagate into the
  request path; the recovery scan runs on Celery beat — Jarvis embeds beat in
  its single worker, which is safe only at exactly one replica; a
  multi-worker deployment must split beat into its own service before
  copying the topology.

## 3. Approval gate rules (ADR-004) + cloud-eligibility negative contract

- **Concept transferred:** (a) write/delete/deploy/external-send happen only
  through a human-approved ApprovalRequest; hard rules no configuration may
  relax (agents never modify their own definitions/permissions, coder ≠
  reviewer, every tool call and cost logged). (b) The negative contract:
  `private`-sensitivity records never reach a cloud model payload, enforced
  by **two independent gates** — an ingestion-side gate (private content is
  never embedded/exported in the first place) and a retrieval-side queryset
  gate that never trusts the first.
- **Source in Jarvis:** ADR-004; `Memory.objects.cloud_eligible()`
  (`backend/memory/models.py`) and `KnowledgeChunkQuerySet.cloud_eligible()`
  (`backend/knowledge/models.py`).
- **Independent re-implementation notes:** keep the two gates structurally
  independent (different code paths, both tested with negative-contract
  tests). Auto-approval, if ever added, is per low-risk tool per user based
  on observed error rates — never for the hard rules. An external
  installation should map "external-send" to its own boundary (e.g. anything
  leaving the customer's server).

## 4. `for_actor()` scope gate (ADR-001)

- **Concept transferred:** one mandatory queryset gate through which every
  query reaching a view or agent tool must pass; the scope axis is a column
  on every business table; isolation is a release gate with its own test
  package.
- **Source in Jarvis:** `backend/core/db.py` (`for_actor()`,
  `WorkspaceScopedModel`/`QuerySet`/`Manager`); ADR-001.
- **Independent re-implementation notes:** where an installation's scope
  axis is wider than `workspace_id` (e.g. a **tenant × department
  intersection**), the gate should take the actor and resolve every
  dimension in one place rather than scattering per-dimension checks per
  view. Denormalize the scope column onto
  child tables (as Jarvis does on `KnowledgeChunk.workspace`) so the gate
  filters without joins. Role × endpoint permission tests accompany every
  API surface.

## 5. Memory approval flow + RAG chain design

- **Concept transferred:** (a) AI-suggested memories enter as
  `pending_approval` and pass a role-gated `approved | rejected` state
  machine; every access (read, context injection, export) is audited; users
  can edit/forget/export everything they can read. (b) The RAG chain:
  chunking → gateway `embed()` → provider/dimension-matched vector retrieval
  (pgvector cosine) → injection-safe framing into chat context → persisted
  provenance (`memory_ids` / `chunks_used`) + a context-access audit event,
  recorded even for empty retrievals.
- **Source in Jarvis:** `backend/memory/` (models, services, extraction,
  `build_context`), `backend/knowledge/` + `docs/prompts/mvp-03/brief.md`
  (approved design; retrieval lands in MVP 3 branch 4); ADR-005.
- **Independent re-implementation notes:** retrieved/injected text is
  untrusted input — keep the injection-safe neutralizing frame, don't
  re-derive it. Filter retrieval to a single embedding provider before any
  vector-distance ordering (a variable-dimension vector column only compares
  operands of equal dimension). Record provenance and the audit event on the
  same transaction as the answer persistence.

## 6. Agent harness patterns (Phase 2–3) — transfer candidates

> Added 2026-09-27. Proven in Jarvis's `harness` app (ADR-008) across the
> Phase 2–3 review arcs; listed as candidates only — none is required
> downstream until an installation runs external CLI agents itself.

- **Concept transferred:** running untrusted, vendor-supplied CLI agents as
  workers without trusting them: (a) a **container brake system** — runtime
  timeout, post-hoc cost ceiling (`budget_exceeded`), a kill switch polled by
  the owning worker (teardown only ever by that worker), and recovery scans
  that sweep leaked containers/worktrees; (b) **bundle-only push** — the
  agent's worktree never talks to the remote; a bundle is fetched into a
  separate clean clone and pushed by explicit SHA with a create-only
  compare-and-swap (`--force-with-lease=<ref>:`); (c) **process quiescence
  before reading agent-written state** — the wrapper stops agent processes
  before writing its result file, and the host reads it off the filesystem
  (regular file only, never via container stdout); (d) **fenced CLI output
  is never trusted as its own summary** — transcripts are bounded, fenced and
  neutralized, and the summary/verdict is produced by a separate gateway
  call; (e) **`record_external_usage()`** — non-gateway LLM spend (a CLI's
  own provider calls) still lands as exactly one `ModelUsage` row.
- **Source in Jarvis:** `backend/harness/execution.py` (brakes, recovery,
  fencing), `backend/harness/runner.py` (`push_bundled_commit`,
  `prepare_review_clone`, `read_implementation_result`),
  `backend/harness/docker/*_run.sh`, `backend/ai_core/gateway.py`
  (`record_external_usage`); `docs/prompts/phase-2-harness-v1/brief.md`,
  `docs/prompts/phase-3-harness-v2/brief.md`.
- **Independent re-implementation notes:** the review clone must also strip
  agent-instruction files (`AGENTS.md`, `CLAUDE.md`, case-insensitively) and
  isolate global/system git config, and diffs must be taken with
  `--text --no-ext-diff --no-textconv` — each was a real finding in the
  Phase 3 review arc. The cost ceiling cannot cancel spend already incurred;
  treat it as a flag, not a guard.
