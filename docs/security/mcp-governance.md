# MCP / External Tool Governance

Default answer is **no**: an MCP or external tool that is not on the allowlist
(`docs/mcp/allowlist.md`) is not installed. MCPs can hold file/mail/browser/shell/
GitHub powers; treat every installation as a security decision.

## Permission manifest (required before install, one file per tool in docs/mcp/)

- Source & maintenance: official/community, activity, version pinned?
- Capability class: read-only / write-capable / execute-capable
- Access surface: which paths/services; can it be path-jailed?
- Outbound data: does it send data anywhere? telemetry?
- Credentials: token/cookie/OAuth? minimal scopes? storage?
- Sandbox: can it run isolated?
- Logging: can calls be tied into Friday audit?
- Scope: which workspaces, which agents, sensitive data allowed? (default: no)
- Review: who reviewed, when.

## Binding rules

1. Write/execute-capable or credential-holding tools require a security review
   before install; read-only local tools get a light review.
2. Unofficial browser-automation / cookie-based MCPs (e.g. NotebookLM MCPs) are
   **never** enabled for sensitive or company workspaces; public/educational
   documents only, on a separate Google account.
   Phase 5 closeout (2026-11-20): NotebookLM MCPs and browser/Playwright MCP
   stay rejected as Friday *runtime* connectors (reasons in
   `docs/mcp/allowlist.md`); no manifest exists for either.
3. MCP write actions cannot bypass the Friday approval gate — MCPs are wrapped by
   the tool registry, never wired raw into production agents.
4. Every MCP call is logged; unmaintained tools get removed at the periodic
   re-review (first pass scheduled with the Phase 4 security baseline).

## Runtime connectors: code manifests (Phase 5, ADR-010)

A connector Friday *calls at runtime* (an MCP server or a native bridge) is
governed by code, not by configuration:

1. **Three artifacts, one reviewed commit.** A connector exists only as
   `backend/connectors/manifests/<name>.py` (a `ConnectorManifest`, listed in
   `BUILTIN`), its governance twin `docs/mcp/<name>.md` (underscores may be
   spelled as hyphens, e.g. `home-assistant.md`; from
   `docs/mcp/_template.md`) and a `` `<name>` connector `` row in
   `docs/mcp/allowlist.md`. `connectors/tests/test_manifests.py` fails if any
   of them is missing. Widening a connector -- a new tool, channel, scope or
   pin -- is a new reviewed commit, never a database write (ADR-004).
2. **The manifest is the tool ceiling.** Only tools the manifest lists are
   ever registered, offered to the chat router or callable; anything else a
   server offers is unreachable. Each listed tool carries a Friday-authored
   name and description (server descriptions and instructions never reach an
   LLM prompt), a strict DRF argument serializer, an optional scope check,
   and -- for writes -- a gate name, renderer and `records_work_log`.
3. **Schema pins.** Each tool pins the sha256 of the server's `inputSchema`
   (canonical JSON). The pin is compared on enable, on the 15-minute pin beat
   (`connectors.tasks.check_connector_pins`) and in the same MCP session
   before every read and write. A changed or missing schema disables that tool
   (`connector.pin_mismatch` audit) and the disable is sticky for that
   manifest version: a server flipping back, or the owner toggling the
   binding, does not re-enable it -- only a commit that bumps the manifest
   version (with the new pin, after review) does. To update a pin: review the
   new schema, update `schema_sha256` and bump `version` in the same commit.
4. **Bindings are per workspace and off by default.** `ConnectorBinding`
   (`enabled=False`), switched on only by the owner over the web
   (`/api/v1/connectors/bindings`, audited). `personal`-class connectors bind
   only to personal workspaces; a `blocked` manifest (external installation, D14) can never
   bind.
5. **Transport and isolation (D1).** Streamable HTTP only, to the URL in the
   env var the manifest names, no redirects, no proxies, no client
   capabilities (no sampling, elicitation or roots). Each server runs as its
   own hardened compose sidecar (`connectors` profile: digest-pinned image,
   read-only, `cap_drop: ALL`, no-new-privileges, no published ports,
   internal `connectors` network, egress only where needed) holding only its
   own credential. A stdio-only server runs behind
   `backend/connectors/docker/stdio_bridge.py` inside its sidecar, with a
   second, sidecar-side tool allowlist.
6. **Reads are ungated, writes are gated (ADR-010 decisions 4 and 6).**
   Reads: at most one per chat turn, `firm` over web + CLI, `personal` over
   web only, never Telegram or the facade; results are fenced as
   `<connector_result>` data and never become memories. Writes: one gated
   `ToolSpec` per tool, decided over the web only, snapshot-backed, sent at
   most once, never retried (`unknown_outcome` instead).
7. **Native bridges (D8, D9).** A `native` manifest has no MCP server: its
   module exports a `BRIDGE` (`backend/connectors/bridges/`) that calls the
   vendor's REST API itself; the read/write paths, snapshot, gate and audit
   are the same (`connectors.bridges.call_tool` dispatches by kind). The
   bridge's pins hash its own request shapes (a bridge change that alters
   one ships with a pin update). Availability comes from the bridge's health
   check on enable and on the pin beat -- e.g. Google's granted OAuth scopes:
   a tool whose scope is not granted is `scope_missing`, a grant wider than
   the manifest maximum makes every tool `scope_extra`, a rejected refresh
   token makes every tool `reconnect_required`. These statuses follow the
   credential and are not sticky (a re-consent re-enables at the next
   check); scopes are re-checked inside every call before any API request.
   Credentials are env-only, read at call time; access tokens live in
   process memory only.

### Logging and audit design (replaces the 2026-09-27 note)

There is no `ToolCall` model and none is needed: every connector event is an
`audit` row written by the `connectors` app (plus `ModelUsage` for the LLM
calls around a read), and none of them carries content or credentials.

| Event | Audit action | Metadata (never content) |
|---|---|---|
| Binding switched on/off | `connector.binding.enabled` / `.disabled` | connector, manifest version, channel |
| Pin drift detected | `connector.pin_mismatch` (system) | connector, tool, status, expected/actual sha256, manifest version |
| Every read attempt (ok, refused, failed) | `connector.read` | binding, connector, tool, sha256 of canonical arguments, size, truncated, duration_ms, outcome, channel |
| Write requested / decided | `approval.requested` / `.approved` / `.rejected` | the approvals app's usual rows |
| Write terminal state | `connector.write.succeeded` / `.failed` / `.unknown_outcome` (system) | connector, tool, binding, approval request, arguments sha256, closed-vocabulary error type |
| Native grant wider than the manifest | `connector.scope_mismatch` (system) | connector, tools disabled, extra scope names, manifest version |
| Native credential rejected / missing | `connector.reconnect_required` (system) | connector, closed error code (`reconnect_required` / `not_configured`) |
| Personal data entered a conversation | `assistant.conversation.marked_personal_data` | -- |

The chat completion that consumed a read records the read's bookkeeping in
`ModelUsage.context_report["connector_reads"]` and on
`Message.connector_reads` (tool, source, data class, size, truncation).
Every terminal write also leaves a Friday-authored `work_log` memory
(`tools.work_log`; private for personal-class connectors). Log lines name the
connector, operation, closed error code and exception class -- never
exception text, arguments, results, URLs with credentials or tokens.
Credentials are env-only: MCP credentials live only in the sidecar; a native
bridge reads its own from the environment at call time (Home Assistant:
`HA_BASE_URL`/`HA_TOKEN`, `connectors/tests/test_home_assistant.py::
test_the_token_is_nowhere_but_the_authorization_header`).

Native-bridge pins (rule 7): with no remote `inputSchema`, a native tool's
pin is the sha256 of the bridge's own code-defined request contract (for
Home Assistant the whole service/data allowlist, `tool_schemas()`): widening
it without updating the manifest's pin and version reports the tool
`mismatch` at the next health check (audited `connector.pin_mismatch`, like
MCP drift) and refuses every call. Execution-time checks that must hold
before the one send (Home Assistant: the entity still exists, the call is
still inside the narrowed allowlist) are the tool's `ToolPolicy.precondition`
-- a refusal ends the execution `failed` (`target_missing` /
`connector_changed`) with nothing sent. A test
(`connectors/tests/test_chat_reads.py::test_no_credential_value_anywhere`)
asserts a credential value appears in no DB row, audit row or log line.

> Note (2026-09-27): the earlier MVP numbering in this document was replaced
> by Friday 2.0 phase references (ADR-008).
