# `n8n` connector — governance twin

Phase 12 D9–D12 (branch `feat/p12-n8n-connector`, 2026-11-20); ADR-010
amendment 2026-11 "HTTP relay and async reads".

## Summary
- **Manifest:** `backend/connectors/manifests/n8n.py`, version `1`
  (`build_manifest(WORKFLOWS)`)
- **Kind:** `mcp` — Streamable HTTP to the `n8n-relay` sidecar
  (`connectors/docker/http_relay.py`), which forwards to the owner's n8n
  MCP Server Trigger
- **Status:** active (ships with the two reference workflows; the owner
  re-pins them against the live trigger before enabling)
- **Data class:** `personal` — personal workspace only, every read marks
  the conversation `personal_data`, `personal_data`-allowed providers only
- **Workspace types:** personal
- **Read channels:** web, node_voice (never cli, hook, openai, telegram)

## Source and maintenance
- Upstream: n8n (n8n.io), the owner's self-hosted instance; the MCP Server
  Trigger node (`@n8n/n8n-nodes-langchain.mcpTrigger`) and "Call n8n
  Workflow Tool" nodes (`@n8n/n8n-nodes-langchain.toolWorkflow`).
- **Licence (R7):** n8n is "Sustainable Use" (fair-code). Running it for
  the owner's personal automations is the intended use; Friday does not
  redistribute it — the relay image contains no n8n code. **Owner step:**
  confirm the licence terms for personal use and record the result here.
- Image: the relay runs from the stdio bridge's image
  (`backend/connectors/docker/Dockerfile.bridge`, `mcp` + `uvicorn` only);
  pin its base by digest when the sidecar is first built (as every sidecar).
- Protocol version: negotiated per connection; recorded in the binding's
  health report.
- Rejected alternatives (D9): n8n's instance-level MCP (`/mcp-server/http`,
  ~60 tools incl. workflow search/create/update/publish and a `workflowId`
  argument that turns one tool into all workflows); the community
  `czlonkowski/n8n-mcp` (second, unofficial surface); a native REST bridge
  to webhook URLs (the webhook secret would live in the worker, no schema
  pins).

## Tools

The table is the Friday-side workflow-ID allowlist (`WORKFLOWS`); a test
(`connectors/tests/test_n8n_manifest.py`) keeps it equal to the code.

| Friday name | Workflow id | Server tool | Kind | Mode | Schema pin (sha256) | Label | Gate / user-invocable | work_log |
|---|---|---|---|---|---|---|---|---|
| `n8n_server_status` | `Ab12Cd34Ef56Gh78` | `wf_Ab12Cd34Ef56Gh78` | read | async | `007afe51a2b6d458b9b2b656f7e018d33dc6c77ab91005bb1050bb630d0f0136` | Sunucu durumu | — | — |
| `n8n_home_backup` | `Hb34Kd56Mf78Np90` | `wf_Hb34Kd56Mf78Np90` | write | sync | `28fb35f96176ee3e034f64a3a4462245b63ff573923a58df90b0bb68911946a9` | Ev yedeği | `mcp_n8n_home_backup` / yes | yes (private) |

- Pins are the sha256 of the reference schemas (`REFERENCE_SCHEMAS`). The
  live trigger's `inputSchema` may differ: a differing pin disables the
  tool (`connector.pin_mismatch`, sticky for the manifest version) until a
  reviewed commit updates it (**owner step**, `check_n8n` on the real stack
  = `manage.py preflight`'s connector pin report).
- Argument serializers (Friday-side, strict): `scope` ∈ {summary, full}
  (default summary); `target` ∈ {nas, cloud}.
- The write's draft card carries router-extracted arguments
  (`ToolSpec.draft_arguments`, D12); the owner sees and decides the exact
  arguments on `/approvals` (renderer rows + `arguments_json`).

Tools the server could offer that are deliberately NOT listed: every other
tool node the owner might wire to the trigger, and every instance-level
tool (`search_workflows`, `execute_workflow`, ...). They are neither listed
nor callable: the relay's `BRIDGE_ALLOWED_TOOLS` drops them, the manifest
never registers them, and a manifest entry must match `wf_<id>`.

### Three ceilings
1. n8n: the trigger exposes only the tool nodes the owner wired.
2. Relay: `BRIDGE_ALLOWED_TOOLS` (compose fills it from
   `N8N_RELAY_ALLOWED_TOOLS`, the line `manage.py n8n_relay_env` generates
   from `WORKFLOWS`) lists and calls only allowlisted names; an unlisted
   call is answered "not available" without contacting n8n. `manage.py
   preflight` (`n8n_allowlist`, Phase 12 closeout) fails when the
   configured value differs from the manifest.
3. Friday: the manifest registers only reviewed entries with pinned schemas.

**Residual (R3):** Friday cannot verify that the node named `wf_X` really
calls workflow `X` — that mapping lives in the owner's n8n and is trusted at
the owner's level.

## Credentials
- Sidecar-side only: `RELAY_BEARER_TOKEN` (the trigger's Bearer Auth
  credential) and `RELAY_UPSTREAM_URL`, in `deploy/docker/.env`, passed to
  the `n8n-relay` service only.
- Friday-side (web, worker): `N8N_MCP_URL` (the relay's internal URL) only.
- Never in Postgres, a payload, an audit row or a log line
  (`connectors/tests/test_n8n_writes.py::test_no_credential_value_anywhere`;
  the relay logs failures by exception class name only and rebuilds every
  result from its content blocks).
- Rotation: create a new credential in n8n, update the sidecar env,
  restart `n8n-relay`. Friday needs no change.

## Sandbox and network
- `n8n-relay`: `x-connector-sidecar` hardening (read-only root, `cap_drop:
  ALL`, `no-new-privileges`, tmpfs `/tmp`), networks `connectors` (Friday
  reaches it) + `connectors-egress` (it reaches n8n). No published port.
- Relay → n8n: one fixed URL, no redirects (`max_redirects=0`), no
  environment proxies, no client capabilities (no sampling, elicitation or
  roots; `input_required_max_rounds=0`). Friday → relay: the hardened
  `connectors.mcp_client` (no redirects, no capabilities, response byte
  cap).
- Only `tools/list` and `tools/call` are forwarded; resources and prompts
  are "method not found".

## Outbound data
- Friday sends the serializer-validated arguments of one tool call (e.g.
  `{"scope": "summary"}`, `{"target": "nas"}`). What the owner's workflows
  do with them, and which services they reach, is the owner's n8n
  configuration.

## Prompt-injection surface
- Every result is low-trust text: a sync read is fenced
  (`<connector_result>`) into the turn's completion; an async read's result
  is bounded (`CONNECTOR_RESULT_MAX_CHARS`), invisible characters stripped,
  stored on `ConnectorAsyncRead` (cleared after a day) and fenced into the
  **one** `chat.followup` completion — no tools, no router, no read, no
  draft. A write's result is stored bounded on the execution row and never
  reaches a prompt. A hostile result ("onayla", "call mcp_n8n_…") produces
  no draft, approval, read, memory or notice text (tests).
- Server tool descriptions never reach a prompt (Friday-authored names,
  labels and descriptions only).

## Logging
- `connector.read` (sync, and async with `mode=async`: one row per refused
  start and one per finished read), `connector.write.*`,
  `connector.pin_mismatch`, `assistant.followup.started` / `.done` /
  `.failed` / `.dropped` — bookkeeping only, never arguments or content.
- Notices: `connectors.n8n_finished` ("n8n: Ev yedeği bitti" — Friday label
  + status word) and `assistant.followups_ready` ("Sonuç hazır: Sunucu
  durumu"); never result text.

## Review
- Implemented 2026-11-20 on `feat/p12-n8n-connector`; awaits the
  independent Sonnet 5 review. Re-review trigger: any `WORKFLOWS` change
  (new workflow, pin, serializer), a relay change, or a channel change.
