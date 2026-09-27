# Phase 1 — Memory v2

> Status: **done (2026-07-19)** — all four branches merged; see the
> closeout note at the end of this brief.
>
> Approval history: **approved (Codex plan-review, 5 rounds, 2026-07-18)** — the
> review arc: round 1 CRITICAL+3HIGH (branch 1 would have embedded
> private/unapproved content); round 2 found the round-1 fix incomplete
> (leftover contradictory sentence, no channel to gate branch 4's overlay
> on, a private-conversation race); round 3 found a fresh CRITICAL (the
> private flag was reversible, reopening a skipped extraction window via
> the unmoved cursor — fixed by making it one-way) plus a finalization-time
> race; round 4 dropped to 1 HIGH + 2 MEDIUM (embedding-lifecycle entry
> points under-enumerated); round 5 came back **0 CRITICAL/HIGH**, only
> 1 MEDIUM + 2 LOW, all fixed. User checkpoints after rounds 3 and 4 both
> chose to continue rather than stop early. Ready for branch 1
> implementation. (Phase-0 precedent: 3 rounds; this phase needed 5 —
> branch 4's cross-workspace overlay and branch 2's private-conversation
> flag were genuinely harder designs than anything in Phase 0.)
> Parent decisions: ADR-008, `docs/VISION_JARVIS_2.md` §6-7, MASTER_PLAN §O/§F.

## Goal / value

Close the two structural gaps memory (MVP 2) shipped without: no semantic
retrieval (deterministic type-priority ordering only, `memory/context.py:11-13`)
and no system-generated activity record (every memory today is either
`ai_suggested` from a conversation or `user_created`). Phase 1 adds
embeddings-backed retrieval, the `work_log` memory class (ADR-008 k.4), a
firm-dossier summary surfaced from the profile data Phase 0 already collects,
and — the one real scope decision left open since MVP 2 — a narrow,
audited cross-workspace read for the personal assistant (MASTER_PLAN §O).
This is the memory groundwork the harness (Phase 2) needs before it can log
its own runs; no harness code lands here.

## Boundary vs. existing systems

- `memory` is extended, not rebuilt: `Memory`'s status machine, fingerprint
  dedup, `cloud_eligible()` gate, and the `memory.context_accessed` audit
  discipline (`memory/context.py`) all stay. Embeddings and `work_log` are
  additive fields/values on the same model.
- `assistant/context.py`'s `build_context` gains one more render step (firm
  block), changes `select_context_memories`'s internal ordering, adds
  `firm_profile_injected` to `context_report` (branch 3), and gains a
  required, explicit `channel` parameter (branch 4 — see below); the
  memory/knowledge independence (`assistant/context.py:6-8`) is untouched.
  `build_gateway_stream` (`assistant/services.py:244`) threads `channel`
  straight through to `build_context` the same way it already threads
  `intent`. Its two real call sites both already have the value in hand:
  `assistant/views.py:154` already computes `channel = request_channel(request)`
  (covers both JWT/web and PAT/CLI, since `request_channel` reads
  `request.auth`) but doesn't pass it today — this branch wires that
  through; `integrations/tasks.py:203`'s Telegram task has no `request` at
  all but already knows its own channel (it hardcodes
  `Message.Channel.TELEGRAM` at line ~213) and passes `channel=
  CHANNEL_TELEGRAM` (`core/channels.py:14`) explicitly. No default value —
  mirroring the Phase 0 branch 1 precedent that made `channel` required,
  keyword-only on the approvals service boundary for the same reason: an
  omitted-by-accident channel must fail loudly, not silently default to the
  most-trusted one.
- `workspaces.profile` (Phase 0, `workspaces/models.py:38`) is read, not
  changed in shape — this phase adds a summary render + a `/workspaces` UI
  view, not new profile schema.
- Knowledge's pgvector/lease-fencing/dimension-guard patterns
  (`knowledge/models.py`, `knowledge/context.py`) are the reference design
  (`docs/DESIGN_TRANSFER.md` precedent already set) — memory embeddings copy
  the same shape rather than inventing a second one.
- Nothing imports anything new across app boundaries (import-linter stays
  as-is); ADR-001 workspace scoping is untouched outside branch 4's narrowly
  scoped exception.

## Scope (one branch each)

### 1. `feat/p1-memory-embeddings` — semantic retrieval

- `Memory.embedding` (`VectorField(null=True, blank=True)`, no fixed
  `dimensions` kwarg — same MVP 3 §1 policy as `KnowledgeChunk.embedding`,
  `knowledge/models.py:151`) + `Memory.embedding_provider` FK to
  `ai_core.ModelProvider`.
- Lease-fencing fields go directly on `Memory` (`claim_token`,
  `lease_expires_at`, `retry_count`) — one embedding *attempt in flight* at
  a time per row (the lease prevents two concurrent workers embedding the
  same row simultaneously), the same field shape as `KnowledgeDocument`'s
  (`knowledge/models.py:84-91`), **not** `MemoryExtractionJob`'s
  per-message-range job model (that problem — allocating non-overlapping
  ranges — doesn't exist here: a `Memory` row's embedding target is always
  "this row's current title+content," like a document's). This is
  lease-per-attempt, **not** embed-exactly-once (Codex round-3 HIGH: the
  lifecycle rules below mean a row can be legitimately re-embedded any
  number of times over its life — on content/title edit, or re-entering
  `cloud_eligible()` after an archive/restore cycle — `restore_memory()`
  only reverses `archived → approved`; there is no `rejected → approved`
  path, `memory/services.py:225,257`, Codex round-4 MEDIUM).
- **Embedding eligibility is `status=approved AND sensitivity=normal` —
  i.e. `cloud_eligible()` (`memory/models.py:35`) rows only, never "every
  existing `Memory` row" (Codex round-1 CRITICAL: the negative contract in
  `memory/context.py:3` — private/unapproved content must never reach
  `InferenceGateway.embed()` — applies to embedding exactly as it already
  applies to context injection; knowledge's own precedent skips embedding
  for private docs before any `texts` list is built,
  `knowledge/tasks.py:178`). Backfill job (Celery, lease-fenced claim/
  release, mirrors `knowledge`'s ingestion worker) walks `cloud_eligible()`
  rows missing an embedding only.
- **Embedding lifecycle, not just creation** (Codex round-1 HIGH: unlike an
  immutable `KnowledgeChunk`, a `Memory` row is editable and
  transitionable — `edit_memory()` can change content/sensitivity/type,
  `approve_memory()` can approve with curated content/sensitivity, and
  `forget_memory()` archives, `memory/services.py:173,191,239`). One
  embedding attempt per row is not enough, and the rule is not "every
  service function that happens to touch `status`/`sensitivity`" — it is a
  single reconciliation the brief pins explicitly, since round 4 found the
  round-1/2 bullets missed several real entry points (`create_memory()`,
  `record_work_log()`, `restore_memory()`) and the backfill worker is
  **not** a substitute recurring safety net for any of them (it only
  covers rows that were never enqueued at all, e.g. pre-Phase-1 history —
  relying on it to eventually catch a missed transition would silently
  delay retrieval by however long the backfill job takes to reach that
  row):
  **Rule:** every service-layer write that can change a `Memory` row's
  `status`, `sensitivity`, or embedded source text (`title`/`content`,
  Codex round-5 LOW: the rule as first worded didn't name the third
  trigger the `edit_memory()` bullet below already depends on)
  re-evaluates `cloud_eligible()` membership and embedded-text freshness
  immediately after the write, in the same transaction, and reconciles:
  newly-eligible-and-not-yet-embedded or stale-text → enqueue;
  no-longer-eligible → clear the stored embedding now, don't wait for
  anything to notice later.
  Enumerated call sites this applies to (Codex round-4 HIGH — all of the
  following, not just approve/edit/exit):
  - `create_memory()` (`memory/services.py:130-158`) births a row
    `APPROVED` directly (the user is their own curator) — a `normal`-
    sensitivity row is `cloud_eligible()` from creation and enqueues
    immediately, not on some later transition.
  - `record_work_log()` (branch 2, new) births `APPROVED` +
    `system_generated` rows the same way — same immediate-enqueue rule if
    `normal`.
  - `approve_memory()` (`memory/services.py:191`) — enqueue on entering
    `cloud_eligible()` with the curated (possibly changed) sensitivity.
  - `edit_memory()` (`memory/services.py:173`) on an already-`approved`
    row: title/content change → clear + re-enqueue (embedded text is
    title + content concatenated, matching the cost accounting
    `select_context_memories` already uses for both fields together,
    `memory/context.py:60` — either field changing invalidates the
    vector, Codex round-2 LOW); a sensitivity change alone (`normal ↔
    private`) → enqueue or clear per the new value, with no content change
    to also re-embed.
  - `restore_memory()` (`memory/services.py:225,257`, `archived →
    approved` only — there is no `rejected → approved` path) — enqueue if
    the restored row's sensitivity is `normal`.
  - exit `cloud_eligible()` via `reject_memory()` (a `pending_approval`
    row was never eligible, so this is a no-op for embeddings) or
    `forget_memory()`/archive (was eligible, now isn't) → clear the stored
    embedding immediately — a vector derived from now-private or
    now-archived content must not persist anywhere, matching ADR-005's
    negative contract in spirit, not just "invisible to retrieval."
  This lifecycle hook lives in `memory/services.py`'s existing transition
  functions, not as a separate poller.
- Uses `InferenceGateway.embed(purpose="memory.retrieval", ...)` — the
  model-tier skeleton (Phase 0 branch 4) can route this purpose once
  configured, no gateway change needed.
- `select_context_memories` (`memory/context.py:40`): when a query
  embedding is available, blend cosine similarity with the existing
  type-priority/recency ordering rather than replacing it outright —
  `preference`-type memories should not lose their priority just because a
  `semantic` memory scores marginally closer. Exact blend (weighted sum vs.
  similarity-first-within-tier) is this branch's design task, written up in
  the branch's own PR description before implementation, not decided here.
  Memories still missing an embedding (backfill in progress) or a
  `GatewayError` from `gateway.embed` (`knowledge/context.py:84-89`'s
  non-propagating pattern) fall back to the current deterministic ordering
  — retrieval degrading to MVP 2 behavior, never failing the turn. The
  exact blend formula and its tie-breakers (Codex round-1 LOW: memory
  selection is deterministic by design, `memory/context.py:49`, and must
  stay fully deterministic under the blend too) are pinned in this branch's
  PR description before implementation starts, not left as a runtime
  free variable.
- Guards copied exactly from `knowledge/context.py`'s `_VectorDims` +
  `embedding_provider_id` filter (`knowledge/context.py:26-37,108-132`): a
  provider swap must never let a cross-dimension vector reach the `<=>`
  operator. This is a repeat of a real bug class (MVP 3 branch 3) — do not
  re-derive it from scratch.
- Tests mirror knowledge's: FakeAdapter for the embed call, dimension-guard
  regression test, backfill idempotency + lease-expiry recovery, and a
  fallback-ordering test for the no-embedding/gateway-error paths.

### 2. `feat/p1-work-log` — system-generated memory class

- `Memory.Type.WORK_LOG` added to the existing `Type` choices
  (`memory/models.py:52-56`).
- Auto-approval (ADR-008 k.4) must key off **provenance**, not
  `memory_type` alone: a conversation-derived `work_log` candidate carries
  the same type as a system-generated one but must stay `pending_approval`
  (today's extraction hardcodes `source=ai_suggested` +
  `pending_approval`, `memory/jobs.py:187` — unchanged for conversation-
  derived rows). `Memory.Source` (`memory/models.py:67-69`) needs a third
  value — `SYSTEM_GENERATED` — distinct from `AI_SUGGESTED` (conversation
  extraction) and `USER_CREATED`; only `type=work_log AND
  source=system_generated` rows are born `APPROVED`. No other
  type/source combination may auto-approve, and the check lives in **one
  new service function** (e.g. `memory.services.record_work_log()`) that is
  the *only* creation path for `system_generated` rows — no direct
  `Memory.objects.create()` calls from a future producer (Codex round-1
  MEDIUM) — so a future auto-approved class can't be added by accident, and
  every system-generated row gets a matching `memory.system_recorded` audit
  entry the same way every other mutation does (`memory/services.py`'s
  `_record()` pattern).
- Closes MASTER_PLAN §O's `private`-conversation-flag half: `Conversation`
  gains a `private` boolean (no such field exists today —
  `assistant/models.py:8-40` confirmed), settable through a new field on
  the conversation create/update serializer (today's serializer only
  exposes title/workspace, `assistant/serializers.py:6` — no private flag
  surface exists to extend — this branch adds the missing conversation
  detail/update route, serializer, and view, plus the frontend BFF call
  (Codex round-2 MEDIUM: no `ConversationDetail`/update endpoint exists
  today, only `ConversationCreateSerializer`, `assistant/serializers.py:13`,
  and `assistant/urls.py` has no per-conversation route).
  **`private` is one-way: `False → True` only, enforced at the service
  layer, not just a UI convention** (Codex round-3 CRITICAL: the extraction
  cursor `last_extracted_message` advances independently of the flag,
  `memory/jobs.py:70-78,122` — allowing `private → False` again would let
  the *next* job's range span back across the interval that was skipped
  while private, silently extracting content the user had explicitly
  marked off-limits; making the transition irreversible removes the
  reopened-window case entirely rather than trying to patch cursor
  semantics around it). A request to unset an already-`True` flag is
  rejected the same way any other invalid transition is
  (`InvalidTransition`-style error, mirroring `memory/services.py`'s
  existing transition-guard pattern).
  When set, the flag must be enforced at **four** points, not two (Codex
  round-1 HIGH covered the first two; round-2 and round-3 HIGH found two
  more still open):
  (a) allocation — `_persist_assistant_message()`
  (`assistant/services.py:394`) must not create a new
  `MemoryExtractionJob` for a `private` conversation;
  (b) processing — the worker (`memory/tasks.py:95`) must re-check the
  flag and, for an already-allocated `pending`/`running` job whose
  conversation is now private, finalize it through the existing
  `finalize_extraction_success()` path with zero candidates
  (`memory/tasks.py:95-139` — a bare early return would leave the row
  stuck `running` until lease-expiry recovery, not a clean skip);
  (c) **finalization-time guard, inside the fenced transaction** (Codex
  round-3 HIGH: (b)'s check and the actual candidate-creation call are not
  atomic — a job can pass the worker-time check, call the extraction
  prompt, and only then have the conversation flip to private before
  `finalize_extraction_success()`'s bulk-create runs). The same
  transaction that calls `Memory.objects.bulk_create(...)`
  (`memory/jobs.py:187-213`) must re-check `conversation.private` under
  lock immediately before that call and finalize with zero candidates
  instead if it is now true — this is the actual race-closing check, (b)
  is the common-case fast path; and
  (d) **retroactive cleanup, covering rows created before the flag was
  set** — marking a conversation private must, in the same transaction:
  reject every still-`pending_approval` `Memory` row whose
  `source_conversation` is that conversation, **and** archive every
  already-`approved` row with that `source_conversation` (Codex round-3
  MEDIUM: an approved-before-private-flag row otherwise stays
  `cloud_eligible()` indefinitely, `memory/models.py:36`) — archiving
  triggers the branch-1 exit-`cloud_eligible()` lifecycle hook, so its
  embedding is cleared too. `approve_memory()` (`memory/services.py:191`)
  additionally gets a defensive check refusing to approve any memory whose
  `source_conversation.private` is true, as belt-and-suspenders against the
  narrow remaining window between (d)'s cascade and a human's approval
  click on a row not yet caught by it. No candidates of any type are
  produced, approved, or left `cloud_eligible()`, not just `work_log`.
  Document the one-way decision and why (irreversibility over cursor
  patching) in this branch's PR, then update MASTER_PLAN §O to close it.
- No harness exists yet (Phase 2) to generate real `work_log` rows — this
  branch builds the class, the auto-approval rule, and the private-flag
  gate, tested against directly-created rows (same "skeleton before real
  usage" pattern Phase 0 branch 4 used for model tiers). Wiring a real
  producer is Phase 2's job.
- Still auditable, forgettable, restorable and exportable: no exemption
  from the existing flow (ADR-005) for `work_log` rows. ("Deletable" is
  the wrong word here, Codex round-5 LOW — `record_work_log()` rows are
  born `approved`, and `delete_memory()` only hard-deletes never-approved
  rows, `memory/services.py:257`; `forget_memory()`'s archive is the actual
  removal path a `work_log` row goes through.)

### 3. `feat/p1-firm-dossier` — firm profile surfaced in context + UI

- No new schema: `Workspace.profile` and its retrieve/PATCH endpoint
  already shipped (Phase 0 branch 2, `workspaces/models.py:38`,
  `workspaces/serializers.py`). This branch is a render + injection + UI
  branch.
- `assistant/context.py` gains a firm-block render step, parallel to
  `memory_block`/`knowledge_block` (`assistant/context.py:84-101`): a short,
  length-bounded summary of `conversation.workspace.profile` (repos,
  services, deploy targets, notes), same fencing discipline
  (`neutralize_fence`/`strip_invisible_formatting`,
  `memory/context.py:120-157`) since profile `notes` is free-text the user
  wrote themselves but still gets framed as data, not instructions, for
  consistency with every other injected block. Not a `Memory` row — sourced
  directly from the workspace, no extraction job. `context_report`
  (`assistant/context.py:131-142`) gains `firm_profile_injected` (bool) and
  a bounded character count for the block — no profile content in the
  report itself — so ModelUsage observability covers this injection the
  same way it already covers memory/knowledge counts (Codex round-1
  MEDIUM).
- `/workspaces` UI: a dossier view rendering the profile (Phase 0 explicitly
  deferred this — brief §2: "Dossier UI ships in Phase 1"). Frontend-only,
  reads/writes the existing retrieve/PATCH endpoint — the frontend today
  only has a list BFF route (`frontend/src/app/api/workspaces/route.ts`),
  so this branch explicitly adds the detail/PATCH BFF route(s) plus tests
  against the existing backend endpoint (Codex round-1 LOW).

### 4. `feat/p1-personal-overlay` — narrow cross-workspace read

- Closes MASTER_PLAN §O's cross-workspace overlay decision. Design
  constraint already fixed by the vision (§7 item 4): read-only, `work_log`
  memories only, every read audited, `private` sensitivity never included
  regardless of source workspace.
- **`for_actor()`-membership alone is not the right gate for this** (Codex
  round-1 HIGH: `for_actor()`, `core/db.py:16-27`, is membership-based, not
  owner-only, and the codebase's membership machinery is not going away).
  The overlay is a new, explicit query path (a sibling function to
  `select_context_memories`, not a change to it) gated on three conditions,
  all checked directly against `Workspace.type`/`Workspace.owner`
  (`workspaces/models.py:20-30`), not inferred from membership: (a)
  `conversation.workspace.type == Workspace.Type.PERSONAL`, (b)
  `conversation.workspace.owner_id == actor.id`, (c) source workspaces are
  filtered to `owner_id == actor.id` as well — a workspace the owner
  merely has membership in in a future multi-membership scenario is not a
  valid overlay source. Only then does it read `type=work_log,
  sensitivity=normal, status=approved` memories from the owner's *other*
  owned workspaces. Company-workspace conversations get no overlay at all —
  one-way (personal reads outward), matching §O's "personal-assistant
  overlay," not a general merge.
- **Channel-bound requests are excluded from the overlay** (Codex round-1
  HIGH, round-2 HIGH on the mechanism): a PAT/CLI or Telegram request is
  deliberately bound to one workspace via
  `scoped_for_actor()`/`ChannelPrincipal.bound_workspace_id`
  (`core/channels.py:44-60`) precisely so a low-trust channel can never see
  outside its bound scope — an overlay wired unconditionally into
  `build_context()` would silently defeat that binding for a personal-
  workspace PAT conversation. This requires `build_context()` to actually
  know the channel, which it doesn't today (round-2 finding) — see the
  boundary section above for the required `channel` parameter and its two
  call sites. With that threaded through, the overlay function only runs
  for `channel == CHANNEL_WEB` (`core/channels.py:12`); any low-trust
  channel gets the existing same-workspace-only behavior, no exception.
- Every overlay-sourced memory in `render_memory_block` is tagged with its
  source workspace name so the model can tell an overlay read from a
  same-workspace one. `memories_used` (`assistant/models.py:101`) stays an
  unchanged ordered UUID-string list (Codex round-4 MEDIUM: it is not a
  structured shape today, so claiming it already carries provenance was
  wrong) — this branch adds a separate `Message.overlay_source_workspaces`
  JSONField (default `{}`, memory-id-string → source-workspace-id-string,
  populated only for overlay-sourced entries) so a future UI surface has
  the data to render "from Workspace X" without redefining the existing
  `memories_used` contract other frontend code already consumes. Codex
  round-5 MEDIUM: this needs its own migration and must be threaded
  through the full existing chain, not bolted on at persistence time
  only — `build_context()` returns it as a fifth value alongside
  `memory_ids`/`chunk_ids` (`assistant/context.py:55,143`),
  `build_gateway_stream()` passes it through (`assistant/services.py:244`),
  the SSE `done` event and `_persist_assistant_message()` both gain it
  (`assistant/services.py:363,394`), and `MessageSerializer`
  (`assistant/serializers.py:21`, which enumerates fields manually)
  exposes it. Tests cover both the empty-`{}` case (no overlay
  contribution) and a populated mapping.
- **Audit shape**: `AuditLog` carries one `workspace` FK
  (`audit/models.py:33`) and today's `memory.context_accessed`
  (`memory/context.py:69`) writes one row against one workspace. An overlay
  read spans multiple source workspaces, so this branch either (a) emits
  one `memory.context_accessed` row per source workspace touched (personal
  workspace's own read, plus one per overlay source actually read from —
  zero extra rows when the overlay contributes nothing), or (b) keeps one
  row against the personal workspace but adds a `source_workspace_id` per
  memory ID in its `metadata` mapping. Either way, "every read audited"
  (Codex round-1 MEDIUM) means the audit trail can answer "which workspace
  did this specific memory ID come from," not just "a read happened."
- Isolation test as the release gate (same discipline as MVP 3 branch 4's
  cross-workspace chunk test): assert a company-workspace conversation never
  sees another workspace's memories, a personal-workspace conversation
  never sees `private`-sensitivity or non-`work_log` rows from another
  workspace regardless of query construction, a personal-workspace
  conversation over a low-trust channel (PAT/Telegram) never gets the
  overlay, and a source workspace the actor merely has membership in
  (not ownership of) never contributes overlay rows.

## Out of scope (explicit)

Harness app and runners (Phase 2) · UI redesign beyond the firm dossier view
(Phase 4) · Graphiti/memory-graph (MASTER_PLAN §O: re-evaluate once during
this phase's design, reject for good if embeddings + consolidation cover the
need — record the outcome in this brief's closeout, don't silently drop it)
· device-trust identity (Phase 4) · voice (Phase 6) · any real `work_log`
producer (branch 2 builds the class only; Phase 2's harness is the first
producer).

## Test strategy

Role × endpoint permission tests for every touched surface (CLAUDE.md rule
7); FakeAdapter for all gateway paths (rule 5, embed calls included);
migration check (`makemigrations --check`); `make lint` + `make test` green
per branch; branch 1 additionally re-asserts the existing knowledge
dimension-guard test shape isn't accidentally weakened by the shared
pattern; branch 4's isolation test is a release gate, not an ordinary test —
a regression there is a workspace-scoping defect (ADR-001), escalate per the
orchestration protocol's CRITICAL/HIGH checkpoint rule.

## Risks

- **Embedding backfill cost.** Every existing `cloud_eligible()` `Memory`
  row (`status=approved AND sensitivity=normal` — never a private or
  unapproved one, per branch 1's eligibility rule above) gets embedded
  once; for a personal-scale dataset this is cheap, but the job must still
  be lease-fenced and resumable (mirrors knowledge branch 3) rather than a
  one-shot script, since Phase 2's harness will add work_log volume on top.
- **Provider/dimension swap limitation carried over from knowledge (MVP 3
  §1, accepted there — `knowledge/context.py:114-125`): a provider change
  makes older memories invisible to similarity ranking until re-embedded.
  Same accepted limitation here, not a new gap — no migration tool built.
- **`work_log` auto-approval scope creep.** ADR-008 k.4 is explicit: no
  other type/source combination may auto-approve, and this list does not
  grow without a new ADR. The single-service-function discipline in branch
  2 exists specifically to keep that a code-reviewable choke point.
- **Cross-workspace overlay becoming a general bypass.** Branch 4's read
  path must stay structurally narrow (one direction, one type, one
  sensitivity value) — if a future need wants more, that's a new decision,
  not a widening of this branch's query.

## Branch table

| # | Branch | Status |
|---|---|---|
| 1 | `feat/p1-memory-embeddings` | **done (2026-07-18)** — 3 rounds Codex code-review (P1 privacy race, P2 recency-bump, P2 Celery-limit derivation fixed; residual pre-embed race window documented as accepted, see `memory/tasks.py`) |
| 2 | `feat/p1-work-log` | **done (2026-07-19)** — 8 rounds Codex code-review (unusually dense: R1 restore guard missing, R2 creation atomicity, R3-4 durable `privacy_locked` marker + coverage for already-archived rows, R5 work_log choke-point via create/edit, R6 cascade TOCTOU unification + extraction bypass + reverse-retype, R7 approve/restore locked live-checks against concurrent privatization + frontend `MemoryType`/`MemorySource` gap, R8 clean). Two genuine two-thread concurrency tests added; an occasional Postgres deadlock between the reversed Memory/Conversation lock orders is an accepted, non-corrupting outcome (documented in `memory/services.py`) |
| 3 | `feat/p1-firm-dossier` | **done (2026-07-19)** — 3 rounds Codex code-review (R1 P2: dossier save state not reset on workspace switch; R2 P2/P3: `repos[].default_branch`/`services[].kind`/`deploy_targets[].notes` never reached the model context + a single long URL could exceed the block's char budget and get silently dropped instead of truncated; R3 clean). No schema changes — `assistant/context.py` gained the firm-profile block + `context_report` fields, `workspaces/[workspaceId]` BFF route and `/workspaces` dossier UI are new |
| 4 | `feat/p1-personal-overlay` | **done (2026-07-19)** — 4 rounds Codex code-review (R1 P1: `overlay_source_workspaces` leaked to a low-trust PAT/CLI read of the same in-scope conversation; R2 P1: nothing stopped a "personal" workspace from gaining a second member, making a fresh overlay a leak path to that fellow member; R3 P2: the R2 gate only stopped *new* overlay data, not historical rows already visible to a later-added web/JWT member; R4 clean). Final redaction rule in `ConversationMessageListView.get()`: `overlay_source_workspaces` is owner-only content, redacted for any low-trust request OR any reader who isn't `conversation.workspace.owner_id`, regardless of channel or when the data was persisted. This closes out Phase 1 — all four branches shipped |

Follow-up filed, not blocking branch 1: sharing one query embedding per
turn between memory and knowledge retrieval (currently two independent
`gateway.embed()` calls) — an idea from a parallel session's draft that
didn't make it into this branch's shipped design; see the spawned
background-task suggestion for the fix.

## Closeout (2026-09-27)

- **Graphiti / memory-graph re-evaluation (MASTER_PLAN §O) — REJECTED for
  now.** Embeddings-backed retrieval, the `work_log` class, the firm dossier
  and the personal overlay together cover the "what did we last do for firm
  X" need without a graph store. Revisit only if multi-hop relational
  queries become a real, observed need.
- **Carried forward (not shipped in Phase 1):**
  - Owner core memory block — `docs/VISION_JARVIS_2.md` §6 (Phase 1 row)
    named both an owner ("kişi") and a firm block; only the firm block
    shipped (branch 3).
  - Consolidation / distillation of memory (ADR-005 consequences; VISION
    §8 R5) — `work_log` now grows without curation beyond manual deletion.
  - Shared per-turn query embedding — `memory/context.py` and
    `knowledge/context.py` still each call `embed()` independently (the
    follow-up filed under the branch table above).
