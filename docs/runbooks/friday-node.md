# Friday Node — runbook

Phase 9 (`docs/prompts/phase-9-friday-node/brief.md`, ADR-013). Branch 4
(`feat/p9-node-gateway`) wrote the gateway part, branch 6
(`feat/p9-node-events`) node events and the hook switch-over, and the
closeout (`chore/p9-closeout`) completed it: TLS pin, signing key generation
and rollover, pairing, kill switch, revocation, the audit chain and the
preflight node checks.

The Mac side (the Swift `friday-node` app) is **deferred to a Mac session**
(brief "Mac session (deferred)", M1–M4). Until it exists, the only node is
the Python reference node the tests run; every step below that happens "on
the Mac" is pending owner evaluation.

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
- The node pins the certificate key it paired over (Mac session M1).
  Optionally set `NODE_TLS_PIN_SHA256` (next section) so preflight fails
  when the served certificate key changes.

### Pinning the served certificate (`NODE_TLS_PIN_SHA256`)

The node pins the TLS key it paired over. If the proxy's certificate key
changes (a new key, not just a renewal that keeps it), every paired node
refuses the server. Let preflight catch that first:

```bash
# On any host that reaches the proxy: base64 sha256 of the served SPKI.
openssl s_client -connect friday.example.net:443 -servername friday.example.net </dev/null 2>/dev/null \
  | openssl x509 -pubkey -noout \
  | openssl pkey -pubin -outform der \
  | openssl dgst -sha256 -binary | base64
```

Put the value in `deploy/docker/.env` as `NODE_TLS_PIN_SHA256` (base64 or
hex; a `sha256/` prefix is accepted) with
`PREFLIGHT_NODE_TLS_URL=https://friday.example.net/ws/node/v1`, and restart
web. `node_tls_pin` then compares the served SPKI with the pin on every
preflight. Caddy keeps the key across automatic renewals only with
`key_type` fixed and the key reused; otherwise plan a re-pin (and a re-pair
on the Mac) with every renewal.

## Signing key: generation and rollover

- **Generate.** `make secrets` (`deploy/docker/gen-secrets.sh`) appends a
  random `NODE_SIGNING_KEY` (base64 of 32 raw Ed25519 bytes) to
  `deploy/docker/.env` once and never overwrites it. The key lives only in
  that file and the containers' environment: never commit it, never copy it
  to the Mac (the Mac pins the **public** key from the pairing response).
- **Same key everywhere.** web, worker and node-gateway must have the same
  key: preflight's `node_signing_key` compares web's key id with the
  gateway's `/healthz` and with the id a worker returns from the
  `nodes.signing_key_id` task. A published test key (the protocol vectors,
  the conformance key pair) is refused everywhere.
- **Rollover = re-pair (Phase 9, brief Q3).** There is no signed rollover
  message yet. To replace the key:
  1. put the old **public** key (base64) into `NODE_SIGNING_KEYS_RETIRED`
     if anything must still verify against it, then set the new
     `NODE_SIGNING_KEY`;
  2. restart web, worker and node-gateway together;
  3. revoke each paired node and pair it again (below); the new pairing
     response hands out the new key. Until then the node refuses every
     invoke (unknown key id), and `node_signing_key` fails with "N active
     node(s) pinned another key id".

## Pairing a node

1. On `/devices` → "Mac düğümleri": "Mac eşleştir" issues a LAN-only PIN
   (8 digits, 5 minutes); from another network use the strong code. From
   the host shell: `manage.py issue_pairing_code --node --strong`. The code
   is shown once and is refused while the kill switch is engaged.
2. In the Mac app's pairing screen (Mac session M1) enter the server URL and
   the code. The app calls `POST /api/v1/nodes/pair` and receives, once,
   `node_id`, the `jnd_…` secret (kept in the Keychain), the server signing
   key to pin, the protocol version and `gateway_path`.
3. The node connects to `wss://…/ws/node/v1`; `/devices` shows it
   `Bağlı`, with its capability list as text (grants are set on the Mac
   only).

The secret rotates every `NODE_CREDENTIAL_ROTATE_AFTER_DAYS` (30) through
`POST /api/v1/auth/nodes/credential/rotate`; a secret older than
`NODE_CREDENTIAL_MAX_AGE_DAYS` (90) or unused for `NODE_IDLE_EXPIRY_DAYS`
(30) stops working and the node must re-pair.

## Kill switch

Cuts off **every** node of the owner at once, without forgetting them:

- `/devices` → "Acil durdurmayı aç" / "Acil durdurmayı kapat" (behind a
  confirmation), or
  `manage.py node_kill_switch on` / `off` / `status`.
- Engaged: every node handshake and pairing is refused, the gateway sends
  `bye{kill_switch}` and closes the owner's live sockets within ~2 s (pub/sub;
  the heartbeat re-check is the backstop), the HUD node line says "acil
  durdurma açık". Another owner's nodes are untouched.
- Released: the (non-revoked, unexpired) nodes reconnect without re-pairing.
  Both are audited (`accounts.node.kill_switch.engaged` / `.released`).

## Revocation

- One node: `/devices` → the node → "İptal et", or
  `POST /api/v1/nodes/<id>/revoke`. Permanent: the gateway sends
  `bye{revoked}` and closes the socket within ~2 s, the node tools are no
  longer offered to the chat, and the node must pair again to come back.
- Everything: `manage.py revoke_all_devices` revokes every browser device
  **and** every node of the owner.
- The `Node` row stays (the audit chain's subject); its status reads
  `revoked`.

## Reading the audit chain

Every node call writes exactly one hash-chained `NodeInvocation` row (codes,
hashes, sizes, the target workspace; never args or output content) and one
`nodes.invoke` `AuditLog` row.

```bash
docker compose -f deploy/docker/compose.yml exec web uv run python manage.py verify_node_audit_chain
# node audit chain OK: 42 row(s)
```

A non-zero exit names the first broken `seq` and the reason (`seq_gap`,
`prev_hash_mismatch`, `row_hash_mismatch`, `head_mismatch`, `head_missing`).
The table is append-only by trigger: a break means someone bypassed it at
the database level -- treat it as an incident, keep the database as it is,
and engage the kill switch until it is understood. The owner reads the rows
on `/devices/nodes/<id>` (last 50, newest first) or
`GET /api/v1/nodes/<id>/invocations`.

## Preflight node checks

`make preflight` (`manage.py preflight`) runs, read-only and without any
model call:

| Check | Pass | Warn | Fail |
|---|---|---|---|
| `node_gateway` | `/healthz` answers, Redis OK | `PREFLIGHT_NODE_GATEWAY_URL` empty | unreachable, non-200, Redis down |
| `node_signing_key` | one key id in web, worker and node-gateway; every active node pinned it | a comparison skipped (no gateway URL, eager Celery) | key missing/invalid/test key anywhere, ids differ, no worker answers, a node pinned another key |
| `node_link:<name>` | connected, fresh heartbeat row, catalogue present | disconnected, kill switch, credential expiring, stale row; **no node paired** (Mac session pending) | — |
| `node_tls_pin` | served SPKI = pin | pin or URL not set | mismatch, handshake error, malformed pin/URL |
| `node_audit_chain` | chain intact | — | first broken `seq` |
| `node_conformance` | case/vector files current | — | stale files (`build_node_conformance`) |

The compose stack sets `PREFLIGHT_NODE_GATEWAY_URL=http://node-gateway:8002/healthz`
for web. The HUD health panel shows only the names of failing checks.

**Until the Mac app exists** (dev stack only, `DEBUG` on), pair the Python
reference node so `node_link` has a node to report:

```bash
CODE=$(docker compose -f deploy/docker/compose.yml exec -T web \
  uv run python manage.py issue_pairing_code --node --strong --plain)
docker compose -f deploy/docker/compose.yml exec web \
  uv run python manage.py run_reference_node --code "$CODE"   # Ctrl-C to stop
make preflight                                                # in another shell
```

It reads a generated fixture home (fake sessions, two tiny git repos),
never the host's files, and never prints its secret. Revoke it on
`/devices` when done; the command refuses to run without `DEBUG`.

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

The `/devices` UI for these is "Mac düğümleri" (branch 7).

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
