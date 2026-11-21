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
| `wsproto` | exact pin (planned) | MIT | `feat/p9-reference-node` | new | The WebSocket implementation on both ends: uvicorn's server side and the reference node's client transport |

The last two rows are filled in by their branches; they are listed here so
the gateway's full dependency surface is in one place.

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
