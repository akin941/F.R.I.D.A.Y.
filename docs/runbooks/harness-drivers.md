# Runbook — harness v3 drivers (owner)

Phase 10 (`docs/prompts/phase-10-control/brief.md` D10, branches 6–7)
added two **drivers** for the implementation step of a harness job: a
long-lived, bidirectional session with the coding agent over the
container's stdio, instead of one CLI run. Everything else in the Phase 3
pipeline is unchanged — Gate A, the worktree, the wrapper-owned test run,
the secret scan, the cross-vendor review (coder ≠ reviewer) and Gate B.

| Agent | Driver value | What runs in the container |
|---|---|---|
| `codex_cli` | `app_server` | `codex app-server` (JSON-RPC over stdio, branch 6) |
| `claude_code` | `agent_sdk` | `claude_agent_bridge.py` with the Claude Agent SDK (`friday-agent-bridge/v1`, branch 7) |
| either | `cli` (default) | the Phase 3 one-shot CLI path |

**Both drivers are off** until the steps below are done. The closeout
preflight row `harness_drivers:<agent>` stays red for any enabled driver
that is missing a step.

## What a driver changes for the owner

- **Agent escalations become approval cards.** When the agent asks for
  something outside its sandbox (a command it may not run alone, a file
  change outside its grant, network), the driver mints a
  `harness_agent_action` `ApprovalRequest` (origin `harness`, "Ajanın
  isteği — güvenilmeyen metin"). Decide it on `/approvals` within **10
  minutes**; anything else — rejected, expired, the job cancelled, the
  runtime brake — answers `decline`. The agent never gets
  "accept for session".
- **What you approve is what runs.** A card is minted only when the
  command and working directory can be shown verbatim (≤ 2 000 / 500
  characters, no invisible, bidi or control characters, no harness
  secret); otherwise the agent gets `decline` without a card.
- **A `file_change` card shows only the grant root** (Codex) or the tool
  and its input as canonical JSON (Claude), not the diff: you approve a
  path scope. The diff is still reviewed after the run (tests, review,
  Gate B). This is a known limit until the real snapshots offer the change
  set at approval time.
- **Usage:** one `ModelUsage` row per turn (purpose
  `harness.implementation`). Codex reports tokens but no cost: those rows
  carry cost 0 (`cost_source: not_reported`) and the run is bounded by
  `HARNESS_DRIVER_MAX_TOKENS`; the Claude SDK reports cost, so its runs are
  also bounded by `HARNESS_MAX_JOB_COST`.
- **Bounds:** `HARNESS_DRIVER_MAX_ESCALATIONS` (20) cards per job, the job
  runtime brake, bounded stdout reading. The job page shows the driver and
  the escalation counts.

## 1. The third-party terms check (d4) — before anything is enabled

Check the current terms of each vendor for driving its agent unattended
from an automated harness:

- OpenAI (Codex CLI / `codex app-server`);
- Anthropic (Claude Agent SDK, Claude Code);
- (later) Antigravity and ACP agents — not built in Phase 10.

Record the result here (date, which terms, the decision) and set the date:

```bash
HARNESS_DRIVER_TERMS_CHECKED_ON=YYYY-MM-DD   # .env of web, worker and runner
```

Without it the system checks refuse to boot with an enabled driver
(`harness.E003`) and a job that selects one fails `runner_error` before
anything is cloned.

| Date | Vendor | Terms checked | Decision |
|---|---|---|---|
| _pending owner evaluation_ | OpenAI | — | — |
| _pending owner evaluation_ | Anthropic | — | — |

## 2. Pin the versions and regenerate the snapshots

The shipped snapshots are **provisional** (hand-written, `0.1.0`,
`"provisional": true`): their method and SDK names were never verified
against a real binary. Replace them before enabling (brief R4: one pin per
driver, a contract test per pin, upgrades on a dedicated branch).

**Codex app-server:**

1. Choose the Codex CLI release; set `ARG CODEX_CLI_VERSION` in
   `backend/harness/docker/codex_cli.Dockerfile` and
   `HARNESS_CODEX_APP_SERVER_VERSION` to it; resolve the base-image digest
   (`docs/runbooks/harness-first-run.md` step 1).
2. Build the image (`make harness-images`).
3. `backend/harness/docker/generate_driver_schemas.sh codex <image>` —
   writes `backend/harness/drivers/codex_schema/<version>/protocol.json`
   from the installed binary (`--network none`); it fails loudly rather than
   write a partial snapshot.
4. `cd backend && uv run pytest -q harness/tests/test_driver_contract.py`
   — every fixture frame validates and every method the driver uses
   (`initialize`, `thread/start`, `turn/start`, `item/completed`,
   `thread/tokenUsage/updated`, `turn/completed`,
   `item/commandExecution/requestApproval`,
   `item/fileChange/requestApproval`) exists. Adjust
   `codex_schema_subset.py` or the driver if the real layout differs.

**Claude Agent SDK:**

1. Choose the `claude-agent-sdk` release; set `ARG CLAUDE_AGENT_SDK_VERSION`
   in `backend/harness/docker/claude_code.Dockerfile` and
   `HARNESS_CLAUDE_AGENT_SDK_VERSION`; resolve the base-image digest and
   `CLAUDE_CODE_VERSION`.
2. Build the image.
3. `backend/harness/docker/generate_driver_schemas.sh claude <image>` —
   writes `backend/harness/drivers/claude_sdk_surface/<version>/sdk_surface.json`
   by introspecting the installed SDK; refuses a version that differs from
   the ARG.
4. `cd backend && uv run pytest -q harness/tests/test_claude_driver_contract.py`
   — every SDK name the bridge uses exists in the surface.

Commit the snapshots and pins on a dedicated upgrade branch. The pin, the
Dockerfile ARG and the snapshot version must agree (`harness.E004`).

## 3. Smoke-test on the real image (never in CI)

Against a disposable repo, by hand:

- Codex: `codex app-server` honours `sandbox: workspace-write`,
  `approvalPolicy: on-request` and `networkAccess: false`; an escalation
  reaches the driver as an approval request.
- Claude: with `permission_mode="default"`, no `allowed_tools` and
  `setting_sources=[]`, every tool call reaches `can_use_tool`; a denied
  call is not executed; the container's egress reaches only the model API;
  the `usage` keys match the bridge's `usage_frame`.

## 4. Enable

```bash
HARNESS_DRIVERS_ENABLED=true
HARNESS_DRIVER_CLAUDE=agent_sdk      # and/or
HARNESS_DRIVER_CODEX=app_server      # Codex also needs its implementation slot (below)
```

The `codex_cli` implementation slot in `HARNESS_AGENT_POLICY`
(`backend/config/settings/base.py`) is off: turning it on (selectable + a
`claude_code` reviewer) is a reviewed code change, after steps 1–3. Then:

```bash
cd backend && uv run python manage.py check        # harness.E002-E005, W002
make preflight                                     # harness_drivers:<agent> must pass
```

`harness_drivers:<agent>` passes when the driver is disabled (CLI path) or
enabled with the d4 date, a non-provisional snapshot and agreeing pins. The
system check only **warns** on a provisional snapshot (`harness.W002`); the
preflight **fails** on it.

## Rolling back

Set `HARNESS_DRIVERS_ENABLED=false` (or the agent's value back to `cli`) and
restart web, worker and runner: the next job takes the CLI path. A job
already running keeps its driver until it ends; cancel it from the job page
to stop it (pending escalations then answer `decline`).

## Known limits (recorded at merge)

- The bridge's protocol channel is the container's stdout, which the
  agent's own processes share: repo code run by the allowed test command
  could forge `message` or `result` frames (cost/usage understated, forged
  transcript text). Forged frames cannot approve anything. Same exposure as
  the CLI path; accepted residual risk.
- The Claude bridge lets the agent write files in the repo and run the
  configured test command without a card, so it can run code it just
  wrote under the test command — bounded by the container sandbox and
  egress, verified in step 3.
- The token brake applies after the run; inside a run the bounds are the
  runtime deadline, the escalation cap and the reader limits.

## Settings

| Name | Default | What |
|---|---|---|
| `HARNESS_DRIVERS_ENABLED` | false | master switch |
| `HARNESS_DRIVER_CODEX` | `cli` | `cli` or `app_server` |
| `HARNESS_DRIVER_CLAUDE` | `cli` | `cli` or `agent_sdk` |
| `HARNESS_DRIVER_TERMS_CHECKED_ON` | empty | the d4 date (ISO) |
| `HARNESS_CODEX_APP_SERVER_VERSION` | `0.1.0` (provisional) | Codex pin |
| `HARNESS_CLAUDE_AGENT_SDK_VERSION` | `0.1.0` (provisional) | Claude SDK pin |
| `HARNESS_AGENT_ACTION_POLL_SECONDS` | 2 | how often a waiting driver re-reads the card |
| `HARNESS_DRIVER_MAX_ESCALATIONS` | 20 | cards per job |
| `HARNESS_DRIVER_MAX_TOKENS` | 2 000 000 | token brake per job |
