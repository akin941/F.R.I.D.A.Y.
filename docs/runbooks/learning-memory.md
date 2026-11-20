# Runbook — Learning memory (Phase 8A)

Phase 8A closeout (brief `docs/prompts/phase-8a-learning-memory/brief.md`,
ADR-012). How the owner turns on continuous learning, routes what it reads
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

## 3. Check it with preflight

```bash
make preflight            # or: uv run python manage.py preflight --no-model-calls
```

The learning lines (read-only, no model call):

| Check | `pass` | `warn` | `fail` |
|---|---|---|---|
| `learning_sources` | every enabled source is mapped, not paused; git has owner e-mails | all disabled; one paused/forgotten/unmapped; `MEMORY_OWNER_GIT_EMAILS` empty | a source is missing → run `seed_ingestion_sources` |
| `learning_beat` | the nightly, recovery, TTL, calendar and `self` tasks on the worker beat, the git producer on the runner beat, all registered | — | a task missing from its beat or not registered |
| `learning_scrubber` | the corpus self-test: 100% secrets recall, PII recall 100%, benign text unchanged | PII miss or a benign item altered (ids named); corpus not shipped here | any secret missed (a release-gate regression: do not enable sources) |
| `learning_budget` | an active budget with a monthly cap covers both learning purposes | no monthly cap | no budget → run `seed_ai_defaults` |
| `learning_last_run` | the latest night's jobs succeeded and were consolidated | no run yet; deferred by a budget; pending retry; expired lease; awaiting consolidation | a job failed terminally (error type named) |

Only ids, counts and kinds are printed — never episode text.

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
