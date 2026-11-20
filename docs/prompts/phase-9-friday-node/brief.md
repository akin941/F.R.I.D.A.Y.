# Phase 9 — Friday Node v1, read-only (stub)

> Status: **Stub (2026-11-20)** from the owner-approved Friday 3.0 roadmap
> (`docs/research/2026-11-roadmap-proposal.md` §B1, §c.4, §c.7,
> MASTER_PLAN §AA "Friday 3.0 phases", order 4). **The full brief is
> written at phase start**, in the Phase 5/6/8A format, and reviewed by an
> independent Sonnet 5 subagent before branch 1.
> Parent decisions: ADR-001, ADR-002, ADR-004/ADR-008 decision 3,
> **ADR-013** (node protocol — its identity decision is conditional on
> spike S9.0), ADR-014 (only the read class applies in this phase),
> ADR-012 (node data reaches memory through the memory-owned port),
> ADR-015 (mic indicator).
> Precondition: Phase 8A closeout; spike S9.0 passed; the owner's Apple
> Developer enrolment (d9).

## Goal

A signed, notarized Swift menu-bar app on the owner's Mac (`friday-node`,
d1) that lets Friday answer "Claude Code / Codex oturumlarım ne yapıyor?"
with local detail — read-only, with no way for the server to change node
policy or run anything.

## Scope (to be split into branches at phase start)

- **Spike S9.0 (before branch 1):** a `kind=node` principal on the
  existing device trust (`accounts.TrustedDevice`, `DevicePairingCode`):
  long-lived credential in the Keychain, rotation without re-pairing,
  immediate revocation, owner kill switch (web UI + `manage.py`),
  `revoke_all_devices` covers nodes. If it fails, the phase does not start.
- Server: new kernel app `nodes` (Node, capability snapshot, invocation
  audit rows, WS consumer) with the ADR-013 import contracts; role ×
  endpoint tests for the WS consumer (anonymous, wrong-kind token, revoked
  node, other-workspace actor).
- Node: outbound-only WSS with a pinned certificate/CA, `hello` with the
  node-owned catalogue, heartbeat 15 s and backoff reconnect, fail-closed
  behaviour, local-only policy behind Touch ID; no TCP listener.
- Read capabilities: `claude.sessions.list/read`, `codex.threads.list/read`,
  `usage.ccusage`, `git.status` (registered repos), `apps.frontmost`,
  `windows.list`; per-directory Touch ID grants for `~/.claude/projects`
  and `~/.codex/sessions`, off by default.
- Hook relay moves from `friday-hook` (Phase 8A) to `friday-node hook` →
  0600 Unix socket with a peer-uid check; bounded spool (≤ 10 MB, 24 h).
- HUD health panel: node link and last heartbeat; `manage.py preflight`
  gains node reachability and catalogue version.
- Compromised-server test suite (unknown capability, out-of-schema args,
  reused/expired nonce, bad signature, policy-mutation message): the node
  refuses every one.

## Go/no-go checkpoint

Spike passes; compromised-server tests pass; the notarized build keeps its
TCC grants across updates.

## Owner steps (pending) — recorded 2026-11-20

1. Enrol in the Apple Developer Program (99 USD/year) and create a
   Developer ID signing identity on the Mac (d9); the key never leaves the
   Mac and CI never holds it.
2. Run the spike S9.0 pairing and revocation checks on the real Mac.
3. Grant the per-directory reads with Touch ID when wanted.
