# Runbook — proactive runs and the morning digest (Phase 12)

Friday starts the conversation, but never acts on its own: owner-made
schedules run deterministic probes, turn what changed into notices, and once
a morning write one short spoken digest. Every run is **unattended**, and an
unattended run is denied every effect except writing its own rows (ADR-016
decision 1). Nothing here approves, sends, runs or creates anything.

Sources: `docs/prompts/phase-12-reach/brief.md` (D1–D8, D18–D20, "Go/no-go
record"), ADR-016 (proactive runs, schedules and instruction skills),
ADR-015 (the voice cap the spoken routes live inside),
`docs/runbooks/live-voice.md` (the Mac voice path), `docs/runbooks/n8n-connector.md`
and `docs/runbooks/skills.md` (the probes those features add).

## What runs where

| Piece | Where | What it may do |
|---|---|---|
| `proactive.tasks.heartbeat` | worker beat, every 60 s | claim due schedules (`next_run_at <= now`), advance `next_run_at` **before** running, run each inside `unattended_run` |
| Probes (`core.probes` registry, one module per owner app) | inside a run | deterministic DB reads; return counts, Friday-authored labels and opaque keys; no model, no network |
| A `heartbeat` run | inside a run | turn probe deltas into `ProactiveNotice` rows (Friday templates); **never** a model call |
| A `morning_digest` run | inside a run | the probes, then — only if something was found — one `proactive.digest` gateway call (`personal_data=True`, one `ModelUsage`), number-checked; template fallback |
| `proactive.tasks.prune` | worker beat, daily | hard-delete notices read/dismissed more than `PROACTIVE_NOTICE_RETENTION_DAYS` (30) ago |
| `/today`, the bell, `/notices`, the HUD | the owner's browser | read digests and notices; "Dinle" speaks through `POST /voice/speech` |
| `digest.today` / `notices.read` wake routes | a voice turn (web voice or `node_voice`) | answer with the stored digest text or the unread notice titles; no model call |

## 1. Turn it on (owner, over the web)

Schedules are rows only the owner's web views write (`proactive.services.
save_schedule` → `core.owner_web.require_owner_web`); no tool, intent,
skill, connector, model, voice turn or run can create or edit one ("cron
cannot create cron", ADR-016 decision 2).

1. Open `/today` → **Sabah brifingi**: time (e.g. 07:45, `TIME_ZONE`),
   weekdays, optionally **açılışta oku** (`speak_on_open`), enable. This is
   Phase 12 owner step 2; the two-week go/no-go starts that day.
2. Optionally enable the **heartbeat** (interval 15/30/60/120 min, quiet
   hours) — it only fills the bell with notices.
3. Check: `manage.py preflight` → `proactive_schedules` passes and lists
   `morning_digest@personal`.

The first heartbeat run for a schedule records the delta baseline and
notifies nothing; enabling it never floods the bell. Missed runs are not
caught up: after downtime only the next occurrence runs.

## 2. Pause, and an inactive owner

"Proaktif çalışmaları duraklat" on `/today` (`POST /api/v1/proactive/pause`,
`DELETE` to resume) skips every schedule of the workspace, audited once per
transition (`proactive.paused` / `.resumed`, `proactive.schedule.skipped`).
A deactivated owner skips too (`owner_inactive`). A run can neither pause nor
resume proactivity.

## 3. Reading a run

```bash
# manage.py shell
from proactive.models import ProactiveRun
for run in ProactiveRun.objects.order_by("-started_at")[:5]:
    print(run.kind, run.outcome, run.duration_ms, run.probe_counts,
          run.probe_failures, run.slow_probes, run.denials, run.model_usage_id)
```

- `quiet` — no probe found anything; nothing else was written.
- `notices` / `digest` — what the run produced.
- `denied` — something inside the run tried a D2 effect; one
  `proactive.unattended_denied` audit row per denial (`effect`, `run_id`,
  `step`). A denial from a probe is a bug in that probe: fix the probe, never
  allow the effect.
- `failed` — the run itself crashed; `proactive_last_run` fails until the next
  good run.

`DigestRun.composed_by=template` with `fallback_reason` tells why the model
text was not used (`no_provider`, `no_prompt`, `budget_exceeded`,
`model_error`, `empty`, `number_check_failed`). A number-check failure is the
safety net working: the model wrote a number that is not in the facts.

`manage.py proactive_heartbeat [--at ISO8601]` runs one tick by hand (due
schedules only; it creates no schedule).

## 4. Preflight (`manage.py preflight`, `check_proactive`)

| Row | Passes when | Warn / fail |
|---|---|---|
| `proactive_beat` | heartbeat every ≤ 60 s and the prune on the worker beat, both registered | fail: an entry missing or slower |
| `proactive_schedules` | every enabled schedule has a `next_run_at` not more than 10 min overdue | warn: none enabled (owner step); fail: overdue (the beat or worker is down) or incomplete |
| `proactive_last_run` | the last run of each enabled schedule is not `failed` | warn: no run yet, or `running` for > 15 min; fail: `failed` |
| `proactive_digest` | `proactive.digest` has a tier, an active budget and the seeded `friday_proactive_digest` prompt | fail: any missing; warn: no monthly cap |
| `proactive_probes` | probes are registered and none was slow or failed on the last runs | warn: slow (`PROACTIVE_PROBE_TIMEOUT_MS`) or failed probes, by name |

Details carry schedule kinds, workspace types, probe names and closed codes —
never a title, label, digest text or workspace name.

## 5. The go/no-go data

```bash
manage.py proactive_checkpoint          # per workspace: first enable, days enabled,
manage.py proactive_checkpoint --json   # every enable/disable, digests produced/opened/spoken
```

After two weeks the owner records the result in the brief's "Go/no-go
record" (Phase 12 owner step 7). If the digest was turned off, add one
sentence on why; the digest is re-scoped (sections, length, time) before
anything is added to it.

## 6. Adding a probe (review checklist)

- It lives in its owner app (`<app>/probes.py`) and registers a `ProbeSpec`
  at `ready()`; the boot check validates its name, section, notice kind and
  link (closed set).
- It reads through `for_actor()` for the one workspace it is given; labels
  come from owner/Friday fields only (`probes.safe_label`), never connector,
  tool or model text; `item_keys` are opaque ids.
- It writes nothing and calls no service with an effect. The D2 guard denies
  it anyway — but a denial ends the step and shows as `denied` in the run.
- A new service that writes business rows calls `deny_if_unattended(effect)`
  first (R5) and gets a row in `proactive/tests/test_unattended_guard.py`.

## 7. The e2e (CI)

`backend/ops/tests/test_reach_e2e.py`: the heartbeat → digest → `/today` ack
path, "günaydın" and "bildirimler" through the Phase 11 reference host and a
real `friday-voice run` child against this server over HTTP, n8n (async read
and gated write) ending in notices, SKILL.md upload → approve → used, three
fake harness jobs → playbook proposal, and one unattended run that tries
every D2 effect (all denied, one audit row each).

## Troubleshooting

- **No digest this morning.** `proactive_last_run`: `quiet` means no probe
  found anything (a quiet day produces nothing, by design). Otherwise check
  `proactive_schedules` (paused? overdue?) and the worker beat.
- **The digest is the template text.** See `fallback_reason`; with
  `no_provider`, add a `personal_data_allowed` (local) provider row.
- **The bell is empty after enabling the heartbeat.** Expected for items that
  were already there at the first run (the baseline); new items notify.
- **"Günaydın" on the Mac answers "Bugün için brifing yok".** No digest for
  today in that workspace; a firm digest never answers a personal session.
