# Friday Node gateway — runbook (v0)

Phase 9 branch 4 (`feat/p9-node-gateway`,
`docs/prompts/phase-9-friday-node/brief.md` D3, D6). This v0 covers running
the gateway and exposing `/ws/node/v1`; branch 6 (`feat/p9-node-events`,
D10) adds node events and switching the Claude Code hooks. The closeout
(`chore/p9-closeout`) completes it: TLS pin, signing key rollover, pairing,
kill switch, revocation and the audit chain.

## What runs where

- **`node-gateway`** (compose service): `uvicorn config.asgi_nodes:application
  --ws wsproto` on port 8002, published on `127.0.0.1:8002` only. It serves:
  - `wss://…/ws/node/v1` — the node socket (subprotocols `friday-node.v2`,
    `friday-node.v1`; `Authorization: Bearer jnd_…`; no `Origin`);
  - `GET /healthz` — `{"status", "redis", "connected_nodes",
    "signing_key_id"}` (the key id, never key material);
  - everything else is 404.
- **web** and **worker** stay as they are. A read (`invoke_read`) runs in
  whichever process asks (a chat turn in web, a task in worker): it signs the
  envelope, pushes it to the node's Redis inbox and waits for the result the
  gateway pushes back. So **all three** need the same `NODE_SIGNING_KEY`.
- **Redis** is the bus: presence (`nodes:presence:<id>`, 45 s TTL), the
  per-node inbox, the ephemeral result keys (≤ 30 s), the shared in-flight
  counter and the `friday:device-revoked` pub/sub channel. Redis down means
  node reads fail closed (`bus_unavailable`) and new node handshakes are
  refused (1013); revocation still bites at the next heartbeat re-check.

Run **one** `node-gateway` replica: it holds every live node socket, and a
second connection of the same node replaces the first only within one
process.

## Start it

```bash
make secrets           # writes NODE_SIGNING_KEY into deploy/docker/.env once
make up                # dev stack: web, worker, node-gateway, postgres, redis
make up-daily          # daily stack: node-gateway runs config.settings.prod
curl -s http://127.0.0.1:8002/healthz
```

`/healthz` must show `"redis": true` and a 16-character `signing_key_id`.
`signing_key_id: null` means the key is missing or invalid: the gateway then
refuses every node handshake (1013) and pairing answers 503. Fix the key in
`deploy/docker/.env` and restart web, worker and node-gateway together.

## Expose `/ws/node/v1`

Never publish 8002 on `0.0.0.0`. Route the path through the overlay or the
TLS reverse proxy that already fronts Friday. Caddy example:

```caddyfile
friday.example.net {
    @node path /ws/node/v1
    handle @node {
        reverse_proxy 127.0.0.1:8002
    }
    # … the existing frontend routes …
}
```

- Only `/ws/node/v1` goes to the gateway; `/healthz` stays local.
- The proxy must pass the `Authorization` and `Sec-WebSocket-Protocol`
  headers through unchanged (Caddy does by default) and must not add an
  `Origin` header: the gateway closes any handshake that carries one (4403).
- The node pins the certificate (or its CA) it paired over (Mac session M1).
  Optionally set `NODE_TLS_PIN_SHA256` so the closeout's preflight can warn
  when the served certificate changes.

## Close codes a node sees

| Code | Meaning |
|---|---|
| 1000 (after `bye`) | `revoked`, `kill_switch`, `credential_expired`, `replaced`, `protocol_error`, `server_shutdown` |
| 1009 | a frame over 256 KiB |
| 1013 | Redis down, or no usable server signing key: try again later |
| 4400 | no common subprotocol, or the first frame was not `hello` / three bad frames in 60 s |
| 4401 | missing, unknown, revoked, expired or idle credential, kill switch engaged, not the owner's node |
| 4403 | an `Origin` header (browsers are refused) |
| 4408 | no `hello` within 10 s, or 45 s without any frame |
| 4429 | more than 20 frames a second |

## Settings (all optional)

| Name | Default | What |
|---|---|---|
| `NODE_INVOKE_TIMEOUT_SECONDS` | 10 | how long a read waits for its result (below `NODE_READ_INVOKE_TTL_SECONDS`) |
| `NODE_RESULT_TTL_SECONDS` | 30 | how long an unread result may stay in Redis |
| `NODE_MAX_INFLIGHT` | 4 | concurrent reads per node, across web, worker and gateway |
| `NODE_READ_INVOKE_TTL_SECONDS` | 30 | a read envelope's lifetime |
| `NODE_MAX_RESULT_BYTES` | 65536 | the largest read output accepted |

A value outside its bounds stops every process at boot (`nodes.E005`–`E007`).

## Owner APIs (web session, owner only)

- `GET /api/v1/nodes` — status (`connected`, `disconnected`, `kill_switch`,
  `revoked`, `credential_expiring`), last heartbeat, spool depth, credential
  age, catalogue counts.
- `GET /api/v1/nodes/<id>` — plus each advertised capability with
  `invocable` and its `not_invocable` reason (read-only: node policy is set on
  the Mac only).
- `POST /api/v1/nodes/<id>/revoke` — permanent; the gateway closes the
  node's socket within ~2 s (pub/sub), at the latest at its next heartbeat.

The `/devices` UI for these is branch 7.

## Node events (branch 6)

A node sends `event` frames (D10); the gateway answers each with an
`event.ack` that carries **counts only** (`stored`, `duplicate`, `dropped`)
and never anything else — no event ever leads to an `invoke`.

- **`claude_code.hook`**: up to 50 Claude Code hook events per batch, in
  `friday-hook`'s per-event shape. `config/asgi_nodes.py` registers
  `memory.ingestion.hooks.ingest_hook_events` as the sink at start-up, so
  these events go through exactly the 8A path: the owner's
  `claude_code_hooks` source must be enabled, its path-prefix rules alone
  decide each event's workspace (an unmapped `cwd` is `dropped`), and an
  event id already stored (from `friday-hook` or an earlier upload) is a
  `duplicate`. Events older than 24 h are dropped, like on the HTTP path.
- **Spool upload**: after a reconnect the node uploads its spool
  (`spooled: true`) one batch at a time, in order, and deletes a batch only
  after its ack. The gateway paces spooled batches to 10 a second per node
  (it delays the ack; nothing is refused).
- **`grant.availability_changed`**: the node reports that the owner
  granted or withdrew a capability on the Mac (`{name, version,
  availability}` items). The gateway writes a successor capability snapshot
  with those flags; `GET /api/v1/nodes/<id>` and the HUD node block follow,
  and a withdrawn read is no longer offered (`server_refused:not_invocable`).
  It never changes anything on the Mac.
- **No sink registered** (a process not started through
  `config.asgi_nodes`): every hook batch is acked `dropped` and stored
  nowhere. If the sink raises (a database error), no ack is sent: the node
  keeps the batch and sends it again after its next reconnect (deduped).
- **Logs**: one `node event node=… kind=… spooled=… stored=… duplicate=…
  dropped=…` line per batch; never an event's content.

## Switching Claude Code hooks to the node

`friday-hook` (`docs/runbooks/claude-code-hooks.md`) keeps working and stays
the fallback; `integrations/cli/friday_hook.py` is unchanged. Both paths
dedupe on the same event id, so running both during the switch-over stores
each event once.

1. Pair the Mac node and confirm it is `connected` (`GET /api/v1/nodes`).
2. On the Mac (Mac session M3), add `friday-node hook` entries next to the
   existing `friday_hook.py` entries in `~/.claude/settings.json` for
   `SessionStart`, `Notification`, `Stop` and `SessionEnd`. Keep the old
   entries for a few days: every event arrives twice and is stored once
   (the node's acks show `duplicate`).
3. Check `/sessions` (or `GET /api/v1/memory/sessions`) still updates; stop
   the node app for a minute, start a Claude Code session, start the node
   again: the spooled events arrive after reconnect.
4. Remove the `friday_hook.py` entries. To fall back at any time, put them
   back: nothing on the server changes, and the `hook` PAT stays valid
   until revoked.

## Troubleshooting

- **Node connects and is closed 4401 at once:** the secret is revoked,
  older than `NODE_CREDENTIAL_MAX_AGE_DAYS`, unused for
  `NODE_IDLE_EXPIRY_DAYS`, or the kill switch is engaged
  (`manage.py node_kill_switch status`). `accounts.node.auth_failed` audit
  rows carry the reason.
- **Reads answer `node_offline` while the Mac says connected:** check
  `redis-cli EXISTS nodes:presence:<node_id>` and the gateway log line
  `node connected node=…`. A presence key without a gateway socket expires
  within 45 s.
- **Logs:** the gateway logs ids, types, sizes and codes only — never a
  frame body, an argument or an output.
