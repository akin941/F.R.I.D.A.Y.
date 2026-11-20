# Phase 5 — Deploy gate, connector framework (MCP), tier routing

> Status: **Approved (2026-09-27) — Sonnet 5 plan-review round 3
> APPROVE after two REVISE rounds (see "Plan-review rounds"; Codex CLI
> is retired)**. Precondition met 2026-09-27 (Phase 4 merged to `main`,
> former repository, PR #27); step-0 spikes recorded below.
> Parent decisions: ADR-001, ADR-002 (import contracts), ADR-003
> (single gateway door, ModelUsage), ADR-004 + ADR-008 decision 3
> (gate-class vs confirm-class: every deploy, external send and git
> push/merge is gate-class), ADR-005 (`private` contract), ADR-006 (workplace
> products are external installations; Friday is only their MCP client), ADR-008 decisions 4
> (`work_log` producers), 6 (bridges, never drivers) and 7 (runners),
> ADR-009 (Phase 4: no confirm-class growth by precedent),
> `docs/VISION_FRIDAY_2.md` §4 steps [5]–[7], §6 row 5, §8 (R1–R3, O-5),
> MASTER_PLAN §AA ("Phase 4–6 outline", "Carried-forward items") and §O
> (HA bridge, external-installation bridge, NotebookLM), `docs/security/mcp-governance.md`,
> `docs/mcp/allowlist.md`.
> **Precondition — hard:** Phase 4 closeout merged (`integ/phase-4` →
> `main`). State when this brief was written (2026-09-27): only Phase 4
> **branch 1** (`feat/p4-security-baseline`: fail-closed throttles,
> `ApprovalRequest.requested_channel`, the `is_agent` refusal, private
> vault tree) is on `integ/phase-4`. Phase 5 depends on symbols that
> land later in Phase 4 and **no Phase 5 branch is cut before they are
> on `main`**: branch 2 (`feat/p4-device-trust`: `WebSessionPrincipal`,
> default-deny `request_channel()`, `core.permissions.IsWebChannel`,
> `User.is_owner`, device pairing — every `CHANNEL_WEB`/owner-only gate
> below), branch 3 (TanStack Query layer), branch 5 (approvals inbox
> with per-tool renderers, extended by Phase 5 branch 9) and branch 6
> (facade `allowed_intents`, D4). Phase 5 branches are cut from `main`
> and integrate on `integ/phase-5`.

## Goal / value

Close the loop VISION §4 promised — request → briefing → Gate A → code
→ review → Gate B → **PR → merge → deploy gate → live** — and give the
assistant its first outside-world reach under the same kernel rules. A
deploy target allowlist, the third owner decision and an allowlisted SSH
runner with health check and rollback; a connector framework (manifested
MCP servers plus native bridges) with audited, fenced reads and gated,
snapshot-backed writes; GitHub, Home Assistant, Google mail/calendar and
an external-installation MCP client placeholder (O-5); the tier map and harness policy table; the
remaining `work_log` producers; the engine-extraction decision. Usable
outcome (MASTER_PLAN §AA): "Request → code → live, end to end", plus home
control and mail/calendar awareness.

## Boundary vs existing systems

- **The approval gate keeps its shape.** Every new write is a registered
  `gated=True` `ToolSpec` (`backend/tools/registry.py:27`) decided via
  `approvals.services.approve_request` over `CHANNEL_WEB` only. One
  service change: the system-only check in `create_request` (today
  `origin != HARNESS`, `integ/phase-4:backend/approvals/services.py:301`)
  becomes a keyword-only `system_created=True` passed only by owning
  services (harness Gates A/B, `github_open_pr`, `deploy`), never by a
  view. `origin` keeps meaning "who produced the request": the owner
  clicking "Deploy" is `HUMAN` although the gate is system-created.
- **No new confirm-class action.** Deploys, PRs, HA service calls, mail
  sends and calendar writes touch external systems → gate-class.
  Automatic rollback is raised explicitly against ADR-008 decision 3's
  clause that a decision is "never implied by the action that created
  the request" (can one decision cover a later automatic mutation?) and resolved by extension in ADR-010, the way HA/mail
  gate-class is: see D10 "Rollback is part of the decided plan".
- **`integrations` stays the channels app** (CLI, Telegram, facade).
  Bridges live in a new kernel app `connectors`, deploys in a new kernel
  app `deploys` (ADR-010). **`harness` stays a leaf**
  (`backend/pyproject.toml:348`): `harness` imports `connectors`; neither
  new app imports `harness` (PR executions link back via an opaque
  `origin_ref`).
- **`Workspace.profile.deploy_targets` stays dossier metadata**
  (`workspaces/serializers.py:73`, "metadata only"). A target becomes
  actionable only as an explicit, owner-gated `DeployTarget` row — the
  split `HarnessRepo` keeps from `profile.repos[]`
  (`harness/models.py:1-12`).
- **The assistant keeps its router architecture.** No autonomous
  tool-calling loop exists (intents: `assistant/models.py:75`) and none is
  added: the router picks at most one read per turn, Friday executes it,
  and the result enters the chat prompt fenced as low-trust data — the
  pattern of `render_knowledge_block` (`knowledge/context.py:173`) and
  the harness transcripts (`neutralize_fence`/`strip_invisible_formatting`,
  `memory/context.py:341,359`). Connector calls are not LLM calls; the
  router/argument/chat calls around them go through `InferenceGateway`
  and write `ModelUsage` as today; MCP `sampling` is never offered.
  Credentials stay env-only (CLAUDE.md) — never in Postgres, a payload,
  an audit row or a log line.

## Decisions (with justification)

### D1 — MCP transport: Streamable HTTP only; servers run as sidecars

A stdio server is a subprocess of whoever spawns it — in web/worker
that means third-party code beside `DJANGO_SECRET_KEY`, the database
credentials and every provider key. So Friday's client speaks **only
Streamable HTTP**, and each server runs as its own compose sidecar
(`deploy/docker/compose.yml` profile `connectors`, and
`compose.daily.yml`): digest-pinned image, `read_only`, `cap_drop: [ALL]`,
`no-new-privileges`, no published ports, reachable over an internal
`connectors` network, egress only where needed, holding only its own
credential. A stdio-only server runs inside its sidecar behind our
stdio↔HTTP bridge (`backend/connectors/docker/stdio_bridge.py`, same
SDK) — never in-process, never on the harness runner. The client
connects only to manifest-named env URLs and follows no redirects.

### D2 — Adopt the official `mcp` Python SDK (new dependency)

VISION §0: a mature tool beats a rewrite. MCP has shipped several
revisions (capability negotiation, session ids, protocol-version
headers, JSON or SSE responses, pagination, OAuth); a hand-rolled client
tracks them alone. The SDK is official and moves with the spec. Its
costs are contained: it is async while Django/Celery are sync → one sync
facade `connectors/mcp_client.py` running each operation in `anyio.run()`
(never inside a running loop). Call sites are fixed and all sync: reads
run in the chat request thread (sync DRF view under gunicorn gthread
workers; no ASGI in the stack), writes, pin checks and health checks in
Celery tasks (prefork, sync). The facade asserts no running event loop
(`asyncio.get_running_loop()` must raise) and fails closed with
`ConnectorError("event_loop_active")` rather than nesting. The SDK's
other costs: it pulls server-side packages (starlette,
uvicorn) → production code may import only `mcp.client.*`/`mcp.types`,
enforced by an import-linter contract forbidding `mcp.server` (tests use
the SDK's in-memory server as a fixture); it moves fast → pinned minor
version, Dependabot, negotiated `protocolVersion` recorded per binding.
`httpx` (transitive today via `anthropic`/`openai`) becomes a direct
dependency for native bridges. No `jsonschema`: Friday-side argument
schemas are strict DRF serializers, as `workspaces/serializers.py` does.

### D3 — Framework shape: manifest-as-code + DB bindings + pins

- **Manifests live in git** (the `skills` "git-approved, DB-less"
  pattern): `backend/connectors/manifests/<name>.py`, a
  `ConnectorManifest` — kind `mcp`|`native`, status `active`|`blocked`,
  `data_class` `firm`|`personal`, allowed workspace types, read channels,
  env var names (URL, credential), sidecar digest, and per tool a
  `ToolPolicy`: read|write, Friday-authored name/description, argument
  serializer, scope check, server `inputSchema` sha256 pin, gate name,
  `user_invocable`, renderer. Each has a governance twin
  `docs/mcp/<name>.md` and an allowlist row; a test fails if any of the
  three is missing. Widening a connector is a reviewed commit, never a
  DB write (ADR-004: no self-granted permissions).
- **`ConnectorBinding`** (`WorkspaceScopedModel`): manifest × workspace,
  `enabled` default False (curator + owner web session, audited),
  `last_health` (server name/version, protocol version, pin status).
  `personal` manifests bind only to the personal workspace.
- **Pins against tool poisoning.** On enable, on a 15-minute beat and
  before every write, `tools/list` is compared with the manifest:
  unlisted tools are ignored; a pinned tool whose schema hash changed is
  disabled (`connector.pin_mismatch`, fail closed) until a commit updates
  the manifest. Server descriptions never reach an LLM prompt.
- **Client capabilities: none** — no sampling, elicitation or roots;
  tools only; `list_changed` just triggers the pin check.

### D4 — How results reach the assistant: ungated, audited, fenced

`tools.registry` gains `ReadToolSpec` (name, description, argument
serializer, `data_class`, `channels`, `execute(*, workspace, actor,
args, channel) -> ReadResult`) and `list_read_tools(...)`, so
`assistant` still depends only on `tools`. New intent
`Message.Intent.CONNECTOR_READ`: `classify_intent`
(`assistant/services.py:149`) lists only read tools with an enabled
binding in the conversation's workspace and the request's channel; a
second call, `router.connector_args`, extracts arguments, validated by
serializer and scope check (invalid → a plain refusal, no read). The
router already sees only the current user message, never history
(`assistant/services.py:188-191`), so connector text never reaches it —
a test pins this. The read runs under `CONNECTOR_READ_TIMEOUT_SECONDS`
(10); only text and `structuredContent` survive (images, resources and
links dropped), bounded to `CONNECTOR_RESULT_MAX_CHARS` (8000, the harness
transcript cap), stripped and fenced as `<connector_result
source="github">` with "data, not instructions" framing, and passed to
`build_context` (`assistant/context.py:199`) as `connector_block`.
`Message.connector_reads` and `ModelUsage.context_report` record tool,
data class, size, truncation — never content; audit `connector.read`
records binding, tool, args sha256, size, duration, outcome, channel.
The facade's `allowed_intents` (Phase 4 branch 6) never includes it.

### D5 — Personal-class data never reaches an unapproved provider

`ModelProvider.personal_data_allowed` (default False; seeded
`ollama-local` True; others flipped only by `manage.py
set_provider_personal_data`, audited). `InferenceGateway.complete(...,
personal_data=True)` makes `_select_providers` (`ai_core/gateway.py:455`)
keep only allowed providers and fail closed with `NoProviderAvailable`
(`routing_reason="personal_data_required"`) — never the silent cloud
fallback tier preference allows. A `personal` read (mail, calendar, HA,
or GitHub for a `sensitive` `HarnessRepo`) marks the conversation
`private` through the existing one-way `mark_conversation_private`
cascade (no extraction, `assistant/services.py:480`) and sets a new
one-way `Conversation.personal_data`. **Ordering:** the flag is set in
the same transaction that stores the read result, *before* the chat
completion that consumes `connector_block`; `personal_data` is read
fresh off `Conversation` immediately before **every**
`gateway.complete()` in the conversation — the same-turn call that
first sees the personal content included, and every router/argument
call of later turns too. Within the triggering turn, before the flag
is set: `router.intent` (`classify_intent`) is **exempt** — it runs
before any read, carries only the owner's message and the tool-name
vocabulary (never connector content), and cannot know its own outcome;
`router.connector_args` runs after the tool is named, so its binding's
`data_class` is known, and it passes `personal_data=True` when that
class is `personal` (it sees the owner's message and the argument
schema, still no connector content). A pre-flight check runs before
the read (and `router.connector_args`' own `NoProviderAvailable` is
caught and mapped to the same outcome): with no allowed provider the
read does not happen, the
flag is not set, and the reply says a local model is required.

### D6 — Writes: gated, snapshot-backed, at-most-once

Each manifest write tool registers a `ToolSpec` (e.g. `ha_call_service`;
generic default `mcp_<connector>_<tool>`). `validate_request` (tier 1, no
network) checks binding, pin and arguments; the payload snapshots
normalized arguments, binding id, manifest version, schema pin and all
display fields. `describe_request` builds `display_summary` from the
payload only — the tool's renderer, or a generic fallback listing every
argument in full. `create_execution` re-checks binding and pin (changed →
`connector_changed`) and creates a lease-fenced `ConnectorWriteExecution`
(mirrors `VaultExportRun`, `tools/models.py`); the task sends exactly the
snapshotted arguments. **Never retried automatically**: a worker lost
mid-call leaves `unknown_outcome`, shown to the owner, never re-sent.
Results are sanitized bounded JSON; errors a closed vocabulary. Chat may
propose user-invocable writes via the existing `tool_request` intent.

### D7 — GitHub through the official GitHub MCP server

First MCP consumer. Sidecar: `github/github-mcp-server`, digest-pinned,
toolsets limited to repos, pull requests and actions. Credential: a
fine-grained PAT over the `HarnessRepo`-registered repos only
(`contents:read`, `pull_requests:write`, `actions:read`,
`metadata:read`). Three layers: server toolsets, Friday manifest (the
only write is `create_pull_request`; merge, file, issue and branch
mutations are unlisted, hence unreachable), PAT scope. Argument scope:
every call must name an `owner/repo` that canonicalizes
(`core/repo_identity.py`, moved from `harness/repo_identity.py:25,86`) to
a `HarnessRepo` of the calling workspace — firm A never reads firm B. MCP
rather than direct REST because the reads ("what is CI doing on ADİA?")
are the open-ended surface the framework exists for; the write is one
pinned tool.

### D8 — Home Assistant: native REST API, not HA's MCP server

HA's MCP server exposes intent tools (`HassTurnOn` by name/area)
resolved inside HA after the decision, so the dialog could not say which
entity changes. The REST API is exact: `GET /api/states[/<entity_id>]`,
`POST /api/services/<domain>/<service>` with an explicit `entity_id`
(no WebSocket: no subscriptions needed). The bridge
(`connectors/bridges/home_assistant.py`, `personal`, personal workspace
only) enforces a code-defined maximum that `HA_ALLOWED_DOMAINS` may only
narrow: `light` (`turn_on` with bounded `brightness_pct`/
`color_temp_kelvin`, `turn_off`), `switch`, `fan`, `cover`, `climate`
(`set_temperature` 16–30, `set_hvac_mode` enum), `media_player`
(play/pause, volume 0–1), `scene.turn_on`,
`wake_on_lan.send_magic_packet` (MAC ∈ `HA_WOL_ALLOWED_MACS`). Never:
`script`, `automation`, `shell_command`, `rest_command`, `homeassistant`,
`hassio`, `lock`, `alarm_control_panel`. Every call is gate-class
(`ha_call_service`, user-invocable); the display shows entity id,
friendly name (labelled HA-supplied text), service, data and the state
read at request time; the state is read back after execution.

### D9 — Mail/calendar: Google only, native REST, host-side owner OAuth

Google-only (Gmail + Calendar APIs), not IMAP/SMTP/CalDAV: Gmail IMAP
needs OAuth (XOAUTH2) anyway, so "generic" means Google OAuth *plus*
MIME, SMTP and iCalendar/RRULE handling for no second provider;
`MailReader`/`CalendarReader` protocols leave room for an adapter later.
No Google SDK: ~six REST endpoints over `httpx`. `manage.py connect_google` runs
the installed-app flow with PKCE on a `127.0.0.1` loopback redirect on
the host — no callback route through the BFF; host shell is the root of
trust, as for Phase 4's `issue_pairing_code`. It prints the refresh token
once for `.env` (`GOOGLE_OAUTH_CLIENT_ID`/`_CLIENT_SECRET`/
`_REFRESH_TOKEN`); access tokens live only in process memory. Granted
scopes are checked against the manifest at startup and on the health
beat; an extra scope disables the connector. Read scopes first
(`gmail.readonly`, `calendar.readonly`); `gmail.send`/`calendar.events`
only after a re-consent, so send capability cannot exist before the owner
grants it. Reads: `mail_search` (≤20: from, subject, date, snippet),
`mail_read` (bounded text body), `calendar_agenda` (≤31 days). Writes,
gate-class and user-invocable: `mail_send` (≤10 To/Cc, subject, text
body, optional thread; display shows raw addresses, never display names;
payload keeps a body sha256) and `calendar_create_event` (≤20 attendees;
the dialog states that Google will send invitations).

### D10 — Deploy: target allowlist, deploy gate, forced-command SSH runner

- **`DeployTarget`** (`deploys`): name, `environment`, `ssh_host`,
  `ssh_port`, `ssh_user`, `host_public_key` (pinned; per-run
  `known_hosts`, `StrictHostKeyChecking=yes`, no trust on first use),
  `remote_app` (`[a-z0-9-]+`), `repo_git_url`, `tracked_branch`,
  `health_url` (optional), `health_timeout_seconds`, `require_ci_success`
  (default True), `enabled` (default False), `current_sha`,
  `config_hash`. Create/edit: owner, web only.
- **Allowlist on both ends.** Friday sends only `<verb> <remote_app>
  [<sha>]`, verbs `status|deploy|health|rollback`, SHA 40-hex. On the VPS
  the key is `command="/opt/friday-deploy/friday-deploy.sh",restrict` in
  `authorized_keys`; the script (`deploy/remote/friday-deploy.sh`,
  versioned here) parses `SSH_ORIGINAL_COMMAND` strictly and runs only
  root-owned per-app config (`/etc/friday-deploy/apps/<app>.conf`: app
  dir, tracked branch, build/restart and local health commands). A
  compromised runner gets four verbs, not a shell; build commands are not
  Friday's to change.
- **Only merged commits deploy:** `deploy` requires `git merge-base
  --is-ancestor <sha> origin/<tracked_branch>`; Friday never merges.
  `rollback` accepts only the script's recorded previous SHA.
- **SSH client:** system OpenSSH, argv only (`-F /dev/null`,
  `-i $DEPLOY_SSH_KEY_PATH`, `IdentitiesOnly`, `BatchMode`, timeouts),
  sanitized env (no `SSH_AUTH_SOCK`), missing key → fail closed — the
  discipline of `harness/runner.py:163` (`_resolve_git_push_env`) and
  `:79` (`_ISOLATED_GIT_ENV`). No paramiko/asyncssh.
- **Where it runs:** `deploys.tasks.*` → `DEPLOY_CELERY_QUEUE` beside
  `HARNESS_CELERY_QUEUE` (`config/settings/base.py:130-131`), consumed by
  the existing runner (`Makefile:85`, `-Q harness,deploy`), so the key
  lives only on the runner host like `HARNESS_GIT_SSH_KEY_PATH`.
- **Gate `deploy` (system-created).** `deploys.services.request_deploy`
  (owner; web or CLI) snapshots target fields + `config_hash`, SHA, commit
  title (labelled repo text), CI conclusion at request time (refused if
  required and not `success`), `expected_current_sha`, `rollback_sha`,
  health plan, verb sequence. Display: "Deploy `a1b2c3d` to ADİA
  production (deploy@203.0.113.7, app `adia-web`). Current `9f8e7d6`. If
  health fails within 120 s, Friday rolls back to `9f8e7d6`."
  `create_execution` refuses if `config_hash` changed — the lesson of
  `HarnessImplementationJob.repo_git_url`'s snapshot
  (`harness/models.py:281-300`) — and refuses with a named, audited
  `target_busy` if the target has an active `DeployRun` (two SHAs
  approved back-to-back, a double click). The partial unique constraint
  is the backstop: its `IntegrityError` is caught in `create_execution`
  and mapped to the same `target_busy` refusal, never a 500. A second
  pending `deploy` request for the same target is allowed to exist (the
  owner may change their mind), but approving it while a run is active
  fails as above; approving it after the first run finished fails the
  drift check unless `expected_current_sha` still holds.
- **Run (`DeployRun`, lease-fenced, one active per target by partial
  unique constraint):** `status` first — remote repo must canonicalize to
  the target's and not to Friday's own (`is_self_repo`,
  `HARNESS_SELF_REPO_IDENTIFIERS`); remote SHA must equal
  `expected_current_sha` (else `target_drifted`, nothing touched) →
  `deploy` → health (VPS `health` verb + optional `health_url` GET, two
  consecutive passes) → `succeeded` (sets `current_sha`), or `rollback` +
  health → `rolled_back` | `rollback_failed` (audit, UI banner,
  `work_log`). Cancel only before the `deploy` verb; a runner lost
  mid-deploy leaves `unknown_outcome`, never retried (the next request's
  drift check covers it). Output bounded (16 KB), redacted, low-trust.
- **Rollback is part of the decided plan (ADR-010, extending ADR-008
  decision 3).** Rollback is a second live mutation, so the brief does
  not assume it is covered; ADR-010 records why one decision may cover
  it: (a) it can only restore `rollback_sha` — the SHA the owner
  approved in the previous deploy and that the drift check proved is
  live — never an arbitrary state; (b) the dialog states the rollback
  SHA, the health checks and the window verbatim, so the owner decides
  the whole plan; (c) it is bounded to the health window of this run; a
  later rollback is always a new `deploy` request. False negatives are
  bounded too: rollback triggers only after **three consecutive**
  health failures spaced across the window (a single blip never rolls
  back), and a failed `health_url` alone triggers nothing when the VPS
  `health` verb passes — it marks the run `succeeded_degraded` with a
  banner. `DeployTarget.auto_rollback` (default True) is snapshotted
  into the payload. Health is decided fail-closed: a window that elapses
  without two consecutive passes counts as a failure (an alternating
  pass/fail pattern never ends in `succeeded`). With `auto_rollback`
  False, a failed health check ends in
  `health_failed` (banner, work_log) and nothing is rolled back without
  a new decision. Any other growth of what one deploy decision covers
  needs a new ADR.

### D11 — Tier-routing map and harness job-distribution policy

`AI_PURPOSE_TIERS` (`config/settings/base.py:172`, empty today) stays a
settings dict (phase-0 decision, `ai_core/models.py:49`) and follows
`docs/cost/token-optimization.md` layer 4:

| Tier | Purposes |
|---|---|
| cheap | `router.intent`, `router.task_draft`, `router.connector_args`, `agent.skill_match`, `harness.briefing`, `harness.diff_summary`, `harness.review_summary`, `facade.ephemeral` |
| mid | `chat`, `memory.extraction`, `agent.read_step` |
| strong | `agent.plan` |

`harness.review` is shared today by the gateway summary
(`review_execution.py:458`) and the CLI usage rows (`:724`, `:782`); the
gateway call becomes `harness.review_summary` (new prompt row) so routing
and cost separate. Historical rows keep `harness.review` for both;
`docs/cost/token-optimization.md` gains a dated note that from branch 2
on `harness.review` means external-CLI cost only and the summary cost is
`harness.review_summary` (sum both for a pre/post comparison). A CI test collects every `purpose=` literal in
production code and fails unless it is mapped or in `UNTIERED_PURPOSES`
(embeddings, external usage). `seed_ai_defaults` gains default
`ModelTier` rows for providers it already seeds (`ollama-local`,
`nvidia-nim`, `deepseek` → cheap; `openai-primary` → mid;
`anthropic-primary` → mid, strong).

Harness agents get `HARNESS_AGENT_POLICY` (settings), replacing
`_SELECTABLE_*_AGENTS` (`harness/services.py:53-62`) and feeding
`select_review_agent` (`review_execution.py:62`):

| Job kind | Default | Owner-selectable | Reviewer |
|---|---|---|---|
| discovery | `claude_code` | `claude_code` | — |
| implementation | `claude_code` | `claude_code` (`codex_cli` slot, off) | `codex_cli` |
| review | from pairing | — | never the implementer |

A Django system check fails boot on any self-pairing (ADR-008 decision
3). Job rows gain `agent_routing_reason` (`policy:default` |
`owner_selected` | `policy:cross_vendor`); `record_external_usage`
(`ai_core/gateway.py:42`, `routing_reason=""` fixed at `:84`) gains a
`routing_reason` argument, so external and in-process rows both explain
themselves.

### D12 — Shared harness execution engine: extract the leaf layer only

The three-call-site trigger is met. Side by side in `harness/`:
`_teardown_container`/`_safe_remove_container` are identical three
times; the brake loops (`execution.py:308`,
`implementation_execution.py:184`, `review_execution.py:293`) differ only
in stop predicate, transcript capture and a post-exit cancel re-check;
cleanup differs on purpose (confirmed, `implementation_execution.py:158`
vs best-effort, `review_execution.py:268`); claim/finalize/recovery
differ in substance (one phase vs two gates + worktree retention + push
fence vs a job-wanted predicate). **Decision:** extract only the leaf
layer into `harness/container_lifecycle.py` (teardown, remove,
`cleanup_directory(confirm=)`, `wait_for_container(...) -> WaitOutcome`
parameterized by those three differences); each caller maps
`WaitOutcome` to its own `FailureReason`. Calls go through the
`harness.runner` module attribute so existing monkeypatch seams hold.

### D13 — Remaining `work_log` producers (ADR-008 decision 4)

`tools.work_log.record_tool_outcome(approval_request, *, outcome,
summary, sensitivity)` wraps `memory.services.record_work_log`
(`memory/services.py:224`, still the only creation path) with
Friday-authored bookkeeping — never connector content or payload free
text beyond owner-authored titles. Producers: vault export finalize
(`tools/export.py:163,208`), terminal `ConnectorWriteExecution`s,
terminal `DeployRun`s (rollbacks included), and task outcomes
(`tasks/services.py:169` complete, `:187` cancel). `ToolSpec` gains
`records_work_log=True`; `ha_call_service` sets False — a light toggle is
audit-worthy, not memory-worthy (VISION R5). Content carries the date so
a repeated outcome is not swallowed as `DuplicateMemory`. MVP 7 agent
runs are not added (ADR-008's list does not name them).

### D14 — External-installation MCP client placeholder (O-5); no dynamic runner registry

`connectors/manifests/external_installation.py` ships `status="blocked"`
(company workspaces, reads only, `firm`); `docs/mcp/external_installation.md`
records the open auth model and approval treatment (§O). A blocked manifest
can never bind (tested); a later brief flips it when an external
installation exposes MCP. The deploy
queue rides the one existing runner; the dynamic registry waits for a
second runner host.

## ADR-010 (branch 1)

"Phase 5 — connectors, deploys, MCP client": the two kernel apps and
contracts (`connectors`/`deploys` may import `core`, `workspaces`,
`audit`, `ai_core`, `tools`, `memory`; `deploys` may import `connectors`;
`harness` may import `connectors`; `assistant` reaches reads only via
`tools.registry`; `memory`, `tools`, `approvals`, `workspaces`,
`ai_core` must not import either); the SDK adoption (D1–D2); connector
data as low-trust data and the personal-data provider rule (D4–D5);
deploy-gate semantics (D10), including the explicit extension of
ADR-008 decision 3 that lets one deploy decision cover a bounded
automatic rollback (previous SHA only, stated window, three consecutive
failures, per-target opt-out); `system_created`. It moves nothing
between gate-class and confirm-class.

## Scope (one branch each)

### 1. `feat/p5-kernel-hooks` — ADR-010, gate plumbing, work_log producers

**Scope.** ADR-010 + ADR-002 kernel-list note + CLAUDE.md rule-4
pointer; `create_request(..., system_created=False)` replacing the
origin check (harness gates pass it); `ToolSpec.records_work_log`;
`ReadToolSpec` + `register_read_tool`/`get_read_tool`/`list_read_tools`;
`tools/work_log.py`; vault-export and task producers (D13);
`core/repo_identity.py` (pure functions moved from `harness`);
empty `connectors`/`deploys` apps registered so contracts bite from day
one. **Files.** `docs/adr/ADR-010-connectors-deploys-mcp.md`,
`backend/approvals/services.py`, `backend/tools/{registry,work_log,
export}.py`, `backend/tasks/services.py`, `backend/core/repo_identity.py`,
`backend/harness/{services,repo_identity}.py`, `backend/pyproject.toml`,
`config/settings/base.py`.
**Tests.** A `user_invocable=False` tool refused from `POST /approvals`
for every origin; **load-bearing invariant:** `system_created=True` is
the only thing separating system gates from user requests, so a test
pins that the HTTP view passes `system_created=False` unconditionally
(no request field, header or origin value can flip it) and an
import-scan test fails if any module under `*/views.py` or
`*/serializers.py` passes `system_created=True`; harness gates still
minted; registry filters; work_log
rows (bookkeeping only, `memory.system_recorded` audit) for vault export
success/failure and task complete/cancel; contracts green.
**Done when:** ADR-010 accepted; existing gates behave identically.

### 2. `feat/p5-tier-routing` — D5 + D11

**Scope.** `personal_data_allowed`, `personal_data=` kwarg,
`set_provider_personal_data`; tier map, `harness.review_summary`,
purpose-coverage test, tier seeds, `HARNESS_AGENT_POLICY` + system check,
`agent_routing_reason`, `record_external_usage(routing_reason=)`.
**Files.** `backend/ai_core/{models,gateway}.py` + migration,
`ai_core/management/commands/`, `config/settings/base.py`,
`backend/harness/{services,models,execution,implementation_execution,
review_execution}.py` + migration, `.env.example` (commented defaults),
`docs/cost/token-optimization.md` (dated purpose-split note).
**Tests.** Tier ordering and `routing_reason=tier:<name>`;
`personal_data=True` with only cloud providers → `NoProviderAvailable`,
a `ModelUsage` row `error_type="no_provider"`, FakeAdapter call count 0;
purpose coverage; self-pairing check; reasons on job and external rows.
**Done when:** every production purpose is mapped or explicitly
untiered; no path reaches a non-allowed provider with `personal_data`.

### 3. `refactor/p5-harness-lifecycle` — D12

**Scope.** `harness/container_lifecycle.py`; the three modules call it.
**Tests.** The existing harness suite (`backend/harness/tests/`, 18
modules) passes with no assertion edits; unit tests for
`wait_for_container` (timeout, stop predicate, post-exit recheck, `None`
exit code fails closed, transcript on/off).
**Done when:** the reviewer confirms each call site maps to its old
`FailureReason`; `make harness-smoke` passes.

### 4. `feat/p5-connector-framework` — `connectors` app, MCP client, read/write paths

**Step 0 — transport spike, recorded in this brief before code:** does
`github-mcp-server` offer a local HTTP mode (else our stdio bridge;
GitHub's hosted endpoint only as fallback); its current tool names and
`inputSchema`s to pin (it has renamed/consolidated tools before); does it
take the PAT from its own env or per request (either way env-only).
**Scope.** `ConnectorBinding`, `ConnectorWriteExecution`, manifests and
`ToolPolicy`; `mcp_client.py` (D2); pin check + health beat; read executor
(D4); `CONNECTOR_READ` intent + `router.connector_args` prompt;
`build_context(connector_block=)`; `Message.connector_reads`,
`Conversation.personal_data` (D5 wiring); generic write `ToolSpec`
factory (D6) with recovery and work_log; error redaction; binding API
(`/api/v1/connectors/bindings`, `IsWebChannel` + owner, web only);
compose network/profile; external-installation blocked manifest; `mcp-governance.md` gains
the code-manifest and pin rules and the concrete logging design
(replacing its 2026-09-27 note), plus `docs/mcp/_template.md`.
**Files.** `backend/connectors/` (models, manifests/, mcp_client.py,
reads.py, writes.py, tasks.py, services.py, views.py, serializers.py,
docker/), `backend/assistant/{models,services,context}.py` + migrations,
`deploy/docker/compose*.yml`, `docs/security/mcp-governance.md`,
`docs/mcp/`.
**Tests.** In-memory SDK server fixture: unlisted tool invisible; schema
drift → disabled + audit; server descriptions absent from every captured
gateway payload; a result containing `</connector_result>` and
zero-width characters is neutralized; size cap; images dropped; router
vocabulary excludes disabled bindings, other workspaces and disallowed
channels; the facade never classifies `connector_read`; a personal read
→ `private` + `personal_data`, and the chat call **in the same turn**
as the read already passes `personal_data=True` (captured-kwargs
assertion), as do all later calls; in the triggering turn
`router.intent` runs without `personal_data` and `router.connector_args`
with `personal_data=True` for a `personal` binding and without it for a
`firm` one; no allowed provider → no read, flag
unset; the MCP client exercised from the real call contexts — the sync
chat view under the test client and a Celery task in eager mode — plus
a call from inside a running loop failing with `event_loop_active`; a write's display equals its execution arguments
byte-for-byte; binding disabled before approval → `connector_changed`,
no call; lost worker → `unknown_outcome`, no second call; blocked
manifest cannot bind; role × endpoint (five Phase 4 principals); no
credential value in any captured DB row, audit row or log.
**Done when:** a fake server's read reaches chat fenced and audited, and
its write reaches the server exactly once, only after a web decision.

### 5. `feat/p5-github` — PR gate and PR/CI status (D7)

**Scope.** Manifest, sidecar, `docs/mcp/github.md`, allowlist row; scope
check via `core/repo_identity`; reads `github_pr_status`,
`github_ci_status`, `github_branch_head` (`firm`, web + CLI; `personal`
for a `sensitive` repo); gate `github_open_pr`, system-created from a
`succeeded` `HarnessImplementationJob` — payload snapshots repo,
`head=branch_name`, `base=repo_default_branch`,
`head_sha=produced_commit_sha`, an owner-editable title and a
Friday-authored body (goal, `diff_summary` labelled model-written, test
result, review verdict); execution reads the remote head first and fails
`head_moved` unless it equals `head_sha`. Harness API: `POST`/`GET
/api/v1/harness/implementations/<id>/pull-request`, web only.
**Tests.** Cross-workspace repo argument refused before any call;
`github_open_pr` unmintable via `POST /approvals` and from non-succeeded
jobs; `head_moved`; merge/file tools unreachable even when the server
lists them; reads audited; role × endpoint.
**Done when:** a pushed harness branch gets a PR after one web decision
and its CI state shows on the job.

### 6. `feat/p5-deploy` — `deploys` app, gate, SSH runner, rollback (D10)

**Scope.** `DeployTarget`, `DeployRun`, `deploy` gate (`describe_request`,
`describe_execution`), `request_deploy` (GitHub title/CI reads when the
binding exists — wired here or in branch 5, whichever lands second; a
manual SHA otherwise, CI "unknown" and refused if required),
`deploys/ssh.py`, runner tasks + recovery beat on the `deploy` queue,
health/rollback state machine, work_log on every terminal state, API
(targets: owner + web; runs: list/detail; request: web + CLI),
`deploy/remote/friday-deploy.sh` + example app config, runbook
`docs/runbooks/deploy-target-setup.md` (dedicated VPS user, forced
command, host key captured out of band, first `status`).
**Files.** `backend/deploys/`, `config/settings/base.py` (`DEPLOY_*`,
routes), `Makefile` (runner `-Q`, `deploy-smoke`), `deploy/remote/`,
`docs/runbooks/`.
**Tests.** SSH argv/env (no `SSH_AUTH_SOCK`, `-F /dev/null`, pinned
known_hosts, missing key → fail closed); verb/SHA/app validation; the
remote script under pytest with stub `git`/`docker` on `PATH` (the
`harness/tests/test_write_mode_wrapper.py` pattern): unknown verb,
non-ancestor SHA, rollback to an unrecorded SHA refused; state machine:
success, health fail → rollback ok / rollback fail, drift, self-repo
remote, `config_hash` changed, cancel before/after the deploy verb, lost
runner → `unknown_outcome`; one active run under concurrent approvals;
a second approved request for a busy target → `target_busy` (audited,
4xx/terminal, never a 500), including the `IntegrityError` backstop
path; rollback only after three consecutive failures (one blip → no
rollback); window elapsed without two consecutive passes (alternating
results) → treated as failure; `health_url`-only failure →
`succeeded_degraded`;
`auto_rollback=False` → `health_failed`, no rollback verb sent;
`deploy` unmintable via `POST /approvals`; role × endpoint. Manual/
nightly `make deploy-smoke`: an sshd container running the real script
against a dummy compose app — deploy, forced health failure, rollback.
**Done when:** the smoke passes and a staging target deploys a merged
commit after one web decision, rollback proven.

### 7. `feat/p5-home-assistant` — D8

**Scope.** Native bridge, manifest, `docs/mcp/home-assistant.md`,
`ha_states` read (allowed domains, attribute whitelist), `ha_call_service`
gate with per-service serializers and renderer, state read-back,
`records_work_log=False`. **Tests.** `httpx.MockTransport` only;
disallowed domain/service/data key/out-of-range value → 400 at request
time; entity gone at execution → fail closed; `HA_ALLOWED_DOMAINS` cannot
widen the code maximum; WoL MAC allowlist; firm workspace cannot bind;
personal-data routing on reads.
**Done when:** "salonun ışığını %40 yap" yields an exact, decidable
request whose execution matches the dialog.

### 8. `feat/p5-mail-calendar` — D9

**Step 0 — Google vendor spike, run at phase start (in parallel with
branch 1) and recorded in this brief before branch 8 is scoped:** for a
single-user External app in "In production" but unverified, confirm the
refresh-token lifetime (not the 7-day Testing expiry), whether the
restricted Gmail scopes (`gmail.readonly`, `gmail.send`) work for the
owner's own account without a verification review, and the unverified
consent-screen user cap. If verification would be required, branch 8
drops Gmail to read-via-IMAP-app-password or waits — decided with the
owner then; branch 8 is outside the minimum usable cut, so the deploy
path is not held.
**Scope.** Google bridge, `connect_google`, scope verification, reads,
`mail_send`/`calendar_create_event` gates and renderers,
`docs/mcp/google.md`, runbook section (OAuth client, publishing status —
R4). **Tests.** PKCE/loopback flow against a mocked token endpoint;
extra scope → disabled; send without `gmail.send` → refused at request
time; MIME built only from payload fields (hash matches); raw addresses
displayed; reads web-only; personal-data routing; no token in DB, cache,
audit or logs.
**Done when:** "bugün neler var, son gelen mail ne diyor?" is answered
through a local model, and a drafted reply is sent only after a decision.

### 9. `feat/p5-connectors-ui` — frontend

**Scope.** Settings → "Bağlantılar" (bindings, health, pin status,
recent reads from audit); Firma → "Deploy hedefleri" (targets, runs,
fenced step log, distinct banners for `rolled_back`, `rollback_failed`,
`health_failed`, `succeeded_degraded` and `unknown_outcome` (renderer
test per state — a degraded deploy must never look clean), "Deploy" dialog with SHA prefill and
CI state); harness implementation detail: "PR aç" + PR/CI panel;
renderers for `deploy`, `github_open_pr`, `ha_call_service`, `mail_send`,
`calendar_create_event` and generic connector writes (Phase 4
`components/approvals/renderers/`); chat source badge ("Kaynak: GitHub/
Posta/Ev") and a notice when a conversation turned private/personal;
catalog entries and error codes. **Tests.** Renderer selection; every
external-effect dialog blocks until confirmed; badges; query-key
workspace isolation.
**Done when:** every new gate renders its full snapshot; deploy and PR
flows work at phone width.

### 10. closeout

MASTER_PLAN §AA row 5 → done and §O (HA, external-installation bridge) updated; ADR-008
implementation notes (deploy gate, decision-4 producers);
`docs/integrations/README.md` "Other integrations" rewritten; governance
pass: NotebookLM MCPs stay rejected for runtime (unofficial,
cookie-based), browser/Playwright MCP stays rejected; e2e: harness job
(stubbed CLI) → PR (fake MCP server) → deploy (sshd smoke target) →
`work_log` visible.

## Branch table

| # | Branch | Scope | Depends on |
|---|---|---|---|
| 1 | `feat/p5-kernel-hooks` | ADR-010, `system_created`, `ReadToolSpec`, `records_work_log`, vault-export/task producers, `core/repo_identity`, contracts | Phase 4 closeout on `main` (device trust, query layer, inbox, facade) |
| 2 | `feat/p5-tier-routing` | Tier map, `harness.review_summary`, purpose coverage, personal-data routing, harness policy table, routing reasons | 1 |
| 3 | `refactor/p5-harness-lifecycle` | Leaf lifecycle extraction, zero behavior change | Phase 4 closeout on `main`; parallel with 1–2 |
| 4 | `feat/p5-connector-framework` | Spike, `connectors`, MCP client, pins, reads → chat, gated writes, external-installation blocked manifest, governance | 1, 2 |
| 5 | `feat/p5-github` | Sidecar, scope check, PR gate, PR/CI reads | 4 |
| 6 | `feat/p5-deploy` | `deploys`, deploy gate, SSH runner, remote script, health/rollback, smoke, runbook | 1 (GitHub title/CI snapshot wired by whichever of 5/6 lands second) |
| 7 | `feat/p5-home-assistant` | Native HA bridge, allowlists, gated service calls | 4 |
| 8 | `feat/p5-mail-calendar` | Google bridge, host OAuth, reads, gated send/invite | 4 |
| 9 | `feat/p5-connectors-ui` | Settings, deploy targets, PR panel, renderers, chat badges | 5, 6 (7, 8 renderers as they land) |
| 10 | closeout | Docs, governance pass, end-to-end | 1–9 |

Minimum usable cut if the phase runs long: 1, 2, 4, 5, 6, 9 deliver
"request → code → live"; 7 and 8 may land after without holding it.

## Out of scope (explicit)

Social media (undefined until a concrete service is named) · Friday
merging PRs · deploying Friday itself (self-repo guard) · free-form or
per-target commands sent by Friday · SSH password auth, agent
forwarding, trust on first use · stdio servers in web/worker · MCP
resources, prompts, sampling, elicitation, roots · MCP access for harness
containers or MVP 7 agents · an autonomous tool loop in chat · connector
reads over Telegram or the facade · structured mail/agenda/home
dashboards · firm mailboxes, non-Google providers · HA scripts,
automations, locks, alarms, HA's MCP server · confirm-class for any HA
domain (Q7) · external installations beyond the blocked manifest (O-5), fleet (§P) · a
second runner / dynamic registry · Gemini CLI / Ollama as harness agents
· SSE log streaming, revise-per-feedback loop, owner core memory block,
consolidation, shared per-turn query embedding (still carried forward) ·
facade tier aliases (Phase 4 Q10 stands) · caches, evals, Token Optimizer.

## Test strategy

Same discipline as Phases 2–4 (CLAUDE.md rules 5, 7, 8): lint, tests,
CI green per branch; `makemigrations --check`; import-linter with the new
contracts, including `mcp.server` forbidden to production code. No test
touches a real LLM, MCP server, GitHub, Google, HA or VPS: the SDK's
in-memory server, `httpx.MockTransport`, FakeAdapter with call-count and
captured-payload assertions, SSH/Docker behind module-attribute seams.
Real infrastructure only in manual/nightly smokes (`make harness-smoke`,
`make deploy-smoke`). Role × endpoint for every new surface against the
five Phase 4 principals (owner web session, owner PAT `cli`/`openai`,
non-owner, `is_agent`, anonymous). Isolation: bindings, reads, argument
scope and deploy targets per workspace; `personal` connectors unbindable
in firm workspaces. **Release-gate regressions** (escalate per the
CRITICAL/HIGH rule): an external write without an approved, linked
`ApprovalRequest`; a write re-sent after an unknown outcome; a
system-only gate mintable via `POST /approvals`; a machine origin,
`is_agent` user or low-trust channel deciding; personal-class data sent
to a provider without `personal_data_allowed`; a deploy of a SHA not on
the tracked branch or to a drifted target; an unpinned or schema-changed
MCP tool callable; a credential in the DB, a payload, audit or logs.

## Risks

- **R1 — Deploy is production authority.** Separate decision showing the
  full plan; forced-command verbs; pinned host key; merged commits only;
  drift check; one run per target; no retries; key only on the runner.
  Residual: the VPS app config and `docker` group are root-equivalent on
  that VPS — installed and owned by the owner, never by Friday.
- **R2 — Prompt injection through connector data** (mail is fully
  attacker-authored). Fenced, bounded, stripped results; one read per
  turn; no tool loop; router blind to connector text; every write gated
  with its exact payload shown; Phase 4 markdown drops images. Residual:
  persuasive prose can still mislead — hence no action without a decision.
- **R3 — MCP supply chain / tool poisoning.** Digest-pinned sidecars,
  code manifests, schema pins, Friday-authored descriptions, no sampling,
  per-sidecar credentials.
- **R4 — Google OAuth friction.** A consent screen in "Testing" issues
  refresh tokens that expire after 7 days; `gmail.readonly` is restricted
  (step-0 spike: the owner's personal use is exempt from verification)
  and an unverified personal app shows a warning; a forced verification
  review would be a multi-week external timeline. Runbook: "In
  production", unverified, single user; the health beat surfaces
  `invalid_grant` as "reconnect with `connect_google`". Branch 8's step
  0 verifies these vendor facts at phase start, symmetric with D7's
  GitHub spike, so a surprise cannot land late.
- **R5 — The HA token is admin-equivalent** (HA has no scoped tokens).
  Dedicated non-admin HA user, HA on LAN/Tailscale only, code-bounded
  allowlist, env-only token.
- **R6 — The GitHub PAT can merge via REST.** The tool is unlisted;
  registered repos only; 90-day expiry; branch protection recommended.
- **R7 — Gate friction** (Gate A, Gate B, PR, deploy, each light toggle).
  Accepted: ADR-008 decision 3 forbids implied decisions. Approval counts
  per tool are measured; relief only by ADR (Q5, Q7).
- **R8 — Async SDK in sync Django; runner availability.** `anyio.run`
  per operation, never in a running loop, per-call session setup accepted
  at owner volume. Deploys wait while the Mac runner is offline; the UI
  says so.
- **R9 — Phase size.** Ten branches; the minimum usable cut protects the
  deploy path.

## Step-0 spikes (recorded 2026-09-27, before code)

Vendor facts checked against official sources on 2026-09-27; detail and
source URLs in the spike notes kept with the branch-4/branch-8 PRs.

**Branch 4 — transport (D1, D2, D7).**
- `github-mcp-server` (v1.12.2, `ghcr.io/github/github-mcp-server`,
  pin by digest) has a Streamable-HTTP mode (`http` subcommand), but in
  that mode it takes the token **only from each request's
  `Authorization` header** — Friday would hold the PAT, which D1 forbids
  (a sidecar holds only its own credential; R6: the PAT can merge via
  REST). **Decision:** run it in **stdio mode inside its sidecar behind
  our stdio↔HTTP bridge** (D1's stdio path), with
  `GITHUB_PERSONAL_ACCESS_TOKEN` in the sidecar's env only. GitHub's
  hosted endpoint is not used (its tool limits are client-sent headers).
- Tool ceiling server-side: start with `--tools=` listing only the
  pinned tools (merge, file-write, branch and workflow-trigger tools are
  then unregistered); not `--read-only`, which would also drop
  `create_pull_request`.
- Pinned tools: `pull_request_read` (methods restricted by the manifest
  to `get`, `get_status`, `get_check_runs`), `actions_list` /
  `actions_get` (the old workflow-run tools were consolidated into
  these), `get_commit` (takes a branch name → branch head; there is no
  `get_branch`), `list_branches`, `list_commits`, `list_pull_requests`,
  and the one write `create_pull_request`. Its `reviewers[]` and
  `maintainer_can_modify` arguments are fixed by Friday (empty / false)
  and shown in the dialog. Schemas have changed across minor releases
  (e.g. `perPage` in v1.12.0) — the schema pins are needed.
- MCP Python SDK: `mcp` 2.x (2.2.0, 2026-09-07) is the stable line; 1.x
  is security-fix only. Target 2.x. Stable protocol `2026-07-28` is
  stateless (`server/discover` replaces the initialize handshake/
  sessions); the client is `mcp.client.streamable_http.
  streamable_http_client(url, http_client=...)` with headers/timeouts on
  its async HTTP client. `anyio.run()` per operation is sound (D2's
  running-loop guard matches anyio's own requirement). The SDK still
  pulls server-side packages, so the `mcp.server` import ban stays. It
  refuses cross-origin redirects but follows same-origin 307/308: D1's
  "follows no redirects" is enforced by our client configuration
  (`follow_redirects=False` on the HTTP client), tested.

**Branch 8 — Google (D9, R4).**
- Verification does **not** block branch 8. Only `gmail.readonly` is a
  restricted scope; `gmail.send`, `calendar.readonly` and
  `calendar.events` are sensitive. An unverified External app used by
  its own developer falls under Google's personal-use exemption: the
  owner clicks through the "unverified app" screen; no security
  assessment; cap of 100 new users.
- In "In production" status the 7-day refresh-token expiry of "Testing"
  does not apply. Tokens still end after 6 months unused, on a password
  change (Gmail scopes), on revocation, or past 100 tokens per client per
  account — the health beat maps `invalid_grant` to "reconnect with
  `connect_google`".
- Flow: Desktop-app OAuth client, loopback redirect on `127.0.0.1` with
  a random port, PKCE S256 (out-of-band is blocked since 2023-01-31).
  Installed apps get no incremental authorization: adding the send
  scopes re-consents the full scope set and yields a new refresh token.
  Users can decline individual scopes — the granted-scope check (D9)
  covers it. Client secrets are shown once at creation; clients unused
  for 6 months are deleted (the daily refresh counts as use). The IMAP
  app-password fallback is not needed.

## Plan-review rounds (Sonnet 5, 2026-09-27)

Round 1 (REVISE), addressed:

| # | Finding | Resolution |
|---|---|---|
| C1 | Precondition claimed device trust etc. were on `integ/phase-4`; only Phase 4 branch 1 is | Precondition rewritten to the true state; hard dependency on Phase 4 closeout on `main` (branches 2, 3, 5, 6 named) |
| H1 | Same-turn personal-data ordering unstated | D5: flag set before the consuming completion, read fresh before every `complete()`; pre-flight check; branch-4 same-turn test |
| H2 | Rollback folded into one decision implicitly | Raised against ADR-008 d.3 and resolved by extension in ADR-010 (D10): previous SHA only, stated window, 3 consecutive failures, `auto_rollback` opt-out, `succeeded_degraded` |
| H3 | Two approved deploys for one target → raw `IntegrityError` | `create_execution` `target_busy` check + mapped backstop; tests |
| M1 | Sync/async bridge hazard not tested | Fixed call sites named; running-loop guard; tests from view and Celery contexts |
| M2 | `harness.review` split loses continuity | Dated note in `token-optimization.md` |
| M3 | `system_created` is the load-bearing invariant | Branch-1 tests pin the view and scan views/serializers |
| M4 | Google OAuth needs an early spike | Branch 8 step 0 at phase start, parallel with branch 1 |

Round 2 (REVISE): **H** the pre-read router ordering claim was
circular → `router.intent` explicitly exempt (no connector content,
precedes the decision), `router.connector_args` carries
`personal_data` by the named binding's class; tested. **M** doc added
to branch 2 files; per-state deploy banners named in branch 9 with
renderer tests; health window elapsing without two consecutive passes
is a failure (tested); ADR-008 d.3 clause citation sharpened; D2 run-on
split.

Round 3: **APPROVE** — all round 1–2 findings verified resolved; one
note folded in (the `router.connector_args` `NoProviderAvailable` maps
to the D5 "local model required" reply).

## Open questions (with recommended answers)

- **Q1–Q3 — Transport, SDK, HA transport.** Streamable HTTP only in
  hardened sidecars (D1); adopt the pinned `mcp` SDK client-side (D2);
  HA over native REST, since exact entity ids keep the display true (D8).
- **Q4 — Mail/calendar: Google-only or generic?** Google-only behind
  reader protocols (D9); the owner confirms the primary mailbox and
  calendar are Google before branch 8 starts.
- **Q5 — Fold PR creation into Gate B?** Not in Phase 5: one decision per
  external effect keeps snapshots simple; revisit with usage data.
- **Q6 — Does the deploy decision cover automatic rollback?** Yes, by an
  explicit ADR-010 extension of ADR-008 decision 3 (D10): only to the
  recorded previous SHA, only within the stated window, only after
  three consecutive health failures, and the owner can turn it off per
  target (`auto_rollback`); a later manual rollback is a new deploy
  request.
- **Q7 — Direct control for low-risk HA domains?** No; that adds a
  confirm-class kind and needs a new ADR (ADR-008 k.3, ADR-009). Revisit
  with Phase 6 voice, which needs an on-screen confirm anyway.
- **Q8 — Read channels?** `firm` reads web + CLI; `personal` reads web
  only; Telegram and the facade never in Phase 5.
- **Q9–Q10 — Engine extraction, runner registry.** Leaf lifecycle layer
  only (D12); no registry until a second runner exists (D14).
- **Q11 — GitHub server: local or hosted?** Local, digest-pinned; hosted
  only if the step-0 spike finds no local HTTP/bridge path.
- **Q12 — Where do build commands live?** On the VPS in root-owned per-app
  config; Friday sends verbs only (D10). **Q13 — External installations?** Blocked manifest
  only; O-5 unchanged (D14).
- **Q14 — Do connector reads become memories?** No: only Friday-authored
  `work_log` about writes and deploys; conversations with personal reads
  are private, so extraction never runs on them.
