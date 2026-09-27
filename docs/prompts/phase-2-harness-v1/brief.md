# Phase 2 — Harness v1 (read-only discovery/briefing)

> **Closed out (2026-07-20): all 4 branches shipped and merged into
> `claude/repo-setup-env-transition-1g94hx`.** No CRITICAL finding across
> any branch's code-review; branch 2 (`feat/p2-harness-runner`) was the
> densest arc (7 rounds), branches 1/3/4 landed in 5/5/2 rounds
> respectively. Four MEDIUM-equivalent findings from branch 2's own
> round 7 were tracked as an explicit follow-up rather than re-opening
> that branch, and were fixed and merged separately
> (`fix(harness): translate delete's InvalidTransition, sweep leaked
> terminal containers, raise finalization buffer, harden clone argv`,
> plus a round-8 buffer-math fix) before branch 3 began. The usable
> outcome this phase targeted (MASTER_PLAN §AA: "Analyze this bug in
> repo X, brief me.") is now real end-to-end: register a repo (branch 1),
> launch a container-isolated CLI run with a full brake system (branch
> 2), get a gateway-produced briefing with the R2 prompt-injection
> mitigation applied both in the pipeline and at render time (branch 3),
> and drive all of it from a plain list/detail UI (branch 4).
>
> Closeout note (2026-09-27): R6's Codex CLI go/no-go was recorded in the
> Phase 3 brief (resolved GO, API-key auth only). "Real end-to-end" above
> means the mocked suite — no real (non-mocked) harness run has happened
> yet (the image versions/digests are placeholders, see
> `backend/harness/docker/README.md`); a real run is scheduled in the
> Phase 3 closeout.
>
> Status: **approved for implementation, with two items explicitly deferred
> to branch 2's own PR rather than pinned here** (plan-review arc: round 1
> 6H/3M/1L, round 2 4H/2M/1L, round 3 2H/1M/1L, round 4 3H/1M/1L — no
> CRITICAL in any round. Round 4's HIGH count went back up rather than
> down; user checkpointed a second time and chose: fix the pending-state
> cancellation gap in the brief itself, since it's a plan-level state-
> machine completeness issue, but treat the container image/CLI-update
> policy and the exact per-vendor network-egress endpoint manifest as
> real, legitimate findings that are nonetheless implementation-level
> Docker/vendor-documentation specifics better pinned and verified against
> branch 2's actual code and its own Codex code-review than against more
> brief prose — both are recorded as explicit branch-2 PR requirements
> below, not silently dropped. No further plan-review round was run after
> this revision; branch 2's own code-review is where these two remaining
> items get closed out for real.).
> Parent decisions: ADR-008, `docs/VISION_JARVIS_2.md` §4/§6/§7/§8,
> MASTER_PLAN §AA row 2.

## Goal / value

Ship the first slice of the agent harness: a new `harness` kernel app that
drives an external CLI coding agent (Claude Code first; Codex CLI as a
documented second option, see O-2 below) **read-only** against a registered
repo and produces a **briefing** — a structured, human-readable summary of
what the agent found, never a code change. No git write, no branch push, no
deploy: those are Phase 3/5. This phase proves the riskiest new primitives
(external CLI invocation, ModelUsage accounting for out-of-process work, a
harness-specific brake system, and the low-trust framing of repo content)
at the lowest possible blast radius — a failure mode here is "a wrong or
wasted briefing," never "an unreviewed write landed somewhere." The usable
outcome (MASTER_PLAN §AA): "Analyze this bug in repo X, brief me."

Phase 1 built the landing spot for this phase's output (`work_log` memory
class, ADR-008 k.4, `memory.services.record_work_log()`) but wired no real
producer — this phase is that producer's first instance.

## Boundary vs. existing systems

- **New kernel app, one-way dependency root** (ADR-002/ADR-008 decision 1):
  `harness` may import `core`, `workspaces`, `ai_core`, `audit`, `memory`
  (to call `record_work_log()`); nothing may import `harness` in this phase
  (no app yet depends on harness output beyond the memory row it writes).
  Added to `root_packages` and given the same three baseline contracts every
  other kernel app already has (`backend/pyproject.toml:67-127`): no
  `config` import, independent of `core`, no Django-config import from
  `core`.
- **Not the `agents` (MVP 7) runtime.** `agents` stays as-is for chat-born
  light plans running in-process against the gateway; `harness` is a
  separate, parallel runtime for out-of-process external CLI agents against
  real repos. ADR-008 decision 1 explicitly defers merging them to
  post-Phase-3 (O-3, still open) — this phase does not touch `agents/`.
  Where a pattern is proven there, it's copied, not imported: the brake
  system (`agents/execution.py`'s per-step timeout, consecutive-failure
  halt, resource budget, `select_for_update`-fenced kill switch —
  brake fields on `Agent`, `agents/models.py:78-100`, settings in
  `config/settings/base.py:206-235`) is the reference shape this phase's
  `HARNESS_MAX_*` settings and `cancel_harness_job()` mirror, adapted to a
  single long-running external-process job rather than an in-process
  multi-step plan (Codex round-1 LOW: `agents/models.py:29-64` in an
  earlier draft was imprecise — that range is `Status`/`FailureReason`
  choices, not the brake fields themselves).
- **ModelUsage stays `ai_core`-owned, even for out-of-process calls**
  (Codex round-1 HIGH: an earlier draft of this brief had `harness` write
  `ModelUsage` rows directly, which contradicts CLAUDE.md rule 2's "all LLM
  calls go through `ai_core`'s InferenceGateway — no direct provider SDK
  usage anywhere else," and undersells what ADR-008 decision 1 actually
  says: external CLI usage "is reported **through** the InferenceGateway as
  ModelUsage," not written around it). `ai_core.gateway` gains one new
  function, `record_external_usage(*, workspace, purpose, backend,
  input_tokens, output_tokens, estimated_cost, latency_ms, success, error_type="",
  context_report=None)` — it does not call any provider adapter (there is
  no adapter for an external CLI subprocess) but it is the *only* place a
  `ModelUsage` row can be constructed outside `InferenceGateway.complete()`/
  `.embed()`'s own internal path, keeping `ai_core` the single accounting
  door in fact, not just in spirit. `harness` calls this function; it never
  imports or constructs `ModelUsage` itself. (Codex round-3 LOW: an earlier
  draft's signature used `cost` while the test-strategy section described
  `estimated_cost` round-tripping — pinned now to match `ModelUsage`'s own
  field name exactly, `estimated_cost`; `request_id` is not a parameter at
  all, read from `core.context.get_request_id()` inside the function the
  same way `ai_core/gateway.py:703`'s existing in-process accounting
  already does, so external and in-process usage rows get request-id
  correlation the same way, not two different conventions.) `backend` is
  `"harness_cli:<agent-name>"` (`ModelUsage.backend`, `ai_core/models.py:136`,
  default `"in_process"` for every existing in-process call). Token/cost
  figures come from the CLI's own reported usage where the invocation
  surface exposes it (Claude Code's `--output-format json` reports a
  `total_cost_usd`/usage block in its result object — confirmed against
  Anthropic's CLI reference during this brief's plan-review, not assumed);
  where a selected agent's output genuinely doesn't expose it,
  `input_tokens`/`output_tokens`/`estimated_cost` are recorded as `0` with
  `context_report` noting `"usage_source": "unavailable"` rather than a
  fabricated estimate — but see branch 2's cost-brake bullet below: an
  agent without usable reported cost is not selectable in this phase at
  all, not merely "degraded."
- **One `ModelUsage` row per external call actually attempted, not a fixed
  "two per job"** (Codex round-1 HIGH: an earlier draft pinned "exactly one
  row per job" in branch 2 while branch 3 left open a second, in-process
  gateway call for briefing summarization — a direct contradiction; Codex
  round-2 HIGH: the round-1 fix's own "two rows per job" phrasing was
  itself imprecise about cancellation/failure — a job cancelled before any
  transcript exists cannot have attempted a briefing call, so "always two"
  was never literally true). Pinned precisely now: a **discovery row** is
  written for every job whose external CLI subprocess actually launched
  (`backend="harness_cli:<agent-name>"`, `purpose="harness.discovery"`, via
  `record_external_usage()` above — including a cancelled/failed run,
  `success=False`); a **briefing row** is written only for a job that
  actually reaches the summarization step (`purpose="harness.briefing"`,
  through `InferenceGateway.complete()` exactly like every other in-process
  call — mockable via FakeAdapter, versioned `PromptTemplate`, real
  `context_report`) — a job cancelled or failed before that point simply
  has no briefing row, which is the correct, not a missing, outcome. See
  branch 3 — this also resolves that branch's deferred "CLI-native summary
  vs. a second gateway call" question in favor of the gateway call, since
  trusting the CLI's own self-summary to have already done the low-trust/
  high-trust separation is exactly the framing risk CLAUDE.md rule 2 exists
  to avoid: Jarvis's own reasoning about what the repo said must go through
  the same accounted, versioned, mockable path as everything else, not the
  external tool's own opinion of itself.
- **Repo registration is new schema, not reuse of the firm-dossier JSON.**
  `Workspace.profile.repos[]` (Phase 0/1, `workspaces/models.py:38`) is
  free-text context-injection metadata — no validation that the URL is
  reachable, no notion of "harness may act here." Harness needs an
  explicit, separate allowlist: a repo only becomes harness-actionable by a
  deliberate registration step, distinct from (and not derived from) the
  dossier. See branch 1.
- **`record_work_log()` is the only write this phase makes into `memory`**
  (Phase 1 branch 2, `memory/services.py`) — one row per completed harness
  job, `system_generated`/`work_log`, auto-approved per ADR-008 k.4. No
  other memory type/source combination is touched.
- **Audit**: every harness job gets an `AuditLog` row on creation, on
  completion (success or failure), and on cancellation — same discipline as
  `memory.context_accessed`/`tools.export`'s dispatch trail, scoped to the
  job's workspace.

## Open question resolved: O-2 (CLI agent headless auth)

- **Claude Code**: officially supports non-interactive/headless invocation
  (`claude -p "<prompt>" --output-format json`, and the Claude Agent SDK) —
  this is a documented, supported mode, not a workaround. This phase's
  first and default agent.
- **Codex CLI**: this repo's own review workflow already runs it
  non-interactively today (`codex exec`/`codex review --base <branch>`,
  `docs/prompts/.../brief.md` orchestration notes) under the owner's
  ChatGPT plan — proving headless invocation works in practice, including
  its rate-limit behavior (a usage-limit error, not a silent hang). Vendor
  terms for *automated, unattended* use (vs. the owner manually launching a
  review each time) are not yet separately confirmed; branch 2 verifies
  this explicitly before wiring Codex CLI as a selectable harness agent and
  documents the outcome in that branch's PR. If terms block it, Codex CLI
  is deferred (Claude Code alone still satisfies this phase's usable
  outcome) rather than automated against unclear terms.
- **Gemini CLI**: named in the vision as a future comparison agent
  (§4) — not wired in this phase. Registering it is a follow-up once the
  policy table (branch 2) has two working agents to route between.
- **API-key fallback**: the vision's fallback plan (§7 item, ADR-008
  decision 7) if vendor terms block subscription-based automation — not
  needed in this phase since Claude Code's headless mode is already the
  supported path; documented here so it isn't re-litigated later.

## Scope (one branch each)

### 1. `feat/p2-harness-repo-registry` — `harness` app skeleton + repo allowlist

- New `harness` app: `HarnessRepo` (`WorkspaceScopedModel` — mirrors
  `core.db.WorkspaceScopedModel`/`for_actor()` exactly, ADR-001) with
  `name`, `git_url`, `default_branch`, `runner` (`CharField` with a closed
  `TextChoices` vocabulary, one value for this phase: `MAC_LOCAL` — the
  "first runner is the Mac" decision, ADR-008 decision 7 — a real Runner
  abstraction with dynamic registration is a later-phase concern, not
  invented speculatively here; `MAC_LOCAL` means the owner's Mac running its
  own Docker daemon, which branch 2's per-job container relies on), `enabled`
  (bool, default `False` — a repo is inert until explicitly enabled, never
  auto-enabled from the dossier), `sensitive` (bool, default `False` — see
  below). No credentials field: auth for the clone (SSH key / PAT) is
  resolved from env at run time (`CLAUDE.md` "secrets only via env"), never
  stored on the row.
- Curator-gated CRUD (owner/admin roles, same `_CURATOR_ROLES` convention
  as `agents/execution.py:51`, `WorkspaceDetailView`'s edit gate
  (`workspaces/views.py:34`, Codex round-1 LOW: an earlier draft named a
  `WorkspaceDossierView` that doesn't exist — the dossier is the frontend
  component, `WorkspaceDossierView.tsx`; the backend gate is on
  `WorkspaceDetailView`)) — role × endpoint permission tests for every verb
  (CLAUDE.md rule 7).
- Registered as a real Django app: added to `INSTALLED_APPS`
  (`config/settings/base.py`) and to pytest's `testpaths`
  (`backend/pyproject.toml:49`, Codex round-1 MEDIUM: `testpaths` is an
  explicit list — a new app's tests silently never run under `make test`/CI
  unless added here, which would violate CLAUDE.md rules 7/8 without anyone
  noticing) from this branch onward.
- `HarnessRepo.sensitive` (bool, default `False`, curator-settable at
  registration/edit time): resolves branch 3's work_log sensitivity trigger
  (Codex round-1 HIGH, see branch 3) at the point where it actually
  belongs — a property of the repo the owner is registering, not a
  per-job runtime decision. A repo covering sensitive client work is
  flagged once, here, not re-decided on every run.
- Registering a repo here is deliberately **not** wired to the dossier's
  `profile.repos[]` in either direction in this branch — no migration
  copies one into the other. Whether a later phase should offer "register
  this dossier repo for harness" as a UI convenience is left as a follow-up
  note in this branch's PR, not solved here (avoids coupling a validated,
  security-relevant allowlist to a free-text convenience field).
- Import-linter: add `harness` to `root_packages`, plus the three baseline
  contracts (no `config` import, independent of `core`, no Django-config
  import from `core` — mirrors every existing kernel app's entry).

### 2. `feat/p2-harness-runner` — CLI agent invocation + brake system

- `HarnessJob` model: `workspace` FK, `repo` FK (`HarnessRepo`, must be
  `enabled=True` — enforced at creation, not just at dispatch time), `goal`
  (text, the read-only discovery prompt), `agent` (`TextChoices`:
  `CLAUDE_CODE` default; `CODEX_CLI` only if O-2's Codex verification in
  this branch clears it), `sensitivity` (`TextChoices`, `Memory.Sensitivity`'s
  exact values — snapshotted from `repo.sensitive` at creation, see branch
  3's precise mapping), `status` (`pending`/`running`/`succeeded`/
  `failed`/`cancelled` — same vocabulary shape as `Agent.Status`), plus the
  brake fields mirrored from `agents/models.py:78-100`:
  `gateway_calls_used`-equivalent is not applicable (one external call, not
  a multi-step plan) but a `started_at`/`elapsed_seconds` pair backs the
  runtime brake, `container_id` (nullable string, the launched Docker
  container's id — see the kill switch below, Codex round-3 HIGH: an
  earlier draft still stored `pgid`/`os.killpg` from before branch 2's own
  round-2 fix moved the CLI invocation inside a container; a local process
  group is no longer the durable thing to track or kill once the CLI runs
  containerized), `cancel_requested` (bool, default `False` — Codex round-3
  MEDIUM: an earlier draft's kill-switch description depended on this field
  without it being listed here), and `failure_reason` (closed
  `TextChoices`: `timeout`, `runner_error`, `budget_exceeded`, `killed`).
- **Invariant: `repo.workspace_id == job.workspace_id`, enforced in
  `HarnessJob.save()` itself, not a `clean()`/DB-constraint backstop**
  (Codex round-1 HIGH raised the invariant; Codex round-2 HIGH: an earlier
  fix's proposed second layer — "`clean()` or a DB constraint" — doesn't
  actually work: `clean()` is never called by `.save()`/`.objects.create()`
  unless a caller explicitly invokes it, and a plain DB check constraint
  can't compare across a joined FK to `repo.workspace_id`). The real,
  single mechanism: `HarnessJob.save()` raises before calling `super().save()`
  if `self.repo.workspace_id != self.workspace_id` — the exact shape
  `Memory.save()` already uses for its own analogous cross-FK invariant
  (`memory/models.py:159`), not a new pattern. `create_harness_job()` (the
  service layer) is expected to never trigger it in normal use, but the
  guarantee is the model-level check, not the service call — tested with a
  direct `HarnessJob(...).save()` bypassing the service layer entirely, the
  same "prove the ORM itself refuses it" test shape `Memory.save()`'s own
  regression test uses.
- **Brakes** (adapting the 4-brake pattern, `agents/execution.py`'s
  reference shape, to a single-call job rather than a multi-step plan):
  1. **Runtime timeout** — `HARNESS_MAX_JOB_SECONDS` (Celery
     `soft_time_limit`, mirrors `AGENT_STEP_TIMEOUT_SECONDS`'s role).
  2. **Cost/budget ceiling** — `HARNESS_MAX_JOB_COST`, checked against the
     CLI's own reported cost. **An agent is only selectable in this phase's
     `agent` choices if its non-interactive output reliably reports a
     usable cost figure** (Codex round-1 MEDIUM: "degrades to timeout-only"
     silently weakens the one brake this phase most needs against a
     runaway/expensive job — instead of a silent degrade, an agent without
     confirmed cost reporting simply isn't offered; Claude Code's
     `--output-format json` reporting `total_cost_usd` is what clears it
     for this phase, confirmed during this brief's plan-review against
     Anthropic's own CLI reference, not assumed).
  3. **Kill switch** — `cancel_harness_job(*, job, actor)`, curator-gated,
     `select_for_update`-fenced across start/running/finalization exactly
     like `agents/execution.py:422-458`'s `cancel_agent` (including its
     round-2 fix: fencing only at start left a stale-worker gap at
     completion — this branch starts from that already-corrected shape,
     doesn't re-derive it) — **plus real container termination, which
     `cancel_agent` never needed, its mechanics pinned precisely enough for
     two implementers to build the same thing, and consistent with the
     Docker containment below, not the pre-container process-group design**
     (Codex round-1 HIGH raised the gap; Codex round-2 MEDIUM pushed for
     precision; Codex round-3 HIGH: an earlier draft's kill mechanics —
     `pgid`/`os.killpg` — were never updated after this same branch's
     round-2 fix moved the CLI invocation inside a Docker container; a
     `docker run` client process exiting is not proof the containerized CLI
     is gone, and a crashed worker leaves no `pgid` for the recovery scan to
     act on either way — the durable, killable, recoverable unit is the
     container, tracked by `container_id`). The precise shape:
     `cancel_harness_job(*, job, actor)` — called from the web request — is
     a **fast, fenced flag flip only, covering every non-terminal status,
     not just `running`** (Codex round-4 HIGH: an earlier draft's flip only
     fired for `job.status == running`, mirroring `cancel_agent`'s own
     "start/resume/finalization" coverage in words but not matching its
     actual behavior — `cancel_agent` cancels any non-terminal state,
     `agents/execution.py:421`, so a `pending` harness job — queued but not
     yet claimed by a worker — could still launch after the owner cancelled
     it). Pinned precisely: under `select_for_update`, if `job.status` is
     `pending`, transition it directly to `cancelled` (nothing has launched
     yet, so there's nothing to signal); if `running`, set
     `HarnessJob.cancel_requested = True` instead (the container needs the
     teardown sequence below, not an instant status flip); any other
     status is a no-op (already terminal). The worker's own claim step
     (whatever picks up a `pending` job to start it) re-checks
     `status == pending` under the same lock immediately before cloning/
     launching — a job cancelled between enqueue and claim is never
     started. `cancel_harness_job` never itself touches Docker (the web
     request process is not the one that started the container and has no
     business touching it directly). The Celery task that actually owns the
     container runs a
     **non-blocking wait loop around the whole container lifetime** — not
     just a check "between phases" (Codex round-3 MEDIUM: an earlier draft's
     "polls between clone → invoke → capture" phrasing meant a cancellation
     during the invocation itself, the one phase that can run long, would
     go unnoticed until it exits on its own — directly contradicting this
     same branch's own mid-run kill-switch test) — polling `cancel_requested`
     every `HARNESS_CANCEL_POLL_SECONDS` (new setting, default `2` — Codex
     round-4 MEDIUM: "every few seconds" wasn't itself a pinned value)
     while the container runs, and on seeing `cancel_requested` set (or on
     the container's own exit for any other reason), tears it down in this
     exact order — Codex round-4 MEDIUM: an earlier draft's ordering could
     be read as removing the container before capturing its output, which
     would leave branch 3's `raw_transcript` with nothing to read:
     1. capture whatever transcript/output the container has produced so
        far (`docker logs`) — always done first, cancelled or not;
     2. `docker stop --time HARNESS_KILL_GRACE_SECONDS <container_id>`
        (new setting, default `10`), escalating to
        `docker kill <container_id>` if still running after that grace
        period;
     3. `docker rm` unconditionally, only after 1 and 2.
     A "no such container" error from step 2/3 is treated as already-gone,
     not a failure. Only after this sequence confirms the container is
     removed does the task finalize the row to `cancelled` (still fenced,
     matching `cancel_agent`'s existing start/resume/finalization coverage)
     — no DB row lock is held during the wait itself, only at the flag-flip
     and at the final state transition, mirroring `cancel_agent`'s own
     short-fenced-flip shape rather than a lock held across a multi-second
     wait. Cancellation is not "done" until this sequence confirms the
     container is gone, and the ephemeral clone directory is still cleaned
     up on this path exactly as on every other exit path.
  4. **Recovery scan** — a job still `running` past
     `HARNESS_MAX_JOB_SECONDS` plus a buffer, never finalized by its own
     worker (crash/OOM/hard-kill), is swept the same way
     `agents`/`memory`/`knowledge`/`tools` already do (shared convention,
     `config/settings/base.py:230-235`'s comment documents the pattern to
     copy) — the sweep uses the job's own `container_id` to run the same
     `docker stop`/`docker kill`/`docker rm` sequence above, rather than
     assuming a dead worker means a dead container (Codex round-3 HIGH: a
     crashed worker leaves an orphaned container running with no in-process
     handle to it at all — `container_id` on the row, not a live process
     reference, is exactly what makes this recoverable).
- **Execution**: a Celery task clones `repo.git_url` at `repo.default_branch`
  **read-only** into an ephemeral, job-scoped directory on the runner (no
  persistent worktree — that's Phase 3's job when writes start), invokes
  the selected CLI agent in its non-interactive/plan/read-only mode with
  the job's `goal`, captures the transcript, deletes the ephemeral
  directory when done regardless of outcome (success, failure, or
  cancellation — no leftover clones on the runner disk). **Process-level
  containment is pinned now, not left implicit in "read-only mode"**
  (Codex round-1 HIGH: a CLI agent's own read-only/plan flag is a
  cooperative setting, not a security boundary against low-trust repo
  content instructing it to do something else):
  - the subprocess is launched via argv array, never a shell string (the
    job's `goal` and repo content are both untrusted text that must never
    reach a `shell=True` interpolation);
  - its environment is an explicit allowlist built for the call, not the
    worker's inherited environment — no ambient secrets beyond exactly what
    the CLI needs (its own auth token/session, nothing else);
  - the git clone's own credential (SSH key / PAT, resolved from env per
    `HarnessRepo`'s auth, not stored on the row) is scoped to the clone step
    only and is not present in the environment handed to the CLI agent
    process itself — the agent gets a checked-out working tree, not
    live credentials to the remote;
  - `HOME`/XDG config dirs are redirected to the job's own ephemeral
    directory, so the invocation can't read or write the runner's real
    `~/.claude`/`~/.codex` config/session state and nothing persists there
    across jobs;
  - no permission-bypass/"skip confirmation" flag is ever passed (e.g. no
    Claude Code `--dangerously-skip-permissions`-class flag, no Codex CLI
    full-auto/write-approval flag) — the whole point of v1 is that the CLI
    agent's own tool-use is confined to read-only tools by the invocation
    itself, not by trusting its output afterward; Codex CLI's `--ephemeral`
    (no persistent session artifacts) is used where available for the same
    reason;
  - the ephemeral clone directory and any job-scoped `HOME`/config dir are
    deleted on every exit path (success, failure, timeout, or the kill
    switch above) — verified by branch 2's own tests, not just asserted.
  - **A filesystem boundary, not just a no-write boundary** (Codex round-2
    HIGH: round-1's fix stops the CLI from writing or persisting session
    state, but running as the worker's own OS user with "read-only tools"
    still lets it *read* arbitrary host paths outside the clone — the
    CLI's own read-only flag is cooperative, not a sandbox). The CLI
    subprocess runs inside a disposable per-job container (the runner's
    Docker daemon — already present in this dev stack), with **only** the
    ephemeral clone directory bind-mounted, **read-only**, and the
    job-scoped `HOME`/config dirs living inside the container's own
    ephemeral filesystem (not bind-mounted from the host, so nothing
    persists once the container is removed). No other host path is ever
    exposed to it. This is the actual "repo-only" guarantee the vision's
    R1 antidote ("worktree dışına yazamaz") requires, and it generalizes
    to a future non-Mac runner the same way, rather than a Mac-specific
    OS sandbox profile that wouldn't. The container is removed
    unconditionally on every exit path (success, failure, timeout, kill),
    tracked by `container_id` (see the kill switch below), not a bare
    subprocess pid — the containment mechanism and the cancellation
    mechanism must agree on what the killable/recoverable unit is.
  - **Container policy, pinned now, not left as an implementation detail
    of "runs inside a container"** (Codex round-3 HIGH: containerizing the
    filesystem boundary raises exactly the questions a bare subprocess
    didn't have — what the container can reach over the network, whose
    image it runs, and whether it can exhaust the runner):
    - **Image**: a single Jarvis-owned Dockerfile per supported agent
      (`CLAUDE_CODE`/`CODEX_CLI`), pinned to a specific, recorded base-image
      digest — never `latest` — built and version-controlled in this repo,
      not pulled from an unpinned third-party image at run time. Bumping
      the pinned digest is a normal reviewed code change, not a runtime
      choice. **Required, pinned in branch 2's own PR rather than
      re-litigated here** (Codex round-4 HIGH: a pinned base-image digest
      alone doesn't make the image reproducible if the Dockerfile installs
      the CLI tool itself via an installer/npm/Homebrew "latest" at build
      time, and a never-rebuilt image silently falls behind the vendor's
      own security fixes — this is a real gap the brief flags but doesn't
      finish designing, since the exact pin mechanism depends on how each
      CLI actually ships): the Dockerfile pins the CLI's own version/
      artifact (not just the base OS image), disables the CLI's runtime
      auto-update/update-check behavior, and branch 2's PR states the
      reviewed bump/rebuild cadence this project will actually follow.
    - **Credential handoff**: the CLI's own auth (Claude Code/Codex CLI
      session or API-key material, resolved from env per O-2) is injected
      into the container as an environment variable or mounted file at
      launch, scoped to that one container's lifetime and never written
      into the bind-mounted clone directory — it disappears when the
      container is removed, same as the job-scoped `HOME`/config state
      above. This is a real credential the containerized process needs to
      run at all (distinct from the git clone credential, which — per the
      bullet above — is used only for the host-side clone step and is
      never handed to the container in the first place).
    - **Network egress**: allowed only to the CLI vendor's own endpoints —
      no general internet access, so a low-trust repo cannot instruct the
      agent to exfiltrate data to an arbitrary host or fetch arbitrary
      remote content. Enforced via Docker network policy (a dedicated
      bridge network with an egress allowlist, or the runner's firewall
      rules scoped to the container), not left to the CLI's own good
      behavior. **The exact endpoint set is a real per-vendor manifest,
      not "the API domain," and is branch 2's own task to pin against each
      CLI's current documentation, not re-derived here** (Codex round-4
      HIGH: a bare-API-domain allowlist can break real auth/telemetry/
      update-check traffic these tools actually make — e.g. Claude Code
      also reaches auth/telemetry/update hosts beyond its core API domain
      depending on mode, and Codex's ChatGPT-plan auth involves its own
      separate auth host, not just OpenAI's API domain). Branch 2's PR
      records, per selected agent and auth mode: the exact endpoint
      allowlist used, and that nonessential traffic (telemetry, bug/error
      reporting, plugin/MCP discovery, auto-update checks) is disabled
      inside the image wherever the CLI exposes a flag/env var to do so,
      preferring API-key/pre-provisioned-session auth over an interactive
      flow the container can't complete anyway.
    - **Resource limits**: CPU/memory/pids-limit set on every container
      launch via standard `docker run --memory`/`--cpus`/`--pids-limit`
      flags — a runaway or hostile process inside the container cannot
      exhaust the runner's CPU/RAM/process table, independent of and in
      addition to the runtime-timeout brake above. Disk is bounded by
      construction rather than a Docker disk-quota flag (Codex round-4 LOW:
      `--storage-opt`-style disk limits are storage-driver-dependent and
      not portable — not worth relying on for v1): the only writable
      space inside the container is the job-scoped `HOME`/config directory
      living in the container's own ephemeral filesystem, itself cleaned up
      on every exit path per this bullet's own rule above, so there is no
      long-lived writable area for disk usage to accumulate against in the
      first place. Verified by branch 2's cleanup tests, not by a disk-quota
      flag.
- **ModelUsage**: one discovery-run row per job that reached subprocess
  launch (per the boundary section's precise rule above), written when the
  job finalizes — a cancelled job still gets this row (`success=False`,
  `error_type="cancelled"`) so cost observability covers every launched
  job, not just completed ones. The separate briefing-call row (branch 3)
  is written only if that call was actually attempted, never fabricated for
  a job that never got that far.
- Tests: FakeAdapter-equivalent for the CLI invocation (a fake subprocess
  runner, never a real `claude`/`codex` call in the test suite — mirrors
  CLAUDE.md rule 5's "LLM-calling code must be mockable" applied to an
  out-of-process call), brake tests (timeout, kill switch mid-run,
  kill switch at completion race, recovery scan), and a real Docker-stack
  smoke test against the actual CLI binary is explicitly **not** part of
  the automated suite (nightly/manual only, same carve-out as rule 5).

### 3. `feat/p2-harness-briefing` — briefing framing + work_log production

- `HarnessJob.briefing` (`JSONField`): `summary`, `findings` (list),
  `raw_transcript` (the CLI's actual output, stored verbatim for audit/
  debugging, never itself injected into a future gateway call without the
  fencing below).
- **R2 mitigation (prompt injection, vision §8), decision pinned now, not
  deferred** (Codex round-1 HIGH: an earlier draft left "gateway call vs.
  CLI-native summary" as this branch's own design task; that ambiguity is
  exactly what the plan-review checkpoint exists to close before
  implementation, and it also directly determines whether a second
  ModelUsage row is ever written, per the boundary section's precise
  per-call rule): `summary`/`findings` are produced
  by an explicit, in-process `InferenceGateway.complete()` call
  (`purpose="harness.briefing"`, its own conditional `ModelUsage` row per
  the boundary section's precise rule) over the fenced `raw_transcript` —
  never taken as-is from the CLI agent's own self-summary. The repo's own
  content
  (README, code comments, issues the CLI agent read) is low-trust input
  that reached the CLI agent, not Jarvis's own reasoning, and the CLI
  agent's own opinion of "what's a fact vs. what's my recommendation" is
  not a boundary Jarvis controls or can verify — routing the actual
  separation through Jarvis's own accounted, versioned, mockable gateway
  call (same `neutralize_fence`/`strip_invisible_formatting` discipline as
  every other injected block, `memory/context.py:341,359`, Codex round-2
  LOW: an earlier draft cited `:120-157`, the wrong range) is what makes
  "the repo/tool said X" vs. "the harness recommends Y" a guarantee Jarvis
  itself produces, not one it takes on trust from an external tool.
  `raw_transcript` itself is still stored (for audit/debugging) but is
  always labeled as external, low-trust data in every render path (UI, any
  future gateway re-consumption) — never presented as the harness's own
  voice, and never itself sent to a future gateway call without the same
  fencing applied fresh each time.
- **`raw_transcript` handling — retention, size, and access, pinned now**
  (Codex round-1 MEDIUM: CLI transcripts can contain repo snippets, stack
  traces, incidentally-printed secrets, file paths, and tool-call metadata
  — storing it with no bound is a real exposure surface, not a formatting
  detail). Bounded to a fixed character cap at write time (truncated with
  an explicit marker past the cap, mirroring the firm-dossier block's
  bounded-line discipline, Phase 1 branch 3) rather than stored unbounded;
  readable only by the same curator roles (owner/admin) gating the rest of
  this app's endpoints — never a wider read audience than the job itself;
  excluded from `AuditLog.metadata` entirely (the audit trail records that
  a job ran and its outcome, never the transcript content, matching
  `audit/models.py:15`'s existing "no secrets in metadata" discipline);
  retained for the life of the `HarnessJob` row (deleted only if the job
  itself is deleted — no separate retention/expiry job invented in this
  phase).
- **`record_work_log()` gains a `sensitivity` parameter** (Codex round-1
  HIGH: the shipped Phase 1 function, `memory/services.py:223`, hardcodes
  `sensitivity=normal` and immediately reconciles embedding on that
  assumption, `memory/services.py:260`; the earlier draft's "normal unless
  the run is marked private" promise was therefore not actually
  implementable against today's signature — this branch changes the
  function, it does not work around it).
  **`HarnessJob.sensitivity` is its own stored field, using
  `Memory.Sensitivity`'s exact string vocabulary
  (`normal`/`private`, `memory/models.py:71`), not `HarnessRepo.sensitive`'s
  bool re-read live** (Codex round-2 HIGH: a bare bool on `HarnessRepo`
  can't be passed straight into a string-choice parameter without a pinned
  mapping, and branch 2's `HarnessJob` field list never actually listed a
  `sensitivity` field despite branch 3 calling `job.sensitivity` — a real
  gap, not just an imprecision. If sensitivity were instead re-derived live
  from `repo.sensitive` at `record_work_log()`-call time, flipping a repo's
  flag later would silently make already-created jobs' outcomes ambiguous
  about what sensitivity governed them). Branch 2's `HarnessJob` model gains
  `sensitivity` (same `TextChoices` values as `Memory.Sensitivity`),
  snapshotted once at job creation via a fixed mapping —
  `repo.sensitive=True → HarnessJob.sensitivity=private`,
  `repo.sensitive=False → normal` — and never re-derived afterward: a job
  carries the sensitivity that was true of its repo *when it ran*,
  immutable from then on, the same "decided once, at creation, durable on
  the row" shape Phase 1 branch 4 already used for
  `overlay_source_workspaces`. `record_work_log(..., sensitivity=
  job.sensitivity)` passes the stored value straight through; a `private`
  result keeps the row out of `cloud_eligible()` (`memory/models.py:41`)
  exactly like every other `private` memory, and Phase 1 branch 1's
  existing embedding-lifecycle rule (no special-casing needed, per the
  bullet below) already handles not enqueuing it. On job success, one
  `work_log` memory row per job — content is the gateway-produced
  `summary`, never the raw transcript (which is exactly the kind of
  untrusted, potentially-large text ADR-005's negative contract and the
  memory char-budget discipline both argue against ever embedding
  wholesale).
- No embedding-lifecycle special-casing needed beyond what Phase 1 branch 1
  already built — a `work_log` row born `approved`+`normal` enqueues for
  embedding immediately under that branch's existing rule.

### 4. `feat/p2-harness-ui` — request + view a briefing

- Minimal API: create a `HarnessJob` (workspace + repo + goal), list/detail
  by workspace, cancel. No harness console redesign (Phase 4) — a plain
  list/detail view mirroring the existing `/workspaces` dossier view's
  level of polish, not the chat shell.
- Frontend BFF routes mirroring the existing `workspaces`/`conversations`
  pattern (`isValidUuid` guards, `rejectCrossOrigin`/`rejectNonJson` on
  writes) — role × endpoint permission tests, workspace-isolation tests
  (a `HarnessJob` from workspace A never appears in workspace B's list,
  same release-gate discipline as Phase 1 branch 4's isolation test).
  Status polling only (no SSE in this phase — a discovery job's transcript
  isn't a token-by-token stream the user watches live like chat; simple
  poll-until-terminal-status is enough for v1's usable outcome). SSE log
  streaming (vision §8 R4) is deferred to whichever phase first needs a
  long-running job's live output, revisited if Phase 3's implementation
  jobs run long enough to need it.
- The UI must render `raw_transcript` (when shown at all) visibly
  distinguished from `summary`/`findings` — same R2 labeling requirement
  as branch 3, now at the presentation layer too.

## Out of scope (explicit)

Any write to a repo (Phase 3) · worktree-per-branch implementation, cross-
vendor review, gated branch push (Phase 3) · deploy gate #2 / VPS deploy
(Phase 5) · harness console / UI redesign (Phase 4) · dynamic runner
registry beyond the single hardcoded `MAC_LOCAL` value (later phase, once a
second runner actually exists) · job-distribution policy beyond "pick the
one configured agent for this job" (a real policy table routing by task
type across Claude Code/Codex CLI/Gemini CLI/Ollama, vision §4, needs at
least two working agents to route between — this phase ships one, verifies
a second) · merging `agents` (MVP 7) and `harness` (O-3, post-Phase-3) ·
Gemini CLI / Ollama as harness agents (follow-up once the policy table
exists) · SSE live log streaming (deferred per branch 4) · any change to
`Workspace.profile` dossier schema.

## Test strategy

Role × endpoint permission tests for every touched surface (CLAUDE.md rule
7); the CLI invocation is fully mockable in the automated suite — no test
depends on a real `claude`/`codex` binary being installed or authenticated
(rule 5, applied to an out-of-process call the same way it applies to
gateway calls, via a fake subprocess runner); migration check
(`makemigrations --check`); `make lint` + `make test` green per branch;
`harness` added to `INSTALLED_APPS` and pytest `testpaths` from branch 1
(Codex round-1 MEDIUM — otherwise its tests exist but never run in CI);
import-linter contract for the new `harness` app checked from branch 1
onward. `ai_core.gateway.record_external_usage()` gets its own direct unit
tests in `ai_core` (Codex round-2 MEDIUM, not just exercised incidentally
through harness's tests): a `provider=None` row is created correctly,
`estimated_cost` is a properly-quantized `Decimal` (Codex round-3 LOW: the
signature and this test both now consistently use `estimated_cost`, and
`request_id` is asserted to come from `core.context.get_request_id()`, not
a caller-supplied parameter), `backend`/`context_report` round-trip as
given, and both `success=True`/`False` + `error_type` paths are covered;
`harness`'s own tests separately assert it never imports
`ai_core.models.ModelUsage` directly. Branch 2's brake tests (timeout, kill
switch including both the completion-race shape and actual container
termination via the flag-flip → task-polls → task-tears-down-the-container
sequence pinned above, keyed on `container_id` — asserted by checking the
container is actually gone via the Docker API, not just that the DB row
flipped to `cancelled`) and the cross-workspace `HarnessJob.save()`
invariant test
(a direct model-level save bypassing the service layer, mirroring
`Memory.save()`'s own regression-test shape) are release gates alongside
branch 4's workspace-isolation test — escalate per the orchestration
protocol's CRITICAL/HIGH checkpoint rule if any of them regresses.

## Risks

- **R1 — Harness authority, even read-only.** v1 only clones and reads; the
  panzehir (antidote) already in this scope: explicit repo allowlist
  (branch 1, off by default), ephemeral clones only, no credential
  persisted on the row or handed to the CLI process, a per-job disposable
  container as the actual filesystem boundary with a pinned image/network/
  resource-limit policy (round-2/round-3 fixes — a bare subprocess with no
  shell/write flags still isn't confined to the repo, and a container
  without egress/image/resource limits isn't either), a container-keyed
  kill switch with precisely-pinned semantics + brake system (branch 2).
  Write/deploy authority is explicitly Phase 3/5, not smuggled in here.
- **R2 — Prompt injection from repo content.** Addressed structurally in
  branch 3/4 (fencing + labeling at every render path), not just a UI
  disclaimer.
- **R3 — Cost.** Budget ceiling brake, gated on confirmed cost reporting
  (branch 2) + a ModelUsage row for every external call actually attempted
  (boundary section) from the first job onward, not retrofitted later.
- **R4 — Long-running jobs.** This phase accepts poll-based status (branch
  4) rather than building SSE log streaming speculatively; revisit if a
  later phase's jobs run long enough to need it.
- **R5 — work_log growth from automated runs.** Every harness job now
  writes a `work_log` row (branch 3) on top of Phase 1's existing
  extraction-derived rows; Phase 1's consolidation/distillation follow-up
  (flagged, not yet scheduled) becomes more relevant once real harness
  volume exists — noted here so it isn't forgotten, not solved in this
  phase.
- **R6 — Vendor terms (O-2).** Codex CLI's automated-use terms are
  verified in branch 2 before it's wired in as a selectable agent, with an
  explicit go/no-go recorded in that branch's PR and this brief's closeout
  — not assumed clear by extension from the existing manual `codex review`
  usage.

## Branch table

| # | Branch | Status |
|---|---|---|
| 1 | `feat/p2-harness-repo-registry` | **done (2026-07-19)** — 5 rounds Codex code-review (R1 P2: pre-lock authorization check bypassable by a forged/stale instance, fixed by locking before authorizing, mirroring `Memory.save()`'s own invariant; R2 P2: `git_url` could embed HTTPS userinfo credentials, contradicting the "no credentials field" design; R3 2×P2: the R2 fix missed query-string/fragment credential carriers, and `urlsplit()` could raise an unhandled 500 on malformed input — user checkpointed here given 3 straight rounds of real findings, chose fix + continue; R4 P2+P3: the R3 query/fragment check didn't cover the scp-like URL branch's early return, and the import-linter contracts only named 2 of 11 apps that must not import `harness`; R5 clean). `HarnessRepo` (curator-gated CRUD across every verb, not just writes) is the explicit repo allowlist harness needs before any CLI-agent work can begin |
| 2 | `feat/p2-harness-runner` | **done (2026-07-20)** — 7 rounds Codex code-review, the densest arc in this repo's history (R1 P1+3P2: job creation never dispatched the Celery task at all — the whole feature was stuck `pending` forever — plus unhandled soft-time-limit, a recovery-scan double-usage race, and a Dockerfile build-context bug; R2 3P2: CLI secrets exposed via `docker run` argv, an unknown container exit code treated as success, no recovery path for a lost dispatch; R3 2P1+2P2 — user checkpointed, chose fix+continue: the container's id/name was only persisted *after* `docker run` returned (a worker crash mid-launch left an unrecoverable orphan), a cancel request landing right as the container exited naturally was silently dropped, worker-side terminal transitions wrote no audit row, and the Dockerfiles pinned a mutable tag instead of a digest; R4 3P2: an unconfirmed CLI cost silently defaulted to `$0` instead of failing the job, `docker run` had no `--pull=never` so a bad image tag would silently fetch from a registry, and a cleanup exception could abort finalization before the real outcome was recorded; R5 1P1+3P2 — user checkpointed again: cloned repos were mode 0700 owned by the worker while both container images run as a different non-root user, meaning every *real* job would have failed before the CLI could read the repo (missed by every mocked test) — plus a Celery soft-limit racing the internal wait-loop deadline, a narrower version of R3's cancellation race, and an unhandled `ProtectedError` on repo deletion once job history exists; R6 3P2: my own R5 permission fix was itself a regression (world-readable clones would leak `sensitive`-repo content on a shared runner or delayed cleanup) — fixed by running the container with `--user` matching the host uid instead of widening permissions — plus undetected `docker rm` failures silently clearing the only container handle, and a stale assertion left over from R5's own new setting; R7 4P2 — user checkpointed a third time and chose **merge now, track as follow-up** given the arc's length and that none of the four are curator-external-attacker-exploitable: `HarnessInvalidTransition` from R5's repo-delete fix isn't actually translated in the view layer (still a 500, not the intended 400); a container whose removal genuinely fails during timeout/cancel is preserved but never actually retried since the recovery scan only sweeps `RUNNING` jobs; the Celery finalization buffer is undersized against real worst-case Docker-control timeouts; and a curator-supplied `git_url` starting with `-` could be parsed by git as an option instead of a repository argument. No CRITICAL in any of the 7 rounds. `HarnessJob`, its full brake system (timeout/cost/kill-switch/recovery), the Docker-based CLI runner, and `ai_core.record_external_usage()` all shipped; the 4 round-7 items are tracked as an explicit follow-up, not silently dropped |
| 3 | `feat/p2-harness-briefing` | **done (2026-07-20)** — 5 rounds Codex code-review, user checkpointed at round 5 given the arc length (no CRITICAL/HIGH after round 2, chose merge-now): R1 P1: the briefing's gateway call ran *before* the locked terminal status save/usage record, so a Celery soft-limit interrupting that unbounded-latency call would discard the job's real outcome — the terminal save now happens first, briefing production is a best-effort addendum after; R2 P1: a malformed/wrong-shape gateway reply fell back to trusting the raw model text as the summary, which would smuggle a prompt-injected transcript echo straight into an auto-approved work_log — now yields an empty summary (no work_log), never the untrusted text; R3 P2+P3: even a validly-shaped reply's `summary`/`findings` were unbounded (capped now, same write-time discipline as `raw_transcript`), and `raw_transcript` itself was storing the fenced/neutralized copy instead of the actual CLI output (fixed — neutralization now applies only to a separate prompt-only copy); R4 P2+P3: an AI-provider outage or missing prompt template lost the only stored transcript along with the summary (transcript is now always persisted when one exists, independent of summarization success), and the import-boundary regex only matched a bare `import ModelUsage` (now catches mixed/aliased import forms too); R5 P2: the round-4 fix still lost the transcript if the process was killed *during* the gateway call itself, not just on a clean failure — transcript is now saved before the call, overwritten with the real summary after. `HarnessJob.briefing`, `record_work_log()`'s new `sensitivity` param, and the fence→gateway→parse→persist pipeline all shipped. Rebased onto branch 2's round-7/8 follow-up fixes before merging; full backend suite, migration check, and import-linter all green post-rebase |
| 4 | `feat/p2-harness-ui` | **done (2026-07-20)** — 2 rounds Codex code-review: R1 P2: `HarnessJobDetail`'s raw-transcript disclosure was nested under a summary/findings check, so a job whose briefing gateway call failed (branch 3's own `_finalize()` deliberately preserves `raw_transcript` in that case) showed "no briefing" instead of the transcript curators need to debug why summarization didn't run — fixed by including `raw_transcript` in the same predicate; R2 clean. `HarnessJobListCreateView`/`HarnessJobDetailView`/`HarnessJobCancelView` (curator-gated like the repo registry), `HarnessJobSerializer`/`HarnessJobDetailSerializer` (list stays light, detail adds `briefing`), BFF routes mirroring `tasks`/`agents`'s pattern, and a plain list/detail view (`HarnessJobsView`/`HarnessJobDetail`) mirroring `AgentsView`/`AgentDetail`'s polish level all shipped. Manual browser verification (not just automated tests) caught one real bug before Codex ever saw it: `job.briefing` is `{}` for any non-terminal job (backend model default), and an earlier version of `HarnessJobDetail` assumed `briefing.findings` was always an array, crashing `.length` on `undefined` — fixed and covered by a regression test. This completes Phase 2 — all 4 branches shipped |
