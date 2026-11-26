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
  `srt` already on PATH only for the opt-in `pytest -m srt` tests and
  `manage.py node_escape_suite --executor srt` (local evidence on Linux; the
  ADR-014 evidence is the Mac run, M3). Without `srt` those tests skip and
  the command refuses to start.
- **Invocation:** `srt --settings <file> -- <argv...>`
  (`SRT_ARGV_TEMPLATE`); the exact flags are verified against the pinned
  version on the Mac (owner step, Phase 10 branch 2 record).
- **Pinned version:** `@anthropic-ai/sandbox-runtime` **0.0.83** (npm,
  published 2026-11-13; `dist.integrity`
  `sha512-yEusORnVJrNseF/9fvjVYyk6tr79/aFbIB3T/X8p2puLZdaaiGNeYN50v0CE+1DT70v7XuzKOAnTivh1f5cksA==`;
  source `github.com/anthropics/sandbox-runtime`). Installed 2026-11-27 on
  the owner's Mac (runbook A6) into a private prefix,
  `~/Library/Application Support/Friday/srt`, with
  `npm install --prefix … --ignore-scripts --save-exact` (the package has
  no install-time scripts; 4 runtime dependencies: `zod`, `commander`,
  `node-forge`, `@pondwader/socks5-server`). Needs Node ≥ 22.12 (the Mac has
  24). Not on the global PATH; the node app (B6) calls it by absolute path.
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
     (`srt` exits 1, "Shell 'bash' not found in PATH"). The opt-in
     `pytest -m srt` run passes once that one limit is skipped on darwin. B6
     must enforce `max_processes` another way on macOS (for example a
     process-group count, or `RLIMIT_NPROC` relative to the current count)
     and document it; the reference executor stays Linux-targeted.
  3. `manage.py node_escape_suite --executor srt` on the Mac: 30/32
     contained, with `limit_output` and `limit_runtime` not exercised — the
     escape scripts do not run on macOS even unsandboxed
     (`test_without_a_sandbox_the_scripts_really_escape` is a known
     macOS failure). The ADR-014 evidence run (Mac session M3) has to make
     the escape scripts macOS-capable first; 30/32 is not evidence.

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
