# MVP 2 — Memory: Curated Memory + Context Injection + Portable Export

> Status: **done (2026-07-07)** — all 6 branches merged to main (scope approved by the user 2026-07-06;
> plan reviewed by Codex CLI, 6 rounds → APPROVE). Roadmap slot: personal kernel
> step 2 (shell/chat → **memory** → knowledge/RAG). Binding constraints:
> ADR-005, `docs/memory/obsidian-vault-strategy.md`, CLAUDE.md rules 1–9.
> Codex CLI reviewed drafts v1–v5 (CHANGES REQUESTED ×5, findings shrinking
> 16 → 11 → 8 → 5 → 2, no blockers left after round 4); v6 incorporates all
> five rounds. Key decision
> carried from v2: **Obsidian vault filesystem export is deferred** until the
> generic approval gate exists (MVP 5), because automated filesystem writes
> without an approval gate violate CLAUDE.md rule 4 and the vault strategy's
> own "bulk changes require approval" rule. ADR-005's "export everything"
> requirement is met in MVP 2 by a user-requested portable download export
> (Codex round 2 confirmed this is sound: a scoped read response, not an agent
> filesystem write or third-party send).

## Goal and user value

Friday starts remembering: durable facts, preferences and episodes are distilled
from conversations (as `pending_approval` suggestions), curated by the user, and
injected back into chat context so answers stop being stateless. Everything is
workspace-scoped, auditable, editable, forgettable and exportable — memory is a
trust feature before it is an intelligence feature.

## Scope

### 1. `memory` kernel app — model + governance

- `Memory` (extends `WorkspaceScopedModel`): `memory_type` (semantic / episodic
  / preference / procedural), `sensitivity` (normal / private), `status`
  (pending_approval / approved / rejected / archived), `source` (ai_suggested /
  user_created), `title`, `content`, `created_by` (provenance; it also feeds
  the single member-authorization predicate defined below — it is not a
  general ACL owner field), `source_conversation` (nullable FK), `fingerprint`
  (dedup; unique per workspace across **all rows including rejected** —
  otherwise a rejected suggestion immediately resurfaces; the slot frees only
  on hard delete).
- Scope is **derived from the workspace row** (personal workspace = personal
  scope); no separate scope field. Agent scope is documented as future work
  only — no schema reservation, so no invalid agent-less rows can exist.
- Server-controlled fields: `status`, `source`, `created_by` and
  `source_conversation` are never accepted by serializers. The target
  `workspace` is necessarily declared by the request (as in conversation
  create) but is only ever resolved through `for_actor()` before any service
  runs — never trusted raw, and never taken from LLM output.
  `source_conversation` must belong to the same workspace, enforced as a
  model-level invariant on every write path (+ test).
- **State machine** (enforced in services, illegal transitions → 400):
  `pending_approval → approved | rejected`; `approved → archived`;
  `archived → approved` (explicit restore / re-remember). **Edit is a
  separate action, not a transition** — it may mutate the fields of an
  `approved` (or `pending_approval`) row without changing status. Repeating a
  transition the row is already in (e.g. approving an approved row) is an
  idempotent no-op.
  User-created memories are born `approved`. **Pending suggestions are
  curatable before approval**: the approve operation accepts curated fields
  (title, content, type, and — critically — `sensitivity`, since extraction
  defaults to `normal`) applied atomically with the transition; pending rows
  can also be edited in place. Hard-delete is allowed only for
  `pending_approval`/`rejected` rows (never-approved suggestions); forgetting
  an approved memory archives it.
- **Role policy matrix** — applies identically to personal and company
  workspaces (a personal workspace's owner holds the owner role; if the
  schema ever admits other members there, the same matrix governs them —
  no personal-workspace special case). "Own user-created rows" is the exact
  predicate `source=user_created AND created_by=actor`; AI-suggested rows are
  admin-managed at every stage (including after approval):

  | action | owner/admin | member | viewer |
  |---|---|---|---|
  | read/list | ✓ | ✓ | ✓ |
  | create (user_created) | ✓ | ✓ | – |
  | edit content | ✓ | own user-created rows | – |
  | approve/reject/restore | ✓ | – | – |
  | forget (archive) | ✓ | own user-created rows | – |
  | hard-delete never-approved | ✓ | – | – |
  | portable export | ✓ (what they can read) | ✓ (what they can read) | ✓ (what they can read) |

- **Access auditing (ADR-005: every memory access auditable)**: mutation events
  (`memory.created/updated/approved/rejected/restored/forgotten/deleted`) plus batched
  read events — `memory.read` (list/detail API: ids + count, **no content**),
  `memory.context_accessed` (per gateway call: injected ids), `memory.exported`
  (portable export: count + statuses). Audit metadata never contains
  title/content (private or not); memory **identifiers, statuses and
  classifications are allowed** in audit metadata — auditing is internal
  bookkeeping, not a model payload, and ADR-005 requires it.
- **Portable export** (ADR-005 "users can export everything"): a
  user-requested, synchronous download (JSON) available to **every role**, of
  exactly the rows the actor can read via `for_actor` in that workspace —
  every status, including `private` and `archived`. Export scope ≡ read scope
  by definition (it is the same queryset), so it cannot widen access; it is a
  scoped read returned in the HTTP response — not an external send or
  filesystem write — so it does not require the approval gate. Audited as
  `memory.exported`; responses (backend and BFF) carry
  `Cache-Control: no-store` since exports include private content.

### 2. Context builder v1 — memory injection

- `build_context()` gains an `actor` parameter and injects **only memories of
  `conversation.workspace`** via `Memory.objects.for_actor(...)`. Personal
  memories therefore apply only in the personal workspace. **No personal
  overlay in company workspaces** — company conversations are visible to all
  members, so a personally-influenced reply would leak personal data into
  company scope. Overlay is deferred until conversations have per-user
  visibility and a disclosure policy.
- Selection: `status=approved`, `sensitivity=normal` only, deterministic order
  (type priority: preference > semantic > procedural > episodic, then
  `-updated_at`, id tiebreak), hard caps (count + character budget, budget
  accounting specified in the implementation).
- **Injection-safety framing**: memories are serialized as delimited,
  length-bounded *data* blocks; the system prompt states they are factual
  context, not instructions. Content is escaped/fenced.
- **Private negative contract (central)**: one cloud-eligibility
  service/queryset (`Memory.objects.cloud_eligible()`) is the only path by
  which **`Memory` rows** enter any gateway payload. The precise guarantee:
  *private `Memory` **title/content** are never read from or serialized from
  a `Memory` record into any gateway payload, `context_report`,
  `memories_used`, or audit metadata; identifiers, statuses and
  classifications may appear in audit metadata only* (consistent with the
  access-auditing rule in §1). Conversation history is sent verbatim by
  design (today's builder
  already does), so text a user typed that happens to duplicate a private
  memory is out of this guarantee's scope — a "private conversation" flag goes
  to MASTER_PLAN Open Decisions. Tests capture the exact gateway input and
  assert Memory-sourced private title/content appear nowhere.
- **Sanitized `memories_used` contract** (backend half of the UI feature): the
  injected memory ids are persisted on the assistant `Message` row at write
  time as an **immutable, ordered UUID list (default `[]`)**, exposed in the
  message serializer and in the SSE `done` event.
  `ModelUsage.context_report` itself is never exposed to the frontend. The
  indicator survives reload. Semantics: "memories supplied to the model" — the
  UI labels it *memory context*, not proof the model used them.

### 3. Extraction job model + structured contract (no dispatch yet)

- `MemoryExtractionJob` (workspace-scoped): unique `(conversation,
  through_message)` (there is no separate `target_message` field — the range
  end *is* the target), status (pending / running / succeeded / failed), error
  info, retry count, lease fields (`claim_token`, `lease_expires_at` — used
  by branch 4), and a **non-overlapping range** `(after_message,
  through_message]`. Range allocation: `Conversation` gains an authoritative
  **extraction cursor** (`last_extracted_message` FK + its `created_at`).
  **Lock ordering is fixed**: the persist step locks the `Conversation` row
  (`select_for_update`) *first*, and only then, inside the same transaction,
  creates the assistant `Message`, creates the job with range `(cursor,
  new_message]`, and advances the cursor. Locking before insertion means two
  concurrent persists serialize in insertion order — the cursor can never
  move backward and a range can never be reversed. Ranges are contiguous and
  non-overlapping by construction, so workers later claim jobs independently
  without a per-conversation lock, and "latest job" needs no fragile ordering
  query.
- **Structured output contract**: versioned JSON schema (list of candidates:
  type, title, content, with count and per-field size limits). Strict
  validation; malformed or oversized output marks the job `failed` — a
  terminal, non-retryable outcome (no partial writes) — never a crash, never
  unvalidated rows. Retryable provider failures instead return the job to
  `pending` with an incremented retry count (bounded).
- Extraction prompt: its own `PromptTemplate` + `PromptVersion`
  (`friday_memory_extraction`, seeded in `seed_ai_defaults`),
  `purpose="memory.extraction"`, ModelUsage recorded (gateway rules apply).
  Prompt-version lineage tested.
- FakeAdapter today receives only `messages`/`params` and returns plain chat
  text: the gateway's **internal adapter invocation context** is extended to
  carry `purpose` (an `ai_core`-internal API change for all adapters — not a
  fake-only parameter leaking into real provider calls), and the fake adapter
  returns a deterministic candidate-JSON fixture for `memory.extraction`.
- This branch ships the model, allocation, parser and prompt **without
  enqueueing anything** — dispatch lands with the task implementation in
  branch 4, so merged `main` never publishes an unregistered task.

### 4. Extraction worker — dispatch, concurrency + idempotency

- Celery task (Celery app config, autodiscovery, Redis broker — the compose
  worker container exists; this is its first real workload).
  `CELERY_TASK_ALWAYS_EAGER` becomes **environment-backed** (default off;
  backend tests and the e2e CI job set it true, together with
  `CELERY_TASK_EAGER_PROPAGATES=true` so eager task failures surface in
  tests). Dispatch: `transaction.on_commit` after the job-creating
  transaction; the publish call is wrapped so a **broker publication failure**
  (specifically — not arbitrary eager task exceptions) never propagates into
  the SSE stream (the reply must not fail because Redis is down) — the job
  stays `pending` and recovery picks it up.
- `on_commit` narrows but does not close the commit/publish crash window
  (process death after commit, before publish). This is an accepted,
  **recoverable** window: a recovery scan re-enqueues stale `pending` jobs
  *and* abandoned `running` jobs whose lease expired, distinguishing retryable
  failures from terminal schema failures. Scheduling — committed design, not
  an open option: the scan is a Celery periodic task and the compose worker
  runs with **embedded beat (`-B`)**. This is safe while there is exactly one
  worker container (the current and foreseeable dev/CI topology); scaling to
  multiple workers requires splitting out a dedicated beat service — recorded
  as a constraint comment on the compose service. The scan also ships as a
  manually runnable management command.
- **Lease fencing**: jobs carry `claim_token` (per-attempt UUID) and
  `lease_expires_at`. Claim: `select_for_update(skip_locked)` + status/lease
  guard, set token + lease, commit. The gateway call runs outside any
  transaction; **every post-claim write — candidate insertion, terminal
  state, and equally the retryable-failure path (back to `pending`,
  `retry_count`, error fields) — re-locks the job and verifies
  `status=running AND claim_token=<mine>` first**. Precisely: every
  job/candidate/audit **state mutation** is fenced; `ModelUsage` is the one
  intentional exception, as an attempt-level cost record written by the
  gateway itself. A stale worker that outlived its lease finds the token
  rotated and discards whatever it was about to write, success or failure
  alike (its ModelUsage row stands as the cost record of the wasted attempt).
  Task soft/hard time limits are set below the lease duration so a hung task
  cannot straddle a takeover.
- **Retryability contract (ai_core change, lands in branch 3)**:
  `GatewayError` gains a `retryable` flag classified at the adapter/gateway
  boundary — timeouts, 5xx, rate limits, `no_provider` → retryable;
  auth/permanent 4xx → non-retryable; schema-validation failure of the
  extractor output → terminal `failed` regardless. Retryable failures
  increment `retry_count` and return the job to `pending` **until
  `retry_count` reaches `MAX_EXTRACTION_RETRIES`; the limit-reaching attempt
  transitions the job atomically to `failed` instead** (boundary test
  required) — a job can never stay indefinitely recoverable. Non-retryable →
  `failed` immediately.
- Task body: claim (as above); process the job's fixed `(created_at,
  id)`-ordered range; create candidates (born `pending_approval`,
  `source=ai_suggested`) and write terminal state in one fenced transaction.
  Redelivery of a succeeded job is a no-op; ranges never overlap by
  construction (branch 3); fingerprint conflicts are absorbed with
  `bulk_create(ignore_conflicts=True)`, never an `IntegrityError` failing the
  whole transaction. Fingerprint = cheap normalization
  (lowercase/whitespace/hash; no embeddings, that is MVP 3).
- Concurrency tests use genuinely concurrent transactions (two connections),
  not merely sequential eager tasks — both for **allocation** (two
  simultaneous job creations against one conversation yield exactly adjacent,
  non-overlapping ranges) and for **claim/fencing** (expired-lease takeover
  where the stale worker finishes last and must discard).
- Extraction input honours the private negative contract as scoped in §2
  (Memory-sourced private content never enters the extraction prompt;
  conversation messages are sent verbatim by design).

### 5. Frontend memory UI

- Memory page in the shell: pending-approval inbox (approve/reject), memory
  list with status/type filters, create, edit, forget, portable-export
  download. BFF routes follow the branch-5 pattern (HttpOnly cookies, CSRF
  origin + content-type checks, no token in JS).
- Chat: "memory context" indicator (labelled as context supplied, not usage
  proof) driven by the sanitized `memories_used` field (SSE `done` + reloaded
  history).

### 6. E2E + docs

- Playwright smoke extension (FakeAdapter backend). The e2e job stays without
  a Redis broker/worker, **so it must run the backend with
  `CELERY_TASK_ALWAYS_EAGER=true`** (env-backed by branch 4) — otherwise the
  post-reply `on_commit` dispatch would try to contact a missing broker.
  Dispatch failure is non-propagating by design (branch 4), but the e2e
  environment should not exercise that failure path on every message.
  Extraction correctness is pytest's job; the smoke asserts the curated path:
  seeded pending memory → approve in UI → memory list shows it → next chat
  turn's `memories_used` includes it.
- Docs: MASTER_PLAN section F fill-in + section N status fix (MVP 1 still says
  "in progress"), Open Decisions entry for a "private conversation" flag,
  stale settings comment fix (`base.py` claims vault export arrives in MVP 2),
  this brief's status updates per branch.

## Out of scope (explicit)

- **Obsidian vault filesystem export** — deferred until the generic approval
  gate (MVP 5) per CLAUDE.md rule 4 + vault strategy "bulk changes require
  approval". When it lands it must include forget/edit reconciliation
  (tombstone or delete of mirrored files), the full frontmatter standard
  (incl. `project`), `OBSIDIAN_VAULT_PATH` (existing setting name), symlink-safe
  path jail, atomic writes, and a worker bind mount. Recorded here so the
  requirements are not rediscovered.
- Vault ingestion (vault → Friday) and echo-chamber guard (MVP 3+).
- Embeddings / vector retrieval / semantic search (MVP 3); dedup here is
  textual only.
- Memory consolidation/merging beyond the fingerprint guard.
- Personal-memory overlay in company workspaces (needs per-user conversation
  visibility + disclosure policy).
- Agent-scoped memory (no agent runtime; no schema reservation either).
- Per-provider private routing (private is never injected, full stop).
- Generic approval gate (MVP 5) — memory approve/reject is purpose-built
  curation, not the tool approval gate.

## Branch plan

| # | Branch | Contents |
|---|---|---|
| 1 | `feat/mvp2-memory-foundation` — **done (2026-07-06)**. Codex review: round 1 CHANGES REQUESTED (admin exposure, stale-instance transitions, +6), round 2 CHANGES REQUESTED (actor-scoped locking, fingerprint-race 500), round 3 APPROVE. Notable deviations from §1 as planned: Memory deliberately has **no admin registration** (unscoped/unaudited private-content access); mutating services lock actor-scoped (`select_for_update(of=("self",))` + membership join) and authorize against the locked row; `memory.read` audit chunks large reads so every id is recorded. | memory app, model + state machine + role matrix, CRUD/curation API, access-audit events, portable export, permission tests |
| 2 | `feat/mvp2-memory-context` — **done (2026-07-06)**. Codex review: round 1 CHANGES REQUESTED (case-sensitive fence bypass, empty selections not audited, coverage gaps), round 2 CHANGES REQUESTED (CR-split fence bypass: neutralizing before `\r` removal let `</memo\rry>` reassemble into a literal closing tag), round 3 APPROVE. Notable deviations: `_neutralize()` must run LAST, after all other text normalization, or a split marker can be reassembled by a later step — this ordering constraint is load-bearing, not stylistic; `memory.context_accessed` is recorded on every call including empty selections. | context builder v1 (workspace-only injection, caps, injection-safe framing), cloud-eligibility service, `memories_used` persistence + SSE/serializer contract |
| 3 | `feat/mvp2-extraction-jobs` — **done (2026-07-06)**. Codex review: round 1 CHANGES REQUESTED (parser type-check ordering letting a non-hashable `type` escape as raw `TypeError`; `SET_NULL`/`CASCADE` on cursor/range-end message FKs letting a deleted message silently reset the cursor and produce overlapping ranges), round 2 CHANGES REQUESTED (the round-1 fix used `PROTECT`, which also blocked the legitimate cascading deletion of a whole Conversation), round 3 APPROVE. Notable deviations: message FKs referenced by the extraction cursor or a job's range end use `on_delete=RESTRICT`, not `PROTECT` — RESTRICT still fails a standalone message delete but permits it when the FK holder is part of the same cascading deletion (e.g. deleting the whole Conversation); the JSON schema parser rejects extra fields at both levels and explicitly rejects `bool` as the integer schema version (`True == 1` in Python). | MemoryExtractionJob model + conversation extraction cursor, locked contiguous range allocation, transactional creation (no dispatch), JSON schema + strict parser, `GatewayError.retryable` contract, extraction prompt seed, purpose-aware adapter context + fake fixture |
| 4 | `feat/mvp2-extraction-worker` — **done (2026-07-06)**. Codex review: round 1 CHANGES REQUESTED (dispatch swallowed eager task-body exceptions; error_message not secret-safe on the broad-exception and invalid-type paths; task time limits below the gateway's real worst-case duration; retry-exhaustion off-by-one; cross-workspace job creation not rejected; e2e CI + a pre-existing branch-3 test not eager-isolated), round 2 APPROVE. Notable deviations: `CLAIM_LEASE_SECONDS=420` (not 300) — sized to the gateway's actual worst case (2 providers × up to 3 attempts × 60s ≈ 360s), not a single attempt; `dispatch_extraction_job` branches on `CELERY_TASK_ALWAYS_EAGER` explicitly rather than blanket-catching `.delay()`, so eager-mode task exceptions still propagate under `CELERY_TASK_EAGER_PROPAGATES`; `create_extraction_job` validates `through_message` belongs to the given conversation/workspace (`ValueError` otherwise) — a gap the original design left open. | Celery wiring (first workload, worker `-B` embedded beat), `on_commit` non-propagating dispatch, fenced claim/finalize task (claim_token + lease), recovery scan (pending + expired running leases, retryable vs terminal), fingerprint `ignore_conflicts` dedup, env-backed eager mode, concurrent allocation + takeover tests |
| 5 | `feat/mvp2-memory-ui` — **done (2026-07-06)**. Codex review: round 1 CHANGES REQUESTED (2 High: unvalidated `memoryId` route segment allowed path injection into the upstream bearer-authenticated backendFetch call; MemoryCreateForm draft state crossed workspace boundaries on a switch — plus a Medium test-gap finding and a Low `memories_used` validation finding), round 2 APPROVE. Notable deviations: `frontend/src/lib/uuid.ts` validates every dynamic `memoryId` segment as a UUID before interpolation, on every memory-detail/action route; `MemoryCreateForm` is keyed by workspace id and `MemoryView` closes its `creating` state on a workspace switch, with `handleCreated` also discarding a stale cross-workspace response. Manually verified end-to-end against the live backend/FakeAdapter (the dev Postgres volume needed `migrate` + `seed_ai_defaults` brought forward, which had lagged since branches 1-4 landed — an environment gap, not a code defect). | Next.js memory page, BFF routes, pending inbox (curate-on-approve incl. sensitivity), portable-export download, chat memory-context indicator, Vitest coverage |
| 6 | `feat/mvp2-e2e-docs` — **done (2026-07-07)**. Codex review: round 1 CHANGES REQUESTED (the memory-polling step assumes the extraction job gets processed, but the documented no-worker `runserver` e2e fallback has neither a worker nor eager mode), round 2 CHANGES REQUESTED (navigating to Memory right after the reply text renders can race the SSE `done` event — aborting the stream persists the message `partial`, which never dispatches an extraction job), round 3 APPROVE. Notable deviations: the smoke test now waits for the composer to leave streaming state (`Stop` -> `Send`) before leaving chat, and `e2e/global-setup.ts`'s error message documents that the no-worker fallback path needs `CELERY_TASK_ALWAYS_EAGER=true`; verified manually against both a real worker+Redis stack and a true eager/no-broker backend (the e2e CI job's actual configuration). | Playwright smoke extension (curated-memory happy path: seeded pending suggestion -> approve -> `memories_used` on the next reply), MASTER_PLAN F + N + Open Decisions, settings-comment fix, brief status updates |

Config enumerations to touch: branch 1 — installed apps, URLs, pytest paths,
import-linter roots/contracts (`memory` is a kernel app: may import core/audit,
never `apps.modules.*`); branch 4 — env-backed `CELERY_TASK_ALWAYS_EAGER` +
`CELERY_TASK_EAGER_PROPAGATES` in settings, `.env.example`, backend CI env,
e2e CI env, compose worker command (`-B`, single-worker constraint comment).

Per-branch flow unchanged from MVP 1: implement → tests+ruff+CI green → Codex
CLI review (`--base main`) → fix → APPROVE → merge to main (no extra user
confirmation per the 2026-07-05 protocol).

## Test strategy

- pytest: `for_actor` leakage matrix, state-machine transitions (legal /
  illegal / idempotent-repeat, restore, curate-on-approve), role × endpoint
  permission matrix incl. the member `source=user_created AND
  created_by=actor` predicate, server-controlled serializer fields,
  same-workspace provenance, access-audit events (read/context/export, no
  content in metadata), portable export completeness (all statuses + private,
  export ≡ read scope, `no-store` header), context injection caps +
  deterministic ordering + injection framing, **exact gateway payload capture
  proving Memory-sourced private absence**, `memories_used` persistence + SSE
  `done` field, job uniqueness + locked contiguous range allocation +
  transactional creation, `on_commit` dispatch + broker-publication-failure
  non-propagation, recovery scan (stale pending + expired running lease) with
  retryable / non-retryable / schema-terminal classification, malformed /
  oversized extractor output → terminal failed job, redelivery no-op,
  **two-connection concurrency tests for both allocation (adjacent
  non-overlapping ranges) and expired-lease takeover — covering a stale
  worker finishing last with a success *and* with a retryable failure (both
  must discard)**, gateway-boundary retryability mapping
  (timeout/5xx/rate-limit/no-provider → retryable; auth/permanent-4xx →
  non-retryable), eager-mode negative pair (arbitrary task exception
  propagates; broker publication error is swallowed), state-transition audit
  events incl. `memory.restored`, fingerprint dedup incl. rejected-row
  retention + `ignore_conflicts`, retry-exhaustion boundary (limit-reaching
  attempt → atomic `failed`), prompt-version lineage, ModelUsage rows on
  every **gateway-invoking attempt** (a succeeded-job redelivery no-op makes
  no gateway call and records nothing).
- Vitest/RTL: memory page states, approve/reject (incl. curated fields) flows,
  BFF CSRF/cookie contracts + `no-store` on export, memory-context indicator
  (stream + reload).
- Playwright: the curated happy path (scope 6). No real-LLM tests outside
  nightly.

## Risks / notes

- Branch 2 and 3 both touch `assistant`; land 2 first (context is read-only)
  so 3's transactional hook rebases cleanly. Branch 3 merges with dispatch
  deliberately absent: jobs are created `pending` and sit inert (nothing
  publishes an unregistered task); branch 4's recovery scan naturally drains
  that backlog once the worker exists.
- Extraction quality with the fake fixture is untestable by definition — tests
  assert plumbing (statuses, watermarks, usage rows), not semantic quality.
- The extraction prompt will see raw conversation text; ADR-005's `private`
  applies to memories, not messages — a future "private conversation" flag is
  out of scope here and worth an Open Decision entry in MASTER_PLAN.
