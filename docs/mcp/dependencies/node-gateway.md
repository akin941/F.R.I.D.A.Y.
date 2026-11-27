# Friday Node gateway — dependency manifest (Phase 9)

Governance record for the direct Python dependencies the Friday Node server
side adds (`docs/prompts/phase-9-friday-node/brief.md` D3,
`docs/security/mcp-governance.md`). Rules for every entry: an exact pin in
`backend/pyproject.toml`, the sha256 hashes in `backend/uv.lock` (installed
with `uv sync --frozen`), a licence review, and no install scripts run at
install time (wheels only for the platforms we deploy). Upgrades go in a
dedicated branch (brief R1), never as a side effect of another change.

| Package | Pin | Licence | Added by | Was | Used for |
|---|---|---|---|---|---|
| `cryptography` | `==50.0.1` | Apache-2.0 OR BSD-3-Clause | `feat/p9-node-protocol` (2026-11-20) | transitive (MCP SDK) | Ed25519 signing and verification of `invoke` envelopes (`nodes.protocol.signing`) |
| `jsonschema` | `==4.26.0` | MIT | `feat/p9-node-protocol` (2026-11-20) | transitive (MCP SDK) | Protocol v1 message validation and the capability contracts (`nodes.protocol.messages`, `nodes.capabilities`) |
| `uvicorn` | `==0.54.0` | BSD-3-Clause | `feat/p9-node-gateway` (2026-11-20) | transitive (MCP SDK) | Serving `config/asgi_nodes.py` (`--ws wsproto`) in the `node-gateway` process |
| `wsproto` | `==1.3.2` | MIT | `feat/p10-reference-executor` (2026-11-20; `feat/p9-reference-node` reuses the same pin unchanged, 2026-11-20) | new (its one dependency, `h11` 0.16.0, was already locked) | The WebSocket implementation on both ends: the reference node's client transport and the hostile fake gateway (`node_conformance_server`) now, uvicorn's server side in `feat/p9-node-gateway` |

The gateway's full dependency surface is in this table.

## `uvicorn` 0.54.0

- **Source and maintenance:** the Encode project (Starlette, httpx), very
  active, the reference ASGI server.
- **Why direct now:** Phase 9 branch 4 serves the node gateway process with
  it (`uvicorn config.asgi_nodes:application --ws wsproto --ws-max-size
  262144 --lifespan on --no-server-header`, compose service
  `node-gateway`). It was already locked at this version as a transitive
  dependency of the MCP SDK; relying on that would let an SDK bump move the
  server that faces the network.
- **Install:** pure-Python wheel with a hash in `uv.lock`; no new transitive
  dependency (`click`, `h11` were already locked). No `[standard]` extra:
  no `uvloop`, `httptools` or `websockets` -- the WebSocket implementation
  is `wsproto`, the same one the reference node uses.
- **Surface used:** the HTTP/1.1 server (`h11`), the `wsproto` WebSocket
  protocol, lifespan events. Published on `127.0.0.1:8002` only; exposed by
  the owner's TLS reverse proxy for `/ws/node/v1` alone
  (`docs/runbooks/friday-node.md`).
- **Residual risk (brief R1):** a uvicorn or `wsproto` CVE. Upgrades go in
  a dedicated branch.

## `wsproto` 1.3.2

- **Source and maintenance:** python-hyper (the `h11`/`h2` maintainers), a
  sans-I/O WebSocket (RFC 6455) state machine; 1.3.2 was released
  2025-11-20.
- **Why now:** Phase 10 branch 2 runs every conformance case over a real
  localhost WebSocket against the reference node (`nodes.reference.
  transport.NodeClient`, `nodes.conformance.gateway`). Phase 9 branch 3 had
  planned the same pin; this branch adds it first, and Phase 9 branch 4
  reuses it for uvicorn's `--ws wsproto`.
- **Install:** pure-Python wheel with a hash in `uv.lock`; its only
  dependency, `h11==0.16.0`, was already locked (uvicorn, httpx). No build
  step, no install script.
- **Surface used:** `WSConnection` (client and server), the `Request`,
  `AcceptConnection`, `RejectConnection`, `TextMessage`, `Ping` and
  `CloseConnection` events. No compression extension is negotiated.
- **Network:** localhost only in this branch (tests and the conformance
  command bind `127.0.0.1`).

## `srt` (Anthropic sandbox-runtime) — node-side tool, never a backend dependency

- **What:** the sandbox the node runs mapped commands under (Phase 10 brief
  D4): Seatbelt on macOS, bubblewrap on Linux. The settings document it
  reads is generated exactly as `backend/nodes/reference/sandbox.py` does
  and pinned by `backend/nodes/conformance/vectors/sandbox.json`.
- **Where it lives:** in the Mac app only (Mac session M2), at an **exact
  version recorded here by the owner** (owner step 4 of the Phase 10 brief)
  before M2 starts. It is not in `pyproject.toml`, not in `uv.lock`, not in
  any backend image, and CI never installs it.
- **Use on this side:** `nodes.reference.executor.SrtExecutor` calls an
  `srt` found on PATH only for the opt-in `pytest -m srt` tests and
  `manage.py node_escape_suite --executor srt` (local evidence on Linux; the
  ADR-014 evidence is the Mac run, M3). Without `srt` those tests skip and
  the command refuses to start. On the Mac `srt` is **not** on the global
  PATH, so put the private prefix's `.bin` in front for that one run (from
  `backend/`):

  ```bash
  PATH="$HOME/Library/Application Support/Friday/srt/node_modules/.bin:$PATH" uv run pytest -m srt
  ```

  (`srt`'s `#!/usr/bin/env node` shebang then needs a Node ≥ 22.12 on that
  PATH too; `/usr/local/bin/node` is 24.)
- **Invocation (reference):** `srt --settings <file> -- <argv...>`
  (`SRT_ARGV_TEMPLATE`); verified against the pinned version on the Mac
  (runbook A6).
- **Invocation (the Swift node, runbook B6, `Exec/SrtExecutor.swift`):** by
  absolute paths only, never through a PATH lookup or the shebang:

  ```text
  <node> <prefix>/node_modules/@anthropic-ai/sandbox-runtime/dist/cli.js --settings <private dir>/settings.json -- <mapped argv...>
  ```

  The location is the owner's choice in the node's policy UI ("İzinler" →
  "srt konumu…", stored with the command mappings in the keychain); the
  recorded choice is the default, `node = /usr/local/bin/node` and
  `prefix = ~/Library/Application Support/Friday/srt`. The node refuses a
  location (`repo.command.run` advertised `unsupported`) whose package is
  not 0.0.83 or whose Node is older than 22.12 (`node --version`, asked once
  with an empty environment).
  - `--settings` is **always** given. srt then refuses to run (exit 1) when
    the file is missing, empty or invalid instead of falling back to its
    built-in defaults.
  - The child gets the document's stripped environment (D4) plus
    `CLAUDE_CODE_TMPDIR=<run temp dir>`: srt otherwise hands the sandboxed
    command `TMPDIR=/tmp/claude`; with it, `TMPDIR` is the run's private
    temp dir, as D4 wants. srt adds its own proxy variables
    (`HTTP(S)_PROXY`, `ALL_PROXY`, …, with a per-run proxy password) inside
    the sandbox.
  - **Network is proxy-mediated:** srt starts an HTTP and a SOCKS proxy in
    its own (unsandboxed) process and the Seatbelt profile only lets the
    child reach those. With no allowed domain an HTTPS `CONNECT` is
    answered `403` (`curl: (56) CONNECT tunnel failed, response 403`);
    direct sockets are refused by the profile.
  - **ripgrep is not needed on macOS** (checked 2026-11-27 in the 0.0.83
    sources and by a run with `PATH=/usr/bin:/bin`, which has no `rg`):
    srt uses `rg` only on Linux (`linuxGetMandatoryDenyPaths`); its macOS
    backend builds the deny patterns without it.
  - `max_processes` is not `RLIMIT_NPROC` (finding 2): the node counts the
    run's process tree, followed by unique-id lineage (`p_puniqueid`, kept
    across re-parenting to launchd) from kqueue fork events and a 50 ms
    scan, and kills it (SIGKILL, per pid after re-checking unique id and
    start time) above the limit; `cpu_seconds` the same way, over the
    sampled CPU time of every member (posix_spawn cannot set `RLIMIT_CPU` on
    the child). Known limits: an intermediate process that forks and exits
    within microseconds can go unseen with its children, a fork burst can
    overshoot for one sample (`kern.maxprocperuid` is the backstop), and a
    process that lives shorter than a sample adds no CPU time.
    `Exec/ProcessTree.swift`.
  - **Integrity, not the version string:** the owner's Touch ID
    confirmation of the location stores the sha256 of the Node binary, the
    whole `<prefix>/node_modules` tree and `package-lock.json` in the
    keychain; every start and every availability answer recomputes them and
    a change is `unsupported` until re-confirmed (an srt or Node upgrade
    needs that step).
- **Pinned version:** `@anthropic-ai/sandbox-runtime` **0.0.83** (npm,
  published 2026-11-13; `dist.integrity`
  `sha512-yEusORnVJrNseF/9fvjVYyk6tr79/aFbIB3T/X8p2puLZdaaiGNeYN50v0CE+1DT70v7XuzKOAnTivh1f5cksA==`;
  source `github.com/anthropics/sandbox-runtime`). Installed 2026-11-27 on
  the owner's Mac (runbook A6) into a private prefix,
  `~/Library/Application Support/Friday/srt`, with
  `npm install --prefix … --ignore-scripts --save-exact` (the package has
  no install-time scripts; 4 runtime dependencies: `zod`, `commander`,
  `node-forge`, `@pondwader/socks5-server`). Needs Node ≥ 22.12 (the Mac has
  24, `/usr/local/bin/node`): the Swift spawner runs `cli.js` with that Node
  by absolute path. Not on the global PATH; the node app (B6) calls it by
  absolute path. **The transitive pins are the prefix's lockfile**,
  `~/Library/Application Support/Friday/srt/package-lock.json` (on
  2026-11-27: `@pondwader/socks5-server` 1.0.10, `commander` 12.1.0,
  `node-forge` 1.4.4, `zod` 3.25.76); a reinstall must reproduce it.
- **Verification against the pin (2026-11-27, runbook A6):**
  - CLI: `srt --version` → `0.0.83`; `srt [options] [command...]` with
    `-s, --settings <path>`, so `SRT_ARGV_TEMPLATE`
    (`srt --settings <file> -- <argv...>`) holds unchanged.
  - Settings keys: all six `settings` documents of
    `backend/nodes/conformance/vectors/sandbox.json` pass the package's own
    `SandboxRuntimeConfigSchema` (zod) — `network.allowedDomains`,
    `deniedDomains`, `allowUnixSockets`, `allowLocalBinding`,
    `filesystem.denyRead`, `allowWrite`, `denyWrite` all exist with these
    names and shapes. **No generator change, no vector regeneration.**
  - Smoke run of a vector document with real paths: a write in the
    worktree succeeds; a write beside it, a read of a `denyRead` path and an
    HTTPS fetch (network off) are all refused.
- **macOS findings for B6 (the Swift `SrtExecutor`):**
  1. `srt` on macOS runs the argv through a shell it finds on the child's
     `PATH` (`binShell`, default `bash`, resolved with `which`), quoting the
     argv itself; the mapped `PATH` must therefore contain `/bin`.
  2. `RLIMIT_NPROC` on macOS counts **every process of the user**, not the
     command's: with about 290 owner processes running, the reference
     executor's `RLIMIT_NPROC = 256` (`_set_limits`) makes every fork fail
     (`srt` exits 1, "Shell 'bash' not found in PATH"). With that one limit
     skipped on darwin, `test_a_real_srt_run` passes; the escape suite stays
     at 30/32 either way (finding 3). B6 enforces `max_processes` with a
     process-tree count instead (see the Swift invocation above); the
     reference executor stays Linux-targeted.
  3. `manage.py node_escape_suite --executor srt` on the Mac: 30/32
     contained, with `limit_output` and `limit_runtime` not exercised. Some
     escape scripts do not escape on macOS even unsandboxed (for example
     `read_ssh`), while others do (for example `write_home`), so
     `test_without_a_sandbox_the_scripts_really_escape` is a known macOS
     failure. The ADR-014 evidence run (Mac session M3) has to make the
     escape scripts macOS-capable first; 30/32 is not evidence.
  4. **Implicit writable paths (runbook B6, 2026-11-27).** srt 0.0.83 adds
     its own write allowances on macOS beyond the settings document:
     `/tmp/claude` and `/private/tmp/claude` (always, if they exist; they do
     not on the owner's Mac and the sandbox cannot create them, since `/tmp`
     is not writable) and the home "convenience" directories `~/.npm/_logs`
     and `~/.claude/debug` unless a `denyRead` entry covers them
     (`~/.claude` is denied; **`~/.npm/_logs` exists on the owner's Mac and
     is writable from inside the sandbox**). A `denyWrite` entry for
     `~/.npm` stops it (verified). The generator is the spec and the
     vectors pin it, so B6 does not add the entry; recommended follow-up: add
     `~/.npm` to `denyWrite` in `sandbox.py`, regenerate `sandbox.json`, and
     take it into the M3 evidence run.

## `cryptography` 50.0.1

- **Source and maintenance:** the Python Cryptographic Authority (pyca),
  very active, the de-facto standard binding for OpenSSL in Python.
- **Why direct now:** `nodes.protocol.signing` imports it. Relying on a
  transitive pin would let an MCP SDK bump move our signing library
  silently.
- **Install:** binary wheels (manylinux/macOS) with hashes in `uv.lock`; its
  `cffi` dependency is already locked. No build step runs on install.
- **Surface used:** `Ed25519PrivateKey` / `Ed25519PublicKey` (sign, verify,
  raw serialization) only. No TLS, no X.509, no key storage.
- **Secrets:** the server signing key comes from `NODE_SIGNING_KEY` (env
  only); it is never logged, never in a payload, an audit row or a DB row.

## `jsonschema` 4.26.0

- **Source and maintenance:** python-jsonschema (Julian Berman), active,
  the reference Python implementation of JSON Schema.
- **Why direct now:** `nodes.protocol.messages` and `nodes.capabilities`
  validate every frame and every capability's args/results with it.
- **Install:** pure-Python wheel; its dependencies (`attrs`,
  `jsonschema-specifications`, `referencing`, `rpds-py`) are already locked
  for the MCP SDK.
- **Surface used:** `Draft202012Validator`, extended with a full-match
  `pattern` keyword (the protocol's stricter semantics). No remote `$ref`
  resolution: every schema is self-contained and loaded from the repository.

## Runtime footprint

Both run inside the existing web, worker and (branch 4) `node-gateway`
processes. Neither opens a network connection. Neither is used by the
Swift node, which mirrors the schemas and vectors in
`backend/nodes/protocol/` (Mac session M1).
