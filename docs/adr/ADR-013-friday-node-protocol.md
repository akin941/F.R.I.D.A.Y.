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
**Amended 2026-11-25 (d9 revised):** signing uses a free Apple ID Personal
Team identity, not a notarized Developer ID build — see "Amendment
2026-11-25 (d9 revised)" at the end.

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
1. **Shape (d1, d9).** One signed Swift macOS menu-bar app,
   `friday-node`, with the Python voice pipeline as a child process from
   Phase 11. It opens one **outbound-only** WSS connection to the server and
   has **no TCP listener**. ~~Signing uses the owner's Apple Developer ID~~
   *(amended 2026-11-25)* Signing uses the owner's free Apple ID Personal
   Team "Apple Development" identity with hardened runtime; the app is not
   notarized and not distributed (paid Developer ID + notarization is an
   optional later path). The signing key stays on the owner's Mac and CI
   never holds it.
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
- ~~The owner pays for an Apple Developer membership (99 USD/year, d9)~~
  *(amended 2026-11-25)* The owner needs no paid Apple Developer membership
  while the node runs only on their own Mac; they keep a Personal Team
  signing key on the Mac. Paid membership returns only if the Personal Team
  spike fails or the owner later wants distribution or auto-update.
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
the spike, the Swift conformance pass and TCC across notarized updates (since
2026-11-25: across rebuilds signed by the same Personal Team identity) are
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

## Implementation notes — Phase 10 closeout (`chore/p10-closeout`, 2026-11-20)

**What the server half of Phase 10 delivers (branches 1–7, closeout).**
Decisions 4 (invocation grant), 5 (policy is local-only) and 6 (fail closed,
owner away = deny) are implemented server side and exercised against the
Python reference node v2 (`backend/nodes/reference/`), which stands in for
the deferred Swift app:
- protocol v2 (`friday-node.v2`, negotiated per socket; pairing still
  answers the v1 baseline): one new class of node → server frames
  (`confirmation`, `output`, the extended `result`) and **no** new
  server → node type (branch 1);
- the grant: domain-separated Ed25519 over the canonical grant, single use,
  ≤ 120 s (`NODE_GRANT_TTL_SECONDS`, 90 by default), bound to an approved,
  fresh, unused `node_action` approval of the node's owner; issued only by
  `nodes.grants.issue` after `check_gate_request`; an approved action not
  dispatched within 60 s fails `stale_dispatch` and is never retried
  (branch 3);
- policy stays on the Mac: the server proposes from its own 8A path rules
  and the closed `command_kind` vocabulary; an unmapped combination is a
  node refusal the owner fixes locally (brief Q2); the confirmation window
  is rendered by the node from its own validated args (branch 2);
- fail closed: a socket loss, the kill switch or the watchdog ends every
  in-flight execution terminally (`node_disconnected`, `timeout`) and the
  node closes an open window; the output stream is scrubbed server side
  (`nodes.output.StreamScrubber`, tail re-scrub, the fail-closed marker
  without a scrubber) before it reaches Redis, the HUD or the stored tail
  (branches 3, 5);
- audit: chain row v2 (`hash_version` 2, class, approval and grant ids,
  hashes only) and one `nodes.invoke` AuditLog row per execution.

**Operations (closeout).** `manage.py preflight` gains the control checks
(`ops/preflight.py` `check_control`, read-only, no model call, nothing sent
to a node): `node_grant_config`, `node_protocol_v2` (per node; a `warn`
while no node speaks v2), `node_conformance_v2`, `node_escape_suite`,
`node_output_scrubber` (the gateway's `/healthz` now reports
`output_scrubber`), `trust_check`, `node_action_actor` and
`harness_drivers`. `manage.py run_reference_node` offers v2 by default, so
the dev stack's preflight is green with the reference node paired. The
runbook section is "Control" in `docs/runbooks/friday-node.md`.

**The contract.** `ops/tests/test_control_e2e.py` pairs the reference node
v2 through the endpoint, connects it to the real `NodeSocketApp` on v2, and
runs a typed "acme'de testleri çalıştır" from the router's draft through the
card, the trust check, `/approvals`, one grant, the node window and the
fake executor into the HUD terminal stream, with one `ok` chain row (v2) and
one audit row; the same turn as voice yields a draft only; a confirm-class
`apps.open` runs with no `ApprovalRequest`; the kill switch while a window
is open closes the socket and the window and ends the execution
`node_disconnected` with nothing run. The Swift app must pass the
conformance v1/v2 runs and the contract tests of the Phase 10 brief's "Mac
session (deferred)" (M1–M2); its measurements (M4) are **pending owner
evaluation**.

## Implementation notes — the voice child and `friday-voice/v1` (Phase 11 branch 2, 2026-11-20)

Branch `feat/p11-voice-child-protocol` (Phase 11 brief D1, D2, D6, D7,
D16) makes decision 1's "Python voice pipeline as a child process"
concrete. Status of decisions 1, 6 and 8 is unchanged; this note records
how the child honours them.

- **The child.** `node/friday-voice/` is its own uv project (exact pins and
  `uv.lock`, console script `friday-voice`), outside the signed bundle
  (`make friday-voice-install` installs it under
  `~/Library/Application Support/Friday/voice/`; the host checks the
  installed `uv.lock` sha256). It imports nothing from `backend/` and the
  backend imports nothing from it (tests on both sides).
- **Stdio only.** The host spawns `friday-voice run` with stdin/stdout
  pipes; stderr is the child's scrubbed log (the vendored ADR-012
  scrubber, a generated byte-identical copy with `--check`, the reference
  node's pattern). The child opens **no listening socket**: an AST scan
  refuses `socket`, `socketserver`, `http.server`, `uvicorn`, server
  modules and `.bind/.listen/start_server/serve` calls in the package, and
  a runtime test reads `/proc/<pid>/fd` against `/proc/net/*` after
  `friday-voice run` starts under the reference host (with a control
  process that does listen). Decision 1's "no TCP listener" now covers the
  child too.
- **`friday-voice/v1`.** A length-prefixed frame (u32 big-endian, ≤ 1 MiB)
  carrying a canonical UTF-8 JSON header line and, for `audio` and `play`
  only, a PCM16 payload (`pcm_bytes`, ≤ 64 KiB, even). Two frozen sets:
  host→child `hello, credential, audio, playback, mode, mute, ptt, link,
  shutdown`; child→host `ready, state, play, stop_playback, earcon,
  release_mic, log`. Every schema is closed and bounded, numbers are
  integers only, and the node protocol's `POLICY_SHAPED` regex (copied
  verbatim; a test compares the literal) matches no type or property name,
  with no reserved exception. No child message changes the host's mode,
  hotkey, mute, device, credential or node policy (decision 5 holds), and
  the child cannot ask the host to open, run or send anything.
- **Fail closed (decision 6) on both ends.** The child releases the mic
  and drops its buffers on `link: down`, `mute: true|unreadable`, `mode:
  off`, credential expiry, a host protocol error, stdin EOF and `shutdown`,
  and sends nothing at all before the first valid `credential`. The host
  releases on any child protocol error, an unknown or policy-shaped frame,
  odd PCM, a missing `state` for 10 s (watchdog) and child exit, then
  restarts the child with backoff 1 s → 60 s; it releases on its own
  fail-closed inputs whatever the child reports.
- **Conformance.** `friday_voice/conformance/cases/{host,child}/*.json` and
  `friday_voice/protocol/vectors/frames.json` are generated from one
  definitions module and checked by `friday-voice conformance --check`
  (the `build_node_conformance --check` pattern). The reference host
  (`friday_voice.reference.host`, with `EchoDouble`) passes every host case
  and the protocol shell (`friday_voice.child`) every child case, in
  process and as real processes (`--live`; the hostile child is
  `friday-voice conformance-child --case <id>`). These files are what the
  Swift host's test target loads (Phase 11 Mac session M1).
- **Decision 8, the detector reading.** *A detector is an on-device
  classifier that emits only scores; anything that emits text or tokens is
  a model call and goes through the gateway.* openWakeWord, Silero VAD and
  smart-turn v3 therefore run inside the child (as openWakeWord and Silero
  already run in the browser under ADR-011 decision 2); STT and TTS never
  do: they stay `InferenceGateway.transcribe()` / `.synthesize()` behind
  Friday's voice endpoints (ADR-003, one `ModelUsage` per call). The import
  guard refuses, in the child's package, `piper`, `whisper`,
  `faster_whisper`, `mlx_whisper`, `mlx`, `ctranslate2`, `pywhispercpp`,
  `openai`, `anthropic`, `google.generativeai`, `google.genai`, `litellm`,
  `kokoro` and `pipecat.services.*`; a backend test refuses `import piper`
  anywhere in the repository. The child holds no provider key and never
  the node secret: only the short-lived `node_voice` token, received on
  stdin (Phase 11 D3).

## Implementation notes — the `voice.state` event kind (Phase 11 branch 7, 2026-11-20)

- **One new node → server event kind, v2 only.** `nodes.protocol.messages`
  gains `VOICE_STATE_KIND = "voice.state"` and `EVENT_KINDS_BY_VERSION`;
  `EVENT_KINDS` (v1) is unchanged, so the v1 `event` schema keeps its two
  kinds and a `voice.state` frame on a v1 session is a schema error (a frame
  error). The server → node set is untouched (`SERVER_TO_NODE_V2 is
  SERVER_TO_NODE`): nothing the server sends can arm, mute or change the
  node's mic (decision 5 holds).
- **Per-kind item schema.** The v2 `event` schema keeps its opaque per-kind
  hole for the other kinds and adds `$defs.voice_state_item`, applied by one
  `allOf: [{if: kind == "voice.state", then: batch = exactly one item,
  spooled = false}]`. The item is closed and every field bounded and
  enumerated: `state` (the nine `friday-voice/v1` child states), `mode`
  (`off` / `push_to_talk` / `always_on`), `session_open`, three daily
  counters (0–100 000) and `at` (node clock, Unix seconds, ≤ 2100). No
  audio, no text; the policy-shaped guard walks it. The schema meta-tests
  now walk `$defs` and conditional branches, and a `$ref` must be local.
- **Relay rules (the reference node, and the Swift host in Mac session
  M1).** Validate the item before sending (an unknown state, an
  out-of-range counter or an extra key is dropped and counted); never on
  v1; only after `hello.ack`; at most one per second, coalesced with the
  latest state winning; re-sent with every heartbeat (so a state older
  than two heartbeats means the node stopped reporting) and after a
  reconnect; never spooled; never waiting for its `event.ack`.
- **Server.** `nodes.events.apply_voice_state` stores the item on the
  `Node` row (`voice_*` fields, migration `nodes.0004`) with the server's
  receive time as `voice_state_at`, at most once per node per second (a
  frame inside that second is acked `duplicate` and writes nothing). The
  kind takes no sink (`SINK_KINDS` unchanged) and never reaches the invoke
  bus. `hud` reads it through `nodes.selectors.node_health` (no `voice`
  import, no import contract changed).
- **Conformance.** Nine cases (`v1_voice_state_never_sent`,
  `v2_voice_state_*`) use a new hook op `{"op": "voice_state", "state"}`
  (the scripted child state) and the report key `voice_states_dropped`;
  negative-control tests prove that a node which relays unchecked items,
  sends the kind on v1 or does not coalesce fails them.

## Implementation notes — Phase 11 closeout (`chore/p11-closeout`, 2026-11-20)

- **The voice child's contract files are locked.** `friday-voice
  conformance --build` now also writes `node/friday-voice/conformance.lock`
  (sha256 of every host/child case file, `frames.json` and the frozen v1
  schemas); `--check` fails on its drift. The server's
  `friday_voice_conformance` preflight row hashes the checkout against it
  with hashlib only (the backend imports nothing from the child) and warns
  where no `node/friday-voice` exists (the backend image).
- **`node_voice_state`** (preflight, per active node): passes for a node
  whose `voice.state` is stored, warns while none reports — the Swift relay
  is the deferred Mac session (M1).
- **The e2e** drives the reference host (`HostCore`, paced playback, the
  echo double) and a real child process over stdio against a live server;
  the child's last `state` frame, relayed as the node's `voice.state`
  event through `nodes.events.handle_event`, is what the HUD shows; after
  the kill switch the HUD's mic state is `off` and the host, its link
  dropped, shows `off` with the mic released (decision 6).
- Decisions 1, 6 and 8 are unchanged: the child stays a stdio-only,
  model-free, keyless process; the server→node message set is untouched.

## Amendment 2026-11-25 (d9 revised)

**Owner decision 2026-11-25.** `friday-node` runs only on the owner's own
Mac. It is never distributed to other Macs and is installed from a local
Xcode build. So the paid Apple Developer Program (99 USD/year), the
Developer ID Application identity and notarization are **not required**.

- **Signing identity.** The app is signed with the free Apple ID **Personal
  Team** "Apple Development" identity (Xcode → Settings → Accounts), with
  hardened runtime. That identity is stable: the designated requirement
  stays the same from build to build, so TCC grants (Accessibility, Screen
  Recording, Full Disk Access, Microphone, Automation) and Keychain items
  written by build N should survive build N+1. Ad-hoc signing ("Sign to Run
  Locally") is **never** used for a build that holds TCC grants: its
  designated requirement is the code hash, so every rebuild loses them.
- **No notarization, no Developer ID, no distribution.** Gatekeeper
  assessment (`spctl`) of a Personal Team build is expected to report it as
  not notarized; that is accepted because the app is never downloaded from
  elsewhere.
- **Unchanged.** The signing key never leaves the Mac; CI never holds it.
  The conformance build stays a separate, unsigned debug configuration,
  never signed with the Personal Team identity and never installed as a
  login item, and the local build script still refuses conformance markers
  in a release build.
- **Verification first.** The first step of the Mac session is a spike
  (`docs/runbooks/mac-session-handoff.md` A1) that checks on the real Mac:
  (a) Personal Team signing works with hardened runtime and the needed
  entitlements (microphone, Apple Events/automation, keychain access, no
  App Sandbox requirement); (b) the certificate's validity and expiry, and
  what happens on renewal; (c) TCC grants persist across two rebuilds
  signed with the same identity; (d) Keychain items written by build N are
  readable by build N+1. The shipped build uses Xcode automatic signing,
  so the entitlements Xcode adds (application identifier, team identifier,
  keychain access groups) are kept; a manual re-sign uses that full set.
- **Keychain variant.** The node credential belongs in the data
  protection keychain, the only macOS keychain that honours
  `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`. If the spike shows
  it is unavailable to a Personal Team build, the outcome is PARTIAL: the
  file-based keychain would protect the secret only by the login keychain
  lock and an ACL bound to the designated requirement. Using it needs a
  dated, owner-acknowledged residual note in this ADR before Phase 9 M1;
  otherwise the paid fallback applies.
- **Fallback.** If the spike fails (a needed entitlement is unavailable to
  a Personal Team, or grants or Keychain items do not persist), the
  original path applies: enrol in the paid program, sign with Developer ID
  and notarize. The failure is recorded in the runbook and in MASTER_PLAN
  §O d9.
- **Optional later.** Paid enrolment + Developer ID + notarization is an
  optional later step, only if the owner wants to distribute the app to
  other Macs or use Sparkle-style auto-update.

Where earlier sections of this ADR or the briefs say "notarized build" or
"Developer ID", read "Personal Team signed build" and "the Personal Team
signing identity", and read "TCC across updates" as "TCC across rebuilds
signed by the same Personal Team identity".

## Implementation note — "which PC" and one node per call (Phase 13 branch 2, 2026-11-20)

`feat/p13-node-targeting` (Phase 13 brief D5–D7, D9). **No protocol
change**: no WS message, schema field, capability contract or
`friday-voice/v1` change; `build_node_conformance --check` and the
conformance locks are untouched. What changed is server-side only:

- **One resolver.** `nodes.targeting.resolve_target(owner, *, capabilities,
  source_text, origin_node_id, requested_node_id, requested_label)` decides
  the one node of every node read and node-action proposal, in this order:
  the owner's own words (a node name or alias, a room in locative form, a
  role or platform word; a named node that is offline, lacks the capability
  or is cut off is refused *naming it*, never replaced by another node);
  the speaking node of a `node_voice` turn (the voice token's
  `node_device_id`, now also on `ChannelPrincipal.node_device_id`); the only
  eligible node; the eligible node used last within
  `NODE_LAST_ACTIVE_WINDOW_SECONDS` (900), strictly later than the others;
  otherwise Friday asks ("Hangi bilgisayar: MacBook mu, Masaüstü mü?") and
  calls no node. The model's `node_id` / `node` argument is untrusted: it
  is only checked against that resolution (`target_mismatch`,
  `node_unknown`) and can neither decide nor settle an ambiguity. It
  replaced Phase 9's `nodes.selectors.read_node_for`.
- **`last_active_at`** is written (at most once a minute per node) by a
  relayed hook batch, a `node_voice` transcription request (injected into
  `voice` by `config/urls.py`) and a click on the node's own confirmation
  window (`approved` / `denied`); never by a heartbeat. It is node-
  influenced, so it only ranks candidates nobody named: an explicit mention
  and the speaking node always win, and the approval card plus the target
  node's own window still name the PC (ADR-014 unchanged).
- **One node per invocation.** `invoke_read`, `invoke_confirm` and
  `dispatch_gated` keep their single `node`; `NodeInvocation` and
  `NodeActionExecution` keep one `node` FK; `node_action` takes exactly one
  string `node_id`. `tools/tests/test_one_node_invariant.py` walks every
  registered node-reaching read tool with two online reference nodes and
  asserts every chain row of a call is the named node's and the other node
  receives nothing. The grant stays bound to its `node_id` (protocol v2):
  an approval for node A dispatched while node B is the active one still
  goes to A only.
- **Labels stay server-side.** The router's targets block carries each
  node's name, room, role and platform as data (prompt text, purpose and
  prompt version unchanged); the approval summary adds `node_room` and
  `node_platform`. Nothing is sent to a node.
