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
