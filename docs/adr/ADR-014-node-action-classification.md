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

**Completed at the Phase 8B closeout (2026-11-20).** The classification is
unchanged and final for 8B: both owner-turn capabilities stay `read`, with
`data_class: personal`, and are the only capabilities a `channel=system`
read may name (the D13 amendment's `SYSTEM_READ_CAPABILITIES`). Being read
class does not make them ungated in practice: they need the per-directory
Touch ID grant on the Mac (ADR-013 decision 11), the pull is signed and
chain-audited per call, and nothing they return is approved data until the
owner approves a proposal built from it (ADR-012). The 8B closeout added no
confirm- or gate-class capability.

## Implementation note — capability classes in protocol v2 (Phase 10 branch 1, 2026-11-20)

Brief `docs/prompts/phase-10-control/brief.md` D1, D3. The server-known
action contracts live in `backend/nodes/capabilities/v2/` (one JSON file
each; the v1 reads stay in `v1/`):

| Capability | Class | Data class | Workspace from |
|---|---|---|---|
| `repo.command.run` (`repo_path`, `command_kind` ∈ test / lint / typecheck / build / format_check) | gate | firm | `repo_path` (`path_arg`) |
| `applescript.recipe.run` (`recipe`, `params` per recipe) | gate | personal; a repo recipe takes its repo's workspace | `recipe_params` |
| `ax.press` (`bundle_id`, `role`, `title`) | gate | personal | personal only |
| `ax.type_text` (`bundle_id`, `text`, `submit`) | gate | personal | personal only |
| `apps.open` (`bundle_id`) | confirm | personal | personal only |
| `apps.focus` (`bundle_id`) | confirm | personal | personal only |

- The confirm-class list is exactly decision 1's one kind (open/focus an app
  from a local list); every other action contract is gate-class, and
  test/lint (`repo.command.run`) stays gate-class (decision 3's evidence is
  pending, Phase 10 M3).
- The recipe catalogue v1 (`v2/recipes/`: `music.play_pause`,
  `music.next_track`, `system.volume_set`, `finder.reveal_repo`,
  `terminal.open_repo`) carries params schemas and Turkish confirm
  templates only; the AppleScript text exists only on the node. A recipe
  whose params have `repo_path` is a repo recipe, derived from the schema.
- Each contract's `confirm_template` is rendered **by the node** from its own
  validated args (decision 2); the loader refuses a template whose `{slot}`
  names no real arg.
- Class negotiation (ADR-013, protocol v2): a confirm- or gate-class entry is
  invocable only on a `friday-node.v2` session whose advertised entry equals
  the contract on name, version, **class** and both schema hashes; on a v1
  session it is `not_invocable` with reason `protocol_v1`, and an action
  entry advertised with another class is `class_mismatch`.
