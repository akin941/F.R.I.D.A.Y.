# friday-node-linux — the Friday Node for Linux (Phase 13)

A **read-only** Friday Node for a Linux PC, paired with the one home server
(`docs/prompts/phase-13-home-hub/brief.md` D10, ADR-017). It is the Python
reference node (`backend/nodes/reference/`, the executable spec of the node
side of the Friday Node protocol, ADR-013) packaged as a daemon: vendored
and locked, never re-implemented, and it imports nothing from `backend/`.

What it does:

- offers protocol **v1 only** (`friday-node.v1`): read invokes, never an
  action (ADR-014 is trivially satisfied: no executor exists);
- reads, over your **real** home directory and only where you granted it:
  `claude.sessions.list/read`, `codex.threads.list/read`, `usage.ccusage`
  (only when `ccusage` is on `PATH`), `git.status` (registered repos only)
  and the 8B owner-turn reads (`claude.transcripts.owner_turns`,
  `codex.transcripts.owner_turns`); every reader is bounded and scrubbed and
  never returns tool results or file contents;
- advertises `apps.frontmost` and `windows.list` as `unsupported` (no
  portable, consent-respecting Wayland equivalent);
- relays Claude Code hook events (`friday-node-linux hook`) live or through a
  bounded spool (10 MiB, 24 h);
- fails closed: no listener of any kind, nothing in flight survives a lost
  socket, reconnect with backoff 1 s → 60 s (jitter), `bye{revoked}` ends it.

## Layout

| Path | What |
|---|---|
| `friday_node_linux/cli.py` | the console script (`pair`, `run`, `hook`, `grant`/`revoke`, `repo`, `policy show`, `config set server-url`, `rotate`, `status`, `unpair`, `conformance`) |
| `friday_node_linux/api.py` | the two HTTPS calls over pinned TLS: pairing and secret rotation |
| `friday_node_linux/daemon.py` | `LinuxNode` (the vendored `ReferenceNode` with this node's `hello` identity), `PinnedNodeClient`, `Backoff`, `Daemon` (sessions, SIGHUP reload, hook relay, pid file) |
| `friday_node_linux/readers_root.py` | `LinuxReaders`: the vendored readers over `$HOME`, availability, the owner-turn vetted view, the catalogue |
| `friday_node_linux/policy.py` | `policy.json` (directory grants, registered repos): strict, 0600, atomic writes |
| `friday_node_linux/credential.py`, `tls.py` | the 0600 credential file; TLS pins (private CA and/or SPKI sha256) |
| `friday_node_linux/conformance.py` | the external conformance hook client (`conformance`) |
| `friday_node_linux/_vendor/` | **generated** copy of the backend's node code (`scripts/vendor.py`), checked by `vendor.lock` |
| `scripts/vendor.py` | vendor / `--check` (CI fails on any drift, one byte is enough) |
| `scripts/external_conformance.py` | the backend's hostile gateway + this node over localhost TLS |
| `../../deploy/home/friday-node-linux.service` | the systemd **user** unit |

## Files on the PC

| File | Mode | Written by |
|---|---|---|
| `~/.config/friday-node/policy.json` | 0600 | the interactive CLI only (the daemon only reads it) |
| `~/.local/state/friday-node/credential` | 0600 | `pair`, `config set server-url` |
| `~/.local/state/friday-node/spool/` | 0700 / 0600 files | the daemon (hook events not yet acked) |
| `~/.local/state/friday-node/daemon.pid` | 0600 | the daemon (the CLI sends it SIGHUP) |
| `$XDG_RUNTIME_DIR/friday-node/hook.sock` | 0600 in 0700 | the daemon (peer uid checked) |

A credential or policy file that is group/other readable, owned by another
user or a symlink is refused: the daemon does not start.

## Install

On the Linux PC, as your desktop user (not root), from a checkout:

```bash
make linux-node-install     # ~/.local/share/friday-node-linux (+ locked .venv), the user unit
```

`linux-node-install` copies this directory without tests and caches,
runs `uv sync --frozen --no-dev` there, and installs
`deploy/home/friday-node-linux.service` into `~/.config/systemd/user/`.
Put `~/.local/share/friday-node-linux/.venv/bin` on your `PATH` (or call the
binary by its full path).

## Pair

On the server, issue a **node** pairing code (`/devices` → "düğüm eşle", or
`manage.py issue_pairing_code --node --strong`). Then, with the private CA
of the home server (`docs/runbooks/home-server-setup.md` §5) or the served
certificate's SPKI pin:

```bash
friday-node-linux pair --server https://friday-home.local --code <code> \
    --ca ~/friday-home-ca.pem --name "Masaüstü"
# or: --tls-pin-sha256 <NODE_TLS_PIN_SHA256>
```

The node checks the TLS pin **before** the pairing code is sent, posts
`{code, node_name, protocol_versions: [1], platform: "linux"}` to
`/api/v1/nodes/pair`, and stores the one-time `node_secret` plus the
server's signing key in the 0600 credential. Nothing prints the secret or
the code. On `/devices` the node shows as *Linux*; set its room and role
there.

**Through the home server's Caddy** (`deploy/home/Caddyfile.example`):
pairing and `rotate` need `/api/v1/nodes/pair` and
`/api/v1/auth/nodes/credential/rotate` to reach Django, and the example
sends them to the BFF, which has no such routes. Until the owner decides
how those two paths are routed (Phase 13 brief, `chore/p13-closeout` owner
steps), pairing through Caddy fails; the backend tests pair
against Django directly.

## Grant

Nothing is readable until you grant it, at an interactive terminal:

```bash
friday-node-linux grant claude          # ~/.claude: sessions, usage, your own turns
friday-node-linux grant codex           # ~/.codex: threads, your own turns
friday-node-linux repo add ~/work/acme  # git.status counts for this repo
friday-node-linux revoke codex
friday-node-linux repo remove acme
friday-node-linux policy show
```

Each command prints the exact change and asks you to type the directory's
basename (`.claude`, `.codex`, `acme`) — the Linux stand-in for Touch ID.
No TTY or a wrong word changes nothing. A running daemon gets SIGHUP and
uses the new policy from its next session (the current one is closed and
reopened; the policy never changes during a session). No protocol message
can reach the file.

## Run (systemd user unit)

```bash
systemctl --user daemon-reload
systemctl --user enable --now friday-node-linux
journalctl --user -u friday-node-linux -f
```

The unit runs with `NoNewPrivileges`, `ProtectSystem=strict` and only its
state and runtime directories writable. To relay Claude Code activity, add
the command hook to `~/.claude/settings.json` (the events `SessionStart`,
`Notification`, `Stop`, `SessionEnd`):

```json
{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "friday-node-linux hook"}]}]}}
```

`friday-node-linux hook` always exits 0 within 3 s and prints nothing.

## Secret rotation

The daemon rotates its node secret when it is `rotate_after_days` old (30
by default, from the pairing response) before opening a session:
`POST /api/v1/auth/nodes/credential/rotate` with the current secret, over
the same pinned TLS; the new secret is saved (0600) before it is used. A
failed rotation keeps the current secret and is retried after an hour; a
secret past the server's hard cap (90 days) ends in
`bye{credential_expired}` and a re-pair. `friday-node-linux rotate` does it
by hand (restart the unit afterwards).

## Re-point (the VPS move, ADR-017 decision 7)

```bash
friday-node-linux config set server-url https://friday.example.net
systemctl --user restart friday-node-linux
```

The new server must present a certificate that passes the pins taken at
pairing (same private CA, or the same key for an SPKI pin); otherwise
nothing changes. The signing-key pins are kept too: a server with another
signing key is refused on every invoke (re-pair instead).

## Uninstall

```bash
systemctl --user disable --now friday-node-linux
friday-node-linux unpair                 # forget the credential; revoke it on /devices too
rm -rf ~/.local/share/friday-node-linux ~/.config/friday-node ~/.local/state/friday-node
rm ~/.config/systemd/user/friday-node-linux.service
```

## Develop and test

```bash
uv sync --frozen
uv run ruff check . && uv run ruff format --check .
uv run python scripts/vendor.py --check      # the vendored copy == backend/
uv run pytest
uv run python scripts/external_conformance.py  # every v1 conformance case, real TLS
```

`make linux-node-test` runs all of it (the CI `linux-node` job). After a
change to the reference node, the protocol or the contracts in `backend/`,
run `uv run python scripts/vendor.py` and commit the regenerated
`_vendor/` and `vendor.lock` with it. The backend's own tests
`backend/nodes/tests/test_linux_node_external.py` and
`backend/config/tests/test_linux_node_gateway.py` run this package as a
process (they skip where its `.venv` is missing; `FRIDAY_LINUX_NODE=1`
makes that a failure). The Phase 13 closeout adds two more: the home-hub
e2e (`backend/ops/tests/test_home_hub_e2e.py`, this daemon as
"Masaüstü" next to the reference node as "MacBook" on one live TLS
server) and the `linux_node_vendor` preflight row, which hashes
`vendor.lock` against the backend (`make preflight`).
