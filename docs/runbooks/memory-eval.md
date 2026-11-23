# Runbook: the memory eval set (private cases, baseline, compares)

Phase 8B branch 3 (brief `docs/prompts/phase-8b-transcripts-graph/brief.md`
D9). This is how the owner writes the **private** eval set, runs the
baseline, and records the numbers. The harness and a synthetic public seed
are in the repo (`backend/memory/evals/`). The private set is **never**
committed.

The eval set measures retrieval and answers. It gates two later decisions:
- flipping `MEMORY_RETRIEVAL_MODE` to `hybrid` (branch 4, decided at the
  closeout);
- enabling Headroom compression for a purpose (branch 6).

## 1. Where things live

| What | Where | Committed? |
|---|---|---|
| Harness, schema, metrics | `backend/memory/evals/` | yes |
| Public seed (about 60 synthetic cases) | `backend/memory/evals/cases/public/*.jsonl` | yes |
| **Private set** (about 100 real cases) | `MEMORY_EVAL_PRIVATE_DIR`, default `~/friday-evals/`, mode **0700** | **never** |
| Private result files | `~/friday-evals/results/<run id>.json`, mode 0600 | never |
| Public result files | `MEMORY_EVAL_RESULTS_DIR`, default `~/friday-evals-public-results/` (outside the private directory, which may hold only case files and `results/`) | never |
| Recorded metrics | `MemoryEvalRun` rows in the live database, in your personal workspace | n/a |

Two guards keep private cases out of the repo:
- a CI test fails if any file exists under `backend/memory/evals/cases/`
  outside `public/`;
- `.gitignore` ignores `friday-evals/`.

## 2. Writing private cases

Create the directory once:

```bash
mkdir -p ~/friday-evals && chmod 700 ~/friday-evals
```

The loader refuses the whole set in any of these cases:
- the directory is readable by group or others, is a symlink, or is not
  owned by you;
- it holds anything except `*.jsonl` case files and the `results/`
  directory, for example a `.txt`, a `.json`, a backup file, a symlink or
  another directory.

Each line of a `.jsonl` file is one case (format v1). Errors name the file
and line number, never the line's content.

```json
{"id": "work-001", "workspace_ref": "acme", "sensitivity": "private",
 "tags": ["temporal", "tr"],
 "setup": {
   "memories": [{"ref": "m1", "type": "semantic", "title": "Deploy",
                 "content": "Acme deploys run from the release-runner."}],
   "facts": [{"ref": "f-old", "subject": {"name": "acme-api", "kind": "service"},
              "predicate": "deployed_to", "object": {"name": "heroku", "kind": "tool"},
              "statement": "acme-api is deployed to Heroku",
              "valid_from": "2026-01-01", "valid_to": "2026-09-15", "superseded_by": "f-new"},
             {"ref": "f-new", "subject": {"name": "acme-api", "kind": "service"},
              "predicate": "deployed_to", "object": {"name": "hetzner", "kind": "tool"},
              "statement": "acme-api is deployed to Hetzner", "valid_from": "2026-09-15"}],
   "episodes": []},
 "question": "2026-10'da acme-api nereye deploy ediliyordu?",
 "expected": {"must_contain": ["hetzner"], "must_not_contain": ["heroku"],
              "relevant_ids": ["f-new"]}}
```

(Shown wrapped here. In the file a case is **one line**.)

The fields:
- `id`: a unique lowercase slug.
- `workspace_ref`: the workspace the question is asked in. `personal` is the
  eval owner's personal workspace; any other slug (`acme`) is a firm
  workspace. A setup row may carry its own `workspace_ref`, which is how you
  build a **leak trap**: a firm row next to a personal question, with the
  firm term in `must_not_contain`.
- `sensitivity`: `normal` (default) or `private`. See section 3.
- `tags`: free slugs. Two have a meaning:
  - `temporal` counts toward temporal correctness;
  - `compression` cases need a `history` with at least 2 `assistant`
    messages totalling at least 2000 characters (Headroom, branch 6).
- `setup.memories[]`: `ref`, `type` (`semantic`, `episodic`, `preference`,
  `procedural`, `work_log`), `title`, `content`, and optionally
  `sensitivity`, `workspace_ref` and `updated_at`.
- `setup.facts[]`:
  - required: `ref`, `subject` `{name, kind}`, `predicate` (a learned
    predicate), exactly one of `object` `{name, kind}` and `object_text`,
    `statement` and `valid_from`;
  - optional: `valid_to`, `superseded_by` (the ref of the newer fact with
    the same subject and predicate), `sensitivity`, `workspace_ref` and
    `quote`.
- `setup.episodes[]`: `ref`, `source` (`git`, `harness`,
  `claude_code_hooks`, `calendar`), `kind`, `occurred_at` and `owner_text`,
  plus optional `context_text`, `sensitivity` and `workspace_ref`. Episode
  text is scrubbed on load, like `record_episode`.
- `history[]`: optional prior turns, `{role: user|assistant, content}`, at
  most 12.
- `question`: what is asked.
- `expected`:
  - `must_contain`: key phrases the answer must contain;
  - `must_not_contain`: terms that must appear neither in the retrieved
    context (the leak rate) nor in the answer;
  - `relevant_ids`: the setup refs that should be retrieved (recall@10).

Dates are `YYYY-MM-DD` (local midnight) or an ISO datetime with an offset.
Matching is lenient on both sides: case-insensitive, Turkish-aware
(`İ`/`I`/`ı`), with diacritics dropped. "Hetzner'de" therefore matches
`hetzner`, and "çarşamba" matches `carsamba`. Unknown keys anywhere refuse
the case.

## 3. Flag firm and personal work data `private`

**Flag every case built from firm or personal work data
`sensitivity: private`.** For such a case:
- every setup row is stored `private`;
- the answer call runs with `personal_data=True`, so it goes only to a
  provider with `personal_data_allowed` (a local model);
- with only cloud providers configured, the case is counted `no_provider`:
  no call is made and it gets no answer metric.

Consequence (the chat rules, unchanged): `private` rows never enter a model
context (`cloud_eligible()` and `model_eligible()`). So a `private` case
measures leak behaviour and the "I do not know" answer, not recall.

To measure recall on real work data without sending it to a cloud model,
leave the case `normal` and configure only local chat and embedding
providers in the eval database. Alternatively, rewrite the case with
synthetic names. In a `normal` case, any single row may still be `private`;
it must then never show up (put its terms in `must_not_contain`).

## 4. Running the eval

The run subcommands work **only** under the eval settings. These use their
own database, `MEMORY_EVAL_DATABASE_NAME`, default `friday_eval`, on the
same server as `DATABASE_URL`. They never touch the live workspaces. Create
the database once:

```bash
createdb -h <db host> -U <db user> friday_eval   # or CREATE DATABASE friday_eval;
export DJANGO_SETTINGS_MODULE=config.settings.eval
cd backend
uv run python manage.py migrate
uv run python manage.py seed_ai_defaults   # eval prompt + learning_eval cap (5 USD/month)
```

Configure the eval database's providers in its admin, or with the same env
keys as the live stack. Then run:

```bash
uv run python manage.py memory_eval baseline --set private      # THE recorded baseline
uv run python manage.py memory_eval run --set private --retrieval-mode legacy
uv run python manage.py memory_eval compare --set private --retrieval-mode hybrid   # after branch 4
uv run python manage.py memory_eval baseline --set public --retrieval-only          # what CI runs
```

Each command prints a metrics table and the path of its result file. A
compare writes two files, `<id>-a.json` (legacy) and `<id>-b.json` (the
candidate). The verdict is on arm B:
- `pass` when B is at least A on recall@10 and answer accuracy, and leaks
  no more;
- `inconclusive` when recall is missing on either side;
- `fail` otherwise.

**The eval database persists.** Do not drop it between runs. Each case
clears only the previous case's setup rows, and `ModelUsage` is kept. That
is how the `learning_eval` monthly cap holds across runs. Even if the
database is recreated, the cap holds: before every run and every answer
call, the runner takes the larger of the database's eval spend and the
result files' `spend_usd` for the month. A run that would start over the
cap is refused with no call. A run that reaches the cap stops, counts the
rest `budget_stopped`, and still writes its partial result.

Only the answer calls (`memory.eval_answer`) are under the cap. The
embedding calls that rank setup memories (`memory.retrieval`) are not, and
they are cheap.

## 5. Recording the numbers

Record a result file into the **live** database. Use the normal settings,
not the eval ones, which `record` refuses:

```bash
unset DJANGO_SETTINGS_MODULE
uv run python manage.py memory_eval record ~/friday-evals/results/<run id>.json --owner you@example.com
```

The run is stored as a `MemoryEvalRun` in your personal workspace (no
other workspace is accepted). It holds counts, rates, enums and timestamps
only: no case text, ids, tags, questions or answers. A result file with any
key outside the D9 list is refused. Re-recording the same file is a no-op.

You can read the runs at `GET /api/v1/memory/evals/runs` (owner, web
session only). Copy the baseline numbers into the brief's "Go/no-go
record".

## 6. The two decisions the eval gates (Phase 8B closeout)

Both decisions are the owner's, on recorded **private** results. The public
seed is synthetic and only guards against regressions in CI.

**Retrieval default (brief D8).** Run the compare and record both arms:

```bash
export DJANGO_SETTINGS_MODULE=config.settings.eval
uv run python manage.py memory_eval compare --set private --retrieval hybrid
unset DJANGO_SETTINGS_MODULE
uv run python manage.py memory_eval record ~/friday-evals/results/<id>-a.json --owner you@example.com
uv run python manage.py memory_eval record ~/friday-evals/results/<id>-b.json --owner you@example.com
```

Flip `MEMORY_RETRIEVAL_MODE=hybrid` in `.env` (and restart web and worker)
only when arm B's verdict is `pass`. Otherwise keep `legacy`. The closeout
(2026-11-20) kept `legacy`: the public compare showed no regression, but
no private compare is recorded yet (pending owner evaluation, brief M2
item 2). Preflight `retrieval_indexes` warns while `hybrid` runs without a
recorded passing private compare.

**Headroom compression (brief D11).** Only `chat` is a candidate. Build and
start the sidecar first (`docker compose --profile compression up -d
compressor`), then:

```bash
export DJANGO_SETTINGS_MODULE=config.settings.eval
uv run python manage.py memory_eval compare --set private --compression chat
```

Record both arms as above. Set `AI_COMPRESSION_ENABLED=true` and
`AI_COMPRESSION_PURPOSES=chat` only on a `pass`: at most 1 point of answer
accuracy lost, no extra leak, and something was actually compressed (a
compare that compressed nothing is `inconclusive`). Preflight `compressor`
fails while an enabled purpose has no recorded passing private compare, or
while the sidecar's `/healthz` reports telemetry on.

## 7. Preflight

`manage.py preflight` reports `eval_baseline`: `pass` with the private
baseline's metrics once one is recorded, `warn` (pending owner evaluation)
until then. It never fails the run. Copy the numbers into the brief's
"Go/no-go record" (8B brief, M2 items 1–3).
