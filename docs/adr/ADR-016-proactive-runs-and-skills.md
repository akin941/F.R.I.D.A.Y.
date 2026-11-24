# ADR-016: Proactive Runs, Schedules and Instruction Skills

## Status
Proposed 2026-11-20 — written in `feat/p12-proactive-core` (Phase 12
branch 1, `docs/prompts/phase-12-reach/brief.md` D1–D5, D13–D17, D19).
Becomes Accepted on the owner's review of branch 1 (Phase 12 owner step 1).
Builds on ADR-001 (workspace axis), ADR-003 and its 2026-11-20 amendment
(single gateway door, gateway-enforced budgets), ADR-004 and ADR-008
decision 3 (gated actions are decided by the owner over the web),
ADR-005/ADR-012 (learned content is proposed, never auto-promoted) and
ADR-013 decision 6 (owner away = deny, for the node). Amends nothing.

## Context
Until Phase 12 every action Friday took started from something the owner
did: a chat turn, a card click, a web decision. The fixed system tasks on
the beat (recovery scans, the 8A nightly learning run, the 8B weekly
reflection, the connector pin beat) are reviewed code paths that do not
consult owner data to decide what to do.

Phase 12 adds actors that run while the owner is away: owner schedules
(the morning digest, a heartbeat that turns changes into notices), a skill
curator and a playbook distiller. It also adds instruction skills —
owner-approved text injected into the model's instructions. The external
lessons are consistent: a scheduler an agent can extend becomes a
persistence mechanism for whatever reaches it (OpenClaw); cron jobs should
be script-gated so they wake the model only when there is work (Hermes);
skill registries and "allow always" wildcards turn instructions into a
supply chain (ClawHub, agentskills.io).

## Decision
1. **Unattended runs are deny-by-default, enforced at each effect's owning
   service.** Every proactive run, the curator and the distiller execute
   inside `core.unattended.unattended_run`. While it is open, each effect's
   owning service refuses on its first line (`deny_if_unattended`):
   approval requests and decisions, read-tool execution
   (`tools.registry.run_read_tool`), connector reads and writes, task,
   memory and fact writes, node invocations and harness jobs. The gateway
   refuses every purpose outside the run's allowlist (`proactive.digest`
   for the morning digest; none for the heartbeat), and Celery dispatch
   outside the proactive tasks is refused at publish (`before_task_publish`)
   and, for eager execution, at `task_prerun`. A refusal is audited
   (`proactive.unattended_denied`: effect, run id, step; no content) through
   a **required** `on_denied` recorder that buffers in memory and is flushed
   after each step's own transaction has ended, so a rolled-back step cannot
   take its denial row with it. The refusal (`UnattendedDenied`) is a
   `BaseException`, like `asyncio.CancelledError`: best-effort `except
   Exception` wrappers in services, and Celery's own signal dispatch (which
   swallows `Exception` from handlers), cannot swallow it. There is **no
   owner-switchable allow list** of unattended actions; a future one needs
   a new ADR and threat model.
2. **Schedules are owner-created over the web only, from a closed set of
   kinds, and nothing a run, tool, skill, model or connector can do creates
   or edits one ("cron cannot create cron").** Kinds are `morning_digest`
   and `heartbeat`, at most one row per workspace and kind. The only writer
   is `proactive.services.save_schedule`, which requires the workspace
   owner, a person, over the web channel (`core.owner_web`); the model's
   `save()` and the pause row's `save()`/`delete()` raise inside an
   unattended context. The beat stays code: one static
   `proactive.tasks.heartbeat` entry reads schedule rows, claims each due
   row with `skip_locked`, advances `next_run_at` before running (a crash
   skips one run, never repeats it) and never catches up missed runs.
   Import contracts keep every app but `assistant`, `hud` and `ops` from
   importing `proactive`, and `assistant`/`hud` reach only `proactive.api`.
   A per-workspace pause (owner over the web) and an inactive owner skip
   every schedule.
3. **Script gate.** A run first collects deterministic probes (DB reads,
   registered in `core.probes` by the app that owns the data, one workspace
   per call, labels from Friday templates and owner-authored fields only,
   links from a closed set). It calls a model only when a probe found
   something, only for the `morning_digest` kind, and only for an
   allowlisted purpose; a heartbeat never calls a model and turns probe
   deltas into notices with Friday-authored templates. A run writes only
   its own rows (run, digest, notices, probe state) plus audit. Each probe
   runs in its own transaction under a Postgres `statement_timeout`
   (`PROACTIVE_PROBE_STATEMENT_TIMEOUT_MS`, default 10 s): a hung probe
   query is cancelled and recorded as `probe_timeout`, and the run goes
   on; `PROACTIVE_PROBE_TIMEOUT_MS` only flags a slow probe. Python time
   spent outside SQL is not bounded (probes are DB reads by contract).
4. **Instruction skills are text that grants no capability.** A `SKILL.md`
   skill changes what the model is told, never which tools exist, which
   channel may read, which class an action has or who decides. Every
   version is staged (`proposed`) and approved by the owner per version and
   content hash over the web; there is no bulk approval and no LLM
   approval. Permission-shaped front-matter keys (`allowed-tools`, `tools`,
   `permissions`, `allow`, `auto_approve`, `trust`) are refused; bundles
   carry Markdown only and nothing from a skill is ever executed on the
   host. There is no public registry, no URL import and no outbound HTTP
   from the `skills` app. Skills are archived, never deleted.
5. **Playbooks are proposals with code-checked provenance.** A playbook
   distilled from harness runs is a skill proposal whose every step is
   traced, in code, to owner- or Friday-recorded fields of succeeded,
   reviewed jobs (the owner's goal text, the repo's test command, changed
   paths); text quoted from model output never survives the check. The
   owner decides each proposal like any other skill version.

## Consequences
- A probe or a future service that writes business rows must call
  `deny_if_unattended` on its first line; the guard test walks every
  registered `ToolSpec` and `ReadToolSpec` and every row of the effect
  table, and a review checklist line covers new services (brief R5).
- The existing fixed system tasks (8A nightly learning, 8B reflection,
  recovery scans, pin beat) are not unattended runs in this sense and keep
  running unchanged.
- The owner cannot ask Friday to "run X every night"; an automation that
  must run unattended lives in the owner's own n8n, and Friday only
  announces its results (Phase 12 Q3).
- A heartbeat's cost is DB reads only; the morning digest makes at most one
  cheap, local, number-checked call per day, with a template fallback.

## Alternatives considered
- **A convention ("proactive code does not call X").** Fails the first time
  a probe author reuses a service; rejected for guards at the effect.
- **`proactive` importing each app's selectors.** Makes `proactive` a hub
  that the harness leaf rule forbids and that reaches `connectors` against
  ADR-010 decision 1; rejected for the `core.probes` registry.
- **An owner-switchable unattended allow list.** Out of scope; it needs its
  own threat model (Phase 12 Q3).
- **Skills as rows in MVP 8's plan catalog.** The plan catalog names tools
  and steps (capability); merging would let a row name a tool step
  (Phase 12 Q4).

## Implementation note — Phase 12 closeout (`chore/p12-closeout`, 2026-11-20)

Decisions 1–5 landed in branches 1–7 and are now visible in operations:
- `manage.py preflight` adds three read-only groups (no model call, no run
  started, nothing written). `check_proactive`: `proactive_beat` (the
  heartbeat every ≤ 60 s and the notice prune on the worker beat — the only
  proactive beat literals), `proactive_schedules` (every enabled schedule
  has a `next_run_at` at most 10 min overdue; a `warn` while none is enabled,
  owner step), `proactive_last_run` (no schedule's last run `failed`),
  `proactive_digest` (tier, budget and seeded prompt of the one allowlisted
  digest purpose) and `proactive_probes` (none slow or failing; a denial is
  not counted, it is the guard working). `check_skills`: `skills_ai` (the
  three skills purposes seeded), `skills_beat` (curator and distiller
  scheduled) and `skills_parser` (every usable skill's current version
  passes the current parser — decision 4's strictness applies to old
  versions too, by name and closed code).
- The closeout e2e (`backend/ops/tests/test_reach_e2e.py`) runs one
  heartbeat whose probes try every D2 effect of the branch-1 table plus the
  decision-2 writes (a schedule through its service and through `save()`,
  the pause, a wake alias, a skill proposal, a digest ack): every one is
  denied in its owning service, one `proactive.unattended_denied` row each,
  and nothing is created. The same file drives the digest from the owner's
  web schedule through the heartbeat to `/today`, a SKILL.md that is never
  offered to a turn until the owner approves its hash, and three fake
  harness jobs to one provenance-checked playbook proposal (a step quoted
  from nowhere the owner wrote is dropped in code).

Status stays **Proposed** until the owner's review (Phase 12 owner step 1);
no decision changes.
