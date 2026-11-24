# Phase 12 — Reach and proactivity (server side + Mac session)

> Status: **Draft (2026-11-20). Written on `docs/p12-brief` from the stub of
> 2026-11-20, which was taken from the owner-approved Friday 3.0 roadmap.
> It needs its independent Sonnet 5 plan review to reach APPROVE before
> branch 1, because the implementing model never reviews its own brief.**
> Sources: `docs/research/2026-11-roadmap-proposal.md` §B7 (integration
> strategy), §a (scenario 7, the morning card), §c.2 (runaway guards),
> §c.4 (owner away), §c.5 (observability), §c.6 (testing), §c.8
> (supply-chain controls), §d row 8; `docs/research/2026-11-landscape.md`
> §1 (OpenClaw/Hermes: skills, curator, script-gated cron, wake routing),
> §2 (n8n MCP Server Trigger, instance-level MCP, async voice lesson), the
> OpenJarvis row (morning digest); MASTER_PLAN §AA "Friday 3.0 phases"
> (order 8).
> Parent decisions:
> - ADR-001: workspace axis, `for_actor()`.
> - ADR-002: modular monolith; this phase adds one kernel app
>   (`proactive`) and its import contracts (D19).
> - ADR-003 and its 2026-11-20 amendment: single gateway door, one
>   `ModelUsage` per call, gateway-enforced budgets; every new purpose
>   gets a tier, a budget row and a seeded prompt (D18).
> - ADR-004 and **ADR-008 decision 3**: every write, delete, deploy or
>   external send is an `ApprovalRequest` decided by the owner over the
>   web; machine actors and low-trust channels never decide. This phase
>   adds actors that run while the owner is away (schedules), so it states
>   the "unattended = deny" rule as an ADR (D2, ADR-016).
> - ADR-005 / ADR-012: learned content is proposed, never auto-promoted;
>   provenance is checked in code; content from external tools or MCP is
>   never auto-promoted (ADR-012 decision 9). Skills and playbooks follow
>   the same discipline (D14, D17).
> - **ADR-010** (connector framework, MCP client only): manifests in git,
>   pinned schemas, sidecars holding their own credential, reads ungated
>   and fenced, writes gate-class and at most once. **Amended by this
>   phase** (branch 4): the bridge's HTTP relay mode (D9) and async reads
>   (D11).
> - ADR-013 decision 6 (owner away = deny; nothing queued to run later)
>   and the frozen server→node message set (Phase 10 D1). Phase 12 adds no
>   node protocol surface at all.
> - **ADR-015** (voice-originated turns): a voice turn reads and proposes
>   only. Wake-phrase routes (D8) live inside that cap; an ADR-015
>   implementation note records it (branch 3).
> - `docs/security/mcp-governance.md`: allowlist + manifest + governance
>   twin before any runtime connector (D9, D10).
>
> **Stub decisions kept.** The stub's goal ("Friday starts useful
> conversations and uses the owner's automations: a spoken morning digest,
> allowlisted n8n workflows, curated skills and playbooks learned from
> harness runs — with unattended actions denied by default"), its scope
> list (n8n through the Phase 5 connector framework with narrow MCP Server
> Trigger workflows, a Friday-side workflow-ID allowlist, gated writes and
> async voice turns; script-gated heartbeat/cron where cron cannot create
> cron and unattended means deny by default; a spoken morning digest
> including the 8A learning card; SKILL.md skills with staged approval and
> a curator — no public skill registries, no "allow always" wildcards, no
> LLM auto-approval, no host execution by default; procedural playbooks
> distilled from harness runs as proposals; wake-phrase routing), its
> parent decisions and its go/no-go checkpoint ("the owner keeps the
> morning digest on after two weeks") are kept unchanged. The stub
> recorded no owner steps; the ones this phase needs are listed under
> "Owner steps (pending)".
>
> **Platform boundary (owner decision 2026-11-20).** The implementation
> container is Linux, with no macOS and no Xcode. Phase 12 is almost
> entirely server work:
> - **In scope now, with full branches (1–7 and the closeout):** the
>   `proactive` app (probes, unattended guard, schedules, notices), the
>   morning digest and its web/HUD surfaces including the "Dinle" playback
>   through the existing speech endpoint, wake-phrase routing for web voice
>   and `node_voice` turns, the n8n connector (manifest, relay sidecar
>   mode, async reads, gated writes) tested against the SDK's in-memory
>   MCP server, SKILL.md skills with the curator, harness playbooks, and
>   the preflight checks. The Mac path is exercised now through the
>   Phase 11 reference host and the real `friday-voice` child against a
>   live test server (closeout e2e), so the spoken route on the Mac is
>   covered by a contract test before any Mac exists.
> - **Not built here:** nothing in Phase 12 needs new Swift code. What
>   needs the Mac is real-world verification: the spoken digest and the
>   wake routes through the real microphone, Whisper and TTS on the
>   owner's MacBook, with barge-in during a long digest (Mac session M1).
>   That is listed under "Mac session (deferred)" and marked "deferred:
>   Mac session" in the branch table. It must pass the server-side contract
>   tests that branches 3 and 8 commit now, and it must leave
>   `node/friday-voice/conformance.lock` and the node conformance suite
>   unchanged (Phase 12 adds no protocol surface).
>
> Go/no-go items that need the Mac or real-world owner data are recorded
> as **pending owner evaluation**. Phase 12 is the last scheduled phase;
> its pending rows do not block the backlog (Phase 7 Workshop) or the
> "later" list.
>
> **Precondition.**
> - Phase 11 closeout merged on the integration branch (it is:
>   `chore/p11-closeout`, 2026-11-20). Phase 12 uses the `node_voice`
>   channel and live sessions (Phase 11 D3–D5), the reference host and the
>   live-voice e2e harness (Phase 11 closeout), the HUD node line, the 8A
>   learning card and the Phase 5 connector framework.
> - The Mac half of Phases 9–11 is pending owner evaluation. Phase 12's
>   server branches do not depend on it; M1 needs Phase 11 M1 (the Swift
>   voice host).
>
> **Branches.** Phase 12 branches are cut from the integration branch and
> use `feat/p12-…` names, under the MASTER_PLAN stable-numbering rule.

## Goal / value

Friday starts useful conversations and uses the owner's automations: a
spoken morning digest, allowlisted n8n workflows, curated skills and
playbooks learned from harness runs — with unattended actions denied by
default.

Concretely, at the end of the phase:

1. **07:45, weekdays.** The owner's `morning_digest` schedule (made by the
   owner on `/today`, never by Friday) fires. Code-defined probes collect
   what changed: two approvals waiting, three tasks due today, the first
   calendar item at 10:00 (from 8A's calendar episodes, no live read), an
   `auth-refactor` Claude Code session waiting for permission since last
   night, the 8A learning card with six proposals, the audio budget at
   40 %. Because there is something to say, one gateway call (purpose
   `proactive.digest`, local provider, one `ModelUsage`) writes a short
   Turkish spoken summary; a code check refuses any number in it that is
   not in the probe facts. Nothing is approved, sent, run or created: the
   run may only write its own digest and notices.
2. **"Dinle".** On `/today` and the HUD the digest card has a play button;
   the browser speaks it sentence by sentence through the existing
   `POST /api/v1/voice/speech` (ADR-011, audio budget). On the Mac,
   **"Hey Friday, günaydın"** is a wake-phrase route: the server answers the
   live voice turn with the stored digest text, no intent call, and the
   Phase 11 pipeline speaks it. "Dur" interrupts it like any reply.
3. **"Hey Friday, ev yedeğini başlat".** An owner alias routes to the
   allowlisted n8n workflow `wf_…` (a write). The voice turn produces a
   draft card only (ADR-015); the owner opens it on screen, approves the
   exact arguments on `/approvals`, the write runs once through the relay
   sidecar, and the result arrives as a notice ("n8n: Ev yedeği bitti").
   A slow read workflow ("sunucu durumu") is an **async read**: the turn
   answers "Başlattım, sonucu bu konuşmaya yazacağım" and a follow-up
   message with the fenced result appears when n8n returns.
4. **Skills.** The owner uploads a `SKILL.md` ("PR açıklaması nasıl
   yazılır"); it stays `proposed` until the owner approves that exact
   version over the web. Approved skills are offered to the chat turn by
   a cheap matcher and injected as owner-approved instructions; they grant
   no tool, no permission, no host execution. Weekly, the curator marks
   unused skills `stale` and proposes merges or archives; the owner
   decides each one.
5. **Playbooks.** After three successful, reviewed harness implementation
   jobs on the same repo, Friday proposes a playbook skill ("acme-api:
   migration ekle — testleri `make test` ile çalıştır, `backend/*/
   migrations/` dosyalarına dokun"), each step traced to a job id. The
   owner approves it; later implementation requests can attach it, and
   the runner mounts it read-only into the job's worktree.

## What already exists (verified 2026-11-20 on the integration branch)

- **Beat schedule is code.** `backend/config/celery.py` `_BEAT_SCHEDULE`:
  every periodic task is a literal entry (recovery scans every 60 s, the
  15-minute connector pin beat, the 8A nightly learning run
  `memory.ingestion.tasks.run_learning_night_task`, the 8B weekly
  reflection at `crontab(minute=30, hour=3, day_of_week=0)` in
  `TIME_ZONE` Europe/Istanbul); `build_beat_schedule(runner_mode=…)`
  splits worker and runner beats by task prefix; the compose worker runs
  embedded beat (`-B`, one worker). `friday_beat_schedules` exposes both
  for `manage.py preflight`. **No owner-editable schedule exists** and no
  task, tool or model writes the beat schedule.
- **8A learning card.** `memory/ingestion/digest.py`: `build_digest`,
  `decide`, `bulk_approve`, gated by `require_owner_web` (web channel,
  owner, never an agent); `window_start(days)` with
  `MEMORY_LEARNING_DIGEST_DAYS`; served by `memory/learning_views.py`
  (`LearningDigestView`) at `/memory/learning`. The 8A brief (Open
  questions) defers "a spoken morning digest" to Phase 12.
- **Read models a digest can use without a live read.** `tasks.Task`
  (`due_date`, status), `approvals.ApprovalRequest` (pending, expiry
  sweep every minute), `memory.CodingSession` (hook-fed sessions, last
  notification ≤ 200 chars fenced as untrusted), `memory.Episode` rows of
  the calendar-metadata source (`connectors.tasks.ingest_calendar_metadata`
  every 6 h), `harness.HarnessImplementationJob.Status`
  (`pending_gate_a`, …), `nodes` health (`nodes.selectors.node_health`),
  `ai_core` budgets (`ai_core/budgets.py` `budgets_for`, `find_refusal`).
- **HUD.** `hud/services.py` `SECTIONS = ("approvals", "tasks", "cost",
  "health", "terminal")`, read-only, `for_actor()` per stream workspace,
  no content; the hud contract allows only `core`, `workspaces`,
  `ai_core`, `approvals`, `tasks`, `nodes`.
- **Voice.** `POST /api/v1/voice/speech` (one sentence ≤
  `VOICE_TTS_MAX_CHARS`, language-routed, `voice.tts` purpose, audio
  budget); `frontend/src/lib/voice/segmenter.ts` (sentence chunking with
  the Turkish abbreviation list, Markdown-to-speech) and
  `self-trigger.ts` (Turkish lower-casing, NFC, punctuation and wake
  phrase stripped). The chat turn (`assistant/views.py`
  `ConversationMessageListView.post`) applies
  `apply_voice_capability_cap` (`assistant/services.py`) to every
  `input_mode=voice` turn; `node_voice` forces `input_mode=voice`
  (Phase 11 D4). The Phase 11 child (`node/friday-voice`) posts
  transcripts to the chat turn and speaks the streamed reply; it calls
  nothing else but the transcription, speech, sessions and voice-policy
  endpoints. The reference host (`friday_voice.reference.host`) and the
  live-voice e2e (`backend/ops/tests/test_live_voice_e2e.py`) run the real
  child against a live test server.
- **Router.** `Message.Intent` = `chat`, `memory_query`,
  `knowledge_query`, `task_draft`, `tool_request`, `connector_read`;
  `classify_intent` (purpose `router.intent`), `router.connector_args`,
  `router.task_draft`, `router.node_action_args`; at most one connector
  read per turn; drafts as `draft` SSE events, a card click is the only
  path to a task or an `ApprovalRequest`.
- **Connector framework (Phase 5, ADR-010).** `connectors/manifest.py`
  (`ConnectorManifest`, `ToolPolicy` with `schema_sha256`, `scope_check`,
  `renderer`, `precondition`, `gate_name()` defaulting to
  `mcp_<connector>_<tool>`, `validate_manifest`), `manifests/__init__.py`
  `BUILTIN = ("external_installation", "github", "google",
  "home_assistant")`, `reads.py` (`perform_read`, `connector.read` audit,
  `CONNECTOR_RESULT_MAX_CHARS`), `writes.py` (request → decision →
  `ConnectorWriteExecution` → `run_connector_write`, at most once,
  `unknown_outcome`, work_log), `mcp_client.py` (Streamable HTTP only, no
  redirects, no client capabilities, `_connect_http` test seam),
  `docker/stdio_bridge.py` (sidecar-side `BRIDGE_ALLOWED_TOOLS`,
  `BRIDGE_ENV_PASSTHROUGH`), the compose `connectors` profile and the
  `connectors` / `connectors-egress` networks. Tests talk to the SDK's own
  in-memory server (`connectors/tests/fakes.py`: `FakeServerState`,
  `build_server`, `asgi_connector`, `make_manifest`).
  `tools.registry.READ_CHANNELS`: personal reads over `{web,
  node_voice}`, firm over `{web, cli, node_voice}` (Phase 11 amendment).
- **Skills today (MVP 8).** `skills/catalog.py`: a code-only dict of
  `SkillSpec` plan templates (`read`, `tool_request`, `task_draft` steps)
  registered at boot (`skills/apps.py`, `note_to_task`,
  `weekly_export`), tool references validated at boot; `agents` picks one
  through `agent.skill_match` (`agents/skill_match.py`). No model, no
  migration, no `SKILL.md` support, no UI. `docs/skills/README.md` is a
  three-line note.
- **Harness outcomes.** `harness/ingestion.py` pushes implementation
  outcomes into `memory.ingestion.ports.record_episode` (goal as owner
  text when the owner typed it; briefing/diff/review as context text ≤
  1000 chars). `HarnessImplementationJob` keeps `repo_test_command`,
  `changed_files`, `diff_summary`, `test_result`, `produced_commit_sha`,
  status and the review link; `HarnessReview.Verdict`. `harness` is a leaf
  (no app imports it) and may import every app but `deploys`.
- **Gateway purposes.** `config/settings/base.py` `AI_PURPOSE_TIERS`,
  `AI_UNTIERED_PURPOSES` (a CI test fails on an unmapped purpose
  literal), per-family budget settings (`AI_LEARNING_PURPOSES`,
  `AI_CONTROL_PURPOSES`, …) seeded by `seed_ai_defaults`;
  `ai_core/compression.py` `COMPRESSION_BYPASS`.
- **Channels.** `core/channels.py`: `CHANNEL_SYSTEM` exists for
  machine-originated actions and is never trusted for a decision.
- **Preflight.** `ops/preflight.py` `CHECKS` (web … `live_voice`),
  `beat_schedules()`.
- **Not present.** No `proactive` app, no notices, no digest, no
  owner-editable schedule, no wake-phrase routing (Phase 11 "Out of
  scope"), no n8n anything in code (`grep -ri n8n backend frontend/src`
  is empty), no async connector read, no SKILL.md parser, no curator, no
  playbook.

## Boundary vs existing systems

- **Proactive runs are readers and writers of their own rows only.** A
  run may read through probes, make the one `proactive.digest` gateway
  call and write `DigestRun`, `ProactiveNotice` and `ProactiveRun` rows
  plus audit. It creates no task, memory, approval request, connector
  call, node invocation or harness job; the guard (D2) refuses each one
  in the owning service, not by convention.
- **The beat stays code.** The one new beat entry
  (`proactive.tasks.heartbeat`, every 60 s) is a literal in
  `config/celery.py`. Owner schedules are rows the heartbeat reads; no
  code path outside the owner's web views writes them (D3).
- **Voice stays a transport, routes stay inside the cap.** A wake-phrase
  route replaces the intent classification of a voice turn with a
  deterministic match; it can select a read or a proposal, never more,
  and the ADR-015 cap still runs after it (D8).
- **No new node protocol surface.** No server→node type, no `friday-voice/v1`
  change, no new endpoint for the `node_voice` token: the Mac reaches the
  digest through the chat turn it already has (D7).
- **n8n is a connector, not a brain.** It is one manifest in the Phase 5
  framework. Friday never uses n8n's instance-level MCP (workflow
  builder, publish, create), never lets n8n call Friday, and n8n's own AI
  agents are invisible to Friday (D9).
- **Skills are text, not capability.** An approved skill changes what the
  model is told, never which tools exist, which channel may read, which
  class an action has or who decides. MVP 8's code-only plan catalog
  (`skills.catalog`) is untouched (D13).
- **Workspace axis.** Schedules, digests, notices, skills, playbook
  sources and wake aliases are workspace-scoped (`WorkspaceScopedModel`,
  `for_actor()`); a personal digest never contains firm rows and vice
  versa; a skill of one workspace is never offered in another.

## Decisions (with justification)

### D1 — The `proactive` app and the probe registry in `core`

A new kernel app `proactive` owns schedules, runs, notices and digests.
Data comes from **probes**: small, deterministic functions registered at
startup by the app that owns the data, in a registry that lives in
`core` (`core/probes.py`):

```python
@dataclass(frozen=True)
class ProbeSpec:
    name: str                      # "approvals.pending", closed snake_case
    owner_app: str                 # "approvals"
    collect: Callable[[Workspace, User, datetime], ProbeResult]
    digest_section: str            # "approvals" | "tasks" | "calendar" | ...
    notice_kind: str = ""          # "" = digest only
```

`ProbeResult` carries `count`, up to 5 `ProbeItem(key, label, link)` and
`data_class` (`firm` / `personal`). `label` is built by the owning app
from Friday-authored templates and owner-authored fields only (a task
title, a repo name, a calendar item's title already scrubbed by 8A);
never connector result text, tool output, model output or a coding
session's notification text. `link` is a path from a closed set (D5).
`collect` must filter by the workspace it is given; a test per probe
asserts a second workspace's rows never appear.

Probes in this phase (each in its owner app, branch 2 unless noted):
`approvals.pending`, `tasks.due_today`, `tasks.overdue`,
`calendar.today` (memory, from calendar-metadata episodes),
`memory.learning_card` (counts by kind in `window_start(1)` without the
web check — counts only), `memory.sessions_waiting`
(`CodingSession` waiting for permission, project label only),
`harness.waiting_gates` (implementation jobs at Gate A / Gate B),
`nodes.link` (offline / kill switch / revoked), `ai_core.budgets`
(any budget ≥ 80 %), `connectors.n8n_finished` (branch 4),
`assistant.followups_ready` (branch 4), `skills.proposals`
(branches 5–7).

**Why `core`.** The existing cross-app registries live where their
callers can import them: `tools.registry` is imported by `connectors`,
`approvals` and `assistant`, but `memory` may not import `tools` and
`connectors` may not import a new kernel app without an ADR-010
amendment. `core` is importable by every app (the "core must stay
independent of kernel apps" contract keeps it a pure registry: it holds
callables, imports nothing). The alternative — `proactive` importing each
app — would make it a hub that `harness`'s leaf rule forbids and that
reaches `connectors` against ADR-010 decision 1.

### D2 — Unattended runs: deny by default, enforced in the owning services

`core/unattended.py`: a `contextvars.ContextVar` and

```python
@contextmanager
def unattended_run(run_id: str, *, allowed_purposes: frozenset): ...
def current() -> UnattendedContext | None
def deny_if_unattended(effect: str) -> None   # raises UnattendedDenied
```

Every proactive run executes inside `unattended_run`. The guard is called
at the **first line** of each effect in its owning service:

| Effect | Guard site |
|---|---|
| Approval request | `approvals.services.create_request` |
| Tool execution / gated dispatch | `approvals` dispatch path and `tools.registry` read-tool execution wrapper (`run_read_tool`, used by `assistant/services.py` instead of calling `spec.execute` directly) |
| Connector read / write | `connectors.reads.perform_read`, `connectors.writes.prepare_write_payload`, `run_connector_write` |
| Task / memory / fact write | `tasks.services` create/update, `memory.services` create/approve, `memory.graph.services` |
| Node invocation | `nodes` invocation creation |
| Harness job | `harness.services` job creation |
| Gateway call | `ai_core.gateway.InferenceGateway` (`complete`, `embed`, `transcribe`, `synthesize`, before `_enforce_budget`) refuses a purpose outside `current().allowed_purposes` (`proactive.digest` for schedule runs) |
| Celery dispatch | a `before_task_publish` signal handler refuses any task name outside `PROACTIVE_UNATTENDED_TASKS` (the proactive tasks themselves) while a context is set |

A refusal raises `UnattendedDenied(effect)`, audits
`proactive.unattended_denied` (effect, run id, probe or step name; no
content) and ends the run step `denied`; the run continues with the next
probe. There is **no allow list of unattended actions in this phase**:
the owner cannot switch one on, and no setting widens it (a later ADR
would). The contextvar does not cross a Celery hop, which is why
dispatch itself is guarded.

**Why at the services.** A convention ("proactive code does not call
X") fails the first time a probe author reuses a service; a guard at the
effect is tested once per effect (a probe that tries each row of the
table gets `denied`) and holds for code not yet written. ADR-013
decision 6 ("owner away = deny; nothing is queued to run later") is the
node half of the same rule; ADR-016 states the general one.

**Other unattended runs.** The skill curator (D16) and the playbook
distiller (D17) are fixed system tasks, not owner schedules, but they run
inside `unattended_run` too, each with its own one-purpose allowlist
(`skills.curate`, `skills.playbook_distill`); besides their run rows they
may write only skill proposals and stale flags through `skills.ports`.

**Scope.** The guard covers these runs. The existing system tasks
(8A nightly learning, recovery scans, pin beat) are not unattended runs
in this sense: they are fixed code paths reviewed with their own rules
and do not consult owner schedules; they keep running unchanged.

### D3 — Schedules: owner-made over the web; cron cannot create cron

`proactive.ProactiveSchedule` (workspace-scoped): `kind` from a closed
set — `morning_digest` (local time `HH:MM` in `TIME_ZONE`, a weekday
mask, `speak_on_open` flag) and `heartbeat` (interval in {15, 30, 60, 120}
minutes, quiet hours `start`/`end`) — `enabled`, `created_by`,
`last_run_at`, `next_run_at`. At most one row per (workspace, kind).

- **Writers.** Only `proactive.services.save_schedule(actor, channel,
  …)` writes a schedule, and it calls `require_owner_web` (the 8A
  digest's helper pattern: web channel, owner, never an agent).
  `ProactiveSchedule.save()` additionally raises inside an unattended
  context. No `ToolSpec`, `ReadToolSpec`, router intent, MVP 8 skill
  step, SKILL.md skill, connector manifest or harness path names
  `proactive` schedules; a test walks every registered tool, read tool,
  intent and skill and the import graph (D19: only `assistant`, `hud` and
  `ops` may import `proactive` at all, and `assistant`/`hud` only its
  narrow `proactive.api` module).
- **Runner.** One static beat entry, `proactive.tasks.heartbeat`, every
  60 s on the worker beat. It selects due schedules
  (`next_run_at <= now`, enabled, owner still active, workspace not
  paused), claims each with `select_for_update(skip_locked=True)`,
  advances `next_run_at` **before** running (a crash skips one run, never
  repeats it), and runs it inside `unattended_run`. Missed runs are not
  caught up: after downtime only the next occurrence runs (no backlog of
  digests, §c.4 spirit).
- **Pause.** One owner toggle, "Proaktif çalışmaları duraklat" on
  `/today` (`ProactivePause` row per workspace, web only, audited), or a
  deactivated owner skips every schedule (`skipped:paused` /
  `skipped:owner_inactive`), audited once per
  transition.

**Why closed kinds and code beat.** Hermes' lesson is "cron jobs should
be script-gated so they wake the LLM only when there is work"; OpenClaw's
is that a scheduler the agent can extend is a persistence mechanism for
anything that ever reaches it. A closed set of kinds, owner-only writes
and a guard on `save()` make "cron cannot create cron" a property of the
code, not of the prompt.

### D4 — Script gate: probes first, the model only when there is something to say

A run is two steps. **Collect:** run the schedule kind's probes
(deterministic, no model call, no network). **Speak:** only for
`morning_digest`, and only if at least one probe returned `count > 0`,
make one gateway call (D6). A `heartbeat` run never calls a model: it
turns probe deltas into notices with Friday-authored templates (D5). A
run with nothing new writes a `ProactiveRun` row with `outcome=quiet` and
nothing else. Each run records per-probe counts, duration, outcome
(`quiet` / `notices` / `digest` / `denied` / `failed`) and the
`ModelUsage` id of its one call, if any.

A probe that raises is caught, logged (probe name and exception class
only) and counted `probe_failed` in the run; the run continues. A probe
over its time limit (`PROACTIVE_PROBE_TIMEOUT_MS`, default 2000, measured,
not interrupted — probes are DB reads) is flagged slow in the run row
and in preflight.

### D5 — Notices

`proactive.ProactiveNotice` (workspace-scoped): `kind` (closed vocabulary,
one per probe that emits notices), `dedupe_key` (probe item key; unique
per workspace and kind while unread), `title` (Friday-authored template
filled with D1 labels, ≤ 160 chars), `count`, `link` (closed set:
`/approvals`, `/approvals/<id>`, `/tasks`, `/today`, `/memory/learning`,
`/sessions`, `/harness/<id>`, `/devices`, `/chat/<id>`, `/skills`,
`/skills/<id>`), `data_class`, `created_at`, `read_at`, `dismissed_at`.

- Created only by proactive runs (from probe deltas) — never by a view,
  tool or model.
- Read by the owner over the web (`GET /api/v1/proactive/notices`,
  `POST …/{id}/read`, `POST …/dismiss-all`), by the HUD `notices` section
  (count and titles, no deep content) and by the `notices.read` wake
  route (D8), which reads the titles aloud.
- A notice decides nothing; its link opens the existing page where the
  owner acts.
- Retention: read or dismissed notices are hard-deleted after
  `PROACTIVE_NOTICE_RETENTION_DAYS` (default 30) by the daily
  `proactive.tasks.prune` (static beat entry). Notices are not memories
  and never reach the learning pipeline.

### D6 — The morning digest

`proactive.DigestRun` (workspace-scoped): the run, `sections` (JSON: per
`digest_section`, the probe count and item labels — the facts),
`spoken_text` (≤ `PROACTIVE_DIGEST_MAX_CHARS`, default 900),
`composed_by` (`model` / `template`), `model_usage_id`, `opened_at`,
`spoken_at`, `spoken_channel` (`web` / `node_voice`).

- **Sections, in order:** approvals, calendar, tasks (due, overdue),
  sessions waiting, harness gates, learning card (counts by kind and the
  number of core-block proposals; ADR-012's per-item rule means the
  digest only points to `/memory/learning`, it never decides), node link,
  budgets, n8n results and follow-ups (branch 4), skill proposals
  (branches 5–7). Empty sections are omitted.
- **Composition.** One `InferenceGateway.complete(purpose=
  "proactive.digest", personal_data=True, …)` call: the system prompt
  (seeded `PromptTemplate`, Turkish, "speak naturally, at most N
  sentences, no Markdown, no numbers that are not given") and the facts as
  a fenced `<digest_facts>` block. `personal_data=True` always — calendar
  and personal tasks are personal-class — so only
  `personal_data_allowed` providers serve it (ADR-010 decision 5); with
  none, or with a budget refusal, the digest is composed from templates
  (`composed_by=template`) and the run still succeeds.
- **Number check (code).** Every digit run in the model's text must
  equal a number present in `sections` (counts, times, dates); otherwise
  the model text is discarded, `composed_by=template` and the run counts
  `number_check_failed`. Names are not checked (the facts contain them),
  but the output is length-bounded and stripped of Markdown and invisible
  formatting (`memory.context.strip_invisible_formatting`).
- **Per workspace.** A personal schedule digests the personal workspace
  only; a firm workspace may have its own schedule (owner member only).
  A personal digest never lists firm rows (probes receive one workspace).
- **Opened / spoken.** `POST /api/v1/proactive/digests/{id}/ack` with
  `{"event": "opened" | "spoken"}` from the web; the `node_voice` route
  sets `spoken_at` server-side when it returns the text. These timestamps
  and the schedule's audited `enabled` history are the go/no-go data
  (D20).

**Why a model at all.** The template text is correct but reads like a
report; the checkpoint is whether the owner keeps the digest on. One
cheap, local, number-checked call per day is the smallest step that makes
it sound like Friday; the template fallback keeps it working with no
provider.

### D7 — Spoken digest: pulled, never pushed

- **Web.** `/today` and the HUD show the latest digest card. "Dinle"
  speaks `spoken_text` through the existing segmenter and
  `POST /voice/speech` (one `ModelUsage` per sentence, `voice.tts`, audio
  budget). With `speak_on_open` on, the HUD speaks the digest once, the
  first time it opens after the digest is ready, only if browser voice is
  enabled and not muted (ADR-015 decision 3: the mute path is the same
  speak function); the microphone is never opened by it.
- **Mac.** The node cannot be pushed to (frozen server→node set; the
  voice child speaks only replies to its own turns). The owner says "Hey
  Friday, günaydın" (or "brifing"); the `digest.today` route (D8)
  answers the live voice turn with `spoken_text` and the Phase 11
  pipeline speaks it, barge-in included.
- **Telegram and other channels.** Not in this phase: sending the digest
  out is an external send of personal data (gate-class), and an
  unattended run cannot create the request (D2).

### D8 — Wake-phrase routing

`assistant/wake_routes.py`: a code-defined table of routes and a
deterministic matcher used by the chat turn **only when `input_mode` is
`voice`** (web voice and `node_voice`), before `classify_intent`.

```python
@dataclass(frozen=True)
class WakeRoute:
    name: str              # "digest.today"
    effect: str            # "read" | "proposal"
    default_phrases: tuple # ("günaydın", "brifing", "good morning")
    handler: Callable      # (turn) -> RouteResult
```

Routes in this phase: `digest.today` (stored digest text, branch 3),
`digest.learning` (learning-card counts, a link; never decides),
`notices.read` (unread notice titles, marks nothing read — reading aloud
is not an act), `tool.<name>` (branch 4: an owner alias for one read
tool → a `connector_read` turn for that tool, args from the remainder
through the existing `router.connector_args`; or for one write tool → a
`tool_request` draft, exactly what the router would produce), and
`skill.<name>` (branch 5: force-select an approved skill for this turn).

- **Matching.** Normalize as `self-trigger.ts` does (NFC, Turkish
  lower-casing, punctuation stripped, a leading wake phrase "hey friday"
  / "friday" stripped) — the normalizer is a Python port with shared
  vectors (`docs/voice/wake-route-vectors.json`, checked by pytest and,
  if the browser ever routes, vitest). Zero-argument routes match the
  whole utterance; `tool.*` and `skill.*` routes match a leading alias
  followed by the remainder. No fuzzy matching, no model call: a miss
  falls through to the normal router.
- **Aliases.** `assistant.WakeRouteAlias` (workspace-scoped, owner over
  the web only, max 5 per route, 2–40 chars after normalization, unique
  per workspace, refused when it equals or prefixes another route's
  phrase or alias). No voice turn, tool, skill or model can create an
  alias.
- **Cap.** A route's `effect` is `read` or `proposal` by construction
  (the table is validated at boot: anything else refuses to start), and
  `apply_voice_capability_cap` still runs on the route's result. A route
  that would need a confirm- or gate-class step yields a draft card.
- **Bookkeeping.** The assistant message records `route=<name>`
  (`Message.route`, new nullable field) for audit and tests. A route with
  no model call writes no `ModelUsage` (there is no call); a `tool.*` route
  writes the same rows the router path writes.

**Why after the wake word, not multiple wake words.** OpenClaw routes
several wake words to agents; Friday has one trained wake model
(`hey_friday`) and its false-accept budget is per model (Phase 11 D11).
More wake words would multiply the FAR evidence the owner has to record.
Routing on the phrase after the wake word costs nothing on the Mac and
saves the `router.intent` call (latency) on the most common requests.

### D9 — n8n transport: MCP Server Trigger behind a relay sidecar

- **Server side (owner's n8n).** One dedicated n8n workflow, "Friday
  tools", whose **MCP Server Trigger** node exposes one tool per
  allowlisted workflow; each tool node is a "Call n8n Workflow Tool" and
  is named `wf_<workflowId>`. The trigger requires bearer auth. A
  reference export ships at `deploy/n8n/friday-tools.workflow.json` (two
  sample tools wired to two sample sub-workflows) for the owner to
  import.
- **Relay sidecar.** n8n requires a credential on every MCP request; ADR-010
  decision 3 keeps credentials out of web and worker. The bridge gains an
  **HTTP relay mode** (`backend/connectors/docker/http_relay.py`, same
  image as `Dockerfile.bridge`): it serves Streamable HTTP on the internal
  `connectors` network, forwards `tools/list` and `tools/call` only to
  the upstream URL in `RELAY_UPSTREAM_URL` with the bearer from
  `RELAY_BEARER_TOKEN` (both sidecar env only), lists and calls only
  `BRIDGE_ALLOWED_TOOLS`, follows no redirects and offers no client
  capabilities upstream. Compose service `n8n-relay` (profile
  `connectors`, the `x-connector-sidecar` hardening, networks
  `connectors` + `connectors-egress`). Friday's `N8N_MCP_URL` points at
  the relay.
- **Rejected:** n8n's instance-level MCP (`/mcp-server/http`): about 60
  tools including workflow search, create, update, publish and the
  builder; workflows are opted in for every client at once; a
  `workflowId` argument turns one tool into all workflows. The community
  `czlonkowski/n8n-mcp`: a second, unofficial surface (landscape §2:
  "fallback only"). A native REST bridge to n8n's webhook URLs: it would
  put the webhook secret in the worker and bypass MCP schema pins.

### D10 — The Friday-side workflow-ID allowlist

`backend/connectors/manifests/n8n.py`:

```python
WORKFLOWS = (
    N8nWorkflow(
        tool="n8n_server_status",          # Friday name
        workflow_id="Ab12Cd34Ef56Gh78",     # n8n id; server tool wf_<id>
        kind=TOOL_READ,
        mode="async",                       # D11; "sync" for fast reads
        description="…Friday-authored…",
        argument_serializer=ServerStatusArgs,
        schema_sha256="…",
    ),
    ...
)
MANIFEST = build_manifest(WORKFLOWS)   # data_class personal, version N
```

- `build_manifest` derives each `ToolPolicy` (`server_tool =
  f"wf_{workflow_id}"`), refuses a workflow id that does not match
  `^[A-Za-z0-9]{8,32}$`, a duplicate id or Friday name, a write without
  a renderer, and any server tool outside the `wf_` convention.
  `docs/mcp/n8n.md` lists the same table (a test compares them).
- **Three ceilings**: n8n's trigger exposes only the tools the owner
  wired; the relay's `BRIDGE_ALLOWED_TOOLS` (generated by
  `manage.py n8n_relay_env`, which prints the env line from `WORKFLOWS`)
  lists only allowlisted names; the manifest registers only reviewed
  entries with pinned input schemas. Adding a workflow is a reviewed
  commit (manifest + twin + pin), never a database write (ADR-004,
  governance rule 1).
- **Data class `personal`.** The owner's automations touch home, mail and
  personal services, so the manifest binds to the personal workspace
  only and reads run over `web` and `node_voice` only (the Phase 11
  `READ_CHANNELS`). Every read marks the conversation `personal_data`
  (ADR-010 decision 5). A firm-class n8n would be a second manifest
  (out of scope).
- **Residual (R3).** Friday cannot verify that the tool node named
  `wf_X` really calls workflow `X`: that mapping lives in the owner's
  n8n. Friday guarantees that nothing outside the allowlist is listed or
  callable and that each schema is pinned; it trusts the owner's own n8n
  configuration at the owner's trust level.

### D11 — Async reads and follow-ups ("async voice turns")

n8n workflows take seconds to minutes (landscape §2: "started, I'll tell
you"). A read tool may be declared `mode="async"`:

1. The router picks it as today (one read per turn). `tools.registry`
   gains `ReadToolSpec.start_async(*, workspace, actor, args, channel) ->
   AsyncReadHandle` and `fetch_async(handle) -> ReadResult`; connectors
   implements them with `connectors.ConnectorAsyncRead` (binding, tool,
   arguments sha256, status `pending` / `running` / `ok` / `failed` /
   `timed_out`, bounded sanitized result text, closed error code) and the
   task `connectors.tasks.run_async_read` (one call, never retried,
   `N8N_ASYNC_READ_TIMEOUT_SECONDS` default 120, the same pin re-check and
   `connector.read` audit with `mode=async`).
2. The turn's reply is a Friday-authored template ("Başlattım: <tool
   label>. Sonucu bu konuşmaya yazacağım."), no model call. The assistant
   stores `assistant.PendingFollowUp` (conversation, source message,
   tool name, handle, status).
3. On completion connectors calls `tools.registry.notify_async_read(
   handle)`; `assistant` registered a listener at startup (the
   `tools`-registry pattern; `tools` imports nothing of either side) that
   dispatches `assistant.tasks.compose_followup`. That task makes **one**
   completion, purpose `chat.followup`, `personal_data` per the tool's
   data class, with the result fenced as `<connector_result>` (the
   existing `assistant.context.render_connector_block`), no tools, no
   router, no read; it appends an assistant message to the conversation
   (`Message.followup_of` → the source message) and marks the follow-up
   `done`. A failed or timed-out read appends a template message with the
   closed error code, no model call.
4. The `assistant.followups_ready` probe turns completed follow-ups into
   notices; the voice owner hears them via `notices.read` or by opening
   the conversation.

This is a continuation of an owner-initiated turn, not an unattended run:
it can make exactly one completion and nothing else; it is not inside
`unattended_run` because the request it answers was the owner's. The ADR-015
cap is unaffected (a read is a read).

### D12 — n8n writes: gate-class, at most once, announced

A write workflow is a `TOOL_WRITE` entry: one gated `ToolSpec`
`mcp_n8n_<tool>` (`user_invocable=True`), decided over the web only,
snapshot-backed, sent once by `run_connector_write`, never retried,
`unknown_outcome` on doubt, a Friday-authored `work_log` (private,
personal class) — all existing ADR-010 decision 6 machinery, unchanged.
A voice turn naming it yields a draft card (ADR-015). Terminal executions
are announced by the `connectors.n8n_finished` probe (status word and the
tool's Friday label; never the result text). No n8n write is
confirm-class.

### D13 — SKILL.md instruction skills

A second kind of skill, next to MVP 8's code-only plan templates:

- **Format.** Anthropic-style `SKILL.md`: a front-matter block and a
  Markdown body. Allowed keys: `name` (`^[a-z][a-z0-9-]{1,63}$`),
  `description` (≤ 1024 chars, required), `license` (≤ 100 chars),
  `metadata` (flat string map, ≤ 10 keys). **Refused keys:**
  `allowed-tools` and any other permission-shaped key (`tools`,
  `permissions`, `allow`, `auto_approve`, `trust`) → `permission_key_refused`;
  any unknown key → `unknown_key`. Body ≤ `SKILLS_BODY_MAX_CHARS`
  (default 16 000), plain text, invisible formatting stripped.
- **Parser.** A strict hand-written parser (`skills/skill_md.py`) for a
  YAML subset: one `key: value` per line, scalar strings (plain or
  double-quoted), `metadata:` with indented `key: value` lines; no
  anchors, aliases, tags, flow collections, multi-line scalars or
  documents beyond the first. **Why no YAML library:** none is a
  dependency today, and a full YAML loader is an attack surface (tags,
  aliases, billion laughs) for a five-key header.
- **Bundles and host execution.** An upload is either one `SKILL.md` or
  a `.zip` with `SKILL.md` and at most 10 `references/*.md` files
  (≤ 64 KB each). Any other file — scripts, binaries, `.py`, `.sh`, `.js`,
  dotfiles, symlinks, paths with `..` — refuses the whole upload
  (`host_execution_refused` / `bundle_refused`). Friday never executes
  anything from a skill, anywhere; a skill mounted into a harness job
  (D17) is read-only text inside the container.
- **No registries.** Skills arrive only as owner uploads or owner-typed
  text over the web, or as curator/playbook proposals. There is no URL
  import, no registry client, no "install from ClawHub/agentskills.io"
  path; a test asserts the `skills` app performs no outbound HTTP and
  imports no HTTP client.
- **Storage.** `skills.InstructionSkill` (workspace-scoped: `name`,
  `status`, `origin` = `owner` / `upload` / `curator` / `playbook`,
  `current_version`, `last_used_at`, `use_count`) and
  `skills.InstructionSkillVersion` (immutable after insert: front matter
  JSON, body, `references` JSON, `content_sha256`, `proposed_by`
  (`owner` / `curator` / `playbook`), `provenance` JSON, `decided_by`,
  `decided_at`, `decision`). An approved version is never edited; an
  edit is a new version.
- **Relation to MVP 8.** `skills.catalog` stays the code-only plan
  registry its docstring describes (agents' plan templates, tool
  references validated at boot). Instruction skills are rows because they
  grant nothing: they cannot name a tool to run, cannot add a step to an
  agent plan and cannot change a permission, so the "code-only gives a
  stronger self-modification guarantee" argument does not apply to
  them. What they can change — the model's instructions — is behind the
  owner's per-version approval.

### D14 — Staged approval lifecycle

States: `proposed` → (`active` | `rejected`); `active` ↔ `stale`
(curator, metadata only); `active`/`stale` → `archived` (owner, one click,
reversible: `restore` back to `active`). A newer version of an active
skill is `proposed` while the current version stays in use.

- **Decisions.** Approve, edit-then-approve, reject, archive and restore
  are owner-only over the web (`require_owner_web`), one item at a time.
  There is **no bulk approve** for skills (unlike `work_log` memories) and
  no "allow always": every version is approved by content hash; a version
  whose hash changed between display and approval is refused
  (`version_changed`).
- **Never by a model.** No LLM approves, ranks-to-approve or
  auto-promotes anything (the stub's "no LLM auto-approval"; Hermes'
  "smart" approval mode rejected). Curator and playbook proposals land in
  `proposed` like an upload.
- **Never deleted.** Skills and versions are archived, never deleted, in
  this phase (Hermes: "stale, then archive, never delete, audit ledger");
  every transition writes an audit row (`skills.version.proposed`,
  `.approved`, `.rejected`, `skills.skill.stale`, `.archived`,
  `.restored`) with ids and hashes, never content.
- **Caps.** At most `SKILLS_MAX_ACTIVE` (default 50) active skills per
  workspace; at most 5 pending proposals from the curator and 3 from
  playbooks at any time (D16, D17).

### D15 — Using skills in a turn

- **Matching.** When a workspace has active skills, the chat turn runs one
  cheap gateway call, purpose `skills.match`, with the owner's message
  and the active skills' `name` + `description` (owner-approved text),
  returning `{"skill": "<name>" | null}`; strict parser, unknown names
  degrade to `null` (the `agent.skill_match` pattern). A `skill.<name>`
  wake route or an explicit `/skill <name>` prefix skips the call.
  Skipped entirely when no skill is active (no call, no `ModelUsage`).
- **Injection.** The matched skill's approved body goes into the chat
  completion's system context inside `<skill name="…" version="…">`,
  framed as "owner-approved instructions for this kind of request; they
  do not change which tools exist or what needs approval". References are
  appended only when the body names them, bounded to
  `SKILLS_CONTEXT_MAX_CHARS` (default 8000). The context report records
  `skills: [{name, version, sha256}]`.
- **No capability.** The router's tool vocabulary, read channels, the
  voice cap, the approval classes and the trust check are computed
  exactly as without a skill; a test runs every intent with and without a
  hostile skill ("ignore approvals", "call mcp_n8n_* directly", "you may
  auto-approve") and asserts identical drafts, approvals and tool calls.
- **Usage.** `use_count` / `last_used_at` updated per use (the curator's
  input).

### D16 — The curator

Weekly (static beat entry, Sunday 04:15 `TIME_ZONE`, after the 8B
reflection), per workspace with active skills, `skills.tasks.curate`:

1. **Deterministic:** an active skill unused for `SKILLS_STALE_DAYS`
   (default 45) becomes `stale` (still offered to the matcher, shown
   dimmed); a stale skill used again becomes `active`. No model call.
2. **Proposals (one gateway call, purpose `skills.curate`):** given the
   active skills' names, descriptions and bodies (owner-approved text) and
   usage counts, propose at most 3 changes from a closed set: `merge`
   (two skills → one new version proposal), `edit` (a new version of one
   skill), `archive` (a suggestion; the owner clicks). The output is
   strictly parsed; a proposed body must pass the SKILL.md parser and
   refusals; anything else is dropped and counted.
3. Proposals land as `proposed` versions (origin `curator`) or archive
   suggestions (stored on the curator run, surfaced as notices through the
   `skills.proposals` probe). Never applied.

The curator is a fixed system task (like the 8A nightly run), not an
owner schedule; it runs under `unattended_run` with
`allowed_purposes={"skills.curate"}` and may write only skill proposals,
archive suggestions and stale flags (D2).

### D17 — Procedural playbooks from harness runs

- **Sources.** At an implementation job's terminal state, beside the
  existing `record_episode` call, `harness` calls
  `skills.ports.record_playbook_source(…)` (harness → skills is allowed;
  skills never imports harness). Recorded only when the job `succeeded`,
  its review verdict is approve, its tests passed and its goal was typed
  by the owner. Fields: workspace, repo label, goal (owner text), the
  repo test command, the changed-file paths (no contents), the test
  summary counts, the job id; the model-produced diff and review
  summaries as `context_text` (≤ 1000 chars, fenced as untrusted when
  shown to a model). Sensitive repos record nothing (`sensitive` repos
  stay out of playbooks entirely).
- **Distillation.** Nightly (static beat entry, after the 8A run), for a
  repo with ≥ `SKILLS_PLAYBOOK_MIN_SOURCES` (default 3) unused sources,
  one call (purpose `skills.playbook_distill`, `personal_data` by the
  workspace's data class) drafts one SKILL.md: name, description ("when
  to use") and steps. **Provenance in code:** each step must carry a
  `source` job id from the input set and a verbatim `quote` that matches
  an owner-authored or Friday-recorded field of that source (goal, test
  command, a changed-file path) after whitespace normalization;
  context-text (model-produced) can never be a quote. A step without a
  match is dropped; a draft with fewer than 2 valid steps is dropped and
  counted. Cap: 3 playbook proposals per workspace per week. The run is
  inside `unattended_run(allowed_purposes={"skills.playbook_distill"})`
  (D2).
- **Proposal.** The draft becomes a `proposed` version (origin
  `playbook`, provenance = the step ↔ job map), shown on `/skills/<id>`
  with each step's source job linked. Owner approves per item (D14).
- **Reuse in the harness.** `HarnessImplementationJob` gains
  `skill_version_ids` (owner-selected on the implementation request over
  the web; active, same workspace). The runner writes each version's
  `SKILL.md` (and references) into the worktree at
  `.claude/skills/<name>/` read-only before the agent starts, and the
  Gate A display lists them by name and hash. Nothing else changes: the
  harness still runs in its container, Gate A and Gate B still decide.
  A skill is text inside the container; it is never executed on a host.

**Why proposals, not auto-learning.** ADR-012 decision 7: model output
(the diff and review summaries) is never the provenance of a learned
item. The playbook's quotes come from the owner's goal and Friday's own
recorded commands and paths; the owner approves each one.

### D18 — Purposes, tiers, budgets, compression

| Purpose | Tier | Budget (seeded, owner-editable) | `personal_data` |
|---|---|---|---|
| `proactive.digest` | cheap | `proactive`: 3 USD/month, 0.25 USD/day | always |
| `chat.followup` | mid | the `chat` path's existing budgets | by tool data class |
| `skills.match` | cheap | `skills`: 5 USD/month | by conversation |
| `skills.curate` | mid | `skills` + 0.5 USD/week (daily window on Sunday, the reflection pattern) | by workspace class |
| `skills.playbook_distill` | mid | `skills` + 0.5 USD/day | by workspace class |

Each purpose gets an `AI_PURPOSE_TIERS` entry, a seeded `PromptTemplate`
(data migration), and the budget rows in `seed_ai_defaults` plus a data
migration (the Phase 10 pattern). All five are added to
`COMPRESSION_BYPASS` (number and provenance checks need the exact text).
Wake routes and notices make no model call. Budget refusals degrade:
digest → template; follow-up → template with `budget_refused`; match →
no skill; curate / distill → skipped and counted.

### D19 — Import contracts

New and changed import-linter contracts (branch that introduces each):

- **`proactive` may import only `core`, `workspaces`, `audit`,
  `ai_core`, `accounts`** (branch 1; `accounts` only for the owner
  lookup and active state that D3's runner checks). It reaches every
  other app's data through `core.probes`.
- **Only `assistant`, `hud` and `ops` may import `proactive`**, and
  `assistant` / `hud` only `proactive.api` — `latest_digest`,
  `mark_digest_spoken`, `unread_notices`, `notice_section` (branch 1: a forbidden
  contract over every other app; a second one forbidding
  `assistant`/`hud` → `proactive.services`, `proactive.tasks`,
  `proactive.views`).
- **hud** contract widened by `proactive` (branch 2; the notice section).
- **`skills`**: unchanged edges (`tools`, `ai_core`, `core`,
  `workspaces`, `audit`); `harness` → `skills.ports` is allowed already;
  a new contract forbids `skills` → `connectors`, `nodes`, `assistant`,
  `proactive` (branch 5).
- **`connectors`**: unchanged (`tools`, `memory`, `core`, `workspaces`,
  `audit`, `ai_core`); async completion goes through `tools.registry`.
- `core.probes` and `core.unattended` import nothing outside the
  standard library (the existing core contracts cover it).
- The relay is `connectors.docker.http_relay`, added to the
  `mcp.server` ignore list next to `stdio_bridge` (sidecar-only).

### D20 — Surfaces and the checkpoint data

- **Web.** `/today`: digest card (spoken text, sections, "Dinle", "Ayrıntı"),
  schedule editor (time, days, `speak_on_open`, heartbeat interval and
  quiet hours, on/off). `/notices` panel (shell bell with unread count).
  `/skills`: list by status, upload, editor, version diff, proposals
  queue with provenance, archive/restore. `/settings/wake-routes`: routes
  and aliases. All owner-only, web channel.
- **HUD.** New `notices` section (unread count, five latest titles,
  links) and the digest card with "Dinle". HUD stays read-only: nothing
  on it decides.
- **Checkpoint data.** `manage.py proactive_checkpoint` prints, per
  workspace: first enable date of `morning_digest`, days enabled since,
  every disable/enable (audit `proactive.schedule.enabled` /
  `.disabled`), digests produced, opened, spoken (web / node_voice). The
  go/no-go reads it after two weeks (pending owner evaluation).

## ADR

- **ADR-016 (new, written in branch 1 before code): "Proactive runs,
  schedules and instruction skills".** Decisions: (1) unattended runs are
  deny-by-default, enforced at each effect's owning service, with no
  owner-switchable allow list (a future one needs a new ADR); (2)
  schedules are owner-created over the web only, from a closed set of
  kinds, and nothing a run, tool, skill, model or connector can do
  creates or edits one ("cron cannot create cron"); (3) script gate: a
  run calls a model only when its deterministic probes found something,
  and only for allowlisted purposes; (4) instruction skills are text that
  grants no capability; staged, per-version owner approval; no public
  registries, no permission keys, no host execution, no LLM approval;
  never deleted; (5) playbooks are proposals with code-checked
  provenance from owner/Friday-recorded fields only. Status Accepted on
  the owner's review of branch 1.
- **ADR-010 amendment (branch 4): "Amendment 2026-11 — HTTP relay and
  async reads".** Decision 3 gains the relay mode (credential in the
  sidecar for servers that need one per request); decision 4 gains async
  reads (one read per turn, result fenced into one follow-up completion,
  never a tool loop). The n8n manifest is recorded under decision 3's
  manifest rule.
- **ADR-015 implementation note (branch 3):** wake-phrase routes are
  read/proposal-only by construction and the cap runs after them.
- **ADR-012 note (branch 7):** playbook sources are not episodes; their
  provenance rule is ADR-012 decision 7/8 applied to skills.
- **ADR-002:** no amendment; the new app and contracts follow its rules
  (D19).

Nothing moves between gate-class and confirm-class.

## Scope (one branch each)

### 1. `feat/p12-proactive-core` — app, probe registry, unattended guard, schedules, notices, ADR-016

**Scope.** D1–D5, D19 (proactive parts), ADR-016:
- `core/probes.py` (`ProbeSpec`, `ProbeItem`, `ProbeResult`,
  `register_probe`, `list_probes`, boot validation of names, sections,
  notice kinds and link paths);
- `core/unattended.py` (context, `deny_if_unattended`, `UnattendedDenied`)
  and the guard calls in every row of the D2 table, plus the
  `before_task_publish` handler (registered in `proactive.apps`) and the
  gateway purpose check;
- `tools.registry.run_read_tool` wrapper (guard + the existing call) and
  `assistant/services.py` switched to it;
- `proactive` app: `ProactiveSchedule`, `ProactivePause`, `ProactiveRun`,
  `ProactiveNotice` (migration), `services.save_schedule`, `api` (the public surface D19
  allows), `tasks.heartbeat`,
  `tasks.prune`, `runs.run_schedule` (collect → notices), views
  (`/api/v1/proactive/schedules`, `/notices`), audit events;
- `config/celery.py`: `proactive-heartbeat` (60 s) and
  `proactive-notice-prune` (daily) entries;
- settings and `.env.example`: `PROACTIVE_PROBE_TIMEOUT_MS`,
  `PROACTIVE_NOTICE_RETENTION_DAYS`, `PROACTIVE_UNATTENDED_TASKS`;
- import-linter contracts (D19, proactive part);
- `docs/adr/ADR-016-proactive-runs-and-skills.md`.

**Files.**
- `backend/core/{probes,unattended}.py`, `backend/core/tests/test_probes.py`,
  `backend/core/tests/test_unattended.py`;
- `backend/proactive/{__init__,apps,models,services,api,runs,tasks,views,urls,serializers}.py`,
  `backend/proactive/migrations/0001_initial.py`,
  `backend/proactive/tests/{test_schedules,test_heartbeat,test_notices,test_unattended_guard,test_views,test_isolation}.py`;
- `backend/{approvals,tasks,memory,nodes,harness,connectors}/…services.py`
  (one guard line each), `backend/ai_core/gateway.py`,
  `backend/tools/registry.py`, `backend/assistant/services.py`;
- `backend/config/{celery,urls}.py`, `backend/config/settings/base.py`,
  `.env.example`, `backend/pyproject.toml`;
- `docs/adr/ADR-016-proactive-runs-and-skills.md`.

**Tests.**
- Guard: inside `unattended_run`, each D2 effect raises
  `UnattendedDenied` before any row or network call (approval request,
  gated dispatch, read tool, connector read/write, task create, memory
  create/approve, fact write, node invocation, harness job, a gateway
  call with a non-allowlisted purpose, `apply_async` of a non-allowlisted
  task) and writes one `proactive.unattended_denied` audit row; outside
  it, every existing test passes unchanged.
- Cron cannot create cron: `ProactiveSchedule.save()` inside a run
  raises; the services refuse every channel but web and every actor but
  the owner (agent, `cli`/`openai`/`hook`/`node`/`node_voice` tokens,
  member, anonymous); a walk of `list_tool_names()`, read tools, router
  intents, MVP 8 skills and connector manifests finds no path to
  schedules; `lint-imports` enforces D19.
- Heartbeat: due selection, `skip_locked` claim, `next_run_at` advanced
  before the run (a crash mid-run never repeats it), no catch-up after a
  48 h gap (one run), quiet hours honoured, pause / inactive owner
  skip (audited once per transition), manual clock.
- Script gate: a `heartbeat` run makes no gateway call ever
  (`ModelUsage` count unchanged with every probe non-empty); a quiet run
  writes only its run row.
- Notices: delta-only creation (same dedupe key twice → one unread
  notice), link outside the closed set refused at registration, title
  template only (a probe returning an item label with a `<` or a URL is
  refused by validation), retention prune.
- Probe isolation: two workspaces, two owners' rows; each probe sees only
  its workspace (a fake probe set here; the real probes in branch 2).
- Role × endpoint: schedules and notices endpoints × the five Phase 4
  principals, `cli`/`openai`/`hook` PATs, node secret, `node_voice` token,
  kill-switched owner, firm workspace with a non-owner member.

**Done when:** a fake probe set turns into notices through the real
heartbeat on the worker beat, every D2 effect is denied inside a run,
`lint-imports` is green with the new contracts, and ADR-016 is written.

### 2. `feat/p12-morning-digest` — probes, digest, `/today`, HUD notices, "Dinle"

**Scope.** D1 probes, D6, D7 (web), D18 (`proactive.digest`), D20:
- probes registered in their owner apps' `ready()`: `approvals.pending`,
  `tasks.due_today`, `tasks.overdue`, `memory.calendar_today`,
  `memory.learning_card`, `memory.sessions_waiting`,
  `harness.waiting_gates`, `nodes.link`, `ai_core.budgets` (registered by
  `proactive`'s app config through `ai_core` selectors, since `ai_core`
  imports no business app);
- `proactive.DigestRun` (migration), `runs.compose_digest` (gateway call,
  number check, template fallback), `/api/v1/proactive/digests/latest`,
  `…/{id}`, `…/{id}/ack`;
- purpose `proactive.digest`: tier, seeded prompt (data migration),
  `proactive` budget rows (`seed_ai_defaults` + migration),
  `COMPRESSION_BYPASS`;
- frontend: `/today` digest card + schedule editor, shell notice bell and
  `/notices`, HUD `notices` section and digest card; "Dinle" through
  `segmenter.ts` and the speech client; `speak_on_open`;
- `hud/services.py` `notices` section (`SECTIONS` + `proactive.api`),
  hud contract widened;
- `manage.py proactive_checkpoint`.

**Files.**
- `backend/{approvals,tasks,memory,harness,nodes}/probes.py` (+ `apps.py`
  registration), `backend/proactive/{digest,templates}.py`,
  `backend/proactive/migrations/0002_digest.py`,
  `backend/ai_core/migrations/00xx_proactive_digest_prompt_and_budget.py`,
  `backend/ai_core/management/commands/seed_ai_defaults.py`,
  `backend/ai_core/compression.py`,
  `backend/proactive/management/commands/proactive_checkpoint.py`,
  `backend/proactive/tests/{test_digest,test_number_check,test_probes_*,test_checkpoint}.py`;
- `backend/hud/{services,views}.py`, `backend/hud/tests/test_notices_section.py`;
- `frontend/src/app/(shell)/today/page.tsx`,
  `frontend/src/components/proactive/{DigestCard,ScheduleEditor,NoticeBell,NoticesPanel}.tsx`,
  `frontend/src/lib/api/proactive.ts`, HUD panel component, vitest specs,
  `frontend/e2e/digest.e2e.ts`;
- `backend/config/settings/base.py`, `.env.example`
  (`PROACTIVE_DIGEST_MAX_CHARS`, `AI_BUDGET_PROACTIVE_*`).

**Tests.**
- Each probe: counts, labels from owner/Friday fields only (a coding
  session's untrusted notification text never appears; a task title
  does), links in the closed set, workspace isolation, data class.
- Digest: no probe content → no call, no row beyond the run; content →
  exactly one `ModelUsage` (purpose `proactive.digest`,
  `personal_data=True`) with FakeAdapter; no personal-data provider →
  template, no call; budget refusal → template; a model text with an
  extra number ("5 onay" when the facts say 2) → template,
  `number_check_failed`; Markdown and invisible characters stripped;
  length bound; personal digest omits firm rows with both workspaces
  populated.
- `speak_on_open` and "Dinle": vitest with the fake speech client — one
  `/voice/speech` call per segment, nothing when muted, the mic never
  opened (the Phase 6 mute spec extended).
- Ack: `opened` / `spoken` from the web only; idempotent; other
  workspace → 404.
- HUD: `notices` section counts and titles only, no body, `for_actor()`;
  `changed_sections` emits on a new notice.
- Checkpoint command over an audited enable/disable history.
- Role × endpoint for the digest and ack endpoints.

**Done when:** a seeded dev database with approvals, tasks, a calendar
episode and learning proposals produces a number-checked digest at the
scheduled minute through the real heartbeat, `/today` shows and speaks
it with the fake speech provider, and the HUD shows the notice.

### 3. `feat/p12-wake-routes` — wake-phrase routing, aliases, the spoken digest on `node_voice`

**Scope.** D8 (zero-argument routes), D7 (Mac half):
- `assistant/wake_routes.py` (table, boot validation, normalizer,
  matcher), `assistant.WakeRouteAlias` + `Message.route` (migration),
  the chat turn hook for `input_mode=voice` before `classify_intent`;
- routes `digest.today` (sets `spoken_at`, `spoken_channel`),
  `digest.learning`, `notices.read`;
- shared vectors `docs/voice/wake-route-vectors.json` (pytest);
- `/api/v1/assistant/wake-routes` (list) and alias CRUD (owner, web);
  `/settings/wake-routes` page;
- ADR-015 implementation note.

**Files.**
- `backend/assistant/{wake_routes,models,views,services,serializers,urls}.py`,
  migration, `backend/assistant/tests/{test_wake_routes,test_wake_route_aliases,test_wake_routes_node_voice}.py`;
- `docs/voice/wake-route-vectors.json`;
- `frontend/src/app/(shell)/settings/wake-routes/page.tsx`, component,
  vitest;
- `docs/adr/ADR-015-voice-originated-turns.md` (note).

**Tests.**
- Normalizer vectors (Turkish İ/ı, NFC, punctuation, leading "hey
  friday" / "friday", trailing filler) and matcher: whole-utterance match
  for zero-argument routes, no prefix match for them, miss → normal
  router (the `router.intent` call happens), typed turns never routed.
- `digest.today` on the web voice path and on `node_voice` (a token
  minted with the reference node's secret, a live session conversation):
  the reply is the stored `spoken_text`, no `ModelUsage`, `spoken_at`
  set with `spoken_channel=node_voice`; no digest → a template ("Bugün
  için brifing yok"); a firm digest never answers a personal route.
- `notices.read` marks nothing read; `digest.learning` decides nothing
  (learning items untouched).
- Cap: the route table refuses an effect other than `read` /
  `proposal` at boot; the ADR-015 cap still runs on a route result
  (a test route returning an executing intent degrades to chat).
- Aliases: owner web only (role × endpoint), conflicts and prefix
  clashes refused, max 5, no path from a voice turn, tool or skill to
  an alias (walk as in branch 1).
- Node protocol and `friday-voice/v1` unchanged:
  `build_node_conformance --check` and the child's
  `conformance --check` against the unchanged `conformance.lock`.

**Done when:** a `node_voice` turn "Hey Friday, günaydın" from the Phase
11 reference host returns the digest text with no model call and the
pipeline speaks it in the reference host's playback double; aliases work
on the web; vectors are green.

### 4. `feat/p12-n8n-connector` — manifest, relay, async reads, gated writes, notices

**Scope.** D9–D12, ADR-010 amendment, `tool.*` wake routes:
- `backend/connectors/docker/http_relay.py` (relay mode; reuses the
  bridge's allowlist and env handling), compose service `n8n-relay`;
- `backend/connectors/manifests/n8n.py` (`N8nWorkflow`, `WORKFLOWS` with
  the two reference entries, `build_manifest`), `BUILTIN` += `n8n`,
  `docs/mcp/n8n.md`, allowlist row, `manage.py n8n_relay_env`;
- `deploy/n8n/friday-tools.workflow.json` (reference export) and its
  README section;
- async reads: `tools.registry` `start_async` / `fetch_async` /
  `notify_async_read` / listener registration,
  `connectors.ConnectorAsyncRead` (migration),
  `connectors.tasks.run_async_read` + recovery scan,
  `assistant.PendingFollowUp`, `Message.followup_of` (migration),
  `assistant.tasks.compose_followup`, purpose `chat.followup`
  (tier, prompt, `COMPRESSION_BYPASS`);
- probes `connectors.n8n_finished` (registered by connectors in `core`)
  and `assistant.followups_ready`;
- wake routes `tool.<name>` with owner aliases;
- settings: `N8N_MCP_URL`, `N8N_ASYNC_READ_TIMEOUT_SECONDS`; sidecar env
  names `RELAY_UPSTREAM_URL`, `RELAY_BEARER_TOKEN` in `.env.example`
  (names only);
- ADR-010 amendment.

**Files.**
- `backend/connectors/{manifests/n8n.py,manifests/__init__.py,docker/http_relay.py,reads.py,tasks.py,models.py,probes.py}`,
  migration, `backend/connectors/management/commands/n8n_relay_env.py`,
  `backend/connectors/tests/{test_n8n_manifest,test_http_relay,test_async_reads,test_n8n_writes,test_n8n_chat,test_n8n_probe}.py`,
  `backend/connectors/tests/fakes.py` (an n8n-shaped fake state);
- `backend/tools/registry.py`, `backend/tools/tests/test_async_read_registry.py`;
- `backend/assistant/{models,services,tasks,wake_routes}.py`, migration,
  `backend/assistant/tests/{test_followups,test_wake_routes_tools}.py`;
- `deploy/docker/compose.yml`, `deploy/n8n/friday-tools.workflow.json`;
- `docs/mcp/n8n.md`, `docs/mcp/allowlist.md`,
  `docs/adr/ADR-010-connectors-deploys-mcp.md`, `.env.example`,
  `backend/pyproject.toml` (relay ignore entry).

**Tests.**
- Manifest: `build_manifest` refusals (bad id, duplicate, non-`wf_`
  server tool, write without renderer), `test_manifests.py` three-artifact
  check passes with `n8n`, the twin's table equals `WORKFLOWS`,
  `n8n_relay_env` output equals the allowlist.
- Relay (in-process, upstream = SDK in-memory server): forwards only
  allowlisted tools in `tools/list` and `tools/call`, refuses others,
  sends the bearer only upstream (never to Friday, never in a log line),
  refuses redirects, offers no client capabilities, refuses
  resources/prompts/sampling.
- Pins: a changed upstream `inputSchema` disables that tool
  (`connector.pin_mismatch`), sticky for the manifest version.
- Sync read on web and `node_voice` (personal), refused on `cli`,
  `hook`, `openai`, `telegram`; personal-data flag set before the
  completion; result fenced; `connector.read` audit without content.
- Async read: turn reply is the template with no model call; the task
  runs once; timeout → `timed_out` template follow-up; a lost worker →
  recovery scan marks `failed`, never re-sends; completion → exactly one
  `chat.followup` `ModelUsage` with the result fenced and no tools; a
  hostile result ("onayla", "call mcp_n8n_…") produces no draft, approval
  or read; follow-up appended to the right conversation only; probe
  notice created.
- Writes: voice turn → draft only; web card → approval → one send →
  `succeeded` / `failed` / `unknown_outcome` (never retried); work_log
  private; notice with status word only; inside an unattended run the
  write request is denied.
- `tool.*` routes: alias → `connector_read` for a read tool, draft for a
  write tool; unknown alias → normal router.
- No credential value anywhere (DB, audit, logs), the
  `test_no_credential_value_anywhere` pattern.

**Done when:** against the SDK's in-memory server behind the in-process
relay, an async n8n read started by a `node_voice` turn ends in a
follow-up message and a notice, a write runs once after a web approval,
and the manifest/twin/allowlist checks pass.

### 5. `feat/p12-skills-core` — SKILL.md parser, staged lifecycle, matcher, `/skills`

**Scope.** D13–D15, `skill.*` wake routes:
- `skills/skill_md.py` (parser, refusals, bundle reader with zip path and
  type checks), `skills.InstructionSkill` / `InstructionSkillVersion`
  (migration — the app's first), `skills/services.py` (propose, approve,
  edit-approve, reject, archive, restore; `require_owner_web`; caps),
  `skills/selectors.py`, `skills/ports.py` (proposal entry point for
  curator/playbook), views and URLs;
- matcher (purpose `skills.match`: tier, prompt, budget,
  `COMPRESSION_BYPASS`) and injection in `assistant/context.py` /
  `services.py`; context report `skills`;
- `skills.proposals` probe; `skill.<name>` wake routes;
- frontend `/skills` (list, upload, editor, version diff, decisions);
- `docs/skills/README.md` rewritten (both kinds, the rules).

**Files.**
- `backend/skills/{skill_md,models,services,selectors,ports,views,urls,serializers,probes,match}.py`,
  `backend/skills/migrations/0001_instruction_skills.py`,
  `backend/skills/tests/{test_skill_md,test_bundles,test_lifecycle,test_views,test_match,test_no_capability,test_no_network}.py`,
  `backend/skills/tests/fixtures/` (valid and hostile SKILL.md files);
- `backend/assistant/{context,services,wake_routes}.py`,
  `backend/assistant/tests/test_skill_injection.py`;
- `backend/ai_core/migrations/00xx_skills_prompts_and_budget.py`,
  `backend/config/settings/base.py`, `.env.example`,
  `backend/pyproject.toml` (D19 skills contract);
- `frontend/src/app/(shell)/skills/…`, `frontend/src/components/skills/…`,
  vitest;
- `docs/skills/README.md`.

**Tests.**
- Parser: valid files; every refusal (`allowed-tools`, each
  permission-shaped key, unknown key, anchors/aliases/tags/flow/multi-line,
  oversize body, missing description, bad name, second document,
  invisible characters stripped); vectors in fixtures.
- Bundles: zip with a script, binary, dotfile, symlink, `..` path,
  absolute path, > 10 references, oversize reference, nested zip → whole
  upload refused, nothing stored.
- Lifecycle: proposed → approve (owner web only) → active; edit = new
  version, current stays; `version_changed` on hash drift; reject;
  archive/restore; no delete endpoint (405); no bulk endpoint; caps; audit
  rows carry ids and hashes only; immutable version (`save()` on an
  existing version raises).
- Match: no active skills → no call; one call with FakeAdapter; unknown
  name → none; budget refusal → none; `skill.<name>` route and `/skill`
  prefix skip the call.
- No capability (release gate): a hostile skill injected for every intent
  (web and voice) yields the same drafts, approvals, reads and tool
  calls as with no skill; the voice cap and the trust check unchanged.
- No network: the `skills` package imports no HTTP client and opens no
  socket (import scan + a socket guard during the suite).
- Workspace isolation of skills, versions, match candidates and routes.
- Role × endpoint for every skills endpoint.

**Done when:** an uploaded SKILL.md is approved on `/skills`, matched and
injected into a chat turn with its version in the context report, and
the hostile-skill gate is green.

### 6. `feat/p12-skill-curator` — stale marking, curator proposals, archive suggestions

**Scope.** D16:
- `skills/curator.py` (deterministic stale/active pass; one
  `skills.curate` call; strict output parser; proposals through
  `skills.ports`; archive suggestions as notices via the
  `skills.proposals` probe), `skills.SkillCuratorRun` (migration: counts,
  outcome, `ModelUsage` id), `skills.tasks.curate` (static beat entry,
  Sunday 04:15), run under `unattended_run(allowed_purposes={"skills.curate"})`;
- purpose `skills.curate` (tier, prompt, weekly cap);
- `/skills` proposals queue shows curator origin and the diff.

**Files.**
- `backend/skills/{curator,tasks}.py`, migration,
  `backend/skills/tests/{test_curator,test_curator_parser,test_curator_unattended}.py`;
- `backend/config/celery.py`, `backend/ai_core/migrations/…`,
  `backend/config/settings/base.py` (`SKILLS_STALE_DAYS`);
- frontend proposals queue updates.

**Tests.**
- Stale after `SKILLS_STALE_DAYS` without use, back to active on use,
  manual clock; no model call in the deterministic pass.
- Curator call: at most 3 proposals, closed action set, bodies through
  the parser (a proposal with `allowed-tools` dropped and counted), never
  applied, never approved, never archived by itself; cap of 5 pending
  curator proposals; budget refusal → skipped.
- Inside its run the curator cannot create an approval, task, memory,
  schedule, connector call or another skill's approval (D2 guard).
- No workspace with active skills → no call.

**Done when:** a week of fake usage produces stale marks and at most three
curator proposals that the owner can decide on `/skills`, with no
other effect.

### 7. `feat/p12-harness-playbooks` — playbook sources, distillation, provenance, reuse in harness jobs

**Scope.** D17, ADR-012 note:
- `skills.PlaybookSource` (migration), `skills.ports.record_playbook_source`;
- `harness` terminal hook calls it under the D17 conditions (beside
  `record_episode`, swallowed on error like the 8A producers);
- `skills/playbooks.py` (selection, one `skills.playbook_distill` call,
  provenance check, cap), nightly static beat entry;
- `HarnessImplementationJob.skill_version_ids` (migration), request
  serializer and Gate A display, runner mounts read-only
  `.claude/skills/<name>/` in the worktree;
- `/skills/<id>` shows each playbook step's source job.

**Files.**
- `backend/skills/{playbooks,ports,models}.py`, migration,
  `backend/skills/tests/{test_playbook_sources,test_playbook_distill,test_playbook_provenance}.py`;
- `backend/harness/{ingestion,services,serializers,implementation_execution,runner}.py`,
  migration, `backend/harness/tests/{test_playbook_hook,test_skill_mount}.py`;
- `backend/config/celery.py`, `backend/ai_core/migrations/…`,
  `docs/adr/ADR-012-continuous-learning-ingestion.md` (note);
- frontend: harness request form (skill picker), `/skills/<id>` provenance.

**Tests.**
- Source recorded only for `succeeded` + approve verdict + tests passed +
  owner-typed goal; never for failed/cancelled jobs, agent-typed goals,
  sensitive repos; harness state machine unaffected when the port raises.
- Distillation: below the minimum → no call; one call per eligible repo;
  a step with a quote from `context_text` (model-produced) dropped; a
  step with a quote not verbatim in its source dropped; < 2 valid steps →
  dropped and counted; weekly cap 3; draft passes the SKILL.md parser;
  `personal_data` by workspace class; budget refusal → skipped.
- Reuse: only active versions of the job's workspace attachable (other
  workspace → 400); Gate A display lists name + hash; the fake runner
  writes the files read-only under `.claude/skills/` and nowhere else;
  no skill file is ever executed (the runner never `exec`s inside the
  skills path; the container policy unchanged).
- Inside the distill run, every D2 effect is denied except the proposal
  write.

**Done when:** three fake successful jobs on one repo produce one
provenance-checked playbook proposal; once approved it can be attached to
a new implementation request and the fake runner mounts it.

### 8. `chore/p12-closeout` — preflight, e2e, runbooks, ADR notes, MASTER_PLAN, go/no-go record

**Scope.**
- `ops/preflight.py` `check_proactive` (heartbeat entry present on the
  worker beat; schedules' `next_run_at` not stale; last run per schedule
  not `failed`; `proactive.digest` tier/budget/prompt seeded; no probe
  slow), `check_n8n` (binding state, relay reachable when enabled, pins
  `ok`, `BRIDGE_ALLOWED_TOOLS` equal to the manifest; warn while not
  configured), `check_skills` (prompts/budgets seeded; no active skill
  failing the current parser — a parser tightening surfaces old
  versions); rows added to `CHECKS`;
- e2e `backend/ops/tests/test_reach_e2e.py`: heartbeat → digest →
  `/today` ack; reference host + `friday-voice run` + live server
  "günaydın" → digest spoken (playback double), "bildirimler" → notice
  titles; n8n via the in-process relay and the in-memory server: async
  read from a `node_voice` turn → follow-up + notice, write draft →
  web approval → one send; SKILL.md upload → approve → used in a turn;
  three fake jobs → playbook proposal; an unattended run that tries every
  D2 effect → all denied;
- runbooks `docs/runbooks/proactive.md`, `docs/runbooks/n8n-connector.md`,
  `docs/runbooks/skills.md`;
- ADR-016 / ADR-010 / ADR-015 closeout notes;
- `assistant/self_sources.json` (`build_self_sources --check`) for the
  new capabilities;
- MASTER_PLAN §AA row 12 Brief/Status; this brief's branch table and the
  go/no-go record.

**Files.** `backend/ops/preflight.py`, `backend/ops/tests/{test_preflight_reach,test_reach_e2e}.py`,
`docs/runbooks/{proactive,n8n-connector,skills}.md`, `docs/adr/…`,
`backend/assistant/self_sources.json`, `docs/MASTER_PLAN.md`, this brief.

**Tests.** The e2e above; preflight rows pass on the fake stack, warn
(not fail) for an unconfigured n8n; `build_self_sources --check`.

**Done when:** the full backend suite, vitest, import-linter and the
e2e are green, preflight passes on the fake stack with only the
documented warnings, and the MASTER_PLAN row and go/no-go record are
updated.

## Branch table

| # | Branch | Scope | Depends on | Status |
|---|---|---|---|---|
| brief | `docs/p12-brief` | This brief (full, from the stub; stub goal, scope, parent decisions and checkpoint kept), MASTER_PLAN §AA row 12 Brief/Status, `backend/assistant/self_sources.json` regenerated for the row's new status (`build_self_sources --check`) | Phase 11 closeout | **implemented 2026-11-20** (docs only; awaits the independent Sonnet 5 plan review to APPROVE) |
| 1 | `feat/p12-proactive-core` | `core.probes`, `core.unattended` + guards, `proactive` app (schedules, runs, notices, heartbeat), contracts, ADR-016 | brief approved | planned |
| 2 | `feat/p12-morning-digest` | Probes in owner apps, `DigestRun`, number-checked composition + template fallback, `/today`, notice bell, HUD `notices`, "Dinle", `proactive_checkpoint` | 1 | planned |
| 3 | `feat/p12-wake-routes` | Wake-phrase routes + aliases, `digest.today` / `digest.learning` / `notices.read` on web voice and `node_voice`, vectors, ADR-015 note | 2 | planned (on-Mac verification: deferred: Mac session, M1) |
| 4 | `feat/p12-n8n-connector` | n8n manifest + workflow-ID allowlist, relay sidecar mode, async reads + follow-ups, gated writes, probes, `tool.*` routes, ADR-010 amendment | 1 (3 for `tool.*` routes) | planned (live n8n pins: owner step) |
| 5 | `feat/p12-skills-core` | SKILL.md parser + refusals, staged lifecycle, matcher + injection, `/skills`, `skill.*` routes | 1 (3 for `skill.*` routes; parallel with 2–4) | planned |
| 6 | `feat/p12-skill-curator` | Stale marking, curator proposals, archive suggestions | 5 | planned |
| 7 | `feat/p12-harness-playbooks` | Playbook sources from harness, provenance-checked distillation, attach to implementation jobs, ADR-012 note | 5 (parallel with 6) | planned |
| 8 | `chore/p12-closeout` | Preflight rows, reach e2e, runbooks, ADR notes, `self_sources.json`, MASTER_PLAN row, go/no-go record | 1–7 | planned |
| M1 | Spoken digest and wake routes on the Mac | Real mic/STT/TTS run of `digest.today`, `notices.read`, `tool.*` drafts, barge-in during a long digest; Turkish route-phrase recognition | 3 merged; Phase 11 M1 | deferred: Mac session (pending owner evaluation) |

**Minimum usable cut** if the phase runs long: 1, 2 and 3 — the digest
exists, is spoken on the web and on the Mac on request, and the
checkpoint can be measured. 4 adds n8n; 5–7 add skills and playbooks.

## Mac session (deferred)

Phase 12 adds **no Swift code and no protocol surface**. The Mac task is
a verification run on the owner's MacBook, in a later Claude Code session
there, after the Phase 11 Mac session's M1 (the Swift voice host) has
merged.

**Preconditions:**
- Phase 11 M1 merged (the node spawns `friday-voice run`, push-to-talk and,
  if permitted, always-on work); Phase 11 M2 (speech server) or the
  Phase 6 speaches server running as gateway rows;
- branches 2 and 3 merged here (and 4 for the n8n items);
- a `morning_digest` schedule enabled for the personal workspace; at least
  one digest produced; two owner aliases set (`ev yedeği`, `sunucu durumu`)
  if branch 4 is merged.

### M1 — Spoken digest and wake routes on the Mac

**Create (on the Mac, outside the repo):** a results note only; the
owner's recordings never enter the repo or the database.

**Do:**
1. Say "Hey Friday, günaydın" (and "Friday, brifing") ten times each in a
   quiet room and five times with the TV on. Record: the route matched
   (`Message.route = digest.today` on the server), the first audio byte
   latency (target: p95 ≤ 2.5 s — no model call in the route), and
   whether the whole digest was spoken.
2. Interrupt a digest with "dur" three times: playback stops within the
   Phase 11 barge-in budget; the next utterance is handled normally.
3. "Hey Friday, bildirimler": notice titles are read; nothing is marked
   read or decided.
4. (Branch 4 merged) "Hey Friday, ev yedeğini başlat": a draft card only;
   approve it on screen; the completion notice is read back by
   "bildirimler". "Hey Friday, sunucu durumu": the async template reply,
   then the follow-up appears in the live conversation.
5. Turkish route-phrase recognition: of the utterances in step 1, ≥ 90 %
   must match their route after STT; record misses with their
   transcripts (text only) and, if needed, add owner aliases rather
   than fuzzy matching (D8).

**Contract it must satisfy (already enforced here):**
`backend/assistant/tests/test_wake_routes_node_voice.py` and the closeout
`backend/ops/tests/test_reach_e2e.py` green; `node/friday-voice/
conformance.lock` and `build_node_conformance --check` unchanged by
Phase 12.

**Record** the results in this brief ("Go/no-go record", M1 rows) on a
docs branch, reviewed like every brief change.

## Go/no-go record (Phase 12 checkpoint)

Stub checkpoint: "the owner keeps the morning digest on after two weeks".

| Item | How it is measured | Result |
|---|---|---|
| Digest kept on for 14 consecutive days | `manage.py proactive_checkpoint` (audited enable/disable history) | pending owner evaluation |
| Digest opened or spoken on ≥ 10 of those days | same command (`opened_at` / `spoken_at`) | pending owner evaluation |
| Unattended deny suite | Branch 1 guard tests + closeout e2e | to be recorded at closeout |
| Hostile-skill no-capability gate | Branch 5 release gate | to be recorded at closeout |
| n8n allowlist and pins against the owner's live n8n | `check_n8n` preflight on the real stack | pending owner evaluation (owner step 3) |
| Spoken digest and routes on the Mac | M1 | pending owner evaluation (Mac session) |

If the owner turns the digest off before two weeks, the closeout records
why (the disable is audited; the owner adds a sentence) and the digest is
re-scoped (sections, length, time) before anything is added to it. These
rows do not block the backlog.

## Out of scope (explicit)

- Any unattended action: no allow list, no "run this every morning"
  workflow, no unattended n8n call, no unattended send (D2; a later ADR).
- Sending the digest or notices anywhere outside Friday (Telegram, mail,
  push notifications): an external send of personal data (gate-class) that
  an unattended run cannot request.
- A server→node push, a menu-bar badge for notices, any new node or
  `friday-voice/v1` message (frozen sets).
- Multiple wake words or per-route wake models (D8).
- n8n's instance-level MCP, n8n calling Friday (webhooks into Friday,
  Friday as an MCP server), a firm-class n8n manifest, n8n agents.
- Public skill registries, URL imports, `allowed-tools`, skill scripts,
  any skill execution on a host or in web/worker (D13).
- Auto-approval of anything learned (skills, playbooks, curator output),
  bulk approval of skills (D14).
- Deleting skills or versions (archive only in this phase).
- Changing MVP 8's code-only plan catalog or `agent.skill_match`.
- Live connector reads inside the digest (it uses ingested calendar
  episodes; D1).
- Retrofitting `unattended_run` onto existing system tasks (D2 scope).

## Test strategy

**Discipline.** As Phases 2–11 (CLAUDE.md rules 5, 7, 8): ruff, ruff
format, pytest, vitest, import-linter, `makemigrations --check`,
`build_self_sources --check`, `build_node_conformance --check` and the
`friday-voice` CI job green per branch.

**What no test touches.** No real LLM, n8n instance, MCP server outside
the SDK's in-memory server, Google, Home Assistant, GitHub, speech
server, microphone, Mac or network beyond localhost. Tests use
FakeAdapter and the fake speech provider, `connectors/tests/fakes.py`
(an n8n-shaped `FakeServerState`) behind the in-process relay, the
Phase 11 reference host and the real `friday-voice` child against a live
test server (closeout), the fake harness runner, the manual clock, and
real Postgres and Redis.

**Release gates.**
- Unattended deny: every D2 effect denied inside a run, with its audit
  row; no Celery dispatch escapes.
- Cron cannot create cron: no path from a run, tool, intent, skill,
  connector or voice turn to a schedule or an alias.
- No capability from skills: identical decisions with and without a
  hostile skill.
- The ADR-015 cap with routes: a routed voice turn reads or proposes
  only; a `tool.*` route to a write yields a draft.
- Connector data stays data: n8n results (sync, async, write) never
  produce a draft, approval, read, memory or notice text.
- Number check: a digest with an invented number is never shown or
  spoken.
- Provenance: a playbook step quoted from model output never survives.
- Frozen protocols: node conformance and `friday-voice/v1` unchanged.

**Role × endpoint** for every new surface (schedules, notices, digests,
ack, wake routes and aliases, skills and versions, curator queue,
playbook provenance, the harness request's `skill_version_ids`, the
connectors binding for `n8n`) against the five Phase 4 principals,
`cli`/`openai`/`hook` PATs, a node secret, a `node_voice` token, a
revoked node's token, a kill-switched owner, and a second (firm)
workspace with a non-owner member.

**Release-gate regressions** (escalate per the CRITICAL/HIGH rule):
- a proactive run creating anything but its own run, digest and notice
  rows (or, for the curator and distiller, skill proposals);
- a schedule or alias created or changed by anything but the owner over
  the web;
- a model call from a `heartbeat` run, or from any run with a purpose
  outside its allowlist; a model call without a `ModelUsage` row;
- a digest or notice containing firm data in a personal workspace (or the
  reverse), or containing connector/tool/model text;
- an n8n tool outside the allowlist listed or called; the n8n bearer in
  web/worker env, a DB row, an audit row or a log line;
- an async follow-up making more than one completion or any tool call;
- a skill version used before the owner approved that exact hash; an
  uploaded script stored or executed; any outbound HTTP from `skills`;
- a skill changing a tool vocabulary, read channel, approval class or
  cap.

## Risks

- **R1 — Digest fatigue.** The checkpoint can fail on taste, not
  correctness. Mitigation: short (≤ 900 chars), quiet days produce
  nothing, sections omitted when empty, the owner edits time and days;
  the disable is audited and the reason recorded.
- **R2 — Hallucinated facts in the spoken digest.** Mitigation: facts
  fenced, the number check, the template fallback; names are not checked
  (residual: the model could garble a title; it can only use titles it
  was given).
- **R3 — n8n tool-to-workflow mapping lives in n8n.** Friday cannot see
  which workflow a `wf_X` tool node really calls (D10 residual). Mitigation:
  allowlist + pins + relay ceiling; writes gated with the exact arguments
  shown; the owner's n8n is trusted at the owner's level. A compromised
  n8n can lie in results (data only, fenced) and run its own workflows
  (outside Friday's boundary either way).
- **R4 — Skills as prompt injection with an approval stamp.** An approved
  skill steers the model. Mitigation: per-version owner approval by hash,
  no permission keys, the no-capability gate, the voice cap and the trust
  check unchanged. Residual: a badly written skill makes worse answers;
  the curator and archive address it.
- **R5 — Unattended guard coverage.** A new effect added later without a
  guard. Mitigation: the guard test walks every registered `ToolSpec` /
  `ReadToolSpec` and the D2 table; new services that write business rows
  are expected to add the guard (review checklist line in the runbook).
- **R6 — Async follow-ups to stale conversations.** A result arriving
  after the owner moved on. Mitigation: the follow-up is a message plus
  a notice, never a voice push; it is ignored if the conversation was
  trashed.
- **R7 — n8n licence.** n8n is "Sustainable Use" (fair-code); running it
  for personal automations is the intended use; Friday does not
  redistribute it (the relay image contains no n8n code). Recorded in
  `docs/mcp/n8n.md` (owner step 4).
- **R8 — Mac work slips.** Everything is tested here through the
  reference host; M1 is verification only and does not block the backlog.

## Open questions (with recommended answers)

- **Q1 — Should the digest be pushed to the Mac at digest time?** No.
  It would need a server→node message or a child listener, both against
  the frozen-protocol defence. "Günaydın" is one phrase away.
- **Q2 — Should heartbeat notices use a model to prioritize?** No (D4):
  ordering by a fixed section order is predictable and free; a model
  ranking can be a later experiment behind a flag.
- **Q3 — Should the owner be able to allow one unattended action (e.g.
  "start the backup every night")?** Not in this phase. n8n can schedule
  its own workflows; Friday announces their results. A Friday-side
  unattended allow list needs its own ADR and threat model.
- **Q4 — Should SKILL.md skills replace MVP 8's plan catalog?** No. The
  plan catalog names tools and steps (capability); instruction skills are
  text. Merging them would make a row able to name a tool step.
- **Q5 — Should playbooks also come from Claude Code hook sessions?** Not
  yet: hook events are metadata only (8A) and transcripts are owner
  turns only (8B); neither has a verified success signal like a
  succeeded, reviewed harness job.

## Owner steps (pending) — recorded 2026-11-20

The stub recorded no owner steps. These cannot happen in a cloud session
(they need the owner's Mac, accounts or real services):

1. Before branch 1: have the independent Sonnet 5 plan review of this
   brief reach APPROVE; review and accept ADR-016 when branch 1 lands.
2. After branch 2: enable the `morning_digest` schedule on `/today`
   (time, days); the two-week checkpoint starts that day.
3. After branch 4: run an n8n instance the `n8n-relay` sidecar can reach,
   import `deploy/n8n/friday-tools.workflow.json`, create the MCP Server
   Trigger's bearer credential and put it only in the sidecar env
   (`RELAY_BEARER_TOKEN`), set `RELAY_UPSTREAM_URL`; choose the workflows
   to allowlist and add each in a reviewed commit with its live schema
   pin (`check_n8n` must pass on the real stack).
4. Confirm n8n's licence terms for personal use and record the result in
   `docs/mcp/n8n.md`.
5. After branch 5: write or upload the first SKILL.md skills and decide
   curator and playbook proposals as they arrive.
6. Mac session M1 after Phase 11 M1.
7. After two weeks: run `manage.py proactive_checkpoint` and record the
   go/no-go rows.

### Owner steps (pending) — `docs/p12-brief`, recorded 2026-11-20

1. Start the independent Sonnet 5 plan review of this brief; branch 1
   does not start before APPROVE.
2. Confirm the defaults this brief proposes or change them in the review:
   digest length 900 chars, notice retention 30 days, `proactive` budget
   3 USD/month, `skills` budget 5 USD/month, stale after 45 days,
   playbook minimum 3 sources and 3 proposals per week.
