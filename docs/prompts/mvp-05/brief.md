# MVP 5 — Tools & Approval Gate: Generic Gate + Obsidian Vault Export

> Status: **done (2026-07-10)** — all 5 branches merged to main (scope
> approved by the user 2026-07-08 with 3
> amendments ((1) requester ≠ decider is a hard model-level rule, not a
> self-approval allowance — a narrowly-scoped, default-off
> `DEV_ALLOW_SELF_APPROVAL` flag is the only, explicitly temporary
> exception, dev/test use only; (2) expiry is enforced at decision time as
> well as by the periodic sweep; (3) the Obsidian-export branch validates
> the vault root before anything runs, on top of the per-operation path
> jail); plan reviewed by Codex CLI, 3 rounds → APPROVE (round 1: 9
> findings incl. 4 HIGH — missing actor-scoped row locking, a
> non-enforced dev flag, a TOCTOU-vulnerable path check, an overly-broad
> prune; round 2: 3 more HIGH — incomplete path-jail scope, a web/worker
> mount contradiction in vault-root validation, a missing durable-before-
> dispatch guarantee; round 3 APPROVE, no new findings)). Roadmap slot:
> personal kernel step 5 (shell/chat → memory → knowledge/RAG → tasks →
> **tools/approvals** → router → agents). Binding constraints:
> ADR-004 (approval gate rules — "ships in MVP 5, before any agent runtime"),
> ADR-005 (Obsidian vault mirror), CLAUDE.md rule 4, `docs/memory/
> obsidian-vault-strategy.md`. This MVP ships two new kernel apps (`approvals`,
> `tools`, both reserved names per ADR-002) plus the first concrete gated
> tool: the Obsidian vault filesystem export deferred from MVP 2
> (`docs/prompts/mvp-02/brief.md` "Out of scope" — "When it lands it must
> include forget/edit reconciliation..., the full frontmatter standard...,
> `OBSIDIAN_VAULT_PATH`..., symlink-safe path jail, atomic writes, and a
> worker bind mount"). No agent runtime exists yet, so every gated action in
> this MVP is human-requested, not agent-requested — the gate is proven out
> end-to-end (request → approve → async execute → audit) before MVP 7 gives
> an agent the ability to request one.

## Goal and user value

Friday gets a real write-class action for the first time: exporting curated
memories to the user's local Obsidian vault as human-readable, frontmatter-
tagged markdown files, kept in sync (edits/forgets reconcile, not just
one-shot dumps). The action is never silent — every filesystem write goes
through an explicit request → approve → execute flow, audited end to end,
establishing the approval-gate pattern every future write/delete/deploy/
external-send action (tools, agents, integrations) will reuse.

## Scope

### 1. `approvals` kernel app — `ApprovalRequest` model + decision API

- `ApprovalRequest` (extends `WorkspaceScopedModel`): `tool_name` (which
  registered tool this gates — see §2), `payload` (JSON, whatever the tool
  needs to run, e.g. `{}` for a workspace-wide vault export), `status`
  (`pending` / `approved` / `rejected` / `expired`), `requested_by`,
  `decided_by` (nullable), `decided_at` (nullable), `created_at`/
  `updated_at` (inherited). No `execution_status` field here — the actual
  async run outcome is tracked on the tool's own execution record (§2), so
  `ApprovalRequest` stays a pure decision record, not a job-status blend.
- **State machine** (enforced in services, illegal transitions → 400):
  `pending → approved | rejected`. There is no restore/reopen transition —
  a decided request is terminal; requesting the same action again creates a
  new `ApprovalRequest` row. `pending → expired` is primarily set by a
  scheduled sweep (a Celery periodic task, mirroring memory's/knowledge's
  recovery-scan pattern) once a request has sat `pending` past
  `APPROVAL_REQUEST_TTL_HOURS` (setting, **default 24**) — but the sweep is
  a best-effort background process, not the only enforcement point: **every
  approve/reject call itself re-checks `created_at` against the TTL before
  deciding**, and transitions a stale-but-still-`pending` request to
  `expired` synchronously instead of honoring the decision, if the sweep
  simply hasn't run yet (worker down, delayed beat tick, etc.). This closes
  the "a months-old pending request nobody remembered gets approved by
  accident" scenario without depending on the sweep's timing — the sweep
  keeps the *list* clean between decisions, the decision endpoint itself is
  the actual enforcement backstop.
- **Actor-scoped row locking (Codex plan-review round-1 HIGH finding)**:
  approve, reject, AND the expiry sweep's per-row transition all re-fetch
  the `ApprovalRequest` under lock, actor/system-scoped, inside the
  caller's transaction — the exact `_locked()` shape `memory.services` and
  `tasks.services` already ship (`ApprovalRequest.objects.filter(pk=...,
  workspace__members__user=actor).select_for_update(of=("self",))`, never
  `for_actor()` directly for the same `DISTINCT`-vs-`FOR UPDATE`
  incompatibility reason). Without this, a concurrent approve-vs-reject (or
  approve-vs-the-sweep-expiring-it) race could authorize a decision against
  a stale `pending` read. Two-connection concurrency tests cover both races
  explicitly (§ Test strategy).
- **Role policy**: *requesting* a gated action requires the same
  `_WRITER_ROLES` (owner/admin/member) every other kernel app's create path
  uses; *deciding* (approve/reject) requires `_CURATOR_ROLES` (owner/admin)
  — identical shape to memory's approve/reject, tasks' role matrix, etc.
  **The requester and the decider must be different actors — enforced at
  the model level (`ApprovalRequest.clean()`/a service-level check before
  every decide, not just a serializer nicety), not merely a role check.**
  This is ADR-004's core premise (the entity that produces an action is not
  the entity that approves it) and is the whole reason the gate has any
  value once an agent — not a human — becomes the requester in a later
  MVP; loosening this later would be materially harder than building it
  right from the start. **Single-developer/single-user dev reality carve-
  out**: an explicit, narrowly-scoped `DEV_ALLOW_SELF_APPROVAL` setting
  (boolean, env-backed, **default `False`**) lets the same-actor check be
  bypassed — intended for local/dev use only, never set in a real
  deployment, and flagged in this brief as a **temporary exception**, not a
  design endorsement of self-approval. **A `.env.example` comment is not
  enforcement (Codex plan-review round-1 HIGH finding)**: `approvals.apps.
  ApprovalsConfig.ready()` asserts at process startup that
  `DEV_ALLOW_SELF_APPROVAL` is never `True` while `settings.DEBUG` is
  `False`, raising `django.core.exceptions.ImproperlyConfigured` (the
  process refuses to boot at all, not just "misbehaves silently") — the
  flag is structurally incapable of doing anything in a `DEBUG=False`
  deployment (`config/settings/prod.py` sets `DEBUG = False`), not merely
  discouraged by documentation.
- **API**: `POST /api/v1/approvals/requests` (create — `workspace` is a
  required field resolved through `Workspace.objects.for_actor()` before
  the service runs, the same never-trust-raw pattern every other kernel
  app's create path uses; server resolves `tool_name`'s validity and
  gated-ness against the registry in §2, requesting a non-existent or
  non-gated tool name is a 400, not silently accepted), `GET
  /api/v1/approvals/requests` (list, `?status=` filter — readable by every
  role, same read-open shape memory/tasks use), `POST
  /api/v1/approvals/requests/<uuid:id>/approve` (decide + dispatch — see
  §2 for what "dispatch" means), `POST .../reject`. **`payload` is scoping-
  inert by construction (Codex plan-review round-1 MEDIUM finding)**: a
  tool's execution reads scope exclusively from `ApprovalRequest.workspace`
  (the row this request lives on), never from any key inside `payload` —
  `payload` carries only tool-specific arguments, and a forged
  `{"workspace": "<other-workspace-id>"}` key inside it is simply ignored,
  never interpreted (tested explicitly).
- **Payload confidentiality on read (Codex plan-review round-1 MEDIUM
  finding)**: the list/detail serializer never returns the raw `payload`
  JSON by default — a tool may register a `describe_payload()` function in
  its `ToolSpec` (§2) returning a short, tool-authored safe summary string
  for display, but the field itself is never serialized verbatim. This
  MVP's only tool (`vault_export`) has an empty payload in practice, so
  this has no visible effect yet, but the contract is written now so a
  future tool with real arguments doesn't silently start leaking them.
- **Audit**: `approval.requested` / `approval.approved` / `approval.rejected`
  / `approval.expired` mutation events via `record_audit()` (memory/tasks
  convention — no model mixin exists in this codebase). Metadata carries
  ids, `tool_name`, and status only — the `payload` field is NOT
  unconditionally safe to log verbatim (a future tool's payload could carry
  something sensitive), so audit metadata never includes it.
- Permission tests: full role × endpoint matrix, illegal transitions,
  idempotent-repeat semantics do NOT apply here (unlike memory/tasks) —
  approve/reject are one-shot terminal decisions on a specific request, not
  idempotent state toggles, so a second approve/reject call on an
  already-decided request is a 400 (`InvalidTransition`), never a silent
  no-op. Expiry-sweep test (two-connection or time-mocked, following
  memory's/knowledge's recovery-scan test pattern).

### 2. `tools` kernel app — registry + dispatch wiring (no execution model yet)

**Sequencing note (Codex plan-review round-1 MEDIUM finding, resolved by
re-scoping this branch)**: the original draft had this branch also owning
lease-fenced execution/recovery-scan *tests*, but with no generic
`ToolExecution` model (rejected, see below) and `VaultExportRun` not
existing until §3/branch 3, there was nothing concrete to lease-fence yet.
This branch is now scoped to the registry and the dispatch *mechanism*
only; the actual lease-fenced claim/finalize pattern, its model, and its
tests all move to branch 3 alongside `VaultExportRun` — the model whose
rows actually get fenced.

- **No `Tool` database model in this MVP.** With exactly one concrete tool
  (§3) and no agent runtime to introspect a dynamic registry, a Python-level
  registry (`tools/registry.py`: a plain dict of `tool_name -> ToolSpec`,
  where `ToolSpec` carries `gated: bool` and a reference to the Celery task
  to dispatch) is the right amount of structure — a DB-backed
  `ToolDefinition` model is exactly the kind of speculative generality
  CLAUDE.md's simplicity rule warns against until a second tool or
  agent-driven tool selection actually needs it (MVP 7).
- **Dispatch mechanism, durable-before-dispatch (Codex plan-review round-2
  HIGH finding)**: `ToolSpec` carries three things, not two: `gated: bool`,
  a `create_execution(approval_request) -> execution_id` callable, and the
  Celery task to dispatch. Approving a request (§1) — **inside the same
  transaction as the decision itself** — calls the registered tool's
  `create_execution()` to create its pending execution row (whatever shape
  that tool needs), then dispatches the Celery task with that execution's
  id over `transaction.on_commit` (non-propagating broker-publish-failure
  handling, same as memory/knowledge). Creating the row *before* `on_commit`
  — not inside the dispatched task itself — is the whole point: if the
  broker publish fails after commit, the recovery scan still finds a real
  `pending` row to reclaim, exactly like `KnowledgeDocument`'s/
  `MemoryExtractionJob`'s pattern; if the task instead created its own row,
  a failed publish would leave nothing for recovery to find at all. This
  branch proves the *transactional-ordering* invariant itself — `create_
  execution()` is called and commits before `on_commit` fires — using a
  trivial stub `ToolSpec` in tests whose `create_execution()` is a mock
  returning a sentinel id (no real backing table needed to prove call
  ordering); branch 3 then proves the same invariant with real
  `VaultExportRun` rows (a broker-publish-failure test asserting a
  `pending` row still exists after). This mirrors exactly how memory's
  branch 3 created extraction jobs with dispatch deliberately absent,
  verified via a mock, before branch 4 gave it a real worker — just one
  level more granular, since here the *creation* and the *dispatch* are
  the two moments being kept in the right order, not creation and dispatch
  as a single later addition. This keeps slow/blocking I/O (a filesystem
  write to a mounted vault path) off the request/response cycle and off
  the web container entirely once a real tool exists — it runs in the
  worker container, which is where the vault bind mount lives (§3).
- **No `Tool` database model and no generic `ToolExecution` model in this
  MVP** — kept tool-specific (`VaultExportRun` in branch 3) rather than a
  premature generic shape, since its result shape (files written/updated/
  removed) is genuinely tool-specific; a generic shape can be extracted
  once a second tool exists and the commonality is real, not assumed.

### 3. Obsidian vault export — the first concrete gated tool

- **`VaultExportRun` — the lease-fenced execution record** (moved here from
  §2 per the round-1 sequencing fix): one row per approved
  `ApprovalRequest`, status `pending -> running -> succeeded | failed`,
  `retry_count`, sanitized error fields (see below), `claim_token`/
  `lease_expires_at` — the exact claim/finalize fencing
  `MemoryExtractionJob`/`KnowledgeDocument` already ship (`select_for_update
  (skip_locked=True)` claim, re-lock-and-verify-token before every
  post-claim write). Recovery scan (Celery periodic task, embedded beat,
  mirrors the existing two): stale `pending`/expired-lease `running` runs
  get reclaimed, retryable vs terminal failure classified the same way
  memory's/knowledge's workers already do.
- **Sanitized error fields (Codex plan-review round-1 MEDIUM finding)**:
  filesystem exceptions routinely embed absolute paths and the host
  username in their message text — persisting one verbatim would leak
  `OBSIDIAN_VAULT_PATH`'s actual value into the database, the API, and the
  UI. `error_type` is one of a small closed set (`path_validation_failed`,
  `permission_denied`, `disk_full`, `unknown`) and `error_message` is a
  fixed, per-type human string that never interpolates the underlying
  exception's own text or any path — tested explicitly (the raw
  `OSError`/`PermissionError` message is asserted to never reach the stored
  row, the audit log, the API response, or the UI).
- **Scope of what's exported**: every `Memory` row the actor can read via
  `for_actor()` in the workspace, **filtered to `status=approved`
  only** — `pending_approval`/`rejected` rows were never curated into the
  user's accepted knowledge, and an `archived` (forgotten) memory is
  explicitly meant to stop being mirrored (see reconciliation below), so
  none of those three statuses are ever written to the vault. `private`
  vs `normal` sensitivity is irrelevant here (ADR-005's cloud-eligibility
  restriction is about LLM gateway payloads, not the local filesystem vault
  — a private memory is still exported, same as it's still included in the
  portable JSON export from MVP 2).
- **File layout & naming**: `03_Workspaces/<workspace.id>/Memory/
  <memory.id>.md` (per `docs/memory/obsidian-vault-strategy.md`'s layout;
  `workspace.id`/`memory.id` — not `name`/`title` — keeps paths
  filesystem-safe and stable across a title edit, since the safe path
  characters permitted are a smaller set than the fields users may freely
  edit).
- **Frontmatter standard** (per the vault-strategy doc, all required):
  `friday_id` (the memory's UUID — the reconciliation key, see below),
  `type` (the memory's `memory_type`), `workspace` (`workspace.id`),
  `project` (empty/omitted for now — no project concept exists yet in this
  codebase; the field is reserved per the vault-strategy doc, not invented
  here), `sensitivity`, `source: friday_export` (marks the file
  never-re-ingest, the echo-chamber guard the vault-strategy doc requires —
  vault → Friday ingestion itself remains out of scope, still deferred past
  MVP 3 and this MVP alike), `updated_at`.
- **Reconciliation (forget/edit), full resync + conservative prune
  (tightened per Codex plan-review round-1 HIGH finding)**: each export run
  (i) writes/overwrites a file for every currently-`approved` memory in
  scope (edits naturally reconcile — the file is fully regenerated from the
  current row every run, never patched), then (ii) scans the workspace's
  `Memory/` vault folder and deletes a file **only if it passes every one
  of these checks**: parses as valid frontmatter, `source: friday_export`,
  `workspace` frontmatter equals the current export's workspace id,
  `friday_id` parses as a UUID, and that UUID is **not** in the current
  `approved` set. **Any file failing any single check — malformed
  frontmatter, a missing field, `source` not `friday_export`, a mismatched
  workspace, a non-UUID `friday_id` — is left untouched and counted as
  "skipped," never deleted.** This is deliberately conservative: a
  human-authored note, a future non-memory export, or simply a file this
  job doesn't recognize must never be silently destroyed by a prune pass.
  Matches "atomic writes" (write to a temp file + rename, never a partial
  file visible mid-write) and "tombstone or delete of mirrored files" from
  the MVP 2 deferred spec, without the original "delete anything not in
  the current set" design's collateral-damage risk.
- **Two-tier vault root validation, split by which container can actually
  check what (Codex plan-review round-2 HIGH finding — the original single
  `validate_vault_root()` contradicted the worker-only bind mount: the web
  process cannot verify "exists as a real directory" for a path it doesn't
  have mounted)**:
  - **Tier 1 — web-safe config validation, at request time.** `OBSIDIAN_
    VAULT_PATH`'s *string value* (the env var itself, available to both
    containers regardless of the mount) is checked in the request-creation
    path with zero filesystem access: non-empty, `os.path.isabs()` (rejects
    both empty/unset and **relative paths** — Codex plan-review round-1
    MEDIUM finding; `.env.example` already documents this setting as
    absolute, this makes it an enforced rule, not just a comment), not `/`
    (or any OS root), and not equal to or a string-prefix ancestor of
    `Path.home()` (a pure path-string comparison, not a `stat` call, so it
    needs no mount access either). Any of these failing means the tool is
    **not runnable** and the request itself 400s with a clear message —
    this genuinely doesn't need worker-side filesystem access to catch.
  - **Tier 2 — worker-side existence/directory validation, at execution
    time.** Whether the configured path actually **exists as a real
    directory** can only be checked where it's actually mounted — the
    **worker** container (compose change, §config; never mounted into web
    at all, keeping web's containment surface unchanged). The export task
    checks this first, before any other file operation, and if it fails
    (misconfigured mount, path doesn't exist, etc.) the `VaultExportRun`
    transitions to `failed` with `error_type=path_validation_failed` — a
    run-time failure surfaced via the run-status indicator (§4), not a
    request-time 400, since by the time this check runs the request has
    already been approved and dispatched.
  - This is the first worker task in this codebase that touches the host
    filesystem rather than only Postgres or an outbound HTTP call, so it
    gets its own explicit two-tier contract the same way `knowledge.
    url_fetch` got explicit SSRF-hardening contracts.
- **Per-operation path safety, race-resistant and covering every
  filesystem interaction the export makes (Codex plan-review round-1 HIGH
  + round-2 HIGH findings — a single `realpath` check before the whole run
  is TOCTOU-vulnerable, and the fix must cover more than just write/rename/
  unlink)**: once tier 2 passes, **every one of the export's filesystem
  interactions** — creating the `03_Workspaces/<workspace.id>/Memory/`
  directory if missing, opening/writing a temp file, the atomic rename,
  unlinking a pruned file, scanning the directory for prune candidates, and
  opening a file to read its frontmatter — re-validates its own immediate
  parent directory's `realpath` immediately before that specific operation,
  not once at run start. This narrows the window in which a directory
  component could be swapped to a symlink (e.g. by Obsidian itself, a sync
  client, or another local process) between validation and use from "the
  whole run" to "one operation." Where the platform supports it (`os.
  supports_dir_fd`, available on Linux — the deployment target — though
  not universally on every OS), every one of these operations (including
  `mkdir`, `scandir`, and file `open` for reading — not only writes) is
  performed `dir_fd`-relative to an already-opened, already-validated
  directory descriptor with `O_NOFOLLOW`, closing the gap further by
  construction rather than by re-checking. **This narrows the race window;
  it does not claim to eliminate it on platforms without `dir_fd`/
  `O_NOFOLLOW` support** — that residual risk is recorded here explicitly
  rather than assumed away, and is small in practice (it requires an
  adversarial or badly-behaved *local* process racing this job's
  operations, not a remote attacker — a materially different threat model
  than the SSRF case this design is otherwise modeled on). Tested with a
  symlink-swap-mid-run race test covering at least one read-path operation
  (directory scan) and one write-path operation, not only a static
  "symlink present from the start" test on writes alone.
- Audited as `vault_export` (per the vault-strategy doc's naming), metadata
  carries counts (written/updated/removed) and workspace id, never file
  contents.

### 4. Frontend: Approvals UI + export trigger

- `/approvals` page: pending list (approve/reject), decided-history list
  (status filter), following the established BFF pattern (HttpOnly cookies,
  CSRF, UUID-validated dynamic segments). **The approve/reject controls are
  hidden (not just disabled) for a request the viewing actor made
  themselves** — a UI-level reflection of §1's model-level `requested_by !=
  decided_by` rule, not merely a fallback for when the backend 403s; own
  requests still show in the list with their status, just without a
  decide affordance the backend would reject anyway.
- An export trigger surfaced on the Memory page (e.g. an "Export to Obsidian
  vault" button next to the existing portable-export button) that creates
  an `ApprovalRequest` for the `vault_export` tool. **Because a personal
  workspace today has exactly one member (its owner), and requester ≠
  decider is enforced without exception in production, the request the
  button creates cannot be approved by anyone in a personal workspace
  today** — the success state says so plainly ("Request created. It needs
  approval from another workspace admin before it runs.") rather than
  implying the user can just go approve it themselves. This is a real,
  intentional limitation of this MVP, not a bug — see Risks/notes.
- A visible run-status indicator on `/approvals` for an approved request
  (pending execution / succeeded / failed with the error surfaced), since
  approval and actual completion are now two separate moments in time
  (dispatch is async, §2) — the UI must not imply "approved" means "done."

### 5. E2E + docs

- Playwright smoke extension: request the vault export from the Memory
  page, approve it on `/approvals`, confirm the run reaches `succeeded`.
  The e2e stack signs up a single fresh user per run (no second workspace
  member exists to be the decider), so the e2e backend sets
  `DEV_ALLOW_SELF_APPROVAL=true` — the same dev-only flag from §1, used
  here deliberately and only because there is no second actor in this test
  environment, not as an endorsement of the pattern in a real deployment.
  The e2e backend has no real Obsidian vault directory —
  `OBSIDIAN_VAULT_PATH` is set to a temp directory created by
  `global-setup.ts` for the e2e run (mirrors how the e2e stack already sets
  other env-backed settings), and the assertion is on the UI's own
  succeeded state, not on inspecting the filesystem from Playwright.
- Docs: MASTER_PLAN new section (mirroring §Q/§T's structure), §L roadmap
  line update, §O Open Decisions resolution (the Obsidian-export item this
  brief's own header references), this brief's status updates per branch.

## Out of scope (explicit)

- **Agent-requested approvals.** Every request in this MVP is human-
  initiated. The requester/decider separation from §1 is already built for
  the day an agent becomes the requester — nothing further needs revisiting
  there when that MVP arrives; what that MVP *does* still need to decide is
  how an agent's request surfaces to a human decider (notification, queue,
  etc.), which is a UX question, not a data-model one.
- **A generic, DB-backed `Tool`/`ToolExecution` model.** One concrete tool
  doesn't justify one; extract the common shape once a second tool exists
  and the commonality is real (§2's reasoning).
- **Auto-approval per low-risk tool** (ADR-004: "may later be enabled...
  never for the hard rules") — every gated action in this MVP requires an
  explicit human decision, no exceptions, no per-tool config to skip it.
- **Vault ingestion (vault → Friday)** and its echo-chamber guard — still
  deferred (originally MVP 3+, remains open; `source: friday_export`
  frontmatter is written now so a future ingestion job can honor the guard
  from day one, but nothing reads the vault in this MVP).
- **KnowledgeDocument vault export** — the vault-strategy doc's exported
  classes are memories/decisions/agent-run summaries/skill cards; knowledge
  documents aren't named there and aren't exported in this MVP. Revisit if
  a concrete need surfaces.
- **Non-Obsidian export targets, or exporting anything other than Memory**
  (tasks, knowledge documents) — one tool, one content type, proving the
  gate end to end; breadth is a later concern.
- **Company-workspace-specific approval routing** (e.g. requiring a
  *different* admin to decide than the requester) — no company workspaces
  are active yet (MVP 10 deferred); the curator-role check (owner/admin)
  is the only rule this MVP needs.

## Branch plan

| # | Branch | Contents |
|---|---|---|
| 1 | `feat/mvp5-approvals-foundation` — **done (2026-07-09)**. Codex review (`--base main`): round 1 APPROVE, no findings ("state transitions, scoping checks, expiry handling, and configuration guard appear consistent with existing project patterns"; independently verified `ApprovalsConfig.ready()` refuses to boot under `config.settings.prod` with `DEV_ALLOW_SELF_APPROVAL=true`, raising `ImproperlyConfigured`, as designed). A transaction-rollback bug was caught by the test suite itself before review: the original `approve_request`/`reject_request` raised `InvalidTransition` from inside the same `@transaction.atomic` block that recorded the decision-time expiry write, silently rolling that write back; fixed by exiting the atomic block before raising. 56 approvals tests, 717 total backend tests, ruff/import-linter clean. | `approvals` kernel app, `ApprovalRequest` model + state machine + role matrix, actor-scoped `_locked()` decision service, decision-time expiry re-check, `DEV_ALLOW_SELF_APPROVAL` + its `ready()` fail-fast guard, request/approve/reject API (tool-name validated against the registry from branch 2 — see Risks on sequencing), expiry sweep, audit events, permission + concurrency tests |
| 2 | `feat/mvp5-tools-registry` — **done (2026-07-09)**. Codex review (`--base main`): round 1 APPROVE, no findings ("the new registry wiring and approval dispatch behavior appear consistent with the staged implementation and existing tests"). 7 new tests (63 approvals+tools tests, 724 total backend tests), ruff/import-linter clean (new one-way contract added: `tools` must not import `approvals`). | `tools` kernel app, `ToolSpec` registry (name/gated/`create_execution()` callable/dispatch-task reference), decision service calls `create_execution()` inside its own transaction before `on_commit` dispatch, tests using a trivial in-test-only stub `ToolSpec` (mock `create_execution` — no real backing table, proves transactional ordering only) |
| 3 | `feat/mvp5-obsidian-export` — **done (2026-07-09)**. Codex review (`--base main`): **5 rounds**, matching MVP3's SSRF-hardening branch in depth (all P2, never CRITICAL/HIGH — user explicitly confirmed continuing the loop past the round-4 checkpoint). Round 1: short-write truncation on `os.write` (not guaranteed to write the whole buffer — could silently publish a truncated file on a near-full disk), a symlinked `OBSIDIAN_VAULT_PATH` bypassing tier-1 checks entirely (tier 2 now re-validates the resolved realpath against tier 1's own rules), prune-read following a symlink (`read_validated` now uses `O_NOFOLLOW`). Round 2: the round-1 `O_EXCL` hardening itself introduced a regression — a failed write left a deterministic per-pid temp file behind, so a same-process retry hit `FileExistsError` immediately, turning a transient failure into a permanent one (fixed with cleanup-on-failure). Round 3: `UnicodeDecodeError` (not `OSError`) from a non-UTF-8 `.md` file wasn't caught by the prune loop, failing the whole export instead of skipping one file; overlapping concurrent exports for the same workspace could race (an older run's stale approved-id snapshot pruning a newer run's freshly-written file) — fixed with a `select_for_update` lock on the workspace row held for the whole write+prune pass. Round 4: worker-side validation only re-checked the *resolved* path's safety, never that the *original configured string* was absolute (`os.path.realpath` always returns absolute even for `.` — silently accepting a relative `OBSIDIAN_VAULT_PATH` misconfiguration); a FIFO planted in the vault could hang the prune read forever (`O_NOFOLLOW` only refuses symlinks, not other special files — fixed with `O_NONBLOCK` + an explicit `S_ISREG` check). Round 5 APPROVE, no findings. 786 backend tests, ruff/import-linter clean (9/9 contracts, including a new one-way `tools -> workspaces` dependency for the export-serialization lock). | `VaultExportRun` model + lease-fenced claim/finalize Celery task + recovery scan (real `create_execution()` implementation, durable-before-dispatch test with a real row), frontmatter, full-resync + conservative-prune reconciliation, two-tier vault-root validation (web-safe config check at request time, worker-side existence check at execution time), race-resistant path safety across every filesystem interaction (not just writes), sanitized error fields, atomic writes, registered as the first real gated tool, compose worker bind mount + `OBSIDIAN_VAULT_PATH` wiring, security tests (path-escape + symlink-swap-race rejection covering read and write paths, relative/unsafe-root rejection, prune-conservatism, sanitized-error assertions) |
| 4 | `feat/mvp5-approvals-ui` — **done (2026-07-09)**. Codex review (`--base main`): **5 rounds**, all P2, never CRITICAL/HIGH (user explicitly confirmed continuing the loop past the round-4 checkpoint, same as branch 3). Round 1: the unpaginated `/api/v1/approvals/requests` list ran a fresh `VaultExportRun` query per row for the new `execution` field (N+1) — fixed with `select_related("vault_export_run")` on the queryset plus a `describe_vault_export_execution` that reads the cached reverse relation instead of querying; a `CaptureQueriesContext` regression test proves the query count stays flat as row count grows. Round 2: dispatch is async, so approving a request left the UI's run-status indicator stuck at "Waiting to run" until a manual reload — `ApprovalView` now polls silently (no skeleton flash) every 3s while any request has an in-flight execution, stopping on its own once every execution reaches a terminal status. Round 3 (two findings): `ApprovalCard` rendered Approve/Reject for any non-requester, even members/viewers whom the backend's curator-role check (owner/admin only) always rejects with a 403 — fixed by threading the active workspace's role down as a `canDecide` prop; the Memory page's "Export to Obsidian vault" button had no in-flight guard, so a double-click issued duplicate approval requests — fixed with a busy-state disable. Round 4: switching workspaces while a vault-export request was in flight could leak workspace A's success/error notice onto workspace B and leave B's button disabled until A's request settled — fixed by mirroring the existing `activeWorkspaceIdRef` guard already used for memory creation. Round 5 APPROVE, no findings. 793 backend tests + 243 frontend tests, ruff/eslint/import-linter/typecheck/build clean, browser-verified end to end (create request → plain-language success notice → pending in `/approvals` with own-request gating). | Next.js `/approvals` page, BFF routes, Memory-page export trigger + run-status indicator, Vitest coverage |
| 5 | `feat/mvp5-e2e-docs` — **done (2026-07-10)** — MVP 5's final branch. Codex review (`--base main`): **2 rounds**. Round 1: the documented local e2e flow (`make up` then `make test-e2e`) broke, since only CI's e2e job env had `DEV_ALLOW_SELF_APPROVAL` — the compose dev stack's backend didn't, so the new smoke test's self-approval call 403'd locally; fixed by enabling the same test-only flag in `deploy/docker/compose.yml`'s backend env (this stack is already `DEBUG=true` with an insecure dev secret and a fake AI provider, so this matches its existing posture), verified by re-running `make test-e2e` against it directly. Round 2 APPROVE, no findings. Verified with a real Playwright run against the Docker stack (not just CI): approve call returns 200, the worker actually writes a file under `/vault`, and the UI's run-status indicator reaches "Succeeded" via the existing reload-poll convention. | Playwright smoke extension (request → approve → succeeded), MASTER_PLAN new section + §L roadmap line + Open Decisions resolution, brief status updates |

Branch 1 and 2 have a real ordering dependency (branch 1's request-creation
API validates `tool_name` against branch 2's registry) — branch 1 lands
first with the registry check stubbed to "no tools registered yet, every
`tool_name` 400s," which branch 2 then makes real, the same staged-landing
shape memory used across its extraction branches (branch 3 created jobs
with no dispatch; branch 4 added dispatch).

Config enumerations to touch: branch 1 — installed apps (`approvals`), URLs,
pytest paths, import-linter roots/contracts, new settings
`APPROVAL_REQUEST_TTL_HOURS` (default 24) and `DEV_ALLOW_SELF_APPROVAL`
(default `False`, documented in `.env.example` as dev-only, never set in a
real deployment); branch 2 — installed apps (`tools`), embedded-beat
schedule entry for the execution-recovery scan; branch 3 — compose worker
bind mount for the vault directory, `OBSIDIAN_VAULT_PATH` moves from an
inert placeholder to an actually-consumed setting, `.env.example` comment
update reflecting that.

Per-branch flow unchanged from MVP 2/3/4: implement → tests+ruff+CI green →
Codex CLI review (`--base main`) → fix → APPROVE → merge to main (no extra
user confirmation per the 2026-07-05 protocol, except a CRITICAL/HIGH
finding or a review loop dragging past a few rounds — both still checkpoint
with the user, and this MVP's filesystem-write surface makes a CRITICAL/HIGH
path-safety finding more likely than prior MVPs, so treat that checkpoint
rule as live here, not theoretical).

## Test strategy

- pytest: `for_actor` leakage matrix, `ApprovalRequest` state-machine
  (legal/illegal, no idempotent-repeat — a second decide is always 400),
  role × endpoint matrix (request: writer roles; decide: curator roles),
  **requester ≠ decider enforced test** (same actor attempting to
  approve/reject their own request is rejected even when they hold a
  curator role, both with `DEV_ALLOW_SELF_APPROVAL` unset/false — the
  default — and a separate test confirming the flag actually relaxes it
  when explicitly enabled, so the flag's own behavior is covered, not just
  assumed), **`DEV_ALLOW_SELF_APPROVAL`-with-`DEBUG=False` startup test**
  (`ApprovalsConfig.ready()` raises `ImproperlyConfigured` — a Django
  system-check-style test, not a request-level test), **decision-time
  expiry re-check test** (a `pending` request older than the TTL is
  transitioned to `expired` and the decide call itself fails, even if the
  sweep task has never run in that test), expiry-sweep (time-mocked or
  two-connection), **two-connection concurrency tests for the actor-scoped
  lock**: approve-vs-reject on the same request (one wins, the other
  observes the post-lock decided state and 400s) and approve-vs-the-sweep-
  expiring-it (mirroring the equivalent test class in memory's/tasks'
  service suites), `tool_name` validation against the registry (unknown
  name, known-but-non-gated name if any, known-and-gated), **workspace-
  scoping tests for `payload`** (a forged `{"workspace": "<other-id>"}` key
  inside `payload` is ignored — the export always uses
  `ApprovalRequest.workspace`; cross-workspace `ApprovalRequest` creation is
  rejected the same way every other kernel app's create path already is),
  **payload-redaction-on-read test** (list/detail responses never include
  the raw `payload` field), lease-fenced execution claim/takeover
  (two-connection concurrency test, mirroring memory's/knowledge's),
  retryable vs terminal failure classification, **sanitized-error tests**
  (a simulated `OSError`/`PermissionError` whose message contains the real
  vault path never reaches the stored `error_message`, the audit log, or
  any API response — assert the closed `error_type` vocabulary, not just
  "some error was recorded"), **path-jail security tests are
  review-blocking, not incidental coverage**: tier-1 web-safe validation
  rejects empty/unset, a relative path, `/`, and the home directory at
  **request time** (400, each as its own test case), tier-2 worker-side
  validation rejects a **non-existent** path only at **execution time**
  (the run transitions to `failed` with `error_type=path_validation_failed`,
  never a request-time 400, since the check can only run where the mount
  actually exists), a **symlink-swap-mid-run race test covering both a
  read-path operation (directory scan/prune) and a write-path operation**
  (not only a static "symlink present from the start" test on writes alone
  — the swap happens between a validation and its corresponding in-flight
  operation), a memory whose title/content somehow contains path-like
  characters never affects the file's actual location (the export path is
  built from `memory.id`, never from user-controlled text),
  **durable-before-dispatch test** (mock the broker publish to fail after
  the approval's transaction commits — a `VaultExportRun` row still exists
  in `pending` afterward, provable by the recovery scan reclaiming it),
  **conservative-prune tests** (a malformed
  file, a file with `source` other than `friday_export`, a file whose
  `workspace` frontmatter doesn't match, and a file with a non-UUID
  `friday_id` are each individually left untouched by a prune pass — not
  one combined "prune works" assertion), reconciliation test (an approved
  memory gets a file; forgetting it removes the file on the next run; a
  hard-deleted memory's file is also removed; editing a memory's content
  regenerates its file rather than leaving stale content), atomic-write
  test (a simulated mid-write failure never leaves a partially-written file
  visible at the final path), audit metadata never contains memory content
  or the full `payload` field verbatim.
- Vitest/RTL: Approvals page states (pending list, approve/reject, decided
  history + status filter), Memory-page export trigger, run-status
  indicator (pending execution vs succeeded vs failed), BFF CSRF/cookie
  contracts.
- Playwright: the request → approve → succeeded happy path (scope 5), using
  a temp-directory `OBSIDIAN_VAULT_PATH` the e2e stack sets up itself.

## Risks / notes

- This is the first MVP where a background job **writes to the host
  filesystem** rather than only to Postgres — every prior worker task
  (extraction, embedding, URL fetch) only ever touched the database or made
  an outbound HTTP call. Treat the path-jail/symlink/atomic-write tests with
  the same weight MVP 3's SSRF tests got (that branch's Codex review found
  a real P1 bypass across 10 rounds) — a filesystem escape is the same
  severity class as an SSRF bypass, just a different resource.
- The branch 1/2 sequencing dependency (tool-name validated against a
  registry that doesn't exist until branch 2) is a known, accepted
  awkwardness — flagged explicitly here rather than discovered mid-review,
  mirroring how MVP 2 flagged its own branch 2/3 `assistant`-touching
  sequencing in its own Risks section.
- Whether `project` (an empty/reserved frontmatter field per §3) ever gets
  populated depends on a "project" concept that doesn't exist anywhere in
  this codebase yet — worth a MASTER_PLAN Open Decision entry of its own if
  it doesn't already have a natural home once this brief is finalized.
- **Deliberate, explicit trade-off (user decision, 2026-07-08): strict
  requester ≠ decider means no gated tool is actually runnable in
  production for a personal workspace today**, since a personal workspace
  currently has exactly one member. This MVP ships the gate built
  correctly for its real purpose — the day an agent, not a human, is the
  requester — even though that means the human-facing "click the button
  and use the feature" path doesn't fully close until either MVP 10
  (company workspaces, a second admin) or a later agent-requested-approval
  MVP exists. `DEV_ALLOW_SELF_APPROVAL` exists solely so this MVP's own
  branches (and later ones) can exercise and test the approve/execute path
  without waiting for either of those — it is not a workaround offered to
  end users, and nothing in this MVP's scope suggests exposing it as a
  user-facing setting. This is a real, known limitation, not an oversight
  — recorded here so it isn't rediscovered as a "bug" later.
