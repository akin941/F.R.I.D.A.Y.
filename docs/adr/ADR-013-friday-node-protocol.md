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
