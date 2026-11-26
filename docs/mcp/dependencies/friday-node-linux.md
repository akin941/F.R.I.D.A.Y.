# friday-node-linux — dependency manifest (Phase 13 branch 4)

Governance record for the Linux node package (`node/friday-node-linux`,
`docs/prompts/phase-13-home-hub/brief.md` D10,
`docs/security/mcp-governance.md`). It is its own uv project: exact pins in
its `pyproject.toml`, sha256 hashes in its own `uv.lock` (installed with
`uv sync --frozen`, `--no-dev` on the PC), wheels only. It is never
imported by the backend and imports nothing from `backend/`.

| Package | Pin | Licence | Was | Used for |
|---|---|---|---|---|
| `cryptography` | `==50.0.1` | Apache-2.0 OR BSD-3-Clause | the backend's pin (`docs/mcp/dependencies/node-gateway.md`) | Ed25519 verification of `invoke` envelopes (vendored `nodes.protocol.signing`), certificate parsing for the SPKI pin (`friday_node_linux.tls`) |
| `jsonschema` | `==4.26.0` | MIT | the backend's pin | protocol v1 message and capability-contract validation (vendored `nodes.protocol.messages`, `nodes.capabilities`) |
| `wsproto` | `==1.3.2` | MIT | the backend's pin | the WebSocket client (vendored `nodes.reference.transport`) |

Dev only (never installed on the PC): `pytest==9.1.1`, `ruff==0.15.20`,
the backend's pins.

- **Same versions as the backend, on purpose.** The vendored reference node
  (`friday_node_linux/_vendor/`, `vendor.lock`) is tested in `backend/`
  with these exact versions; the node runs the same code with the same
  libraries. An upgrade changes both lock files in one dedicated branch.
- **No new network surface.** Outbound only: HTTPS to the home server's
  `/api/v1/nodes/pair` once, then WSS to `/ws/node/v1`, both TLS-pinned
  (private CA and/or SPKI). No listener of any kind except the 0600 Unix
  hook socket (peer uid checked). No MCP server, no model call, no
  provider key.
- **Not used at runtime:** `ccusage` is never executed; its presence on
  `PATH` only decides whether `usage.ccusage` is advertised (the amounts are
  computed by the vendored reference reader from the granted transcripts).
- **Residual risk:** a `cryptography` / `wsproto` CVE (upgrade both lock
  files); the local policy's stand-in for Touch ID is a TTY confirmation
  (brief Q4 / Risks, accepted by the owner in the plan review, owner step).
