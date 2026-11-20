# Phase 10 — Control, with approval (stub)

> Status: **Stub (2026-11-20)** from the owner-approved Friday 3.0 roadmap
> (`docs/research/2026-11-roadmap-proposal.md` §B1, §B6, MASTER_PLAN §AA
> "Friday 3.0 phases", order 6). **The full brief is written at phase
> start**, in the Phase 5/6/8A format, and reviewed by an independent
> Sonnet 5 subagent before branch 1.
> Parent decisions: ADR-004/ADR-008 decision 3, ADR-009 (unchanged),
> ADR-013 (invocation grants), **ADR-014** (node action classes:
> gate-class by default), ADR-015 (voice only proposes), ADR-003.
> Precondition: Phase 8B closeout (Phase 9 already merged); the owner's
> third-party terms check (d4) before any coding-agent driver is enabled.

## Goal

"Testleri çalıştır", "Codex ile başlat": proposed by voice or text,
approved on screen, confirmed on the Mac, run safely — every command
execution gate-class until ADR-014's evidence bar says otherwise.

## Scope (to be split into branches at phase start)

- Gate-class node capabilities: allowlisted repo commands under `srt`
  (stripped environment, denied reads of `~/.ssh`, `~/.aws`,
  `~/.config/gh`, `~/.claude`, `~/.codex`, Keychain files and browser
  profiles), an AppleScript recipe catalogue, AX primitives; open/focus
  app as the one confirm-class action (local bundle-id list).
- Invocation grants bound to the `ApprovalRequest` (args hash, nonce,
  ≤ 120 s, single use) and the node's own confirmation window; owner-away
  rules (60 s local timeout, 10 min approval expiry, nothing queued).
- The ADR-014 test/lint evidence work (throwaway worktree, network
  denied, escape tests); its result recorded as an ADR-014 amendment or a
  "stays gate-class" note.
- HUD terminal panel streaming fenced, untrusted output.
- Harness v3: Claude Agent SDK and Codex app-server drivers, each pinned
  with generated types and a contract test per pin; a Codex
  `requestApproval` becomes an `ApprovalRequest`. The Codex app-server is
  a product feature (Friday driving the owner's sessions), not a return of
  Codex to this repo's own review workflow (CLAUDE.md).
- Trust check (Nod pattern): a cheap gateway call compares gate-class
  arguments with the owner's recent typed utterances (voice counts as
  weaker evidence); it can warn or block, never approve.
- Voice tools that only propose (ADR-015).
- Fuzz and injection suites for capability argument schemas.

## Go/no-go checkpoint

Fuzz/escape suites green; the ADR-014 test/lint decision recorded.

## Owner steps (pending) — recorded 2026-11-20

1. Check the Claude, Codex and Antigravity terms for automation (d4) and
   record the result before any driver is enabled.
