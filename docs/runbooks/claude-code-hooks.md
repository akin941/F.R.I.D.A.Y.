# Runbook — Claude Code hooks → Friday sessions panel

Phase 8A branch 3 (brief D6/D12, ADR-012); verified in the Phase 8A
closeout (preflight section 7, e2e `backend/ops/tests/test_learning_e2e.py`). Makes the owner's Claude Code
sessions show up in `GET /api/v1/memory/sessions` (and, from branch 6, the
HUD sessions panel) with live status. Everything below runs on the **owner's
Mac**; nothing here is needed on the server beyond the deployed code.

What leaves the Mac per event: the event name (`SessionStart`,
`Notification`, `Stop`, `SessionEnd`), the session id, the working directory
(`cwd`, used only for workspace mapping; the server keeps its last path
component as the project label), the notification type and Claude's
notification message (scrubbed on the Mac, cut to 200 chars), the
`SessionStart` source / `SessionEnd` reason, a timestamp and a random event
id. Never prompts, tool inputs, transcripts or file contents.

## 1. Prerequisites (server side, once)

1. Deployed with migrations applied (`manage.py migrate`) and the sources
   seeded (`manage.py seed_ingestion_sources`).
2. In the web app (or `PATCH /api/v1/memory/ingestion/sources/<id>`), enable
   the `claude_code_hooks` source.
3. Add **path-prefix mapping rules** for that source
   (`POST /api/v1/memory/ingestion/sources/<id>/rules`,
   `rule_type=path_prefix`), e.g. `/Users/<you>/code` → personal workspace,
   `/Users/<you>/code/acme` → the Acme firm workspace. The longest prefix
   wins. An event whose `cwd` matches no rule is **dropped** (counted in the
   audit view's unmapped list), never stored in a default workspace. Rules
   may target only workspaces you own. Use the absolute form (`/Users/...`):
   Claude Code sends absolute paths.

## 2. Mint the hook token

Web app → Integrations → "Yeni kişisel erişim token'ı" → kind **Claude Code
kancası** (`hook`), workspace = your **personal** workspace (any other
workspace is refused). Or `POST /api/v1/auth/tokens` with
`{"workspace": "<personal id>", "name": "claude-code-hook", "kind": "hook"}`
from a trusted web session. The raw `jpat_…` value is shown once.

This token can only post hook events; it cannot read anything and is refused
on every other endpoint. Rotate it like any PAT (revoke + mint); it expires
after its chosen lifetime (≤ 365 days).

## 3. Store the token in the Keychain

```bash
security add-generic-password -U -s friday-hook -a "$USER" -w
# paste the jpat_… token at the prompt (it never lands in shell history)
security find-generic-password -s friday-hook -w >/dev/null && echo stored
```

`friday-hook` reads it with `security find-generic-password -s friday-hook
-w` (1 s timeout). The first run may show a Keychain prompt — choose
"Always Allow" for `/usr/bin/security`. On a machine without a Keychain set
`FRIDAY_HOOK_TOKEN` in the hook's environment instead (never in a committed
file).

## 4. Install `friday-hook`

Requires `python3` ≥ 3.11 (standard library only). Either run it from a
checkout of this repo, or copy the two files side by side:

```bash
mkdir -p ~/.local/share/friday
cp backend/integrations/cli/friday_hook.py backend/integrations/cli/_scrubber_vendored.py \
   ~/.local/share/friday/
mkdir -p ~/.config/friday
printf '[hook]\nbase_url = "https://friday.example.internal"\n' > ~/.config/friday/hook.toml
```

(`FRIDAY_HOOK_URL` overrides the config file.) Re-copy both files after
every update of the scrubber; CI guarantees the vendored copy matches the
server's rules.

## 5. Add the hook entries to `~/.claude/settings.json`

```json
{
  "hooks": {
    "SessionStart": [
      {"hooks": [{"type": "command", "command": "python3 ~/.local/share/friday/friday_hook.py", "timeout": 5}]}
    ],
    "Notification": [
      {"hooks": [{"type": "command", "command": "python3 ~/.local/share/friday/friday_hook.py", "timeout": 5}]}
    ],
    "Stop": [
      {"hooks": [{"type": "command", "command": "python3 ~/.local/share/friday/friday_hook.py", "timeout": 5}]}
    ],
    "SessionEnd": [
      {"hooks": [{"type": "command", "command": "python3 ~/.local/share/friday/friday_hook.py", "timeout": 5}]}
    ]
  }
}
```

Merge with any hooks you already have. `friday-hook` always exits 0 within
about 3 s and prints nothing, so it never blocks or alters a Claude Code
session (a `SessionStart` hook's stdout would otherwise be added to Claude's
context).

## 6. Verify

1. Start `claude` in a mapped directory; within seconds
   `GET /api/v1/memory/sessions` (web session) lists it as `running`.
2. Trigger a permission prompt → `waiting_for_owner`; answer it and let the
   turn finish → `running`; exit → `stopped`. No event for 24 h → `stale`.
3. Paste a fake key (e.g. `sk-ant-api03-` followed by 40 random letters)
   into something that produces a notification; the stored
   `last_notification` shows `[SECRET:…]`, never the key.
4. Nothing arriving? `tail ~/.local/state/friday/friday-hook.log` (one line
   per failure: error class / HTTP status only), check the source is enabled
   and the `cwd` is covered by a rule (the source audit view lists unmapped
   keys).

## 7. Check it with preflight (Phase 8A closeout)

`make preflight` (or `manage.py preflight --no-model-calls`) prints
`learning_sources`: it warns while `claude_code_hooks` is enabled without a
path-prefix rule ("has no mapping rule (every item is dropped)") or paused,
and `learning_beat` fails if a learning task lost its beat. The hook source
never produces proposals (no owner text in 8A), so it does not show up on
the morning card; its sessions appear on `/sessions` and the HUD sessions
panel. Enabling the other sources, the audit view and forget/restore are in
`docs/runbooks/learning-memory.md`.

## Limits (8A)

- No spool: an event lost while the server is unreachable is gone; the panel
  only goes stale. Phase 9's node relay (ADR-013) adds a spool: see
  "Switching Claude Code hooks to the node" in `docs/runbooks/friday-node.md`.
  `friday-hook` stays the fallback, and both paths dedupe on the event id.
- No prompt text (`UserPromptSubmit`) and no transcripts: 8B, via the node.
- Forgetting the `claude_code_hooks` source pauses it, trashes its episodes
  and hides its sessions until restored.
