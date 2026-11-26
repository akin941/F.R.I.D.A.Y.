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

Phase 10 (`docs/prompts/phase-10-control/brief.md`, ADR-014) adds node
actions with approval; its closeout (`chore/p10-closeout`) wrote the
"Control" section below. The Swift confirmation window and executors are
the Phase 10 Mac session (M1–M2).

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

### A node on this Mac only (loopback TLS, nothing exposed)

When the stack and the node run on the same Mac and nothing may be reachable
from outside it (owner decision 2026-11-26), skip the overlay and the public
proxy:

```bash
make up-local-tls     # the dev stack + a Caddy proxy on 127.0.0.1:8443 only
```

`make local-tls` (run by `up-local-tls`) creates a self-signed P-256
certificate in `deploy/docker/local-tls/` (gitignored) once and prints its
SPKI pin. The proxy (`deploy/docker/compose.local-tls.yml`,
`Caddyfile.local`) forwards only `/api/v1/nodes/pair`,
`/api/v1/auth/nodes/credential/rotate` and `/ws/node/v1`; every other path
is 404, and it never installs a certificate into any trust store. Put the
printed `NODE_TLS_PIN_SHA256=...` line into `deploy/docker/.env` so
preflight's `node_tls_pin` checks the served key (the overlay sets
`PREFLIGHT_NODE_TLS_URL` to the proxy).

Pair from the FridayNode menu ("Eşleştir…"): server `https://127.0.0.1:8443`,
a strong node code (`manage.py issue_pairing_code --node --strong`; PIN codes
are refused because the proxy is not the BFF), and the printed pin in
"Sertifika pini". With a pin the node accepts the server only if its key
matches; the certificate is never trusted system-wide. `make local-tls
--force`-style key replacement (`gen-local-tls.sh --force`) means re-pairing.

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

## Several nodes, one server (Phase 13)

Phase 13 (`docs/prompts/phase-13-home-hub/brief.md`, ADR-017) makes several
nodes per owner a supported setup: every PC in the home is a node of the
one home server (`docs/runbooks/home-server-setup.md`). The protocol is
unchanged (the `node_protocol_frozen` preflight row checks it).

**Labels.** Each node has a unique name, a room, a role and up to five
aliases, set on `/devices` (the node edit dialog; rooms on
`/settings/home`). The node's `platform` (`macos`, `linux`, `windows`)
comes from pairing and cannot be edited. Labels are server data: nothing
is sent to the node. Pairing a second node with a name already in use adds
a suffix ("MacBook 2"); rename it on `/devices`. `nodes_labels` (preflight)
warns while a node in a multi-node home has no room.

**Which PC.** Every node call goes to exactly one node, chosen in this
order (`nodes.targeting`, no model call):
1. the PC named in your own words (name, alias, a room in locative form
   such as "salondaki", a role word such as "dizüstü", a platform word such
   as "Mac'te"). A named PC that is offline or lacks the capability is
   refused by name ("Masaüstü bu işlemi sunmuyor; başka bir bilgisayarda
   çalıştırmadım."), never replaced by another PC;
2. on a voice turn from a node, that node (the speaking PC);
3. the only eligible PC, or the one active in the last
   `NODE_LAST_ACTIVE_WINDOW_SECONDS` (15 min) and later than every other;
4. otherwise Friday asks: "Hangi bilgisayar: MacBook mu, Masaüstü mü?",
   and calls nothing.
The model's own `node_id` choice is checked against this and never wins.
Read tools stay web-only; the speaking-node rule applies to node-action
proposals from voice turns. The approval card names the PC, its room and
its platform, and a gate-class action still needs a click in **that** PC's
own window (ADR-014). Nothing is ever sent to several PCs at once.

**Presence and audit.** The HUD's **Düğümler** panel and `/devices` show
each node's status (connected, last heartbeat, voice state, invocable
reads and actions). `/devices/nodes/<id>` → "Denetim" lists that node's
own chain rows (`GET /api/v1/nodes/<id>/audit`).

**The Linux node.** `node/friday-node-linux` (README there) is a read-only
node for a Linux PC: protocol v1, no action capabilities, local policy in
`~/.config/friday-node/policy.json` changed only from a terminal with a
typed confirmation, a 0600 credential, a systemd **user** unit. It pairs
with `platform: linux` and must pin the server's TLS (`--ca` or
`--tls-pin-sha256`). Its vendored copy of the reference node is checked by
`scripts/vendor.py --check` in CI and by the `linux_node_vendor` preflight
row. A Windows node is not built (stub README only).

**Re-pointing a node** (moving the server, ADR-017 decision 7). The new
server URL is entered on the node itself, never sent by the server. Linux:
`friday-node-linux config set server-url https://<new>` as the node's user
(the stored CA / SPKI pins and the signing-key pin are kept; a server that
fails them is refused before any secret is sent). Mac: "Sunucu adresini
değiştir" behind Touch ID (Mac session M1, Phase 13 Part E). Keep the same
`NODE_SIGNING_KEY` and TLS key on the new server, or every node re-pairs.

**Pairing through the home server's Caddy** does not work yet:
`Caddyfile.example` sends `/api/v1/nodes/pair` and
`/api/v1/auth/nodes/credential/rotate` to the BFF, which has no such
routes. How they reach Django is an open owner decision (Phase 13 brief,
`chore/p13-closeout` owner steps).

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
reference node so `node_link` (and, on protocol v2, `node_protocol_v2`) has
a node to report:

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

## Control (Phase 10)

Phase 10 lets Friday run a small, closed set of actions on the Mac: repo
commands (`repo.command.run`: test, lint, typecheck, build, format_check),
AppleScript recipes, two Accessibility primitives and open/focus an app.
Brief: `docs/prompts/phase-10-control/brief.md`; classes: ADR-014. Until the
Swift app ships (Mac session M1–M2), only the Python reference node runs
them, on a fake executor.

### Protocol v2

- The node offers `friday-node.v2, friday-node.v1`; the gateway picks v2
  when both sides speak it. Pairing still answers the v1 baseline; the
  session version is per socket.
- Actions are invocable only on a v2 session whose catalogue entry equals
  the server contract (name, version, class, both schema hashes). On a v1
  session every action is `not_invocable` (`protocol_v1`).
- The server sends nothing new: actions ride the same signed `invoke`
  envelope, with `class` (`confirm` or `gate`) and, for gate class, a
  `grant`. The node answers `confirmation` (`shown`, then `approved`,
  `denied`, `timed_out` or `closed`), `output` chunks and one `result`.
- No server message reads or writes node policy (repos, command mappings,
  recipes, the bundle list). The owner edits them on the Mac behind Touch ID.

### Approving a node action

Every action needs two human acts on screen or one on screen and one on the
Mac; voice only proposes (ADR-015).

| Class | Capabilities | Acts |
|---|---|---|
| gate | `repo.command.run`, `applescript.recipe.run`, `ax.press`, `ax.type_text` | card click → `ApprovalRequest` → **Approve on `/approvals`** → one grant → click **Çalıştır** in the Mac window |
| confirm | `apps.open`, `apps.focus` | card click (`POST /api/v1/nodes/<id>/actions`) → click in the Mac window; no `ApprovalRequest` |

- Only the installation owner, holding the `owner` role in the target
  workspace, may request, approve or invoke a node action on their own
  node (`actor_not_owner` otherwise; `node_action` has `precheck_request`
  and `authorize_decision` hooks).
- Repo commands follow the 8A path rules: `~/work/acme` resolves to the
  workspace its `path_prefix` rule maps to; a path no rule maps is refused.
  AX, app and non-repo recipe actions are personal-workspace only.
- The trust check compares the proposal with what the owner actually typed
  or said in the last 30 minutes: `ok`, `warn` (`voice_evidence`,
  `repo_not_mentioned`, `unclear`) or `block` (no owner evidence, an
  inconsistent proposal). It never approves anything; a `block` creates no
  card. A voice-sourced card is at best `warn` and says "Sesle önerildi".

### What the Mac window shows

The node renders the window from **its own** validated args and the
contract's Turkish template — never server text: the action, the repo's
local name, the exact argv, the network setting ("Ağ: kapalı" unless the
owner mapped domains), and for gate class the first 8 characters of the
approval id, which must match the card on `/approvals`. One window at a
time; it closes after 60 s, on socket loss and on the kill switch.

### Owner-away rules (ADR-013 decision 6)

| Situation | Outcome (never retried) |
|---|---|
| a `node_action` card not decided within 10 minutes | `expired` at decision time or by the per-minute sweep |
| approved, but not dispatched within 60 s (`NODE_ACTION_DISPATCH_MAX_DELAY_SECONDS`) | `stale_dispatch` |
| the node is offline at dispatch | `node_offline` (the approval is consumed) |
| nobody clicks the Mac window within 60 s | `confirm_timeout` |
| socket loss or kill switch while a window is open or a command runs | `node_disconnected` |
| no result by the watchdog deadline | `timeout` |

A grant lives `NODE_GRANT_TTL_SECONDS` (90 s; 70–120 enforced by the
preflight, > 120 refused at boot), is single use and is bound to one
approval's args: a replay is refused `replay`, approval A's grant on
approval B's args `grant_mismatch`.

### Reading executions and chain rows

- `GET /api/v1/nodes/<id>/actions/<execution_id>` (owner web session):
  status, class, capability, confirm state, exit code, refusal code,
  timestamps — never args or output.
- The HUD's terminal panel streams the output of the actor's executions
  (`GET /api/v1/hud/terminal/<execution_id>/stream`, SSE): scrubbed by the
  server, sanitised of escape and invisible characters, labelled
  "Güvenilmeyen çıktı", kept in Redis 15 minutes, at most 1 MiB. Only a
  4 KiB scrubbed `tail` is stored on the row, cleared after
  `NODE_ACTION_TAIL_RETENTION_DAYS` (14). A reverse proxy must not buffer
  `/api/v1/hud/terminal/*/stream`.
- "Testler ne dedi?" uses the `node_action_result` read tool: the latest
  execution's tail, fenced as untrusted text.
- Each execution writes exactly one chain row (`NodeInvocation`,
  `hash_version` 2: class, approval id, grant id, args hash — no args, no
  output) and one `nodes.invoke` AuditLog row. `manage.py
  verify_node_audit_chain` checks the chain.

### The escape suite (ADR-014 decision 3)

```bash
cd backend
uv run python manage.py node_escape_suite                    # fake sandbox (CI, preflight)
uv run python manage.py node_escape_suite --executor srt     # Linux srt, opt-in, local evidence only
uv run python manage.py node_escape_suite --export-policy /tmp/policies.json
uv run python manage.py node_escape_suite --check-policy /tmp/policies.json
```

32 cases (writes outside the worktree incl. `/tmp` symlinks, denied reads,
network, env leaks, limits, a deleted worktree) must all be contained. The
ADR-014 evidence is the same cases under macOS `srt` in the Swift app
(Mac session M3); record it in ADR-014 and set
`NODE_ESCAPE_EVIDENCE_RECORDED_ON=<date>`. Test and lint stay gate-class
until a later ADR-014 amendment, whatever the suite says.

### Preflight control checks

| Check | Pass | Warn | Fail |
|---|---|---|---|
| `node_grant_config` | grant TTL 70–120 s, dispatch delay ≤ 60 s, `node_action` approval 600 s, the approval sweep, node action sweep and tail retention on the worker beat | — | any bound broken, the tool unregistered, a sweep missing, too rare or unregistered |
| `node_protocol_v2:<name>` | the node's catalogue is on v2 with gate/confirm actions invocable | v1 session, no catalogue, no action invocable | — |
| `node_protocol_v2` | — | no node paired, or none speaks v2 (Mac session pending) | — |
| `node_conformance_v2` | v1 and v2 case files and `vectors/sandbox.json` current | — | stale (`build_node_conformance`), no cases |
| `node_escape_suite` | all cases contained and the Mac evidence date set | all contained, Mac evidence pending | a case escapes, a malformed date |
| `node_output_scrubber` | the gateway's `/healthz` says `output_scrubber: true` | no `PREFLIGHT_NODE_GATEWAY_URL` | gateway down, no scrubber, an older gateway |
| `trust_check` | the real check and evidence source registered; `control.trust_check` has a tier, a capped budget and its prompt | the budget has no monthly cap | nothing registered, the branch-3 stub, no tier, no budget, prompt not seeded |
| `node_action_actor` | both hooks declared, `approve_request` calls the approver hook first | — | a hook missing, the call missing or after the decision |
| `harness_drivers:<agent>` | disabled (CLI), or enabled with d4, a real snapshot and agreeing pins | — | see `docs/runbooks/harness-drivers.md` |

On the dev stack, `run_reference_node` (above) offers v2 by default, so
`node_protocol_v2` passes with the reference node paired. Its window is a
scripted double: `--confirm none` (default) lets every window time out,
`--confirm approve|deny` clicks it after one second; `--protocol 1` keeps a
v1-only session. Actions only ever reach its fake executor.

### Control settings (all optional)

| Name | Default | What |
|---|---|---|
| `NODE_GRANT_TTL_SECONDS` | 90 | a gate-class grant's lifetime (boot refuses > 120; preflight wants ≥ 70) |
| `NODE_ACTION_DISPATCH_MAX_DELAY_SECONDS` | 60 | approved → dispatched limit (`stale_dispatch` beyond) |
| `NODE_ACTION_TAIL_RETENTION_DAYS` | 14 | when the stored scrubbed tail is cleared |
| `HUD_TERMINAL_POLL_SECONDS` / `HUD_TERMINAL_RATE` | 0.5 / 30/min | the terminal stream's poll and throttle |
| `AI_BUDGET_CONTROL_MONTHLY_USD` | 5 | the seed value of the `control` budget (edit the row in the admin) |
| `NODE_ESCAPE_EVIDENCE_RECORDED_ON` | empty | the date the M3 evidence was recorded in ADR-014 |

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
