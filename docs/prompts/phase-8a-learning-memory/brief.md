# Phase 8A — Learning memory core (server only)

> Status: **Draft (2026-11-20) — written on `docs/friday-3-roadmap` from
> the owner-approved Friday 3.0 roadmap; awaits its independent Sonnet 5
> plan review (the implementing model never reviews its own brief).**
> Implementation waits for the precondition below.
> Parent decisions: ADR-001 (workspace axis, `for_actor()`), ADR-002
> (import contracts), ADR-003 + its 2026-11-20 amendment (single gateway
> door, one `ModelUsage` per call, gateway-enforced budgets), ADR-004 +
> ADR-008 decision 3 (gate-class vs confirm-class), ADR-005 (memory
> governance, `private` contract) as extended by **ADR-012**
> (continuous-learning ingestion, Accepted 2026-11-20), ADR-008 decision 4
> (`work_log`), ADR-009 (reversible owner deletes, gated purge), ADR-010
> (connectors, personal-data provider rule), ADR-015 (voice turns propose
> only), MASTER_PLAN §AA "Friday 3.0 phases" (order 3) and §O (d5, d6,
> d10), `docs/research/2026-11-roadmap-proposal.md` §B4, §c.6, §c.9,
> §c.10.
> **Precondition — hard:** Phase 6 closeout merged to `main`. 8A consumes
> the gateway budget primitive (Phase 6 branch 1, ADR-003 amendment), the
> HUD v1 panel slots and the `hud` leaf app (Phase 6 branch 5), and the
> Phase 5 connectors (`connectors.bridges.google` calendar reads, Phase 5
> branch 8). Phase 8A branches are cut from `main` and integrate on
> `integ/phase-8a`. Branch names use `feat/p8-…` (MASTER_PLAN stable-
> numbering rule).

## Goal / value

Close the Friday 3.0 **vertical slice**: talk to Friday (Phase 6), and it
knows itself, knows its developer, shows what the owner's Claude Code
sessions and harness jobs are doing, and learns from yesterday's work —
without weakening a kernel rule. 8A adds always-in-prompt **core memory
blocks** (`self`, `developer`, `current_focus`, `active_projects`,
`preferences`), a server-side **ingestion pipeline** behind ADR-012's
privacy rules (source registry, workspace mapping, scrubber, TTL, audit
view, forget-source), four default sources (d5: git commits of registered
repos, harness outcomes, Claude Code hook events, calendar metadata),
owner-authored-only extraction with checked provenance, and a capped
nightly proposal card (d6). Usable outcome (MASTER_PLAN §AA): "Sen kimsin,
seni kim geliştiriyor, şu an ne yapıyoruz?" answered from approved blocks;
"Claude Code oturumlarım ne yapıyor?" answered from hook events and harness
jobs; each morning at most 10 proposals, each quoting the owner's own
words.

## What already exists (verified 2026-11-20 on the integration branch)

- **Memory rows and their governance.** `memory.Memory`
  (`backend/memory/models.py:56`): types `semantic`/`episodic`/
  `preference`/`procedural`/`work_log`, `sensitivity` `normal`/`private`,
  statuses `pending_approval`/`approved`/`rejected`/`archived`, sources
  `ai_suggested`/`user_created`/`system_generated`, per-workspace
  `fingerprint` uniqueness, `privacy_locked`, pgvector `embedding` with a
  lease-fenced embedding job. Services (`memory/services.py`):
  `create_memory` (:190), `record_work_log` (:231, the only auto-approved
  path), `approve_memory`/`reject_memory`/`restore_memory`/
  `forget_memory`/`delete_memory` (:380–474, `delete_memory` takes a
  required `channel`), `record_read`/`record_export` audit helpers.
- **Chat extraction (Phase 1).** `MemoryExtractionJob`
  (`memory/models.py:189`) over a fixed message range
  (`memory/jobs.py:138`), run by `memory/tasks.py:_process_claimed_job`
  (:143): it sends **both user and assistant messages** to the gateway
  (`purpose="memory.extraction"`, prompt `friday_memory_extraction`,
  tier `mid`), parses at most `MAX_CANDIDATES_PER_JOB = 10` candidates
  (`memory/extraction.py:25`), never of type `work_log`, and writes them
  `pending_approval`. Candidates carry no provenance quote today.
- **Context assembly.** `assistant.context.build_context`
  (`assistant/context.py:248`) injects the `friday_system` prompt, the
  firm-profile block (`Workspace.profile`, `FIRM_PROFILE_CHAR_BUDGET =
  2000`), curated memories (`memory.context.select_context_memories`,
  `MEMORY_CHAR_BUDGET = 4000`), the audited personal overlay
  (`select_overlay_memories`, web channel only, single-member workspace
  only) and RAG chunks, all fenced with `neutralize_fence`/
  `strip_invisible_formatting` (`memory/context.py:350,368`). There is
  **no owner core block**: it is a carried-forward item (MASTER_PLAN §AA),
  scheduled here.
- **Gateway.** `InferenceGateway.complete(purpose, messages, *, actor,
  workspace, prompt_version, stream, params, context_report, on_usage,
  personal_data)` (`ai_core/gateway.py:134`); `personal_data=True` keeps
  only `personal_data_allowed` providers and fails closed. Tier map
  `AI_PURPOSE_TIERS` and `AI_UNTIERED_PURPOSES`
  (`config/settings/base.py:397,418`) with a coverage test
  (`ai_core/tests/test_purpose_coverage.py`): every `purpose=` literal in
  production code must be in one of them.
- **Producers that already call memory.** `harness/execution.py:587` and
  `harness/implementation_execution.py:842` call `record_work_log` at a
  terminal state; `tools/work_log.py:70` and `tasks/services.py:195` do
  the same. `HarnessJob.goal` (`harness/models.py:101`) is the owner's
  typed request; the briefing summary is model-produced text.
- **Repo registry.** `harness.HarnessRepo` (`harness/models.py:31`):
  workspace-scoped, `enabled`, `sensitive`, `git_url`, `default_branch`;
  `harness/runner.py:118` `clone_repo` (read-only shallow clone with
  env-resolved credentials) runs on the owner's Mac runner beat
  (`config/celery.py`, `HARNESS_RUNNER_BEAT`).
- **Tokens and channels.** `accounts.PersonalAccessToken.Kind` is
  `cli`/`openai` (`accounts/models.py:102`), mapped to channels in
  `accounts/authentication.py:62` (`KIND_CHANNELS`); each authenticator
  accepts exactly one kind. `deploys` gets its PAT authenticator injected
  by `config/urls.py` because it may not import `accounts` — the
  precedent 8A follows. The stdlib-only CLI client lives at
  `backend/integrations/cli/friday_cli.py`.
- **Trash and purge.** ADR-009 soft delete (`deleted_at`, `all_objects`),
  `TRASH_RETENTION_DAYS` (`config/settings/base.py:372`), the gate-class
  `purge_trash` tool (`tools/purge.py`).
- **Import contracts** (`backend/pyproject.toml`): `memory` must not import
  `tools`, `assistant`, `agents`, `harness`, `connectors`, `deploys`; no
  app imports `harness`; `connectors` may import `memory`; `tools` and
  `assistant` may import `memory`.
- **Calendar reads.** `connectors/bridges/google.py` (Phase 5 branch 8),
  personal-class data (Phase 5 D5).

## Boundary vs existing systems

- **Memory stays the single owner of memory rows.** Ingestion is a new
  package `memory.ingestion` inside the `memory` app. Producers that live
  in apps `memory` may not import (`harness`, `connectors`, the HTTP hook
  endpoint's authenticator) call a memory-owned port
  (`memory.ingestion.ports.record_episode`), exactly like
  `record_work_log` today. Memory never reaches into them.
- **Every learned row is a proposal.** No new auto-approved provenance:
  `record_work_log` stays the only one (ADR-008 decision 4). Core-block
  changes are proposals too; `developer` is written only by the owner.
- **The chat turn keeps its shape.** Core blocks are one more fenced block
  in `build_context`, injected first. No new intent, no new SSE event.
  Two read tools are added to the existing registry (D12).
- **The approval gate keeps its shape.** Approving a proposal is the
  existing confirm-class memory approval (a human click, web channel).
  Forget-source is a reversible confirm-class trash move (ADR-009);
  permanent purge is the existing gate-class `purge_trash`.
- **Workspace axis unchanged.** Every new table is a
  `WorkspaceScopedModel`; every view and read tool goes through
  `for_actor()`; firm items land only in the firm workspace (ADR-001).
- **Server only.** No Mac app, no transcripts, no knowledge graph, no
  Headroom, no Obsidian `Inbox/` import: those are Phase 9 and 8B.

## Decisions (with justification)

### D1 — `memory.ingestion` package and one port

`backend/memory/ingestion/` holds the models' services, the scrubber, the
mapping resolver, the extraction and consolidation jobs and their Celery
tasks; models live in `memory/models.py` (one app, one migration history).
The port:

```
record_episode(*, source, kind, external_id, occurred_at, mapping_key,
               owner_text="", context_text="", metadata=None,
               sensitivity="normal") -> EpisodeResult
```

It resolves the workspace (D4), scrubs (D3), dedupes on
`(source, external_id)`, enforces the source's enabled state, and returns
`stored | dropped_unmapped | dropped_paused | duplicate`. `owner_text` is
the only field extraction may quote; `context_text` is fenced context.
Producers decide which of their text is owner-authored (D5) and the port
records it as such; nobody else writes `Episode` rows. Justification: the
same single-choke-point discipline as `record_work_log`, and it keeps the
`memory ↛ harness/connectors` contracts intact.

### D2 — Data model

- `IngestionSource` (scoped to the owner's personal workspace): `kind`
  (`git`, `harness`, `claude_code_hooks`, `calendar`), `enabled`,
  `paused_at`, `daily_token_budget` (default 40 000), `created_by`,
  `forgotten_at`. One row per kind in 8A, created disabled by a seed
  command; the owner enables each (d5 lists them as "on", so the closeout
  runbook enables all four).
- `WorkspaceMappingRule` (personal workspace): `source`, `rule_type`
  (`repo` → a `HarnessRepo` id string, `path_prefix`, `calendar_binding`),
  `pattern`, `target_workspace` (FK; must be a workspace the owner owns,
  validated in the service), `enabled`.
- `Episode` (`WorkspaceScopedModel`, the **target** workspace): `source`
  (FK), `kind`, `external_id`, `occurred_at`, `owner_text` (scrubbed,
  ≤ 4000 chars), `context_text` (scrubbed, ≤ 4000), `metadata` (JSON,
  scrubbed values, bounded), `sensitivity`, `scrub_report` (counts per
  placeholder type, never the matched text), `token_estimate`,
  `expires_at`, `deleted_at` (ADR-009 trash), `extracted_at`. Unique
  `(source, external_id)`. Default manager excludes trashed and expired
  rows; `all_objects` for the trash view and purge.
- `ScrubTerm` (`WorkspaceScopedModel`): owner-maintained literal terms
  (customer names, project code names) replaced with `[TERM:<id>]`
  before storage in that workspace.
- `IngestionCounter` (`WorkspaceScopedModel`, per source per day):
  stored, duplicate, dropped_unmapped, dropped_paused, scrubber hits by
  type, tokens spent, proposals created/accepted/rejected — the audit
  view's data (D11); counters, never content.
- `Memory` gains `ingestion_source` (FK, null), `source_episode_id`
  (UUID, plain field — the episode may expire, d10) and
  `provenance_quote` (≤ 300 chars). Forget-source finds derived
  memories through `ingestion_source`, so it still works after episodes
  have expired.
- `CoreMemoryBlock`, `CoreMemoryBlockVersion`, `CoreBlockProposal`: D7.

### D3 — Scrubber before storage

`memory/ingestion/scrubber.py`, pure functions, no network: (1) secret
rules — provider key prefixes (`sk-`, `sk-ant-`, `ghp_`, `github_pat_`,
`xox[baprs]-`, `AKIA…`, `AIza…`), JWTs, PEM blocks, `KEY=value` lines
whose key matches `(?i)(secret|token|password|passwd|api[_-]?key|
private[_-]?key)`, URLs with credentials, and a Shannon-entropy check for
long base64/hex runs; (2) PII — e-mail, Turkish and E.164 phone numbers,
TCKN (11 digits with the official checksum), IBAN (mod-97), card numbers
(Luhn); (3) the workspace's `ScrubTerm`s. Hits become typed placeholders;
`scrub_report` keeps counts only. It runs inside `record_episode` before
any row is written, and a second time on every text the extraction job
sends to the gateway (defence in depth against a later rule addition).
**Corpus:** `memory/tests/scrubber_corpus/` — synthetic API keys and
tokens for every rule, `.env` dumps, Claude Code hook payload fragments,
commit messages with pasted keys, shell history, Turkish PII; every item
labelled. CI requires **100% recall on the secrets class** and reports
PII recall/precision; a regression in secrets recall fails the build.
Justification: ADR-012 decision 3; no third-party scanner is added (a
dependency would need the governance manifest and runs per item in the
hot path).

### D4 — Workspace mapping: drop, never default

`resolve_workspace(source, mapping_key)` tries the source's enabled rules
in a fixed order (exact repo id, longest `path_prefix`, calendar binding)
and returns a workspace or `None`. `None` → the item is **dropped**, the
`dropped_unmapped` counter is incremented, and the mapping key is recorded
in the audit view's "unmapped" list as a hash plus a scrubbed short form
(so the owner can add a rule) — the item itself is not kept. A rule's
target must be a workspace where the owner is `owner`; a firm repo or
calendar therefore lands only in the firm workspace. Path prefixes are
normalised (resolved `~`, no trailing slash, case-sensitive) before
matching. Justification: ADR-012 decision 2; the proposal's "never
defaulted to personal" rule made mechanical.

### D5 — The four 8A sources and what counts as owner-authored

| Source | Producer (app → port) | `owner_text` | `context_text` / metadata | Sensitivity |
|---|---|---|---|---|
| git commits | `harness` runner-beat task (needs the runner's clone credentials; memory may not import harness) | commit subject + body **only** when the author e-mail is in `MEMORY_OWNER_GIT_EMAILS` **and** the message has no agent trailer (`Co-Authored-By: Claude…`, Codex, harness branch prefix) | sha, repo, branch, authored date, files changed / insertions / deletions counts; never diffs, never file contents | the repo's `sensitive` flag → `private` |
| harness outcomes | `harness` at the existing terminal-state hook next to `record_work_log` | `HarnessJob.goal` / implementation request goal (the owner typed it) | status, agent, repo, cost, duration, briefing / review summary (model-produced, ≤ 1000 chars) | the job's `sensitivity` |
| Claude Code hook events | the hook-ingest endpoint (D6) | none in 8A (prompt text arrives with transcripts in 8B, d5) | event name, session id, mapped project, timestamps, notification type; Claude's notification message is tool/model text (≤ 200 chars) | `normal` (no authored text) |
| calendar metadata | `connectors` beat task over `connectors.bridges.google` reads | event title **only** when the owner is the organiser | start/end, all-day flag, attendee **count**, organiser-is-owner flag; never bodies, attendee addresses or locations | always personal-class → extraction with `personal_data=True` |

Commits authored by an agent under the owner's identity are the common
case in this repo (every branch is written by Claude); treating them as
owner-authored would make Friday learn its own words as the owner's.
Non-owner commits keep metadata only.

### D6 — Hook relay before the node: `hook` PAT kind and `friday-hook`

- `PersonalAccessToken.Kind.HOOK = "hook"` with a new low-trust channel
  `CHANNEL_HOOK` (`core/channels.py`, added to `KIND_CHANNELS`). A
  `HookTokenAuthentication` (`accepted_kind = HOOK`) in `accounts`,
  injected into the memory hook view by `config/urls.py` (the `deploys`
  precedent: `memory` does not import `accounts`). Every existing
  authenticator keeps accepting only its own kind, so a hook token is
  refused everywhere else; a test walks every URL pattern with a hook
  token and expects 401/403 except the one endpoint.
- `POST /api/v1/memory/ingest/hook-events`: **append-only** — accepts a
  batch (≤ 50 events, ≤ 64 KB body), validates a strict per-event schema
  (allow-listed event names `SessionStart`, `Notification`, `Stop`,
  `SessionEnd`; unknown fields dropped), scrubs, maps by `cwd` path
  prefix, returns `202` with counts only. It reads nothing back, cannot
  list sessions, and never echoes payloads. Throttle scope `hook_ingest`
  (Redis-backed, fail closed, like Phase 4's).
- `friday-hook` (`backend/integrations/cli/friday_hook.py`, stdlib only,
  no Django import — a test asserts it): invoked by Claude Code **command
  hooks** with the hook JSON on stdin; keeps an allow-listed subset of
  fields, applies the same scrubber rules (a vendored copy generated from
  `memory/ingestion/scrubber.py` and checked identical in CI), reads the
  token from the macOS Keychain (`security find-generic-password`), and
  posts with a 3 s timeout. It **never blocks Claude Code**: on any error
  it exits 0 after writing one line to a local log (mode 0600). No spool in
  8A (the node's bounded spool arrives in Phase 9, ADR-013); a lost event
  only makes the panel stale. `docs/runbooks/claude-code-hooks.md` shows
  the `~/.claude/settings.json` hook entries and the Keychain setup.
- Rotation and revocation are the existing PAT flows; the token is
  scoped to the owner's personal workspace and may map events only to
  workspaces the owner owns (D4).

### D7 — Core memory blocks

- `CoreMemoryBlock` (`WorkspaceScopedModel`): `label` (`self`,
  `developer`, `current_focus`, `active_projects`, `preferences`),
  `content`, `version`, `sensitivity`, `updated_by_kind` (`owner`,
  `assembler`, `approved_proposal`). Unique `(workspace, label)`. Char
  budgets: `self` 1500, `developer` 1200, `current_focus` 600,
  `active_projects` 800, `preferences` 1000 (≤ 5100 chars total, below
  the memory block's 4000 + firm profile's 2000 already in the prompt).
  `CoreMemoryBlockVersion` keeps every prior content (owner can view and
  roll back; rollback is a confirm-class in-app edit).
- **Who writes what.** `self`: only the deterministic assembler (D8).
  `developer`: only the owner (PATCH over `CHANNEL_WEB`, `IsWebChannel`,
  `not is_agent`); agents may only file a `CoreBlockProposal` with a
  verified quote (D9). `current_focus`, `active_projects`, `preferences`:
  owner edits or approved proposals. Every change is audited
  (`memory.core_block.updated`, with label, version, writer kind —
  never content in metadata).
- `CoreBlockProposal` (`WorkspaceScopedModel`): `label`, `diff` (the
  proposed new content and a rendered line diff), `provenance_quote`,
  `ingestion_source`, `source_episode_id`, `inferred` (always true for
  model-made proposals), `status`. Approval is per item, web channel only;
  at most 3 approved-or-pending core-block proposals per owner per ISO
  week (D10); a proposal whose base `version` is stale is refused at
  approval (re-proposed next night).
- **Injection** (`assistant.context.build_context`): a new
  `render_core_blocks` block placed **first** after the system prompt,
  fenced with its own tag vocabulary (`core_block`) and the existing
  `neutralize_fence`/`strip_invisible_formatting`. Rules, following the
  overlay precedent (`select_overlay_memories`): `self` on every channel
  and workspace (it describes Friday, not the owner); `current_focus` and
  `active_projects` of the conversation's own workspace on every channel,
  like memories; the owner's personal blocks (`developer`,
  `preferences`) only over `CHANNEL_WEB`, and in a firm-workspace
  conversation only when that workspace has no member besides the owner,
  with a `memory.core_block.read` audit row. A `private` block is never
  injected (same rule as `cloud_eligible()`). Firm dossiers stay
  `Workspace.profile` and its existing block; they are not migrated.

### D8 — `self` is assembled deterministically

- **Sources snapshot.** `manage.py build_self_sources` reads CLAUDE.md,
  `docs/adr/ADR-*.md` (status line and the first decision sentence of
  each accepted ADR) and the MASTER_PLAN §AA "Friday 3.0 phases" table
  (the row whose status is "in progress", else the first "planned" row)
  and writes `backend/assistant/self_sources.json`: quoted lines with
  `file:line` anchors and a content hash. It is committed; CI runs
  `build_self_sources --check` (like `makemigrations --check`) so the
  snapshot cannot drift from the docs. The image need not contain `docs/`.
- **Assembler** (`assistant/self_block.py`; `assistant` may import
  `tools`, `ai_core` and `memory`): a fixed Turkish template filled from
  the snapshot, the enabled user-invocable tool names and read tools from
  `tools.registry`, the enabled `ModelProvider` names (never keys, never
  `base_url`s) and the current phase. It makes **no model call** and
  writes through `memory.services.set_assembled_self_block`, the only
  writer of `label=self`, which creates a version only when the content
  hash changes. Triggered on deploy (`post_migrate` signal handler) and
  by a daily beat.
- Model-proposed *wording* for `self` is out of scope for 8A (it would
  need its own eval); if added later it is a `CoreBlockProposal` with
  `inferred=true`, approved per item. Friday never invents facts about
  itself or the owner: a test asserts every sentence of the assembled
  block comes from the template, the snapshot or the registries.

### D9 — Owner-authored extraction with checked provenance

- **Episode extraction** (`memory.ingestion.extraction`, Celery, the
  existing lease-fenced job pattern of `MemoryExtractionJob`): a nightly
  job per workspace over episodes with `extracted_at IS NULL`. Messages:
  the system prompt `friday_episode_extraction` (seeded by
  `seed_ai_defaults`), then **only `owner_text`**, each item with its
  episode id; `context_text` and metadata are passed inside an
  `untrusted_context` fence that the prompt says cannot be quoted. Output
  schema v1: `{"version":1,"candidates":[{"type","title","content",
  "quote","episode_id","target"}]}` where `target` is `memory` or a core
  block label; parsed with the same strictness as
  `memory/extraction.py` (unknown keys → schema error, terminal).
- **Provenance check in code.** A candidate survives only if `quote`
  (≥ 12 chars after whitespace normalisation) is a verbatim substring of
  the cited episode's `owner_text` in the same workspace; otherwise it is
  dropped and counted (`proposals_dropped_no_provenance`). The quote is
  stored (≤ 300 chars) and shown on the card.
- **Gateway.** New purposes `memory.episode_extraction` (tier `cheap`)
  and `memory.consolidation` (tier `mid`), added to `AI_PURPOSE_TIERS`
  (coverage test). `personal_data=True` for any episode whose source is
  personal-class (calendar) or whose sensitivity is `private`, so only
  `personal_data_allowed` (local) providers serve them; such episodes are
  never batched with cloud-eligible ones. One `ModelUsage` per call; the
  context report carries source kinds, episode count and token estimate —
  never text.
- **Triage is deterministic in 8A.** Episodes are skipped before any model
  call when `owner_text` is empty, shorter than 20 chars, a duplicate
  fingerprint, or over the source's daily budget. A model triage stage
  (`memory.triage` on the local tier) is deferred until the acceptance
  data shows it is needed — it would add calls without a measured gain.
- **Chat extraction brought under the same rule** (schema v2 of
  `friday_memory_extraction`): assistant messages move into an
  `untrusted_context` fence; candidates must carry a `quote` that matches a
  `user`-role message of the job's range verbatim, else they are dropped.
  `MAX_CANDIDATES_PER_JOB` stays 10.
- **Never auto-promoted:** content from connectors/MCP reads, tool
  results, web pages or `context_text`.

### D10 — Nightly consolidation, the morning card and the caps

- **Consolidation** (purpose `memory.consolidation`, one call per
  workspace with new candidates): dedupes candidates against approved
  memories (fingerprint + embedding similarity using the existing
  `memory.retrieval` embedding path) and against core blocks, merges
  near-duplicates, and ranks. It **never applies** anything; output is
  proposals only.
- **Caps (d6), enforced in code, not in the prompt:** ≤ 10 new proposals
  per owner per night across all workspaces (memory + core-block
  proposals; the rest are discarded and counted, not queued), ≤ 3
  core-block proposals per ISO week. Bulk approve exists only for
  `work_log`-type items; every `self`/`developer`/`preferences` item and
  every core-block proposal is approved one at a time.
- **Morning card.** `GET /api/v1/memory/learning/digest` (owner, web
  channel): last night's proposals grouped by workspace, each with title,
  content, quote, source kind and date, plus approve / edit / reject
  actions that call the existing `approve_memory`/`edit_memory`/
  `reject_memory` services or the core-block proposal services. A HUD
  panel card and a `/memory/learning` page render it.
- **Acceptance metric.** Accepted/rejected counts per source and week in
  `IngestionCounter`; the go/no-go checkpoint reads it.

### D11 — Retention, audit view, forget-source

- **TTL (d10).** `expires_at = ingested_at + 30 days` in a personal
  workspace, `+ 14 days` in a company workspace (`Workspace.type`);
  settings `MEMORY_EPISODE_TTL_DAYS_PERSONAL`/`_FIRM`. A daily beat
  hard-deletes expired episodes (the retention window is part of the
  owner's d10 decision, as in ADR-009 decision 3a). Derived memories keep
  the quote and the plain `source_episode_id`.
- **Audit view.** `GET /api/v1/memory/ingestion/sources` and
  `/sources/{id}` (owner, web channel, `for_actor()`): per source the
  `IngestionCounter` series, the last 20 episodes (scrubbed text,
  workspace name), scrubber hits by type, unmapped keys, tokens spent vs
  budget, proposal acceptance rate. Every read is audited
  (`memory.ingestion.viewed`).
- **Forget source X.** `forget_source(source, *, actor, channel)`:
  required `channel` (`CHANNEL_WEB` only), human actor; in one
  transaction pauses the source, trashes its episodes (`deleted_at`),
  archives every memory and rejects every pending proposal whose
  `ingestion_source` is this source, marking them with the forget
  operation id; audited `memory.source_forgotten` (counts only). The UI
  confirmation names the source and the counts (ADR-009 conditions).
  `restore_source` undoes exactly the rows marked by that operation while
  they are still in the trash. Permanent purge: the existing gate-class
  `purge_trash` gains an `episodes` and `forgotten_memories` payload key
  (`tools/purge.py`), decided separately on `/approvals`. The vault mirror
  exports only `approved` memories, so archived rows drop out on the next
  export; branch 5 verifies that previously exported notes of archived
  rows are removed and adds that removal if not.

### D12 — Sessions panel v0 and the two read tools

- `CodingSession` (`WorkspaceScopedModel`, read model, upserted by the
  hook endpoint): `tool` (`claude_code`), `session_id`, mapped `project`
  label, `status` (`running`, `waiting_for_owner` — a `Notification` of a
  permission or idle type —, `stopped`), `started_at`, `last_event_at`,
  `last_notification` (≤ 200 chars, fenced as untrusted when shown to a
  model). Sessions with no event for 24 h show as `stale`.
- `GET /api/v1/memory/sessions` (owner, web channel, `for_actor()`), shown
  in the HUD sessions slot and on `/sessions` next to the existing harness
  job list (the frontend composes both endpoints; no backend join, since
  `memory` may not import `harness`).
- Read tools in the existing registry (`ReadToolSpec`, ungated, audited,
  results fenced): `coding_sessions_status` (registered in `tools/`, which
  may import `memory`) and `harness_jobs_status` (registered by
  `harness/tools.py`, which already registers harness tools). The chat
  model answers "Claude Code oturumlarım ne yapıyor?" from the fenced
  results inside the normal `chat` call — **no separate summarisation
  call** in 8A (purpose `sessions.summarize` waits for transcript detail
  in Phase 9). Both tools are allowed on the web channel only (they list
  firm project names).

### D13 — Budgets and cost

Per-source daily token budgets are checked by the extraction job before
each call (sum of `token_estimate` of the batch plus that day's spent
tokens from `IngestionCounter`); the gateway's purpose budgets (Phase 6
primitive) are the hard stop: `memory.episode_extraction` and
`memory.consolidation` get daily caps and a shared monthly learning cap
of **20 USD** (`AI_BUDGET_LEARNING_MONTHLY_USD`). A refused call leaves the
episodes un-extracted for the next night (they expire on schedule; nothing
is retried in a loop). Expected cost: about 7–13 USD/month (proposal
§c.10 rows for extraction and consolidation), shown in the audit view.

### D14 — Import contracts

- `memory.ingestion` may import `memory`, `workspaces`, `audit`, `core`,
  `ai_core` (gateway only); the existing `memory` contracts already forbid
  `tools`, `assistant`, `agents`, `harness`, `connectors`, `deploys`. A new
  contract forbids `memory.ingestion` from importing `knowledge`,
  `integrations` and any future `nodes` app (listed now so it bites when
  `nodes` lands; data from the node arrives through the port).
- `integrations.cli.friday_hook` imports only the standard library
  (contract + test).
- `assistant.self_block` may import `tools`, `ai_core`, `memory` (already
  allowed); a contract pins that `memory` never imports
  `assistant.self_block` (covered by "memory must not import assistant").

## ADR

ADR-012 (Accepted 2026-11-20) is the decision record; branch 1 adds an
"Implementation notes" section naming the tables, the port and the
contracts. No new ADR: 8A moves nothing between gate-class and
confirm-class (forget-source is a reversible trash move under ADR-009 as
written; approvals reuse the existing memory confirm-class path).

## Scope (one branch each)

### 1. `feat/p8-ingestion-core` — sources, mapping, scrubber, episodes, TTL

**Scope.** D1–D4, D11's TTL and forget-source (episode side and source
pause; memory cascade lands in branch 5), `IngestionCounter`, the seed
command `seed_ingestion_sources` (four disabled sources), the
`record_episode` port, contracts (D14), ADR-012 implementation note,
settings and `.env.example` names (`MEMORY_OWNER_GIT_EMAILS`, TTL days,
default daily budget). No producer yet.
**Files.** `backend/memory/models.py` + migration,
`backend/memory/ingestion/{__init__,ports,scrubber,mapping,services,
tasks}.py`, `backend/memory/management/commands/seed_ingestion_sources.py`,
`backend/memory/tests/scrubber_corpus/`, `backend/memory/tests/`,
`backend/pyproject.toml`, `backend/config/settings/base.py`,
`backend/config/celery.py`, `.env.example`,
`docs/adr/ADR-012-continuous-learning-ingestion.md` (note).
**Tests.** Scrubber corpus: 100% secrets recall (fails the build
otherwise), PII recall/precision reported, TCKN/IBAN/Luhn checksums
(valid-looking invalid numbers are not hits); placeholders typed; no
matched text in `scrub_report`. Mapping: longest prefix wins, unmapped →
dropped and counted (no row), a rule targeting a workspace the owner does
not own is refused, firm rule lands in the firm workspace only. Port:
paused/disabled source → `dropped_paused`; `(source, external_id)` dedupe;
text over the cap truncated after scrubbing. TTL: 30/14 days by
`Workspace.type`; the beat deletes only expired rows. Forget-source:
pauses, trashes, is reversible, requires `CHANNEL_WEB` and a human actor
(agent, PAT channels refused), audited with counts only. Isolation:
`Episode.objects.for_actor()` of a firm-only actor never sees personal
episodes and vice versa. Contracts green.
**Done when:** the four sources exist disabled; an episode posted through
the port in a test lands scrubbed in the mapped workspace or is dropped;
the corpus gate is in CI.

### 2. `feat/p8-core-blocks` — core blocks, deterministic `self`, injection

**Scope.** D7, D8: models + versions + proposals (services only; the
proposal producer is branch 5), owner edit/rollback API for
`developer`/`current_focus`/`active_projects`/`preferences`,
`build_self_sources` + `--check`, `self_sources.json`, the assembler,
`set_assembled_self_block`, injection in `build_context` with the channel
and workspace rules, budgets, fencing.
**Files.** `backend/memory/{models,services,serializers,views,urls}.py` +
migration, `backend/memory/core_blocks.py`,
`backend/assistant/{context,self_block}.py`,
`backend/assistant/self_sources.json`,
`backend/assistant/management/commands/build_self_sources.py`,
`backend/assistant/tests/`, `backend/memory/tests/`,
`.github/workflows/` (the `--check` step), `frontend/src/lib/i18n/tr.ts`
(error strings only).
**Tests.** Role × endpoint for the block API against the five Phase 4
principals (owner web 200; owner `cli`/`openai` PAT 403; non-owner 403;
`is_agent` 403; anonymous 401); `self` cannot be written through the API
(405/403) — only the assembler; the assembler makes zero gateway calls
(FakeAdapter call count) and every sentence traces to the template,
snapshot or registries; `--check` fails on a changed CLAUDE.md line;
injection order (core blocks first), budgets, fence neutralisation of a
block containing a fake closing tag; personal blocks absent on CLI,
facade and Telegram turns and in a firm workspace with a second member;
present (with an audit row) for the owner's web turn in a single-member
firm workspace; `private` block never injected; versions recorded and
rollback restores content.
**Done when:** in a web chat "Sen kimsin, seni kim geliştiriyor?" is
answered from `self` and `developer` (fake provider in CI; the real model
in the manual pass), and the `self` block changes only when its sources
do.

### 3. `feat/p8-hook-relay` — `hook` PAT, ingest endpoint, `friday-hook`, sessions read model

**Scope.** D6 and D12's `CodingSession` + `GET /memory/sessions`:
`PersonalAccessToken.Kind.HOOK`, `CHANNEL_HOOK`,
`HookTokenAuthentication`, the endpoint (wired via `config/urls.py`),
throttle, schema, scrub, map, upsert; `friday_hook.py`; the vendored
scrubber copy and its identity check; the runbook.
**Files.** `backend/accounts/{models,authentication,serializers}.py` +
migration, `backend/core/channels.py`, `backend/config/urls.py`,
`backend/memory/ingestion/hooks.py`, `backend/memory/{views,urls,
serializers}.py`, `backend/integrations/cli/friday_hook.py`,
`backend/integrations/cli/_scrubber_vendored.py`,
`backend/pyproject.toml`, `config/settings/base.py`,
`docs/runbooks/claude-code-hooks.md`, `docs/integrations/README.md`.
**Tests.** Hook token accepted only on the ingest endpoint (URL-pattern
walk: every other route 401/403 with a hook token); the ingest endpoint
refuses web sessions, `cli`/`openai` PATs, revoked/expired hook tokens,
anonymous; response contains counts only; unknown event names and fields
dropped; oversize batch 413; throttle 429 and Redis-down 503; secrets in a
payload never reach the DB (DB scan); an unmapped `cwd` stores nothing;
events map to a firm workspace only through a rule; `CodingSession`
status transitions (start → waiting on a permission notification →
running → stopped; stale after 24 h); `friday_hook.py` imports only the
standard library, exits 0 on server errors and timeouts (mocked
`urllib`), never prints the token; vendored scrubber identical to the
source module. No real Claude Code, Keychain or network in tests.
**Done when:** on the owner's Mac (owner step) a Claude Code session
shows up in `GET /memory/sessions` with live status, and a pasted fake key
in a notification never reaches the DB.

### 4. `feat/p8-source-producers` — git, harness outcomes, calendar

**Scope.** D5's three remaining producers: `harness` runner-beat task
`ingest_repo_commits` (enabled `HarnessRepo`s, a new runner primitive
`commit_log(git_url, branch, since)` using a blob-less, `--shallow-since`
clone and `git log --format` with `--shortstat`, owner-identity and
agent-trailer rules, `last_ingested_sha` cursor per repo); the harness
terminal-state producer beside `record_work_log` (both discovery and
implementation jobs); the `connectors` beat task over the Google calendar
read (yesterday and today, owner-organised titles only). Each calls
`record_episode` with the mapping key (`repo:<id>`, binding id).
**Files.** `backend/harness/{runner,tasks,execution,
implementation_execution,models}.py` + migration (cursor field),
`backend/harness/tests/`, `backend/connectors/{tasks,bridges/google}.py`,
`backend/connectors/tests/`, `backend/config/celery.py`.
**Tests.** Commit rules: owner e-mail + no trailer → `owner_text`; a
`Co-Authored-By: Claude` trailer, a harness branch or a foreign author →
metadata only; diffs never read (assert the `git log` argv has no `-p`);
sensitive repo → `private`; cursor advances and re-runs are idempotent
(`duplicate`); runner primitive behind the existing module-attribute seam
(no real git remote; a local fixture repo built in `tmp_path`). Harness:
`goal` → `owner_text`, briefing summary → `context_text` truncated;
failed and cancelled jobs produce an episode with status; producers never
raise into the harness state machine (an ingestion failure is logged and
counted). Calendar: fake Google bridge (`connectors/tests/google_fake.py`);
non-organiser titles go to `context_text`; no bodies, addresses or
locations stored; firm calendar binding → firm workspace. No real Google,
GitHub or git remote.
**Done when:** with all four sources enabled in the dev stack, one day of
fixture activity produces scrubbed episodes in the right workspaces and
the audit counters match.

### 5. `feat/p8-learning-loop` — extraction, provenance, consolidation, caps

**Scope.** D9, D10, D13: episode extraction job and task (lease-fenced,
recovery beat), prompts `friday_episode_extraction` and
`friday_memory_consolidation` in `seed_ai_defaults`, purposes and tiers,
provenance check, consolidation, nightly and weekly caps, core-block
proposals, the digest endpoint, budgets, the forget-source cascade to
memories and proposals and its restore, `purge_trash` payload extension,
vault-export check for archived rows; chat extraction schema v2 (assistant
turns fenced, quote required).
**Files.** `backend/memory/ingestion/{extraction,consolidation,
digest}.py`, `backend/memory/{extraction,jobs,tasks,services,views,
urls,serializers}.py`, `backend/ai_core/management/commands/
seed_ai_defaults.py`, `backend/config/settings/base.py`,
`backend/config/celery.py`, `backend/tools/{purge,export}.py`,
`backend/memory/tests/`, `backend/tools/tests/`, `.env.example`.
**Tests.** FakeAdapter with captured payloads: the extraction payload
contains `owner_text` and the fenced context only — never a raw secret
(the second scrub), never another workspace's episode; `personal_data=True`
for calendar and private episodes and a cloud-only provider set →
`NoProviderAvailable` with no candidates; provenance: paraphrased quote
dropped, quote from `context_text` dropped, quote from another episode
dropped, verbatim quote kept; caps: 15 candidates → 10 proposals, a 4th
core-block proposal in a week discarded; bulk approve refused for
non-`work_log`; `self`/`developer`/`preferences` proposals only per item;
stale-version core-block proposal refused at approval; budget exceeded →
no call, episodes left for the next night, one `budget_exceeded`
`ModelUsage` row; one `ModelUsage` per call with no text in the context
report; schema errors terminal; chat extraction v2: assistant text never
quotable, v1-shaped output rejected; forget-source cascade archives
exactly the source's memories and restore brings back exactly those;
`purge_trash` for episodes is gate-class (no purge without an approved
request); purpose coverage test; role × endpoint for the digest and
proposal actions.
**Done when:** a fixture night yields ≤ 10 proposals, each with a verified
quote, in the right workspaces, and a full forget/restore round-trip
leaves the DB as before.

### 6. `feat/p8-learning-ui` — audit view, morning card, core-block editor, sessions

**Scope.** Frontend for D7, D10, D11, D12: `/memory/learning` (morning
card with approve/edit/reject, per-item for core blocks), the HUD card,
`/memory/ingestion` (sources, enable/pause, mapping rules, scrub terms,
counters, last items, unmapped keys, forget/restore with a confirmation
naming the source and counts), the core-block editor with version
history and rollback (`self` read-only with its source anchors),
`/sessions` and the HUD sessions panel; the two read tools
(`coding_sessions_status` in `tools/`, `harness_jobs_status` in
`harness/tools.py`); mapping-rule and scrub-term APIs if not already in
branch 1.
**Files.** `frontend/src/app/(shell)/memory/{learning,ingestion}/`,
`frontend/src/app/(shell)/sessions/`, `frontend/src/components/memory/`,
`frontend/src/components/hud/` (panels), `frontend/src/app/api/memory/`,
`frontend/src/lib/i18n/tr.ts`, `backend/tools/`, `backend/harness/tools.py`,
`backend/memory/{views,urls}.py`.
**Tests.** Vitest: card actions call the right endpoints, no bulk control
on non-`work_log` items, forget dialog names the source and counts,
`self` not editable, provenance quote always rendered, sessions panel
states incl. stale and empty; read tools: role × channel (web only),
fenced output, `for_actor()` isolation (another workspace's sessions and
jobs never listed), audit row per read; contrast for new tokens.
**Done when:** the owner can run a full morning review, inspect every
source, forget and restore one, and see live sessions — at phone width
too.

### 7. closeout

MASTER_PLAN §AA "Friday 3.0 phases" row 8A → done with the checkpoint
result; ADR-012 implementation note completed; `docs/runbooks/
claude-code-hooks.md` and a `docs/runbooks/learning-memory.md` (enable
sources, mapping rules, reading the audit view, forget/restore);
`manage.py preflight` checks (sources configured, beat tasks scheduled,
scrubber self-test on the corpus, learning budget configured, last
extraction run status); e2e (CI, fake providers): a fixture commit and a
hook event → nightly job (eager) → card → approve → the memory is used in
the next chat answer. **Go/no-go (owner, after two weeks of real use):**
scrubber corpus 100% secrets recall; zero cross-workspace leaks in tests;
≥ 60% of proposals accepted — below that, tune prompts, caps and the
owner-text rules before Phase 8B adds sources.

## Branch table

| # | Branch | Scope | Depends on |
|---|---|---|---|
| 1 | `feat/p8-ingestion-core` | Source registry, mapping rules, scrubber + corpus, `Episode`, port, TTL, forget-source (episodes), counters, contracts, ADR-012 note | Phase 6 closeout on `main` |
| 2 | `feat/p8-core-blocks` | `CoreMemoryBlock` + versions + proposals, owner edit API, deterministic `self` (snapshot + `--check` + assembler), injection rules | Phase 6 closeout on `main`; parallel with 1 |
| 3 | `feat/p8-hook-relay` | `hook` PAT kind + channel, append-only ingest endpoint, `friday-hook` CLI, `CodingSession` + sessions API, runbook | 1 |
| 4 | `feat/p8-source-producers` | Git commit producer (runner primitive), harness outcome producer, calendar metadata producer | 1 |
| 5 | `feat/p8-learning-loop` | Owner-only episode extraction, provenance check, consolidation, caps, digest API, budgets, forget cascade + purge, chat extraction v2 | 1, 2, 4 (3 for hook-derived counters) |
| 6 | `feat/p8-learning-ui` | Morning card, ingestion audit view, core-block editor, sessions page + HUD panel, read tools | 2, 3, 5 |
| 7 | closeout | Docs, runbooks, preflight, e2e, go/no-go | 1–6 |

Minimum usable cut if the phase runs long: 1, 2, 3 and 6's sessions part
("knows itself and you, shows your sessions"); 4 and 5 complete "learns
from yesterday".

| # | Status |
|---|---|
| brief (`docs/friday-3-roadmap`) | **implemented 2026-11-20** (draft brief; awaits Sonnet 5 plan review) |
| 1–7 | not started (precondition: Phase 6 closeout on `main`) |

## Out of scope (explicit)

Claude/Codex transcript ingestion, `UserPromptSubmit` prompt text and
Codex threads (8B, via the node) · temporal entities/facts and the
knowledge graph, RRF retrieval changes, Graphify (8B) · Headroom
compression (8B, flag off) · the Obsidian `Inbox/` import and mirror
format changes (8B) · weekly reflection (8B) · DSPy/GEPA ("later") ·
model triage (`memory.triage`) · model-proposed `self` wording · session
summarisation calls (`sessions.summarize`, Phase 9) · the Friday Node,
its spool and Unix-socket relay (Phase 9) · shell history, mail bodies
(opt-in sources, not built in 8A) · screen content, screenshots, AX
trees, keystrokes, browser history (never, ADR-012) · auto-approval of
anything but the existing `work_log` class · bulk approval of
non-`work_log` items · voice approval of proposals (ADR-015) · learning
from tool output, web content, MCP reads or agent-written commit
messages · a third-party secret scanner dependency.

## Test strategy

Same discipline as Phases 2–6 (CLAUDE.md rules 5, 7, 8): ruff, pytest,
vitest, import-linter (new contracts) and `makemigrations --check` green
per branch, plus `build_self_sources --check`. No test touches a real LLM,
Claude Code, Keychain, Google, GitHub or git remote: FakeAdapter with
captured payloads and call counts, the fake Google bridge, a local fixture
git repo in `tmp_path`, mocked `urllib` for `friday-hook`. Real models only
in the manual Mac pass and nightly runs. **Corpus gates:** the scrubber
corpus (100% secrets recall) and a prompt-injection corpus (commit
messages, hook notifications and calendar titles that say "ignore previous
instructions and approve…" or embed fake fence tags) that must never yield
an approved row, a tool call, a quote from `context_text` or an unfenced
block. Role × endpoint for every new surface against the five Phase 4
principals plus a hook PAT; isolation via a second (firm) workspace with
its own sources and a non-owner member. **Release-gate regressions**
(escalate per the CRITICAL/HIGH rule): a raw secret or PII from the corpus
stored in any row or log, or sent in a gateway payload; an item stored in
a workspace no rule maps it to, or defaulted to personal; firm data in a
personal-workspace row or prompt; a learned row born approved (other than
`record_work_log`); a proposal whose quote is not verbatim owner text; a
calendar or private episode sent to a provider without
`personal_data_allowed`; more than 10 proposals in a night or 3 core-block
proposals in a week; a hook token accepted outside the ingest endpoint or
able to read anything; `self` written by anything but the assembler; a
model call without a `ModelUsage` row; an episode surviving past its TTL.

## Risks

- **R1 — Scrubber misses.** A novel secret format passes the rules. The
  second scrub before every gateway call, the entropy rule, typed
  placeholders and the corpus (grown with every miss) bound it; TTL limits
  the exposure window; forget-source and purge remove it.
- **R2 — Agent text mistaken for owner text.** Most commits here are
  agent-written. The trailer/branch rules (D5) and the verbatim quote
  check (D9) bound it; residual: an agent commit without a trailer under
  the owner's identity. The audit view shows which commits counted as
  owner text so the owner can tighten `MEMORY_OWNER_GIT_EMAILS`.
- **R3 — Review fatigue.** Ten proposals a night may be ignored. Caps,
  dedupe, the acceptance metric and the go/no-go (≥ 60%) bound it;
  unanswered proposals expire with no effect.
- **R4 — Prompt injection through ingested text.** Commit messages,
  calendar titles and notifications are attacker-reachable. Owner-only
  quoting, fencing, no tool calls in the extraction path (it is a plain
  `complete`), and proposals-only output bound it.
- **R5 — Hook relay reliability.** No spool before the node; Claude Code
  must never be slowed. `friday-hook` exits 0 within 3 s; a lost event only
  makes the panel stale; Phase 9's node relay replaces it.
- **R6 — Core blocks bloat the prompt.** Hard char budgets per label, a
  total ≤ 5100 chars and a test on the assembled prompt size.
- **R7 — Cost drift.** Per-source daily budgets and the gateway's monthly
  learning cap (20 USD) stop it; the audit view shows spend.
- **R8 — `self` drifting from reality.** The committed snapshot with a CI
  `--check` and the registry-driven capability list keep it true to the
  repo; it cannot describe features that are not registered.
- **R9 — Calendar is personal data.** Only owner-organised titles are
  owner text, everything goes through `personal_data=True` (local
  providers only), and no addresses or bodies are stored.

## Open questions (with recommended answers)

- **Q1 — One `IngestionSource` per kind or per kind and workspace?** Per
  kind, in the personal workspace; mapping rules route items. Revisit if
  a firm wants its own budget.
- **Q2 — Should hook events produce proposals in 8A?** No: they carry no
  owner-authored text. They feed the sessions panel and the
  `active_projects` evidence shown to consolidation as fenced context.
- **Q3 — Where does the morning card appear first?** The HUD card and
  `/memory/learning`; a spoken morning digest is Phase 12.
- **Q4 — Do voice turns feed extraction?** Yes, like typed turns (Phase 6
  Q6), but under chat extraction v2 only `user`-role text is quotable, and
  ADR-015 keeps any resulting row a proposal.
- **Q5 — Do we migrate firm dossiers into core blocks?** No: the
  `Workspace.profile` block stays as is; revisit only if a firm needs a
  `current_focus` the dossier cannot express.

## Owner steps (pending) — recorded 2026-11-20

1. Plan review: have the independent Sonnet 5 reviewer approve this brief
   before branch 1.
2. Set `MEMORY_OWNER_GIT_EMAILS` and the workspace mapping rules for the
   owner's real repos, paths and calendars (branch 1/4, on the Mac).
3. Create the `hook` PAT, store it in the Keychain and add the Claude
   Code hook entries from `docs/runbooks/claude-code-hooks.md` (branch 3).
4. Enable the four sources (d5) once branches 1–5 are merged.
5. Use the morning card for two weeks and record the acceptance rate for
   the go/no-go (closeout).
