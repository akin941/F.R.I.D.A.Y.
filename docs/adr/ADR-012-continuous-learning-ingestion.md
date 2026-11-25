# ADR-012: Continuous-Learning Ingestion Behind a Privacy Pipeline

## Status
Accepted 2026-11-20 — owner approval of the Friday 3.0 roadmap
(`docs/research/2026-11-roadmap-proposal.md` §B4, §g decisions d5, d6 and d10).
Implemented by Phase 8A (`docs/prompts/phase-8a-learning-memory/brief.md`)
and extended by Phase 8B. Extends ADR-005 (memory governance). Amends
nothing else. ADR-008 decision 4 (`work_log`) and ADR-009 (reversible owner
deletes, gated purge) apply unchanged.

Numbering note: the proposal called this "ADR-011". ADR-011 is reserved by
the approved Phase 6 brief for "voice transport", so the proposal's ADRs are
numbered from ADR-012.

## Context
Friday 3.0 wants Friday to learn from the owner's work without being told:
commits, harness outcomes, Claude Code session events, calendar items and,
later (Phase 8B), the owner's own turns in Claude Code / Codex transcripts.
Today memory comes only from chat (Phase 1 extraction, every candidate
`pending_approval`) and from `record_work_log()` (auto-approved
`system_generated` rows). Continuous ingestion changes three things:

- **Volume and provenance.** Raw activity is mostly not owner-authored:
  tool output, assistant text, web pages, third-party commit messages. Any
  of it can carry prompt-injection text, and none of it is a statement of
  the owner's preferences.
- **Secrets and personal data.** Session events, shell history and
  transcripts routinely contain API keys, tokens, `.env` fragments, e-mail
  addresses, phone numbers, Turkish ID numbers (TCKN) and IBANs.
- **Workspace boundaries.** A repo path or calendar belongs to one
  workspace. Firm work must never land in personal memory (ADR-001).

## Decision
1. **Source registry.** Every ingestion source is a registered row with an
   owner, a kind, a workspace-mapping rule, a daily token budget and an
   enabled/paused state. Nothing is ingested from an unregistered or paused
   source. Default sources (d5): on in 8A — git commits of registered repos,
   harness outcomes, Claude Code hook events (metadata only), calendar
   metadata; on in 8B, after the per-directory node grant — Claude/Codex
   transcripts (owner turns only); opt-in — shell history, mail bodies, the
   Obsidian `Inbox/`.
2. **Workspace mapping before storage.** Each item is mapped to exactly one
   workspace through explicit rules (registered repo → its workspace, local
   path prefix → workspace, calendar/connector binding → its workspace).
   An item no rule maps is **dropped and counted**, never defaulted to the
   personal workspace. Firm items land only in the firm workspace.
3. **Scrubber before storage.** Every item passes a scrubber (secret rules
   in the gitleaks style plus entropy, PII patterns for e-mail, phone,
   TCKN, IBAN and card numbers, and a per-workspace term list such as
   customer names) before any row is written. Hits become typed
   placeholders (`[SECRET:aws_key]`, `[PII:tckn]`). It runs where the data
   is produced (server in 8A, node and server in 8B). A synthetic corpus is
   part of the test suite; CI requires 100% recall on its secrets class.
4. **Retention (d10).** Raw episodes expire 30 days after ingestion in a
   personal workspace and 14 days in a firm workspace, then are deleted by a
   daily task. The retention window is part of the owner's decision, like
   ADR-009's trash retention. Derived memories keep only a provenance quote
   (bounded length) and the episode id.
5. **Ingestion audit view.** Per source: counts, last N items, scrubber
   hits by type, dropped/unmapped counts, tokens spent against budget, and
   proposal acceptance rate. Owner-only, web channel.
6. **Forget source X.** One owner action moves the source's episodes and
   everything derived from them (facts in 8B, memories, the Obsidian mirror
   on its next export) to the trash and pauses the source. It is a
   reversible confirm-class move under ADR-009; permanent purge is the
   gate-class `purge_trash` path. Available from the first branch that
   ingests anything.
7. **Owner-authored extraction only.** The extraction prompt sees only
   text the owner wrote (commit messages authored by a configured owner
   identity, owner turns in transcripts, owner-written notes). Tool output,
   assistant text, web content and third-party text may be passed only
   inside an untrusted-data fence and can never be the provenance of a
   proposal.
8. **Provenance is checked in code.** Every proposal carries a quote that
   must match owner-authored episode text verbatim (after normalisation of
   whitespace). A proposal without a match is dropped and counted.
9. **Caps and approvals (d6).** At most 10 learned proposals per night per
   owner and at most 3 core-block diffs per week. Nothing learned is
   auto-approved except the existing `work_log` class (ADR-008 decision 4);
   bulk approve is allowed only for `work_log`-class items; anything that
   touches the `self`, `developer` or `preferences` core blocks is approved
   per item. Content from external tools or MCP is never auto-promoted.
10. **Never ingested:** screen content, screenshots, accessibility trees,
    keystrokes, browser history. An on-demand screenshot (Phase 10) is a
    gate-class invocation used for one turn and never stored as an episode.
11. **Gateway and cost.** Every learning model call goes through the
    gateway with its own purpose (ADR-003) and a tier; per-source daily
    budgets are checked before the call and the gateway's purpose budget
    (ADR-003 amendment 2026-11-20) is the hard stop. Personal-class sources
    (calendar, mail, transcripts) and `private`/sensitive rows are sent with
    `personal_data=True`, so only `personal_data_allowed` (local) providers
    serve them.
12. **Obsidian stays a mirror (ADR-005).** `Inbox/` notes, when that
    opt-in source is enabled, are imported once as episodes and moved to
    `Inbox/_imported/`; the vault never changes Postgres.

## Consequences
- Learning becomes possible without weakening ADR-001/003/005: every raw
  row is scoped, scrubbed and short-lived; every derived row is a proposal.
- The owner carries a review load (a capped nightly card). The Phase 8A
  go/no-go checks the acceptance rate (target ≥ 60% over two weeks) before
  more sources are added in 8B.
- Recall of the scrubber is a release gate: a corpus regression blocks the
  branch. Precision misses (over-scrubbing) are tolerated and tuned.
- Some learning value is lost on purpose: third-party text, tool output and
  screen content never become memories.
- New tables (sources, mapping rules, episodes, scrub terms) are
  `WorkspaceScopedModel`s; the ingestion code is a `memory.ingestion`
  package with import-linter contracts (no `harness`, `connectors` or node
  imports; producers in those apps call a memory-owned port instead).

## Implementation notes

**Phase 8A branch 1, `feat/p8-ingestion-core` (2026-11-20).**

- **Tables** (`backend/memory/models.py`, migration `memory/0006`):
  `IngestionSource` (personal workspace, one row per kind: `git`,
  `harness`, `claude_code_hooks`, `calendar`; `enabled`, `paused_at`,
  `daily_token_budget`, `forgotten_at`/`forget_operation_id`),
  `WorkspaceMappingRule` (`repo` / `path_prefix` / `calendar_binding` ->
  `target_workspace`), `Episode` (target workspace; scrubbed `owner_text`
  and `context_text` <= 4000 chars, bounded scrubbed `metadata`,
  `scrub_report` counts only, `expires_at`, ADR-009 `deleted_at`; unique
  `(source, external_id)`; the default manager hides trashed and expired
  rows, `all_objects` sees all), `ScrubTerm` (case-insensitive unique per
  workspace), `IngestionCounter` (per source per day, in the source's
  workspace: stored / duplicate / dropped_unmapped / dropped_paused,
  scrubber hits by type, at most 20 unmapped keys as `{hash, short}`,
  tokens and proposal counters). `Memory` gains `ingestion_source`,
  `source_episode_id` (plain UUID) and `provenance_quote` (<= 300).
- **Port:** `memory.ingestion.ports.record_episode(...)` is the only writer
  of `Episode`; it returns `stored | dropped_unmapped | dropped_paused |
  duplicate`. `source` may be the row or its kind (the installation
  owner's source of that kind). An external id outside a strict charset,
  or one the scrubber would touch, is stored as its sha256.
- **Scrubber:** `memory.ingestion.scrubber`, standard library only (branch
  3 vendors it). Corpus gate: `memory/tests/scrubber_corpus/` +
  `memory/tests/test_scrubber.py` (100% secrets recall; PII recall and
  precision printed). 40/64-char lowercase hex runs are kept as git object
  ids; every other long high-entropy run is a secret.
- **Mapping:** `~` is kept as a literal home marker rather than resolved on
  the server (the server cannot know the Mac's home): rules and keys use
  the same form. Target ownership (`WorkspaceMember.Role.OWNER`) is checked
  when a rule is written and again at resolve time.
- **Retention and forget:** daily beat
  `memory.ingestion.tasks.purge_expired_episodes_task` hard-deletes expired
  episodes, trashed or not (`MEMORY_EPISODE_TTL_DAYS_PERSONAL`/`_FIRM`).
  `forget_source` / `restore_source` (web channel, human owner, audited
  with counts) cover episodes and the source pause; the memory and
  proposal cascade and the `purge_trash` payload extension land in branch
  5. Restore leaves the source paused.
- **Owner API:** `/api/v1/memory/ingestion/{sources, sources/<id>,
  sources/<id>/{forget,restore,rules}, rules/<id>, scrub-terms,
  scrub-terms/<id>}`, `IsWebChannel` + `IsOwner`, default authentication
  only (a PAT is 401 there).
- **Contract:** "memory.ingestion may import only memory, workspaces,
  audit, core, ai_core" in `backend/pyproject.toml`; `nodes` is added when
  that app exists (import-linter refuses unknown module names).

**Phase 8A branches 2–6 (2026-11-20).** Details in the brief's per-branch
implementation notes; the decision-relevant facts:

- **Core blocks** (branch 2, `memory/0006_core_memory_blocks`):
  `CoreMemoryBlock` + `CoreMemoryBlockVersion` + `CoreBlockProposal`; `self`
  is written only by the deterministic assembler (`assistant/self_block.py`,
  no model call) from the committed `assistant/self_sources.json` snapshot
  (`build_self_sources --check` in CI); `developer`/`preferences` live only
  in a personal workspace and are written only by the owner; injection is
  first in `build_context`, fenced, web/Telegram rules as D7.
- **Hook relay** (branch 3): `PersonalAccessToken.Kind.HOOK` +
  `CHANNEL_HOOK`; the append-only `POST /api/v1/memory/ingest/hook-events`
  is the only route the hook token authenticates (URL-walk test); events go
  through `record_episode`; `CodingSession` (`memory/0008`) is the sessions
  read model. `friday-hook` is stdlib-only and carries a vendored scrubber
  copy CI keeps identical to `memory/ingestion/scrubber.py`.
- **Producers** (branch 4): the git producer on the runner beat (blob-less
  shallow clone, `git log` messages and `--shortstat` counts only, owner
  e-mail + agent-trailer + harness-branch rules, `harness/0013` cursor), the
  harness outcome producer (owner's `goal` only as owner text), the calendar
  metadata producer (owner-organised titles only; personal-class). Each
  calls the port; none can raise into its caller.
- **Learning loop** (branch 5, `memory/0009_learning_loop`):
  `EpisodeExtractionJob` (lease-fenced; personal-class and `private`
  episodes in their own `personal_data=True` jobs); second scrub before
  every call; verbatim-quote provenance check (`memory/provenance.py`, also
  used by chat extraction schema v2, where only `user` text is quotable);
  consolidation with code-held quote/episode/target; caps in code (10
  proposals per owner-night, 3 core-block proposals per week); budgets
  `learning`/`learning_extraction_daily`/`learning_consolidation_daily`;
  forget-source cascade to memories and proposals with exact restore;
  `purge_trash` keys `episode_ids` and `forgotten_memory_ids`.
- **UI and read tools** (branch 6): `/memory/learning`, `/memory/ingestion`,
  `/memory/core-blocks`, `/sessions`, HUD panels; read tools
  `coding_sessions_status` and `harness_jobs_status` (web only, fenced,
  `for_actor()`, audited).

**Phase 8A closeout (2026-11-20).**

- **Preflight** (`backend/ops/preflight.py` `check_learning`, read-only, no
  model call): `learning_sources`, `learning_beat` (reads both beats'
  schedules from the Celery app's `friday_beat_schedules`, since `ops` may
  not import `config`), `learning_scrubber` (the scrubber self-test over the
  committed corpus, `PREFLIGHT_SCRUBBER_CORPUS_DIR` to override; any missed
  secret is a `fail`), `learning_budget`, `learning_last_run`.
- **End to end** (`backend/ops/tests/test_learning_e2e.py`, fake providers):
  owner web API enables and maps the sources → a fixture commit (local
  `file://` repo) and a hook event → nightly job (eager) → morning card →
  approve → the memory is in the next chat answer's fenced context and its
  `ModelUsage` context report; no secret in any gateway payload, firm text
  never in a personal payload.
- **Runbooks:** `docs/runbooks/claude-code-hooks.md` (preflight section) and
  `docs/runbooks/learning-memory.md` (enable sources, mapping rules, the
  audit view, the morning card, forget/restore, go/no-go).
- **Go/no-go** (owner, after two weeks of real use): scrubber corpus 100%
  secrets recall, zero cross-workspace leaks in tests (both met in CI),
  ≥ 60% of proposals accepted (pending, recorded in the brief).

**Phase 8B branch 1, `feat/p8-temporal-facts` (2026-11-20; part 1 of the
8B note, the closeout extends it).** Details in
`docs/prompts/phase-8b-transcripts-graph/brief.md` D2, D3, D7.

- **Temporal facts are learned data under this ADR.** `memory.MemoryEntity`
  and `memory.MemoryFact` (migration `0010`) live in the `memory` app.
  Owner-text facts are born `pending_approval` through
  `memory.graph.ports.propose_fact` only (confidence capped at 90, a
  verbatim provenance quote required), and are approved per item by the
  owner, a human over the web channel. Owner-typed facts are born
  `approved`, with the owner's words as the quote.
- **Graphify rows are not owner data.** `origin=graphify` ⇔
  `status=system` (a DB check). They are never approvable, editable or
  trashable through the owner API (405). The importer
  (`ports.import_code_graph`) landed in branch 5 (below).
- **Facts are in the forget cascade (decision 6).** `forget_source` archives
  the source's approved learned facts and rejects its pending proposals
  (clearing their `supersedes` link) under the same operation id. It also
  trashes entities left with no live fact. `restore_source` brings back
  exactly those rows; a restored proposal returns without its link.
  `purge_trash` gains `fact_ids` and `entity_ids` and stays gate-class.
  Purging a fact first rejects any pending proposal that supersedes it.
- **Workspace axis.** A fact's subject, object entity and `supersedes`
  target are in the fact's own workspace: checked in `MemoryFact.save()`
  and by the `memory_memoryfact_same_workspace` trigger.
- **Import contract.** `memory.graph` may import only `memory`,
  `workspaces`, `audit`, `core` and `ai_core`, like `memory.ingestion`.

**Phase 8B branch 7, `feat/p8-obsidian-mirror` (2026-11-20).** Details in
`docs/prompts/phase-8b-transcripts-graph/brief.md` D12.

- **`Inbox/` is `00_Inbox/`.** Decision 12's `Inbox/` is the vault layout's
  documented `00_Inbox/` folder (`docs/memory/obsidian-vault-strategy.md`),
  and `Inbox/_imported/` is `00_Inbox/_imported/<YYYY-MM-DD>/`. No change
  to the decision text.
- **The opt-in source.** `IngestionSource.Kind.OBSIDIAN_INBOX`
  (`obsidian_inbox`, migration `memory/0011`), seeded disabled. It is not
  personal-class (decision 11 lists calendar, mail and transcripts only):
  a normal note is extracted like a git commit; only a note with
  `sensitivity: private` frontmatter (any value other than `normal`, fail
  closed) becomes a `private` episode routed to local providers.
- **Mapping.** A new `vault:` key prefix and `vault_prefix` rule type
  (longest segment-boundary prefix, like `path_prefix`), valid only on the
  `obsidian_inbox` source; `normalise_vault_path` refuses absolute, `~`
  and escaping (`..`) paths and anything not under `00_Inbox`. Unmapped
  notes are dropped, counted and left in place (decision 2).
- **Inside an approved run only.** The import runs inside a claimed,
  approved `vault_export` run (`tools.export.claimed_run_scope`), in the
  owner's personal workspace's run only. Each episode commits before its
  note is moved; dedupe on the episode's `external_id` (sha256 of the path
  plus the content) means a failed move never duplicates. The vault still
  never changes Postgres except by creating episodes, and conflict copies
  (owner-edited mirror files) are never read back.

**Phase 8B branch 5, `feat/p8-graphify-job` (2026-11-20; part 2 of the 8B
note: Graphify as `system`).** Details in
`docs/prompts/phase-8b-transcripts-graph/brief.md` D3, D7, D10 and
`docs/mcp/graphify.md`.

- **Graphify rows are `system`, not learned data.** They are a
  deterministic, model-free AST index of a registered repository's own
  code (`graphifyy==0.9.103`, `--network none` harness container), not text
  the owner wrote, so this ADR's per-item approval does not apply: they
  never reach the morning card, get no embedding, take no part in
  retrieval and never enter the memory block. They are written only by
  `memory.graph.ports.import_code_graph` (a temporal replace per repo head,
  serialised on `memory.CodeGraphImport`, migration `0012`) and read only
  by the deterministic `self` line `codebase`, the read tool and the
  read-only graph view. Every label is scrubbed and fence-neutralised
  before storage, so the scrubber guarantee still holds for them.
- **Outside forget and the owner trash, inside the gate.** Turning a
  repo's `graphify_enabled` switch off retires its rows (windows closed,
  entities hidden, nothing deleted, audited with counts); a permanent
  removal is only the gate-class `purge_trash` key `graphify_repo_ids`
  (ADR-009). For a Graphify entity `deleted_at` means "not in the current
  snapshot or retired"; the owner trash and restore refuse Graphify rows.
- **Workspace axis.** Rows land in the repo's own workspace; the `self`
  line reads only the owner's personal workspace, so a firm repo's graph
  never reaches it.

**Phase 8B closeout, `chore/p8b-closeout` (2026-11-20; part 3, completing
the 8B note).** Details in `docs/prompts/phase-8b-transcripts-graph/brief.md`
D4, D8, D13, D14 and the branch 8 and 10 records.

- **Transcript sources (decisions 1, 2, 11).** `claude_code_transcripts`
  and `codex_transcripts` are personal-class sources, seeded disabled.
  They own no mapping rules: they route through the `claude_code_hooks`
  source's `path_prefix` rules (one path registry), and only owner-typed
  turns are stored, scrubbed on the node and again on the server (with the
  workspace's scrub terms), with harness-injected markers re-checked
  (`dropped_not_owner`) and rows outside the invoke's workspace dropped
  (`dropped_foreign`). The pull is a signed, chain-audited node read per
  workspace (ADR-013); until the `nodes`-backed port lands it is a no-op.
- **The scrubber corpus (decision 3).** `transcripts.jsonl` (14 labelled
  fragments) is part of the 100% secrets-recall gate for the server
  scrubber and the vendored node scrubber. Since the closeout preflight's
  `learning_scrubber` **fails** when the file is missing or has no secret
  item, instead of skipping it.
- **Facts follow the episode's class (decision 11).** A fact inherits its
  cited episode's sensitivity, so a transcript- or `private`-derived fact
  is extracted only on a `personal_data_allowed` provider, and a `private`
  fact never reaches a model payload or the open vault tree.
- **Retrieval default.** `MEMORY_RETRIEVAL_MODE` stays `legacy` at the
  closeout: the public compare passed (recall@10 0.5714 → 0.875, leak rate
  0), but the owner's private compare is not recorded (pending owner
  evaluation). Hybrid retrieval only ever ranks `model_eligible()` facts
  after the workspace filter.
- **Operations.** `manage.py preflight` gains `graph_integrity` (no fact
  crosses workspaces, superseded facts are closed, no inverted window),
  `retrieval_indexes`, `graphify`, `compressor`, `transcripts` and
  `eval_baseline`, and extends `learning_budget` (reconciliation,
  reflection, eval), `learning_beat` (reflection, transcript pull,
  Graphify recovery) and `learning_scrubber` (above). The end-to-end test
  `ops/tests/test_transcripts_e2e.py` runs fixture transcripts through the
  reference node double to an approved fact used in a hybrid chat turn,
  mirrored as an entity note, forgotten (fact archived, note pruned) and
  restored.

## Note 2026-11-20 — Phase 12 playbooks (branch `feat/p12-harness-playbooks`)

Phase 12 D17 distils procedural playbooks (SKILL.md instruction skills,
ADR-016 decision 5) from successful harness implementation jobs. This note
records how they relate to this ADR:

- **Playbook sources are not episodes.** `skills.PlaybookSource` rows are
  recorded beside the `harness` episode (`harness.ingestion`, through
  `skills.ports.record_playbook_source`) but never pass through the
  ingestion port, the learning queue or memory. They hold the owner-typed
  goal, the job's `repo_test_command` snapshot, the changed-file **paths**
  (no contents), test counts and the model-produced diff/review summaries
  as bounded context text. A job is recorded only when it `succeeded`, its
  review approved it, its tests passed and the installation owner typed its
  goal; sensitive repos record nothing.
- **Decision 7/8 applied to skills.** Model output is never the provenance
  of a learned item: a playbook step is kept only when its `quote` is a
  verbatim span (after whitespace normalisation) of its source job's
  owner-typed goal or its test command, checked in code
  (`skills.playbooks.verify_steps`). Agent-chosen paths are shown as
  unverified hints; the context text can never be a quote. Fewer than two
  verified steps drop the draft (counted on `PlaybookDistillRun`).
- **Proposed, never promoted.** The draft becomes a `proposed` skill
  version (origin `playbook`); only the owner approves it, by content
  hash, over the web (ADR-016 decision 4). The distiller runs unattended
  with one allowed purpose (`skills.playbook_distill`) and may write only
  the proposal and its own run rows.
- **No feedback loop.** Skills mounted into a later job live outside the
  clone; a produced commit touching `.claude/skills/` fails closed, and
  such paths never reach a playbook source.

## Note 2026-11-25 — UUID-valued metadata (scrubber threat model)

Random uuid4 identifiers in episode metadata (e.g. `job_id`) were sometimes
read as a phone, card or TCKN and corrupted. `scrub_value` now keeps a
metadata string that is **exactly one** canonical RFC 4122 UUID (all lowercase or all uppercase) unchanged; that value skips the whole scrub, secret rules and scrub terms included (a mixed-case value is still scrubbed as text).
Free text gets no exemption: a UUID inside prose is scrubbed like any other
text, so over-scrubbing stays the failure mode. Threat model: the scrubber
stops *accidental* secrets and third-party PII in the owner's own sources; it
cannot stop deliberate encoding (a number hidden in a UUID-shaped value,
base64, spelled-out digits). That residual is accepted here. An earlier
in-text "UUID shield" was rejected in review because it opened PII bypasses
(IBAN followed by a UUID; a number inside a UUID-shaped token).
