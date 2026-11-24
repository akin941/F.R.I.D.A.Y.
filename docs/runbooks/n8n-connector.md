# Runbook — the n8n connector (Phase 12)

Friday uses the owner's own n8n automations, and only the workflows the owner
allowlisted in a reviewed commit: reads run once per turn (slow ones
asynchronously, with a follow-up in the same conversation), writes are
gate-class drafts the owner approves on screen and that are sent exactly
once. n8n never calls Friday, and no unattended run can reach n8n at all.

Sources: `docs/prompts/phase-12-reach/brief.md` (D9–D12, R3, R6, R7),
ADR-010 and its "Amendment 2026-11 — HTTP relay and async reads",
`docs/mcp/n8n.md` (the governance twin: tools, pins, ceilings,
credentials), `deploy/n8n/README.md` (the reference export and the owner's
setup), `docs/runbooks/proactive.md` (the notices).

## What runs where

| Piece | Where | Holds |
|---|---|---|
| n8n + the "Friday tools" workflow (MCP Server Trigger, one `wf_<id>` tool node per allowlisted workflow) | the owner's n8n | the workflows; the trigger's bearer credential |
| `n8n-relay` sidecar (`connectors/docker/http_relay.py`, compose profile `connectors`) | internal `connectors` network, no published port | `RELAY_UPSTREAM_URL`, `RELAY_BEARER_TOKEN` (its env only), `BRIDGE_ALLOWED_TOOLS` |
| Friday web / worker | the server | `N8N_MCP_URL` (the relay's URL), `N8N_RELAY_ALLOWED_TOOLS` (names only, for preflight); never the bearer |
| The allowlist | `backend/connectors/manifests/n8n.py` `WORKFLOWS` + `docs/mcp/n8n.md` | Friday names, workflow ids, labels, pinned input schemas |

Three ceilings stack: the tool nodes the owner wired in n8n, the relay's
`BRIDGE_ALLOWED_TOOLS`, and the manifest. An n8n tool outside the allowlist
is neither listed nor called. n8n's instance-level MCP (search, create,
execute any workflow) is never used.

## 1. Set it up (owner steps 3–5)

Follow `deploy/n8n/README.md` "Setting it up". In short:

1. Import the reference export, rename each tool node to `wf_<workflow id>`,
   create the trigger's Bearer credential and put it **only** in
   `deploy/docker/.env` as `RELAY_BEARER_TOKEN`; set `RELAY_UPSTREAM_URL`.
2. `cd backend && uv run python manage.py n8n_relay_env` prints
   `N8N_RELAY_ALLOWED_TOOLS=wf_…,wf_…`; put that line in `deploy/docker/.env`
   (compose hands it to the relay as `BRIDGE_ALLOWED_TOOLS`).
3. `docker compose --profile connectors up -d n8n-relay`; set
   `N8N_MCP_URL=http://n8n-relay:8080/mcp` for web and worker.
4. Re-pin each tool against the live trigger in a reviewed commit (bump the
   manifest `version`) — a differing live schema disables that tool
   (`pin_mismatch`) until then.
5. Enable the `n8n` connector for the **personal** workspace on the
   Connectors page (web, owner). Optional: aliases on `/settings/wake-routes`
   ("ev yedeği", "sunucu durumu").
6. Confirm n8n's licence terms for personal use and record them in
   `docs/mcp/n8n.md` (R7).

## 2. How a read behaves

- Personal data class: web and `node_voice` turns only; the conversation is
  marked `personal_data` before any completion; only a
  `personal_data_allowed` provider composes the answer.
- **Sync read** (fast workflows): one call inside the turn, the result fenced
  as `<connector_result source="n8n" …>` data.
- **Async read** (`mode="async"`, e.g. "sunucu durumu"): the turn answers at
  once with Friday's template ("Başlattım: Sunucu durumu. Sonucu bu
  konuşmaya yazacağım.", no completion); the read runs once after commit
  (`N8N_ASYNC_READ_TIMEOUT_SECONDS`, never retried); exactly one
  `chat.followup` completion (result fenced, no tools) appends the follow-up
  to the same conversation; the next heartbeat adds a "Sonuç hazır: <label>"
  notice. A failed/timed-out read appends a template with no model call; a
  deleted conversation drops the follow-up (never replayed).

## 3. How a write behaves

A write is gate-class (`mcp_n8n_<tool>`), never confirm-class:

1. The turn — typed or spoken — yields a **draft card only**, with the
   router-extracted arguments validated by the tool's serializer.
2. The owner opens the card on screen, checks the exact arguments and
   approves on `/approvals` (web only; a PAT, node or voice token cannot
   decide).
3. The write is sent once through the relay. A lost worker ends in
   `unknown_outcome` — shown, never re-sent; a new attempt is a new request
   and a new decision.
4. The next heartbeat announces it ("n8n: Ev yedeği bitti | başarısız |
   sonucu belirsiz"): the Friday label and a status word, never result text.

Inside an unattended run, every n8n read, async start and write is denied in
its owning service (ADR-016 decision 1).

## 4. Preflight (`manage.py preflight`, `check_n8n`)

| Row | Passes when | Warn / fail |
|---|---|---|
| `n8n_allowlist` | `N8N_RELAY_ALLOWED_TOOLS` (the relay ceiling) equals the manifest's `wf_<id>` tools | fail: empty, an extra or a missing name (re-run `n8n_relay_env`) |
| `n8n_binding` | an enabled binding, in a personal workspace | warn: no enabled binding (not configured, owner step 3); fail: a binding outside a personal workspace |
| `n8n_relay` | a read-only `tools/list` through the relay answers | warn: not checked (no binding); fail: `N8N_MCP_URL` unset while enabled, or the relay/n8n unreachable (closed code only) |
| `n8n_pins` | every allowlisted tool's live schema matches its pin | warn: not checked; fail: `tool=mismatch|missing` |

Preflight stores nothing on the binding (the 15-minute pin beat stays the
one writer of binding health) and never prints a URL or the bearer. On the
fake stack the three binding rows warn — that is the documented
"not configured" state.

## 5. Adding or changing a workflow

One reviewed commit: an `N8nWorkflow` entry (Friday name `n8n_<snake_case>`,
workflow id, kind, mode, Friday-authored label and description, argument
serializer, live schema pin; a write also a renderer for its decision rows),
the twin's table row in `docs/mcp/n8n.md`, the tool node `wf_<id>` in n8n,
and the regenerated `N8N_RELAY_ALLOWED_TOOLS` line. Never a database write.
Then `manage.py preflight` → `n8n_allowlist` and `n8n_pins` pass.

## 6. The e2e (CI)

`backend/ops/tests/test_reach_e2e.py` drives both paths through the real
chat view and MCP client, the in-process relay and the fake n8n trigger (the
SDK's in-memory server): a `node_voice` async read → one follow-up → one
notice; a voice write → draft only → web approval → exactly one send → one
"bitti" notice. Branch tests: `backend/connectors/tests/test_n8n_*.py`,
`test_http_relay.py`, `test_async_reads.py`.

## Troubleshooting

- **A tool is refused with `pin_mismatch`.** The live schema
  changed in n8n: re-pin in a reviewed commit.
- **`not_configured`.** `N8N_MCP_URL` unset in web/worker, or the binding is
  off.
- **The relay answers "This tool is not available."** The name is not in
  `BRIDGE_ALLOWED_TOOLS` (check `n8n_allowlist`) or the n8n node is not named
  `wf_<id>`.
- **A follow-up never arrived.** `ConnectorAsyncRead` / `PendingFollowUp`
  status: `dispatch_lost`, `worker_lost` and `timed_out` end with a template
  message; `dropped` means the conversation was gone.
