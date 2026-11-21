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
| `uvicorn` | `==0.54.0` (planned) | BSD-3-Clause | `feat/p9-node-gateway` | transitive (MCP SDK) | Serving `config/asgi_nodes.py` (`--ws wsproto`) |
| `wsproto` | `==1.3.2` | MIT | `feat/p10-reference-executor` (2026-11-20; planned for `feat/p9-reference-node`) | new (its one dependency, `h11` 0.16.0, was already locked) | The WebSocket implementation on both ends: the reference node's client transport and the hostile fake gateway (`node_conformance_server`) now, uvicorn's server side in `feat/p9-node-gateway` |

The `uvicorn` row is filled in by its branch; it is listed here so the
gateway's full dependency surface is in one place.

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
- **Pinned version:** _not yet recorded_ (pending owner step).

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
