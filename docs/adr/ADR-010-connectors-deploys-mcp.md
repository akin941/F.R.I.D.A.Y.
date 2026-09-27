# ADR-010: Phase 5 — Connectors, Deploys, MCP Client

## Status
Accepted (2026-09-27, Phase 5 branch 1 — `docs/prompts/phase-5-deploy-mcp/brief.md`,
approved after Sonnet 5 plan-review round 3). Extends ADR-002 (kernel app
list and import contracts) and ADR-008 decision 3 (see decisions 2 and 7
below); amends nothing else.

## Context
Phase 5 closes the loop `docs/VISION_JARVIS_2.md` §4 promised — request →
briefing → Gate A → code → review → Gate B → PR → merge → **deploy gate** →
live — and gives Jarvis its first reach into outside systems: GitHub, Home
Assistant, Google mail/calendar, and (blocked, O-5) the external installation. Every
one of those is gate-class under ADR-008 decision 3 (deploy, external send,
git push/merge). Four things in the kernel as it stood did not fit:

- **No home for bridges or deploys.** `integrations` is the channels app
  (CLI, Telegram, facade); `harness` is a leaf nothing may import. Neither
  can own outside-world bridges that `harness` itself needs to call.
- **System-only gates were keyed on `origin`.** Phase 3 let a
  `user_invocable=False` tool be minted only when `origin == HARNESS`. The
  deploy gate is system-created too, but the owner clicking "Deploy" is a
  human-origin request — `origin` ("who produced it") and "may this
  system-only gate be minted" stop being the same question.
- **Connector output is attacker-reachable text** (mail is fully
  attacker-authored), and some of it is personal data that must never reach
  a cloud provider the owner has not allowed.
- **A deploy that fails its health check needs a rollback**, which is a
  second live mutation. ADR-008 decision 3 says a gated decision is "never
  implied by the action that created the request"; whether one deploy
  decision may cover a later automatic rollback had to be decided
  explicitly, not assumed.

## Decision

1. **Two new kernel apps, one-way contracts.** `connectors` (manifested MCP
   servers and native bridges, their per-workspace bindings, reads and
   gated writes) and `deploys` (deploy targets, the deploy gate, the SSH
   runner). ADR-002's rules apply; import-linter enforces from the day the
   apps are registered (empty, in Phase 5 branch 1):
   - `connectors` may import `core`, `workspaces`, `audit`, `ai_core`,
     `tools`, `memory`.
   - `deploys` may import the same set plus `connectors` (commit title and
     CI reads for the deploy snapshot) and `approvals` (it mints its own
     system-created `deploy` gate through `approvals.services.
     create_request`, the edge `harness` already has for Gates A/B).
   - `harness` may import `connectors` (the `github_open_pr` gate, PR/CI
     reads), not `deploys`. Neither new app imports `harness`, which stays
     a leaf: PR executions link back through an opaque `origin_ref`.
   - `assistant` reaches connector reads only through `tools.registry`
     (`ReadToolSpec`, decision 4). `memory`, `tools`, `approvals`,
     `workspaces`, `ai_core`, and every other kernel app except the two
     named above, import neither.
   - Pure git-remote canonicalization moves to `core/repo_identity.py`
     (from `harness/repo_identity.py`, which re-exports it), so the GitHub
     scope check and the deploy self-repo guard compare remotes exactly as
     the harness self-modification hard-block does.
   - `Workspace.profile.deploy_targets` stays dossier metadata; a target
     becomes actionable only as an explicit, owner-created `DeployTarget`
     row — the split `HarnessRepo` keeps from `profile.repos[]`.

2. **`system_created` is the only key to a system-only gate.**
   `approvals.services.create_request(..., system_created=False)` gains a
   keyword-only flag, default False. A tool with `user_invocable=False` is
   minted only when the caller passes the literal `True`, whatever the
   `origin`. Only the owning service of such a gate passes it — the
   harness for Gates A/B, `github_open_pr`'s harness endpoint, and
   `deploys.services.request_deploy` — never a view or serializer: the
   HTTP view passes `False` as a literal, and a test scans every
   views/serializers module and fails on anything else. `origin` keeps
   meaning "who produced the request" (the owner's "Deploy" click is
   `HUMAN`). This extends ADR-008 decision 3 only in *who may create* a
   system-only request; the decision itself stays explicit, separate,
   audited, owner-only and web-only — `system_created` never implies,
   pre-approves or relaxes a decision. The flag is recorded in the
   `approval.requested` audit row.

3. **MCP client: official SDK, Streamable HTTP only, sidecars.** Jarvis
   adopts the official `mcp` Python SDK client-side (a pinned minor
   version; production code may import only `mcp.client.*`/`mcp.types`,
   and an import-linter contract forbidding `mcp.server` lands with the
   dependency). One sync facade runs each operation in `anyio.run()` and
   fails closed if an event loop is already running. The client speaks
   only Streamable HTTP to manifest-named env URLs, follows no redirects,
   and offers no client capabilities (no sampling, elicitation or roots).
   Every server runs as its own hardened compose sidecar (digest-pinned,
   read-only, all capabilities dropped, no published ports, only its own
   credential); a stdio-only server runs inside its sidecar behind a
   stdio↔HTTP bridge — never in web, worker or the harness runner.
   Connectors are manifests in git (`connectors/manifests/<name>.py`) with
   a governance twin and an allowlist row; widening one is a reviewed
   commit, never a DB write (ADR-004). A per-workspace `ConnectorBinding`
   is disabled by default. Each listed tool's `inputSchema` is pinned by
   hash; a changed schema disables the tool until a commit updates the
   manifest; unlisted tools are unreachable; server descriptions never
   reach an LLM prompt.

4. **Connector data is low-trust data.** Reads are ungated but audited
   (`connector.read`: binding, tool, argument hash, size, duration,
   outcome, channel — never content) and reach the assistant only as
   fenced, bounded, stripped `connector_result` blocks framed as data, not
   instructions — the pattern of the knowledge block and the harness
   transcripts. `tools.registry` gains `ReadToolSpec`; the router picks at
   most one read per turn and never sees connector text (it sees only the
   current user message); there is no autonomous tool loop. `firm` reads
   run over web and CLI, `personal` reads over web only, and neither ever
   over Telegram or the OpenAI-compatible facade — enforced when a read
   tool is registered. Credentials stay env-only: never in Postgres, a
   payload, an audit row or a log line. Connector reads never become
   memories.

5. **Personal-class data never reaches an unapproved provider.**
   `ModelProvider.personal_data_allowed` (default False; only the local
   model seeded True; changed only by an audited management command).
   `InferenceGateway.complete(..., personal_data=True)` keeps only allowed
   providers and fails closed with `NoProviderAvailable` — never a silent
   cloud fallback. A `personal` read marks the conversation `private`
   (ADR-005 cascade) and sets a one-way `personal_data` flag in the same
   transaction that stores the read, before the completion that consumes
   it; the flag is read fresh before every later gateway call. The
   pre-read intent classification is exempt (it carries no connector
   content); the argument-extraction call carries `personal_data` by the
   named binding's data class. With no allowed provider the read does not
   happen.

6. **Every connector write is gate-class, snapshot-backed and at most
   once.** Each write is a registered `gated=True` `ToolSpec` decided
   through the approval gate over the web channel only. The payload
   snapshots the normalized arguments, binding, manifest version and schema
   pin; the decider sees exactly what will be sent; execution re-checks
   binding and pin and sends exactly the snapshot. A worker lost mid-call
   leaves `unknown_outcome` and is never retried automatically. Home
   Assistant service calls, mail sends, calendar invitations, pull requests
   and deploys are all gate-class; no Home Assistant domain becomes
   confirm-class.

7. **The deploy gate, and the one extension of ADR-008 decision 3.** A
   deploy runs only a merged commit (ancestor of the target's tracked
   branch; Jarvis never merges) to an enabled, owner-created target whose
   pinned host key, forced-command SSH verbs (`status|deploy|health|
   rollback`) and root-owned remote config bound what the runner can do.
   The `deploy` gate is system-created (decision 2) and its display states
   the full plan: SHA, target, current SHA, health checks, window and
   rollback SHA. Execution refuses on a changed target configuration, a
   busy target (one active run per target, `target_busy`, never a 500) or
   a drifted remote (`target_drifted`, nothing touched).

   **Bounded automatic rollback is covered by the deploy decision.**
   ADR-008 decision 3 forbids a decision "implied by the action that
   created the request". Rollback is a second live mutation, so it is
   admitted here explicitly, and only because all of the following hold:
   - it can restore only the `rollback_sha` snapshotted in the decided
     payload — the SHA the drift check proved live, which an earlier owner
     decision put there — never an arbitrary state;
   - the dialog the owner decided states that SHA, the health checks and
     the window verbatim, so the rollback is part of the decided plan, not
     implied by it;
   - it is bounded to the health window of that one run; any later
     rollback is a new `deploy` request and a new decision;
   - it triggers only after **three consecutive** health failures spaced
     across the window (one blip never rolls back); a window that elapses
     without two consecutive passes counts as a failure (fail closed); a
     failing optional `health_url` alone, while the target's own `health`
     verb passes, rolls nothing back and ends `succeeded_degraded`;
   - the owner can turn it off per target (`DeployTarget.auto_rollback`,
     default True, snapshotted into the payload); with it off, a failed
     health check ends `health_failed` and nothing is rolled back without
     a new decision.

   Terminal states (`succeeded`, `succeeded_degraded`, `rolled_back`,
   `rollback_failed`, `health_failed`, `unknown_outcome`) are audited,
   bannered and written to `work_log`. Any other growth of what one deploy
   decision covers needs a new ADR.

8. **Remaining `work_log` producers (ADR-008 decision 4).**
   `tools.work_log.record_tool_outcome` wraps `memory.services.
   record_work_log` (still the only creation path) with Jarvis-authored
   bookkeeping — never connector content, never payload free text beyond
   an owner-authored title. Producers: vault export, terminal connector
   writes, terminal deploy runs (rollbacks included) and task outcomes
   (completed, cancelled). `ToolSpec.records_work_log` (default True) lets
   a tool whose outcomes are audit-worthy but not memory-worthy opt out
   (Home Assistant service calls). Content carries the date, so a repeated
   outcome is a new row. MVP 7 agent runs are not added.

9. **Nothing moves between gate-class and confirm-class.** No new
   confirm-class action; ADR-009's "no growth by precedent" holds.

## Consequences
- Every new outside-world write is one more owner decision (Gate A, Gate
  B, PR, deploy, each light toggle). Accepted: relief only by a new ADR,
  based on measured approval counts.
- A system-only gate can no longer be minted by stating an origin; any
  future system-only gate needs an owning service that passes
  `system_created=True`, and that call site is reviewable by grep.
- The `mcp` SDK is a fast-moving dependency with server-side packages
  attached; the `mcp.server` contract and the version pin contain it.
- Connector reads are fenced but persuasive text can still mislead the
  owner; the mitigation is that nothing acts without a decision showing
  the exact payload.
- The deploy key, like the harness push key, lives only on the runner
  host; deploys wait while the runner is offline.
- The external connector ships as a `blocked` manifest that can never bind
  (O-5 unchanged, ADR-006: Jarvis is only an MCP client of the panel).
