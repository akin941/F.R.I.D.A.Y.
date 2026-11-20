# `<name>` connector — governance twin

Copy to `docs/mcp/<name>.md` in the same commit that adds
`backend/connectors/manifests/<name>.py` and the `` `<name>` connector `` row
in `docs/mcp/allowlist.md` (`docs/security/mcp-governance.md`, "Runtime
connectors"). Keep it current: a manifest change without a twin update does
not pass review.

## Summary
- **Manifest:** `backend/connectors/manifests/<name>.py`, version `N`
- **Kind:** `mcp` (Streamable HTTP sidecar) | `native` (bridge in `connectors/bridges/`)
- **Status:** active | blocked
- **Data class:** `firm` | `personal` (personal: personal workspaces only,
  web-only reads, `personal_data`-allowed providers only)
- **Workspace types:** personal / company
- **Read channels:** web / cli (never telegram or openai)

## Source and maintenance
- Upstream project, maintainer (official/community), activity, license
- Image and digest (`sidecar_image`), how and when the digest is bumped
- MCP protocol version negotiated (from the binding's health report)

## Tools
| Friday name | Server tool | Kind | Schema pin (sha256) | Scope check | Gate / user-invocable | work_log |
|---|---|---|---|---|---|---|
| `<name>_...` | `...` | read | `...` | ... | — | — |
| `<name>_...` | `...` | write | `...` | ... | `mcp_<name>_...` / yes | yes |

Tools the server offers that are deliberately NOT listed (and why):

## Credentials
- Env var names only (never values): sidecar-side `...`; Friday-side URL
  `..._MCP_URL`
- Minimal scopes / permissions, expiry, rotation, where it is created
- Never in Postgres, a payload, an audit row or a log line

## Sandbox and network
- Sidecar hardening (inherits `x-connector-sidecar`); egress needed? to where?
- stdio bridge? `BRIDGE_ALLOWED_TOOLS`, `BRIDGE_ENV_PASSTHROUGH`

## Outbound data
- What Friday sends (arguments), what the server may send elsewhere
  (telemetry?), personal data exposure

## Prompt-injection surface
- Which results are attacker-reachable text; fencing/bounding notes

## Logging
- `connector.read` / `connector.write.*` / `connector.pin_mismatch` audit rows
  (see the governance doc's logging table); anything connector-specific

## Review
- Reviewer, date, verdict; re-review trigger (pin change, scope change)
