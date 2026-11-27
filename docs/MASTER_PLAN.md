# Friday OS Master Plan

> Living plan. ADRs in `docs/adr/` are the binding source for architecture
> rules; §AA is the current roadmap (Friday 2.0 phases 0–6, ADR-008, and
> since 2026-11-20 the Friday 3.0 phases 8A–12, §AA "Friday 3.0 phases")
> and carries the per-phase status. Last full plan audit: **2026-09-27**
> (§AA "Plan audit 2026-09-27").

## A. Executive Summary

Personal AI Operating System + a platform core that workplaces can integrate
with. Personal-first. Workplace products are **not** module packs built inside
Friday: they are separate, independently developed and deployed external
installations (ADR-006). Friday's long-term company-facing role is the
operator's multi-installation management/assistant layer over such external installations (§P).

Renamed from Jarvis to Friday on 2026-11-20 (new repository akin941/F.R.I.D.A.Y.).

## B. Product Vision

Binding repositioning (2026-07-07, ADR-006):

- **Identity:** personal AI operating system + a platform core that can be
  integrated into workplaces — not a host for company products built in-tree.
- **Integration mechanism:** Friday integrates with workplaces by connecting
  to external installations as an **MCP client** — consuming an
  installation's MCP-compatible tools on the user's behalf (read-only first,
  writes behind the approval gate) — not by hosting companies inside Friday.
- **Workplace products are external.** A workplace product is an independent
  application with its own codebase and its own deployment. It is developed
  outside this repository and outside this roadmap.
- **Multi-installation future:** where several organizations each run their
  own external installation, Friday can become the operator's fleet layer —
  monitoring, version tracking and tenant-level reporting across
  installations (§P) — plus the personal assistant over that portfolio.
- **Design transfer, not code transfer:** patterns proven in Friday are
  reference designs for external installations; the projects remain
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
ranges and runs the `friday_memory_extraction` prompt through the AI Gateway.
Approved, normal-sensitivity memories are injected into chat context
(`build_context`) under a workspace-only, injection-safe framing; `private`
memories never reach a gateway payload (the cloud-eligibility negative
contract). Every memory access — read, context injection, export — is
audited, and users can export everything they can read as a portable JSON
download; a second, filesystem-based Obsidian export shipped in MVP 5's
approval gate (see §U). Skills: **done — see §X.** Knowledge/RAG itself:
see §Q.

## G. Agent Runtime

Shipped in MVP 7 (§W): given a **goal**, Friday plans a bounded sequence of
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
`ModelUsage.routing_reason`) shipped in Friday 2.0 Phase 0, but the
purpose→tier map (`AI_PURPOSE_TIERS`) is still empty, so no call is
actually tier-routed yet; populating it together with the harness
job-distribution policy table is scheduled in Phase 5 (§AA).

MVP 10 (company workspaces: multi-user tenancy *inside* Friday — invite flows,
company membership management, company-scoped memory) is **cancelled
(ADR-008)**: Friday is a single-owner system and will never host a second
human user. The fleet phase may still add a lightweight external-installation
grouping "organization" concept. (This does not touch the workspace *axis* —
ADR-001 stays: the scoping column and `for_actor()` gate are already in the
data model and remain the isolation mechanism for firm/context profiles.)

Fleet/Organization Management at MVP 11 (vision only — §P). The in-repo
document-intelligence pack formerly occupying MVP 11–15 is removed from scope (ADR-006); MVP 12–15
are reserved. **The roadmap after MVP 9 is reorganized into Friday 2.0
Phases 0–6 (ADR-008, §AA, `docs/VISION_FRIDAY_2.md`).** Voice Assistant,
promoted to MVP 12 by ADR-007, is re-sequenced by ADR-008 into Phase 6 —
its shipped foundation (§Z) stands; orchestration/productization are
absorbed into the phase model (the agent harness *is* the orchestration
layer). **After Phase 6 the roadmap continues as the Friday 3.0 phases
(accepted 2026-11-20, §AA "Friday 3.0 phases"):** 8A → 9 → 8B → 10 → 11
→ 12, with Phase 7 (Workshop) kept as number 7 but moved to the backlog.

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
- ~~NotebookLM external gateway~~ — **rejected for runtime (Phase 5
  closeout governance pass, 2026-11-20):** every NotebookLM MCP is
  unofficial and cookie-authenticated; browser/Playwright MCP stays
  rejected too. Reasons in `docs/mcp/allowlist.md`.
- ~~Private conversation flag~~ — **resolved (Phase 1 branch 2,
  2026-07-19):** one-way `Conversation.private` (`assistant/models.py`),
  enforced at extraction input, context injection, the overlay and
  gateway payload assembly. A private conversation never becomes
  non-private again.
- Friday ↔ external-installation bridge (optional, post-ADR-006): if built,
  it takes the MCP route — Friday as MCP client of the installation's
  MCP-compatible tools, e.g. read-only access for Friday's personal
  assistant to data in an external workplace installation. Auth model,
  approval-gate treatment (ADR-004) and audit trail unresolved. **Reconciled
  with §AA (2026-09-27):** the *client* side is committed to Phase 5 via the
  generic MCP client framework (read-only tools first, writes gated); any
  installation-specific connection waits on that installation exposing an
  MCP server (VISION O-5, external dependency). The fleet phase (§P) stays vision-only.
  **Phase 5 closeout (2026-11-20):** the generic MCP client framework is
  built (pinned schemas, audited fenced reads, gated at-most-once writes);
  the `external_installation` manifest ships **blocked** (cannot be bound)
  until an installation exposes an MCP server — auth model and per-tool
  gates are then a manifest + `docs/mcp/external_installation.md` change,
  not a framework change.
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
- ~~Home Assistant bridge details (Phase 5)~~ — **resolved (Phase 5
  branch 7, recorded at the closeout 2026-11-20):** native HA REST API, not
  HA's MCP server (exact entity ids keep the gate display true, brief D8);
  a dedicated non-admin HA user's long-lived token in env only; reads are
  personal, web only; every service call is the gate-class
  `ha_call_service` inside a code-bounded service/data allowlist narrowed by
  `HA_ALLOWED_DOMAINS` (no confirm-class domain, Q7; scripts, automations,
  locks and alarms out). Bridge, never drivers (ADR-008) holds.
- Shared library extraction: whether the transferred patterns
  (`docs/DESIGN_TRANSFER.md` — gateway, scoping, lease-fenced jobs, approval
  gate) ever become a shared package across Friday and external products, or the projects
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
  with the local-first Ollama decision and ADR-006's personal/workplace infra
  separation. Wired as `openai_compatible`/local `ModelProvider` rows behind
  new `Capability.STT`/`.TTS`, no cloud key required. A cloud fallback stays
  possible later (same gateway, another provider row) but is not built. See
  §Z. *Phase 6 closeout (2026-11-20):* landed as the local `voice-stt-local`
  / `voice-tts-local` rows (speaches or the `deploy/voice/` sidecar behind
  the `openai_compatible` adapter, ADR-011); the cloud fallback now exists
  as the seeded-disabled `openrouter-stt`/`-tts` rows behind the d2 owner
  toggle (below).
- ~~Binary-audio storage mechanism (MVP 12, ADR-007)~~ — **resolved
  (2026-07-17): bounded temp-file, delete-on-completion.** Raw audio is
  written to a temp file only for the duration of the transcription job and
  deleted as soon as the transcript exists — raw audio is never persisted,
  so the dormant MinIO/`django-storages` settings stay dormant and no
  binary-blob column is added. Only the resulting transcript (already text)
  flows into the existing `assistant` pipeline. See §Z. **Superseded by
  ADR-011 (Phase 6, 2026-11-20):** transcription is synchronous and
  in-memory -- no Celery job, no temp file; raw audio is never written to
  disk, a DB row, a log or a `ModelUsage` by Friday (decision 3).
- ~~Friday 3.0 roadmap decisions d1–d10~~ — **decided 2026-11-20** (owner
  approved every recommended answer of
  `docs/research/2026-11-roadmap-proposal.md` §g; plan in §AA "Friday 3.0
  phases"):
  - **d1 Node shell:** a Swift menu-bar app (`friday-node`) with the Python
    voice pipeline as a child process (Phase 11). Tauri v2 only if a
    Windows node is ever needed. (ADR-013)
  - **d2 Audio to the cloud:** local by default; each cloud audio provider
    (e.g. OpenRouter STT/TTS) is a separate owner toggle
    (`personal_data_allowed`), never before the wake word, within a
    gateway-enforced audio cap (default 20 USD/month, ADR-003 amendment
    2026-11-20). Needed at the start of Phase 6 because Turkish TTS may
    need OpenRouter if Piper fails the listening test. This narrows the
    2026-07-17 "fully local" STT/TTS decision above: local stays the
    default, a cloud row is a deliberate owner act.
  - **d3 Always-on wake word / cloud live mode:** push-to-talk (and the
    in-tab browser wake word of Phase 6) until the Phase 11
    false-activation budget is met; always-on after that, with the
    indicator and fail-closed mute (ADR-015). Cloud live mode (OpenAI
    Realtime / Gemini Live) stays on the "later" list; if revived, only
    through a gateway WebSocket relay.
  - **d4 Third-party agent terms:** before any coding-agent driver is
    enabled, the owner checks whether the Claude plan's terms allow
    automating Claude Code / the Agent SDK from Friday (or an API key is
    needed), the same for Codex, and Antigravity's terms on wrapping
    `agy`. A driver whose terms are unclear stays off.
  - **d5 Default ingestion sources:** on in 8A — git commits of registered
    repos, harness outcomes, Claude Code hook events (metadata),
    calendar metadata; on in 8B, after the per-directory node grant —
    Claude/Codex transcripts (owner turns only); opt-in — shell history,
    mail bodies, Obsidian `Inbox/`; never — screen content, screenshots,
    AX trees, keystrokes, browser history. (ADR-012)
  - **d6 Auto-approve learned memories:** no. At most 10 proposals per
    night, each with a quote of the owner's own words; core-block changes
    approved one by one; only `work_log` stays auto-approved (ADR-008).
    Review after a month of use. (ADR-012)
  - **d7 Node action classes:** new ADR-014 (ADR-009 untouched):
    everything is gate-class (web approval + click on the Mac) except
    open/focus app (one click on the Mac) and read-only calls; test/lint
    may become one-click only with the throwaway-worktree sandbox
    evidence; dev servers stay gate-class.
  - **d8 Workshop:** keeps its number (7), deferred to the backlog after
    Phase 12.
  - **d9 Apple signing — revised 2026-11-25:** ~~yes, enrol (99 USD/year)
    before Phase 9; Developer ID signing + notarization~~. **Now:** no paid
    Apple Developer Program, no Developer ID, no notarization. `friday-node`
    runs only on the owner's own Mac and is installed from a local Xcode
    build, never distributed, so the app is signed with the free Apple ID
    **Personal Team** "Apple Development" identity (Xcode → Settings →
    Accounts). That is a stable signing identity, so TCC grants
    (Accessibility, Screen Recording, Full Disk Access, Microphone,
    Automation) and Keychain items should survive rebuilds; ad-hoc "Sign to
    Run Locally" is never used for a build that holds TCC grants. The first
    step of the Mac session is a spike that proves this on the real Mac
    (`docs/runbooks/mac-session-handoff.md` A1); if it fails (a needed
    entitlement is unavailable to a Personal Team, or grants or Keychain
    items do not survive a rebuild), the fallback is the original paid
    path (enrolment + Developer ID + notarization). Paid enrolment stays an
    optional later step, only if the owner ever wants to distribute to
    other Macs or use Sparkle-style auto-update. Unchanged: the signing key
    stays on the owner's Mac, CI never holds it. (ADR-013 amendment
    2026-11-25)
  - **d10 Raw episode retention:** 30 days for personal sources, 14 days
    for firm workspaces; derived memories keep only a provenance quote
    and the episode id; "forget source X" from day one. (ADR-012)
- ~~d11 Where the Friday server runs~~ — **decided 2026-11-26 (owner
  decision; ADR-017, Accepted 2026-11-26; Phase 13 "Home hub",
  `docs/prompts/phase-13-home-hub/brief.md`):** one always-on Friday server
  on a **home PC** (Linux, the Docker Compose stack: Django, Postgres +
  pgvector, Redis, Celery, node-gateway) on the home LAN. Friday is used
  only at home; every PC (Macs now, Linux/Windows later) is a paired node
  of that one server, so memory and identity are the same on every PC.
  Reach is LAN only by default, Tailscale optional, never public; node WSS
  stays TLS with a pinned server certificate or private CA (ADR-013
  unchanged); full-disk encryption on the server; nightly encrypted
  `pg_dump` + restic/age to an off-site target (Cloudflare R2 optional)
  with a tested restore; an explicit later migration path to a VPS (same
  Compose stack, restore from backup, re-point nodes) without changing the
  node protocol. Rejected: Supabase (hosted Postgres only, splits the
  stack, a third party), object storage as the database, the server on one
  Mac. Setup: `docs/runbooks/home-server-setup.md`. (The task text named
  the ADR "ADR-016"; that number is Phase 12's, so it is ADR-017.)
- ~~d12 Interim server, off-site target, disk unlock, direct home media~~ —
  **decided 2026-11-26 (owner decisions; ADR-017 amendment and ADR-018,
  Accepted 2026-11-26; Phase 13b, `docs/prompts/phase-13b-home-decisions/brief.md`):**
  until a home PC is bought the stack runs on the owner's MacBook (Docker
  Desktop/OrbStack; Friday is down while the Mac sleeps; the Mac's own node
  over the loopback TLS path; FileVault is an owner-confirmed preflight
  warning, never a failure); off-site backups go to **Cloudflare R2**
  (restic/age; resolves Phase 13 Q5); the future home PC unlocks LUKS with
  the **TPM** (PCR 7, recovery key off the server; a stolen whole machine
  booting is an owner-accepted residual; resolves Q6); and a
  **direct-execute home media class** (ADR-018, overriding Phase 13 Q1):
  play, pause, play_pause, stop, volume up/down/set (clamped, default max
  0.6) and mute on Home Assistant `media_player` entities the owner flags
  "direct" run without an approval card, also by voice — rate-limited
  (30/min, 10/min per entity), audited with origin and entity, behind an
  owner kill switch; everything else stays gate-class. CLAUDE.md rule 4
  names ADR-018 as its single exception.
- ~~OmniRoute (LLM router) adoption~~ — **rejected as a dependency
  (2026-11-20):** it would be a second door next to the AI Gateway
  (ADR-003). Its routing-trace and circuit-breaker ideas may be copied
  into `ai_core` later ("later" list in §AA).
- ~~morluto/rea adoption~~ — **pattern copied, tool deferred
  (2026-11-20):** if adopted later, it runs only as an exact-pinned tool
  inside harness discovery containers (read-only), each run behind an
  owner-authorization checkbox on the discovery request, producing a spec
  only (fenced data in the briefing, never applied code); allowlist +
  manifest first (`docs/security/mcp-governance.md`). On the "later" list.

## P. Fleet / Organization Management (MVP 11 — vision draft)

Vision + requirements draft only. **Detailed design is deliberately deferred**
— revisit once the personal kernel (through skills/integrations) is done and a
multi-installation setup is concrete. Friday monitors and manages multiple
external installations without hosting their data:

- Health/usage metrics per installation: uptime, error rates, queue depth,
  cost/usage aggregates exposed by the installation itself (its own
  ModelUsage-equivalent), pulled or pushed into Friday.
- Version tracking: which installation runs which release; upgrade planning
  and rollout status across the fleet.
- Tenant-based reporting: per-customer summaries surfaced through the
  operator's Friday assistant (chat: "how is customer X's installation
  doing?").
- Carried-over constraints: read-only by default; any action that touches an
  installation goes through the approval gate (ADR-004); all fleet access is
  audited; whatever fleet metadata Friday stores is workspace-scoped
  (ADR-001).

Open questions live in §O (API bridge, transport pull vs push, auth model
between Friday and installations).

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
passes: valid frontmatter, `source: friday_export`, matching workspace, a
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
line (§L), and, in the user's own words, "Friday's riskiest MVP so far."
Given a **goal**, Friday plans a sequence of steps and executes them
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
mirroring a common `prompt_key`+`prompt_version` usage-record pattern —
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
Friday had shipped through MVP 8 was reachable only through the web app,
authenticated by a password+JWT login. This MVP adds two more ways to
*reach* Friday — a bearer-token CLI and a relayed Telegram bot — without
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

> **Implemented by Phase 6 (closeout 2026-11-20).** The pipeline below
> shipped as `docs/prompts/phase-6-voice-identity/brief.md` branches 1–5
> and ADR-011 (voice transport) / ADR-015 (voice-originated turns). Two
> details changed from this draft: the async transcription job with a temp
> file became a synchronous, in-memory call (ADR-011 decision 3), and the
> "visual identity layer" became HUD v1 (`/hud`, brief A8). The section is
> kept as the original vision.

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
  `a78b6de`) — never wired into any external workplace product, same personal/company
  infra separation ADR-006 already draws. Ships ahead of the rest of MVP 12
  as low-risk foundation work.
- Speech in: push-to-talk mic capture in the web shell, transcribed to text
  and handed to the existing chat pipeline (`assistant/services.py`) — no
  new intent/routing logic, voice is a new *input transport* onto an
  unchanged conversation flow.
- Speech out: the assistant's existing streamed text reply synthesized to
  audio and played back.
- A visible "Friday" state indicator in the web shell (idle / listening /
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

- **Always-listening via a local wake word** ("Hey Friday"): detection runs
  on-device with an openWakeWord/Porcupine-class model — audio never leaves
  the machine and nothing is processed until the wake word fires. The goal
  is that everything the owner does by hand on the computer can be done by
  voice or text.
- **Visual identity comes after voice works:** the animated, speaking
  "Friday" interface layer (built on the dormant orb foundation) is the
  last layer — a visual shell before the pipeline works would be an empty
  costume.

## AA. Friday 2.0 Phases (ADR-008)

The post-MVP 9 roadmap. Full analysis and rationale:
`docs/VISION_FRIDAY_2.md` (approved 2026-07-17). Friday is a
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
| 3 | Harness v2: plan approval → worktree implementation → tests → cross-vendor review → gated branch push | Approved feature development end-to-end | **done (2026-09-27)** — branches 1–4 (2026-07-20 → 07-22) + closeout 5a/5–8 (2026-09-27); first real paid run is an owner runbook step |
| 4 | Security baseline; UI renewal: shell/chat/harness console/approvals inbox redesign; device-trust identity replaces signup/login; OpenAI-compatible facade (chat-only, low-trust) | Professional, safely-exposable daily driver | **done 2026-09-27** (`docs/prompts/phase-4-identity-ui/brief.md`, "Closeout"); six branches, each Sonnet 5 APPROVE, merged to `main` (former repository, PR #27) |
| 5 | Deploy gate + VPS deploy (allowlisted commands); MCP client framework: GitHub, mail/calendar, external-installation client (when one exposes MCP), Home Assistant bridge; tier routing + harness policy table | Request → code → live, end to end | **done (2026-11-20)** (`docs/prompts/phase-5-deploy-mcp/brief.md`, §10): branches 1–10 implemented on the integration branch `claude/inspiring-franklin-pw7avo` (former repository), which **supersedes** the former repository's `integ/phase-5` PR (#29); closeout adds `manage.py preflight`, the seeded `openrouter` provider and the harness → PR → deploy e2e. Owner steps pending: a green preflight on the real stack, real-credential smokes and the merge to `main` (brief §10 "Owner steps (pending)") Remaining (owner, `docs/runbooks/mac-session-handoff.md` C1, D4–D5): real-stack preflight, real-credential smokes, merge to `main`. |
| 6 | Voice (ex-MVP 12): mic capture, faster-whisper STT, Piper TTS, local wake word, orb wired to real audio; then the animated visual identity layer | The real Friday experience | **done (2026-11-20)** (`docs/prompts/phase-6-voice-identity/brief.md`, "6. closeout"): speech gateway (ADR-011), voice API, push-to-talk, in-tab wake word, HUD v1 implemented on the integration branch `claude/inspiring-franklin-pw7avo` (former repository); closeout adds the preflight voice checks and the voice/HUD e2e. Branch 3a (Turkish eval harness) is not on the integration branch yet, so the A9 exit measurements are pending: owner steps (brief "Owner steps (pending) — closeout") Remaining (`docs/runbooks/mac-session-handoff.md` A7, A8, A11, C2): branch 3a (server, not yet written), `hey_friday` training, the A4/A5/A8 Mac measurements. |
| 7 | Workshop (first module pack, `apps.modules.workshop`): sandboxed 3D stage, gateway-driven part-stream builder, change-never-restart conversation, teardown/labels/STL export, saved-build shelf, cost receipt, workshop drill; then picture-in (vision) and studio-photo finish (image capability) | "Friday, bana bir saat mekanizması tasarla" → live, editable 3D model | **deferred to backlog (2026-11-20, owner decision d8)** — number kept; scheduled after Phase 12 or earlier by owner decision (`docs/prompts/phase-7-workshop/brief.md`, written at phase start; design notes under "Roadmap update 2026-11-20" → "Backlog") |

Phases 8A–12 continue this table in "Friday 3.0 phases" below.

Phase briefs live in `docs/prompts/phase-N-<topic>/brief.md`, same
discipline as MVP briefs (branch tables, test strategy, closeout).

**Integration state (2026-09-27):** all Phase 0–3 work (119 commits) lives
on the shared session branch `claude/repo-setup-env-transition-1g94hx`,
not on `main`, and CI (push-to-main / PR only) had never run on it. Draft
integration PR (former repository, PR #26) was opened on 2026-09-27 so CI runs; a
green CI run and the merge of that PR are Phase 3 closeout gates.

**Review process (2026-09-27):** Codex CLI is retired from the development
workflow. Branch and brief reviews are done by independent Claude Code
subagents on Sonnet 5 (implementer ≠ reviewer model); earlier "Codex
code-review round N" notes in briefs are historical.

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
| 8 | closeout | Real-container smoke (actual Docker launch with a stubbed CLI, no paid calls) + owner runbook for the first paid run; CI green on the integration PR (former repository, PR #26); merge to `main` |

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
  (read first, send gated); external-installation client when O-5 unblocks; tier-routing map
  + harness policy table; remaining `work_log` producers (gated tools,
  tasks, deploys). "Social media via existing services" stays undefined
  until a concrete service is named — explicitly out of scope until then.
- **Phase 6 — voice.** Browser mic capture → gateway STT (local
  faster-whisper behind an OpenAI-compatible server) → assistant → TTS
  (local Piper) playback, orb wired to real audio levels; local wake word
  (in-browser, audio never leaves the device before the wake word); voice
  is a web-session channel but approval decisions still need an explicit
  on-screen confirm; then the visual identity layer.

### Friday 3.0 phases (accepted 2026-11-20)

Plan: `docs/research/2026-11-roadmap-proposal.md` (revision 2; the owner
approved every recommended answer of its §g, d1–d10, on 2026-11-20 — see
§O). Goal: a continuously learning Friday that knows itself, its
developer and the owner's work, talks (push-to-talk first, always-on
later), shows an animated HUD, watches and drives Claude Code / Codex
sessions, and acts on the Mac only with approval. The kernel rules do not
change: one gateway door with `ModelUsage` on every call, the approval
gate, `for_actor()` workspace isolation, Postgres as the source of truth.
New ADRs (all Accepted 2026-11-20): **ADR-012** continuous-learning
ingestion (privacy pipeline), **ADR-013** Friday Node protocol (identity
part conditional on spike S9.0), **ADR-014** node action classification,
**ADR-015** voice-originated turns; plus the ADR-003 amendment (audio
purposes, gateway-enforced budgets). ADR-011 stays reserved for Phase 6's
"voice transport". **ADR-017** home hub topology (Accepted 2026-11-26,
d11) was added with Phase 13 and amended 2026-11-26 with Phase 13b (d12);
**ADR-018** direct-execute home media class (Accepted 2026-11-26, d12) was
added with Phase 13b.

**Stable-numbering rule.** Existing phase numbers keep their meaning.
Phase 7 stays "Workshop" (deferred to the backlog; no brief or branch
exists, so nothing becomes ambiguous). New phases take numbers 8–12;
Phase 8 is split into 8A and 8B. Branches follow `feat/pN-<topic>` with
these numbers (`feat/p8-…` for both 8A and 8B, e.g. `feat/p8-core-blocks`,
`feat/p9-node-pairing`). The execution order is the row order below, not
the numeric order: **5 → 6 → 8A → 9 → 8B → 10 → 11 → 12 → 13 → 13b** (Phase 13b, the
owner decisions of 2026-11-26 (d12), follows Phase 13 and needs no new Mac
work before its server branches; Phase 13,
added 2026-11-26 by owner decision d11, is used right after the Phase 9 Mac
session — a real node must exist — while its server branches need no Mac). Each phase ends
in a go/no-go checkpoint: stop, re-scope or continue.

| Order | Phase | Scope | Usable outcome | Go/no-go checkpoint | Brief | Status |
|---|---|---|---|---|---|---|
| 1 | **5 (finish)** | 5.7 Home Assistant reconcile, 5.9 connectors UI, 5.10 closeout with preflight; `integ/phase-5` → `main`; seed an `openrouter` text provider row | Deploy + MCP usable from the UI | preflight green; integration branch merged to `main` | `phase-5-deploy-mcp/brief.md` | implemented 2026-11-20 (preflight + `openrouter` row landed; real-stack preflight and merge are owner steps, row 5 above); **remaining for the owner** (`docs/runbooks/mac-session-handoff.md` C1, D4): real-stack preflight, real-credential smokes, merge to `main` |
| 2 | **6 Voice core + web HUD** | Approved branches 1–4 + the 2026-11-20 amendment: OpenRouter audio spike, Turkish eval set, latency trace, gateway audio caps, ADR-015 voice cap, HUD v1 (health panel, accessibility) | Push-to-talk Friday in a browser with a real HUD, Turkish tested | Turkish WER/TTS targets met or consciously re-set; local p50 end-of-speech → first audio ≤ 1.5 s; caps enforced | `phase-6-voice-identity/brief.md` ("Amendment 2026-11-20", "6. closeout") | **done (2026-11-20)** — implemented incl. closeout (preflight voice checks, voice + HUD e2e); caps enforced in tests; the checkpoint's measurements (WER, TTS rating, Mac latency, one manual cap refusal) and branch 3a are pending owner steps (brief "A9 — exit criteria record"); **remaining for the Mac session** (`docs/runbooks/mac-session-handoff.md` A7, A8, C2): speech server, `hey_friday` training and pin, WER/TTS/latency/GPU passes; branch 3a is a server-side gap (A11) |
| 3 | **8A Learning memory core** (server only) | ADR-012 pipeline (source registry, workspace mapping, scrubber + corpus, TTL, audit view, forget-source); core blocks (`self` deterministic, `developer` owner-edit); sources: git commits, harness outcomes, Claude Code hook events via `friday-hook` (append-only PAT), calendar metadata; owner-only extraction; capped morning card; sessions panel v0 | **Vertical slice complete:** talk to Friday, it knows itself and you, shows your Claude Code sessions, learns from yesterday | scrubber corpus 100% secrets recall; zero cross-workspace leaks in tests; ≥ 60% of proposals accepted over 2 weeks (else tune before 8B) | `phase-8a-learning-memory/brief.md` | **done (2026-11-20)** — implemented incl. closeout (branches 1–6 + closeout: `manage.py preflight` learning checks, the commit + hook event → nightly job → morning card → approve → next chat answer e2e, runbooks `claude-code-hooks.md` / `learning-memory.md`); checkpoint in tests: scrubber corpus 100% secrets recall (CI gate + preflight self-test), zero cross-workspace leaks in the isolation tests; the ≥ 60% acceptance over two weeks of real use is a pending owner step (brief "Go/no-go record"); **remaining for the Mac session** (`docs/runbooks/mac-session-handoff.md` C3): real sources and mapping rules, `friday-hook` with the Keychain token, two weeks of morning cards (≥ 60% acceptance) |
| 4 | **9 Friday Node v1 (read-only)** | Prereqs: spike S9.0 (`kind=node` device trust), Personal Team signing spike (d9, revised 2026-11-25: free Apple ID "Apple Development" identity, no notarization), ADR-013. Then `nodes` app + WS, Swift menu-bar app, pairing, read capabilities, per-directory grants, Unix-socket hook relay, spool, heartbeat, preflight extension | "Claude Code / Codex oturumlarım ne yapıyor?" with local transcript detail | spike passes; compromised-server tests pass; a stably signed Personal Team build keeps its TCC grants across rebuilds | `phase-9-friday-node/brief.md` (full brief 2026-11-20; "Go/no-go record", "Mac session (deferred)") | **server half done (2026-11-20)** — branches 1–7 + closeout implemented (`kind=node` device trust, `nodes` app, protocol v1 + conformance suite against the Python reference node, WSS `node-gateway`, read tools, node-relayed hook events, `/devices` + HUD node line, `manage.py preflight` node checks, the pair → read → revoke e2e, `docs/runbooks/friday-node.md`); spike S9.0 server half and the compromised-server suite pass against the reference node; the Swift app (M1–M3) and the Mac go/no-go measurements (M4: Keychain half, Swift conformance, TCC across rebuilds signed by the same Personal Team identity) are deferred to a Mac session, **pending owner evaluation** — they do not block 8B's server branches; **remaining for the Mac session** (`docs/runbooks/mac-session-handoff.md` A1, B1–B3, C4): the Personal Team signing spike (d9 revised 2026-11-25), the Swift app M1–M3 against the conformance suite, the M4 measurements |
| 5 | **8B Transcripts + knowledge graph** | Claude/Codex transcript ingestion via the node (owner turns only); temporal entities/facts in Postgres; RRF retrieval; Graphify AST-only job; Headroom behind a flag + eval; Obsidian mirror format + `Inbox/` import-once; weekly reflection; private eval set | Friday knows the details of your work across tools | eval baseline recorded; Headroom stays off unless ≤ 1 point loss | `phase-8b-transcripts-graph/brief.md` (full brief 2026-11-20; "Go/no-go record", "Mac session (deferred)") | **server half done (2026-11-20)** — branches 1–9 + closeout implemented (temporal entities/facts with validity windows and the forget cascade, fact learning + reconciliation + weekly reflection, the eval harness + public seed, hybrid retrieval behind `MEMORY_RETRIEVAL_MODE` (kept `legacy`), the Graphify job, the Headroom sidecar (flag off), Obsidian mirror v2 + `00_Inbox`, transcript ingestion through the reference node, `/memory/graph`, `manage.py preflight` graph checks, the transcripts → fact → hybrid chat → vault → forget/restore e2e); scrubber corpus with transcripts at 100% secrets recall in CI and preflight; the `nodes`-backed transcript port is a follow-up branch; the Swift owner-turn readers (M1) and the private eval baseline, hybrid and Headroom compares and real-transcript run (M2) are deferred to a Mac session, **pending owner evaluation** — they do not block Phase 10's server branches; **remaining** (`docs/runbooks/mac-session-handoff.md` A12, B4, C5): the `nodes`-backed transcript port (server, no Mac), the Swift owner-turn readers M1, the private eval and transcript week M2 |
| 6 | **10 Control** | ADR-014 gate-class node capabilities (allowlisted repo commands under `srt`, AppleScript recipes, AX primitives; open/focus app confirm-class); node confirmation window; invocation grants; HUD terminal panel; harness v3 (Codex app-server + Agent SDK pinned, approval mapping, trust check); voice tools that only propose | "Testleri çalıştır", "Codex ile başlat": proposed by voice, approved on screen, run safely | fuzz/escape suites green; ADR-014 test/lint decision recorded | `phase-10-control/brief.md` (full brief 2026-11-20; "Go/no-go record", "Mac session (deferred)") | **server half done (2026-11-20)** — branches 1–7 + closeout implemented (protocol v2 with domain-separated, single-use ≤ 120 s invocation grants; capability contracts v2 + recipe catalogue; the Python reference node v2 with its confirmation double and `srt` policy generator; conformance v1/v2 + fuzz families + the 32-case escape suite (fake executor); the `node_action` gate with per-tool TTL, owner-only actor/approver hooks, gated and confirm-class invocation and chain row v2; the trust check (`control.trust_check`) and router proposals with the ADR-015 voice regressions; the HUD terminal panel over a scrubbed Redis stream; Codex app-server and Claude Agent SDK drivers with the `harness_agent_action` gate, off by default on provisional snapshots; `manage.py preflight` control checks; the typed → approval → grant → node window → HUD stream e2e plus voice-draft-only, confirm-class and kill-switch cases; runbooks `friday-node.md` (Control) and `harness-drivers.md`); fuzz and escape suites green against the reference node; **ADR-014 test/lint decision recorded: stay gate-class** (evidence pending owner evaluation, M3); the Swift confirmation window and executors (M1–M2), the macOS `srt` evidence run (M3), the Mac measurements (M4) and enabling a driver after d4 + real pins are **pending owner evaluation** — they do not block Phase 11; **remaining for the Mac session** (`docs/runbooks/mac-session-handoff.md` A6, B5–B6, C6, D6): `srt` pin, Swift confirmation window and executors M1–M2, the ADR-014 evidence run M3 and M4 measurements; driver pins on the Linux runner |
| 7 | **11 Local live voice** | ADR-015 in full; Pipecat child process of the node; always-on wake word; smart-turn; AEC; barge-in; nightly self-trigger test | Natural, interruptible voice anywhere on the Mac | FAR ≤ 0.2/h on 24 h household audio; self-trigger test green; latency targets met | `phase-11-live-voice/brief.md` (full brief 2026-11-23; "Go/no-go record", "Mac session (deferred)") | **server half done (2026-11-20)** — branches 1–7 + closeout implemented (the `node_voice` credential minted by the node secret, `CHANNEL_NODE_VOICE` on exactly the D4 surfaces, live sessions; `node/friday-voice` with `friday-voice/v1`, the reference host and both conformance families plus `conformance.lock`; the Pipecat pipeline with gateway STT/chat/TTS clients, replay harness and baseline; barge-in and the self-trigger rule on the echo-cancelled signal; the always-on gate (`VoiceWakeEvaluation`, latest row per model), trigger and storm backstops (`VoiceLiveMute`, 423); the CI self-trigger release gate and the FAR/FRR harness; the `voice.state` event and HUD mic state; `manage.py preflight` live-voice checks; the reference host + `friday-voice run` + live server e2e (push-to-talk, node action as a card only, barge-in, self-trigger at residual 1.0, kill switch, storm); runbook `live-voice.md`); always-on stays off (the D11 gate closed) until the owner records evaluations — the Swift voice host (M1), the MLX speech server (M2), the nightly self-trigger (M3) and the FAR/FRR/latency/barge-in measurements (M4) are **pending owner evaluation** (Mac session) — they do not block Phase 12; **remaining for the Mac session** (`docs/runbooks/mac-session-handoff.md` A8–A10, B7–B8, C7): the node model fill (`make voice-models-node`, implemented 2026-11-27 on `feat/p11-node-voice-models`, A10), the Swift voice host M1, loopback + nightly M3, MLX speech server M2, FAR/FRR/latency/barge-in/CPU M4 |
| 8 | **12 Reach and proactivity** | n8n MCP (allowlisted workflows); heartbeat/cron + spoken morning digest; SKILL.md skills + curator; procedural playbooks from harness runs | Friday starts useful conversations and uses your automations | owner keeps the digest on after 2 weeks | `phase-12-reach/brief.md` (full brief 2026-11-20; branches `feat/p12-proactive-core` (probe registry, unattended deny guard, owner-only schedules, notices, ADR-016), `feat/p12-morning-digest`, `feat/p12-wake-routes`, `feat/p12-n8n-connector` (relay sidecar, workflow-ID allowlist, async reads, gated writes, ADR-010 amendment), `feat/p12-skills-core`, `feat/p12-skill-curator`, `feat/p12-harness-playbooks`, `chore/p12-closeout`; "Go/no-go record", "Mac session (deferred)"; runbooks `proactive.md`, `n8n-connector.md`, `skills.md`) | **server side done (2026-11-20); go/no-go checkpoint in progress** — branches 1–7 + closeout implemented (the `proactive` app: `core.probes` registry, the unattended deny guard in every effect's owning service, owner-only web schedules ("cron cannot create cron"), notices, the number-checked morning digest with template fallback, `/today`, the bell, HUD `notices` + "Dinle", `proactive_checkpoint`; wake-phrase routes on web voice and `node_voice`; the n8n connector (relay sidecar, workflow-ID allowlist, async reads + one follow-up, gated writes; ADR-010 amendment); SKILL.md instruction skills with per-version owner approval, the matcher, the weekly curator and provenance-checked playbooks from harness runs attachable to implementation jobs; ADR-016 (Proposed); `manage.py preflight` proactive/n8n/skills checks; the reach e2e (heartbeat → digest → `/today`, "günaydın"/"bildirimler" through the reference host + `friday-voice run`, n8n async read and gated write, SKILL.md upload → approve → used, three fake jobs → playbook, every D2 effect denied in one unattended run); runbooks `proactive.md`, `n8n-connector.md`, `skills.md`); unattended-deny and hostile-skill gates green in CI; the two-week digest checkpoint, the owner's live n8n and the Mac verification run (M1) are **pending owner evaluation** — they do not block the backlog; **remaining** (`docs/runbooks/mac-session-handoff.md` C8, C9, D5, D7): the spoken digest and wake routes on the Mac M1 (after Phase 11 M1), live n8n, the two-week digest checkpoint |
| 9 | **13 Home hub** (added 2026-11-26, d11; executes right after the Phase 9 Mac session — the node must exist) | ADR-017 topology: one always-on server on a home Linux PC (Compose stack, LUKS, LAN-only, private-CA TLS, nightly age/restic off-site backups + restore drill, VPS path); multi-node first class (rooms, names, roles, per-node presence/catalogues/audit, "which PC" resolution: explicit name → speaking node → last active → ask; one node per invocation, never broadcast); Linux node `friday-node-linux` (read-only, same conformance suite; Windows deferred); Home Assistant media/TV + scene/light control behind an owner entity allowlist, gate-class; home voice routes that only propose; home preflight rows | One Friday for the whole home: every PC sees the same memory, the right PC answers, the TV is one approved tap away, and the data survives a dead disk | two weeks on the home server from ≥ 2 PCs with zero wrong-node invocations; a restore drill from the off-site backup passed | `phase-13-home-hub/brief.md` (full brief 2026-11-26; branches `feat/p13-node-registry`, `feat/p13-node-targeting`, `feat/p13-hud-presence`, `feat/p13-linux-node`, `feat/p13-ha-home-control`, `feat/p13-home-voice-routes`, `feat/p13-home-server-ops`, `chore/p13-closeout`; "Go/no-go record", "Mac session (deferred)"; runbook `home-server-setup.md`) | **server side done; go/no-go pending owner evaluation** (`chore/p13-closeout`, 2026-11-26) — brief, ADR-017 (closeout note) and `docs/runbooks/home-server-setup.md` final; branches 1–7 implemented and merged on `integ/phase-13`; closeout: `check_home_control` preflight rows (`nodes_labels`, `ha_entities`, `linux_node_vendor`, `node_protocol_frozen` — no protocol change since the Phase 12 closeout), the home-hub e2e (`backend/ops/tests/test_home_hub_e2e.py`: reference node v2 + packaged Linux node on one live TLS server, voice origin, the TV approved once, backup → drill); **remaining for the Mac session / owner hardware** (`docs/runbooks/mac-session-handoff.md` Part E, E1–E4): Swift pairing `platform` + server re-point (M1), the real-hardware multi-node and home-route run (M2), the home server on the owner's PC with a real restore drill (M3) |
| 9b | **13b Owner decisions 2026-11-26** (d12; follows 13) | Interim server on the owner's MacBook (Docker Desktop/OrbStack; macOS-tolerant home preflight, `HOME_SERVER_PLATFORM`; the Mac's own node over the loopback TLS path); off-site backups to Cloudflare R2 (destination checked by preflight, credentials host-only); TPM auto-unlock (PCR 7, recovery key off the server) for the future home PC; ADR-018 direct-execute home media class (play/pause/play_pause/stop/volume/mute on owner-flagged `media_player` entities, no card, also by voice; rate limits, volume clamp, kill switch, audit with origin) | Friday usable at home now on the Mac, backed up to R2; pausing the film is one sentence | two weeks of daily direct media with zero out-of-class direct calls, the kill switch proven, and a passing drill from the R2 copy | `phase-13b-home-decisions/brief.md` (full brief 2026-11-26; branches `docs/p13b-brief`, `feat/p13-media-play-pause-stop`, `feat/p13-direct-media-model`, `feat/p13-direct-media-exec`, `feat/p13-direct-media-ui`, `feat/p13-interim-mac-server`, `chore/p13b-closeout`; "Go/no-go record", "Mac session (deferred)"; runbook `home-server-setup.md` "Interim: server on the MacBook") | **server side done; go/no-go pending owner evaluation** (`chore/p13b-closeout`, 2026-11-20) — brief (branch table, go/no-go rows), ADR-018 (Accepted 2026-11-26; closeout note), the ADR-017 amendment, ADR-014/015 cross references, the CLAUDE.md rule 4 exception sentence and the runbook "Interim" / R2 / TPM sections; server branches 1a (`media_play_pause`/`media_stop`, manifest v3), 1b (flag, settings, call log, closed set + digest, classifier, rate limits, owner API), 1c (executor, code-only `home_direct` intent and provenance locks, voice/`node_voice`/typed paths), 1d (HUD line, `/connectors` controls) and 2 (macOS-tolerant preflight, R2 destination check, portable scripts, LaunchAgent, `compose.mac.yml`) implemented on `integ/phase-13`; closeout: the `ha_direct_media` preflight row (`check_home_direct`), the home-direct e2e (`backend/ops/tests/test_home_direct_e2e.py`: `node_voice` through the reference voice host + a real `friday-voice run` child, web voice and typed direct; unflagged → card → one approved call; kill switch; rate limit; 0.6 clamp; `node_protocol_frozen`; a macOS preflight with R2 status files), `home-server-setup.md` step 12 rows; **remaining for the Mac session / owner hardware** (`docs/runbooks/mac-session-handoff.md` Part F, F1–F4): the interim server on the real Mac with R2 backups (M1), the Mac node over loopback and the real TV (M2), the home PC with TPM unlock and the move off the Mac (M3) |
| — | **7 Workshop** (number kept) | Unchanged intent (see "Backlog" below) | — | after 12, or earlier by owner decision | written at phase start | **backlog (d8)** |

**Status as of 2026-11-20 — server side vs the Mac session.** Every
server branch of phases 5, 6, 8A, 9, 8B, 10, 11 and 12 is implemented on
the integration branch `claude/inspiring-franklin-pw7avo` (former
repository; in this repository the integration branch is `integ/phase-13`) and tested
against Python reference doubles (the reference node, the reference voice
host, fake providers). What remains needs the owner's MacBook, accounts,
money or real data, and is collected in one ordered checklist,
`docs/runbooks/mac-session-handoff.md` (branch `docs/mac-session-handoff`,
implemented 2026-11-20): **A** prerequisites (the Personal Team signing
spike first — d9 revised 2026-11-25, no paid enrolment or notarization —
then tools, the stack, `srt`, the speech server,
`hey_friday`, the long recordings); **B** the Swift `friday-node` branches
in order — Phase 9 M1–M3, 8B M1, Phase 10 M1–M2, Phase 11 M1 and the M3
loopback — each with the server-side conformance and contract tests it
must pass; **C** the hardware measurements and go/no-go evaluations
(Phase 5 real stack, Phase 6 A9, 8A acceptance, Phase 9 M4, 8B M2,
Phase 10 M3–M4, Phase 11 M2–M4, Phase 12 M1 and the digest checkpoint);
**D** the owner-only steps that are not Swift work. Writing the checklist
found three **server-side gaps** that need no Mac and come first: Phase 6
branch 3a (`feat/p6-voice-eval`) is not on the integration branch; the
`nodes`-backed transcript port (8B closeout owner step 2) is not written;
and the node model fill named by the Phase 11 brief and runbook had no
implementation (closed 2026-11-27: `make voice-models-node`).

Estimates (illustrative, one owner with Claude implementing and a Sonnet 5
review loop per branch): 5 → 8A (the vertical slice) about 3–4 months;
9 → 10 about 3–4 months more; 11–12 after that. The whole plan is likely
more than 12 months. Projected learning + voice running cost: about
13–30 USD/month, capped by gateway budgets (learning 20 USD/month, audio
20 USD/month by default).

**Later list (not scheduled):** cloud realtime live mode (gateway WS relay,
second ADR-003 amendment); desktop WKWebView overlay for the HUD;
Antigravity driver; ACP generic driver; DSPy/GEPA prompt optimisation;
speaker verification; Cua/VM computer use; LiveKit multi-device/phone;
Chatterbox Turkish TTS benchmark; WhatsApp; OmniRoute's routing-trace and
circuit-breaker ideas copied into `ai_core` (§O: OmniRoute itself
rejected); morluto/rea as an exact-pinned tool inside harness discovery
containers only, owner-authorization checkbox per run, spec output only
(§O).

**Not dependencies (ideas copied only):** OpenClaw, Hermes, Nod, Ruflo
(avoided entirely), OpenJarvis, LiteLLM, OmniRoute.

### Roadmap update 2026-11-20 — lessons adopted from two external "Friday" prompt packs

> The full Friday 3.0 plan accepted the same day supersedes the ordering
> implied here: see "Friday 3.0 phases" above and
> `docs/research/2026-11-roadmap-proposal.md`. The preflight and voice
> lessons below stand (the voice ones are folded into the Phase 6
> amendment of 2026-11-20); the Workshop paragraph moved to "Backlog".

Two third-party prompt packs ("Give Your Friday a Workshop" and "Friday 3D
Builds: Draft to Showpiece", Nov 2026) describe a single-file hobby Friday
(Python server + browser viewer, direct Anthropic SDK calls, keys in a
`config.json`). Their architecture is not adoptable — it bypasses the AI
Gateway (ADR-003), keeps no ModelUsage (rule 3), and its browser Web Speech
API sends audio to a cloud recognizer (contradicts Phase 6 D5). Their
*operational lessons* are sound and are folded into the plan as follows:

- **Preflight (Phase 5 closeout, then every phase; implemented
  2026-11-20 in `backend/ops/`, `make preflight`):** `manage.py preflight`
  — live end-to-end checks against the running stack, not mocks: web and
  BFF up, Postgres/pgvector/Redis/Celery reachable, every seeded provider
  answers one minimal gateway call (recorded as ModelUsage, purpose
  `ops.preflight`), the configured model ids are reachable, MCP pins match,
  served frontend build == built build, secrets not reachable from the
  browser, a `?mute=1` voice tab is silent. One summary line `N pass / N
  fail / N warn`, non-zero exit on failure. Each later phase adds its own
  checks; "done" in a closeout means preflight passed. A service that is
  not configured on the machine running it reports `warn`, never `fail`;
  the voice-tab check is a standing `warn` until Phase 6 builds the voice
  layer; `--no-model-calls` skips the per-provider gateway calls.
- **Voice (Phase 6 branch 3/4 additions):** an end-of-speech *finish
  window* (`VOICE_FINISH_MS`, default 900 ms — a mid-sentence pause appends
  instead of dispatching); short interrupt words ("dur", "bekle", "stop")
  bypass the buffer; a mute flag checked inside the single speak function
  (tests never hear audio); deterministic *voice routes* checked before the
  brain (instant client-side commands — never approvals, D8 stands); "a yes
  is not a done": a spoken confirmation only references an action whose
  receipt (task id, approval id, execution status) exists; voice routes for
  "hatırla ki …" (memory capture through the existing ADR-005 service) and
  "bu ne kadar tuttu?" (ModelUsage receipt). Under ADR-015 (2026-11-20)
  a voice route that writes ("hatırla ki …") creates a proposal, never an
  approved row.

#### Backlog

- **Phase 7 — Workshop (first module pack; deferred to backlog
  2026-11-20, owner decision d8, number kept):** generative 3D builds
  as an optional `apps.modules.workshop` pack (ADR-002's first module
  pack; kernel never imports it). Laws carried from the packs and mapped to
  ours: model-written code runs only in a sandboxed, separate-origin
  iframe (`sandbox="allow-scripts"`, strict CSP, `connect-src 'none'`,
  postMessage only — an ADR records it, and a drill test fails loudly if
  `allow-same-origin` ever appears); every build/change/classify call goes
  through the gateway with its own purpose (`workshop.build`,
  `workshop.change`, `workshop.classify`) and tier; text inside a picture
  is data, never an instruction; a change re-sends only the changed parts,
  never restarts the build; the stage's guard pass (NaN/overbright pixel
  clamp) and label-on-real-vertex rules; saved builds replay without a
  model call; a per-build cost receipt from ModelUsage. Later branches:
  picture/floor-plan in (vision), studio photograph / miniature finish via
  a new image capability behind the gateway. Webcam hand tracking stays
  out of scope until the voice layer is in daily use.

**Carried-forward items (not yet scheduled in a branch):** ~~owner core
memory block; memory consolidation/distillation (ADR-005, VISION R5)~~ —
scheduled 2026-11-20 in Phase 8A (core blocks `self`/`developer`/
`preferences`/…, nightly capped consolidation; weekly reflection in 8B);
shared per-turn query embedding; SSE log streaming for harness; revise-
per-feedback loop; shared harness execution-engine extraction (its
three-call-site trigger is now met — evaluate in Phase 5); dynamic runner
registry (needed when a second runner appears); Gemini CLI / Ollama as
harness agents; configurable branch-name prefix. From the Phase 4
closeout: facade binding retention (`FacadeConversation` pruning); a
shared (not per-process) BFF pre-auth limiter; `fork_conversation`'s
no-usable-cut fallback copying the whole conversation; approvals list
pagination; e2e retry isolation; the owner's manual Open WebUI smoke.
(MVP 9 persisted-draft reconstruction landed in Phase 4 branch 4.)
