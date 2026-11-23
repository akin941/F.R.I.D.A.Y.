# Agent swarm — how Friday is built (paused 2026-11-21)

Friday is developed by an **agent swarm**, run with the Claude Code Workflow tool
from a cloud session. This folder keeps the swarm durable across sessions.
**Keep this discipline when you continue.**

## Discipline (unchanged; see CLAUDE.md)

1. **One brief per phase**, at `docs/prompts/phase-*/brief.md`.
   - It is written at phase start, grounded in the real code.
   - It is reviewed by an independent Sonnet reviewer until APPROVE.
   - It is merged before any code.
2. **One worktree agent per branch**, from the brief's branch table, in
   dependency order.
   - It implements the branch, writes tests (role × endpoint) and makes the gates green.
   - It commits early and often.
3. **Independent review per branch.**
   - A Sonnet reviewer never edits code.
   - Up to 3 rounds of REVISE → fix, then a final check.
   - Only LOW findings may remain. They are recorded in the brief as follow-ups.
4. **Serialized merge.**
   - A single merger merges each branch into the integration branch.
   - It runs the full gates: ruff, format, lint-imports, `makemigrations --check`,
     full pytest, and frontend lint/typecheck/test.
   - It pushes only when everything is green.
5. **Linux/Mac split.**
   - Server-side work happens in the cloud. It is tested against a Python reference node.
   - Swift `friday-node`, Touch ID, Keychain, TCC, AppleScript/AX, notarization and
     real-mic work go to each brief's "Mac session (deferred)" section.
   - The final `docs/runbooks/mac-session-handoff.md` is for the MacBook session.
6. **Owner steps** go under a dated "Owner steps (pending)" list in each
   brief. Go/no-go checkpoints that need real data are recorded as
   "pending owner evaluation" and do not block the next phase.

## Files

| File | What it is |
|---|---|
| `swarm-1-p5-p6-p8a.js` | First run (finished): docs, Phase 5, Phase 6, Phase 8A start |
| `swarm-2-p9-p12.js` | Second run (stopped 2026-11-21 for the pause) |
| `swarm-resume.js` | **Use this to continue.** It is idempotent: it skips merged branches, skips briefs that are already full, and sends implemented-but-unmerged branches straight to review |
| `wip-2026-11-21.bundle` | Unmerged local branches saved before the pause (see below) |
| `history-scrub-rules.txt` | `git filter-repo --replace-text` rules for the move to `akin941/F.R.I.D.A.Y.` |

The rules file and the bundle must be removed from history during the rewrite: add
`--path .swarm/history-scrub-rules.txt --path .swarm/wip-2026-11-21.bundle --invert-paths`
in a **separate** filter-repo pass.

## Status at the pause (2026-11-21)

Merged and pushed on `claude/inspiring-franklin-pw7avo`:

- **Phases 5 and 6:** complete.
- **Phase 8A:** all 6 branches.
- **Phase 8B:** branches 1–5 and 7–9.
- **Phase 9:** branches 1–2 (node pairing, node protocol).
- **Phase 10:** branch 1 (protocol v2), branch 2 (reference executor),
  branch 3 (invocation grants) and branch 5 (HUD terminal).

Not done yet:

- **Phase 8B:**
  - 6 `feat/p8-headroom-stage`: implemented and reviewed. The merge ran out of time
    during the full pytest. It is in the bundle, with `merge-wip/p8-headroom-stage`.
  - 10 `chore/p8b-closeout`.
- **Phase 9:** branches 3–8, from `feat/p9-reference-node` to `chore/p9-closeout`. Not started.
- **Phase 10:**
  - 4 `feat/p10-trust-check`: APPROVE. The merge ran out of time during the full
    pytest. It is in the bundle, with `merge-attempt/p10-trust-check`.
  - 6 `feat/p10-codex-app-server`: implemented, not yet reviewed. It is in the bundle.
  - 7 `feat/p10-claude-agent-sdk` and 8 `chore/p10-closeout`.
- **Phases 11 and 12:** briefs and branches not started.
- **Final step:** `docs/mac-session-handoff`.

Known issues the swarm must fix first:

- **Merger time limit.** Mergers returned before the full pytest (about 20 min)
  finished, because foreground Bash stops at 10 minutes. `swarm-resume.js` now tells
  mergers to run pytest in the background and wait for it.
- **Timezone bug in a test — RESOLVED** by `fix/tz-fact-learning-test`
  (status: implemented, 2026-11-20).
  - Root cause, in the test: the `episode()` fixture defaults to `now - 1h`.
    In the measured window 21:00–22:00 UTC (00:00–01:00 Istanbul) that falls
    on the previous local day. The test anchored its expectation on today's
    local date, but the clamp is ±1 day around the episode's local day (D4).
    That production rule was right. The test now anchors on the episode's
    local day under a frozen clock at 12:00, 21:30 and 22:30 UTC, plus a
    cross-UTC-midnight case. The new root fixture `frozen_now` pins
    `timezone.now`.
  - Siblings found by the sweep were production bugs. Code called
    `value.date()` on a UTC-aware row read back from Postgres. A fact whose
    `valid_from` came from a date is stored at local midnight (21:00 UTC the
    day before), so it rendered one day early at any hour. This happened in
    three places: `render_fact_line` (chat context and the graph read tool),
    the reconciliation payload's `existing_fact valid_from`, and the vault
    entity notes. All three now use `timezone.localdate()`, with regressions
    at 12:00/22:30 UTC.
  - No `timezone.now().date()` / `date.today()` remains server-side. The one
    `date.today()` is the CLI's intentional `client_local_date`.
  - Owner steps (pending), recorded 2026-11-20: none.

## How to continue (Monday)

In a cloud session on this repo:

1. Restore the unmerged branches. A fresh container only has what was pushed.

```bash
git fetch .swarm/wip-2026-11-21.bundle 'refs/heads/*:refs/heads/*'
```

2. Make sure Postgres 16 + pgvector and Redis run locally. The database and user are
   both `jarvis`/`jarvis`.

```bash
apt-get install -y postgresql-16-pgvector
```

3. Tell Claude: *"`.swarm/README.md` oku, aynı disiplinle swarm'ı
   `.swarm/swarm-resume.js` ile devam ettir."* It runs
   `Workflow({scriptPath: ".swarm/swarm-resume.js"})` after fixing the timezone
   test.
4. When the swarm finishes, do the history rewrite with the rules file and push to
   `akin941/F.R.I.D.A.Y.`. The steps are in this file and in
   `docs/runbooks/rename-jarvis-to-friday.md`.
