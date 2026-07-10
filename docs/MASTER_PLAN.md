# Jarvis OS Master Plan

> Skeleton only (MVP 0). The full Master Plan v2 + addendum will be placed here
> section by section. Until then, ADRs in `docs/adr/` are the binding source for
> architecture rules.

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
approval gate (see §U). Skills: not started — planned after knowledge/RAG
in the roadmap (§L). Knowledge/RAG itself: see §Q.

## G. Agent Runtime

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
agents *(next)* → skills → integrations). Model-tier/cost routing (a
DB-backed cheap/mid/strong tier table + `ModelUsage.routing_reason`
auditability) is a separate, unstarted Gateway sub-task — deliberately not
folded into the router MVP (§V); see §J.

MVP 10 (company workspaces: multi-user tenancy *inside* Jarvis — invite flows,
company membership management, company-scoped memory) is **deferred — awaiting
a concrete user**. Its first concrete tenant (external) split off via ADR-006; the
reactivation trigger is a real multi-user scenario that must be hosted inside
Jarvis. Re-evaluate this MVP's scope during the Fleet/Organization phase's
requirements definition — the fleet's need is likely a lightweight
installation-grouping "organization" concept, not full workspaces. (This does
not touch the workspace *axis* — ADR-001 stays: the scoping column and
`for_actor()` gate are already in the data model and remain cheap insurance.)

Fleet/Organization Management at MVP 11 (vision only — §P). The external module
pack formerly occupying MVP 11–15 is removed from scope (ADR-006); MVP 12–15
are reserved. Orchestration/voice/productization last.

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
- Graphiti (memory graph) adoption — re-evaluate at MVP 8
- NotebookLM external gateway — optional, public data only, MVP 9+
- Private conversation flag: ADR-005's `private` sensitivity applies to
  `Memory` rows, not to conversation messages — a message that happens to
  restate private content is still sent to the gateway verbatim (MVP 2 §2).
  Whether conversations need their own privacy flag (and what that implies
  for extraction input and context injection) is unresolved; revisit once
  memory consolidation or cross-workspace overlay work begins.
- Cross-workspace memory overlay: a personal-assistant view that spans
  workspaces touches ADR-001 isolation and ADR-005 scoping head-on. Whether
  such an overlay exists at all, and under what read-only/audit rules, is
  unresolved (see also the private-conversation-flag item above).
- Jarvis ↔ external bridge (optional, post-ADR-006): if built, it takes the MCP
  route — Jarvis as MCP client of the installation's MCP-compatible tools
  (installation side), e.g. read-only access for
  Jarvis's personal assistant to data in a external installation. Auth model,
  approval-gate treatment (ADR-004) and audit trail unresolved. Explicitly
  not committed — revisit with the fleet phase (§P).
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
  full reasoning and `tasks/models.py`'s `Task.Source` docstring.

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
the app refuses to boot under `DEBUG=False` if it's ever left on), a curator
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
