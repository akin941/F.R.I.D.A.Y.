# MVP 6 — Intent Router: The "Understands Me" Layer

> Status: **done (2026-07-10)** — all 5 branches merged to `main`, approved
> by the user after a 3-round Codex plan-review loop (stopped deliberately
> at round 3 on clear convergence — see Risks/notes). Every branch's own
> Codex code-review loop closed with 0 CRITICAL/HIGH findings (branch 1: 2
> rounds; branch 2: 1 round, clean; branch 3: 2 rounds; branch 4: 2 rounds).
> `docs/MASTER_PLAN.md` §V records the final summary; §O's "AI-suggested
> task extraction (MVP 4)" Open Decision is marked resolved/amended. Scope
> agreed with the user after an explicit disambiguation exchange: "router"
> in the roadmap (`docs/MASTER_PLAN.md` §L) means **intent routing**
> (understand a chat message, dispatch it to the right subsystem), not
> model-tier/cost routing. Model-tier routing (a DB-backed cheap/mid/strong
> tier table + `ModelUsage.routing_reason` auditability, serving
> `docs/cost/token-optimization.md`'s "send cheap" layer) is a **separate,
> deferred item** — a future Gateway sub-task or its own MVP, not part of
> this brief. Do not conflate the two; this brief's `purpose="router.intent"`
> gateway call uses today's ordinary priority-ordered provider selection,
> nothing tier-aware.
>
> The user explicitly required the router↔agents boundary to be justified
> from existing docs, not invented — see "Router vs. agents: the autonomy
> boundary" below. If plan-review or implementation ever finds this
> boundary doesn't hold up in practice, flag it as a **contradiction**
> against this brief rather than quietly resolving it differently.

## Goal and user value

Today, every Friday capability (memory, knowledge, tasks, the one gated
tool) is a separate, manually-triggered UI surface — the user has to know
which page to go to and which button to click. This MVP makes chat itself
the front door: the user says what they want in plain language, and Friday
figures out which subsystem that concerns and acts on it — for reads,
directly; for writes, by drafting the action and asking for a one-click
confirmation before anything happens. Nothing here creates a new
autonomous write path: every write this MVP can trigger already exists
today behind an explicit human click (task creation, gated-tool approval
requests) — the router only adds a faster way to *reach* that same click,
never a way to *skip* it.

## Router vs. agents: the autonomy boundary

The roadmap (§L) lists `tools/approvals → router → agents` without
defining where router ends and agents begin. This brief resolves that from
two things already on record, not from scratch:

- MVP 4's brief (Open Decisions, "AI-suggested task extraction"): *"the
  roadmap places tools/approvals and **agents** right after tasks
  specifically so 'the assistant creates a task' can be modeled as a real,
  approval-gated tool call once that infrastructure exists."*
- MVP 5's own brief header: *"every gated action in this MVP is
  human-requested, not agent-requested — the gate is proven out end-to-end
  ... **before MVP 7 gives an agent the ability to request one**."*

Both anchor the distinction to **autonomy**, not to *understanding*:

- **Router (this MVP)**: one user utterance → one classified intent → one
  dispatched action, always inside the same request/response turn the
  human is actively present for. It can *read* on the user's behalf
  (memory/knowledge lookups) and it can *draft* a write for the user to
  confirm (task, gated tool) — but it never acts without that specific
  utterance and, for writes, an explicit follow-up click. It has no memory
  of "goals" beyond the current turn and cannot chain multiple actions on
  its own initiative.
- **Agents (MVP 7+)**: given a goal, plans and executes **multiple** steps
  over time, potentially without a human typing each one — this is what
  ADR-004 and the MVP 5 brief mean by "agent-requested": the request
  originates from the agent's own reasoning about a broader goal, not from
  a human's literal words in that moment. This is also where "auto-approval
  ... based on observed error rates" (ADR-004) would ever become relevant —
  never in this MVP.

Concretely: router recognizes "toplantı ekle" and asks "create *this*
task?" — that's still the human's one utterance turning into one action
they explicitly confirm. An agent deciding, unprompted, that it should
also create three follow-up tasks because it inferred a broader goal — that
crosses into MVP 7 territory this brief does not touch.

## Scope

### 1. Intent classification (`assistant` — foundation, no new writes)

A new gateway call, `purpose="router.intent"`, runs before the existing
chat context-build step (`assistant.services.build_gateway_stream`) for
every user message. Schema-constrained JSON output (same discipline as
`friday_memory_extraction`'s prompt — a fixed shape, not free text),
classifying the message into exactly one of:

- `chat` — the default/fallback; ordinary conversation, no dispatch.
- `memory_query` — the message is asking about curated memory content.
- `knowledge_query` — the message is asking about the knowledge base
  (e.g. "summarize", "what does my knowledge base say about X").
- `task_draft` — the message describes a task/reminder to create.
- `tool_request` — the message names an action a registered gated tool
  performs (today: `vault_export`).

Classification failure (malformed JSON, gateway error) always degrades to
`chat` — a router that can't classify must never block or corrupt the
underlying conversation; the existing chat flow is the safe fallback for
every failure mode here, not a new error path.

**Call ordering (Codex plan-review round-1 finding, MEDIUM — the original
draft left this ambiguous):** classification must run as its own step
*before* both (a) the user `Message` row is persisted and (b) the main
reply/streaming call begins, since its result changes both. The exact
sequence `ConversationMessageListView.post()` follows becomes:

1. Classify (`purpose="router.intent"`, blocking, non-streaming) —
   independent of and prior to `build_gateway_stream()`.
2. Call `build_gateway_stream()` (builds context/history + sets up the
   lazy gateway generator — this does not itself perform any streaming
   yet) — its position **relative to persisting the user `Message` row is
   deliberately unchanged from today**, precisely so `build_context()`'s
   history query (which runs before the new row exists, then appends
   `new_user_content` once, by hand) is never at risk of double-counting
   the message being sent. Confirmed correct in Codex round-1 branch-1
   review (originally flagged MEDIUM as a documentation ambiguity against
   this section's earlier wording, not an actual bug — this wording is the
   fix).
3. Persist the **user** `Message` row with the classification result
   attached (see below) — still before the assistant reply begins, exactly
   like today, just now carrying `intent`/`intent_payload` at creation
   time instead of being a bare content row.
4. Return the `StreamingHttpResponse`; the (possibly intent-aware-framed —
   see §2) reply/streaming call actually executes once Django iterates it,
   after the view returns.

`assistant.Message` gets two new fields mirroring the existing
`memories_used`/`chunks_used` discipline (ordered, immutable once written,
persisted so a page reload doesn't lose them): `intent` (the classified
value, blank for pre-MVP-6 rows and for the `assistant`-role rows —
classification is an interpretation *of the user's own message*, so it
belongs on the **user** `Message` row, never the assistant's reply row)
and `intent_payload` (JSONField, shape depends on `intent` — empty for
`chat`; `{title, due_date}` for `task_draft`; `{tool_name, payload}` for
`tool_request`).

**`intent_payload` never holds a created resource's id (Codex plan-review
round-2 HIGH finding — the original draft contradicted itself here: it said
`intent_payload` is written once and immutable, but also said the
`tool_request` case stores "the created `ApprovalRequest` id," which
cannot exist yet at message-persist time since confirmation (§4) hasn't
happened — and can never be written afterward without breaking the
immutability this field is supposed to have).** `intent_payload` is
strictly the frozen classification/extraction output — never mutated,
never referencing anything created later. What a confirmed draft actually
produced (a `Task` row, an `ApprovalRequest` row) is visible through its
own existing surface (`/tasks`, `/approvals`) exactly as if it had been
created through the manual UI — this MVP does not add a persisted link
from the originating `Message` back to that resource. One accepted
consequence, spelled out rather than silently left as a bug: reloading the
page before confirming re-shows the same draft card in a confirmable
state; if the user had already confirmed it in an earlier session/tab,
confirming again creates a second `Task`/`ApprovalRequest`. For
`task_draft` this is a harmless duplicate row (identical in effect to
manually creating the same task twice). For `tool_request` the duplicate
is still just a second, fully audited, still-gated `ApprovalRequest` —
never a duplicate *execution*, since execution only ever follows a
separate admin approval either way. Deduplication is not designed here;
flag in review if this is judged unacceptable rather than treating it as
already decided.

This branch wires classification and persistence end-to-end but **every
intent still falls through to the existing chat.reply flow** — no
dispatch side effects yet (mirrors MVP 5 branch 1's own staged pattern:
prove the new mechanism's plumbing and persistence before any branch wires
real side effects into it). `intent`/`intent_payload` are recorded but
ignored by the response path in this branch.

### 2. Read-dispatch: `memory_query` / `knowledge_query`

**Honesty check before scoping this:** knowledge retrieval
(`knowledge.context.select_context_chunks`) already runs on *every* chat
turn today, query-embedded against the latest user message — intent
classification doesn't unlock a retrieval capability that didn't exist.
Memory injection (`memory.context.select_context_memories`) is similarly
always-on today, but it is **not** query-driven at all (recency/type
priority only — memory has no embeddings, unlike knowledge, which shipped
semantic search in MVP 3). This MVP does **not** add memory search; see
Out of scope.

Given that, the real, honest scope for these two intents is **intent-aware
framing of retrieval that already runs**, not a new retrieval mechanism:

- `knowledge_query`: widen `select_context_chunks`'s budget (more chunks,
  larger character budget) for this turn only, and add an explicit system-
  prompt instruction telling the model the user specifically wants an
  answer grounded in retrieved knowledge.
- `memory_query`: add the equivalent explicit framing instruction for the
  (unchanged) curated-memory block already being injected.

Both are additive parameters to the existing `build_context` call, not new
selection algorithms. `context_report` gains an `intent` field recording
which framing (if any) was applied, for the same auditability every other
context decision already gets.

### 3. Confirmable draft: `task_draft`

**This section was substantively revised after Codex plan-review round 1
(HIGH finding) — the original draft directly contradicted an existing,
explicit decision and is recorded here so the contradiction and its
resolution are both visible, not quietly patched over.**

The original draft said task-draft confirmation calls the existing tasks
API "with no approval gate involved." That is inconsistent with MVP 4's
own Open Decisions entry: *"the roadmap places tools/approvals and agents
right after tasks specifically so 'the assistant creates a task' can be
modeled as a real, approval-gated tool call... whichever MVP takes this up
must also add a `source`/sensitivity field... with a conservative backfill
... otherwise old manually-created tasks would silently become
gateway-eligible by default the moment that machinery exists."* Every
`Task` row today is created by a human with no `source` split at all
(`tasks/models.py`'s own docstring: *"every row is user intent, there is
no AI-suggested source... nothing in this MVP reads either"*) — MVP 6
introducing an LLM-drafted task without addressing that is exactly the
gap that Open Decision was written to prevent.

**Resolution — two separate questions, not one:**

1. **Does task creation need the full `tools.registry`/`ApprovalRequest`
   gate (like `tool_request`, §4)?** No. ADR-004 gates *"write / delete /
   deploy / external-send"*-class actions — actions with real, often
   irreversible external or destructive consequences. Creating a `Task`
   row in the user's own workspace is none of those: it's fully
   reversible, scoped to the user themselves, and structurally identical
   in risk to the create the user could do by hand right now on `/tasks`.
   Routing it through the approval-gate machinery would misapply ADR-004
   to a class of action it was never meant to cover, and would need a
   *second* human decider — that person doesn't exist for a personal
   workspace today (the same gap MVP 5's brief already recorded for
   `vault_export`), which would make chat-drafted tasks *permanently
   unusable* in a personal workspace, not merely gated. That is a worse
   outcome than the (real, separate) provenance gap identified below.

   **This is a formal, explicit amendment to MVP 4's Open Decision, not a
   quiet reinterpretation of it (Codex plan-review round-2 HIGH finding —
   round 1's fix addressed the provenance half of that decision but left
   its "approval-gated tool call" half unresolved, which is not the same
   as resolving it).** The Open Decision's own goal — never let an
   AI-drafted task pass as an ordinary one, and never silently change what
   old tasks are eligible for — is fully met by the `source` field and
   backfill below. Its literal mechanism (route through the tool/approval
   gate) is *narrowed*, deliberately and on the record, to apply only where
   ADR-004's actual risk class applies: this MVP's `tool_request` path
   (§4) *does* go through exactly that gate, because a gated tool's
   effects are the ADR-004 class this decision was written for. Task
   creation isn't. When this MVP ships, `docs/MASTER_PLAN.md` §O's entry
   gets marked resolved with a note pointing here — mirroring how that
   section already marks resolved items (`~~Exact embedding model choice
   (MVP 3)~~ — resolved: ...`) — so the amendment has the same paper trail
   the original decision did, not just an argument buried in this brief.
2. **Does it need the `source`/sensitivity provenance the Open Decision
   demanded?** Yes — this part of the original draft was simply wrong to
   skip. `Task` gets a `source` field this MVP, mirroring `Memory.source`'s
   existing naming exactly: `Task.Source.USER_CREATED` /
   `Task.Source.AI_SUGGESTED` (`TextChoices`, same vocabulary as
   `memory/models.py`). A migration backfills every existing row to
   `user_created` (Django's own default-value migration mechanism — no
   custom backfill script needed, mirroring how `memories_used`/
   `chunks_used` needed no backfill when introduced) — satisfying the Open
   Decision's explicit backfill requirement so no pre-MVP-6 task silently
   becomes anything it wasn't. Only chat-drafted, confirmed tasks ever get
   `ai_suggested`. Cloud-eligibility policy for `ai_suggested` tasks is
   intentionally deferred with a documented default (treat identically to
   `user_created` for now — nothing in this MVP's tasks flow injects task
   content into a gateway payload the way memory/knowledge context does,
   so there is no cloud-eligibility decision this MVP actually needs to
   make yet; the field exists so a future MVP that *does* need one has
   somewhere to hang it, per the Open Decision's own phrasing).

**Due-date extraction needs the client's local date (Codex plan-review
round-1 finding, MEDIUM).** `Task.due_date` is deliberately a client-local
calendar date, never server time (`tasks/models.py`'s own docstring, and
the exact lesson MVP 4's Today page already learned — "today" is always
computed in the browser, never the server, per that branch's Codex
finding). A `router.task_draft` prompt asked to resolve "yarın" (tomorrow)
or "Cuma" (Friday) needs to know what day *the user's own calendar* says
today is — the chat POST currently sends only `content`
(`assistant/serializers.py`). This MVP adds an optional
`client_local_date` field to that same POST (mirroring the exact
`todayLocalDate()` convention `/today` already uses), threaded into the
`router.task_draft` prompt as the reference date for relative-date
resolution. If it's ever missing (an older client, a malformed value),
extraction falls back to only resolving explicit absolute dates and
leaving relative ones (`due_date: null`) rather than guessing against
server time — a `null` due date the user can fill in on `/tasks` after
confirming is a safe degradation; a wrong day picked from the wrong
timezone is not.

With that resolved: the classification step extracts a structured draft
(title, optional due date) via the same schema-constrained-JSON discipline
`friday_memory_extraction` already established — a second, small,
purpose-specific prompt (`purpose="router.task_draft"`), not a repurposing
of the classification call's own output (classification decides *that*
it's a task; a focused second call decides *what* the task should contain,
mirroring memory extraction's own "classify, then extract" shape).

The assistant's streamed reply still contains a short natural-language
acknowledgment (e.g. "Looks like you want a task — confirm below"); a new
`draft` SSE event carries the structured draft — see §5 for exactly how
this relates to the existing `chunk`/`done`/`error` events (Codex
plan-review round-2 finding: the original draft called it "a terminal
event... alongside" the others without saying whether it replaces `done`,
which would have been a real regression). The frontend renders a confirm/
discard card. Discard is a pure client-side no-op — nothing was created,
so there is nothing to undo.

**Confirm does not pass a client-asserted `source` (Codex plan-review
round-2 MEDIUM finding).** The original draft had confirm call
`POST /api/tasks` "passing `source=ai_suggested`" directly — but
`TaskCreateSerializer`/`create_task` deliberately treat provenance-like
fields as server-controlled (`status` and `created_by` already are, per
that serializer's own comment), precisely so an arbitrary caller can never
assert a provenance value it doesn't actually have. Letting the *client*
say "this one's AI-suggested" would let any caller claim that for any
task, which defeats the entire point of adding `source` in the first
place. Resolved instead: `TaskCreateSerializer` gains an optional
`draft_message_id` (UUID) — a *reference*, not a claim. On create, if
present, the server resolves that `Message` via `for_actor()` (the same
workspace-scoping every other lookup in this codebase uses), verifies it
belongs to this actor's own conversation *and* has `intent="task_draft"`,
and only then sets `source=Task.Source.AI_SUGGESTED` internally — the
server decides, from a fact it can verify itself, never from anything the
client merely asserts.

**Failure contract, corrected after Codex plan-review round 3 (MEDIUM
finding — the original draft's "fail toward the less interesting value"
framing was backwards for this specific field):** `draft_message_id`
*absent* is the ordinary manual-create path, unchanged, `source` defaults
to `user_created` exactly as today. `draft_message_id` *present but
failing verification* (message not found, wrong actor, wrong workspace,
wrong `intent`) is a **400, no `Task` row created at all** — not a silent
downgrade to `user_created`. Silently creating the task anyway (just with
the "wrong" provenance) would mean a confirm click that should have
produced an `ai_suggested` task can silently produce an indistinguishable
`user_created` one instead — exactly the outcome the Open Decision this
section already amends was written to prevent (an AI-drafted task passing
as an ordinary one). A hard failure surfaces the bug instead of masking
it. (Round 3 also flagged an "already resolved to something else" clause
in an earlier revision of this list — removed: this MVP deliberately
tracks no resolution state at all for a draft (§1's accepted
duplicate-on-reload note), so that condition described a check nothing in
this design can actually perform; the real, implementable list is exactly
the four above.)

A misclassification here costs the user one ignored card, never a stray
write: no task is created unless they explicitly click confirm.

### 4. Confirmable action: `tool_request`

**Two findings from Codex plan-review round 1 revised this section — both
were left as open questions in the original draft, which is exactly what
plan-review is for; both are now decided, not open.**

**Confirm-before-request is now required, not optional (round-1 HIGH
finding).** The original draft's own stated goal is that writes are
"drafted... asking for a one-click confirmation before anything happens" —
but §4 as originally written let the router call
`approvals.services.create_request` immediately on classification, with
"require confirm" only a "proposed default." Creating an `ApprovalRequest`
is not the gated *action* itself, but it **is** a durable, audited
workspace mutation (a real row, a real audit event, a real entry on
another admin's pending-approvals list) — auto-creating one from an
unconfirmed classification contradicts the brief's own stated design, not
just a stylistic inconsistency. Resolved: symmetric with `task_draft`,
classification produces a draft request (tool name + payload) surfaced as
a confirm/discard card; only on confirm does the router call
`create_request`. Denied-role and unknown-tool-name both resolve to "no
side effect, degrade to plain `chat`" — mirroring `task_draft`'s own
misclassification-is-free guarantee, and consistent with `create_request`'s
existing behavior (it already raises `InvalidTransition` for both an
unregistered tool name and a non-writer role; the router must not call it
speculatively and must not surface a raw service exception to the user —
classification failing to resolve a real tool degrades exactly like a
malformed-JSON classification does in §1, straight to `chat`).

**Tool resolution does not gain a new registry-search capability (round-1
MEDIUM finding).** The original draft implied `ToolSpec` already carries
enough metadata (name/description) for a classifier to search against —
it doesn't; `tools/registry.py`'s `ToolSpec` has `name`, `gated`,
`create_execution`, `dispatch`, `validate_request`, `describe_execution`,
nothing resembling a searchable description. Adding one *to the registry*
for a single-tool MVP would be exactly the premature abstraction
CLAUDE.md's simplicity rule warns against (mirrors `tools/registry.py`'s
own module docstring reasoning for why it's a plain dict and not a DB
model at all). Resolved instead: a small, `assistant`-local, read-only
helper — `tools.registry.list_tool_names()` (the only new registry-surface
addition; still no metadata, just names) — combined with a short
natural-language description of each known tool maintained in the
router's *own* prompt-construction code (not the registry), used purely to
build the classification prompt's tool vocabulary. `vault_export` is
resolved by exact name/synonym match against that one hardcoded
description for this MVP; a real multi-tool resolution mechanism (fuzzy
matching, embeddings-based tool search, etc.) is explicitly deferred to
whenever a second tool actually exists to prove it against — see Out of
scope.

The router calls **`approvals.services.create_request`** on confirm — the
exact function the "Export to Obsidian vault" button already calls. Chat
becomes a second front door onto that one function, not a bypass of it:
the human is still `requested_by`, `requester != decider` is still
enforced with zero exceptions (ADR-004), and the resulting `ApprovalRequest`
shows up on `/approvals` exactly like a button-created one, including the
existing own-request gating and run-status indicator (MVP 5).

### 5. Frontend

**`draft`'s exact place in the SSE stream (Codex plan-review round-2
MEDIUM finding — the original draft called it "a terminal event...
alongside" `chunk`/`done`/`error` without saying which).** `done` keeps
its existing contract completely unchanged — every reply, regardless of
intent, still ends with exactly one `done` carrying `message_id`, usage,
`memories_used`, `chunks_used`, precisely as today, so no frontend code
that only understands `chunk`/`done`/`error` regresses. `draft` is a new,
*non-terminal*, optional event emitted once, after the streamed
acknowledgment's `chunk` events and *before* `done` — only for `task_draft`
and `tool_request`. The stream shape becomes `chunk...chunk → draft
(optional) → done`, never `draft` instead of `done` and never `draft`
after `done`.

**`draft`'s payload must carry the source user message's id (Codex
plan-review round-3 MEDIUM finding — the original draft never specified
this, and without it the frontend has no correct id to send back as
`draft_message_id`/the tool-request equivalent on confirm).** The user
`Message` row is already persisted, with its `id` known, *before*
streaming begins (§1's revised ordering: classify → persist user message
→ reply) — so the view already has this id in hand when it later builds
the `draft` event. Payload:
`{"type": "draft", "kind": "task" | "tool", "source_message_id": "<uuid>",
...intent-specific fields}` (`title`/`due_date` for `kind: "task"`;
`tool_name`/`payload` for `kind: "tool"`). The frontend's confirm action
sends `source_message_id` back verbatim as `draft_message_id` (tasks) or
the equivalent reference (tool requests) — never a client-side-generated
or optimistic id, since (per the existing chat UI's own optimistic-render
behavior) the only id guaranteed to match the server's persisted row is
the one the server itself handed back in this event.

- New SSE handling for the `draft` event (see ordering and payload shape
  above), added alongside existing `chunk`/`done`/`error` handling in the
  chat composer, not replacing any of it.
- A draft/request card component (confirm/discard for `task_draft`;
  status display for `tool_request`, reusing `ApprovalCard`'s existing
  run-status rendering where relevant).
- Confirming a `task_draft` reuses the existing `POST /api/tasks` BFF
  route, extended with the optional `draft_message_id` reference (§3) —
  no *new* route, an extended request body on the existing one.
- Confirming a `tool_request` reuses the existing
  `POST /api/approvals/requests` BFF route exactly as the manual "Export
  to Obsidian vault" button already does (MVP 5) — no new route there
  either, since that endpoint already accepts `tool_name` + a payload.

### 6. E2E + docs

- Playwright smoke extension covering all three live dispatch behaviors:
  a knowledge-intent message that gets wider-budget framing, a task-draft
  confirm that creates a real task, and a tool-request that creates a real
  `ApprovalRequest` (approved the same way MVP 5's own smoke section
  already does, via `DEV_ALLOW_SELF_APPROVAL` + an in-page fetch, not a UI
  click that doesn't exist for the requester).
- MASTER_PLAN.md: new section (mirrors §Q/§T/§U's structure), §L roadmap
  line update, and an explicit note that model-tier routing was
  deliberately kept a separate, unstarted line item — not folded into this
  MVP's "done" status.

## Out of scope (explicit)

- **Model-tier/cost routing** (cheap/mid/strong tiers,
  `ModelUsage.routing_reason`). Explicitly a different concern — see the
  status line at the top of this brief. Do not let plan-review or
  implementation quietly merge the two.
- **Real memory search.** `memory_query` gets framing, not retrieval —
  memory has no embeddings and this MVP does not add them. If targeted
  memory search is ever wanted, it is a separate, sizable feature (its own
  brief), not a side effect of this one.
- **Agent-initiated, multi-step, or unprompted actions of any kind.** See
  "Router vs. agents" above — this is the hard line for this MVP.
- **Auto-approval of any gated action**, confidence score or not (ADR-004:
  "never for the hard rules"; this brief's `tool_request` path always ends
  at the *existing* pending-approval state, never an auto-approved one).
- **New tools, or a general tool-search mechanism.** `vault_export`
  remains the only registered gated tool; `tool_request` classification
  proves the dispatch mechanism via one hardcoded name/description match
  (see §4's revised resolution mechanism), not a searchable registry or a
  general-purpose tool-calling framework. Fuzzy/embeddings-based tool
  resolution is deferred to whenever a second tool actually exists to
  prove it against.
- **`Task.source`'s cloud-eligibility policy.** The field is added this
  MVP (see §3's revised resolution) so `ai_suggested` provenance exists,
  but no MVP-6 code path actually branches on it for gateway-eligibility
  purposes — nothing in the tasks flow injects task content into a
  gateway payload the way memory/knowledge context does today. Defining
  that policy is left to whichever future MVP first needs it.
- **Editing or retrying a draft.** Confirm or discard only — no inline
  edit of the extracted title/date before creating the task. A rough draft
  the user can still edit afterward (on `/tasks`, as today) is an
  acceptable v1; inline editing is a UX enhancement, not a blocker.
- **Classification-latency mitigation** (parallel classification +
  speculative chat-reply, cheap heuristic pre-filter before the LLM call,
  etc.). This MVP accepts a blocking classification call before every
  chat turn as the simplest correct baseline; see Risks below for why this
  is flagged, not silently accepted.

## Branch plan

| # | Branch | Contents |
|---|---|---|
| 1 | `feat/mvp6-router-foundation` — **done (2026-07-10)** | `purpose="router.intent"` classification call (schema includes a `tool_name` field, populated only for `tool_request`), `Message.intent`/`intent_payload` on the **user** message row, revised call ordering (classify → persist user message → reply), seed data, classification-failure-degrades-to-chat behavior, no dispatch side effects yet |
| 2 | `feat/mvp6-read-dispatch` — **done (2026-07-10)** | `memory_query`/`knowledge_query` intent-aware framing (widened budget + explicit instruction), `context_report` gains `intent` |
| 3 | `feat/mvp6-task-draft` — **done (2026-07-10)** | `Task.source` field + migration (default `user_created`, no custom backfill needed) + MASTER_PLAN §O amendment note, `client_local_date` threaded from the chat POST into a `router.task_draft` extraction call, `draft` SSE event (non-terminal, before `done`), `TaskCreateSerializer`'s server-verified optional `draft_message_id` (never a client-asserted `source`), frontend confirm/discard card |
| 4 | `feat/mvp6-tool-request` — **done (2026-07-10)** | `tools.registry.list_tool_names()`, router-local tool description map, confirm-before-request card (never auto-request), wiring the confirm action into `approvals.services.create_request`, frontend status card reusing `ApprovalCard` |
| 5 | `feat/mvp6-e2e-docs` — **done (2026-07-10)** | Playwright smoke extension (all 3 live behaviors), MASTER_PLAN + brief closeout |

Branches 3 and 4 both depend on branch 1's classification plumbing but not
on each other — order between them is not load-bearing.

## Test strategy

- Classification: schema-parsing tests mirroring `memory.extraction`'s own
  (valid shape, malformed JSON degrades to `chat`, gateway error degrades
  to `chat`), fixture-driven per-intent examples for the deterministic
  `FakeAdapter` (mirrors `ai_core/tests/test_fake_adapter.py`'s existing
  seed/query fixture pattern).
- Read-dispatch: `context_report["intent"]` correctness, budget-widening
  behavior verified with real chunk/memory fixtures exceeding the default
  (non-widened) budget.
- Task draft: `Task.source` migration/backfill test (every pre-existing
  row reads `user_created` after migrating), extraction schema tests
  including the `client_local_date`-present/absent/malformed cases (the
  absent/malformed case must degrade to `due_date: null`, never a
  server-time guess), SSE `draft` event ordering and payload (always
  before `done`, never instead of it, always carries `source_message_id`),
  `draft_message_id` verification tests — the security-relevant ones for
  this branch: **absent** `draft_message_id` creates a normal
  `source=user_created` task (today's unchanged path); a **valid**
  same-actor, same-workspace `intent="task_draft"` reference yields
  `source=ai_suggested`; a **present-but-invalid** reference (missing
  message, wrong actor, wrong workspace, wrong `intent`) returns 400 with
  **no `Task` row created at all** — never a silent downgrade to
  `user_created` (this is the test that must never regress, mirroring how
  `test_the_execution_field_does_not_cost_a_query_per_row`-style
  regression tests anchor a round's finding in MVP 5's own precedent).
- Tool request: resolution-against-the-hardcoded-description-map tests
  (unknown/unrecognized tool name degrades to `chat`, never a broken
  dispatch or a raw exception), a test proving no `ApprovalRequest` row is
  created until confirm (the round-1 HIGH finding this section exists to
  close — this is the one test that must never regress), `create_request`
  call-through tests on confirm (same requester/decider and role-matrix
  tests MVP 5 already has, exercised via this new entry point instead of
  the manual button — no new permission logic, so no new permission
  *bugs* to invent tests for, but the entry point itself needs coverage,
  including a denied-role confirm degrading cleanly rather than leaking a
  service exception to the chat UI).
- E2E: full round trip for all three live behaviors against the real
  Docker stack, not just CI (mirrors the MVP 5 branch 5 discipline of
  actually running `make test-e2e`, not trusting CI alone).

## Risks / notes

- **Classification-latency trade-off (flagged for plan-review, not
  silently accepted).** A blocking `purpose="router.intent"` call before
  every chat turn adds a full extra gateway round-trip's latency to every
  single message, including plain chit-chat that classifies as `chat`.
  This is the simplest correct baseline and is deliberately *not*
  optimized in this MVP (see Out of scope) — but it is a real, user-
  visible UX cost this brief is not hiding. If plan-review considers this
  unacceptable, the fallback is a cheap rule-based pre-filter (obvious
  task/tool keyword matches shortcut straight to `chat` without an LLM
  call) — deliberately not designed here so plan-review can weigh in on
  whether it's needed at all before this brief commits to the added
  complexity.
- **Resolved during plan-review, recorded here so it isn't relitigated
  (round 1):** the original draft let `tool_request` auto-create an
  `ApprovalRequest` on classification alone and left `task_draft`
  bypassing the `source`/provenance requirement MVP 4's Open Decisions
  entry already demanded for exactly this scenario. Both are now
  confirm-gated / marked `ai_suggested` (see §3, §4).
- **Resolved during plan-review, recorded here so it isn't relitigated
  (round 2):** round 1's fix for `task_draft` addressed provenance but not
  the "approval-gated tool call" wording of the Open Decision it cites —
  §3 now states explicitly that this is a formal, on-the-record amendment
  to that decision (narrowing its mechanism to where ADR-004's risk class
  actually applies), not a quiet reinterpretation, and commits to marking
  `docs/MASTER_PLAN.md` §O resolved when this MVP ships. Separately,
  `intent_payload` briefly (self-)contradicted its own "immutable, written
  once" contract by claiming to store a created `ApprovalRequest` id that
  couldn't exist yet at persist time — fixed to store only the frozen
  draft/request content, never a downstream resource's id (see §1). And
  `task_draft` confirmation briefly let the *client* assert `source`
  directly — fixed to a server-verified `draft_message_id` reference
  instead (see §3). If a future round of review reopens any of these,
  treat it as revisiting a deliberate decision, not discovering an
  oversight — the reasoning for each is recorded in its own section
  above.
- **Resolved during plan-review, recorded here so it isn't relitigated
  (round 3):** round 2's `draft_message_id` fix had its own failure
  contract backwards — a present-but-invalid reference silently downgraded
  to `user_created` instead of rejecting the write, which could let a
  confirm click silently produce an ordinary-looking task instead of the
  `ai_suggested` one it should have, exactly the outcome the whole
  amendment exists to prevent; fixed to a hard 400/no-write on that
  specific failure (§3). The `draft` SSE payload never actually specified
  which message id the frontend should send back on confirm — fixed by
  requiring `source_message_id` (the already-persisted user message's id)
  in the payload itself (§5). And an "already resolved to something else"
  verification clause was quietly describing a check this design has no
  state to perform (no resolution tracking exists at all — see §1's
  accepted-duplicate note) — removed rather than left as an
  unimplementable requirement.
- **Two new prompt purposes join the existing two** (`chat`,
  `memory.extraction`): `router.intent` and `router.task_draft`. Both need
  `seed_ai_defaults` entries and count toward the same "no gateway call
  skips ModelUsage" guarantee (ADR-003, CLAUDE.md rule 3) as every purpose
  before them — a review-blocking defect if either ever calls a provider
  outside `InferenceGateway`.
- **This MVP touches `assistant`, the one app every other kernel app's
  context builder already reaches into** (`memory.context`,
  `knowledge.context`) — the one-way import-linter contracts already in
  place (`memory must not import assistant`, `knowledge must not import
  assistant`) must hold unchanged; `assistant` doing more orchestration
  work here does not relax that direction.
- Whether `intent`/`intent_payload` need backfill for pre-MVP-6 `Message`
  rows: no — both default to blank/empty, and nothing reads them as
  "must be present," mirroring how `memories_used`/`chunks_used` needed no
  backfill when MVP 2/3 introduced them.
