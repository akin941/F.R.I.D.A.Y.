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
  `active_projects` 800, `preferences` 1000 (≤ 5100 chars total, on top of
  the memory block's 4000 and the firm profile's 2000 chars).
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
  block label other than `self` (a candidate targeting `self` is dropped
  and counted; a `developer` target becomes a `CoreBlockProposal`, never a
  direct write); parsed with the same strictness as
  `memory/extraction.py` (unknown keys → schema error, terminal).
- **Provenance check in code.** A candidate survives only if `quote`
  (≥ 12 chars after whitespace normalisation) is a verbatim substring of
  the cited episode's `owner_text` in the same workspace; otherwise it is
  dropped and counted (`proposals_dropped_no_provenance`). The quote is
  stored (≤ 300 chars) and shown on the card.
- **Gateway.** New purposes `memory.episode_extraction` (tier `cheap`)
  and `memory.consolidation` (tier `mid`), added to `AI_PURPOSE_TIERS`
  (coverage test); the proposal's `memory.extract`/`memory.consolidate`
  are renamed to match the existing `memory.extraction` style. `personal_data=True` for any episode whose source is
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
`record_episode` port, the owner APIs (sources enable/pause, mapping
rules, scrub terms, forget/restore, the D11 audit-view reads; web channel,
owner only, `for_actor()`), contracts (D14), ADR-012 implementation note,
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
episodes and vice versa. Owner APIs (sources enable/pause, mapping rules,
scrub terms, forget/restore, the D11 audit-view reads): role × endpoint
against the five Phase 4 principals (owner web session 200; owner
`cli`/`openai` PAT 401 — the views are default-authentication only, as in the
connectors-bindings matrix, so a PAT is never authenticated there; non-owner
403; `is_agent` 403; anonymous 401) and
another workspace's sources, rules and episodes → 404. Contracts green.
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
principals (owner web 200; owner `cli`/`openai` PAT 401 — default-
authentication-only views, as in branch 1; non-owner 403; `is_agent` 403;
anonymous 401); `self` cannot be written through the API
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
events map to a firm workspace only through a rule; role × endpoint for
`GET /memory/sessions` (five Phase 4 principals, a hook token refused,
another workspace's sessions never listed); `CodingSession`
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
`harness/tools.py`). The backend APIs it calls exist from branches 1, 2,
3 and 5.
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
| 1 `feat/p8-ingestion-core` | **implemented 2026-11-20** (sources, mapping, scrubber + corpus gate, episodes, port, TTL beat, forget/restore (episodes), counters, owner API, contract, ADR-012 note; awaits Sonnet 5 review). Merged after branch 2: its migration is `0007_ingestion_core` |
| 2 `feat/p8-core-blocks` | **implemented 2026-11-20** — see "Branch 2 implementation notes" below; awaits independent Sonnet 5 review |
| 3 `feat/p8-hook-relay` | **implemented 2026-11-20** — see "Branch 3 implementation notes" below; awaits independent Sonnet 5 review |
| 4 `feat/p8-source-producers` | **implemented 2026-11-20** — git commit producer (runner `commit_log` primitive, cursor, authorship rules), harness outcome producer (discovery + implementation, every terminal path), calendar metadata producer; see "Branch 4 implementation notes" below; awaits independent Sonnet 5 review. Migration `harness/0013_repo_commit_cursor` |
| 5 `feat/p8-learning-loop` | **implemented 2026-11-20** — owner-text episode extraction (lease-fenced jobs, triage, second scrub, provenance check), consolidation (dedupe, merge/rank, nightly cap 10, weekly core-block cap 3), digest + per-item actions + work_log-only bulk approve, learning budgets, forget cascade + exact restore, `purge_trash` episode/forgotten-memory keys, vault-mirror check, chat extraction schema v2; see "Branch 5 implementation notes" below; awaits independent Sonnet 5 review. Migration `memory/0009_learning_loop` |
| 6 `feat/p8-learning-ui` | **implemented 2026-11-20** — morning card (`/memory/learning`, per-item approve/edit/reject, work_log-only bulk), ingestion audit view (`/memory/ingestion`: sources, enable/pause/budget, counters, last items, scrubber hits, unmapped keys, mapping rules, scrub terms, forget/restore naming source and counts), core-block editor (`/memory/core-blocks`, version history + rollback, `self` read-only with its source anchors), `/sessions` + HUD sessions panel and morning-card summary, read tools `coding_sessions_status` / `harness_jobs_status`; see "Branch 6 implementation notes" below; awaits independent Sonnet 5 review. No migration |
| 7 | not started |

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

## Branch 2 implementation notes (`feat/p8-core-blocks`, 2026-11-20)

- **Models** (`memory/models.py`, migration `0006_core_memory_blocks`):
  `CoreMemoryBlock` (unique `(workspace, label)`, `content_hash`,
  `version`, `updated_by_kind`), `CoreMemoryBlockVersion` (every version,
  current included), `CoreBlockProposal` (`proposed_content`, rendered
  `diff`, `base_version`, `provenance_quote`, plain `source_episode_id`,
  `inferred`, `status`). The proposal's `ingestion_source` FK is **not**
  added here: `IngestionSource` comes from branch 1 (parallel); branch 5
  adds the FK when it lands the producer. If branch 1 also ships a
  `memory` migration numbered `0006`, the merge renumbers one of them.
- **Labels per workspace.** All five labels live in a personal workspace;
  a company workspace holds only `current_focus` / `active_projects`
  (`developer` / `preferences` are the owner's personal blocks). `self`
  lives in the installation owner's personal workspace.
- **Services** (`memory/services.py`): `set_assembled_self_block` (only
  writer of `self`, versions on hash change), `update_core_block`,
  `rollback_core_block` (rollback = a new version with the old content),
  `create_core_block_proposal` (weekly cap of 3 approved-or-pending per
  owner per ISO week, `self` never a target, quote required),
  `approve_core_block_proposal` (stale `base_version` refused, proposal
  left pending), `reject_core_block_proposal`. Every owner write requires
  the installation owner, a human, `CHANNEL_WEB` and workspace ownership.
  New error codes `memory.core_block.{permission_denied,invalid,
  stale_version,weekly_cap}` with Turkish strings in `tr.ts`.
- **API** (owner, web session, `for_actor()`; not on the PAT allowlist, so
  `cli`/`openai` PATs get **401**, like every web-only surface):
  `GET /api/v1/memory/core-blocks?workspace=`,
  `GET|PATCH /api/v1/memory/core-blocks/<label>?workspace=`,
  `GET …/<label>/versions`, `POST …/<label>/rollback`. `self` is listed
  read-only (`editable: false`); PATCH/rollback of `self` → 403, PUT → 405.
  Reads are audited (`memory.core_block.viewed`, labels only).
- **Snapshot** (`assistant/self_sources.py`, `manage.py build_self_sources
  [--check]`, committed `assistant/self_sources.json`, CI step in
  `.github/workflows/ci.yml`): identity sentence + rule headlines from
  CLAUDE.md, title/status/first decision sentence of each accepted ADR,
  the MASTER_PLAN phases row ("in progress", else first "planned"). Line
  anchors are part of the snapshot, so a doc edit that shifts a quoted
  line also requires re-running `build_self_sources` (CI says so).
- **Assembler** (`assistant/self_block.py`): fixed Turkish `TEMPLATES`,
  filled from the snapshot, `tools.registry.list_tool_names` /
  `list_read_tool_names` (new helper) and active `ModelProvider` names;
  lines added in priority order and skipped when over the 1500-char
  budget. No model call (test spies on every gateway method). Runs on
  `post_migrate` (errors logged, never fail a deploy) and daily
  (`assistant.tasks.assemble_self_block_task`, compose beat).
- **Injection** (`memory.core_blocks.select_core_blocks` +
  `assistant.context.render_core_blocks`): first after the system prompt,
  `<core_blocks>`/`<core_block label=…>` fence with its own vocabulary;
  rules exactly as D7; `memory.core_block.read` audit row (labels,
  versions, target workspace) whenever the owner's personal blocks are
  injected; context report carries labels, versions and char count only.
- **Follow-up "D7 `self` block on low-trust channels" — resolved:** the
  `self` block's tool / read-tool / provider lines are injected only on
  `web` and `telegram` turns; `cli`/`openai` facade turns get `self`
  without them (test asserts no tool or provider name on those turns).

### Owner steps (pending) — branch 2, recorded 2026-11-20

1. After deploy, run `manage.py migrate` (the `post_migrate` handler
   assembles `self`) and check the block in the core-block API.
2. Write the `developer` (and optionally `preferences`, `current_focus`,
   `active_projects`) blocks from the web UI/API (the editor UI is
   branch 6; until then `PATCH /api/v1/memory/core-blocks/<label>`).
3. Manual pass with a real model: ask "Sen kimsin, seni kim
   geliştiriyor?" in a web chat and confirm the answer comes from `self`
   and `developer` (CI covers this with the fake provider only).

### Owner steps (pending) — `feat/p8-ingestion-core`, recorded 2026-11-20

1. On the deployed stack run `manage.py migrate` and `manage.py
   seed_ingestion_sources` (creates the four sources, disabled).
2. Set `MEMORY_OWNER_GIT_EMAILS` (and, if wanted, the TTL / daily budget
   overrides) in the server env — names are in `.env.example`.
3. Add the real mapping rules (repo ids, `~/…` or absolute path prefixes,
   calendar bindings) and any scrub terms (customer names, code names) from
   the web, then enable sources once their producers (branches 3–4) land.
4. Independent Sonnet 5 review of this branch before merge.

Implementation notes for later branches (branch 1, 2026-11-20): the owner
APIs are default-authentication only, so an owner `cli`/`openai` PAT gets
**401** (not 403) — the established connectors-bindings matrix; branch 3's
hook token must likewise be refused there. Domain errors reuse the
`memory.permission_denied` / `memory.invalid_transition` codes (no new
frontend strings). `~` in path rules is a literal home marker (not resolved
server-side); producers must send `path:~/…` or the absolute form matching
the rule.

Review round 1 fixes (branch 1, 2026-11-20): the scrubber caps raw input at
64 KB per string *before* any rule runs (cut at the last whitespace so a
straddling token is dropped whole), the `assignment` rule is anchored at a
key boundary with a bounded key prefix/suffix, the e-mail, URL-scheme and
PEM rules are bounded, and placeholder spans are computed once per pass — a
100 KB hostile run (`A`×N, hex, e-mails, `password`×N, PEM headers) now
scrubs in well under a second, with a time-budget test. Quoted secret values
are consumed up to their closing quote (`"password": "hunter two three"` is
fully replaced; corpus rows added). `path:~/../x` (a key climbing above `~`)
is now an invalid key, hence unmapped, instead of normalising to `~/x`. The
PAT wording in this section and branch 2's matrix now says 401.

## Known follow-ups (from review, unresolved at merge)

- **LOW — D11 reference.** D11 cites "ADR-009 decision 3a" for the TTL
  hard-delete. ADR-009 has no "3a"; the clause is decision 3(a), the 30-day
  automatic trash purge. The analogy is loose: that clause purges rows already
  trashed by a confirmed owner delete, while episode TTL hard-deletes untrashed
  rows on a system schedule (ADR-012 decision 4 and owner decision d10 do
  justify it). Fix: change the reference to "ADR-009 decision 3(a)" and add that
  TTL expiry is a retention rule the owner accepted in d10, not an owner delete.
- **LOW — D7 `self` block on low-trust channels.** D7 injects the `self` block
  on every channel, including the cli and openai facade channels. The block
  lists enabled tool names and provider names, exposing capability and provider
  inventory to those channels (mild: both tokens belong to the owner, but not
  stated as deliberate). Fix: either restrict `self` to CHANNEL_WEB and Telegram
  turns, or add one sentence accepting the exposure; add a test that the facade
  turn's `self` block contains no tool or provider names beyond what the facade
  already exposes.
- **LOW — D6 hook PAT workspace vs D4 mapping.** D6 says the hook PAT is scoped
  to the owner's personal workspace, while existing PATs carry a bound workspace
  id (`bound_workspace_id` in `accounts/authentication.py`) and D4 rules let hook
  events map to firm workspaces. The brief does not say which rule decides.
  Fix: state that the PAT's bound workspace only authenticates the principal and
  D4 mapping alone decides the target workspace; add a test that a hook token
  bound to personal lands a mapped firm `cwd` only in the firm workspace, and
  only when the owner owns that workspace.

### Branch 2 (`feat/p8-core-blocks`) — known follow-ups (from review, unresolved at merge)

- **LOW — stale Tests line (PAT status code).** The brief's Tests list says an
  owner cli/openai PAT gets 403 on the core-block routes. The implementation
  and tests return 401, because the routes are not on the PAT allowlist. The
  deviation is recorded in the branch 2 implementation notes, and 401 matches
  other web-only surfaces. Fix: amend the spec line to say 401 (PAT refused by
  authentication), or add the routes to the PAT-refusal 403 path. No code
  change needed.
- **LOW — render budget applied before sanitisation**
  (`backend/memory/core_blocks.py`). `_bounded()` truncates content before
  `render_core_blocks` runs `neutralize_fence` and the invisible-character
  strip. Neutralisation can lengthen a block slightly, so the 5100-char total
  is not strictly guaranteed on the rendered text. Raw content is already
  capped by the service budgets, so the practical risk is negligible. Fix:
  optionally apply the budget after sanitisation in `render_core_blocks`.
- **LOW — personal-block injection predicate** (`backend/memory/core_blocks.py`).
  `_personal_blocks_allowed` checks `workspace.owner_id == actor.id` but not
  `actor.is_owner`. Only the installation owner can write personal blocks, so
  nothing leaks today. Fix: add an `is_owner` check to the injection predicate
  as defence in depth.

## Branch 3 implementation notes (`feat/p8-hook-relay`, 2026-11-20)

- **Token and channel.** `PersonalAccessToken.Kind.HOOK` (migration
  `accounts/0007_pat_kind_hook`), `core.channels.CHANNEL_HOOK`,
  `KIND_CHANNELS[HOOK]`, `accounts.authentication.HookTokenAuthentication`
  (`accepted_kind = HOOK`, surface `hook`). Minting a `hook` token requires
  the installation owner (human) and their **personal** workspace; anything
  else is a 400 (`code: invalid`). The frontend token form offers the kind
  (`PatKind` + Turkish label/hint). `create_dev_pat --kind hook` works for dev.
- **Endpoint.** `POST /api/v1/memory/ingest/hook-events`
  (`memory.ingestion.views.HookEventIngestView`), routed by
  `memory.urls.build_hook_urlpatterns` from `config/urls.py` with
  `HookTokenAuthentication` as its **only** authenticator (`memory` does not
  import `accounts`); `IsHookChannel` permission on top. Body
  `{"events": [...]}`: > 50 events or > 64 KB → 413; not an `events` list →
  400; throttle scope `hook_ingest` (`HOOK_INGEST_RATE`, default 240/min,
  fail closed 503). Answers `202` with counts only (`received`, `stored`,
  `duplicate`, `dropped_unmapped`, `dropped_paused`, `ignored` — event name
  outside the allow list, `invalid` — schema failure). No audit row per
  batch (high volume); the `IngestionCounter` series is the audit trail.
- **Schema** (`memory/ingestion/hooks.py`): kept fields `hook_event_name`,
  `session_id` (`[A-Za-z0-9_-]{1,128}`), `cwd` (absolute or `~/`, normalised
  by the D4 helper), optional `occurred_at` (aware ISO; future → clamped to
  now; older than 24 h → invalid), `event_id` (`[A-Za-z0-9_-]{8,64}`, the
  dedupe key with the session id), `notification_type`/`source`/`reason`
  (short lowercase tokens, per event) and `message` (Notification only).
  Everything else is dropped. Each event goes through `record_episode`
  (kind `hook.<event>`, mapping key `path:<cwd>`, `owner_text` empty, message
  → `context_text` cut to 200 chars after scrubbing, metadata = event,
  session id, project label = last `cwd` component, type/source/reason).
- **D6 follow-up (LOW, "hook PAT workspace vs D4 mapping") — resolved:** the
  token's bound workspace only authenticates the principal and names the
  owner's `claude_code_hooks` source (`for_actor()`); D4 mapping alone decides
  the target workspace, and only workspaces the owner owns. Tested: a token
  bound to personal lands a mapped firm `cwd` only in the firm workspace; a
  disabled firm rule or lost firm ownership → dropped, never personal.
- **`CodingSession`** (migration `memory/0008_coding_sessions`): unique
  `(workspace, tool, session_id)` — a session that `cd`s into another
  workspace's tree gets one row per workspace. Upserted only for a
  **stored** episode, from the episode's scrubbed fields (a session id the
  scrubber would touch is replaced by a digest). Transitions: `SessionStart`
  → `running`; `Notification` of type `permission_prompt`/`idle_prompt`/
  `elicitation_dialog` (or untyped) → `waiting_for_owner`, other types keep
  the status; `Stop` → `running`; `SessionEnd` → `stopped`; an older event
  never overwrites newer state. `stale` is computed at read time
  (`MEMORY_CODING_SESSION_STALE_HOURS`, default 24). Rows of a forgotten
  source are hidden from the API until restored; the existing TTL beat also
  deletes sessions idle past their workspace's episode TTL.
- **Sessions API.** `GET /api/v1/memory/sessions[?workspace=&limit=]`
  (owner, web session, `for_actor()`, default-authentication only, so
  `cli`/`openai`/`hook` PATs get 401). Each row: workspace id + name, tool,
  session id, project, effective and stored status, timestamps, last event,
  last notification (scrubbed; branch 6's read tool must fence it as
  untrusted), event count. Not audited per read (HUD polling).
- **`friday-hook`** (`integrations/cli/friday_hook.py`): stdlib only (AST
  test + an isolated `python -I` run that asserts Django is never imported +
  an import-linter contract), Keychain service `friday-hook`
  (`FRIDAY_HOOK_TOKEN` fallback without a Keychain), base URL from
  `FRIDAY_HOOK_URL` or `~/.config/friday/hook.toml`, ~3 s deadline, always
  exit 0, nothing on stdout/stderr, one log line (0600, error class/status
  only) on failure. Vendored scrubber `integrations/cli/_scrubber_vendored.py`
  = fixed header + the source module verbatim, regenerated by
  `python -m integrations.cli.vendor_scrubber` (`--check` mode too); a test
  fails when it drifts.
- **URL walk.** `config/tests/test_hook_token_surfaces.py` calls every routed
  DRF view (GET/POST/PATCH/DELETE) with a valid hook token: 401/403 on every
  authenticating route, `last_used_at` untouched; only the ingest view uses
  the hook authenticator. The channel matrix now reads injected
  `authentication_classes` (`view_initkwargs`) too.

### Owner steps (pending) — `feat/p8-hook-relay`, recorded 2026-11-20

1. Deploy and `manage.py migrate` (accounts `0007`, memory `0008`).
2. Enable the `claude_code_hooks` source and add path-prefix rules for the
   real code trees (personal and each firm) — runbook §1.
3. Mint the `hook` token from the web UI, store it in the macOS Keychain
   (service `friday-hook`), install `friday_hook.py` + `_scrubber_vendored.py`
   and the four hook entries in `~/.claude/settings.json` —
   `docs/runbooks/claude-code-hooks.md` §2–5.
4. Manual pass on the Mac (cannot run in CI: real Claude Code, Keychain,
   network): a session shows in `GET /memory/sessions` with live status
   through start → permission prompt → stop → end, and a pasted fake key in
   a notification never reaches the DB (runbook §6).
5. Independent Sonnet 5 review of this branch before merge.

### Branch 3 (`feat/p8-hook-relay`) — known follow-ups (from review, unresolved at merge)

- **LOW — body-size check buffers chunked bodies**
  (`backend/memory/ingestion/views.py`). When Content-Length is absent or the
  body is chunked, `HookEventIngestView` calls `len(request.body)`. That
  buffers up to Django's `DATA_UPLOAD_MAX_MEMORY_SIZE` before the 64 KB check
  runs, so an oversized chunked body gets the framework's 400 instead of a
  413. The impact is small, because the caller is already authenticated and
  throttled. Fix (optional): read at most `MAX_BODY_BYTES + 1` from the
  request stream before parsing.
- **LOW — forget hides sessions but keeps notification text**
  (`backend/memory/ingestion/hooks.py`). Forgetting a source only hides its
  `CodingSession` rows, because the sessions API filters on `forgotten_at`.
  The scrubbed `last_notification` text stays in the table until restore, the
  TTL purge or deletion of the source. This matches the reversible-trash model
  and the docstring, but it should be checked against the D10/D11 forget
  semantics. Fix (optional): blank `last_notification` on forget and rebuild
  it on restore, or record this behaviour in the runbook.
- **LOW — throttle keyed per user, not per token**
  (`backend/memory/ingestion/hooks.py`). The throttle scope uses DRF's
  `ScopedRateThrottle` ident, which is per user, where the brief says per
  token. With a single owner the effect is the same. Fix: none needed, or key
  the throttle on `token_id` for strictness.

## Branch 4 implementation notes (`feat/p8-source-producers`, 2026-11-20)

- **Runner primitive** (`harness/runner.py` `commit_log(git_url, branch,
  since, clone_env)`): `git clone --filter=blob:none --no-checkout
  --single-branch --branch <b> --shallow-since=<since>` into a temp dir
  (removed on every path), then two reads: `git log -z
  --format=%H%n%P%n%ae%n%aI%n%cI%n%B` (NUL-separated records, so a message
  cannot forge a record) and `git log --format=%x00%H --shortstat` (counts
  only). At most 200 commits per read. No `-p`/`--stat`/`--name-only`,
  `show` or `diff` (test pins the argv). A window with no commits ("no
  commits selected for shallow requests") is `[]`. Behind the module seam;
  tests use a local `file://` fixture repo in `tmp_path`.
- **Git producer** (`harness/ingestion.py`, `harness.tasks.
  ingest_repo_commits`, runner beat every 6 h, harness queue): every enabled
  `HarnessRepo`; one `git.commit` episode per commit, external id
  `commit:<repo id>:<sha>`, mapping key `repo:<repo id>`, `private` for a
  `sensitive` repo. Owner text only for authorship `owner`; otherwise the
  recorded `authorship` is `merge` (a merge commit's subject is
  machine-written — added rule), `foreign_author`, `agent_trailer`
  (`Co-Authored-By:` Claude/Anthropic/Codex/OpenAI/Copilot/…,
  `Claude-Session:`, "Generated with Claude Code/Codex",
  `Generated-by:`/`Assisted-by:`) or `harness_branch` (a
  `harness/pN-<uuid>` name in the message, or the `produced_commit_sha` of
  one of the repo's implementation jobs). Metadata: sha, repo id/name,
  branch, authored date, files changed / insertions / deletions,
  `owner_authored`, `authorship` — never the author address.
- **Cursor** (`HarnessRepo.last_ingested_sha` / `last_ingested_at`,
  migration `harness/0013_repo_commit_cursor`): first run looks back
  `MEMORY_GIT_INGEST_LOOKBACK_DAYS` (default 2); later runs re-read from 24 h
  before the cursor's committer date (late-pushed commits) and skip the
  cursor commit and its ancestors, so a re-run records nothing (and bumps no
  `duplicate` counter); a lost cursor falls back to the port's dedupe. While
  the git source is disabled or paused nothing is cloned and every enabled
  repo's cursor moves to "now" with no sha, so re-enabling never back-fills
  the paused window. A failing repo is logged and the others continue.
- **Harness outcomes** (`harness.ingestion.record_discovery_outcome` /
  `record_implementation_outcome`): called from `execution._finalize`
  (after the briefing/work_log, with the CLI-reported cost), the recovery
  scan, `resolve_running_job_after_unexpected_error`, `cancel_harness_job`
  (pending → cancelled), `record_implementation_work_log` (every post-run
  terminal path), Gate A rejection/expiry and the pre-Gate-A cancel. Kinds
  `harness.discovery` / `harness.implementation`, external ids
  `harness:<id>` / `harness-impl:<id>`, mapping key `repo:<repo id>`, the
  job's `sensitivity`. The goal is owner text only when the installation
  owner (human, `is_owner`, not `is_agent`) created the job; otherwise it is
  context ("Goal (not owner-authored): …"). Context: briefing summary /
  diff summary / latest review summary, each ≤ 1000 chars; the raw CLI
  transcript never. Each write runs in its own savepoint and never raises.
- **Calendar producer** (`connectors/ingestion.py`,
  `connectors.tasks.ingest_calendar_metadata`, compose beat every 6 h):
  every enabled `google` binding whose `calendar_agenda` tool is `ok`,
  yesterday and today, through the new `google.read_calendar_metadata` —
  the same request shape (so the existing `calendar.events.agenda` pin
  covers it), credentials and granted-scope check, outside the chat tool
  registry (a system read: no channel, nothing shown to a model; one
  `connector.ingest_read` audit row per binding with counts only).
  `GoogleCalendarReader.event_metadata` copies only id, title, start/end,
  all-day, attendee count and `organizer.self`. Owner-organised titles →
  owner text; others → context. Kind `calendar.event`, external id
  `gcal:<binding id>:<event id>`, mapping key `calendar:<binding id>`,
  metadata `data_class: personal` (the source's `is_personal_class` drives
  branch 5's `personal_data=True`).
- **Port helper** `memory.ingestion.ports.source_accepting(kind)` lets
  producers skip expensive reads for a disabled/paused source.
- Tests: `harness/tests/test_commit_ingestion.py`,
  `test_outcome_ingestion.py`, `test_source_producers_e2e.py` (the
  "done when" fixture day: four sources, personal + firm repos, a job, a
  hook event through the port, a calendar event → episodes in the right
  workspaces, counters equal row counts, no raw secret),
  `connectors/tests/test_calendar_ingestion.py`,
  `config/tests/test_source_producers_beat.py`.

### Owner steps (pending) — `feat/p8-source-producers`, recorded 2026-11-20

1. On the Mac runner: `manage.py migrate` (harness `0013`), restart `make
   harness-runner` so its beat picks up `memory-ingest-git-commits`; confirm
   `git` ≥ 2.27 (blob-less + `--shallow-since`) and that the runner's clone
   credentials reach every enabled repo (the producer uses the same
   ambient/env credentials as discovery clones).
2. Set `MEMORY_OWNER_GIT_EMAILS` to the e-mails the owner commits with by
   hand; add `repo:<HarnessRepo id>` mapping rules for the git and harness
   sources (firm repos → the firm workspace) and a `calendar:<binding id>`
   rule for the Google binding.
3. Enable the git, harness and calendar sources and, after a day, check the
   audit view: stored counts per source, the `authorship` mix of commits
   (tighten the e-mail list if agent commits show up as `owner`, R2) and
   the unmapped-keys list.
4. Real-Google pass: the calendar producer has only run against the fake
   bridge; confirm one real read lands owner-organised titles only and no
   location/description/attendee address (DB check).
5. Independent Sonnet 5 review of this branch before merge.

### Branch 4 (`feat/p8-source-producers`) — known follow-ups (from review, unresolved at merge)

- **LOW — "logged and counted" is only logged**
  (`backend/harness/ingestion.py`). A producer ingestion failure is logged by
  `_safely`, but no counter is bumped: `IngestionCounter` has no error field,
  so a failed write is invisible in the audit counters. In `ingest_repo` the
  `error` status appears only in the task result dict. Fix: record the failure
  in an existing audit surface, or as an owner-visible note in the branch 6
  audit view. Until then, "counted" means the task result and the log.
- **LOW — cursor advances past failed commits** (`backend/harness/ingestion.py`).
  `ingest_repo` moves `last_ingested_sha`/`last_ingested_at` to the tip even
  when some commits hit status `error` (a DB failure in the savepoint). Those
  commits are never retried, because the ancestry skip covers them. Commits
  beyond the 200-commit cap in one window are skipped the same way. Fix: do not
  advance the cursor when the results contain `error`; log a warning when
  `len(commits)` equals the cap.
- **LOW — `commit_log` uses the host git config and ambient credentials**
  (`backend/harness/runner.py`). The clone and `git log` run without
  `_ISOLATED_GIT_ENV` and with `clone_env={}`, so the host's global git config
  applies and private-repo auth relies on ambient credentials. This matches
  discovery's `clone_env={}`, but the `--shortstat` read in a blob-less clone
  lazily fetches blobs without the clone credentials, and may fail or be slow
  on private repos. The credential caveat is recorded in the owner steps. Fix:
  pass `_ISOLATED_GIT_ENV` plus the resolved clone env to the clone and to
  `_run_git_log`. Real-remote verification is an owner step.
- **LOW — owner authorship rests on the author e-mail alone**
  (`backend/harness/ingestion.py`). The author e-mail is trivially forgeable in
  a git commit, so a foreign commit carrying the owner's e-mail would enter
  `owner_text`. This is the brief's accepted rule and stays as a residual risk.
  Fix (optional): also require the committer e-mail to match.

## Branch 5 implementation notes (`feat/p8-learning-loop`, 2026-11-20)

- **Models** (migration `memory/0009_learning_loop`): `EpisodeExtractionJob`
  (workspace-scoped, lease-fenced like `MemoryExtractionJob`: `night`,
  `personal_data`, fixed `episode_ids`, `status` pending/running/succeeded/
  failed/**deferred**, `candidates` waiting for consolidation, its own
  consolidation claim token/lease and `consolidated_at`);
  `CoreBlockProposal.ingestion_source` (the FK branch 2 deferred);
  `Memory`/`CoreBlockProposal.forget_operation_id` + `forget_prior_status`
  (the forget cascade marker); `IngestionCounter.
  proposals_dropped_no_provenance` and `learning_drops` (reason → count:
  `triage_no_owner_text`, `triage_short`, `triage_duplicate`,
  `deferred_budget`, `no_provenance`, `self_target`, `invalid_target`,
  `duplicate`, `merged`, `nightly_cap`, `weekly_cap`, `over_budget`,
  `consolidation_failed`, `consolidation_schema_invalid`,
  `private_block_target`, `source_forgotten`, `source_paused`).
- **Planning** (`memory/ingestion/extraction.py`, nightly beat
  `memory.ingestion.tasks.run_learning_night_task`, compose beat every
  24 h): per workspace with un-extracted live episodes of an accepting
  source, at most one open job set at a time; deterministic triage (empty /
  < 20 chars / duplicate fingerprint → marked extracted, counted, no call);
  personal-class (calendar) and `private` episodes in their own jobs (sent
  with `personal_data=True`), ≤ 25 episodes per job.
- **The call** (`memory.episode_extraction`, tier `cheap`, prompt
  `friday_episode_extraction` v1): per-source daily budget checked first
  (episodes over it stay un-extracted → `deferred`, no call); every text and
  metadata value scrubbed a second time; payload = system prompt + one user
  message with `<owner_items>` (owner text only, each with its episode id)
  and one `<untrusted_context>` (context text + metadata JSON), all fenced
  with `neutralize_fence` / `strip_invisible_formatting`. Context report:
  job id, source kinds, episode count, token estimate — never text. Tokens
  spent are attributed to sources by estimate share (`tokens_spent`).
  `BudgetExceeded` → `deferred` (episodes for the next night, nothing loops);
  a retryable gateway error (e.g. `NoProviderAvailable` for a personal-data
  job with only cloud providers) → back to `pending` (recovery scan, ≤ 3
  attempts); a schema error → `failed`, terminal, the sent episodes are
  marked extracted so the same input is never re-sent.
- **Provenance** (`memory/provenance.py`, shared with chat extraction): the
  quote must be ≥ 12 chars after whitespace normalisation and a verbatim
  substring of the cited episode's (sent) owner text in the same job; else
  `no_provenance`. `self` targets → `self_target`; a core-block target the
  workspace cannot hold (`developer`/`preferences` from a firm episode) →
  `invalid_target`; a core-block target from a `private` or personal-class
  (calendar) episode → `private_block_target` (ADR-005: a block has no
  per-line sensitivity and normal blocks reach cloud payloads, so such
  content may only become a sensitivity-carrying memory proposal;
  consolidation refuses it again as defence in depth).
- **Inactive sources** (review round 1): `finalize_success`, the start of
  consolidation and `_finish` (under the job lock) re-check each
  candidate's source; one forgotten (`source_forgotten`), paused or
  disabled (`source_paused`) meanwhile yields no proposal, closing the race
  with `forget_source`, whose candidate strip is scoped to the source's
  workspace.
- **Consolidation** (`memory/ingestion/consolidation.py`, purpose
  `memory.consolidation`, tier `mid`, prompt `friday_memory_consolidation`
  v1): triggered on commit when a workspace's last extraction job closes
  (plus the nightly leftovers pass). Per workspace and data class: fingerprint
  dedupe against every memory of the workspace and within the batch,
  core-block line already in the block, embedding similarity (existing
  `memory.retrieval` path, cosine distance < 0.08, best effort); then one
  call that may only select candidates by index, reword title/content and
  rank — quote, episode, source and target stay code-held. The existing
  context sent is `cloud_eligible()` memories and non-private, non-`self`
  blocks only. Caps in code: `MAX_PROPOSALS_PER_NIGHT = 10` per owner per
  local day across all workspaces (serialised by locking the owner's
  workspaces; the rest discarded and counted), the weekly core-block cap via
  `create_core_block_proposal`. A core-block proposal appends the candidate
  line to the block's current content (refused over the label budget).
  Every output row is `pending_approval` / `pending`; job candidates are
  cleared after consolidation. Budget refusal / retryable error → claim
  released, candidates wait for the next night; schema error → discarded.
- **Budgets** (D13): `seed_ai_defaults` creates `learning` (both purposes,
  monthly `AI_BUDGET_LEARNING_MONTHLY_USD`=20), `learning_extraction_daily`
  (`AI_BUDGET_EPISODE_EXTRACTION_DAILY_USD`=1) and
  `learning_consolidation_daily` (`AI_BUDGET_CONSOLIDATION_DAILY_USD`=0.5)
  once; owner edits are never overwritten.
- **Morning card** (`memory/ingestion/digest.py`, `memory/learning_views.py`;
  owner, web session, default authentication so every PAT kind is 401):
  `GET /api/v1/memory/learning/digest[?days=]` (pending learned proposals of
  the last `MEMORY_LEARNING_DIGEST_DAYS`=2 local days, grouped by workspace,
  each with title, content, quote, source kind, date, `bulk_approvable`;
  audited `memory.learning.digest_viewed`, ids only);
  `POST /api/v1/memory/learning/proposals/memory/<id>/{approve,edit,reject}`
  (existing `approve_memory`/`edit_memory`/`reject_memory`, curated fields
  allowed) and `…/core-block/<id>/{approve,reject}` (existing core-block
  services; stale base version → 400 `memory.core_block.stale_version`);
  `POST /api/v1/memory/learning/bulk-approve {"ids"}` — only when every id is
  a pending `work_log` memory, otherwise the whole request is refused.
  Decisions bump `proposals_accepted`/`proposals_rejected` (acceptance
  metric). Core-block proposals have no "edit" action in 8A (approve or
  reject; the owner edits the block itself afterwards).
- **Chat extraction schema v2**: `friday_memory_extraction` v2 is seeded and
  activated over the seeded v1; user messages are sent verbatim, every other
  role inside `<untrusted_context role=…>`; candidates need a `quote` that
  matches a `user` message verbatim (stored as `provenance_quote`); a
  v1-shaped output is a terminal schema error. The FakeAdapter quotes the
  latest plain user message (the e2e smoke's "Hello, Friday" still yields
  its suggestion).
- **Forget cascade** (`forget_source`): approved derived memories →
  `archived` (embedding cleared), pending → `rejected`, pending core-block
  proposals → `rejected`, all marked with the operation id and prior status;
  waiting candidates of the source are stripped from jobs (not restorable).
  `restore_source` puts back exactly the marked rows at their prior status
  (a `privacy_locked` row stays archived; restored approved rows are
  re-embedded). `updated_at` is not touched, so a round trip leaves the rows
  as before. `forget_preview` now names memories and proposals too.
  Already-*approved* core-block content derived from the source stays in the
  block (it is the owner's versioned block; rollback is the owner's tool).
- **Purge** (`tools/purge.py`): new payload keys `episode_ids` (trashed
  episodes) and `forgotten_memory_ids` (memories a forget operation
  archived/rejected); gate-class as before, audited `memory.episode_purged`
  / `memory.purged` with the approval request id.
- **Vault mirror**: verified, no change needed — the export prunes notes of
  rows that are no longer approved; a test exports, forgets, re-exports
  (note removed) and restores (note back).
- **TTL**: the daily TTL beat also deletes extraction jobs older than their
  workspace's episode TTL (their candidates derive from episode text).
- Tests: `memory/tests/test_learning_{extraction,consolidation,digest,
  night}.py`, `tools/tests/test_learning_forget.py` (forget cascade, purge,
  vault mirror -- in `tools/tests` because memory may not import tools),
  `config/tests/test_learning_beat.py`, chat v2 cases in
  `memory/tests/test_{extraction,tasks}.py`.

### Owner steps (pending) — `feat/p8-learning-loop`, recorded 2026-11-20

1. Deploy, `manage.py migrate` (memory `0009`) and `manage.py
   seed_ai_defaults` (prompts `friday_episode_extraction`,
   `friday_memory_consolidation`, `friday_memory_extraction` v2, the three
   learning budgets); check the budgets in the admin.
2. Make sure a `personal_data_allowed` (local) chat provider is active:
   calendar and private episodes are extracted only through one; without it
   those jobs fail closed and the episodes expire unlearned.
3. Manual pass with a real model (CI uses the fake provider only): after a
   real night, read the morning card (`GET /api/v1/memory/learning/digest`;
   the UI is branch 6), confirm every quote is the owner's own words and the
   firm items sit in the firm workspace, and check the spend in the audit
   view against the 7–13 USD/month estimate.
4. Start the two-week acceptance count for the go/no-go (closeout).
5. Independent Sonnet 5 review of this branch before merge.

### Branch 5 (`feat/p8-learning-loop`) — known follow-ups (from review, unresolved at merge)

- **LOW — `backend/tools/purge.py` / `backend/memory/ingestion/services.py`**:
  the permanent-purge extension only covers trashed episodes and forgotten
  memories. Core-block proposals that forget-source rejected stay in the DB
  permanently, as does the `provenance_quote` text of anything the owner
  decides to purge; no purge key covers them. Fix: add a
  `forgotten_proposal_ids` key to `purge_trash`, or delete forget-rejected
  `CoreBlockProposal` rows when the matching memories are purged (or accept
  this as a known residual).
- **LOW — `backend/memory/ingestion/consolidation.py` (`_create_proposal`)**:
  several core-block proposals for the same label in one night are all built
  from the block's current content; the first approval makes the others stale
  (400 `stale_version`) and their appended lines are lost. Optional fix: at
  most one core-block proposal per label per night, or build the base from
  the pending proposal.
- **LOW — `backend/ai_core/management/commands/seed_ai_defaults.py`**:
  `memory.extraction` now parses schema v2 only, so between `migrate` and
  `seed_ai_defaults` the active prompt is still v1 and chat extraction fails
  with a terminal schema error. Already covered by owner step 1; keep the
  ordering (seed before the workers run).
- **LOW — `backend/memory/ingestion/consolidation.py`**: the consolidation
  model may reword a candidate's content; the stored quote stays the verified
  one but the reworded content is not re-checked against it. The owner sees
  both on the card and approval is required (accepted design). Optional:
  reject rewordings that grow far past the candidate's length.

## Branch 6 implementation notes (`feat/p8-learning-ui`, 2026-11-20)

- **Read tools** (D12; ungated `ReadToolSpec`s, web channel only, data class
  `firm`): `coding_sessions_status` (`backend/tools/coding_sessions.py`,
  registered in `tools.apps`) and `harness_jobs_status`
  (`backend/harness/tools.py`, registered beside the harness gates). Shared
  rules in `tools/owner_reads.py`: a human installation owner who is a
  member of the conversation's workspace (else `ReadToolError("forbidden")`),
  any non-web channel → `channel_forbidden`, strict arguments (only an
  optional `limit` ≤ 20), the listing scoped to the conversation's own
  workspace with `for_actor()` (another workspace's sessions/jobs never
  listed), a bounded JSON-lines result that the chat turn fences with the
  existing `render_connector_block` (untrusted data), and one
  `read_tool.read` audit row per call, every outcome, ids and counts only.
  Sessions of a forgotten source are hidden; `harness_jobs_status` re-checks
  the curator role through `list_jobs`, lists the owner's goal (≤ 200 chars)
  but never model-written briefings, and a job of a `sensitive` repo makes
  the result personal-class (`ReadResult.data_class`, the Phase 5 GitHub
  precedent). Each tool is offered to the router only where the workspace
  has something to read (a live-source session / a harness job), so existing
  read-vocabulary behaviour is unchanged elsewhere. No summarisation call:
  the normal `chat` completion answers from the fenced block (e2e test with
  the FakeAdapter: purposes `router.intent`, `router.connector_args`,
  `chat`).
- **`self` anchors.** `GET /api/v1/ai/self-sources`
  (`assistant/self_views.py`; owner, web session, default authentication so
  every PAT kind gets 401): the committed snapshot as a flat anchor list
  (identity, rules, each accepted ADR's decision line, current phase) with
  `file:line`. Lives in `assistant` because `memory` may not import it.
- **Frontend.** BFF routes under `frontend/src/app/api/memory/` (segments,
  labels and action names validated before interpolation; `proxyDelete` and
  `workspaceSuffix` added to `lib/bff-proxy.ts`). Pages `/memory/learning`
  (`LearningCardView`), `/memory/ingestion` (`IngestionView`; sources live in
  the owner's personal workspace whatever workspace is active),
  `/memory/core-blocks` (`CoreBlocksView`, active workspace; edits send
  `base_version`; rollback = confirm dialog; `self` shows no edit/rollback
  control) and `/sessions` (`SessionsView`: coding sessions across the
  owner's workspaces + the active workspace's harness jobs, composed
  client-side). HUD: `HudSessionsPanel` fills the Phase 6 sessions slot and
  `HudLearningPanel` shows the pending count — both read-only with deep links
  (D8). Nav: "Oturumlar" (work) and "Sabah kartı" (memory); the sidebar
  marks only the most specific matching item. `/sessions` added to the
  proxy guard. Cross-workspace query keys (`learningDigest`,
  `codingSessions`, `selfSources`) live under `ALL_WORKSPACES`. New status
  domain `codingSession` maps onto the checked tones; the HUD panel uses only
  HUD text tokens already contrast-checked on `--hud-surface` (test).
- Tests: `tools/tests/test_coding_sessions_read.py`,
  `harness/tests/test_jobs_read_tool.py`,
  `assistant/tests/test_owner_read_tools.py` (chat e2e, CLI never offered the
  tool, fence, self-sources role matrix); vitest
  `src/app/api/memory/memory-routes.test.ts`,
  `components/memory/{LearningCardView,IngestionView,CoreBlocksView,SessionsView}.test.tsx`,
  `components/shell/nav.test.ts`, token/tone checks in
  `lib/theme/tokens.test.ts`.

### Owner steps (pending) — `feat/p8-learning-ui`, recorded 2026-11-20

1. Deploy (no migration) and open `/memory/learning`, `/memory/ingestion`,
   `/memory/core-blocks` and `/sessions` from a trusted device: run one full
   morning review (approve, edit, reject one item each), inspect every
   source, forget and restore one source and check the counts in the dialog
   match the audit view.
2. Repeat the review and the sessions page at phone width (real phone or
   devtools 375 px): no horizontal page scroll; the counters table scrolls
   inside its own box.
3. Manual pass with a real model (CI uses the fake provider): in a web chat
   ask "Claude Code oturumlarım ne yapıyor?" while a hooked Claude Code
   session is live on the Mac, and check the answer comes from the fenced
   session list (and harness jobs where present) of that workspace only.
4. Write the `developer` block from the editor (owner step 2 of branch 2).
5. Independent Sonnet 5 review of this branch before merge.

Note (branch 6 run, 2026-11-20): one full-suite run hit a flaky failure in
the branch 4 test
`harness/tests/test_commit_ingestion.py::test_harness_branch_and_produced_commits_keep_metadata_only`
(the commit dated "now" missing from the ingested set under load). It is not
touched by this branch; the file passes 3/3 alone and `harness/tests` passes
in full. Worth a look at the fixture's commit dates vs the
`--shallow-since` window.

