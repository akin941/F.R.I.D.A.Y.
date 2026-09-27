# Jarvis OS Master Plan

> Living plan. ADRs in `docs/adr/` are the binding source for architecture
> rules; §AA is the current roadmap (Jarvis 2.0, ADR-008) and carries the
> per-phase status. Last full plan audit: **2026-09-27** (§AA "Plan audit
> 2026-09-27").

## A. Executive Summary

Personal AI Operating System + a platform core that workplaces can integrate
with. Personal-first. external is **not** a module pack built inside Jarvis: the external installation is a separate, independently developed Django application
(ADR-006). Jarvis's long-term company-facing role is the operator's
multi-company management/assistant layer over such external installations (§P).

## B. Product Vision

Binding repositioning (2026-07-07, ADR-006):

- **Identity:** personal AI operating system + a platform core that can be
  integrated into workplaces — not a host for company products built in-tree.
- **Integration mechanism:** Jarvis integrates with workplaces by connecting
  to external installations as an **MCP client** — consuming an
  installation's MCP-compatible tools on the user's behalf (the mirror of
  (installation side) — not by hosting companies inside Jarvis.
- **external is an external product.** The external installation is an independent
  Django application, mature with real data,
  deployed with Docker on the its own server. It is developed outside
  this repository and outside this roadmap.
- **Multi-company future:** the external platform is planned to be offered to other
  firms; each customer runs its own installation. Jarvis then becomes the
  operator's fleet layer — monitoring, version tracking and tenant-level
  reporting across installations (§P) — plus the personal assistant over that
  portfolio.
- **Design transfer, not code transfer:** patterns proven in Jarvis are
  reference designs for external and future installations; the two projects remain
  independent codebases (`docs/DESIGN_TRANSFER.md`).

## C. Core Principles

Workspace is the data axis · Postgres is the source of truth · AI Gateway is the
single door · every LLM call is logged as ModelUsage · no write/delete/deploy/
external-send without the approval gate · agents never change their own
permissions · Obsidian is a human-readable memory mirror, never the primary store.

## D. Workspace Architecture

## E. AI Gateway

## F. Memory & Skills

Memory (MVP 2): workspace-scoped `Memory` rows (semantic / episodic /
preference / procedural), curated through a `pending_approval -> approved |
rejected` state machine with role-gated approve/reject/forget/restore
(ADR-005). AI-suggested candidates come from an async extraction pipeline —
a Celery worker claims lease-fenced jobs over fixed, non-overlapping message
ranges and runs the `jarvis_memory_extraction` prompt through the AI Gateway.
Approved, normal-sensitivity memories are injected into chat context
(`build_context`) under a workspace-only, injection-safe framing; `private`
memories never reach a gateway payload (the cloud-eligibility negative
contract). Every memory access — read, context injection, export — is
audited, and users can export everything they can read as a portable JSON
download; a second, filesystem-based Obsidian export shipped in MVP 5's
approval gate (see §U). Skills: **done — see §X.** Knowledge/RAG itself:
see §Q.

## G. Agent Runtime

Shipped in MVP 7 (§W): given a **goal**, Jarvis plans a bounded sequence of
`AgentStep`s (`read` / `task_draft` / `tool_request`) and executes them
without requiring a human to type each one — the other half of the
roadmap's `router → agents` line (§L), drawing the same autonomy boundary
`docs/prompts/mvp-06/brief.md` and ADR-004 already anticipated ("agent-
requested" vs. "human-requested"). The approval gate (ADR-004) is not
rebuilt or given an "agent" exception: every `tool_request` step goes
through the exact same `ApprovalRequest` machinery a human-typed request
already used (MVP 5), and `task_draft` steps still pause for an explicit
human confirm before any `Task` row is created — an agent can plan and
sequence, but it cannot create a second agent, alter its own steps after
creation, or touch `tools`/`approvals` permission logic directly (ADR-004's
self-modification guarantee, proven by branch 4's test suite). Four brakes
were the user's explicit condition for approving this autonomy increase,
not nice-to-haves: a plan step-count bound, execution-time brakes (per-step
timeout, consecutive-failure halt, a `gateway_calls_used` budget ceiling), a
backend-enforced kill switch (`cancel_agent`, fenced against every
concurrent path via `select_for_update`), and step-by-step visibility (live
`Agent`/`AgentStep` status fields, rendered on `/agents`).

## H. Development Agent Orchestration

## I. Obsidian Memory Vault

See `docs/memory/obsidian-vault-strategy.md`.

## J. Token Optimization

See `docs/cost/token-optimization.md`.

## K. MCP Governance

See `docs/security/mcp-governance.md`.

## L. Roadmap

MVP 0–20, simplified to **personal kernel + platform capabilities** (ADR-006):
personal kernel first (shell/chat → memory → knowledge/RAG *(done — §S)* →
tasks *(done — §T)* → tools/approvals *(done — §U)* → router *(done — §V)* →
agents *(done — §W)* → skills *(done — §X)* → integrations
*(done — §Y)*). Model-tier/cost routing: the skeleton (`ModelTier` +
`ModelUsage.routing_reason`) shipped in Jarvis 2.0 Phase 0, but the
purpose→tier map (`AI_PURPOSE_TIERS`) is still empty, so no call is
actually tier-routed yet; populating it together with the harness
job-distribution policy table is scheduled in Phase 5 (§AA).

MVP 10 (company workspaces: multi-user tenancy *inside* Jarvis — invite flows,
company membership management, company-scoped memory) is **cancelled
(ADR-008)**: Jarvis is a single-owner system and will never host a second
human user. The fleet phase may still add a lightweight external-installation
grouping "organization" concept. (This does not touch the workspace *axis* —
ADR-001 stays: the scoping column and `for_actor()` gate are already in the
data model and remain the isolation mechanism for firm/context profiles.)

Fleet/Organization Management at MVP 11 (vision only — §P). The external module
pack formerly occupying MVP 11–15 is removed from scope (ADR-006); MVP 12–15
are reserved. **The roadmap after MVP 9 is reorganized into Jarvis 2.0
Phases 0–6 (ADR-008, §AA, `docs/VISION_JARVIS_2.md`).** Voice Assistant,
promoted to MVP 12 by ADR-007, is re-sequenced by ADR-008 into Phase 6 —
its shipped foundation (§Z) stands; orchestration/productization are
absorbed into the phase model (the agent harness *is* the orchestration
layer).

## M. MVP 0

Foundation: repo, backend scaffold, Docker Compose dev stack, CI, pre-commit,
ADRs, governance stubs, prompt pack masters. No feature code.

## N. MVP 1

Web shell: auth (JWT), workspaces, audit skeleton, AI Gateway v0 + ModelUsage,
SSE chat, Next.js scaffold. Plan + branch status: `docs/prompts/mvp-01/brief.md`.
Status: **done (2026-07-05)** — all 6 branches merged to main.

## O. Open Decisions

- ~~Exact embedding model choice (MVP 3)~~ — **resolved:** OpenAI
  `text-embedding-3-small` is the default (`seed_ai_defaults`), but
  provider-agnostic by construction — any `ModelProvider` row with
  `capability="embedding"` works, embeddings are not hardcoded to one
  vendor. See §Q.
- ~~Graphiti (memory graph) adoption — re-evaluate at MVP 8~~ — **closed
  as deferred (2026-07-17):** MVP 8 came and went with no memory-graph
  need. Re-evaluate once during Phase 1 (memory v2) design — embeddings +
  consolidation may cover the need without a graph; if so, reject for good.
- ~~Graphiti re-evaluation (Phase 1)~~ — **rejected (recorded 2026-09-27;
  the Phase 1 closeout omitted it):** embeddings + `work_log` + firm
  dossier + the personal overlay answer "what did we last do for firm X"
  without a graph. Revisit only if multi-hop relational queries become a
  real, observed need.
- NotebookLM external gateway — optional, public data only. Re-triaged
  (2026-09-27): evaluated in the Phase 5 MCP review, not before.
- ~~Private conversation flag~~ — **resolved (Phase 1 branch 2,
  2026-07-19):** one-way `Conversation.private` (`assistant/models.py`),
  enforced at extraction input, context injection, the overlay and
  gateway payload assembly. A private conversation never becomes
  non-private again.
- Jarvis ↔ external bridge (optional, post-ADR-006): if built, it takes the MCP
  route — Jarvis as MCP client of the installation's MCP-compatible tools
  (installation side), e.g. read-only access for
  Jarvis's personal assistant to data in a external installation. Auth model,
  approval-gate treatment (ADR-004) and audit trail unresolved. **Reconciled
  with §AA (2026-09-27):** the *client* side is committed to Phase 5 via the
  generic MCP client framework (read-only tools first, writes gated); the
  external-specific connection waits on the panel exposing an MCP server (VISION
  O-5, external dependency). The fleet phase (§P) stays vision-only.
- ~~Cross-workspace memory overlay~~ — **resolved (Phase 1 branch 4,
  2026-07-19):** the personal assistant reads approved, non-private
  `work_log` memories of firm workspaces read-only; every read audited;
  gated off entirely if the personal workspace has any other member;
  `overlay_source_workspaces` redacted on low-trust reads. ADR-001
  isolation for writes is untouched.
- ~~Harness runner placement (ADR-008)~~ — **resolved (2026-07-17):** runner
  abstraction; any machine running a registered runner can execute harness
  jobs. First runner is the owner's Mac.
- ~~CLI-agent headless auth~~ — **resolved (Phase 3 branch 3,
  2026-07-21):** API keys only — `ANTHROPIC_API_KEY` (Claude Code),
  `CODEX_API_KEY` (Codex CLI). ChatGPT/subscription OAuth is never used by
  the harness. See the Phase 3 brief, "Open question resolved: Codex CLI
  vendor terms".
- **O-3 — `agents` (MVP 7) ↔ `harness` merge (due at Phase 3 closeout).**
  Both now create `ApprovalRequest`s through `tools.registry` and carry
  their own brake systems. Decision recorded in the Phase 3 closeout
  (§AA): keep separate; re-evaluate only if a chat-born plan needs repo
  write authority.
- Owner hard-deletes vs ADR-008's gate-class "delete" (found in the
  2026-09-27 audit): memory/knowledge/task deletes are direct owner
  actions over the high-trust web channel with no `ApprovalRequest`.
  Decide in the Phase 4 brief whether a direct owner action over the web
  channel *is* the explicit decision (ADR amendment) or deletes move to
  soft-delete/gated.
- Home Assistant bridge details (Phase 5): transport (native API vs MCP),
  auth, and which device domains are exposed through the approval gate.
  Open until Phase 5; the architectural hook (bridge, never drivers) is
  fixed by ADR-008.
- Shared library extraction: whether the transferred patterns
  (`docs/DESIGN_TRANSFER.md` — gateway, scoping, lease-fenced jobs, approval
  gate) ever become a shared package across Jarvis and external, or the projects
  stay on independent re-implementations permanently. Open; independent
  re-implementation is the working default.
- ~~AI-suggested task extraction (MVP 4)~~ — **resolved, amended (MVP 6 §3):**
  `Task.source` (`user_created`/`ai_suggested`, mirroring `Memory.source`)
  ships in MVP 6's intent-router work, with every pre-existing row backfilled
  to `user_created` — the provenance half of this decision is fully met. Its
  literal mechanism (route through the tools/approval-gate machinery like a
  gated tool call) is deliberately narrowed, on the record: ADR-004 gates
  write/delete/deploy/external-send-class actions, and creating a `Task` row
  in one's own workspace (fully reversible, self-scoped) isn't in that risk
  class — routing it through the gate would need a second human decider that
  doesn't exist in a personal workspace, making chat-drafted tasks
  permanently unusable there. See `docs/prompts/mvp-06/brief.md` §3 for the
  full reasoning and `tasks/models.py`'s `Task.Source` docstring. **This
  narrowing is now formally codified as ADR-008 decision 3's write-class
  taxonomy** (gate-class vs confirm-class) — it no longer amends ADR-004
  by prose alone, and the confirm-class list does not grow without an ADR.
- ~~STT/TTS provider choice (MVP 12, ADR-007)~~ — **resolved (2026-07-17):
  fully local.** STT via a Whisper-class model (e.g. faster-whisper), TTS
  via a local synthesizer (e.g. Piper), both running on the user's own
  machine — free, private (audio never leaves the device), and consistent
  with the local-first Ollama decision and ADR-006's personal/external infra
  separation. Wired as `openai_compatible`/local `ModelProvider` rows behind
  new `Capability.STT`/`.TTS`, no cloud key required. A cloud fallback stays
  possible later (same gateway, another provider row) but is not built. See
  §Z.
- ~~Binary-audio storage mechanism (MVP 12, ADR-007)~~ — **resolved
  (2026-07-17): bounded temp-file, delete-on-completion.** Raw audio is
  written to a temp file only for the duration of the transcription job and
  deleted as soon as the transcript exists — raw audio is never persisted,
  so the dormant MinIO/`django-storages` settings stay dormant and no
  binary-blob column is added. Only the resulting transcript (already text)
  flows into the existing `assistant` pipeline. See §Z.

## P. Fleet / Organization Management (MVP 11 — vision draft)

Vision + requirements draft only. **Detailed design is deliberately deferred**
— revisit once the personal kernel (through skills/integrations) is done and a
second external installation is concrete. Jarvis monitors and manages multiple
external installations (external today, future customer installations later)
without hosting their data:

- Health/usage metrics per installation: uptime, error rates, queue depth,
  cost/usage aggregates exposed by the installation itself (its own
  ModelUsage-equivalent), pulled or pushed into Jarvis.
- Version tracking: which installation runs which release; upgrade planning
  and rollout status across the fleet.
- Tenant-based reporting: per-customer summaries surfaced through the
  operator's Jarvis assistant (chat: "how is customer X's installation
  doing?").
- Carried-over constraints: read-only by default; any action that touches an
  installation goes through the approval gate (ADR-004); all fleet access is
  audited; whatever fleet metadata Jarvis stores is workspace-scoped
  (ADR-001).

Open questions live in §O (API bridge, transport pull vs push, auth model
between Jarvis and installations).

## Q. Knowledge / RAG

MVP 3: workspace-scoped `KnowledgeDocument`/`KnowledgeChunk` rows (pasted
text, plain-text/markdown file upload, or a fetched URL), chunked and
embedded via the same AI Gateway single door as chat (`InferenceGateway.
embed()`, ADR-003 — one `ModelUsage` row per call, provider-agnostic).
Ingestion is async (Celery, lease-fenced claim/finalize, recovery scan,
mirroring memory's extraction-worker pattern): `url` sources are fetched
through an SSRF-hardened fetcher (pinned-IP connect, per-hop redirect
re-validation, content-type/size/time limits — `knowledge/url_fetch.py`);
`private`-sensitivity documents are never embedded in the first place
(ingestion-side gate).

Retrieval (`assistant.context.build_context`, parallel to memory injection,
not merged into it): the latest user message is embedded, `KnowledgeChunk`
rows are scoped through `for_actor().cloud_eligible()` (ADR-001 + ADR-005 —
a second, independent gate behind the ingestion-side one) and filtered to
the serving embedding provider *and* actual vector dimension before pgvector
cosine-similarity ordering ever runs (`embedding_provider_id` alone is not a
safe dimension proxy — a provider row's `dimension`/`model_id` are ordinary
editable fields). Selected chunks are framed as injection-safe delimited
data (`neutralize_fence()`, shared with memory's context builder) under a
count + character dual-cap budget. `chunks_used` (mirrors `memories_used`
exactly) is persisted on the assistant `Message` row and surfaced in the
chat UI; `knowledge.context_accessed` is audited on every call, including
empty retrievals.

Knowledge UI: upload (paste text / file / URL), document list with
status/sensitivity, delete — no content-editing endpoint exists by design
(re-upload rather than mutate embedded content in place).

Status: **done (2026-07-08)** — all 6 branches merged to main. Plan + branch
status: `docs/prompts/mvp-03/brief.md`.

## R. MVP 2

Memory: workspace-scoped curated memory with an approval workflow, async
extraction from chat, injection into context, portable export, and a
Next.js Memory page + chat memory-context indicator. Full design in §F.
Status: **done (2026-07-07)** — all 6 branches merged to main. Plan + branch
status: `docs/prompts/mvp-02/brief.md`.

## S. MVP 3

Knowledge/RAG. Full design in §Q. Status: **done (2026-07-08)** — all 6
branches merged to main. Plan + branch status: `docs/prompts/mvp-03/brief.md`.

## T. MVP 4

MVP 4: workspace-scoped `Task` rows (title, description, due date, priority),
a service-enforced `open -> done | cancelled` / `done | cancelled -> open`
state machine with actor-scoped `select_for_update` locking (mirroring
memory's `_locked()` pattern, so concurrent transitions on the same row
serialize instead of racing), a role matrix (owner/admin manage every task,
members manage their own, viewers read-only), and `record_audit()` mutation
events that never carry task title/description text. Deliberately manual
CRUD only in this MVP — no AI-suggested extraction, no context injection into
chat (true at the time; see §O's Open Decisions entry — MVP 6 §3 later
resolved/amended this, adding chat-drafted tasks via `Task.source` rather
than the approval-gated tool call originally envisioned here).

Frontend: a `/tasks` page (list/create/edit/complete/cancel/reopen/delete,
status filter) following the Next.js BFF pattern established by memory/
knowledge, and a real `/today` page (replacing the MVP 1 skeleton
placeholder) showing open tasks due today or overdue. "Today" is always the
*client's* local calendar date, computed in the browser and passed to the
list endpoint as `due_before` — the server never computes "today" itself, so
a user near a day boundary in a different timezone than the server always
sees the correct task set.

Status: **done (2026-07-08)** — all 3 branches merged to main. Plan + branch
status: `docs/prompts/mvp-04/brief.md`.

## U. MVP 5

MVP 5: the generic approval gate (ADR-004) plus the first concrete gated
tool. `approvals` kernel app: workspace-scoped `ApprovalRequest` rows with a
`pending -> approved | rejected | expired` state machine, requester != decider
enforced at the model/service level with no production exception (a
narrowly-scoped `DEV_ALLOW_SELF_APPROVAL` flag exists for dev/test only, and
the app refuses to boot under `DEBUG=False` if it's ever left on — **both
superseded by ADR-008 decision 3 (Phase 0): the flag is removed from code
and requester≠decider is replaced by the single-owner explicit, separate,
audited decision rule; machine origins never decide**), a curator
role check (owner/admin only may decide), a decision-time expiry backstop
layered on top of a periodic Celery sweep, and full audit coverage. `tools`
kernel app: a plain-dict `ToolSpec` registry (no DB model — one concrete
tool doesn't justify one; extract once a second tool needs the shared
shape) wiring `create_execution()`/`dispatch()` into the decision service's
own transaction (durable-before-dispatch: the execution row commits before
the broker is ever contacted, dispatch itself deferred to
`transaction.on_commit`).

The first real gated tool: Obsidian vault filesystem export, deferred from
MVP 2 (see §F). `VaultExportRun` — a lease-fenced claim/finalize execution
record mirroring memory/knowledge's own worker pattern — full-resync +
conservative-prune reconciliation (a file is deleted only if every check
passes: valid frontmatter, `source: jarvis_export`, matching workspace, a
valid UUID no longer in the approved set), two-tier vault-root validation
(a zero-filesystem-access string check at request time, a real
existence/safety check at execution time in the worker), and TOCTOU-
resistant path safety on every filesystem operation, not just writes.

Frontend: a `/approvals` page (pending/decided lists, a run-status
indicator reflecting the tool's own `describe_execution()` hook, decision
controls hidden both for the requester's own request and for any role the
backend wouldn't actually let decide), and a plain-language "Export to
Obsidian vault" trigger on the Memory page.

Status: **done (2026-07-10)** — all 5 branches merged to main. Plan + branch
status: `docs/prompts/mvp-05/brief.md`.

## V. MVP 6

MVP 6: the intent router — "understand a chat message, dispatch it to the
right subsystem" — resolved as the roadmap's actual meaning of "router"
after an explicit disambiguation with the user (not model-tier/cost
routing, which is a separate, deferred Gateway sub-task; see §J). No new
autonomous write path: every write this MVP can trigger already existed
behind an explicit human click (task creation, gated-tool approval
requests) — the router only adds a faster way to *reach* that same click,
never a way to *skip* it.

**Router vs. agents, the autonomy boundary** (grounded in MVP 4's and MVP
5's own briefs, not invented): router = one user utterance → one classified
intent → one dispatched action, inside the same turn, writes always
requiring explicit confirmation. Agents (MVP 7+) = goal → multi-step plan,
potentially executed without a human typing each step. This is the line
ADR-004's "agent-requested" vs. "human-requested" language already drew.

Four dispatch behaviors, one per branch:

1. **Classification foundation** (`assistant.intent`, no dispatch side
   effects): a new `purpose="router.intent"` gateway call classifies every
   chat message into `chat` / `memory_query` / `knowledge_query` /
   `task_draft` / `tool_request` before the reply begins. Classification
   failure always degrades to `chat` — never blocks or corrupts the
   underlying conversation. `Message.intent`/`intent_payload` (new fields,
   user-role rows only, immutable once written) persist the result.
2. **Read-dispatch** (`memory_query`/`knowledge_query`): purely additive
   framing on top of retrieval that already ran every turn — `knowledge_query`
   widens the retrieval window for that turn only, both intents add an
   explicit "the user specifically wants this" system-prompt instruction.
   No new retrieval mechanism; memory still has no embeddings (MVP 3
   shipped semantic search for knowledge only, see §Q).
3. **Confirmable task drafts** (`task_draft`): a second, focused
   `purpose="router.task_draft"` extraction call (title, optional due date)
   after classification, a non-terminal `draft` SSE event, and a
   server-verified `draft_message_id` reference on `TaskCreateSerializer`
   (never a client-asserted claim) that's the only path producing
   `Task.source=ai_suggested` — a new provenance field that formally
   amends MVP 4's Open Decision (§O): the *provenance* half of that
   decision is met, but its literal "route through the approval-gate
   machinery" mechanism is deliberately narrowed to not apply, since
   ADR-004 gates write/delete/deploy/external-send-class actions and
   creating a `Task` row in one's own workspace isn't in that risk class.
4. **Confirmable tool requests** (`tool_request`): symmetric with
   task_draft — classification alone never calls
   `approvals.services.create_request`, only a confirmed draft does, via
   the exact function the existing "Export to Obsidian vault" button
   already calls. `tools.registry.list_tool_names()` (names only) plus a
   router-local tool-description map build the classification prompt's
   vocabulary from whatever's actually registered.

Frontend: `TaskDraftCard`/`ToolRequestCard` confirm/discard components
wired into the chat composer's SSE handling; both reuse existing BFF
routes unchanged (`POST /api/tasks`, `POST /api/approvals/requests`) with
only an added optional field, and the tool-request card renders the same
`ApprovalCard` `/approvals` uses for run-status once confirmed.

Playwright smoke extended to exercise all three live dispatch behaviors
against the real Docker stack. `FakeAdapter`'s `router.intent` response is
content-triggered (specific substrings like "remind me"/"export my vault")
purely so this e2e suite — a real backend process it cannot monkeypatch —
can still exercise every intent; unit tests monkeypatch `parse_intent`
directly instead and never depend on this.

Status: **done (2026-07-10)** — all 5 branches merged to main. Plan + branch
status: `docs/prompts/mvp-06/brief.md`.

## W. MVP 7

MVP 7: Agent Runtime — the other half of the `router → agents` roadmap
line (§L), and, in the user's own words, "Jarvis's riskiest MVP so far."
Given a **goal**, Jarvis plans a sequence of steps and executes them
without requiring a human to type each one — a real increase in autonomy,
scoped narrowly on purpose: ADR-004's approval gate is not rebuilt or
given an "agent" exception, and the user made four brakes an explicit
condition of approving this MVP at all (not nice-to-haves): a plan
step-count bound, execution-time brakes (timeout / consecutive-failure
halt / `gateway_calls_used` budget), a backend-enforced kill switch, and
step-by-step visibility.

New `agents` app: `Agent` (`goal`, `status`, `gateway_calls_used`,
`consecutive_failures`, `failure_reason`) and `AgentStep`
(`kind` — `read` / `task_draft` / `tool_request` —, `description`,
`payload`, `status`, `result`), both workspace-scoped and immutable in
their planned shape once created. A `purpose="agent.plan"` gateway call
produces the whole plan up front (whole-plan-fails-clean on any
parse/schema/gateway/tool-name error, never a partial plan); execution
then advances one step at a time via Celery, pausing at `blocked_on_approval`
for `tool_request` steps (via the exact `approvals.services.create_request`
a human-typed request already used) and at `blocked_on_confirmation` for
`task_draft` steps (a new agents-local confirm/discard endpoint, never the
engine's own unattended advance path — this is what ADR-004's
self-modification guarantee and branch 4's test suite specifically prove
never gets bypassed).

The riskiest branch (3, the execution engine) took **7 rounds** of Codex
review to close — the most scrutiny any branch in this codebase's history
has received — surfacing two recurring bug classes worth remembering
beyond this MVP: a **two-transaction atomicity gap** (a step's own
resolution and the following halt-or-continue decision as two separate
transactions let a crash or duplicate delivery in between corrupt state,
including a genuine kill-switch bypass during planning — round 5, HIGH)
fixed by merging both into one transaction via a shared decision helper,
and a **lost `on_commit` callback** (an in-memory Python closure that
silently loses work if the process dies between commit and callback,
found in three separate places — dispatch, approval-resume, and
approval-resume-for-expired) fixed by extending a single periodic recovery
scan (`agents.tasks.recover_stale_agents`) to cover all three. Branch 4
audited the merged engine for self-modification violations and needed no
new code — only a test suite proving the guarantee already held.

Frontend: `/agents` (goal-entry form, list, detail view with per-step
confirm/discard/approval-link/cancel controls). Playwright smoke covers
the full lifecycle against the real Docker stack — goal → plan →
unattended read step → task-draft confirm → tool-request approval →
completed — plus the kill-switch path (cancel a `blocked_on_approval`
agent, then approve the now-orphaned `ApprovalRequest` anyway; the Agent
must stay `cancelled`). `FakeAdapter`'s `agent.plan` purpose is
content-triggered (`"full plan"`), mirroring MVP 6's `router.intent`
convention, purely so this real backend process — which the e2e suite
cannot monkeypatch — can still exercise all three step kinds in one plan.

Status: **done (2026-07-13)** — all 5 branches merged to main. Plan + branch
status: `docs/prompts/mvp-07/brief.md`.

## X. MVP 8

MVP 8: Skills — reusable plan templates, the shortcut half of the
`agents → skills` roadmap line (§L). MVP 7 gave every agent one
`purpose="agent.plan"` gateway call per goal, even for goals that recur in
a predictable shape ("export my weekly notes," "turn this note into a
task"). A **Skill** is a git-authored, pre-approved plan template — the
answer to "we already know how to do this; skip re-planning it from
scratch." This MVP adds a mechanism to register such templates and a
narrow, safe way for the agent runtime to recognize a match, without
touching the execution engine, the approval gate, or any of MVP 7's four
brakes at all: a skill-matched plan's `AgentStep` rows go through the
exact same `finalize_plan()` → execution engine → approval gate path a
freeform LLM plan already does.

New `skills` kernel app: a **code-only registry** (`SkillSpec`/`SkillStep`
frozen dataclasses, no DB table) mirroring `tools/registry.py`'s
established pattern rather than `ai_core.PromptTemplate`/`PromptVersion`'s
weaker "immutable-by-discipline, not code-enforced" one — a deliberate
strengthening the user chose during scoping: changing a skill requires a
git commit + PR review + deploy, never a database write, and there is no
runtime data structure for an agent to mutate at all. `skills` is a new
one-way import-linter root (agents may import skills; skills must never
import agents), with its own per-kind step-shape validator duplicated from
— not importing — `agents.planner`'s, proven identical via parity tests.
Boot-time validation (`SkillsConfig.ready()`) fails the whole process at
startup if any skill references an unregistered tool, rather than only
surfacing the dangling reference when an agent happens to run that skill.

A new, narrower `purpose="agent.skill_match"` gateway call precedes
`agent.plan` in `create_agent_from_goal()`: given the goal and the
registered skill catalog, it returns `{"skill_name": "<name>" | null}` —
binary, not a graded confidence score (a considered simplification: the
prompt itself encodes "only match if unambiguous," and a non-null response
is independently re-validated against the live registry before being
trusted, so a hallucinated or weak match degrades to freeform planning
exactly like an explicit `null` would). A match skips `agent.plan`
entirely — the actual efficiency gain, not just a faster plan. Two new
`Agent` audit fields, `skill_name`/`skill_content_hash`, record which
skill (and which exact version of its template) produced a given plan,
mirroring the panel's `prompt_key`+`prompt_version` usage-record pattern —
answerable months later even after the skill's own template has since
changed in a later deploy.

Two example skills ship: `weekly_export` (read → tool_request, exports the
vault) and `note_to_task` (read → task_draft). Frontend: `AgentDetail`
surfaces `Agent.skill_name` when set ("Ran via skill: weekly_export").
Playwright smoke extends the agents section with a skill-matched goal,
asserting the resulting steps match the skill's fixed template rather
than a freeform LLM plan.

The plan itself took **6 rounds of Codex plan-review** before
implementation began — the round-1 HIGH (a claimed reuse of
`agents.planner`'s validators that contradicted `skills` never importing
`agents`) was escalated to the user, who chose duplicating a local
validator with parity tests over the alternatives; the rest were
increasingly subtle Python dataclass correctness bugs (`dataclasses.
replace()` cannot target an `init=False` field; a mutable-collection hash
invalidation the same shape as an already-fixed bug earlier in the same
review; `ModelUsage` success/failure semantics for the new gateway
purpose). Branch 1 (skills-foundation) took 3 rounds of code review — a
JSON-serialization footgun (`SkillStep.payload`'s `MappingProxyType` isn't
JSON-serializable) and a validation-parity hole (a non-dict payload
silently coerced before shape validation ran) — both real, both fixed.
Branch 2 (skill-selection) came back clean on round 1.

Status: **done (2026-07-13)** — all 3 branches merged to main. Plan + branch
status: `docs/prompts/mvp-08/brief.md`.

## Y. MVP 9

MVP 9: Integrations — a CLI and a Telegram bot, the last stage of the
`shell/chat → ... → skills → integrations` roadmap line (§L). Every surface
Jarvis had shipped through MVP 8 was reachable only through the web app,
authenticated by a password+JWT login. This MVP adds two more ways to
*reach* Jarvis — a bearer-token CLI and a relayed Telegram bot — without
adding a single new way to *act*: both are new front doors onto the exact
same chat pipeline (and, for the CLI, the same agents/approvals endpoints)
the web app already calls. The one new concept introduced on purpose is
**channel trust**: a request authenticated by anything other than the
standard web login is low-trust, and — the MVP's one hard, load-bearing
rule (ADR-004) — a low-trust channel can never *decide* an
`ApprovalRequest`, only propose one, exactly as the web app can.

`core/channels.py` (`ChannelPrincipal`, `request_channel`,
`is_low_trust`, `scoped_for_actor`) is the one primitive both channels sit
on, deliberately placed in `core` rather than `integrations` so `approvals`
can ask "did this arrive over a low-trust channel?" without importing
`integrations` at all. `scoped_for_actor()` is the one function every
workspace-scoped view on the PAT allowlist calls instead of `for_actor()`
directly — for a channel-bound request it forces the query to the token's
bound workspace regardless of what the client supplied (or omitted), the
same containment guarantee a by-id lookup, a query param, and a body field
all had to be independently closed against (three separate P1 findings
during plan-review, below). `PersonalAccessToken` lives in `accounts`, not
`integrations` — a real architecture correction mid-plan-review: it's
fundamentally an authentication credential, the CLI's equivalent of the JWT
`AuditedLoginView` already mints, and `assistant`/`agents`/`approvals`
already import `accounts` everywhere they reference `User`.

The Telegram bot links one 1:1 chat to one `(user, workspace)` pair and one
`Conversation` for the link's lifetime via a short-lived `/link <code>`
flow (atomic, redelivery-safe claim), then relays messages through the
identical `classify_intent → build_gateway_stream → persist` pipeline the
web endpoint uses. Group chats are rejected outright — `chat.type ==
"private"` is checked before anything else runs, non-negotiable, since a
group has no per-sender identity in this design. A `task_draft`/
`tool_request` classification degrades to a read-only description in this
channel; there is no confirm path for a Telegram-surfaced draft in *either*
channel — the web app's own draft-confirm mechanism turned out to only work
for the live SSE stream that produced it, not a reopened past turn, so
building persisted-draft reconstruction stayed explicitly out of scope
rather than being absorbed as a side effect of adding a chat channel.

Frontend: a new `/integrations` settings page (personal access token
list/create/revoke with a one-time-reveal banner for the raw token value;
Telegram link-code request/status/unlink), each section carrying its own
workspace picker since a PAT or a Telegram link is bound to one workspace
for its lifetime, not necessarily whichever workspace the sidebar switcher
currently has active. Playwright smoke extends the existing e2e flow with
a PAT-authenticated chat round trip against the real backend (no browser
session at all) plus the channel-restriction invariant itself (a
PAT-authenticated approve attempt gets a live 403, then the same request
approved over the web channel gets 200) and a Telegram section that POSTs
real webhook payloads against the real backend and asserts on the
resulting DB-observable state (link status, persisted messages) rather
than the webhook response itself — the reply-sending step's one bounded,
fast-failing outbound call to Telegram's API against a fixture bot token
is an accepted, documented trade-off (`docs/integrations/README.md`), not
something worth adding backend-side test scaffolding to eliminate.

The plan took **8 rounds of Codex plan-review** before implementation
began. All 4 P1s were escalated to the user and fixed with explicit
sign-off: a global-PAT-authentication design would have let a PAT
authenticate against *every* `IsAuthenticated` view in the codebase, not
just the ones this MVP intended to expose (fixed with an explicit
per-view allowlist, never `DEFAULT_AUTHENTICATION_CLASSES`); Telegram
group-chat linking was declared out of scope in prose but never actually
enforced (fixed with the non-negotiable `chat.type` gate above); the
original `effective_workspace_id()` design only intercepted query-param
call sites, not a by-id lookup or a body field (fixed by generalizing to
`scoped_for_actor()`); and the PAT authenticator didn't check
`user.is_active`, meaning a deactivated account could keep using an
unrevoked token indefinitely. Branch code review found and fixed real
issues at every stage: branch 1 (channel foundation) took 3 rounds;
branch 2 (CLI channel) took 4 rounds (4 real P2s: an ambiguous
workspace-name match in `create_dev_pat`, a missing `client_local_date`
field, a whitespace-mutation bug, a missing `DEBUG` gate); branch 3
(Telegram bot) took 4 rounds, escalating one more P1 to the user
(relay-time link revalidation — a deactivated or removed user could keep
relaying messages indefinitely through a stale link) plus real P2s
(missing chat-scope throttling on the relay path, the link flow bypassing
the audited `create_conversation` service, link-code *consumption* not
revalidating access the same way relay already did, a webhook accepting
traffic with no bot token configured, an unbounded outbound HTTP call); a
recurring `update_id`-deduplication finding was correctly recognized each
time as a re-raise of an already-decided, documented scope exclusion, not
a new gap. Branch 4 (frontend) took 4 rounds (a raw PAT token surviving in
list state past the reveal banner's dismissal, two instances of the same
stale-closure race after a mid-flight workspace switch, and the reveal
banner's "Copied" state carrying over to a newly created token) — the user
was consulted directly after the third consecutive round with a new
finding, per this project's standing escalation protocol for extended
review loops, and chose to run one more round before merging.

Status: **done (2026-07-14)** — all 5 branches merged to main. Plan +
branch status: `docs/prompts/mvp-09/brief.md`.

## Z. Voice Assistant (MVP 12 — vision draft)

Vision section (ADR-007; re-sequenced to Phase 6 by ADR-008). At creation
this section's two §O decisions (STT/TTS provider choice, binary-audio
storage) blocked all implementation; **both are resolved (2026-07-17, §O)**
and the Gateway STT/TTS foundation has shipped — the remaining pipeline
work is gated only on Phase 6's slot in the roadmap, not on open
decisions (`docs/prompts/mvp-12-voice/brief.md`).
Promoted off the roadmap's unscoped "last" bucket now that the personal
kernel (shell/chat through skills/integrations, §Y) is done and the owner
has local-LLM infrastructure (Ollama) available for dogfooding:

- Local-first "thinking" LLM: an opt-in, personal-only `ollama-local`
  `ModelProvider` (`openai_compatible`, mirrors NVIDIA NIM's pattern,
  `a78b6de`) — never wired into the external product, same personal/company
  infra separation ADR-006 already draws. Ships ahead of the rest of MVP 12
  as low-risk foundation work.
- Speech in: push-to-talk mic capture in the web shell, transcribed to text
  and handed to the existing chat pipeline (`assistant/services.py`) — no
  new intent/routing logic, voice is a new *input transport* onto an
  unchanged conversation flow.
- Speech out: the assistant's existing streamed text reply synthesized to
  audio and played back.
- A visible "Jarvis" state indicator in the web shell (idle / listening /
  thinking / speaking), reacting to the same SSE stream `ChatView.tsx`
  already consumes.
- Carried-over constraints: every STT/TTS call goes through
  `InferenceGateway` like any other model call (ADR-003 single door, rule 3
  — `ModelUsage` recorded for STT/TTS too, not just chat/embedding); no
  write/delete/deploy/external-send exception is created for voice (ADR-004
  still applies to anything voice-triggered beyond chat replies, e.g. a
  voice-drafted task still follows `Task.source=ai_suggested`'s existing
  rules, §O).

Both §O blockers are now resolved (2026-07-17: fully local STT/TTS;
bounded temp-file, delete-on-completion audio). The Gateway STT/TTS
foundation and the orb UI shipped ahead of schedule (see
`docs/prompts/mvp-12-voice/brief.md` §2); the remaining work is
**re-sequenced to Phase 6 by ADR-008**, with two design constraints fixed
now:

- **Always-listening via a local wake word** ("Hey Jarvis"): detection runs
  on-device with an openWakeWord/Porcupine-class model — audio never leaves
  the machine and nothing is processed until the wake word fires. The goal
  is that everything the owner does by hand on the computer can be done by
  voice or text.
- **Visual identity comes after voice works:** the animated, speaking
  "Jarvis" interface layer (built on the dormant orb foundation) is the
  last layer — a visual shell before the pipeline works would be an empty
  costume.

## AA. Jarvis 2.0 Phases (ADR-008)

The post-MVP 9 roadmap. Full analysis and rationale:
`docs/VISION_JARVIS_2.md` (approved 2026-07-17). Jarvis is a
**single-owner system** (ADR-008): the kernel is the control plane, a new
`harness` kernel app drives external CLI coding agents (Claude Code, Codex
CLI, Gemini CLI, Ollama) as workers under the approval gate, with
cross-vendor review (the implementing agent never reviews its own diff).
Repo-write automation carries **three** separate owner decisions: plan
approval (Gate A, Phase 3), push approval (Gate B, Phase 3) and deploy
approval (Phase 5).

| Phase | Scope | Usable outcome | Status |
|---|---|---|---|
| 0 | ADR-008 in code: firm profiles on Workspace, Ollama embedding provider, model-tier routing skeleton (§J), approvals machine-requester fix, signup off | Daily local-LLM use; firms defined; agent approvals decidable | **done (2026-07-17)** |
| 1 | Memory v2: embeddings + semantic retrieval, `work_log` class, core memory blocks (owner + firm dossiers), personal read-only overlay | "What did we last do for firm X?" answerable | **done (2026-07-19)** — owner core block + consolidation carried forward (below) |
| 2 | Harness v1: read-only discovery runs → briefing; runner abstraction (first runner: Mac); usage → ModelUsage; kill switch + budgets | "Analyze this bug in repo X, brief me" | **done (2026-07-20)** (mocked suite; first real run in Phase 3 closeout) |
| 3 | Harness v2: plan approval → worktree implementation → tests → cross-vendor review → gated branch push | Approved feature development end-to-end | **in progress** — branches 1–4 done (2026-07-20 → 07-22); closeout branches 5–8 re-planned 2026-09-27 (below) |
| 4 | Security baseline; UI renewal: shell/chat/harness console/approvals inbox redesign; device-trust identity replaces signup/login; OpenAI-compatible facade (chat-only, low-trust) | Professional, safely-exposable daily driver | not started — brief to be written at Phase 3 closeout |
| 5 | Deploy gate + VPS deploy (allowlisted commands); MCP client framework: GitHub, mail/calendar, external client (when an installation exposes MCP), Home Assistant bridge; tier routing + harness policy table | Request → code → live, end to end | not started |
| 6 | Voice (ex-MVP 12): mic capture, faster-whisper STT, Piper TTS, local wake word, orb wired to real audio; then the animated visual identity layer | The real Jarvis experience | not started (foundation from MVP 12 shipped, dormant) |

Phase briefs live in `docs/prompts/phase-N-<topic>/brief.md`, same
discipline as MVP briefs (branch tables, test strategy, closeout).

**Integration state (2026-09-27):** all Phase 0–3 work (119 commits) lives
on the shared session branch `claude/repo-setup-env-transition-1g94hx`,
not on `main`, and CI (push-to-main / PR only) had never run on it. Draft
integration PR akin941/jarvis-os#26 was opened on 2026-09-27 so CI runs; a
green CI run and the merge of that PR are Phase 3 closeout gates.

### Plan audit 2026-09-27

A full re-read of plans + four independent audits (plans/docs, harness
code, kernel rules, frontend) after a two-month pause. Kernel rules held:
single-door gateway, ModelUsage on every path, `for_actor()` on every
view, machine origins never decide, `private` never reaches a payload,
27/27 import contracts, ~1.8k backend + 392 frontend tests green. The
audit found the following, all folded into the branches below:

- **Harness (Phase 3 code):** the uncommitted branch-3 follow-up diff
  (`.env.example` harness block) breaks boot when copied (`KEY=` parses as
  an empty string for `env.int`); the wrapper's tested-tree == committed-
  tree check is still bypassable (assume-unchanged, lowercase `s`, paths
  with spaces, `.git/info/exclude`); a `pending_review` job with no review
  row is a permanent stuck state no recovery case covers; non-`RunnerError`
  exceptions (invalid UTF-8 `result.json`, raw `TimeoutExpired` from
  `capture_logs`/`container_is_running`/`container_exit_code`) leave jobs
  to the 35-min recovery path with 0 recorded cost; cost parsing trusts the
  first JSON object on container stdout (agent-writable); agent-set
  `chmod 500` defeats host cleanup; self-repo canonicalization misses
  alternate spellings and the guard is fail-open when unconfigured;
  cancelling a job doesn't stop a running review container; the test
  command inherits the CLI API key; no `--cap-drop`/`no-new-privileges`;
  headless write mode likely can't `git commit` (wrapper should own the
  commit). The harness has **never run for real** (placeholder image
  digests, no compose wiring).
- **Phase 3 branch 5's original definition was stale:** there is no
  implementation-job API at all (services exist, no views/serializers/
  URLs); the implementation CLI transcript is discarded, not stored;
  `diff_summary` is a deterministic placeholder, never gateway-produced
  (boundary section promised otherwise); Gate B's approver sees only
  `tool_name`; `ApprovalCard` prints "undefined" counters for harness
  executions; `harness_implement`/`harness_push` leak into the router and
  planner tool vocabulary as dead-end proposals; implementation/push
  outcomes write no `work_log` (ADR-008 decision 4).
- **Kernel/security:** dev compose publishes Postgres/Redis/MinIO/web on
  `0.0.0.0` with committed credentials and a committed SECRET_KEY;
  settings fall back to a hard-coded key; `prod.py` is a TODO; no login
  throttle or password validators on the single owner account (the only
  decider); rotated refresh tokens never revoked, no logout endpoint;
  approval decisions don't check `is_agent`; `approvals` CLI requests are
  stamped `origin=HUMAN` with no channel; private memories are exported to
  the main vault tree instead of `_private/`; throttles use per-process
  LocMem cache; `.env.example` is missing ~25 variables read in code.
- **Frontend:** the whole app renders in Times (self-referential
  `--font-sans` token) and Geist lacks `latin-ext` for Turkish; the
  Playwright smoke is broken since the i18n commits (English selectors) and
  still depends on signup; shell height/overflow chain doesn't bound
  scroll areas; chat flickers/blank-flashes after every reply, swallows
  load errors; backend English `detail` strings override Turkish fallbacks;
  cards not keyboard-reachable; no confirmation on destructive actions.

### Phase 3 closeout (re-planned 2026-09-27)

The original branch 5 (`feat/p3-harness-ui`) is split into four branches;
detail in `docs/prompts/phase-3-harness-v2/brief.md` branch table.

| # | Branch | Scope |
|---|---|---|
| 5a | `chore/p3-deps-security` | Close the 68 open Dependabot alerts (3 critical) with patch-level upgrades (django, drf, sqlparse, anyio, next, vitest, transitive npm) |
| 5 | `feat/p3-harness-hardening` | The pending branch-3 follow-ups (fixed `.env.example`: commented defaults) + every harness audit finding above + O-3 decision recorded |
| 6 | `feat/p3-implementation-api` | Implementation-job + review API (create/list/detail/cancel), stored implementation transcript, gateway-produced `diff_summary`, a safe approval display summary for Gate A/B, `work_log` for implementation/push outcomes, `ToolSpec.user_invocable` filter for router/planner |
| 7 | `feat/p3-harness-ui` | Frontend: repo picker, request implementation (fresh or from a discovery job), pipeline view, gate status + `/approvals` deep links, review verdict/findings/diff summary with fenced transcripts, harness-aware approval cards; plus the frontend "fix now" list (font, e2e, shell height, chat error/flicker, keyboard cards) |
| 8 | closeout | Real-container smoke (actual Docker launch with a stubbed CLI, no paid calls) + owner runbook for the first paid run; CI green on PR #26; merge to `main` |

### Phase 4–6 outline (briefs written at each phase start)

- **Phase 4 — security baseline + UI renewal + identity.**
  (1) `feat/p4-security-baseline`: loopback-only compose ports, no
  committed secrets, fail-hard `prod.py` (HSTS, secure cookies, SSL
  redirect), login/refresh throttles, password validators, refresh-token
  blacklist + logout, `is_agent` never decides, request channel on
  approval audit, private memories → `_private/` in vault export, Redis
  cache for throttles, agent-create throttle, configurable test DB name,
  complete `.env.example`, obsolete TODOs removed. (2)
  `feat/p4-device-trust`: device enrollment (owner-issued pairing code/PIN
  on LAN, strong external key) replacing signup/login; signup endpoint and
  route removed; e2e seeded with `bootstrap_owner`; owner-only firm
  workspace creation. (3) `feat/p4-design-system`: tokens (semantic
  status colors, light/dark), primitives (Badge, AlertDialog, Tabs,
  Tooltip, Toast, Empty/ErrorState, Markdown), shared data hook, routed
  shell with grouped nav + approval badge, i18n catalog + backend error
  mapping. (4) `feat/p4-chat-redesign`: markdown, smart scroll, no
  post-stream flash, visible errors, mobile conversation drawer. (5)
  `feat/p4-console-and-inbox`: routed harness console, repo registry UI,
  approvals inbox with per-tool renderers, confirmations, filters. (6)
  `feat/p4-openai-facade`: `/v1/chat/completions`, chat-only, PAT auth,
  low-trust channel.
- **Phase 5 — deploy + MCP.** Deploy-target allowlist model + deploy gate
  + allowlisted SSH command runner with health check; generic MCP client
  framework (manifested servers, read-only tools ungated, writes gated,
  audited); GitHub (PR for a pushed harness branch, CI status), Home
  Assistant bridge (domain allowlist, service calls gated), mail/calendar
  (read first, send gated); external client when O-5 unblocks; tier-routing map
  + harness policy table; remaining `work_log` producers (gated tools,
  tasks, deploys). "Social media via existing services" stays undefined
  until a concrete service is named — explicitly out of scope until then.
- **Phase 6 — voice.** Browser mic capture → gateway STT (local
  faster-whisper behind an OpenAI-compatible server) → assistant → TTS
  (local Piper) playback, orb wired to real audio levels; local wake word
  (in-browser, audio never leaves the device before the wake word); voice
  is a web-session channel but approval decisions still need an explicit
  on-screen confirm; then the visual identity layer.

**Carried-forward items (not yet scheduled in a branch):** owner core
memory block; memory consolidation/distillation (ADR-005, VISION R5);
shared per-turn query embedding; SSE log streaming for harness; revise-
per-feedback loop; shared harness execution-engine extraction (its
three-call-site trigger is now met — evaluate in Phase 5); dynamic runner
registry (needed when a second runner appears); Gemini CLI / Ollama as
harness agents; configurable branch-name prefix; MVP 9 persisted-draft
reconstruction (Phase 4 UI candidate).
