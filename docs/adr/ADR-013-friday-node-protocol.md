# ADR-013: Friday Node Protocol

## Status
Accepted 2026-11-20 — owner approval of the Friday 3.0 roadmap
(`docs/research/2026-11-roadmap-proposal.md` §B1, §c.4, §c.7, §g decisions
d1 and d9). Implemented by Phase 9 (`docs/prompts/phase-9-friday-node/`).
**Condition:** decision 2 (identity) is confirmed by spike S9.0 before
Phase 9 branch 1; if the spike fails, Phase 9 does not start and this ADR is
revisited before any node code lands. Extends ADR-002 (new kernel app
`nodes`), ADR-004/ADR-008 decision 3 (gated node actions, classified by
ADR-014). Amends nothing.

Numbering note: the proposal called this "ADR-012". ADR-011 is reserved by
the Phase 6 brief for "voice transport", so the proposal's ADRs are
numbered from ADR-012.

## Context
Friday needs a presence on the owner's Mac: microphone and Accessibility
grants, local reads of Claude Code / Codex sessions, and (Phase 10) gated
command execution. A process on the owner's machine that a server can
instruct is the largest new attack surface in the plan. The OpenClaw
lesson is concrete: an approval-settings endpoint reachable over a hijacked
WebSocket was exploited. The repo already has device trust
(`accounts.TrustedDevice`, `DevicePairingCode`, `PersonalAccessToken.kind`,
`manage.py revoke_all_devices`), but it is shaped for a browser (HttpOnly
cookie, idle expiry).

## Decision
1. **Shape (d1, d9).** One signed and notarized Swift macOS menu-bar app,
   `friday-node`, with the Python voice pipeline as a child process from
   Phase 11. It opens one **outbound-only** WSS connection to the server and
   has **no TCP listener**. Signing uses the owner's Apple Developer ID; the
   signing key stays on the owner's Mac and CI never holds it.
2. **Identity.** A `kind=node` principal: a long-lived node credential kept
   in the macOS Keychain, rotation without re-pairing, immediate revocation,
   an owner kill switch (web UI and `manage.py`), covered by
   `revoke_all_devices`. Pairing reuses the `DevicePairingCode` flow. The WS
   client pins the server certificate or CA. (Confirmed by spike S9.0.)
3. **Node-owned capability catalogue.** On connect the node sends `hello`
   with its catalogue (name, version, class, JSON schema). The server can
   only `invoke` catalogue entries with typed arguments. There is no generic
   "run this string" capability. The node returns `result` (exit, truncated
   output, artifact hash) and `event` messages.
4. **Invocation grant.** For a gated call the server issues a grant signed
   with a server key whose public half is pinned in the node at pairing. It
   covers the capability id and version, the canonical-JSON args hash, the
   workspace id, the node id, the approval id, a random nonce, `issued_at`
   and `expires_at` (at most 120 s). The node verifies signature, expiry and
   args hash and keeps used nonces until expiry, so a grant works once. A
   grant alone is never enough: the node shows its own confirmation window,
   rendered by the node from the args, and a human clicks it.
5. **Node policy is local-only.** Allowlist, exec policy and catalogue
   change only in the node's own UI behind LocalAuthentication (Touch ID or
   the macOS password). No WS message, server endpoint or web-UI path reads
   or writes node policy; a test asserts the protocol has no
   policy-mutation message type.
6. **Fail closed; owner away = deny.** WS lost or server down: no
   invocations, local confirmation windows close, the always-on mic is
   released. Local confirmation times out after 60 s (deny); a node-action
   `ApprovalRequest` expires after 10 minutes; nothing is queued to run
   later; in-flight invocations fail and are not retried. Heartbeat 15 s,
   reconnect with exponential backoff (1 s → 60 s, jitter).
7. **Hook relay.** Claude Code command hooks call the signed
   `friday-node hook` CLI, which writes to a user-owned Unix domain socket
   (mode 0600, peer-uid check). While the WS is down, scrubbed events are
   spooled to a bounded local file (≤ 10 MB, 24 h, mode 0600) and uploaded
   on reconnect — events only, never commands.
8. **No LLM, no provider keys.** The node makes no model calls and holds
   no provider API keys; summaries run server-side through the gateway.
9. **Server side.** A new kernel app `nodes` (Node, capability snapshot,
   invocation audit rows, the WS consumer). It may import `approvals`,
   `audit`, `workspaces`, `accounts`, `core`; it must not import `ai_core`,
   `memory`, `knowledge`, `harness`, `assistant` or `apps.modules.*`. It joins
   the "kernel apps must not import config" and "ai_core must not import
   business apps" contracts. Node data reaches memory through a
   memory-owned port (ADR-012).
10. **Scoping and audit.** Every invocation and event carries
    `workspace_id`, resolved from the repo registry; an unmapped path is
    dropped. One hash-chained audit row per invoke (capability, args hash,
    grant nonce, exit, duration, workspace, trace id). Everything the node
    returns is fenced as untrusted data (Phase 5 D4).
11. **Local reads need a per-directory grant.** Reading `~/.claude/projects`
    or `~/.codex/sessions` needs an explicit owner grant in the node UI
    (Touch ID), off by default, and only the node's own reader uses it.
12. **Logs.** Node logs are local, scrubbed with the ADR-012 scrubber,
    rotated, and never uploaded except as a redacted excerpt the owner
    attaches.

## Consequences
- A compromised server can sign grants but cannot change node policy, run
  anything outside the catalogue, or get past the local click.
- The owner pays for an Apple Developer membership (99 USD/year, d9) and
  keeps a signing key on the Mac.
- Phase 9 is read-only; gated execution and its classification arrive with
  ADR-014 in Phase 10.
- A compromised-server test suite (unknown capability, out-of-schema args,
  reused or expired nonce, bad signature, policy-mutation message) is a
  release gate for every node branch.

## Implementation notes — spike S9.0 (Phase 9 branch 1, 2026-11-20)

**Result: spike S9.0 server half: passed.** Decision 2 holds without a
browser-shaped compromise: a node is a `TrustedDevice` of `kind=node`
(`accounts/models.py`), with no idle expiry of the browser kind, no
cookie and no JWT. The Keychain half (storing, reading and rotating the
secret from Swift) is pending owner evaluation (Mac task M1).

What landed in `accounts` / `core`:
- `TrustedDevice.kind` (`browser`/`node`), `DevicePairingCode.device_kind`,
  `jnd_` node secrets (256-bit, sha256-hashed), `secret_issued_at`,
  `previous_secret_hash` / `previous_secret_valid_until`, and the per-owner
  `NodeKillSwitch` (migration `accounts/0008_node_device_trust`).
- Services in `accounts/devices.py`: `enroll_node_device`,
  `authenticate_node_secret`, `node_device_refusal` (the gateway's
  per-heartbeat re-check), `rotate_node_secret`,
  `engage_node_kill_switch` / `release_node_kill_switch`.
- `NodeCredentialAuthentication` → `CHANNEL_NODE` (`core/channels.py`),
  `IsNodeChannel`; `core/revocation.py` (`publish_device_revoked`,
  `publish_kill_switch_engaged`, `parse_revocation`) on
  `friday:device-revoked`, published after commit, never raising.
- Endpoints: `POST /api/v1/auth/nodes/credential/rotate` (node secret
  only), `GET/POST/DELETE /api/v1/auth/nodes/kill-switch` (owner web
  session only), `POST /api/v1/auth/pairing-codes` with `device_kind=node`;
  commands `issue_pairing_code --node [--strong]` and
  `node_kill_switch on|off|status`.

Pass criteria and their tests (`backend/accounts/tests/test_node_credentials.py`
unless noted):
1. Pair with both code kinds, cross-kind refusals:
   `test_a_node_code_pairs_a_node[pin|strong]`,
   `test_a_browser_code_is_refused_for_a_node_and_is_not_consumed`,
   `test_a_node_code_is_refused_for_a_browser`,
   `test_the_remote_key_never_pairs_a_node`,
   `test_node_pin_lan_gating_is_unchanged`,
   `test_node_pin_attempts_burn_as_before`,
   `test_a_node_code_is_single_use_under_concurrency`.
2. Rotation without re-pairing: `test_rotation_returns_a_new_secret_once_and_is_audited`,
   `test_old_secret_valid_within_the_grace`,
   `test_old_secret_invalid_after_the_grace`,
   `test_old_secret_invalid_after_first_use_of_the_new_one`,
   `test_rotation_with_the_previous_secret_is_refused`,
   `test_concurrent_rotations_yield_one_successor`,
   `test_rotate_endpoint_returns_the_secret_once`; age cap and idle window:
   `test_the_age_cap_refuses_an_unrotated_node`,
   `test_the_age_cap_counts_from_the_last_rotation`,
   `test_node_idle_expiry_uses_the_node_window`.
3. Revocation: `test_revoking_one_node_fails_its_next_authentication`,
   `test_revoke_all_devices_covers_nodes`,
   `test_revocation_is_published_once_per_device_after_commit`,
   `test_a_redis_error_never_fails_the_revoke`.
4. Kill switch: `test_kill_switch_on_then_off`,
   `test_one_owners_kill_switch_leaves_another_owners_node_alone`,
   `test_kill_switch_endpoints`, `test_kill_switch_command_prints_the_state`.
5. A node secret is refused by every other surface:
   `backend/config/tests/test_node_credential_surfaces.py`
   (URL walk), `test_a_node_secret_is_refused_by_start_device_session`,
   `test_a_forged_jwt_naming_a_node_device_is_refused`.
6. A browser device or PAT is refused by the node surfaces:
   `test_rotate_accepts_only_a_node_secret[*]` and
   `test_kill_switch_accepts_only_the_owner_web_session[*]` (owner web
   session, real browser JWT, `cli`/`openai`/`hook` PATs, a browser
   `jdv_` secret, a node secret, non-owner, agent, anonymous).

Noted residual: a node whose rotate *response* is lost still holds only
its previous secret, which works for the grace (600 s) but cannot rotate
again (refused so rotation cannot chain); after the grace it must
re-pair. The Mac app (M1) must therefore persist the new secret to the
Keychain before it closes the rotate request, and rotate only while
connected so a failure is noticed within the grace.

## Implementation note — owner-turn capabilities (Phase 8B branch 8, 2026-11-20)

Phase 8B (brief `docs/prompts/phase-8b-transcripts-graph/brief.md`, D13,
D17) adds two server-known read capabilities, `claude.transcripts.owner_turns`
and `codex.transcripts.owner_turns` (`class: read`, `data_class: personal`,
`workspace_resolution: row_cwd`). They return only owner-typed turns, scrubbed
on the node and re-checked for harness-injected markers on the server. The
**cursor is server-owned** (`memory.TranscriptCursor`); the node keeps none,
returns turns with `at >= since` oldest first, and caches nothing. Decision 11
(the per-directory grant on `~/.claude/projects` / `~/.codex/sessions`) gates
them on the Mac: without it the node answers `not_granted`.

The pull is system-initiated: `actor` = the node's owner, `channel =
system`, refused before signing as `server_refused:actor_not_owner` unless
the actor is the active node owner and owns the target workspace; the system
channel is accepted only for these two capabilities. Because Phase 9 branches
3–5 had not merged on 2026-11-20, branch 8 stages the contracts, reference
readers, fixtures and conformance cases in `backend/integrations/
node_reference/` and reaches the node through a `NodeReadPort` seam in
`tools/transcript_ingest.py`; the move into `nodes/capabilities/v1/` and the
`invoke_read` amendment are recorded as owner steps in the 8B brief.

## Implementation notes — contracts, protocol v1, audit chain (Phase 9 branch 2, 2026-11-20)

**Import contracts (decision 9, brief D2), in `backend/pyproject.toml`:**
- "nodes may import only approvals, audit, workspaces, accounts, core":
  forbids `config`, `ai_core`, `memory`, `knowledge`, `harness`,
  `assistant`, `tools`, `agents`, `skills`, `integrations`, `connectors`,
  `deploys`, `voice`, `hud`, `ops` and `tasks`. Phase 9 `nodes` does not
  use its `approvals` edge (a `nodes` -> `approvals` import would break
  "tools must not import approvals" once `tools` reads `nodes`).
- "only tools, hud, ops may import nodes": every other kernel app is a
  source; `config` is the composition root. `memory.ingestion`'s own
  contract now lists `nodes` too, closing the Phase 8A placeholder.
- `nodes.protocol` and `nodes.capabilities` import no project package and
  no other part of `nodes`; `nodes/tests/test_protocol_purity.py` imports
  them in a clean subprocess and asserts Django never loads.
- `nodes` joined the blanket contracts: core independence, kernel apps ↛
  config, ai_core ↛ business, audit ↛ business, no app ↛
  harness/ops/voice/hud, kernel apps ↛ connectors/deploys, MCP client-only.

**Dependencies (brief D3):** `cryptography==50.0.1` and `jsonschema==4.26.0`
promoted from transitive to direct exact pins, recorded in
`docs/mcp/dependencies/node-gateway.md`.

**Protocol v1 (decision 3/4/5, brief D5)** lives in `backend/nodes/protocol/`:
- `schemas/v1/<type>.schema.json` — the closed set of nine message types;
  server → node is exactly `hello.ack`, `heartbeat.ack`, `invoke`,
  `event.ack`, `bye` (`test_the_server_to_node_set_is_exactly_the_five_types`).
  No type name or schema property matches the policy regex; the single
  reserved exception is `invoke.grant` (null in v1, refused non-null by a v1
  node: `test_no_schema_property_is_policy_shaped`). The regex is widened
  from `catalogue_set` to `catalogue[._]set` so the dotted type
  `catalogue.set` is caught.
- Stricter-than-JSON-Schema semantics the Swift port must mirror: `pattern`
  is a full match, and numbers must be integers (a float is refused even
  where JSON Schema calls `1.0` an integer). Sub-schemas marked
  `x-friday-opaque` are typed holes validated elsewhere (capability
  schemas, event sinks).
- Times are integer Unix seconds. Signatures and nonces are base64url
  without padding; `key_id` is inside the signed body.
- Shared vectors: `vectors/canonical.json` (incl. the RFC 8785 UTF-16 key
  order example and the refusals) and `vectors/signing.json` (a published
  TEST key; every server refuses it as `NODE_SIGNING_KEY`).

**Capability contracts (brief D7)** in `backend/nodes/capabilities/v1/`: the
eight read capabilities. Amounts are integer micro-USD (`usd_micros`,
`total_usd_micros`); `output_sha256` is sha256 of the canonical JSON of
`output` (brief follow-up 1). A catalogue entry is invocable only on equal
name, version and sha256 of both schemas.

**Pairing (brief D4):** `POST /api/v1/nodes/pair` (anonymous, throttle
`node_pair`, fail-closed). `node_id` is the node's `TrustedDevice` id, so it
is the same id in the pairing and rotate responses and every envelope. The
response names the active signing key only (a retired key verifies but is
never pinned by a new node). The signing key and protocol version are
checked before any code is consumed.

**Audit chain (decision 10, brief D8):** `NodeInvocation` rows are written
only by `nodes.audit_chain.append` under `select_for_update` on the single
`NodeAuditHead`, append-only by trigger (`nodes/0002_append_only_chain`),
every FK `PROTECT`; `manage.py verify_node_audit_chain` reports the first
break by `seq`. `row_hash` covers every column but itself (pinned by
`test_the_hash_covers_every_field_and_fks_as_ids`).

## Implementation notes — protocol v2 and the grant format (Phase 10 branch 1, 2026-11-20)

Brief `docs/prompts/phase-10-control/brief.md` D1, D2, D3, D5. Phase 9's
protocol v1 (branch 2, `feat/p9-node-protocol`) was not yet merged on the
integration branch, so `feat/p10-protocol-v2` merges it as its base.

**Negotiation (D1).** The node offers `friday-node.v2, friday-node.v1`;
`nodes.protocol.messages.negotiate_subprotocol` picks the highest common
version (None: close 4400). The path stays `/ws/node/v1`, and pairing keeps
`protocol_version: 1` as its baseline: the session version is per socket.
A v1 session gets read invokes only.

**Schemas (D1).** `backend/nodes/protocol/schemas/v2/`: every v1 type at
`v: 2`; `result` gains `exit_code` (-255..255 or null), `output_bytes`,
`tail` (≤ 4 096 UTF-8 **bytes**, the new `x-friday-max-bytes` keyword the
Swift port mirrors), `confirm_state` and `sandbox_violation`; two
node → server types, `confirmation` and `output` (`seq` 0..65 535, `data`
≤ 8 192 bytes). `MESSAGE_TYPES[version][direction]`; the server → node
set is the same frozenset in both versions (no cancel, no policy type).
The policy regex and the bounds meta-test cover v2, the action contracts
and the recipe params; `invoke.grant` stays the one reserved property.

**Domain separation (D1).** v2 signs `b"friday-node/invoke/v2\n" +
canonical(envelope − sig)`; grants sign `b"friday-node/grant/v2\n" +
canonical(grant − sig)`; v1 keeps the bare canonical body so paired v1 nodes
keep verifying. The domain follows the body's own `v`, which is signed.
Vectors: `vectors/signing_v2.json` (read and confirm envelopes, the signed
bytes, and signatures in the wrong domain that must not verify).

**The grant (decision 4, D2).** `schemas/v2/grant.schema.json`, embedded
verbatim as `invoke.grant` (a test pins it): `{v: 2, grant_id, approval_id,
node_id, workspace_id, capability, capability_version, class: "gate",
args_sha256, nonce, issued_at, expires_at, key_id, sig}`. Same key as the
envelope. Every shared field (`node_id`, `workspace_id`, `capability`,
`capability_version`, `args_sha256`, `nonce`, `issued_at`, `expires_at`)
must be equal between grant and envelope, type-strictly. `MAX_GRANT_TTL_SECONDS
= 120` is refused at build (`nodes.protocol.grants.build_grant`,
`build_gated_invoke`) and again by the node. Vectors: `vectors/grants.json`
(a gated envelope, the signed bytes, and every D5 v2 refusal 13–20 as a
frame a compromised server could sign, with its expected code and check).
Issuing a grant only from an approved, fresh, unused `node_action`
approval is server-side policy (Phase 10 branch 3, `nodes.grants.issue`).

**Verification order (D5).** `nodes/protocol/verify.py` is the pure-Python,
protocol-level part of the node's order: v1 checks 1–16 and v2 checks 1–21
(class 13, grant presence 14, grant key 15, grant signature 16, binding 17,
grant TTL 18, args hash 19, own args schema plus recipe params 20,
availability 21), first failure wins, nothing echoed. Checks 22–24 (local
policy, the confirmation window, the post-click re-check) are node-local
(Phase 10 branch 2 reference node; Mac session M1). With equal times
(check 17) and the class TTL ceiling (check 10), check 18 is defence in
depth: a 121 s gated envelope is refused at 10 with the same code.

**Capability snapshots (D1, D3).** Each snapshot entry records the
session's `protocol_version`; `nodes.services.record_capability_snapshot`
writes a fresh row when the catalogue hash **or** the session version
changes, so a node reconnecting on v2 does not keep its `protocol_v1`
verdicts. `invocable_entry(snapshot, contract)` is the check branch 3 runs
before signing a grant.

## Implementation notes — grant issuance and gated invocation (Phase 10 branch 3, 2026-11-20)

**Issuance (decision 4, brief D2).** `nodes.grants.issue(execution)` is the
only code that signs a gate-class envelope. It runs with the
`NodeActionExecution` row locked and refuses, signing nothing, unless (in
this order): the approval's `requested_by` and `decided_by` are both the
node's active owner holding the `owner` role in the approval's workspace
(`server_refused:actor_not_owner`, first); the approval is `approved` and
its tool `node_action`; it was decided at most
`NODE_ACTION_DISPATCH_MAX_DELAY_SECONDS` (60) ago (else `stale_dispatch`);
the stored snapshot's `args_sha256` equals the recomputed hash and names
this node and capability (`args_changed`); `check_gate_request` accepts
the args (before `build_gated_invoke` builds anything); the node is present
(`node_offline`), its device may act (`kill_switch`) and its latest snapshot
still lists the capability invocable at the same version, class and schema
hashes (`not_invocable`); a `repo_path` still resolves to the approval's
workspace (`path_not_in_workspace`); a signing key exists. The grant lives
`NODE_GRANT_TTL_SECONDS` (90; a boot check refuses anything above 120).
Single use: the node's nonce cache, `grant_issued_at` set once under the
row lock (a second dispatch finds the row no longer `approved`), and the
`OneToOne` between execution and approval.

**Fail closed (decision 6).** Nothing is queued to run later: offline at
dispatch is `node_offline` and consumes the approval; a dispatch later than
60 s after the decision is `stale_dispatch` (also by the per-minute beat
backstop when the task never ran); a `node_action` approval expires 10
minutes after creation (per-tool approval TTL); nothing is retried. The
gateway session (`nodes.gateway.NodeSession`) accepts `confirmation`,
`output` and `result` only for invocations in its own in-flight set, runs a
watchdog per invocation (`expires_at` + `max_runtime_seconds` + 30 s →
`timeout`) and ends every in-flight invocation `node_disconnected` on
socket loss. The server→node message set is unchanged.

**Audit.** Every execution ends with exactly one `NodeInvocation` chain row
(`hash_version` 2, hashing `invocation_class`, `approval_id`, `grant_id`,
`exit_code`, `confirm_state`; Phase 9 rows stay version 1 and verify
unchanged) and one `nodes.invoke` AuditLog row. The node's `tail` is
re-scrubbed by the server (the registered output scrubber, fail closed to
"[çıktı gizlendi]") before it is stored; `output` frames are scrubbed,
counted and dropped until the HUD terminal stream (Phase 10 branch 5).

## Implementation notes — Phase 9 closeout (`chore/p9-closeout`, 2026-11-20)

**What the server half delivers (Phase 9 branches 1–7, closeout).** The
decisions of this ADR are implemented server side and exercised against the
Python reference node (`backend/nodes/reference/`), which stands in for the
deferred Swift app:
- identity (decision 2): `TrustedDevice.kind=node`, `jnd_` secrets, rotation
  with grace, the 90-day age cap, revocation and the per-owner kill switch
  (branch 1, spike S9.0 server half);
- transport (decisions 3, 6): the separate `node-gateway` ASGI process
  (`config.asgi_nodes`, uvicorn + `wsproto`, loopback only), Redis as the
  invoke bus, fail closed on any outage, no durable queue (branch 4);
- signed envelopes and pinning (decision 4): Ed25519 over canonical JSON,
  the key handed out at pairing and stored as `Node.pinned_key_id`
  (branch 2); rollover is re-pairing in Phase 9 (brief Q3);
- policy stays on the Mac (decision 5): no message type, endpoint, field or
  UI control writes node policy; `grant.availability_changed` only reports
  it (branches 4, 6, 7);
- the hook relay (decision 7): node `event` frames reach the 8A ingestion
  port through a sink the composition root registers; acks carry counts only
  (branch 6);
- the import contract (decision 9) and the hash-chained audit
  (decision 10): `NodeInvocation`, `verify_node_audit_chain` (branches 2, 5).

**Operations (closeout).** `manage.py preflight` gains six node checks
(`ops/preflight.py` `check_nodes`, read-only, no model call, nothing sent to
a node): `node_gateway` (`/healthz`, Redis), `node_signing_key` (a usable,
non-test key; the same key id in web, worker -- the `nodes.signing_key_id`
Celery task returns the id only -- and node-gateway; every active node pinned
it), `node_link` (per node; a `warn` when none is paired), `node_tls_pin`
(the served SPKI against `NODE_TLS_PIN_SHA256`), `node_audit_chain` and
`node_conformance`. The HUD shows failing check names only. The runbook is
`docs/runbooks/friday-node.md`.

**The conformance suite is the contract.** `ops/tests/test_node_e2e.py`
pairs the reference node through `POST /api/v1/nodes/pair`, connects it to
the real `NodeSocketApp`, answers "Claude Code oturumlarım ne yapıyor?" in a
FakeAdapter chat turn with fenced rows of the conversation's workspace only,
checks the chain and audit rows, revokes the node over the web
(`bye{revoked}`, socket closed, the read tools no longer offered) and
refuses its reconnect (4401). The Swift app must pass the offline and live
conformance runs and the server-side contract tests listed in the brief's
"Mac session (deferred)" (M1–M3) before it is used; the Keychain half of
the spike, the Swift conformance pass and TCC across notarized updates are
**pending owner evaluation** (brief "Go/no-go record").

## Implementation note — Phase 8B closeout (`chore/p8b-closeout`, 2026-11-20)

Completes the owner-turn note above. State at the 8B closeout:

- Phase 9's server half is merged: `nodes.services.invoke_read` takes the
  `channel` keyword and runs the step-0 actor check (`actor_not_owner`)
  before signing. `SYSTEM_READ_CAPABILITIES` is still empty, the two
  owner-turn contracts still live in `backend/integrations/node_reference/
  contracts/v1/`, and the transcript pull still uses its `NodeReadPort` seam
  with the default `UnavailableNodePort` (a no-op). Moving the contracts
  into `nodes/capabilities/v1/` changes the v1 contract set, the reference
  node's catalogue and the conformance files, so it is its own reviewed
  branch (Phase 9 brief "Known follow-ups (from `feat/p9-node-reads`)" 1;
  8B brief, closeout owner steps), not part of the closeout.
- The closeout's end-to-end test runs the pull against the Python reference
  node double (the D13 readers over the synthetic fixtures, one chain row
  per invoke). It is the server-side contract the live Mac node must pass
  in the Mac session (8B M1), with the live node in place of the double.
- `manage.py preflight` `transcripts` reports, per enabled source, whether
  a node port is installed, the owner's node is reachable, the capability
  is granted and the last pull's status. No port, no node or no grant is a
  `warn` (the Swift readers and the Touch ID grant are deferred to the Mac
  session); a `failed` or `server_refused` last pull is a `fail`. It sends
  nothing to a node.
