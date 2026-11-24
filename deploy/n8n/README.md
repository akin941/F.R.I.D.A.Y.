# n8n — the "Friday tools" reference export (Phase 12 D9/D10)

Friday reaches the owner's n8n through **one** dedicated workflow, "Friday
tools", whose **MCP Server Trigger** exposes one tool per allowlisted
workflow. Friday never uses n8n's instance-level MCP (`/mcp-server/http`:
workflow search, create, update, publish, a `workflowId` argument) and n8n
never calls Friday. Governance: `docs/mcp/n8n.md`; manifest:
`backend/connectors/manifests/n8n.py`.

## Files

| File | What it is |
|---|---|
| `friday-tools.workflow.json` | The trigger workflow: an MCP Server Trigger (bearer auth, path `friday-tools`) and two "Call n8n Workflow Tool" nodes named `wf_<workflowId>` |
| `sub-workflows/server-status.workflow.json` | Sample read workflow `Ab12Cd34Ef56Gh78` (`scope`: `summary` / `full`) — the async read `n8n_server_status` |
| `sub-workflows/home-backup.workflow.json` | Sample write workflow `Hb34Kd56Mf78Np90` (`target`: `nas` / `cloud`) — the gated write `mcp_n8n_home_backup` |

## Setting it up (owner, on the real stack)

1. Import both sub-workflows, replace their Code nodes with your real steps,
   and note the ids n8n gives them (an import may keep or replace the ids in
   the files).
2. Import `friday-tools.workflow.json`. Point each tool node at the real
   sub-workflow id **and rename the node to `wf_<that id>`** — the node name
   is the MCP tool name Friday pins. Wire nothing else to the trigger.
3. Create an n8n "Bearer Auth" credential for the trigger. Put the same
   value **only** in `deploy/docker/.env` as `RELAY_BEARER_TOKEN` (the
   `n8n-relay` sidecar's env); never in Friday's web/worker env, the
   database or a commit. Set `RELAY_UPSTREAM_URL` to the trigger's
   production URL (`https://<n8n>/mcp/friday-tools`) and activate the
   workflow.
4. If the ids changed, update `WORKFLOWS` in
   `backend/connectors/manifests/n8n.py` and the table in `docs/mcp/n8n.md`
   in one reviewed commit, then print the relay's ceiling:

   ```bash
   cd backend && uv run python manage.py n8n_relay_env   # N8N_RELAY_ALLOWED_TOOLS=wf_...,wf_...
   ```

   and put that line in `deploy/docker/.env` (compose hands it to the sidecar
   as `BRIDGE_ALLOWED_TOOLS`, and to web/worker for `manage.py preflight`'s
   `n8n_allowlist` row).
5. Start the relay (`docker compose --profile connectors up -d n8n-relay`),
   set `N8N_MCP_URL=http://n8n-relay:8080/mcp` for web and worker, and
   re-pin each tool against the live trigger: the live `inputSchema` n8n
   generates may differ from the reference schema; a differing schema
   disables the tool (`connector.pin_mismatch`) until the pin in the
   manifest is updated in a reviewed commit (bump `version`).
6. Enable the `n8n` connector for your personal workspace on the web
   (Connectors page). Optionally add wake aliases ("ev yedeği", "sunucu
   durumu") on `/settings/wake-routes`.

Adding a workflow later is the same reviewed commit: a new
`N8nWorkflow` entry with its live pin, the twin's table row, a new tool node
`wf_<id>` in n8n, and a regenerated `N8N_RELAY_ALLOWED_TOOLS` line. Never a
database write.
