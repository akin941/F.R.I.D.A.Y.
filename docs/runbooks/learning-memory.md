# Runbook — Learning memory (Phase 8A, 8B)

Phase 8A closeout (brief `docs/prompts/phase-8a-learning-memory/brief.md`,
ADR-012), completed by the Phase 8B closeout (2026-11-20, brief
`docs/prompts/phase-8b-transcripts-graph/brief.md`): transcripts (§2a), the
Obsidian `00_Inbox/` (§2b), facts and the temporal graph (§8) and vault
conflict copies (§9). The eval set has its own runbook, `memory-eval.md`.
How the owner turns on continuous learning, routes what it reads
to the right workspace, reads the audit view, reviews the morning card, and
forgets or restores a source. Every control below is **owner only, web
session only** (a PAT of any kind gets 401 on these endpoints); the web app
pages are `/memory/ingestion`, `/memory/learning`, `/memory/core-blocks` and
`/sessions`.

What Friday learns from in 8A (d5): **git commits** of registered harness
repos, **harness outcomes** (the owner's typed goal; model text only as
fenced context), **Claude Code hook events** (sessions panel only — no owner
text, so never a proposal) and **calendar metadata** (titles of events the
owner organised; never bodies, addresses or locations). Nothing is learned
from tool output, web content, MCP reads or agent-written commits.

Every learned row is a **proposal** (`pending_approval`); nothing but the
existing `work_log` class is ever born approved. At most 10 proposals a night
and 3 core-block proposals a week; each quotes the owner's own words.

## 1. Prerequisites (once, after deploy)

1. `manage.py migrate` (memory `0006`–`0009`, accounts hook kind, harness
   `0013`).
2. `manage.py seed_ai_defaults` — **before** the workers pick up chat
   extraction: it seeds the prompts `friday_episode_extraction`,
   `friday_memory_consolidation`, `friday_memory_extraction` v2 and the three
   learning budgets (`learning` 20 USD/month, `learning_extraction_daily`
   1 USD, `learning_consolidation_daily` 0.5 USD). Owner edits of a budget
   are never overwritten.
3. `manage.py seed_ingestion_sources` — creates the four sources in the
   owner's personal workspace, **disabled**.
4. Settings (`.env`, never committed): `MEMORY_OWNER_GIT_EMAILS` (the author
   e-mails whose commits count as owner text — without it every commit is
   metadata only), optionally `MEMORY_EPISODE_TTL_DAYS_PERSONAL` (30) /
   `_FIRM` (14), `MEMORY_INGESTION_DEFAULT_DAILY_TOKEN_BUDGET`,
   `MEMORY_LEARNING_DIGEST_DAYS` (2).
5. Calendar and `private` (sensitive-repo) episodes are extracted only by a
   `personal_data_allowed` (local) provider. Without one those jobs fail
   closed and the episodes expire unlearned — keep a local chat provider
   active if you enable the calendar source.
6. The git producer runs on the **harness runner's beat**
   (`make harness-runner`, `HARNESS_RUNNER_BEAT=true`) because it clones
   with the runner's credentials; everything else runs on the compose
   worker's beat.

## 2. Enable sources and write mapping rules

An item whose mapping key matches no rule is **dropped** and counted (its
key appears hashed in the audit view's unmapped list) — never stored in a
default workspace. Rules may target only workspaces you own; a firm item
lands only in the firm workspace.

| Source | Rule type | Pattern | Mapping key |
|---|---|---|---|
| `git` | `repo` | the `HarnessRepo` id | `repo:<id>` |
| `harness` | `repo` | the `HarnessRepo` id | `repo:<id>` |
| `claude_code_hooks` | `path_prefix` | an absolute path, e.g. `/Users/<you>/code` (longest prefix wins) | `path:<cwd>` |
| `calendar` | `calendar_binding` | the Google `ConnectorBinding` id | `calendar:<binding id>` |

From the web app (`/memory/ingestion`, the source's mapping-rules section) or the API:

```http
GET   /api/v1/memory/ingestion/sources?workspace=<personal id>
PATCH /api/v1/memory/ingestion/sources/<id>          {"enabled": true}
POST  /api/v1/memory/ingestion/sources/<id>/rules    {"rule_type": "repo", "pattern": "<repo id>", "target_workspace": "<workspace id>"}
PATCH /api/v1/memory/ingestion/rules/<rule id>       {"enabled": false}
```

`PATCH … {"paused": true}` pauses a source (new items are dropped and
counted as `dropped_paused`); `{"daily_token_budget": N}` caps its nightly
extraction spend (episodes over the budget wait for the next night).

Workspace-wide **scrub terms** (a client name, a project codename) are
replaced before storage: `POST /api/v1/memory/ingestion/scrub-terms
{"workspace": "<id>", "term": "Acme"}`.

For the hook source, follow `docs/runbooks/claude-code-hooks.md` (hook PAT,
Keychain, `friday-hook`, `~/.claude/settings.json`).

### 2a. Claude Code and Codex transcripts (Phase 8B branch 8)

Two more sources, `claude_code_transcripts` and `codex_transcripts`, read
**only the turns you typed** in Claude Code sessions and Codex threads —
never assistant text, tool calls, tool results, file contents or
harness-injected text (`<system-reminder>`, `<command-…>`,
`<environment_context>`, …). They are **personal-class**: their episodes are
extracted only by a `personal_data_allowed` (local) provider, like calendar.

- **No rules of their own.** They route through the `claude_code_hooks`
  source's `path_prefix` rules (one path registry); a rule write on a
  transcript source is refused (400, "Path rules are shared with Claude Code
  hooks"), and the audit view shows `rules_shared_with: claude_code_hooks`.
  Without a hooks rule for a directory, its turns are never read.
- **How it runs.** The worker beat pulls every
  `MEMORY_TRANSCRIPT_PULL_MINUTES` (15): one read per workspace that has hooks
  rules, sent to your Mac node with exactly that workspace's prefixes, as you
  (`channel = system`). Each pull writes one `memory.transcript_pull.invoked`
  audit row (counts only). The node scrubs every turn; the server scrubs it
  again with the workspace's scrub terms and drops any turn that starts with
  an injected marker (`dropped_not_owner`) or whose directory maps to another
  workspace (`dropped_foreign`).
- **The 7-day window.** The node refuses to read further back than 7 days.
  The first pull starts at the time you enabled the source; a Mac that was
  asleep, offline or ungranted for over a week loses the older turns (the
  audit row and the cursor's `since_clamped` count say so).
- **Enable** (owner steps, after Phase 9's node is paired): grant the
  per-directory reads for `~/.claude/projects` and `~/.codex/sessions` with
  Touch ID on the Mac, then `PATCH …/sources/<id> {"enabled": true}` for each
  source. The pull reaches the node through a port in
  `tools.transcript_ingest`. Phase 9's `nodes` layer is merged, but the
  `nodes`-backed port (and the move of the two owner-turn contracts into
  `nodes/capabilities/v1/`) is its own follow-up branch (8B brief, closeout
  owner steps). Until it lands the default port has no node, the pull is a
  no-op even when enabled, and preflight `transcripts` warns "no node port
  installed".
- **Check it.** Preflight `transcripts` (§3) reads the last pull of each
  enabled source from the audit rows: `ok` passes, `node_refused` warns,
  `failed` / `server_refused` fail; no node, no grant or no pull yet warn
  (the Swift readers and the Touch ID grant are the Mac session). The
  `/memory/ingestion` transcript panel shows the cursors, the
  `since_clamped` count with the last skipped window, and `tie_overflow`.

### 2b. Obsidian `00_Inbox/` (Phase 8B branch 7, opt-in)

The `obsidian_inbox` source imports notes you drop under
`<vault>/00_Inbox/<folder>/` **once**, inside the gated `vault_export` run
(nothing reads the vault outside an approved run), then moves each note to
`00_Inbox/_imported/<date>/`.

1. Enable `obsidian_inbox` (seeded disabled) and add a `vault_prefix` rule
   per folder: `POST …/sources/<id>/rules {"rule_type": "vault_prefix",
   "pattern": "00_Inbox/<folder>", "target_workspace": "<id>"}`. A note in a
   folder with no rule is left in place and counted `inbox_unmapped`.
2. Flag a sensitive note with frontmatter `sensitivity: private`: it becomes
   a `private` episode, extracted only by a local provider and mirrored only
   under `_private/`. Any value other than empty or `normal` is treated as
   `private` (fail closed). A normal Inbox note is **not** personal-class.
3. Approve a `vault_export` run. The approval card's summary counts the
   notes waiting and the unmapped ones (a dry pass that writes nothing).
4. Skipped and left in place: empty bodies, hidden folders, symlinks,
   anything deeper than one subfolder, a note with an unclosed `---`
   frontmatter; more than 200 notes wait for the next run.

## 3. Check it with preflight

```bash
make preflight            # or: uv run python manage.py preflight --no-model-calls
```

The learning lines (read-only, no model call):

| Check | `pass` | `warn` | `fail` |
|---|---|---|---|
| `learning_sources` | every enabled source is mapped, not paused; git has owner e-mails | all disabled; one paused/forgotten/unmapped; `MEMORY_OWNER_GIT_EMAILS` empty | a source is missing → run `seed_ingestion_sources` |
| `learning_beat` | the nightly, recovery, TTL, calendar, `self`, weekly reflection and transcript-pull tasks on the worker beat, the git producer and the Graphify recovery scan on the runner beat, all registered | — | a task missing from its beat or not registered |
| `learning_scrubber` | the corpus self-test, `transcripts.jsonl` included: 100% secrets recall, PII recall 100%, benign text unchanged | PII miss or a benign item altered (ids named); corpus not shipped here | any secret missed, or `transcripts.jsonl` missing or without a secret item (a release-gate regression: do not enable sources) |
| `learning_budget` | an active budget with a monthly cap covers every learning purpose (extraction, consolidation, fact reconciliation, reflection) and the eval answers (`learning_eval`) | no monthly cap | no budget → run `seed_ai_defaults`; a Phase 8B purpose missing from the settings |
| `learning_last_run` | the latest night's jobs succeeded and were consolidated | no run yet; deferred by a budget; pending retry; expired lease; awaiting consolidation | a job failed terminally (error type named) |

The Phase 8B lines (`check_graph`, read-only: no model call, nothing sent
to a node, no container started):

| Check | `pass` | `warn` | `fail` |
|---|---|---|---|
| `graph_integrity` | every fact's subject, object and fact share one workspace; every superseded approved fact has a `valid_to`; no window ends before it starts | — | any of the three (counts named, never a statement) |
| `retrieval_indexes` | an HNSW index per active embedding provider and table, the three `search` GIN indexes, the mode reported | no embedding provider; a provider above 2000 dimensions (exact scan); `hybrid` without a recorded passing private compare | an index missing or stale → `build_vector_indexes`; a GIN index missing → `migrate` |
| `graphify` | the image carries `org.friday.graphify.version` = the lock's pin; each `graphify_enabled` repo's last run succeeded | no repo enabled; a repo never ran or a run in flight; no `docker` here (run preflight on the runner host) | a failed last run (error type named); the image missing while a repo needs it, or built from another pin |
| `compressor` | off; or the sidecar `/healthz` answers telemetry off and offline and every enabled purpose has a recorded passing private compare | enabled with no purpose, or only bypass purposes | sidecar down or telemetry on; a purpose without a passing private compare |
| `transcripts` | off (both sources disabled); or the node is reachable, granted, and each source's last pull is `ok` | no node port installed yet; node not reachable; not granted; no pull yet; a source paused; last pull `node_refused` | last pull `failed` or `server_refused` |
| `eval_baseline` | a private baseline `MemoryEvalRun` is recorded (its metrics shown) | none yet (pending owner evaluation, `memory-eval.md`) | — |

Only ids, counts and kinds are printed — never episode text, a statement, a
path or a turn. The HUD health panel shows the failing check names only.

## 4. Reading the audit view

`/memory/ingestion` → a source (`GET /api/v1/memory/ingestion/sources/<id>`):

- **Counters** per day: stored, duplicate, dropped (unmapped / paused),
  scrubber hits by type (counts, never the matched text), tokens spent,
  proposals created / accepted / rejected / dropped (no provenance, caps…).
- **Last items**: the latest episodes with their workspace, kind, the scrubbed
  owner text and context text (metadata shows the git authorship). A commit you did not
  write showing as owner text means `MEMORY_OWNER_GIT_EMAILS` is too wide or
  an agent commit lacks its `Co-Authored-By` trailer (risk R2).
- **Unmapped keys**: hashed + shortened keys of dropped items (at most 20 a
  day) — add a rule if one of them should be learned.
- **Acceptance**: accepted / decided proposals — the go/no-go metric (§6).

## 5. The morning card

`/memory/learning` (and the HUD learning card) lists the pending learned
proposals of the last `MEMORY_LEARNING_DIGEST_DAYS` days, grouped by
workspace, each with the quote it was learned from. Per item: approve, edit
(memories) or reject. Only `work_log` items can be bulk-approved;
`self`/`developer`/`preferences` core-block proposals are per item, and a
core-block proposal built on an older block version is refused (re-read the
block). Unanswered proposals expire with no effect. Voice never approves a
proposal (ADR-015).

## 6. Forget and restore a source

`/memory/ingestion` → the source's forget action. The confirmation names the source and
the counts (episodes, memories, proposals). Forget (`POST …/sources/<id>/
forget`, confirm-class, reversible, ADR-009):

- pauses the source and moves its episodes to the trash;
- archives the approved memories derived from it (embedding cleared) and
  rejects its pending memories and core-block proposals, each marked with
  the operation id;
- hides its coding sessions.

Restore (`POST …/sources/<id>/restore`) puts back exactly the marked rows at
their prior status; the source **stays paused** until you resume it.
Already-approved core-block lines derived from the source stay in the block
(roll the block back from `/memory/core-blocks` if needed).

Permanent removal is the gate-class `purge_trash` tool (an approval request
with `episode_ids` / `forgotten_memory_ids`); episodes also expire on their
own after their TTL (30 days personal, 14 days firm) — the daily TTL beat
hard-deletes them, trashed or not.

## 7. Go/no-go after two weeks (owner)

After two weeks of real use, record in the brief's "Go/no-go record":
scrubber corpus 100% secrets recall (preflight `learning_scrubber`), zero
cross-workspace leaks (CI isolation tests), and the acceptance rate from the
audit view. Below 60% accepted: tune the prompts, the caps and the
owner-text rules before Phase 8B adds sources.

## 8. Facts: the temporal graph (Phase 8B)

Friday keeps **entities** (a repo, a service, a firm, a person by role, …)
and **facts** between them with a validity window (`valid_from` →
`valid_to`), per workspace, in Postgres (no graph database). Answers to
"since when?" come from the window; superseded facts stay as history.

- **Where facts come from.** Owner-text extraction proposes them (at most 6
  of the nightly 10 proposals; the quote must be your verbatim words and name
  the entity); the reconciliation pass proposes a supersession when a new
  fact replaces a current one; the weekly reflection (Sunday 03:30) proposes
  within the same caps; you add or edit them yourself in `/memory/graph`
  (born approved, your own words). Graphify facts about Friday's own code
  are `system` rows (§ below) and never take part in retrieval.
- **Review.** Fact proposals sit on the morning card next to memories, with
  the quote: approve, edit (an edit is a new version that supersedes the old
  one) or reject; never bulk. A supersede proposal shows the fact it would
  close; approving it sets the old fact's `valid_to` and `superseded_by`.
- **Sensitivity.** A fact inherits the sensitivity of the episode it cites.
  A `private` fact never reaches a model payload (memory block, hop
  expansion, `memory_graph_lookup`) or the open vault tree; an entity whose
  only live facts are private is a private entity.
- **Retrieval.** With `MEMORY_RETRIEVAL_MODE=hybrid` the chat context gets a
  fenced `<memory_fact>` block (statement + window) next to the memories,
  ranked by vector + full-text RRF after the workspace filter, with 1–2 hop
  expansion and recency decay. The default stays `legacy` until the owner's
  private compare passes (`memory-eval.md` §4, brief D8); preflight
  `retrieval_indexes` warns while `hybrid` runs without that record.
- **Forget and restore.** Forgetting a source (§6) also archives the
  approved facts derived from it, rejects its pending fact proposals and
  trashes entities left with no live fact; restore puts them back. Facts
  and entities are purged only through the gate-class `purge_trash`.
- **Graphify (self-knowledge).** Per repo, `graphify_enabled` (default off)
  runs the AST-only Graphify image on each new default-branch head, in a
  network-denied container on the runner host, and imports its graph as
  `origin=graphify` facts in the repo's workspace. Build the image first on
  the runner host (`docker build -f backend/harness/docker/graphify.Dockerfile
  -t friday-harness-graphify:0.9.103 backend/harness/docker`); preflight
  `graphify` checks its version label against the lock's pin.

## 9. Vault mirror v2: entity notes and conflict copies (Phase 8B)

The Obsidian vault stays a **mirror** (ADR-005): every write happens inside
an approved `vault_export` run, and Postgres wins.

- Each mirrored note carries `friday_format: 2` and a `content_sha256` of
  its body. Entities with an approved fact get a note under
  `03_Workspaces/<workspace id>/Entities/<entity id>.md` (current facts,
  then `## History`; links are `[[<entity id>|<name>]]`). Private entities
  and private facts go to the `_private/` tree only.
- **If you edit a mirrored note** in Obsidian, the next run notices the hash
  changed and copies your version to `_conflicts/<date>/` (or
  `_private/_conflicts/<date>/` for a private note) before rewriting it.
  Nothing you typed is lost, but it is **not** applied to Postgres: copy
  what you want into Friday (`/memory`, `/memory/graph`) yourself. The run's
  `work_log` notice names the folders; `/memory/graph` lists them.
- The first v2 run rewrites v1 files without conflict detection (they have
  no hash): move hand-edited v1 notes elsewhere before approving it.

## Troubleshooting

- **Nothing learned overnight:** `learning_last_run` — no run means no new
  owner-text episodes (check the counters: dropped unmapped? owner e-mails
  set?); `deferred` means a budget refused the call (check the spend in the
  audit view and the budgets in the admin).
- **A secret reached an episode:** forget the source, add the format to the
  scrubber corpus (`backend/memory/tests/scrubber_corpus/secrets.jsonl`, with
  the `<<>>` split marker) and fix the scrubber until the corpus test and
  `learning_scrubber` pass; then purge through `purge_trash`.
- **Calendar episodes never extracted:** no `personal_data_allowed` provider
  is active (jobs retry, then the episodes expire unlearned).
