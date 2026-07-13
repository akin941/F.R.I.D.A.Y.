# MVP 7 — Agent Runtime: Goal to Multi-Step Plan

> Status: **plan approved (2026-07-11)** — 6 rounds of Codex plan-review
> (round 1: 1 CRITICAL + 3 HIGH + 2 MEDIUM + 1 LOW; round 2: 1 HIGH + 1
> MEDIUM remaining; round 3: 1 MEDIUM + 1 LOW remaining; round 4: 2 HIGH;
> round 5: 1 MEDIUM; round 6: 0 findings, APPROVE), converging on a real
> autonomy-boundary fix (task_draft steps must pause for an explicit human
> confirm, never auto-create) plus several implementation-correctness
> fixes (kill-switch fencing at start/resume/finalization, gateway-call
> budget accounting, approval provenance, an agents-local task-draft
> confirm endpoint). Every "Resolved during plan-review" note in Risks
> below is a deliberate decision, not an oversight — do not relitigate
> without a new reason. Not yet implemented — branch 1 has not started;
> awaiting the user's explicit go-ahead per standing protocol (never start
> a new MVP/branch without it).
>
> Scope-approval note from the user (2026-07-10): the four "brakes" below —
> plan step-count bound, execution-time brakes (timeout / consecutive-
> failure halt / resource budget), a backend-enforced kill switch, and
> step-by-step visibility — are the user's explicit condition for
> approving this MVP's autonomy increase. They are not nice-to-haves; if
> plan-review or implementation ever proposes weakening one of them,
> surface it to the user rather than resolving it unilaterally. The user
> named this "Jarvis's riskiest MVP so far."

## Goal and user value

Every capability Jarvis has shipped through MVP 6 requires a human to
either click a button or type one message per action — including the
router (MVP 6), which is explicitly one utterance → one classified intent
→ one dispatched action, always inside the same turn the human is present
for. This MVP adds the other half of the roadmap's `router → agents` line
(`docs/MASTER_PLAN.md` §L): given a **goal**, Jarvis plans a **sequence of
steps** and executes them **without requiring the human to type each one**.

This is a real increase in autonomy, and it is scoped narrowly on purpose:
the approval gate (ADR-004) that already exists is not rebuilt, bypassed,
or given a new "agent" exception — every step this MVP can produce that
would write, delete, deploy, or send externally goes through the exact
same `ApprovalRequest` machinery a human-typed request already goes
through today (MVP 5). What's new is only the *planning and sequencing*
layer sitting above that gate, plus the brake systems this brief adds
specifically because that layer can now act across multiple steps without
a human re-confirming intent between each one.

## Router vs. agents: applying the boundary MVP 6 already drew

`docs/prompts/mvp-06/brief.md`'s "Router vs. agents" section already
resolved this from ADR-004 and the MVP 4/5 briefs, not from scratch here:

- **Router (MVP 6, shipped)**: one utterance → one classified intent → one
  dispatched action, inside one turn, writes always requiring an explicit
  follow-up click. No memory of "goals," no chaining.
- **Agents (this MVP)**: given a goal, plans and executes **multiple**
  steps over time, potentially without a human typing each one. This is
  what ADR-004's "agent-requested" (as opposed to "human-requested")
  language, and the MVP 5 brief's "before MVP 7 gives an agent the ability
  to request one," were both written in anticipation of.

Concretely: a router turn recognizes "toplantı ekle" and asks "create
*this* task?" — one utterance, one confirm. An agent given the goal
"haftalık rapor sürecimi düzenle" might plan "check knowledge base for
last week's report → draft a task → request the vault export tool" as
three sequenced steps, executing the first step unprompted and only
pausing where a step is itself gate-worthy (§4 below) — this is the line
this MVP is allowed to cross that MVP 6 deliberately did not.

## Scope

### 1. Agent & plan model (`agents` — new app, foundation, no dispatch yet)

New Django app `agents` (mirrors how `tasks`/`tools`/`approvals` were each
introduced as a focused, single-purpose kernel app rather than folded into
an existing one).

- `Agent` (workspace-scoped, `WorkspaceScopedModel` + `TimestampedModel`,
  same base classes every other business model already uses): `goal`
  (text, the human's original request), `status`
  (`planning` / `running` / `blocked_on_approval` / `blocked_on_confirmation`
  / `completed` / `failed` / `cancelled`), `created_by` (the real human who
  submitted the goal — see §3's provenance fix for why this actor matters
  beyond attribution), `gateway_calls_used` (integer, default 0 — the
  resource-budget counter; see §3's revised budget mechanism).
- `AgentStep` (FK to `Agent`, ordered via a plain integer `order` field —
  no separate DAG/dependency-graph structure; steps execute strictly in
  sequence, which is deliberately the simplest correct shape for a first
  agent runtime, not a workflow engine): `kind` (`read` — a dedicated
  `purpose="agent.read_step"` gateway call, no gate needed, see §3's
  revised definition — or `tool_request` / `task_draft` — each pauses for
  an explicit human action before its write happens, see §3's revised
  "gate-worthy steps" below; neither ever writes purely on the engine's own
  advance), `status` (`pending` / `running` / `succeeded` / `failed` /
  `blocked_on_approval` / `blocked_on_confirmation` / `skipped`), `payload`
  (opaque, tool/task-specific, same "never used to resolve scope" rule
  `ApprovalRequest.payload` already follows), `approval_request` (nullable
  FK, set only for `tool_request`-kind steps once created), `result` (small
  JSON summary, not a full transcript).
- **Plan step-count bound (user's brake #1).** A hard, configuration-driven
  maximum step count (a Django setting, e.g. `AGENT_MAX_PLAN_STEPS`,
  defaulting to a small number — proposed 10, open to plan-review) enforced
  at plan-creation time, before any `AgentStep` row is even written: if the
  planner call (§2) returns more steps than the bound, the **entire plan**
  is rejected and the `Agent` goes straight to `failed` with a
  user-visible reason — never silently truncated to the first N steps,
  since silently dropping steps would let the agent claim a goal is
  handled when part of the plan was actually discarded. Mirrors MVP 6's
  "classification failure always degrades cleanly, never corrupts state"
  discipline, applied to plan size instead of parse shape.
- No dispatch side effects in this branch — creating an `Agent`/its steps
  never calls `approvals.services.create_request`, `tasks.services.
  create_task`, or any tool. That is branch 3.

### 2. Planner (`agents` — one gateway call, still no dispatch)

A new `purpose="agent.plan"` gateway call (same `InferenceGateway.complete`
door every other purpose already uses — ADR-003, CLAUDE.md rule 2/3),
schema-constrained JSON output analogous to `router.intent`/
`router.task_draft`'s existing discipline: given the goal text, returns an
ordered list of `{kind, description, payload}` steps.

- Parse/schema failure or a `GatewayError` fails the **whole plan**
  (`Agent.status = failed`) — no partial-plan fallback, no silent
  degrade-to-single-step. Unlike the router's "unclear message → treat as
  chat" default (a safe no-op), an agent that can't produce a clean plan
  has nothing safe to fall back to that still matches the user's stated
  goal, so failing loudly is the correct default here.
- **A plan with zero steps also fails, rather than marking the `Agent`
  `completed` (Codex round-1 LOW fix — this case was unstated).** A
  goal that parses cleanly into an empty step list most likely reflects a
  planner malfunction or an unplannable goal, not a legitimately "already
  done" request; treating it as a silent success would be indistinguishable
  from a real, executed plan in the visibility UI (§5) and would defeat the
  whole-plan-fails-clean discipline this section otherwise establishes.
- `tool_request`-kind steps are validated against
  `tools.registry.list_tool_names()` (the same check MVP 6 branch 4 added)
  at planning time — an unresolvable tool name fails the plan for the same
  reason a malformed shape does, not a per-step skip.
- New `seed_ai_defaults` prompt entry (`jarvis_agent_plan`), following the
  same versioned-prompt pattern every gateway purpose already uses.

### 3. Execution engine (`agents` — the brake systems live here)

A Celery task (mirrors `memory.jobs`' lease-fenced claim pattern and
`tools.export`'s `create_execution`/`dispatch` ordering — no new
concurrency primitive invented) that advances an `Agent` one `AgentStep`
at a time:

- **Read steps execute unattended, through one concretely-defined call
  (Codex round-1 MEDIUM fix — "reuses MVP 6 read-dispatch" was not concrete
  enough, since that framing lives inside the chat turn's context builder,
  not a standalone callable).** A `read`-kind step calls a new
  `agents.execution.run_read_step(step)`, which runs `knowledge.context.
  select_context_chunks` (the same retrieval function MVP 6 already calls,
  used directly here rather than through `assistant.context.build_context`,
  since there is no chat turn to build context for) followed by one
  `purpose="agent.read_step"` gateway call that summarizes the retrieved
  chunks against the step's `description` into a short `result` string.
  **A read step makes two gateway calls, not one, and both are bounded
  (Codex round-4 HIGH fix — the original wording only accounted for the
  summarization call; `select_context_chunks` itself already calls
  `InferenceGateway.embed(purpose="knowledge.retrieval")` to embed the
  query, per `backend/knowledge/context.py`, so undercounted the real
  spend).** `run_read_step` is the unit both timeout and budget brakes
  bound as a whole — the timeout window (below) covers the full call from
  `select_context_chunks` through the summarization call returning, and
  `Agent.gateway_calls_used` (§1) is incremented by the actual number of
  gateway calls that specific invocation made (2 in the normal case: one
  `knowledge.retrieval` embed, one `agent.read_step` completion) inside the
  same transaction that commits the step's result — not a flat `+1` per
  step. This is the only unattended gateway spend this MVP produces.
- **Gate-worthy steps always pause for an explicit human action before any
  write happens — never purely on the engine's own advance (Codex round-1
  CRITICAL fix).** The original draft let `task_draft`-kind steps call
  `tasks.services.create_task` directly on the engine's advance, with no
  human action at all. That is a real gap: even MVP 6's `task_draft` path
  — where `Task` creation is *not* in ADR-004's gated risk class (per MVP
  6 §3's own amendment) — still always required an explicit user confirm
  click before creating the row. This MVP must not quietly remove that
  click just because the *initiator* is now an agent instead of chat. The
  two `AgentStep` kinds now pause differently, matched to what each one
  actually needs:
  - **`tool_request`-kind**: the engine calls `approvals.services.
    create_request` on its own advance (this *is* an ADR-004-gated action,
    so the formal gate is the correct pause point) — `requested_by` is set
    to `Agent.created_by` (**Codex round-1 HIGH fix — provenance**: the
    audit trail must trace to the real human who submitted the goal, never
    a synthetic "agent" identity; `requester != decider` is enforced
    exactly as today, so that same human still cannot decide their own
    agent's request). The step and `Agent` move to `blocked_on_approval`;
    the engine does not advance until that specific `ApprovalRequest`
    resolves (approved → step succeeded, advance; rejected → step failed,
    counts toward the failure brake below).
  - **`task_draft`-kind**: the engine does **not** call `tasks.services.
    create_task` on its own advance. Instead the step and `Agent` move to
    `blocked_on_confirmation` — a lighter, `agents`-local pause (no
    `ApprovalRequest` row, since `Task` creation genuinely isn't in ADR-004's
    risk class) that still requires one explicit human click, surfaced on
    the `/agents` detail page (§5) as a confirm/discard control analogous
    to MVP 6's `TaskDraftCard`. **Confirmation goes through a new
    `agents`-local endpoint, not the existing tasks API's `draft_message_id`
    mechanism (Codex round-4 HIGH fix — that mechanism only produces
    `source=ai_suggested` when `draft_message_id` resolves to a real
    `assistant.Message` row, per `tasks/views.py`/`tasks/serializers.py`;
    an `AgentStep` is not a `Message` and was never going to satisfy that
    check).** A new view (e.g. `POST /api/agents/steps/<id>/confirm`)
    validates the `AgentStep` server-side — same actor/workspace as the
    `Agent`, `kind=task_draft`, `status=blocked_on_confirmation` (the
    `AgentStep` itself *is* the server-verified reference here, playing the
    same role `draft_message_id`'s resolved `Message` plays in MVP 6 — no
    client-asserted `source` either way) — and only then calls
    `tasks.services.create_task` directly, server-side, with
    `source=Task.Source.AI_SUGGESTED` (mirroring MVP 6 §3's provenance
    field, reached by a different, agent-shaped path rather than by
    extending the existing chat-message-based endpoint to accept a second,
    unrelated kind of reference). Only that confirm call ever creates the
    `Task` — never the engine unattended. Discarding (a sibling
    `POST .../discard`, or a single endpoint with an action field — an
    implementation-level choice, not a scope one) marks the step `skipped`
    and the agent advances to the next step normally (a discarded task
    draft is not a step *failure*, unlike a rejected tool request, since
    nothing gated was denied — the human just chose not to create that
    task).
  Both pause states are surfaced identically by the visibility brake
  below — "what's waiting on me" always means a concrete pending human
  action, whether that's a formal approval decision or a lighter confirm
  click.
- **Per-step timeout (user's brake #2a).** Each step has a bounded
  execution window (setting, e.g. `AGENT_STEP_TIMEOUT_SECONDS`) covering
  the actual work (the full `run_read_step` call — embedding *and*
  summarization, per the round-4 fix above — for a read step; the local
  `create_request`/pause-transition for a gate-worthy step) — not the
  human approval/confirmation wait itself, which is intentionally unbounded
  up to `ApprovalRequest`'s own existing expiry sweep (MVP 5 branch 1;
  `blocked_on_confirmation` has no analogous expiry in this MVP — see Out
  of scope). A step that exceeds its execution window is marked `failed`
  with a timeout reason, not left `running` forever.
- **Consecutive-failure halt (user's brake #2b), retry count finalized —
  zero (Codex round-1 MEDIUM fix: this was left open, making the brake's
  own tests ambiguous).** A per-agent counter of consecutive step
  failures; on reaching a bound (setting, e.g.
  `AGENT_MAX_CONSECUTIVE_FAILURES`, proposed 2), the whole `Agent` moves to
  `failed` and the engine stops advancing — no unbounded retry loop, no
  "keep trying the rest of the plan after the thing it depended on already
  broke." A step is not retried internally at all: it fails once and
  counts toward this bound, full stop — the simplest correct baseline
  (mirrors MVP 6's "blocking classification latency accepted as v1"
  reasoning), settled now rather than left open for a future round to
  relitigate.
- **Resource/token budget (user's brake #2c), mechanism replaced (Codex
  round-1 HIGH fix — the original "query `ModelUsage`" approach has no
  agent/step reference on that model, so concurrent agents' usage would
  contaminate each other's totals, and a pre-call check alone cannot bound
  a call whose actual cost is only known after it returns).** `Agent.
  gateway_calls_used` (§1) is an integer counter local to that one `Agent`
  row, incremented by the actual number of gateway calls `run_read_step`
  made (2 in the normal case — embedding, then summarization; see the
  round-4 fix above) inside the same transaction that commits a
  `read`-kind step's result (no new relationship to `ModelUsage` needed —
  this branch counts *calls*, not tokens, for v1 simplicity; a token-based
  ceiling is a natural v2 refinement, not blocking this brief). Before
  starting a **new** read step, the engine checks `gateway_calls_used`
  against a bound (setting, e.g. `AGENT_MAX_GATEWAY_CALLS`); if already at
  or over it, the agent halts the same way the failure-count brake does —
  the check is a pre-condition on the *next* call, exactly mirroring how
  the kill switch and timeout brakes both let an already-started step
  finish but never start another. **Bounded one-unit overshoot, made
  explicit (Codex round-5 MEDIUM fix — the check as written only compares
  `gateway_calls_used` against the bound, so a step starting at
  `used == max - 1` would still run and push usage one unit past the
  cap before the *next* check catches it).** This overshoot is deliberate,
  not a bug to close by requiring headroom for a whole `run_read_step`
  unit up front (which would require hardcoding "2" as a worst-case
  call count into the check itself, brittle if a future read-step
  implementation ever changes its call count) — it is the same shape as
  the timeout and kill-switch brakes, which likewise let an
  already-started unit of work finish rather than aborting mid-call. The
  bound is therefore "the agent will not *start* a read step once at or
  over the cap," not "the agent's usage will never exceed the cap by more
  than one step's worth" — `AGENT_MAX_GATEWAY_CALLS` should be read as
  that soft ceiling, and its test (§ Test strategy) asserts the
  one-step overshoot explicitly rather than treating it as a bug. This
  exists specifically because
  `read`-kind steps execute **without** a per-step human gate — the one
  place in this MVP an agent can spend real gateway cost unattended, so it
  needs its own explicit ceiling rather than relying on the approval gate
  to naturally throttle it.
- **Kill switch (user's brake #3, critical), race closed (Codex round-1
  HIGH fix — the original draft required the engine to "re-check status"
  without specifying that the check and the advance must be atomic, so a
  cancel could still race a resume-from-approval and lose).** A
  `cancel_agent(agent)` service function, callable at any time regardless
  of current status (`planning` / `running` / `blocked_on_approval` /
  `blocked_on_confirmation`): sets `Agent.status = cancelled` immediately.
  The step-advance function that starts a new step or resumes one after an
  `ApprovalRequest`/confirmation resolves wraps its status check and the
  transition into `running` in one `transaction.atomic` block using
  `Agent.objects.select_for_update()` — the exact lock-then-verify shape
  `tools.export._fenced` already uses for `VaultExportRun` (`backend/
  tools/export.py`) — so "check not cancelled" and "advance to the next
  step" can never be split by a concurrent `cancel_agent` call landing in
  between; whichever transaction commits first wins, and the loser's
  advance is refused rather than racing through. Per the user's framing:
  "worker finishes the step it's on but does not advance to the next one"
  — an in-flight step's own gateway call or `create_request` call is
  allowed to complete cleanly (never killed mid-write, which could leave a
  half-created `Task`/`ApprovalRequest`), but no *subsequent* step ever
  starts once cancelled, including one resuming from an approval/
  confirmation that resolved after the cancellation.
  **Finalization is fenced too (Codex round-2 HIGH fix — round 1's fencing
  only covered starting/resuming a step, not a running step's own
  completion, so a stale worker finishing `run_read_step` after a
  cancellation landed could still overwrite `Agent.status` or advance to
  the next step).** The same `select_for_update`-guarded transaction shape
  applies to a step's *completion* write, not just its start: marking a
  `read`-kind step `succeeded` (and incrementing `gateway_calls_used`) or
  a gate-worthy step's dispatch call both re-lock the `Agent` row, and if
  `Agent.status` already reads `cancelled` by that point, the step's own
  result/status update is still persisted (the gateway call already ran;
  its cost and result are real and are not thrown away), but the engine
  does not treat this as "advance" — the `Agent` stays `cancelled` and no
  next step is ever enqueued. Concretely: every place §3 says "the engine
  checks status before starting/resuming a step" now also means "and
  before treating that step's own completion as a green light to enqueue
  the next one" — one fencing rule applied at both boundaries of a step
  (start and finish), not just the start. This is a backend
  guarantee this branch must have tests for — the branch 5 frontend button
  is a thin client of `cancel_agent`, not where the guarantee lives.
- **Visibility (user's brake #4).** `Agent`/`AgentStep`'s own status fields
  (already listed above) are the visibility mechanism, not a separate
  logging system: at any moment, reading an `Agent` and its ordered
  `AgentStep`s already answers "which step is it on," "what's next," and
  "what's waiting on me" (`blocked_on_approval` steps carry their
  `approval_request` FK into MVP 5's existing `/approvals` page;
  `blocked_on_confirmation` steps carry their own draft fields for the
  `/agents` page's confirm control, §5). This branch's job is making sure
  those fields are updated transactionally and promptly enough (each
  step's status change commits before the next step starts, inside the
  same `select_for_update`-guarded transaction the kill switch uses) that a
  polling read is never stale by more than one step. §5 (frontend) is what
  actually renders this to the user; this branch just guarantees the
  underlying state is truthful in real time.

### 4. Self-modification guarantee (`agents` — the ADR-004 test)

A focused, deliberately small branch whose entire purpose is proving the
hard rule: **agents can never modify their own definition or permissions**
(ADR-004; also CLAUDE.md rule 4: "Agents can never modify their own
definitions or permissions. Coder and reviewer agents are never the
same.").

- Audit/test that no code path reachable from `AgentStep` execution can
  write to: the `agents` app's own models beyond its own `Agent`/
  `AgentStep` rows (an agent cannot grant itself more steps than its
  original plan, cannot edit a step's `kind` after creation, cannot create
  a *second* `Agent` from inside a running one's execution — no
  self-spawning), the `tools` registry, `approvals` role/permission rules,
  or any Django code/settings/prompt content. Concretely: `AgentStep.kind`
  and `payload` are set once at plan-creation time (§2) and never mutated
  by the execution engine (§3) — only `status`/`result`/`approval_request`
  change during execution.
- This is intentionally not a new runtime sandboxing mechanism — it is a
  design constraint (the engine's code simply has no method that writes to
  those surfaces) plus tests that would fail if someone later added one.
  Mirrors how MVP 5's "coder and reviewer agents are never the same" rule
  is enforced by process (this repository's own Codex-review discipline),
  not by a runtime check.

### 5. Frontend

- An `/agents` page: list of `Agent` rows (goal, status), and a detail view
  per agent showing its ordered `AgentStep` list with each step's status —
  the direct rendering of §3's visibility brake. A `blocked_on_approval`
  step links to its `ApprovalRequest` on `/approvals` (existing page,
  existing own-request gating — MVP 6's `tool_request` card already
  established this exact "confirm here, decide there" split for a human-
  requested tool call; an agent-requested one reuses the same split). A
  `blocked_on_confirmation` step renders its own confirm/discard control
  inline on the `/agents` detail page (mirrors MVP 6's `TaskDraftCard`,
  §3's revised task-draft handling) — confirming calls the new
  `agents`-local confirm endpoint (§3), not the existing tasks API,
  discarding marks the step `skipped` and lets the agent advance.
- A **cancel** control on a running/blocked `Agent`, calling `cancel_agent`
  (§3) — the UI button is a thin trigger; the guarantee it relies on is
  backend-enforced (§3), not client-enforced.
- A goal-entry form (plain text goal → `POST` creates the `Agent` and
  triggers planning). No inline plan editing before execution in this MVP
  (mirrors MVP 6's "confirm or discard only, no inline edit" precedent) —
  if the plan looks wrong, cancel and re-submit a clearer goal.

### 6. E2E + docs

- Playwright smoke: submit a goal that plans to a read step + a
  `task_draft` step + a `tool_request` step (using `vault_export`, the one
  registered tool — see Out of scope), confirm the read step completes
  unattended, the task-draft step reaches `blocked_on_confirmation` and
  confirming it on `/agents` creates a real `Task` (`source=ai_suggested`),
  the tool step reaches `blocked_on_approval` and shows up on `/approvals`.
  **What the smoke test's approval step does and does not prove (Codex
  round-3 MEDIUM fix — round 2's fix named the mechanism but left it
  ambiguous whether it actually demonstrates `requester != decider`).**
  Since `requested_by=Agent.created_by`, the goal-submitting actor is
  blocked from deciding their own agent's request exactly as a human's own
  manual request is blocked today (ADR-004) — the e2e smoke test does
  **not** attempt to prove this rule itself. It approves via
  `DEV_ALLOW_SELF_APPROVAL` + an in-page fetch, the same DEBUG-only
  same-actor bypass MVP 5's own smoke section already uses for its
  manually-created `vault_export` requests — a deliberate escape hatch for
  end-to-end convenience, not a distinct-actor arrangement, and not itself
  a test of the permission rule. The actual `requester != decider`
  enforcement for agent-requested approvals is proven the same way it
  already is for human-requested ones: a backend test (§ Test strategy)
  asserting `Agent.created_by` cannot decide the `ApprovalRequest`
  `create_request` produced, with `DEV_ALLOW_SELF_APPROVAL` off. Approving
  in the smoke test lets the agent proceed to `completed`. A second smoke
  path proves the kill switch: cancel a `blocked_on_approval` agent, then
  approve the now-orphaned `ApprovalRequest` anyway (same
  `DEV_ALLOW_SELF_APPROVAL` mechanism, mirroring how a human could still
  click approve after the requester cancelled) and assert the `Agent`
  stays `cancelled` and no further step ever starts.
- `docs/MASTER_PLAN.md`: new §W (mirrors §T/§U/§V's structure), §L roadmap
  line update (`agents *(done)* → skills → integrations`), §G ("Agent
  Runtime," currently an empty header) filled in with this MVP's summary.

## Out of scope (explicit)

- **A second registered tool.** `vault_export` remains the only one, per
  the user's explicit instruction ("ikinci tool eklemeden önce tek tool'la
  deseni kanıtla" — prove the pattern with one tool before adding a
  second). `tools.registry.list_tool_names()` already returns a list for a
  reason (MVP 6 branch 4) — this MVP doesn't need a second entry to prove
  planning/sequencing against it.
- **Branching/conditional plans, replanning mid-execution, or a DAG of
  steps with dependencies.** Steps execute strictly in the order the
  planner produced; if a step fails, the consecutive-failure brake halts
  the whole agent rather than the engine trying to route around it or
  re-plan. Re-planning after a failure is a natural future enhancement,
  not this MVP.
- **Auto-approval of any gated step**, regardless of the requester being
  an agent instead of a human. ADR-004's "never for the hard rules"
  applies identically; a `tool_request`-kind step always ends at the same
  pending `ApprovalRequest` state a human-typed one would.
- **Agents creating or modifying other agents.** No self-spawning, no
  agent-of-agents hierarchy (§4). A goal that seems to need "sub-goals" is
  a future MVP's problem, not solved by nesting `Agent` rows here.
- **Cost-based or confidence-based dynamic step budgets.** The brakes in
  §3 are fixed, configuration-driven ceilings, not adaptive/learned
  limits — deliberately the simplest correct baseline (mirrors MVP 6's
  "blocking classification latency accepted as v1" reasoning).
- **A general workflow/orchestration engine reusable outside agents.** The
  ordered-`AgentStep`-list executor is intentionally agent-specific, not a
  generic job-DAG runner other apps could adopt.
- **An expiry sweep for `blocked_on_confirmation` steps.** Unlike
  `ApprovalRequest` (which has an existing expiry sweep, MVP 5 branch 1),
  a task-draft step awaiting confirmation stays pending indefinitely until
  the human confirms, discards, or cancels the whole agent — no new
  Celery-beat job this MVP. If stale confirmations become a real problem
  in practice, an expiry sweep mirroring `ApprovalRequest`'s is a natural,
  separately-scoped follow-up.

## Branch plan

| # | Branch | Contents |
|---|---|---|
| 1 | `feat/mvp7-agent-model` — **done (2026-07-11)** | `agents` app scaffold, `Agent`/`AgentStep` models + migration, `create_plan()` (step-count bound + empty-plan both fail clean with `Agent.failure_reason`, no dispatch). 2 Codex review rounds (1 MEDIUM + 1 LOW round 1: missing failure reason, missing `AgentStep.for_actor()`; 0 findings round 2). Merged `main` @ `6ddad6b`. |
| 2 | `feat/mvp7-planner` — **done (2026-07-11)** | `purpose="agent.plan"` gateway call + schema parser, `seed_ai_defaults` entry, tool-name validation against the registry, whole-plan-fails-clean on any parse/schema/gateway error (`failure_reason=planner_error`, distinct from `finalize_plan`'s `plan_too_large`/`plan_empty`). 3 Codex review rounds (1 MEDIUM + 2 LOW round 1: loose date validation, missing tool vocabulary in the prompt, coverage gap; 1 LOW round 2: config validated after Agent creation; 0 findings round 3). Merged `main` @ `609fb50`. |
| 3 | `feat/mvp7-execution-engine` — **done (2026-07-13)** | Celery step-advance task, `run_read_step` (`knowledge.context.select_context_chunks` embed + `purpose="agent.read_step"` summarize, both counted), `tool_request` steps pausing at `blocked_on_approval` via `approvals.services.create_request` (`requested_by=Agent.created_by`), `task_draft` steps pausing at `blocked_on_confirmation` with a new agents-local confirm/discard endpoint calling `tasks.services.create_task` server-side, per-step timeout, consecutive-failure halt (zero retries), `Agent.gateway_calls_used` budget brake, `select_for_update`-guarded `cancel_agent` kill switch (covering start, resume, and finalization), real-time status fields (visibility), plus a full recovery scan (`agents.tasks.recover_stale_agents`) reclaiming worker-lost steps, re-dispatching lost-broker-publish agents, and resuming agents stuck on a decided (approved/rejected/expired) approval. **7 Codex review rounds** — the riskiest branch in this MVP, matched with the most scrutiny: round 1, 2 MEDIUM (duplicate-delivery could skip an in-flight step; confirm-endpoint 500 instead of 403 on a task-service permission error); round 2, **2 HIGH** (no recovery path at all for a lost dispatch or a worker-death-stranded step) — escalated to the user, who directed building the recovery scan properly rather than accepting the gap; round 3, 1 MEDIUM (a duplicate-delivery race could bypass the consecutive-failure brake in the gap between a step's finalize and its halt-decision — fixed by making both one transaction); round 4, 1 MEDIUM (the approval-resume signal had the identical non-durable-`on_commit` gap round 2 fixed for dispatch/step-finalization — recovery scan extended to cover it); round 5, **1 HIGH** (a genuine kill-switch bypass: cancelling an agent mid-planning could be silently overwritten once the planner's gateway call returned, and the resurrected agent could actually start executing) + 1 MEDIUM (the approval-expiry sweep's decision wasn't covered by round 4's recovery query, since expiry never sets `decided_at`) — both escalated, both fixed; round 6, 1 MEDIUM (same atomicity-gap pattern as round 3, this time in the approval-resume path); round 7, 1 MEDIUM (an unexpected non-timeout error left a claimed step `running` for the full recovery-grace window instead of resolving immediately) — user chose to close the loop here rather than run an 8th round. Merged `main` @ `a5c634f`. |
| 4 | `feat/mvp7-self-mod-guarantee` — **done (2026-07-13)** | ADR-004 self-modification test suite. A deliberately small branch: an audit of the merged execution engine found no existing write path violating the guarantee, so no new sandboxing code was needed — the branch is entirely tests, both behavioral (full multi-kind-step lifecycles asserting `AgentStep.kind`/`payload` and `Agent` row count never change; `tools.registry.register_tool`/`unregister_tool` and `approvals.services.approve_request`/`reject_request` monkeypatched to raise if ever called from within the `agents` app) and static cross-checks (`inspect.getsource` scanning `agents/execution.py` for absence of `AgentStep(`/`Agent.objects.create`/`ApprovalRequest.objects.create` construction, and confirming `tasks.services.create_task` is only reachable from the human-click confirm endpoint, never the unattended claim/advance path). **1 Codex review round — 0 findings, APPROVE.** Merged `main` @ `b24756a`. |
| 5 | `feat/mvp7-frontend-e2e` | `/agents` page, cancel control, goal-entry form, Playwright smoke (happy path + kill-switch path), MASTER_PLAN + brief closeout |

Branch 4 depends on branch 3 existing (there must be a running engine to
test the guarantee against) but is otherwise a narrow, independent audit
pass — it does not block branch 5 from starting once branch 3 is merged,
though finishing branch 4 before merging branch 5 is still the safer order
given what's being proven.

## Test strategy

- Agent/plan model: step-count-bound rejection test (over-limit plan never
  creates any `AgentStep` row, `Agent.status = failed`), immutability of
  `kind`/`payload` after creation.
- Planner: schema-parsing tests mirroring `router.intent`/`task_draft`'s
  own (valid shape, malformed JSON fails the plan, gateway error fails the
  plan, unresolvable tool name fails the plan) — every failure mode here
  is "whole plan fails," never a partial/silent one, which is itself the
  behavior under test.
- Execution engine — the brake systems are the test-priority surface:
  timeout test (a step exceeding its window is marked failed, not left
  running), consecutive-failure test (Nth failure halts the agent, N+1th
  step never starts, zero internal retries confirmed), budget test
  (`gateway_calls_used` reaching the ceiling halts a still-succeeding agent
  before its next read step, proving the brake fires independent of
  failures; a second test proves two concurrent `Agent`s' counters never
  cross-contaminate), kill-switch test (cancel mid-`running` lets the
  in-flight step finish but the next step never starts; cancel during
  `blocked_on_approval` then approving the orphaned `ApprovalRequest`
  anyway still leaves the agent `cancelled` with no further steps run;
  a concurrency test drives `cancel_agent` and the resume-from-approval
  path against each other to prove the `select_for_update` fencing — this
  is the test that must never regress), budget tests proving `run_read_step`
  increments `gateway_calls_used` by its actual call count (2 in the normal
  case), not a flat 1, AND proving the deliberate one-step overshoot
  (starting a read step at `used == max - 1` is allowed to complete and
  push usage past `AGENT_MAX_GATEWAY_CALLS`, but the *next* step is refused
  — Codex round-5 fix, §3), task-draft-pause test (a `task_draft` step never
  calls `tasks.services.create_task` on the engine's own advance — only the
  new agents-local confirm endpoint does, after validating the `AgentStep`'s
  actor/workspace/kind/status server-side; discarding advances the agent
  without creating a `Task`), tool-request-provenance
  test (`ApprovalRequest.requested_by == Agent.created_by`, never a
  synthetic identity), a **`requester != decider` permission test** with
  `DEV_ALLOW_SELF_APPROVAL` off, proving `Agent.created_by` cannot decide
  the `ApprovalRequest` its own agent produced (Codex round-3 MEDIUM fix —
  this is the test that actually proves the rule the e2e smoke's
  self-approval bypass deliberately does not, §6), empty-plan test (a
  zero-step plan fails the agent, never completes it), visibility test
  (status fields reflect the true current/next/awaiting-approval-or-
  confirmation step at every intermediate point, not just at completion).
- Self-mod guarantee: tests asserting no engine code path writes to
  `AgentStep.kind`/`payload` post-creation, no code path creates a second
  `Agent` from within a running one's execution, no code path touches
  `tools`/`approvals` permission logic itself (only calls their existing
  public service functions, same as a human-driven request would).
- E2E: full round trip (goal → plan → unattended read step → task-draft
  confirm → blocked tool step → approve on `/approvals` → completed —
  Codex round-3 LOW fix: this summary previously omitted the task-draft
  confirmation §6 already includes) and the kill-switch path, against the
  real Docker stack (mirrors MVP 5/6's "run `make test-e2e` for real, not
  just CI" discipline).

## Risks / notes

- **This is the first MVP where a single human action (submitting a goal)
  can result in more than one write-class request being created over
  time without further human prompting per-request.** The brakes in §3
  exist because of this specific new risk, not as generic hardening — if
  plan-review proposes removing or weakening any of them, that is a
  scope regression against the user's explicit approval condition, not a
  simplification.
- **Open parameter values** (`AGENT_MAX_PLAN_STEPS`, `AGENT_STEP_TIMEOUT_
  SECONDS`, `AGENT_MAX_CONSECUTIVE_FAILURES`, `AGENT_MAX_GATEWAY_CALLS`)
  are proposed defaults in this brief, not final — plan-review and/or the
  user may adjust the numbers; the *existence* of each bound is the
  non-negotiable part, not its exact value.
- **`agents` joins the one-way dependency graph** the same way `assistant`
  did in MVP 6, via concrete new import-linter contracts in `backend/
  pyproject.toml` (Codex round-1 MEDIUM fix — the original note gestured
  at this without naming the actual contracts to add, and `agents` isn't
  yet in `root_packages` at all): add `"agents"` to `root_packages`, then
  one forbidden-contract per existing kernel app naming `agents` as a
  forbidden source (mirroring the existing `"tools must not import
  assistant"`/`"assistant must not import tasks"` shape) — `tools`,
  `approvals`, `tasks`, `memory`, `knowledge`, and `assistant` must each
  gain a contract forbidding them from importing `agents`, while `agents`
  itself is free to import all of them. `agents` does not import
  `assistant` and is not imported by it; the router (MVP 6) and the agent
  runtime (this MVP) are parallel front doors onto the same gated
  services, not layered on each other.
- **New prompt purposes** (`agent.plan`, `agent.read_step`) join `chat`,
  `memory.extraction`, `router.intent`, `router.task_draft` — same "no
  gateway call skips ModelUsage" guarantee (ADR-003, CLAUDE.md rule 3)
  applies unchanged.
- **Resolved during plan-review, recorded here so it isn't relitigated
  (round 1).** The original draft let `task_draft`-kind steps call
  `tasks.services.create_task` directly on the engine's own advance with
  no human action at all — a real bypass of the "confirm before write"
  discipline every prior MVP established, even though `Task` creation
  itself isn't in ADR-004's gated risk class. Fixed by giving `task_draft`
  steps their own `blocked_on_confirmation` pause requiring an explicit
  click (§3, §5) — never auto-created. Separately, the resource budget
  was redefined from an unworkable "query `ModelUsage`" approach (no
  agent/step reference, contaminable across concurrent agents) to a
  simple per-`Agent` call counter (§3); the kill switch's status-check-
  then-advance race was closed with the same `select_for_update` fencing
  `tools.export._fenced` already uses (§3); `ApprovalRequest.requested_by`
  provenance was pinned to `Agent.created_by`, never a synthetic agent
  identity (§3); the internal-retry-count question was settled at zero
  rather than left open (§3); the read step's "reuses MVP 6 read-dispatch"
  claim was made concrete as a dedicated `run_read_step`/`agent.read_step`
  call (§3); empty plans were made an explicit failure case (§2); and the
  import-linter note was made concrete about which contracts to add
  (above). If a future round reopens any of these, treat it as revisiting
  a deliberate decision, not discovering an oversight.
- **Resolved during plan-review, recorded here so it isn't relitigated
  (round 2).** Round 1's kill-switch fencing covered starting/resuming a
  step but not a running step's own completion — fixed so the same
  `select_for_update` fencing applies at both the start and the finish of
  a step (§3): a step already in flight when cancellation lands still
  finishes and its real result/cost is recorded, but that completion is
  never treated as license to advance once `Agent.status` reads
  `cancelled`. Separately, the E2E happy path's approval step briefly had
  the goal-submitting actor approving their own agent's request, which
  `requester != decider` (ADR-004, unchanged by this MVP) already
  forbids — fixed to use the same `DEV_ALLOW_SELF_APPROVAL` smoke-test
  mechanism MVP 5's own manually-created requests already rely on (§6).
- **Resolved during plan-review, recorded here so it isn't relitigated
  (round 3).** Round 2's e2e fix named `DEV_ALLOW_SELF_APPROVAL` as the
  mechanism but left ambiguous whether that mechanism actually proves
  `requester != decider` — it doesn't, and was never meant to; it's a
  DEBUG-only same-actor bypass for e2e convenience. Fixed by stating this
  explicitly (§6) and adding a real backend permission test (§ Test
  strategy) that proves the rule with the bypass flag off. Also fixed a
  drifted one-line e2e summary in Test strategy that had quietly dropped
  the task-draft confirmation step §6 already covers — realigned rather
  than left to narrow implementation scope by omission.
- **Resolved during plan-review, recorded here so it isn't relitigated
  (round 4).** Two real implementation-blocking gaps: (1) a read step's
  budget/timeout accounting only counted the summarization gateway call,
  missing that `select_context_chunks` itself already makes an embedding
  gateway call (`knowledge.context`'s existing `purpose="knowledge.
  retrieval"`) — fixed so `run_read_step` is treated as one bounded unit
  covering both calls, and `gateway_calls_used` increments by the real
  count rather than a flat 1 (§3). (2) `task_draft` confirmation was
  written as going "via the existing tasks API," but that API's
  `source=ai_suggested` path only triggers off a `draft_message_id`
  resolving to a real `assistant.Message`, which an `AgentStep` can never
  be — fixed by specifying a new, agents-local confirm/discard endpoint
  that validates the `AgentStep` itself (playing the same server-verified-
  reference role `draft_message_id` plays in MVP 6) and calls
  `tasks.services.create_task` directly (§3, §5). If a future round
  reopens either, treat it as revisiting a deliberate decision, not
  discovering an oversight.
- **Resolved during plan-review, recorded here so it isn't relitigated
  (round 5).** The gateway-call budget's pre-check compared
  `gateway_calls_used` against `AGENT_MAX_GATEWAY_CALLS` but only at
  step-start, so a step starting at `used == max - 1` could still run and
  land one unit over the cap before the next check caught it. Rather than
  eliminating the overshoot (which would require hardcoding the read
  step's call count into the check, brittle against future changes),
  this was resolved by naming it explicitly as a deliberate one-step
  overshoot — the same shape the timeout and kill-switch brakes already
  have (an in-flight unit of work is always allowed to finish; only the
  *next* one is refused) — and adding a test that asserts the overshoot
  happens and is bounded to exactly one step, not more (§3, § Test
  strategy). If a future round reopens this, treat it as revisiting a
  deliberate decision, not discovering an oversight.
- **Obsidian vault export is not itself long-running from the agent's
  perspective** — `vault_export`'s own execution already runs async via
  the existing `tools.export` Celery/dispatch flow (MVP 5); an agent's
  `tool_request` step is `blocked_on_approval` waiting on the *human
  decision*, not polling the export's own run status. Run-status display
  after approval is `/approvals`' existing job (MVP 5 branch 4), reused
  as-is.
