# ADR-014: Friday Node Action Classification

## Status
Accepted 2026-11-20 — owner approval of the Friday 3.0 roadmap
(`docs/research/2026-11-roadmap-proposal.md` §B1, §g decision d7).
Applies from Phase 10 (`docs/prompts/phase-10-control/`); Phase 9 ships only
the read class. Extends ADR-008 decision 3 to node actions. **ADR-009 is not
amended.** Uses the protocol of ADR-013.

Numbering note: the proposal called this "ADR-013". ADR-011 is reserved by
the Phase 6 brief for "voice transport", so the proposal's ADRs are
numbered from ADR-012.

## Context
ADR-008 decision 3 splits writes into gate-class (an `ApprovalRequest`
decided by the owner) and confirm-class (reversible, self-scoped, in-app
rows; a human click). ADR-009 kept confirm-class from growing by precedent.
Actions on the owner's Mac (running commands, AppleScript, Accessibility
clicks, keystrokes into other apps) are neither in-app nor, in general,
reversible. A draft of the proposal tried to fit them under an ADR-009
amendment; review rejected that.

## Decision
| Class | Node actions | Who acts |
|---|---|---|
| read (ungated, audited) | the read capabilities of Phase 9 (session/thread lists and reads, `usage.ccusage`, `git.status` on registered repos, `apps.frontmost`, `windows.list`) | nobody per call; the per-directory grant was given once with Touch ID (ADR-013) |
| confirm-class (narrow exception) | open or focus an app whose bundle id is on a local list | a human click on the node's own window; the model never confirms |
| gate-class (**default**) | every command execution (tests, lint, build, dev servers), AppleScript recipes, AX click/type, keystrokes into other apps, screenshots, starting a write-mode coding agent, any file write, any outbound network | an `ApprovalRequest` decided in the web UI **and** a click on the node window, bound by an invocation grant (ADR-013) |

1. Anything not listed is gate-class.
2. "Confirm" for the node means a human click on the node's own window,
   rendered by the node from the arguments, never a server-rendered text.
3. **Evidence bar for test/lint.** Test and lint commands may move to
   confirm-class only by a later amendment of this ADR that records this
   evidence: the command runs in a throwaway git worktree created by the
   node, under `srt` with write root = that worktree, network denied,
   environment stripped (allowlisted variables only), CPU/time limits, and
   the worktree deleted afterwards; tests prove that a write outside the
   worktree, a network call and a sandbox escape attempt (including `/tmp`
   symlinks) all fail. Dev servers stay gate-class (long-lived, open ports).
4. A voice-originated turn can never execute a confirm-class or gate-class
   node action; it can only propose (ADR-015).
5. Screenshots taken on demand are used for one turn and never stored as an
   episode (ADR-012 decision 10).

## Consequences
- Phase 10 "terminal by voice" means "voice proposes, screen approves,
  Mac confirms" — two human acts for every command until the evidence bar
  is met.
- The confirm-class list grows by exactly one kind (open/focus app from a
  local list), by this ADR, not by precedent.
- A sandbox escape found later moves test/lint back to gate-class with no
  further decision needed.

## Implementation note — Phase 8B branch 8 (2026-11-20)

`claude.transcripts.owner_turns` and `codex.transcripts.owner_turns` (owner
turns of Claude Code sessions and Codex threads) are session reads and are in
the **read** class under this ADR's existing "session/thread lists and reads"
entry. Nothing moves between classes.
