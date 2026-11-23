# Phase 9 — Friday Node v1, read-only (server side + Mac session)

> Status: **Draft (2026-11-20). Written on `docs/p9-brief` from the stub of
> the same date, which was taken from the owner-approved Friday 3.0 roadmap.
> It needs its independent Sonnet 5 plan review before branch 1, because
> the implementing model never reviews its own brief.**
> Progress: the server branches 1–7 and the closeout are implemented
> (2026-11-20, "Branch table"); the go/no-go record is written, with the Mac
> rows pending owner evaluation ("Mac session (deferred)").
> Sources: `docs/research/2026-11-roadmap-proposal.md` §B1, §c.4, §c.5,
> §c.6, §c.7, §c.8, §c.9, §g d1/d9; MASTER_PLAN §AA "Friday 3.0 phases"
> (order 4).
> Parent decisions:
> - ADR-001: workspace axis, `for_actor()`.
> - ADR-002: modular monolith and its import contracts.
> - ADR-003: single gateway door. The node makes no model call.
> - ADR-004 and ADR-008 decision 3: the gate. Nothing in this phase is
>   gated, because every capability here is a read.
> - ADR-009: unchanged.
> - ADR-010: the personal-data provider rule, and the governance of new
>   dependencies.
> - **ADR-013**: the node protocol. Its identity decision is conditional
>   on spike S9.0, which is branch 1 here.
> - ADR-014: only the read class applies in this phase.
> - ADR-012: node data reaches memory only through the memory-owned port.
> - ADR-015 decision 2: the mic indicator slot on the menu-bar icon.
>
> **Platform boundary (owner decision 2026-11-20).** The implementation
> container is Linux, with no macOS and no Xcode. Two kinds of work follow
> from that:
> - **In scope now, with full branches (1–7 and the closeout):** everything
>   that can be built and tested here. That covers the Django apps, the
>   WebSocket gateway, the protocol schemas, signing and verification, the
>   frontend panels, and a **Python reference node**. The reference node
>   implements the node side of the protocol, so the compromised-server
>   and contract tests exist now. They form the conformance suite that the
>   Swift app must pass later.
> - **Not built here:** everything that must be built, signed or run on
>   macOS. That means the Swift `friday-node` app, Keychain, Touch ID, TCC
>   grants, notarization and real-hardware measurements. These are listed
>   under "Mac session (deferred)" and marked "deferred: Mac session" in
>   the branch table.
>
> Go/no-go items that need the Mac or real-world owner data are recorded
> as **pending owner evaluation**. They do not block Phase 8B.
>
> **Precondition.** These are met on the integration branch:
> - the Phase 8A closeout, which brings the hook relay, `CodingSession`,
>   the mapping rules and the scrubber;
> - the Phase 6 HUD health panel.
>
> The server branches need nothing else. The Mac session also needs:
> - the owner's Apple Developer enrolment (d9);
> - a Developer ID identity on the Mac.
>
> **Branches.** Phase 9 branches are cut from the integration branch that
> holds the merged Phase 8A closeout, and they integrate on the phase
> integration branch. They use `feat/p9-…` names, under the MASTER_PLAN
> stable-numbering rule.

## Goal / value

Friday should answer "Claude Code / Codex oturumlarım ne yapıyor?" with
detail from the owner's own Mac. That detail is:
- which sessions and threads exist;
- what the last turns of one of them say;
- what they cost (`ccusage`);
- the git state of the registered repos;
- which app and windows are in front.

It does this **read-only**, with no path by which the server could
change node policy or run anything. Phase 9 delivers four things:

1. **Spike S9.0.** A `kind=node` principal on the existing device trust.
2. **The `nodes` kernel app.** It holds the node row, the capability
   snapshot, the hash-chained invocation audit and the WSS gateway, all
   behind the ADR-013 import contract.
3. **A versioned protocol.** It has JSON schemas, canonical JSON and
   Ed25519-signed `invoke` envelopes, and a Python reference node with a
   compromised-server conformance suite.
4. **The surfaces that use it:**
   - ungated, audited read tools for the chat;
   - the hook relay path from the node into the 8A ingestion port;
   - the HUD health panel and node management on `/devices`;
   - `manage.py preflight` node checks.

The Swift app that plugs into this contract is built later, in a Mac
session, against the same conformance suite.

## What already exists (verified 2026-11-20 on the integration branch)

- **Device trust (Phase 4 branch 2).**
  - `accounts.TrustedDevice` (`backend/accounts/models.py:146`): a hashed
    256-bit secret (`secret_hash`), soft revoke with `revoke_reason`,
    idle expiry `DEVICE_IDLE_EXPIRY_DAYS`
    (`config/settings/base.py:328`, default 90) and `is_usable()`.
  - `DevicePairingCode` (`accounts/models.py:199`): `pin` (8 digits,
    5 min, LAN only, at most 5 attempts) or `strong` (≥ 128 bits, 30 min,
    any network). It is single-use under `select_for_update`
    (`accounts/devices.py:285`).
  - Services in `accounts/devices.py`: `issue_pairing_code` (:197),
    `enroll_device` (:335), `start_device_session` (:436),
    `revoke_device` (:466) and `revoke_all_devices` (:484, which
    revokes every active `TrustedDevice` of the user, audited
    `accounts.device.revoked_all`).
  - Commands: `manage.py issue_pairing_code [--strong]` and
    `revoke_all_devices`.
  - Endpoints: `POST /api/v1/auth/devices/<id>/revoke`, and the
    `/devices` page with `components/integrations/DevicesPanel.tsx`.
  - Device secrets use the prefix `jdv_` and live in the browser's
    HttpOnly cookie. The device then mints JWT sessions.
  - **Shaped for a browser:** idle expiry, a cookie, and a web session
    with full trust.
- **Channels and authenticators.** `core/channels.py` defines:
  - `CHANNEL_WEB`, which only `DeviceJWTAuthentication`
    (`accounts/authentication.py:183`) produces;
  - the low-trust `CHANNEL_CLI`, `CHANNEL_OPENAI`, `CHANNEL_HOOK`,
    `CHANNEL_TELEGRAM`, `CHANNEL_SYSTEM` and the default-deny
    `CHANNEL_UNKNOWN`.

  `KIND_CHANNELS` (`accounts/authentication.py:70`) maps PAT kinds
  `cli`, `openai` and `hook` to channels, and each authenticator accepts
  one kind. Two URL-walk matrices pin which surface each principal
  reaches: `config/tests/test_channel_matrix.py` and
  `config/tests/test_hook_token_surfaces.py`.
- **Hook relay (Phase 8A branch 3).**
  - `integrations/cli/friday_hook.py` is stdlib-only and reads its token
    from the Keychain (`friday-hook` service). It exits 0 on every error
    within a 3 s deadline and keeps no spool. It posts to
    `POST /api/v1/memory/ingest/hook-events`, which is wired with
    `HookTokenAuthentication` by `config/urls.py`.
  - `memory/ingestion/hooks.py`:
    `ingest_hook_events(*, user, bound_workspace_id, events, now=None)`
    (:301) is channel-agnostic. It accepts ≤ 50 events per batch
    (`MAX_EVENTS_PER_BATCH`) and allow-listed event names, drops events
    older than 24 h (`MAX_EVENT_AGE`), dedupes on the event id through
    `record_episode`'s `(source, external_id)`, and upserts
    `memory.CodingSession` (`memory/models.py:643`).
  - The path mapping uses `memory/ingestion/mapping.py`:
    `normalise_path` (:39), `resolve_workspace(source, mapping_key)`
    (:102), and `path_prefix` rules of the `claude_code_hooks`
    `IngestionSource`.
  - The scrubber is `memory/ingestion/scrubber.py`, with a vendored copy
    `integrations/cli/_scrubber_vendored.py` checked identical in CI. The
    corpus is `memory/tests/scrubber_corpus/`, gated at 100% secrets
    recall.
- **Read tools.** `tools.registry.ReadToolSpec` (`tools/registry.py:219`)
  has `data_class` `firm`/`personal` (:166) with `READ_CHANNELS` (:174).
  Results are fenced as `<connector_result source=...>` and audited.
  `tools/coding_sessions.py` is the 8A precedent: a web-only read over
  `CodingSession`.
- **HUD.** `hud/services.py` `health_section` (:195) covers providers,
  budgets and the last preflight. Its docstring says "the node link
  arrives in Phase 9". `hud` may import only `core`, `workspaces`,
  `ai_core`, `approvals` and `tasks`, and it is a leaf
  (`backend/pyproject.toml`, "hud may import only …").
- **Audit.**
  - `audit.AuditLog` (`audit/models.py`) is append-only through a DB
    trigger (migration 0002). It is **not hash-chained**, and
    `record_audit` is the only writer.
  - ADR-013 decision 10 asks for a hash-chained row per invoke, so the
    chain is new in this phase (D8).
- **Preflight.** `ops/preflight.py` holds checks such as `check_web` and
  `check_redis`, plus the learning checks. `ops` is a leaf that may read
  anything. The HUD reads the last result from the cache
  (`HUD_PREFLIGHT_CACHE_KEY`).
- **Serving.**
  - `config/asgi.py` is plain `get_asgi_application()`.
  - The daily stack runs `gunicorn config.wsgi` (gthread,
    `deploy/docker/compose.daily.yml`), published on 127.0.0.1 only. The
    owner exposes it through an overlay or a TLS reverse proxy.
  - **There is no WebSocket server, no Django Channels and no websocket
    library in `uv.lock`.**
  - `uvicorn` (0.54.0), `cryptography` (50.0.1) and `jsonschema`
    (4.26.0) are in the lock only as transitive dependencies of the MCP
    SDK.
- **Import contracts.** The 8A contract "memory.ingestion may import only
  …" carries the comment "A `nodes` app does not exist yet; when it lands
  (Phase 9), add it here". Blanket contracts already list every kernel
  app: core independence, kernel apps ↛ config, ai_core ↛ business,
  audit ↛ business, no app ↛ harness/ops/voice/hud, and MCP client-only.
- **Self snapshot.** `assistant/self_sources.json` quotes the MASTER_PLAN
  §AA row of the current phase, currently "9 … planned". CI's
  `build_self_sources --check` fails if that row changes without a
  regenerated snapshot.

## Boundary vs existing systems

- **Identity stays in `accounts`.** A node is a `TrustedDevice` of
  `kind=node`. It is not a new credential table and not a PAT kind
  (D1). `nodes` imports `accounts`; `accounts` never imports `nodes`.
- **The node is never `CHANNEL_WEB`.** A node credential cannot mint a
  JWT session, cannot reach any DRF view except its two node endpoints,
  and authenticates as the new low-trust `CHANNEL_NODE`. It can never
  decide an approval or confirm anything (ADR-004).
- **Read-only, server side too.**
  - Phase 9 has no code path that sends a non-read `invoke`.
  - A catalogue entry of another class is recorded in the snapshot and
    shown, but it is never invocable.
  - Gated invocation grants (ADR-013 decision 4) arrive in Phase 10.
  - The envelope format reserves their field now (D5).
- **No policy surface.** No message type, endpoint, serializer field or
  UI control reads or writes node policy (allowlist, per-directory
  grants, catalogue). The node reports grant *availability* in `hello`
  as read-only data. Changing it happens only on the Mac, behind
  LocalAuthentication (ADR-013 decision 5).
- **No model calls in `nodes`.** The node holds no provider keys, and
  `nodes` may not import `ai_core`. Read results reach a model only
  inside the normal `chat` call, as fenced tool results through
  `tools.registry` (D9). That path writes one `ModelUsage` per call, as
  today.
- **Memory boundary.** Node-relayed hook events enter
  `memory.ingestion.hooks.ingest_hook_events` through a sink that the
  composition root injects (D10). `nodes` never imports `memory`, and
  `memory` never imports `nodes`. Transcript *ingestion* is Phase 8B.
  Phase 9 reads transcripts on demand, for one turn, and stores only
  audit metadata.
- **Workspace axis.**
  - The `Node` row lives in the owner's personal workspace.
  - Every invocation names its target workspace, which the server
    resolves from the 8A path rules before invoking. Every row a read
    returns is filtered by the same rules. An unmapped path is dropped,
    never defaulted (ADR-013 decision 10, ADR-012 decision 2).
  - `NodeInvocation` is a `WorkspaceScopedModel` of that target
    workspace.
- **`friday-hook` keeps working.** The node relay is an addition. Both
  paths dedupe on the same event id, so the owner can switch the Claude
  Code hook entries on the Mac whenever the node is ready.

## Decisions (with justification)

### D1 — Spike S9.0: `kind=node` on `TrustedDevice`, not a new table

These changes land in `accounts` (branch 1).

- **`TrustedDevice.kind`.** `browser` (default, existing rows) or `node`.
  - Node secrets use their own prefix, `jnd_`, with the same 256-bit,
    sha256-hashed shape.
  - `start_device_session` and `DeviceJWTAuthentication` refuse any
    `kind=node` row, which gives two independent checks. A node never
    gets a JWT.
  - `enroll_device` (the browser path) refuses a code issued for a node,
    and the reverse holds too.
- **`DevicePairingCode.device_kind`.** `browser`/`node`, default
  `browser`. A node code is issued by `manage.py issue_pairing_code
  --node [--strong]` or by a trusted web session (`POST
  /api/v1/auth/pairing-codes` with `device_kind=node`). The PIN and
  strong-code rules are unchanged: PIN on LAN only, 5 attempts, TTL.
- **Lifetime.** A node has no idle expiry.
  - **Age cap:** the credential is refused after
    `NODE_CREDENTIAL_MAX_AGE_DAYS` (default 90) counted from its last
    rotation.
  - **Rotation:** the node should rotate after
    `NODE_CREDENTIAL_ROTATE_AFTER_DAYS` (default 30). The server
    advertises this in `hello.ack`.
  - **Unused nodes:** a node with no successful connection for
    `NODE_IDLE_EXPIRY_DAYS` (default 30) is expired, the same as idle
    expiry.

  Justification: a browser cookie can be re-minted interactively, but a
  background agent cannot. A hard age cap plus automatic rotation keeps
  a stolen secret's lifetime bounded without forcing re-pairing.
- **Rotation without re-pairing.**
  `POST /api/v1/auth/nodes/credential/rotate` is authenticated by the
  current node secret. It returns a new secret exactly once. The
  previous hash stays valid for `NODE_ROTATION_GRACE_SECONDS` (default
  600) **or until the new secret is first used**, whichever comes first,
  so a lost response does not lock the node out. A rotation using the
  previous secret during the grace is refused, so it cannot chain.
  Rotation is audited as `accounts.node.rotated`.
- **Immediate revocation.**
  - The existing `revoke_device` and `revoke_all_devices` cover nodes
    unchanged: they filter on `user`, not `kind`. A test pins this.
  - Revoking calls `core.revocation.publish_device_revoked(device_id,
    user_id)`. This is a best-effort Redis `PUBLISH` on
    `friday:device-revoked` with the payload `{"device_id": "<uuid>",
    "user_id": "<uuid>"}`; both keys are always present.
    It is an explicit call, not a Django signal, following the
    `accounts/services.py` "explicit over signals" note. The gateway
    subscribes to the channel (D6) and closes that node's socket within
    2 s.
  - The guarantee does not depend on Redis: every heartbeat re-checks
    the device row, so the worst case is ≤ 15 s. This is the node
    equivalent of the web channel's "next request".
- **Owner kill switch.** `accounts.NodeKillSwitch` is a per-owner row
  with `engaged_at`, `engaged_via` and `engaged_by_device`.
  - While engaged, every node authentication fails and every live node
    socket **of that owner** is closed. It is published on the same
    channel as `{"device_id": "*", "user_id": "<owner uuid>"}`; the
    wildcard is always scoped by `user_id` and is never published
    without it. Pairing a new node is refused too.
  - It is reversible without re-pairing. That is the difference from
    revocation, which is permanent.
  - Controls: `manage.py node_kill_switch on|off|status`, plus
    `POST/DELETE /api/v1/auth/nodes/kill-switch` (web channel, owner,
    human) and a toggle on `/devices`.
  - Audited as `accounts.node.kill_switch.engaged` and `.released`.
- **Authenticator.** `NodeCredentialAuthentication` accepts only `jnd_`
  secrets of a usable `kind=node` device of the active, human owner with
  the kill switch released. It sets
  `request.auth = ChannelPrincipal(channel=CHANNEL_NODE, …)`. It is used
  only by the rotate endpoint and, through `nodes`, by the WebSocket
  handshake. Nothing else accepts it: the server's signing keys reach
  the node only in the anonymous pairing response (D4). Failures are audited with the same
  flood cap as PAT failures (`AUTH_FAILURE_AUDIT_LIMIT`).
- **Spike pass criteria.** All of these must hold, in tests on the
  server:
  1. pair with both code kinds, and the cross-kind refusals hold;
  2. rotate without re-pairing, including grace expiry and first-use
     cut-over;
  3. revoke one node, or all devices, and the next authentication fails;
  4. kill switch on, then off;
  5. a node secret is refused by every other surface (URL walk);
  6. a browser device or PAT is refused by the node surfaces.

  Branch 1 records the result in an ADR-013 "Implementation notes —
  spike S9.0" section. The Keychain half (storing, reading and rotating
  the secret from Swift) is Mac work, M1 below. Its result is pending
  owner evaluation and does not hold back branches 2–7, because the
  server contract it must meet is fixed by these tests.

**If the spike fails on the server, Phase 9 stops and ADR-013 is
revisited.** "Fails" means a criterion that cannot be met without a
browser-shaped compromise: idle expiry, a cookie, or a JWT for nodes.

### D2 — `nodes` kernel app and its contracts

`backend/nodes/` is registered in branch 2. Its import contract is
ADR-013 decision 9, verbatim:

- `nodes` may import `approvals`, `audit`, `workspaces`, `accounts` and
  `core`.
- It may not import `config`, `ai_core`, `memory`, `knowledge`,
  `harness`, `assistant`, `tools`, `agents`, `skills`, `integrations`,
  `connectors`, `deploys`, `voice`, `hud` or `ops`.

**Pure-Python packages.** `nodes.protocol` and `nodes.reference` must not
import Django or any project package outside `nodes.protocol` (the
reference node's vendored scrubber is a generated sibling module inside
`nodes.reference`, D13 item 6). Two things enforce this: a contract over the project packages, and a test that
imports them in a subprocess with Django absent from `sys.modules`. These
two packages are the shared spec that the Swift app mirrors.

**Who may import `nodes`.** Only `tools` (read tools, D9), `hud` (the
health read model, D11), `ops` (preflight) and the composition root
`config`. A new contract, "only tools, hud, ops may import nodes", lists
every other kernel app as a source. `memory` and `memory.ingestion` gain
`nodes` in their forbidden lists, which closes the 8A placeholder.

**`nodes` joins the blanket lists:**
- core independence;
- kernel apps ↛ config;
- ai_core ↛ business;
- audit ↛ business;
- no app ↛ harness/ops/voice/hud;
- MCP client-only.

**Two allowlist edges grow.** `hud` gains `nodes` and `tools` gains
`nodes`. Neither breaks a rule:
- `nodes` stays unaware of both.
- ADR-013 limits what `nodes` imports, not who reads it.

### D3 — The node session is served by a separate ASGI process

- `config/asgi_nodes.py` builds a minimal raw-ASGI router:
  - `websocket` scope on `/ws/node/v1` →
    `nodes.gateway.NodeSocketApp`;
  - `http` `GET /healthz` → a liveness JSON (process up, Redis
    reachable, connected node count, the configured signing key id —
    never key material — for the closeout `node_signing_key` check);
  - everything else → 404.

  Django is set up for ORM access, and ORM calls go through
  `asgiref.sync.sync_to_async(thread_sensitive=True)`.
- It is served by `uvicorn --ws wsproto` as a new compose service,
  `node-gateway`, published on `127.0.0.1:8002` only, like every other
  port. The owner exposes it through the existing overlay or TLS
  reverse proxy, for example a Caddy route `wss://<host>/ws/node/v1`
  with the node pinning that certificate or CA (D4). Web and worker are
  untouched: gunicorn stays WSGI.
- **No Django Channels.** We need one consumer, a per-node inbox and a
  revocation fan-out. Redis (already a dependency) covers this with a
  list and pub/sub (D6). Channels would add a channel-layer framework,
  `channels-redis` and `daphne` for one endpoint.
- **New direct dependencies** are added through the
  `docs/security/mcp-governance.md` dependency rules: exact pin plus
  `uv.lock` hash, licence review, no install scripts. They are recorded
  in a manifest `docs/mcp/dependencies/node-gateway.md`.
  - `uvicorn==0.54.0`: already locked; promoted to direct.
  - `wsproto` (new, MIT, pure Python, from the python-hyper project).
    It is used by uvicorn on the server and by the reference node's
    client transport, so one websocket implementation serves both ends.
  - `cryptography==50.0.1` (Apache-2.0/BSD, promoted): Ed25519.
  - `jsonschema==4.26.0` (MIT, promoted): protocol validation.
- Browser origins are refused. A handshake carrying an `Origin` header is
  closed with 4403, because a node sends none. A cross-site WebSocket
  from a browser on the owner's machine therefore cannot reuse anything.
  This applies the OpenClaw lesson to the server side.

### D4 — Pairing endpoint and key pinning

- `POST /api/v1/nodes/pair` (in `nodes`):
  - anonymous, with throttle scope `node_pair` (Redis-backed, fails
    closed);
  - body: `{code, node_name, protocol_versions}`;
  - calls `accounts.devices.enroll_node_device`;
  - returns, exactly once:
    - `node_id`;
    - `node_secret` (`jnd_…`);
    - `server_signing_keys`, a list of `{key_id, alg:"Ed25519",
      public_key_b64}`;
    - `protocol_version` (`1`);
    - `gateway_path` (`/ws/node/v1`);
    - `rotate_after_days`.
- The node pins `server_signing_keys` at pairing (ADR-013 decision 4).
  It refuses any `invoke` signed by an unpinned `key_id`. The server
  records the key id it handed out on `Node.pinned_key_id`, so preflight
  can detect a key change that would strand a paired node.
- The TLS pin (certificate or CA SPKI hash) is captured by the node at
  pairing, from the connection it paired over. Mac task M1 stores it
  next to the secret in the Keychain.
- On the server, `NODE_TLS_PIN_SHA256` (optional) states the expected
  SPKI hash so that preflight can warn when the served certificate no
  longer matches what nodes pinned (closeout).
- **Server signing key.** Configuration:
  - `NODE_SIGNING_KEY` is an Ed25519 private key, base64 raw 32 bytes,
    from the environment only. `.env.example` names it with an empty
    value, and `deploy/docker/gen-secrets.sh` gains a generator.
  - `NODE_SIGNING_KEY_ID` is derived: the first 16 hex characters of
    sha256 of the public key.
  - `NODE_SIGNING_KEYS_RETIRED` holds public keys that still verify but
    never sign. It is used for rollover. Rollover means re-pairing the
    node in Phase 9; signed key announcements are left for later (Q3).
  - A missing or malformed key makes `nodes` refuse pairing and invokes.
    Preflight fails, and no other surface is affected.

### D5 — Protocol v1: messages, canonical JSON, signed envelopes

- **Schemas** live in `backend/nodes/protocol/schemas/v1/*.schema.json`
  (JSON Schema 2020-12, `additionalProperties: false` everywhere, every
  string length-bounded, every array item-bounded). They are the single
  source of truth. `nodes.protocol.validate(message, direction)` loads
  them.
- **Message types** form a closed set. Every message has `v` (`1`),
  `type` and `id` (a UUIDv4 string).

  | Direction | Type | Purpose |
  |---|---|---|
  | node → server | `hello` | Node version, OS version, `catalogue` (entries: `name`, `version`, `class`, `args_schema`, `result_schema`, `availability` `granted`/`not_granted`/`unsupported`), `catalogue_sha256` |
  | node → server | `heartbeat` | `seq`, uptime, spool depth (count/bytes) |
  | node → server | `result` | `invocation_id`, `status` (`ok`/`refused`/`error`/`timeout`), `refusal_code`, `output` (schema-checked, ≤ `NODE_MAX_RESULT_BYTES` = 64 KiB), `truncated`, `output_sha256`, `duration_ms` |
  | node → server | `event` | `kind` (allow-list: `claude_code.hook`, `grant.availability_changed`), `batch` (≤ 50), `spooled` flag |
  | server → node | `hello.ack` | `node_id`, `heartbeat_seconds` (15), `rotate_after_days`, `server_time`, accepted capability names |
  | server → node | `heartbeat.ack` | `seq`, `server_time` |
  | server → node | `invoke` | The signed envelope below |
  | server → node | `event.ack` | `event_id`, counts (`stored`/`duplicate`/`dropped`) — never content |
  | server → node | `bye` | `reason` (`revoked`, `kill_switch`, `credential_expired`, `replaced`, `protocol_error`, `server_shutdown`) |

- **Policy-mutation guard.** Two tests guard the set:
  - The type registry is a frozen constant. The server→node set must be
    exactly `{hello.ack, heartbeat.ack, invoke, event.ack, bye}`.
  - No type name or schema property may match
    `(?i)policy|allowlist|grant(?!_id)|catalogue_set|config|exec_policy|settings`.

  This is ADR-013 decision 5's "the protocol has no policy-mutation
  message type", made mechanical. `bye` carries a reason and nothing
  else. It cannot change anything on the node except closing the
  session, which the node can always do.
- **Canonical JSON** (`nodes/protocol/canonical.py`) is the RFC 8785
  (JCS) subset that our schemas allow:
  - UTF-8;
  - keys sorted by UTF-16 code units;
  - no insignificant whitespace;
  - integers only (no floats: the schemas forbid `number`
    non-integers);
  - strings escaped per JCS.

  Test vectors in `nodes/protocol/vectors/canonical.json` are shared
  with the Swift implementation (M1).
- **The `invoke` envelope.** Every `invoke` is signed, even for reads:

  ```
  {v, type:"invoke", id, invocation_id, node_id, workspace_id,
   capability, capability_version, class:"read", args, args_sha256,
   nonce (32 random bytes, b64url), issued_at, expires_at, trace_id,
   grant: null, key_id, sig}
  ```

  - `sig` is Ed25519 over the canonical JSON of the envelope without
    `sig`.
  - `args_sha256` is sha256 of the canonical JSON of `args`.
  - `expires_at - issued_at ≤ NODE_READ_INVOKE_TTL_SECONDS` (default
    30). ADR-013's 120 s ceiling applies to Phase 10 grants.
  - `grant` is reserved for Phase 10's approval-bound grant and must be
    `null` in v1. A non-null `grant` is refused by the v1 node.

  Justification: signing reads costs one signature per call. In return,
  the replay, expiry, signature and args-hash checks are real from the
  first branch, so the compromised-server suite tests the same code path
  that Phase 10 will extend.
- **Node-side verification order**, which is also the refusal-code
  order and is pinned by the conformance suite:

  | # | Check | Refusal code |
  |---|---|---|
  | 1 | Frame size ≤ 256 KiB | `too_large` |
  | 2 | JSON parses | `malformed` |
  | 3 | Known `type` for this direction | `unknown_type` |
  | 4 | Schema | `schema` |
  | 5 | `key_id` pinned | `unknown_key` |
  | 6 | Signature | `bad_signature` |
  | 7 | `node_id` is mine | `wrong_node` |
  | 8 | `issued_at ≤ now + 5 s` skew | `not_yet_valid` |
  | 9 | `expires_at > now` | `expired` |
  | 10 | TTL ≤ max | `ttl_too_long` |
  | 11 | Nonce unseen (kept until expiry + skew, bounded LRU of 10 000; a full cache refuses) | `replay` / `nonce_cache_full` |
  | 12 | Capability in my catalogue at this version | `unknown_capability` |
  | 13 | Class `read` and `grant == null` | `class_not_allowed` |
  | 14 | Args hash | `args_hash_mismatch` |
  | 15 | Args valid against **my own** `args_schema` | `args_schema` |
  | 16 | Availability `granted` | `not_granted` |

  A refusal produces a `result` with `status=refused` and the code, and
  nothing runs. A frame that fails checks 1–4 gets an `error` frame and
  increments a local counter. Three such frames in 60 s make the node
  close the socket and back off. The node never echoes the offending
  payload.

### D6 — Gateway behaviour

`nodes/gateway.py` implements the server side of ADR-013 decision 6.

- **Handshake.**
  - The `Authorization: Bearer jnd_…` header is checked by the
    `accounts` node-credential service. Failures close with 4401.
  - An `Origin` header closes with 4403. A protocol subprotocol other
    than `friday-node.v1` closes with 4400.
  - One live socket per node. A second connection replaces the first,
    which gets `bye{reason:"replaced"}` and is closed. This avoids two
    inboxes.
- **`hello`.**
  - The first frame must be `hello`, within 10 s, or the socket closes
    with 4408.
  - The catalogue is validated, and a `CapabilitySnapshot` row is
    written when `catalogue_sha256` changes. Entries are checked
    against the server's known contracts (D7). An unknown name, a
    version or schema mismatch, or a non-read class marks the entry
    `not_invocable` with a reason. It is visible in the UI and never
    invoked.
  - `hello.ack` names the accepted entries.
- **Heartbeat.**
  - The node sends every 15 s and the server acks.
  - The server re-checks the device row, the kill switch and credential
    age on every heartbeat. If any fails it sends `bye` and closes.
  - Presence is a Redis key `nodes:presence:<node_id>` with a TTL of
    45 s (three missed heartbeats), refreshed per heartbeat.
  - `Node.last_heartbeat_at` is written at most once per 60 s.
- **Invoke bus.**
  - `nodes.services.invoke_read(…)` (D7) runs in any process. It pushes
    the signed envelope to `nodes:inbox:<node_id>` (Redis list, TTL =
    the envelope TTL) and blocks on `BLPOP nodes:result:<invocation_id>`
    for at most `NODE_INVOKE_TIMEOUT_SECONDS` (default 10, < TTL).
  - The gateway process holding the node's socket consumes the inbox:
    one `BLPOP` loop per connected node. It forwards to the socket,
    matches the node's `result` by `invocation_id`, validates it against
    the server's `result_schema` for that capability, and `LPUSH`es it
    to the result key with a 30 s TTL (`EXPIRE` set in the same
    `MULTI` as the `LPUSH`, so a result key never exists without one).
    The caller `DEL`s the key right after its `BLPOP` returns, so a
    result normally lives in Redis for milliseconds and at most 30 s
    when no caller is waiting. This transit is ephemeral and is the
    only copy of output outside the caller (D7).
  - A late result, after the caller timed out, is discarded and counted.
    It never runs anything.
- **Fail closed.**
  - With no presence key, `invoke_read` fails at once with
    `node_offline`, and nothing is queued.
  - When a socket drops, every in-flight invocation of that node is
    resolved `node_disconnected`. Invocations are never retried
    (decision 6).
  - With Redis down, invoke fails with `bus_unavailable`, and the
    gateway refuses new handshakes with 1013.
  - At most `NODE_MAX_INFLIGHT` (default 4) concurrent invocations per
    node. Beyond that, `busy`.
- **Revocation fan-out.** The gateway subscribes to
  `friday:device-revoked`. A message matches a socket when its
  `device_id` equals that node's device id, or when `device_id` is `"*"`
  **and** `user_id` equals that node's owner id (the kill switch, D1).
  It sends `bye` and closes the matching sockets within 2 s. A payload
  that fails to parse, lacks `user_id`, or carries `"*"` without a
  `user_id` closes nothing and is counted. That fails safe: the
  heartbeat re-check (≤ 15 s) still applies.
- **Result routing is per node.** Each socket keeps the set of
  invocation ids it has forwarded and not yet resolved. Before
  forwarding, the gateway checks that the envelope's `node_id` equals
  the socket's node (otherwise the envelope is dropped and the caller
  resolved `error`). A `result` is accepted only if its `invocation_id`
  is in **that socket's** in-flight set; a result for an unknown,
  finished or other node's invocation is discarded, counted as a frame
  error (D5's three-in-60 s rule closes the socket), and never pushed
  to any result key. One node therefore cannot answer, or poison, an
  invocation sent to another.
- **Limits.**
  - Frame ≤ 256 KiB inbound.
  - ≤ 20 inbound frames/s per socket, beyond which the socket closes
    with 4429.
  - `event` batches ≤ 50.
  - Idle sockets without heartbeat close at 45 s.
- **Logging.** Structured, with no frame bodies: node id, type, sizes,
  codes, trace id.

### D7 — Server-known capability contracts and `invoke_read`

- `nodes/capabilities/v1/` holds one JSON file per read capability:
  `name`, `version`, `class:"read"`, `args_schema`, `result_schema`,
  `data_class`, `path_args` (which argument names are filesystem paths,
  for D9 workspace resolution) and `max_result_bytes`. The server only
  invokes an entry that matches on **name, version and sha256 of both
  schemas** with the node's advertised entry. A node that advertises a
  wider schema cannot get the server to send arguments outside its own
  contract, and the reverse holds too.
- **The eight v1 read capabilities.** All are `class: read` (ADR-014).

  | Capability | Args | Result (bounded) | `data_class` | Workspace resolved from |
  |---|---|---|---|---|
  | `claude.sessions.list` | `since_hours` (1–168), `limit` (≤ 50), `cwd_prefixes` (optional) | `[{session_id, cwd, started_at, last_activity_at, message_count, model, status_hint}]`; no text | firm | `cwd` per row |
  | `claude.sessions.read` | `session_id`, `last_turns` (1–20) | `[{role (user/assistant), at, text ≤ 2000}]`; tool calls and results as counts only | personal | the session's `cwd` |
  | `codex.threads.list` | as `claude.sessions.list` | as above (`thread_id`) | firm | `cwd` per row |
  | `codex.threads.read` | `thread_id`, `last_turns` (1–20) | as `claude.sessions.read` | personal | the thread's `cwd` |
  | `usage.ccusage` | `period` (`today`/`week`/`month`), `cwd_prefixes` (optional) | `{total_usd, tokens_in, tokens_out, by_project:[{cwd, usd, tokens_in, tokens_out}]}`; the server discards the node's totals and recomputes them (D9) | firm | `cwd` per row |
  | `git.status` | `repo_path` | `{branch, ahead, behind, dirty_files_count, untracked_count, last_commit_sha, last_commit_at}`; never file names or diffs | firm | `repo_path` |
  | `apps.frontmost` | none | `{bundle_id, app_name}` | personal | none |
  | `windows.list` | `limit` (≤ 30) | `[{bundle_id, app_name, window_count, on_screen}]`; titles only if the owner enabled them locally (D13) | personal | none |

  These eight wire capabilities are the ADR-014 read list. They group
  into five families (`claude`, `codex`, `usage`, `git`,
  `apps`/`windows`). D9 exposes them through five read tools, grouped
  by what a turn asks rather than by family.

  `cwd_prefixes` (optional on the three per-row lists, ≤ 20 path
  prefixes, absolute or `~/`-anchored, each ≤ 1024 chars) asks the node
  to return only rows whose `cwd` lies under one of them. `tools` fills it from the
  conversation workspace's 8A `path_prefix` rules (D9), so another
  workspace's rows are filtered at the source and never transit the
  server. Prefixes are sent in `normalise_path` form (`~` kept as the
  home marker, mapping.py:39); the node matches a row when its `cwd`,
  normalised the same way, equals a prefix or starts with prefix + `/`
  (the `_path_matches` rule, segment boundaries only), and checks both
  the absolute and the `~/` form of its own home. It is a narrowing
  hint, not a trust boundary: the server's row filter (D9) still runs
  on every result. For `usage.ccusage` the node's totals then cover the
  matching projects only, but the server never trusts them: it
  recomputes every aggregate from the rows that survive its own filter
  (D9, "Aggregates"). An empty list means "the workspace has no
  rules", so `is_available` is already false and no call is made.
- **`invoke_read(*, node, capability, args, workspace, actor, trace_id)
  -> ReadOutcome`** runs these steps:
  1. Check the snapshot entry is invocable.
  2. Validate `args` against the server contract.
  3. Write a pending in-memory record (nothing in the DB).
  4. Sign and push.
  5. Wait.
  6. Validate the result against `result_schema`.
  7. Write exactly one `NodeInvocation` chain row (D8) at the terminal
     state (the closed set below).
  8. Write one `nodes.invoke` `AuditLog` row with the capability, args
     hash, nonce, status, duration, workspace and trace id. It never
     contains args or output content.

  It returns the validated output to the caller and **stores none of
  it**. The only place output exists outside the caller's memory is the
  Redis result key in transit (D6): ephemeral, with a 30 s TTL, and
  deleted by the caller right after its `BLPOP`. It is never written to
  Postgres, logs or the audit tables.
- **`refuse_read(*, node, capability, args, workspace, actor, trace_id,
  code) -> ReadOutcome`** is the second entry point. `tools` calls it
  when its own pre-invoke check fails (D9: `path_not_in_workspace`,
  `workspace_not_allowed`, `session_not_found`). It signs and sends
  nothing, and writes the same chain row and `AuditLog` row as step 7–8.
  `tools` never writes either table itself.
- **Terminal statuses (closed set).** `status` and `refusal_code` are
  two columns; the pair below is the whole vocabulary, pinned by a test.

  | `status` | `refusal_code` | Where it ends | Anything signed or sent? |
  |---|---|---|---|
  | `ok` | — | node answered, result valid | yes |
  | `refused` | a D5 node code (`replay`, `not_granted`, …) | node refused | yes |
  | `error` | `node_error` / `result_schema` / `result_too_large` | node error frame, or step 6 failed | yes |
  | `timeout` | — | no result within `NODE_INVOKE_TIMEOUT_SECONDS` | yes |
  | `node_disconnected` | — | socket dropped in flight | yes |
  | `server_refused` | `not_invocable` / `args_contract` / `signing_unavailable` | `invoke_read` steps 1–2, or no usable signing key | no |
  | `server_refused` | `path_not_in_workspace` / `workspace_not_allowed` / `session_not_found` | `tools` pre-check via `refuse_read` (D9) | no |
  | `node_offline` | — | no presence key | no |
  | `busy` | — | `NODE_MAX_INFLIGHT` reached | no |
  | `bus_unavailable` | — | Redis down | no |

  **Every call that reaches `invoke_read` or `refuse_read` writes
  exactly one chain row and one `AuditLog` row**, including the
  not-sent statuses. A row with nothing sent has `nonce = ""` (the
  empty string, hashed as such), `output_sha256 = ""`, `output_bytes =
  0` and the `args_sha256` of the args as given (args are hashed, never
  stored, so even an out-of-contract args object is safe to hash). A
  tool call stopped earlier by the tools registry (wrong channel,
  `is_available` false) never reaches `nodes` and writes no chain row;
  that is not a node call.
- **A `*.read` is two node calls and two chain rows**, sharing one
  `trace_id`: the `*.list` lookup (D9), then the read itself. When the
  lookup decides the read must not happen, the second row is a
  `server_refused` row (`path_not_in_workspace` or
  `session_not_found`) and nothing is sent for it. When the lookup itself
  ends in a non-`ok` status, the read is not attempted and only the
  lookup's row exists.

### D8 — Hash-chained invocation audit

- `NodeInvocation` (`WorkspaceScopedModel`, the target workspace) has
  these fields:
  - `node` (FK), `seq` (installation-wide, unique);
  - `capability`, `capability_version`, `args_sha256`, `nonce`;
  - `status`, `refusal_code`, `output_sha256`, `output_bytes`,
    `truncated`;
  - `duration_ms`, `trace_id`, `actor` (FK, null for system),
    `channel`;
  - `prev_hash`, `row_hash`.

  `row_hash` is sha256 of the canonical JSON of every other field, with
  `prev_hash` included. Foreign keys enter the hash as their id strings
  (`workspace_id`, `node_id`, `actor_id`), never as related objects. The
  genesis `prev_hash` is 64 zeros.
- **Deletion policy (no SET_NULL, no CASCADE).** An append-only, hashed
  row must never be touched by a cascade, so every FK on
  `NodeInvocation` is `on_delete=PROTECT`:
  - `workspace` overrides `WorkspaceScopedModel`'s `CASCADE` with
    `PROTECT` (an abstract parent's field may be redeclared). The column
    stays `workspace_id`, so `for_actor()` and rule 1 are unchanged.
  - `node` is `PROTECT`. Nodes are never deleted: revocation flips the
    `TrustedDevice` row (D1), and `Node` rows stay as the chain's
    subject. `Node.device` (one-to-one to its `TrustedDevice`) is
    likewise `PROTECT`.
  - `actor` is `PROTECT` and nullable (null only for system calls), not
    `SET_NULL`: a `SET_NULL` would be an UPDATE, which the trigger
    refuses and which would also break `row_hash`.

  Consequences, stated so that no implementer has to guess:
  - **Workspace deletion or purge** is refused while chain rows exist
    (`ProtectedError`, and the trigger as the backstop). Whole-workspace
    deletion is already gate-class and unimplemented (ADR-009, "a whole
    workspace (firm deletion/archival)"); whichever later phase builds
    it must define a chain-aware archival (for example export plus a
    signed tombstone row) in its own ADR. Phase 9 adds no tombstone
    path, and the trigger has no exception.
  - **Owner trash and purge (ADR-009)** cover tasks, knowledge and
    memory rows. `NodeInvocation` is not trashable and holds no
    content, so ADR-009 never reaches it.
  - **User deletion** is refused while the user is an actor or node
    owner on chain rows. This matches today's `AuditLog`, whose
    `SET_NULL` actor FK already makes a user delete fail on its
    append-only trigger (audit migration 0002); Friday deactivates users
    (`is_active=False`) and never deletes them. Phase 9 does not change
    `AuditLog`.
- **Writing.** Rows are written under `select_for_update` on a
  single-row `NodeAuditHead`, which serializes the chain. They are
  append-only through a DB trigger, the same pattern as audit migration
  0002.
- **Verification.** `manage.py verify_node_audit_chain` walks the chain
  and exits non-zero at the first break, printing its `seq`. Preflight
  runs it (closeout).
- **Why a table in `nodes`.** ADR-013 asks for hash-chaining, which
  `AuditLog` does not have. Adding a chain to every `AuditLog` row would
  serialize all audit writes installation-wide. The chain therefore
  covers the high-risk stream only, and `AuditLog` keeps its role as the
  general trail, with one row per invoke too.

### D9 — Read tools, workspace resolution, scrubbing and fencing

- `tools/node_reads.py` registers five `ReadToolSpec`s:
  - `node_sessions` covers both list capabilities;
  - `node_session_detail` covers both read capabilities;
  - `node_usage`;
  - `node_git_status`;
  - `node_desktop` covers `apps.frontmost` and `windows.list`.

  Each spec's `data_class` is the strictest of its capabilities, so
  `node_session_detail` and `node_desktop` are `personal`. A personal
  read marks the conversation private, which routes the chat to
  `personal_data_allowed` providers only (ADR-010 / Phase 5 D5).
- **Channels: `CHANNEL_WEB` only**, which includes voice turns on the
  web channel (ADR-015 decision 1 allows reads). The reason is the same
  as for `coding_sessions_status`: results name firm projects, and the
  low-trust channels must not enumerate the owner's Mac.
- **Availability.** `is_available(workspace)` is true only when all of
  these hold:
  - the owner has a node with a live presence key;
  - the capability is invocable in its latest snapshot;
  - for the per-row-resolved capabilities, at least one 8A path rule
    targets this workspace.
- **Workspace resolution.**
  - Paths are matched with the 8A `path_prefix` rules of the
    `claude_code_hooks` `IngestionSource`. No second path registry is
    created: the owner already maintains these for the hook relay. The
    lookup goes through a new memory helper,
    `memory.ingestion.mapping.workspace_for_path(owner, path) ->
    Workspace | None`. `tools` may import `memory`.
  - For a capability with a path argument (`git.status`), the path must
    resolve to the conversation's workspace, otherwise the call is
    refused **before** invoking: `tools` calls
    `nodes.services.refuse_read(code="path_not_in_workspace")`, which
    writes the chain row and sends nothing (D7).
  - For `*.read`, the server first runs the matching `*.list` (with
    `cwd_prefixes`) to learn the session's `cwd`, then applies the same
    rule. That is two node calls and two chain rows (D7). A session
    absent from the lookup is refused `session_not_found`; one whose
    `cwd` maps elsewhere is refused `path_not_in_workspace`.
  - Every list call carries `cwd_prefixes`: the `path_prefix` values of
    the rules that target the conversation's workspace, via a new memory
    helper `memory.ingestion.mapping.prefixes_for_workspace(owner,
    workspace) -> list[str]`. The node filters at the source (D13).
  - List results are then filtered row by row on the server anyway.
    Unmapped rows and rows of another workspace (from a node that
    ignored or mis-applied `cwd_prefixes`) are dropped and counted,
    never shown. Each surviving row's `cwd` must resolve, through 8A
    `resolve_workspace` (longest prefix wins), to the conversation's
    workspace, so a nested rule of another workspace (W = `~/work`,
    X = `~/work/secret`) removes the X rows even though they lie under a
    W prefix.
  - **Aggregates.** Any node-supplied aggregate over rows that this
    filter applies to is discarded, never shown or passed on. For
    `usage.ccusage` the server drops the node's `total_usd`, `tokens_in`
    and `tokens_out` and recomputes all three as sums over the
    surviving `by_project` rows (hence per-project tokens in the D7
    schema). The shown totals therefore always equal the rows shown,
    and cannot include another workspace's spend even when the node
    ignores `cwd_prefixes` or the rules nest. Every future capability
    with a row-filtered result follows the same rule: aggregates are
    derived server-side after filtering, never taken from the node.
  - `apps.frontmost` and `windows.list` have no path. They are personal
    and allowed only in the owner's personal workspace; elsewhere `tools`
    calls `refuse_read(code="workspace_not_allowed")`.
- **Second scrub.** Every string in a result passes through
  `memory.ingestion.scrubber.scrub` with the target workspace's
  `ScrubTerm`s before it is fenced. The node scrubs first (D13 item 6:
  the reference node with a vendored copy of the same module, branch 3;
  the Swift app with a port proven against shared vectors, M2), so this
  is defence in depth, as in 8A D3. Only the server applies workspace
  `ScrubTerm`s; the node never receives them.
- **Fencing.** Output is rendered as
  `<connector_result source="friday-node">`, with `neutralize_fence` and
  `strip_invisible_formatting`, capped at 8 000 characters per tool
  result (truncation is marked).
- **Prompt injection.** Transcripts are attacker-reachable text. They
  are fenced, never quotable as provenance (8A D9), and never stored.
- **No separate summarisation call** (Q1). The chat model answers from
  the fenced results inside the normal `chat` call. Rule 3 is unchanged:
  one `ModelUsage` row for that call.

> **Amendment to D7 and D9 (Phase 8B branch 8, recorded 2026-11-20).**
> Phase 8B D13 adds a system-initiated read path (the transcript pull,
> `tools/transcript_ingest.py`) that D7/D9 did not define:
> - `invoke_read` and `refuse_read` gain a keyword `channel` (default
>   `CHANNEL_WEB` for the chat tool path). The pull passes `actor=<the
>   node's owner>` and `channel=CHANNEL_SYSTEM`; the chain row's `channel`
>   and the `AuditLog` metadata record `system: true` and the job name
>   `transcript_pull`. A null actor is never used.
> - **Step 0, the actor check**, before anything is signed: for a
>   capability whose contract has `data_class: personal`, the actor must be
>   non-null, active, the node's owner and an owner of the target
>   workspace; otherwise `server_refused:actor_not_owner` (nothing sent, one
>   chain row and one `AuditLog` row). The D7 `(status, refusal_code)`
>   vocabulary gains `server_refused` / `actor_not_owner`.
> - `channel=CHANNEL_SYSTEM` is accepted only for `SYSTEM_READ_CAPABILITIES`
>   (`claude.transcripts.owner_turns`, `codex.transcripts.owner_turns`);
>   any other capability is `server_refused:not_invocable`. The capability
>   allowlist limits capabilities, the import contract ("only tools, hud,
>   ops may import nodes") plus a source test limits callers.
> - The contract set gains the two owner-turn capabilities (8B D13), so
>   the "eight v1 read capabilities" test becomes ten.
>
> **State on 2026-11-20:** Phase 9 branches 3–5 (`invoke_read` itself) had
> not merged, so 8B branch 8 implements the same checks as
> `tools.transcript_ingest.refusal_for` (with its tests) behind a
> `NodeReadPort` seam and stages the two contracts in
> `backend/integrations/node_reference/contracts/v1/`. Branch 5 (or a
> follow-up once it merges) moves the checks into `nodes.services`, the
> contracts into `nodes/capabilities/v1/`, and adds the `nodes`-backed port
> (8B brief, "Owner steps (pending) — `feat/p8-transcript-ingestion`").

### D10 — Node events: the hook relay path into memory

- **The `event` sink.** An `event` of kind `claude_code.hook` carries a
  batch in the **same per-event schema as 8A's ingest endpoint**: the
  allow-listed names `SessionStart`, `Notification`, `Stop` and
  `SessionEnd`, and the same fields, sizes and event-id shape.
- **Hand-off.** The gateway hands the batch to a sink registered at
  start-up by the composition root:
  `config/asgi_nodes.py` calls
  `nodes.events.register_sink("claude_code.hook",
  memory.ingestion.hooks.ingest_hook_events)`.
  `config` may import everything, and this follows the precedent of
  `config/urls.py` injecting authenticators. The sink is called with
  `user=node.owner` and `bound_workspace_id=node.workspace_id` (the
  owner's personal workspace). That is the same principal binding as
  the `hook` PAT. The 8A mapping rules alone decide each event's
  workspace.
- **Dedupe across relays.** `friday-hook` and the node use the same
  event id (`external_id`), so an event relayed by both is stored once
  (`duplicate`). The owner can run both during the switch-over.
- **Spool upload.**
  - After reconnect, events with `spooled=true` arrive in order, at most
    one batch in flight.
  - The server answers each with `event.ack` (counts only). The node
    deletes a batch only after its ack.
  - 8A's `MAX_EVENT_AGE` (24 h) already drops stale events server-side,
    which matches the node spool's 24 h cap.
  - Spooled batches are throttled to 10 batches/s per node.
- **Events never cause commands.** An `event` is never answered with
  anything but `event.ack`. There is no path from `event` to `invoke`.
- **`grant.availability_changed`** updates the snapshot's availability
  flags only, as read-only data, and refreshes the HUD.
- **No sink registered** (for example a test settings module): the event
  is acked `dropped`. It is never stored elsewhere.

### D11 — HUD health panel and node management UI

- `hud.services.health_section` gains a `node` block:
  - `status`: `connected`, `disconnected`, `kill_switch`, `revoked`,
    `credential_expiring` or `never_paired`;
  - `last_heartbeat_at`, `catalogue_sha256` (short form),
    `invocable_count` / `advertised_count`;
  - `spool_depth` (from the last heartbeat);
  - `node_version`.

  It reads through `nodes.selectors.node_health(owner)`. The block holds
  no paths and no content.
- **API endpoints** (`nodes/urls.py`). All are owner-only and web
  channel only (`IsWebChannel`, `not is_agent`), use default
  authentication only, and go through `for_actor()`.
  - `GET /api/v1/nodes`: list.
  - `GET /api/v1/nodes/<id>`: detail with the snapshot entries and
    their `not_invocable` reasons.
  - `GET /api/v1/nodes/<id>/invocations?cursor=`: the last 50 chain
    rows of workspaces the actor can see, with args hash, status,
    duration and workspace name, never content. Audited
    `nodes.invocations.viewed`.
  - `POST /api/v1/nodes/<id>/revoke`: delegates to `revoke_device`.

  Pairing codes and the kill switch use the `accounts` endpoints (D1).
- **Frontend.**
  - `/devices` gains a "Mac düğümleri" section:
    - pair a Mac: issue a node code, shown once with its TTL;
    - the list with status and last heartbeat;
    - capability availability (read-only text such as "Mac'te izin
      verilmedi". No toggle: policy is local, D2/ADR-013 decision 5);
    - credential age;
    - revoke, with a confirmation naming the node;
    - the kill-switch toggle, with a confirmation.
  - `/devices/nodes/[nodeId]` shows the invocation log.
  - The HUD health panel shows the node line.
  - Strings are Turkish (`tr.ts`).

### D12 — Offline and degraded behaviour (server side)

| Failure | Server behaviour |
|---|---|
| Node offline | Read tools are unavailable (`is_available` false), so the router never offers them. A direct call fails `node_offline` with a Turkish message ("Mac'e şu an ulaşılamıyor"). Nothing is queued |
| Gateway process down | Same as node offline. Preflight `node_gateway` fails. The HUD shows `disconnected` |
| Redis down | Invoke `bus_unavailable`. The gateway refuses handshakes (1013). Revocation still takes effect at the next heartbeat re-check |
| Signing key missing or invalid | Pair and invoke refuse. Preflight fails. Chat continues without node tools |
| Node revoked or kill switch engaged | `bye` within 2 s via pub/sub, ≤ 15 s via heartbeat re-check. Authentication refused until re-pair or release |

### D13 — What the node side must do (the contract the Mac session implements)

This is normative for the Swift app. Branch 3's reference node implements
the protocol half of it in Python.

1. **Connection.** Outbound-only WSS to `gateway_path`, with subprotocol
   `friday-node.v1`, `Authorization: Bearer <secret>`, no `Origin`, and
   the TLS pin enforced. The app has **no TCP listener of any kind**.
2. **Session behaviour.**
   - `hello` within 10 s of connecting.
   - A heartbeat every 15 s.
   - Reconnect with exponential backoff (1 s → 60 s, full jitter).
   - Rotate the credential after `rotate_after_days`.
   - A `bye` reason `revoked`, `kill_switch` or `credential_expired`
     stops reconnecting until the owner acts locally.
3. **Verification.** Run the D5 order exactly, and keep the nonce cache.
4. **Fail closed.**
   - When the socket is lost, in-flight reads are abandoned (no late
     `result`), and nothing runs offline except the hook spool.
   - Local confirmation windows do not exist in Phase 9. The rule ("close
     on disconnect") is implemented in Phase 10.
5. **Local-only policy.**
   - Per-directory grants for `~/.claude/projects` and `~/.codex/sessions`
     are off by default and switched on behind LocalAuthentication
     (Touch ID or password).
   - The `git.status` repo list is node-local.
   - The `windows.list` titles toggle is off by default. It needs the
     Screen Recording TCC grant.
   - None of these is reachable from the protocol.
6. **Readers.** Bounded, read-only parsers of the Claude Code JSONL
   transcripts and the Codex rollout JSONL. The output schemas follow D7.
   Tool results and file contents are never returned. Rows outside the
   invoke's `cwd_prefixes` are skipped before parsing. Every returned
   string is scrubbed with the 8A rules (no workspace terms):
   - the reference node uses `nodes/reference/_scrubber_vendored.py`, a
     generated, byte-identical copy of `memory/ingestion/scrubber.py`
     (the `integrations/cli` pattern; branch 3), so the purity and
     import contracts hold;
   - the Swift app uses a port that must reproduce
     `backend/nodes/conformance/vectors/scrub.json` exactly and pass the
     corpus (M2).
7. **Hook relay.**
   - `friday-node hook` writes stdin to a user-owned Unix domain socket
     (mode 0600, in a 0700 directory), and the node checks the peer uid
     (`getpeereid`/`LOCAL_PEERCRED`).
   - Spool: ≤ 10 MB, ≤ 24 h, mode 0600, events only.
   - The CLI exits 0 within 3 s on any error, like `friday-hook`.
8. **No model calls and no provider keys.** Logs are local, scrubbed and
   rotated, and never uploaded (ADR-013 decision 12).
9. **Mic indicator.** The menu-bar icon reserves the ADR-015 decision 2
   mic state. In Phase 9 it always shows "off": the node requests no
   microphone entitlement until Phase 11.

## ADR

ADR-013 (Accepted 2026-11-20) is the decision record. This brief adds no
new ADR. Branch 1 appends "Implementation notes — spike S9.0" (the pass
criteria and their test names). Branch 2 appends the contract and
dependency notes (D2, D3). The closeout completes them. Nothing moves
between gate-class and confirm-class, so ADR-014 is untouched until
Phase 10.

## Scope (one branch each)

### 1. `feat/p9-node-pairing` — spike S9.0: `kind=node` device trust

**Scope.** D1:
- `TrustedDevice.kind` and `DevicePairingCode.device_kind`;
- the `jnd_` secrets;
- the `enroll_node_device`, `authenticate_node_secret`,
  `rotate_node_secret`, `engage_node_kill_switch` and
  `release_node_kill_switch` services;
- `NodeCredentialAuthentication` and `CHANNEL_NODE`;
- `core/revocation.py` (`publish_device_revoked`, best-effort Redis
  `PUBLISH`, never raises);
- the rotate endpoint;
- the kill-switch model, endpoints and command;
- `issue_pairing_code --node`;
- the refusals of node devices in `start_device_session`,
  `DeviceJWTAuthentication` and `enroll_device`;
- the existing devices API and serializer exposing `kind`, and
  `DevicesPanel` showing a "Mac düğümü" badge (the revoke button already
  works);
- settings and `.env.example` names;
- the ADR-013 spike note.

**Files.**
- `backend/accounts/{models,devices,authentication,serializers,views,urls}.py`
  plus a migration;
- `backend/accounts/management/commands/{issue_pairing_code,node_kill_switch}.py`;
- `backend/core/{channels,revocation}.py`;
- `backend/config/settings/base.py`, `.env.example`;
- `backend/accounts/tests/`, `backend/config/tests/test_node_credential_surfaces.py`;
- `frontend/src/components/integrations/DevicesPanel.tsx` and its test,
  `frontend/src/lib/i18n/tr.ts`;
- `docs/adr/ADR-013-friday-node-protocol.md` (note).

**Tests.**
- **Pairing:** a node code pairs a node; a browser code is refused for a
  node and a node code is refused for a browser; PIN LAN gating and
  attempt burning are unchanged for node codes; single use under
  concurrency is kept.
- **Credential:** a node secret is refused by `start_device_session`.
  A node device row with a forged JWT carrying its `did` is refused by
  `DeviceJWTAuthentication`.
- **Rotation:**
  - returns a new secret once;
  - old secret valid within the grace, invalid after first use of the
    new one, invalid after the grace;
  - rotation using the previous secret is refused;
  - audited.
- **Age cap and idle expiry:** with a frozen clock.
- **Revocation:** revoking one node, then `revoke_all_devices`, leads to
  authentication failure. `revoke_all_devices` counts nodes (pins
  "covers nodes"). `publish_device_revoked` is called once per revoked
  device (mocked Redis) with both `device_id` and `user_id` in the
  payload, and a Redis error never fails the revoke.
- **Kill switch:**
  - engaged means authentication fails and pairing is refused;
  - released restores it without re-pairing;
  - engaging publishes `{"device_id": "*", "user_id": <owner id>}`
    (payload asserted on mocked Redis), and owner A's kill switch leaves
    owner B's node authenticating;
  - the endpoints are web channel, owner and human only;
  - the command prints the state;
  - audited.
- **URL walk** (`test_node_credential_surfaces.py`, modelled on
  `test_hook_token_surfaces.py`): with a node secret, every route
  except the rotate endpoint returns 401 or 403.
- **Role × endpoint** for rotate and kill switch, against the five
  Phase 4 principals plus a `hook` PAT and a node secret: owner web
  session; owner `cli`/`openai` PAT; non-owner; `is_agent`; anonymous.
  Rotate accepts only a node secret. The kill switch accepts only the
  owner web session.
- **Vitest:** the badge, and revoke wording for nodes.

**Done when:** the six spike criteria (D1) pass in CI and the ADR-013
note records "spike S9.0 server half: passed". The Keychain half is
pending owner evaluation (M1).

### 2. `feat/p9-node-protocol` — `nodes` app, protocol v1, pairing endpoint, audit chain

**Scope.** D2, D4, D5, D7's contract files, D8:
- the `nodes` app: models `Node`, `CapabilitySnapshot`,
  `NodeInvocation`, `NodeAuditHead`, the append-only trigger migration;
- `nodes.protocol`:
  - schemas v1;
  - `canonical.py`, `signing.py` (Ed25519 sign/verify, key id), and
    `messages.py` (the frozen type registry, `validate`);
  - test vectors;
- `nodes/capabilities/v1/*.json` for the eight wire capabilities;
- `POST /api/v1/nodes/pair` with throttle `node_pair`;
- `NODE_SIGNING_KEY` loading and validation, and the
  `gen-secrets.sh` generator;
- `verify_node_audit_chain` and the chain writer service
  `nodes.audit_chain.append`;
- all import contracts (D2);
- dependency promotion of `cryptography` and `jsonschema`, with their
  manifest entry;
- the ADR-013 contract note.

There is no socket yet.

**Files.**
- `backend/nodes/{__init__,apps,models,admin,services,audit_chain,views,urls,serializers}.py`
  plus migrations;
- `backend/nodes/protocol/{__init__,canonical,signing,messages}.py`,
  `backend/nodes/protocol/schemas/v1/`,
  `backend/nodes/protocol/vectors/`;
- `backend/nodes/capabilities/v1/`;
- `backend/nodes/management/commands/verify_node_audit_chain.py`,
  `backend/nodes/tests/`;
- `backend/pyproject.toml`, `backend/uv.lock`,
  `backend/config/settings/{base,test}.py`, `backend/config/urls.py`;
- `deploy/docker/gen-secrets.sh`, `.env.example`;
- `docs/mcp/dependencies/node-gateway.md`;
- `docs/adr/ADR-013-friday-node-protocol.md` (note).

**Tests.**
- **Canonical JSON:** vectors, including key order by UTF-16, escapes
  and non-ASCII; floats refused.
- **Signing:**
  - sign/verify round trip;
  - a flipped bit, a wrong key, and a `sig` field inside the signed
    body each fail;
  - `key_id` derivation is stable;
  - retired keys verify but never sign.
- **Message registry:**
  - the exact server→node set;
  - the policy-mutation regex over every type name and every schema
    property (D5);
  - every schema has `additionalProperties: false` and bounds on every
    string and array (a meta-test walks the schemas).
- **Pairing endpoint:**
  - success returns the secret and keys once;
  - a browser code is refused;
  - kill switch engaged is refused;
  - throttle returns 429, and Redis down returns 503;
  - a missing signing key returns 503 and pairs nothing;
  - the response is never logged;
  - role × endpoint: anonymous allowed only with a valid code, and
    every other principal is not given special treatment.
- **Audit chain:**
  - append under concurrency (two threads) gives a contiguous `seq`
    and a valid chain;
  - UPDATE and DELETE are refused by the trigger;
  - deletion policy (D8): deleting the row's workspace, node,
    `TrustedDevice` or actor user raises `ProtectedError`, no chain row
    changes, and `verify_node_audit_chain` still passes afterwards; a
    raw-SQL `DELETE FROM workspaces_workspace` for that workspace is
    refused by the FK constraint (at commit: Django FK constraints are
    deferred), and a raw `UPDATE … SET actor_id = NULL` by the
    trigger;
  - the verify command detects a tampered row (trigger disabled inside
    the test) and reports its `seq`.
- **Workspace scoping:** `NodeInvocation.objects.for_actor()` isolation
  between the personal and a firm workspace.
- **Purity:** `nodes.protocol` imports without Django (subprocess test).
- **Contracts** green, including the "only tools, hud, ops may import
  nodes" contract.

**Done when:** a test pairs a node through the endpoint, and a signed
`invoke` built by `nodes.protocol` verifies with the returned key and
fails with any other key.

### 3. `feat/p9-reference-node` — Python reference node and the conformance suite

**Scope.** D5's node-side verification and D13 items 1–4, 7 (protocol
half) as executable spec:
- `nodes.reference`, which is pure Python using `nodes.protocol`,
  `cryptography`, `jsonschema` and `wsproto`:
  - `ReferenceNode` (catalogue from a fixture, verifier, nonce cache,
    fake readers over fixture directories, a bounded spool with the
    D13 limits);
  - a `wsproto` client transport, an in-process transport for tests,
    and backoff;
  - a Unix-socket hook relay with a peer-uid check (`SO_PEERCRED` on
    Linux; the Swift app uses `LOCAL_PEERCRED`) as the reference for M3;
  - the node-side scrub (D13 item 6). **Decision:** vendor, do not
    import. `nodes/reference/_scrubber_vendored.py` is generated from
    `memory/ingestion/scrubber.py` (stdlib-only today) by
    `nodes/reference/vendor_scrubber.py`, which reads and writes text
    and imports nothing from the project, exactly like
    `integrations/cli/vendor_scrubber.py`. The fake readers scrub every
    returned string with it and honour `cwd_prefixes`.
- `nodes/conformance/`:
  - case definitions in Python, and generated case files
    `cases/v1/*.json` (each case lists server frames, the expected node
    frames and refusal codes, and asserts that no reader ran);
  - `manage.py build_node_conformance [--check]`, so the files cannot
    drift from the definitions (CI step). It also writes
    `nodes/conformance/vectors/scrub.json`: every
    `memory/tests/scrubber_corpus/*.jsonl` item and every fixture string
    with its expected scrubbed output from the vendored scrubber. This
    is the parity file M2's Swift port must reproduce byte for byte;
  - a test-only server key pair `conformance/keys/` (clearly labelled,
    never accepted by a production server: settings refuse it);
  - `manage.py node_conformance_server`: a hostile fake gateway on
    localhost. It plays every case against a connecting node (the
    reference node here, the Swift app later) and prints a pass/fail
    table.

**Conformance cases** (the ADR-013 release gate):
- unknown capability; out-of-schema args (extra key, wrong type,
  oversize string, path traversal `../`, NUL, Unicode RTL override,
  shell metacharacters, over-length array);
- reused nonce; expired; not yet valid; TTL too long;
- bad signature; unknown `key_id`; signature over a different args
  hash; args changed after signing;
- wrong `node_id`; non-null `grant`; `class` ≠ `read`;
- capability present but `not_granted`;
- unknown message type; policy-mutation-shaped types (`policy.set`,
  `allowlist.add`, `catalogue.set`, `config.update`, `grant.add`);
- oversize frame; malformed JSON; three bad frames in 60 s → close;
- `bye{revoked}` → no reconnect;
- server silence > 45 s → reconnect with backoff;
- disconnect mid-invoke → no late result.

**Files.**
- `backend/nodes/reference/{__init__,node,transport,readers,spool,hook_relay,vendor_scrubber,_scrubber_vendored}.py`;
- `backend/nodes/conformance/vectors/scrub.json` (generated);
- `backend/nodes/reference/fixtures/` (synthetic Claude and Codex JSONL,
  with fake secrets for the scrub tests);
- `backend/nodes/conformance/{__init__,cases,runner}.py`,
  `backend/nodes/conformance/cases/v1/`,
  `backend/nodes/conformance/keys/`;
- `backend/nodes/management/commands/{build_node_conformance,node_conformance_server}.py`;
- `backend/nodes/tests/`;
- `backend/pyproject.toml` (`wsproto` direct, exact pin), `backend/uv.lock`;
- `.github/workflows/` (`build_node_conformance --check`);
- `docs/mcp/dependencies/node-gateway.md` (`wsproto` entry).

**Tests.**
- Every conformance case passes against the reference node in-process.
- The fake server run against the reference node over a real localhost
  socket (the `wsproto` client) shows all cases passing.
- `--check` fails on a changed definition.
- The production `NODE_SIGNING_KEY` validation refuses the conformance
  key.
- Spool: bounded at 10 MB and 24 h, mode 0600 (checked with
  `os.stat`), oldest dropped first, deleted only after ack.
- Hook relay: socket mode 0600; a connection with a different uid is
  refused (simulated by a test seam over the peer-credential call);
  oversized input is dropped.
- Readers never return tool-result text or file contents.
- Readers honour `cwd_prefixes`: rows outside them are never returned.
- **Scrub:**
  - `_scrubber_vendored.py` equals `vendor_scrubber.render()` (the
    byte-identity check, as `integrations/tests/test_friday_hook.py`),
    and `vendor_scrubber --check` exits 0;
  - the vendored copy reaches 100% secrets recall on
    `memory/tests/scrubber_corpus/` (the corpus files are read as data;
    nothing imports `memory`);
  - a reference-node read of the fixtures with fake secrets returns no
    raw secret value in any `result` frame;
  - `scrub.json` is current (`build_node_conformance --check`).
- Purity: no Django import (subprocess), including
  `_scrubber_vendored.py`.

**Done when:** `node_conformance_server` against the reference node
prints all-pass. The case files are what M1's Swift test target loads.

### 4. `feat/p9-node-gateway` — the WSS gateway and its process

**Scope.**
- D3: `config/asgi_nodes.py`, `/healthz`, uvicorn + `wsproto`, and the
  compose service `node-gateway` (127.0.0.1:8002, `config.settings.prod`
  in the daily stack, `restart: unless-stopped`, same hardening as web).
- D6: handshake, `hello` and snapshot, heartbeat and presence, the
  invoke bus consumer, the revocation and kill-switch subscriber,
  limits, logging.
- The `nodes.services.invoke_read` bus side, with no tool yet.
- The node APIs `GET /nodes`, `GET /nodes/<id>` and
  `POST /nodes/<id>/revoke` (D11, backend only).
- `docs/runbooks/friday-node.md` v0: running the gateway and exposing
  `/ws/node/v1` through the reverse proxy.

**Files.**
- `backend/config/asgi_nodes.py`;
- `backend/nodes/{gateway,bus,presence,selectors,services,views,urls}.py`,
  `backend/nodes/tests/`;
- `backend/pyproject.toml` (`uvicorn` direct, exact pin), `backend/uv.lock`;
- `deploy/docker/{compose.yml,compose.daily.yml}`: the new
  `node-gateway` service, and `NODE_SIGNING_KEY` (plus
  `NODE_SIGNING_KEYS_RETIRED`) passed to **web, worker and
  node-gateway**. `invoke_read` signs in whichever process runs it (a
  chat turn in web, a task in worker), so all three need the key; the
  gateway forwards envelopes others signed, and needs the key for its
  refuse-on-missing-key rule (D4) and the key id in `/healthz`;
- `backend/config/settings/base.py`, `.env.example`;
- `docs/runbooks/friday-node.md`;
- `docs/mcp/dependencies/node-gateway.md`.

**Tests.** These drive the ASGI app in-process with the reference node's
in-process transport, against real Redis (test DB index) and the test
Postgres.

- **WS role × endpoint matrix** (§c.6):
  - anonymous → 4401;
  - browser `jdv_` secret, `cli`/`openai`/`hook` PAT, or a JWT → 4401;
  - revoked node → 4401;
  - expired or idle node → 4401;
  - kill switch engaged → 4401;
  - a node of a **non-owner** user (created directly) → 4401;
  - `Origin` present → 4403;
  - wrong subprotocol → 4400;
  - no `hello` in 10 s → 4408.
- **`hello`:**
  - snapshot written only on a changed hash;
  - an unknown, mismatched or non-read entry is `not_invocable` with
    its reason;
  - `hello.ack` lists only the accepted entries.
- **Heartbeat:**
  - presence TTL refreshed;
  - three missed heartbeats → presence gone and socket closed;
  - revocation detected at the next heartbeat with pub/sub disabled.
- **Kill switch and revocation:** `bye` within 2 s with pub/sub. A
  second connection replaces the first. **Two owners:** with nodes of
  owner A and owner B connected (B's created directly), A's kill switch
  closes only A's sockets and B's stays connected; a `"*"` payload
  without `user_id` closes nothing.
- **Result routing:** node B sending a `result` with node A's in-flight
  `invocation_id` is discarded, A's caller still gets A's own result
  (or `timeout`), and nothing is pushed to A's result key by B; a
  `result` for an unknown id counts toward the frame-error close.
- **Invoke bus:**
  - round trip;
  - timeout → `timeout`, and the late result is discarded;
  - disconnect mid-call → `node_disconnected`, with no retry (an
    assertion on the reference node's received frames);
  - `NODE_MAX_INFLIGHT`+1 → `busy`;
  - no presence → `node_offline` immediately, with nothing pushed;
  - Redis down → `bus_unavailable`, and handshake 1013;
  - result keys are ephemeral: after a round trip the
    `nodes:result:<invocation_id>` key no longer exists (deleted after
    the `BLPOP`); a late result pushed after a timeout carries a TTL
    ≤ 30 s and has expired after it (asserted with `TTL` and a
    fake-clock/short-TTL setting).
- **Limits:** oversize frame, frame rate → 4429; logs carry no bodies
  (captured log assertion).
- **Node APIs:** role × endpoint (five principals plus node secret plus
  `hook` PAT); another user's node → 404; revoke via the API closes the
  socket.
- **Contracts** green. `asgi_nodes` is in `config`, so it may wire
  freely.

**Done when:** the reference node connects to the gateway in a test,
gets `hello.ack`, answers a signed `invoke`, and is cut off within 2 s
of `revoke_device`.

### 5. `feat/p9-node-reads` — read tools, workspace resolution, chain rows

**Scope.**
- D7: `invoke_read` complete, with result validation and the
  `NodeInvocation` row plus `AuditLog` row per call.
- D9: `tools/node_reads.py` with the five `ReadToolSpec`s,
  `memory.ingestion.mapping.workspace_for_path` and
  `prefixes_for_workspace`, `cwd_prefixes` on every list call, the
  pre-invoke refusals through `refuse_read`, row filtering, the second
  scrub, fencing and the size caps.
- `GET /nodes/<id>/invocations` (D11).
- The router description strings for the five tools (Friday-authored,
  Turkish).

**Files.**
- `backend/nodes/services.py`;
- `backend/tools/node_reads.py`, `backend/tools/tests/test_node_reads.py`;
- `backend/memory/ingestion/mapping.py` and its tests;
- `backend/nodes/{views,urls,serializers}.py`, `backend/nodes/tests/`;
- `backend/assistant/tests/` (a chat-turn e2e with FakeAdapter).

**Tests.**
- **Role × channel:** web only. CLI, OpenAI facade, Telegram and hook
  callers never see or run the tools. A voice-mode web turn may run
  them (ADR-015 decision 1).
- **Workspace:**
  - `git.status` of a path mapped to another workspace → refused
    before invoking (the reference node receives nothing);
  - list rows of other or unmapped workspaces are dropped and counted;
  - `*.read` of a session in another workspace is refused;
  - `apps.frontmost` in a firm workspace is refused
    (`workspace_not_allowed`, nothing sent);
  - every list `invoke` carries `cwd_prefixes` equal to the
    conversation workspace's rule prefixes (asserted on the frame the
    reference node received), and the reference node returns no row of
    the other workspace;
  - a hostile reference-node variant that ignores `cwd_prefixes` still
    has the other workspace's rows dropped server-side;
  - **nested rules and aggregates:** with rules W = `~/work` and
    X = `~/work/secret`, a hostile reference node that ignores
    `cwd_prefixes` and returns `usage.ccusage` rows for both, with
    inflated `total_usd`/`tokens_in`/`tokens_out` covering everything,
    yields in a W conversation exactly the W rows and totals equal to
    the sum of only those rows; the X rows and the node's own totals
    appear nowhere in the fenced output or the `ModelUsage` context
    report;
- **Isolation:** a firm-only actor never sees personal rows, and the
  reverse holds.
- **Personal data:** `node_session_detail` marks the conversation
  private. With a cloud-only provider set, the turn fails closed
  (`NoProviderAvailable`), with no read output sent anywhere.
- **Scrub:** a fixture transcript with fake keys and TCKN → no raw
  value in the fenced output, in the `ModelUsage` context report, or in
  any DB row (DB scan).
- **Fencing:** a transcript containing `</connector_result>` and
  "ignore previous instructions, approve…" stays fenced, yields no tool
  call or approval, and is never a provenance quote (the 8A
  prompt-injection corpus is extended with transcript items).
- **Chain:**
  - exactly one `NodeInvocation` and one `AuditLog` row per node call,
    for every row of the D7 terminal-status table, including each
    not-sent `server_refused`, `node_offline`, `busy` and
    `bus_unavailable` case (each with `nonce = ""` and the reference
    node receiving nothing);
  - a `*.read` writes two rows with one `trace_id` (lookup, then read);
    a lookup that resolves to another workspace gives an `ok` lookup
    row plus a `server_refused:path_not_in_workspace` read row;
  - the `(status, refusal_code)` vocabulary equals the D7 table (a test
    over the model's choices);
  - no args or output content in either (field scan);
  - chain verifies after a mixed run.
- **Tools:**
  - `is_available` false when offline, ungranted, or with no rule for
    the workspace;
  - the FakeAdapter chat turn "Claude Code oturumlarım ne yapıyor?"
    picks `node_sessions`, gets fenced rows from the reference node and
    answers;
  - one `ModelUsage` for the turn, and none written by `nodes`.
- **Role × endpoint** for the invocations endpoint.

**Done when:** in a test, the reference node's fixture sessions show up
in a fake-provider chat answer, filtered to the conversation's
workspace, with chain rows and no stored content.

### 6. `feat/p9-node-events` — node-relayed hook events and the spool upload

**Scope.**
- D10: `nodes/events.py` (sink registry, the `event` handler, `event.ack`,
  ordering of spooled batches, throttle);
- the registration in `config/asgi_nodes.py`;
- the `grant.availability_changed` handling;
- runbook section "Switching Claude Code hooks to the node", with
  `friday-hook` kept as the fallback (no change to `friday_hook.py`).

**Files.**
- `backend/nodes/{events,gateway}.py`, `backend/nodes/tests/`;
- `backend/config/asgi_nodes.py`;
- `backend/memory/tests/test_hooks_via_node.py`;
- `docs/runbooks/{friday-node,claude-code-hooks}.md`.

**Tests.**
- **Ingestion:**
  - a `claude_code.hook` batch from the reference node lands in
    `CodingSession` in the mapped workspace;
  - an unmapped `cwd` stores nothing;
  - the same event via `friday-hook`'s HTTP path and the node →
    `duplicate`, so it is stored once;
  - a batch of 51 → refused; an unknown event name → `ignored`;
  - an event older than 24 h → dropped.
- **Acks:** `event.ack` has counts only (asserted with a secret-bearing
  payload).
- **Spool upload:** a reconnect with spooled batches → in order, one in
  flight; the reference spool deletes only acked batches.
- **No reverse path:** no event ever leads to an `invoke` (bus spy).
- **No sink registered** → `dropped`, nothing stored.
- **Availability change** → the snapshot flags change and the HUD
  section changes.
- **Contracts:** `nodes` still imports no `memory` (lint-imports green).
  Only `config` wires the two together.

**Done when:** a hook event written to the reference relay's Unix socket
reaches `GET /memory/sessions` through the node, and replaying it through
`friday-hook` changes nothing.

### 7. `feat/p9-node-ui` — HUD node line, `/devices` node management

**Scope.**
- D11 frontend: the `/devices` "Mac düğümleri" section, `/devices/nodes/[nodeId]`
  (invocation log), and the BFF routes `app/api/nodes/*`.
- The HUD health panel node line: `hud.services` `node` block through
  `nodes.selectors.node_health`, and the contract edit `hud → nodes`.
- The kill-switch toggle and revoke dialogs.

**Files.**
- `frontend/src/app/(shell)/devices/`, `frontend/src/components/nodes/`,
  `frontend/src/components/hud/HudPanels.tsx`;
- `frontend/src/app/api/nodes/`, `frontend/src/lib/i18n/tr.ts`;
- `backend/hud/services.py`, `backend/hud/tests/`, `backend/pyproject.toml`.

**Tests.**
- **Vitest:**
  - node list states: connected, disconnected, `kill_switch`, revoked,
    expiring, never paired;
  - the pairing code is shown once and cleared;
  - the revoke confirmation names the node;
  - the kill-switch confirmation;
  - capability availability rendered as text with **no** toggle (a
    test asserts there is no input element in the capability list);
  - the invocation log shows hashes and status, never content;
  - HUD node line states.
- **Backend:**
  - the `node` block for each status;
  - no path or content in it;
  - the HUD section changes when presence flips.
- **Contrast** for new tokens. The layout works at phone width.

**Done when:** the owner can pair, inspect, kill-switch and revoke a
node, and see its link state in the HUD. In CI the node is the
reference node.

### 8. `chore/p9-closeout` — preflight, runbook, docs, go/no-go record

**Scope.**
- **`manage.py preflight` node checks:**

  | Check | What it verifies |
  |---|---|
  | `node_gateway` | `/healthz` reachable, Redis OK |
  | `node_signing_key` | Configured, valid, not the conformance key; key id matches what active nodes pinned at pairing (stored on `Node.pinned_key_id`); the same key id is configured in web, worker and node-gateway: preflight compares its own (web-side) key id with the one the gateway's `/healthz` reports and with the one a worker returns from a `nodes.signing_key_id` Celery task (a new fakeable preflight seam `worker_signing_key_id`, beside `celery_ping`); a missing key in any of the three fails |
  | `node_link` | Per active node: presence, last heartbeat age, catalogue version and short hash, invocable/advertised counts; **warn** if none paired, which is not a failure because the Mac session is deferred |
  | `node_tls_pin` | When `NODE_TLS_PIN_SHA256` is set, the served certificate's SPKI matches |
  | `node_audit_chain` | `verify_node_audit_chain` passes |
  | `node_conformance` | `build_node_conformance --check` passes |

- **E2E (CI, fakes):** pair the reference node through the endpoint →
  connect → `hello` → chat turn "Claude Code oturumlarım ne yapıyor?" →
  fenced rows in the answer → chain row → revoke → `bye` → the tool
  becomes unavailable.
- **Docs:**
  - `docs/runbooks/friday-node.md` complete: gateway, proxy and TLS pin,
    signing key generation and rollover (re-pair), pairing, kill
    switch, revocation, reading the audit chain, switching hooks;
  - the ADR-013 implementation notes completed;
  - MASTER_PLAN §AA row 9 status, with the server half done and the Mac
    session and go/no-go pending owner evaluation;
  - `self_sources.json` regenerated;
  - this brief's status table.
- **Go/no-go record** (below), with the Mac items marked pending owner
  evaluation.

**Files.**
- `backend/nodes/tasks.py` (the `nodes.signing_key_id` task: returns
  the key id only);
- `backend/nodes/management/commands/run_reference_node.py` (added at
  implementation: DEBUG-only, pairs the Python reference node and keeps it
  connected, so the done criterion can be checked before the Mac app
  exists), `backend/config/tests/test_reference_node_preflight.py`,
  `backend/config/tests/test_preflight_hud_nodes.py`;
- `backend/ops/preflight.py`, `backend/ops/tests/test_preflight_nodes.py`,
  `backend/ops/tests/test_node_e2e.py`;
- `docs/runbooks/friday-node.md`, `docs/adr/ADR-013-friday-node-protocol.md`;
- `docs/MASTER_PLAN.md`, `backend/assistant/self_sources.json`;
- `docs/prompts/phase-9-friday-node/brief.md`.

**Tests.**
- Every new preflight check passes, fails and warns with fakes:
  - unreachable gateway;
  - conformance key configured;
  - signing key id differing between web, worker and gateway, or
    missing in one of them;
  - mismatched pin;
  - broken chain;
  - stale case files.
- The HUD reads the new failed-check names.
- The e2e test passes.

**Done when:** preflight is green on the dev stack with the reference
node paired, the e2e test is green, and the go/no-go record is written.

## Branch table

| # | Branch | Scope | Depends on | Status |
|---|---|---|---|---|
| brief | `docs/p9-brief` | This brief (full, from the stub), MASTER_PLAN §AA row 9 Brief/Status, regenerated `self_sources.json` | Phase 8A closeout | **implemented 2026-11-20** (docs only; review round 1 findings addressed 2026-11-20 — D7 terminal-status table and `refuse_read`, D8 deletion policy, vendored node scrubber + `scrub.json` parity, `cwd_prefixes`, per-owner kill-switch payload, per-socket result routing, `CONFORMANCE` build-only mode; review round 2 findings addressed 2026-11-20 — server-recomputed `usage.ccusage` aggregates with per-project tokens and the nested-rule test, authenticator wording, `NODE_SIGNING_KEY` in web/worker/gateway + preflight, ephemeral TTL-bound Redis result keys; awaits re-review) |
| 1 | `feat/p9-node-pairing` | Spike S9.0: `TrustedDevice.kind=node`, node pairing codes, `jnd_` secrets, rotation with grace, age cap, revocation fan-out, kill switch, `CHANNEL_NODE`, URL walk, ADR-013 spike note | brief approved | **implemented 2026-11-20** (spike S9.0 server half: passed, ADR-013 note; Keychain half pending owner evaluation, M1; known follow-up 4 resolved: `is_usable()` branches on `kind`; awaits independent Sonnet 5 review) |
| 2 | `feat/p9-node-protocol` | `nodes` app + contracts, protocol v1 schemas, canonical JSON, Ed25519 envelopes, capability contracts, pairing endpoint, signing key config, hash-chained `NodeInvocation`, dependency manifest | 1 | **implemented 2026-11-20** (done criterion met: `test_the_done_criterion_signed_invoke_verifies_only_with_the_returned_key`; known follow-ups 1, 2 and 5 resolved; ADR-013 contract note; awaits independent Sonnet 5 review) |
| 3 | `feat/p9-reference-node` | Python reference node, conformance cases + `--check`, hostile fake gateway, reference spool and Unix-socket relay | 2 | **implemented 2026-11-20** (done criterion met: `node_conformance_server --protocol 1` prints all-pass, `test_every_v1_case_passes_through_the_command_over_a_socket`; builds on the session half, v1 protocol cases and vendored scrubber that `feat/p10-reference-executor` already shipped; adds the readers, 13 reader cases, `reader_calls`, the spool, the hook relay, node event upload and `vectors/scrub.json`; awaits independent Sonnet 5 review) |
| 4 | `feat/p9-node-gateway` | `asgi_nodes` + uvicorn/`wsproto`, `node-gateway` service, handshake, `hello`/snapshot, heartbeat/presence, invoke bus, revocation/kill-switch fan-out, limits, node APIs | 1, 2, 3 | **implemented 2026-11-20** (done criterion met: `test_the_done_criterion_round_trip` in process and `test_the_reference_node_over_a_real_socket` under uvicorn — `hello.ack`, a signed read answered, cut off within 2 s of `revoke_device`; `invoke_read` bus side with chain + AuditLog rows; known follow-up 3 resolved (Redis in-flight counter, two-process test); Phase 10 owner step 2 done (`NodeSession` wired into the socket app, 1013 placeholder replaced); awaits independent Sonnet 5 review) |
| 5 | `feat/p9-node-reads` | `invoke_read` end to end, five read tools, path → workspace resolution, second scrub, fencing, chain + audit rows, invocations API | 4 | **implemented 2026-11-20** (done criterion met: `config/tests/test_node_read_turns.py::test_the_done_criterion_sessions_answered_from_the_node` — the reference node's fixture sessions in a FakeAdapter chat answer, filtered to the conversation's workspace, two `ok` chain rows, no stored content; 8B step-0 actor check in `invoke_read`; the 8B owner-turn move is a known follow-up below; awaits independent Sonnet 5 review) |
| 6 | `feat/p9-node-events` | `event` sink wiring into `ingest_hook_events`, cross-relay dedupe, spool upload acks, availability events | 4 (parallel with 5) | **implemented 2026-11-20** (done criterion met: `config/tests/test_hooks_via_node.py::test_the_done_criterion` — an event written to the reference relay's Unix socket reaches `GET /memory/sessions` through the node, and a `friday-hook` replay is a `duplicate`; `nodes/events.py` sink registry + availability successor snapshots, spooled-batch pacing, `config.asgi_nodes` registration; awaits independent Sonnet 5 review) |
| 7 | `feat/p9-node-ui` | `/devices` node section, invocation log page, HUD node line, `hud → nodes` contract | 4, 5 (6 for availability) | **implemented 2026-11-20** (done criterion met server- and UI-side: pair (node code shown once), inspect (status, heartbeat, credential age, capability availability as text, invocation log), kill-switch and revoke behind confirmations on `/devices`; the HUD health panel's node line from `health.node` — `hud/tests/test_node_line.py`, `components/nodes/*.test.tsx`, `HudNodeLine.test.tsx`; the `hud → nodes` edge already existed (Phase 10 D12), only its comment changed; awaits independent Sonnet 5 review) |
| 8 | `chore/p9-closeout` | Preflight node checks, e2e, runbook, ADR-013 notes, MASTER_PLAN row, go/no-go record | 1–7 | **implemented 2026-11-20** (done criterion met in process: `config/tests/test_reference_node_preflight.py::test_preflight_is_green_with_the_reference_node_paired` — the reference node paired by the new `run_reference_node` command and connected to the real gateway process, every node check `pass` (TLS pin `warn`, not configured); e2e `ops/tests/test_node_e2e.py` green; go/no-go record written; preflight on the owner's real dev stack is an owner step below; awaits independent Sonnet 5 review) |
| M1 | `feat/p9-node-app` | Swift menu-bar app: signing/notarization, Keychain credential + TLS pin, pairing, WSS client, D5 verifier, conformance pass | 1–4 merged; owner's Apple Developer ID (d9) | deferred: Mac session |
| M2 | `feat/p9-node-readers` | Swift readers for the eight capabilities, per-directory Touch ID grants, local policy UI, scrubber port + corpus | M1 | deferred: Mac session |
| M3 | `feat/p9-node-hook-relay` | `friday-node hook` CLI, 0600 Unix socket + peer uid, spool, Claude Code hook switch-over | M1; 6 merged | deferred: Mac session |
| M4 | Mac evaluation | Go/no-go measurements on the real Mac (below) | M1–M3 | deferred: Mac session (pending owner evaluation) |

**Minimum usable cut** if the phase runs long: 1, 2, 3, 4, 5 and M1+M2.
That is enough to answer the question with local detail. 6 and M3 move
the hook relay, which `friday-hook` already covers. 7 is needed before
the owner can manage nodes without `manage.py`.

## Mac session (deferred)

These tasks are for a later Claude Code session **on the owner's
MacBook**. They are self-contained: a fresh session starts from this
section, the merged branches 1–7 and the files they name.

**Preconditions:**
- the owner's Apple Developer enrolment and a Developer ID Application
  identity in the login Keychain (d9);
- Xcode (current stable) and `notarytool` credentials stored as a
  Keychain profile (`xcrun notarytool store-credentials friday-notary`);
- a running Friday stack reachable over the owner's overlay or TLS
  proxy, with `node-gateway` exposed at `/ws/node/v1` and
  `NODE_SIGNING_KEY` set;
- the signing key never leaves the Mac, and CI never holds it.

**Location.** `node/friday-node/` at the repository root: a Swift
package with an app target, a CLI target (`friday-node`, which provides
`hook` and `scrub`) and a test target. It has no dependency outside
Apple frameworks unless one goes through the governance manifest
(`Package.resolved` committed, exact versions).

### M1 — `feat/p9-node-app`: shell, identity, session, verifier

**Create:**
- `node/friday-node/Package.swift`;
- `Sources/FridayNode/` (the app, with an `NSStatusItem` menu bar,
  `LSUIElement`);
- `Sources/NodeCore/`:
  - `Canonical.swift` (JCS subset);
  - `Envelope.swift`, `Verifier.swift`, `NonceCache.swift`;
  - `Session.swift` (`URLSessionWebSocketTask` with a pinning
    `URLSessionDelegate`, subprotocol `friday-node.v1`, no `Origin`);
  - `Backoff.swift`;
  - `Credential.swift` (Keychain `kSecClassGenericPassword`, service
    `friday-node`, `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`,
    no iCloud sync; holds the secret, the TLS SPKI pin and the pinned
    server keys);
  - `Pairing.swift` (calls `POST /api/v1/nodes/pair`);
  - `Rotation.swift` (calls `POST /api/v1/auth/nodes/credential/rotate`);
- `Tests/NodeCoreTests/`;
- `scripts/build-sign-notarize.sh` (`Release` configuration only;
  conformance-marker check on the binary, see contract test 2; then
  `codesign --options runtime --timestamp`, `notarytool submit --wait`,
  `stapler staple`);
- a `Conformance` build configuration (unsigned, debug, compilation
  condition `CONFORMANCE`) used only for contract test 2;
- entitlements: hardened runtime, `com.apple.security.network.client`
  only. No server entitlement, no microphone.

**Must satisfy:**
- **Canonical JSON:** `backend/nodes/protocol/vectors/canonical.json`.
  A test target loads the file from the repo path.
- **Message schemas:** `backend/nodes/protocol/schemas/v1/`, as strict
  Codable decoding that rejects unknown keys plus the bounds.
- **Verification:** the D5 order and refusal codes.
- **Behaviour:** D6 and D13 items 1–4 and 9.

**Server-side contract tests it must pass:**
1. **Offline conformance.** Every case file in
   `backend/nodes/conformance/cases/v1/*.json` is run by
   `Tests/NodeCoreTests/ConformanceTests.swift`. It must give the same
   verdicts and codes as the reference node, with no case skipped.
2. **Live conformance.** `uv run python manage.py node_conformance_server
   --port 8765 --tls-self-signed` on the Mac, with a **separate
   conformance build** of the app connected to it. It must print
   all-pass.
   - Conformance mode exists only in a distinct, unsigned debug build
     configuration (`Conformance`) that defines the Swift compilation
     condition `CONFORMANCE`. All code that pins the fake server's
     self-signed SPKI and the conformance key sits inside
     `#if CONFORMANCE … #endif`, and its inputs come from compile-time
     constants or a launch argument read only inside that block.
   - There is **no environment variable, user default or launch
     argument in the release build** that changes the TLS pin, the
     pinned signing keys or the server URL. A runtime switch in the
     notarized binary would be a backdoor that any local process or
     launch agent could flip, defeating ADR-013 decisions 2 and 4.
   - The conformance build is never signed with the Developer ID
     identity, never notarized and never installed as a login item.
   - `scripts/build-sign-notarize.sh` builds the `Release`
     configuration only and, before `codesign`, fails if the release
     binary contains any conformance marker: `strings` over the Mach-O
     must not find `FRIDAY_NODE_CONFORMANCE`, `conformance`, or the
     conformance public key's base64 (read from
     `backend/nodes/conformance/keys/`). A test
     (`Tests/NodeCoreTests/ReleaseHygieneTests.swift`, run in the
     `Release` configuration) asserts that the conformance pin type is
     not compiled in.
3. **Real stack.** Pair against the real stack with a node code from
   `/devices`, then check that:
   - `hello.ack` arrives;
   - `/devices` shows connected;
   - the kill switch closes the app's socket within 2 s;
   - the app stops reconnecting after `bye{kill_switch}` and resumes
     after release (owner action in the menu);
   - revoking leads to re-pair being required.
4. **No listener.** `lsof -nP -iTCP -sTCP:LISTEN -a -p $(pgrep
   FridayNode)` prints nothing, also checked with `nettop`.

### M2 — `feat/p9-node-readers`: readers, grants, local policy, scrubber

**Create:**
- `Sources/NodeCore/Readers/{ClaudeSessions,CodexThreads,CCUsage,GitStatus,Frontmost,Windows}.swift`;
- `Sources/NodeCore/Policy/{LocalPolicy,Grants}.swift`, persisted in
  the app's container and changeable only after `LAContext
  evaluatePolicy(.deviceOwnerAuthentication)`;
- `Sources/NodeCore/Scrubber.swift`, a port of
  `backend/memory/ingestion/scrubber.py`;
- `Sources/friday-node/Scrub.swift` (`friday-node scrub --stdin`).

**Readers:**
- **Claude sessions.** Parse `~/.claude/projects/*/*.jsonl` and **Codex
  threads** `~/.codex/sessions/**/rollout-*.jsonl` as streaming,
  bounded parsers:
  - `user`/`assistant` text only;
  - tool calls and results counted, never returned;
  - each text ≤ 2000 chars.
- **`usage.ccusage`.** Run a pinned `ccusage` binary installed
  separately (path in local policy, hash checked, with an entry in the
  governance manifest per §c.8), with
  `--json --offline`. This is a fixed argv: no shell, no user-provided
  arguments. A missing binary makes availability `unsupported`.
- **`git.status`.** Run `/usr/bin/git -C <repo> status --porcelain=v2
  --branch` plus `log -1 --format=%H%x00%cI` only for repos in the local
  list. Return counts, never file names.
- **`apps.frontmost` / `windows.list`.** Use `NSWorkspace` and
  `CGWindowListCopyWindowInfo`. Titles are returned only when the local
  toggle is on and Screen Recording is granted.

**Must satisfy:**
- D7 result schemas: the server rejects anything else (branch 5 tests).
- Grants are off by default, and a revoked grant gives `not_granted`
  immediately plus a `grant.availability_changed` event. Its batch items
  are exactly `{name, version, availability}` (`availability` one of
  `granted`/`not_granted`/`unsupported`; any other key drops the item):
  branch 6's `nodes/events.py` `apply_availability_change`, pinned by
  `nodes/tests/test_node_events.py`.
- The scrubber passes `backend/memory/tests/scrubber_corpus/` at 100%
  secrets recall: `friday-node scrub` over every corpus item compared
  with the labels, in a script `node/scripts/scrubber-corpus.sh`.
- **Parity:** `Tests/NodeCoreTests/ScrubParityTests.swift` loads
  `backend/nodes/conformance/vectors/scrub.json` from the repo path and
  requires byte-identical output for every vector. Recall alone is not
  enough: a different redaction shape would diverge from the server's
  second scrub and from the reference node.

**Contract tests:**
- conformance cases `not_granted` and the args-schema family;
- the reader cases `backend/nodes/conformance/cases/v1/v1_read_*.json`
  (branch 3): the `CONFORMANCE` build reads the fixture home that
  `backend/nodes/reference/fixtures/factory.py` builds (the transcript
  templates under `fixtures/transcripts/` rendered with `{{HOME}}` = the
  fixture home and the `<<>>` split marker removed, the repos `~/work/acme`
  and `~/code/friday`, `fixtures/desktop.json` in place of `NSWorkspace`).
  There `usage.ccusage` follows the reference rule (assistant
  `message.usage` and `costUSD` of the Claude transcripts, micro-USD
  half-even) because no ccusage binary runs in a test; the release build
  runs ccusage. The prepare op's `setup.reader_grants` and
  `setup.window_titles` set the local grants and the titles toggle, and
  `report` must include `reader_calls` (refusal cases pin 0: no reader
  may run on any refusal path);
- the normative reader rules in the `backend/nodes/reference/readers.py`
  module docstring (prefix match at segment boundaries on both the
  absolute and the `~/` form, identifiers the scrub would change dropped,
  scrub then cut, oldest turns cut first to `max_result_bytes`);
- branch 5's server tests re-run against a live Mac node with the
  owner's real sessions. This is a manual pass: the chat answers "Claude
  Code oturumlarım ne yapıyor?" from local detail, and rows of
  unmapped directories never appear.

### M3 — `feat/p9-node-hook-relay`: Unix socket, spool, switch-over

**Create:**
- `Sources/friday-node/Hook.swift`: reads stdin ≤ 256 KiB, builds the
  same allow-listed event as `integrations/cli/friday_hook.py`
  `build_event`, writes to
  `~/Library/Application Support/Friday/node.sock`, and exits 0 within
  3 s on any error;
- `Sources/NodeCore/HookSocket.swift`: directory 0700, socket 0600,
  `getpeereid` uid == own uid, otherwise close;
- `Sources/NodeCore/Spool.swift`: ≤ 10 MiB (`MAX_SPOOL_BYTES` =
  10 485 760, oldest batch dropped first), ≤ 24 h, file 0600 created
  `O_EXCL` in a 0700 directory, batches of 1–50 events only, upload via
  `event{spooled:true}` after `hello.ack`, one batch in flight, delete on
  the `event.ack` naming that frame's id (a stray ack deletes nothing); a
  live event not acked before the socket drops is spooled.

**Must satisfy:**
- the reference implementation `backend/nodes/reference/{hook_relay,spool}.py`;
- branch 6's dedupe rule (same event id as `friday-hook`);
- ADR-013 decision 7.

**Switch-over.** Change the Claude Code hook entries in
`~/.claude/settings.json` from `friday-hook` to `friday-node hook`, with
`docs/runbooks/claude-code-hooks.md` updated. Keep `friday-hook`
installed as the fallback.

**Contract tests:**
- the reference spool and relay tests re-expressed in Swift;
- the server side the Swift relay must satisfy (branch 6):
  `backend/config/tests/test_hooks_via_node.py` (an event over the relay's
  Unix socket reaches `GET /memory/sessions`; a `friday-hook` replay is a
  `duplicate`; a spooled backlog is ingested in order) and
  `backend/nodes/tests/test_node_events.py` (`event.ack` counts only, one
  batch in flight, deletion only on the ack naming the frame id, no ack
  when the server's sink fails, so the batch stays spooled);
- a manual pass: stop the stack, run a Claude Code session, restart,
  and the spooled events arrive once in `/sessions`.

### M4 — Mac evaluation (go/no-go measurements)

Recorded in this brief's "Go/no-go record" by the owner or by the Mac
session:
1. **TCC across updates.** Install notarized build N and grant Screen
   Recording (window titles). Install build N+1, signed by the same
   Developer ID, so the designated requirement is unchanged. The grant
   must still be present and titles still returned.
2. **Spike Keychain half.** The secret survives reboot. Rotation updates
   the Keychain item in place. Deleting the item forces re-pair. No
   secret appears on disk outside the Keychain:
   `grep -r jnd_ ~/Library` finds nothing.
3. **Kill-switch latency:** measured (target ≤ 2 s).
4. **Heartbeat and backoff:** they behave as specified over sleep/wake
   and network change.
5. **No TCP listener:** confirmed (M1 test 4).

## Go/no-go record (Phase 9 checkpoint)

| Item | How it is measured | Result |
|---|---|---|
| Spike S9.0, server half | Branch 1 tests (six criteria) | **passed 2026-11-20** (branch 1 tests; ADR-013 "Implementation notes — spike S9.0") |
| Spike S9.0, Keychain half | M4 item 2 | pending owner evaluation (Mac session) |
| Compromised-server suite, reference node | Branch 3 conformance run | **passed 2026-11-20** (`node_conformance_server --protocol 1` all-pass, `test_every_v1_case_passes_through_the_command_over_a_socket`; `build_node_conformance --check` in CI and as the `node_conformance` preflight check) |
| Compromised-server suite, Swift app | M1 contract tests 1–2 | pending owner evaluation (Mac session) |
| Notarized build keeps TCC grants across updates | M4 item 1 | pending owner evaluation (Mac session) |
| Server half end to end (closeout) | `ops/tests/test_node_e2e.py`: pair through the endpoint → connect → `hello` → chat turn → fenced rows of the workspace → chain rows → revoke → `bye{revoked}` → tools unavailable | **passed 2026-11-20** (CI, fakes) |
| Preflight green with a node paired | `config/tests/test_reference_node_preflight.py` (in process, real gateway process); `make preflight` on the owner's dev stack with `run_reference_node` | **passed 2026-11-20** in process; on the real dev stack: pending owner evaluation (owner step 2 of the closeout) |
| Kill-switch latency, heartbeat/backoff, no TCP listener on the Mac | M4 items 3–5 | pending owner evaluation (Mac session); server side: socket closed within 2 s of revoke / kill switch (`nodes/tests/test_gateway_handshake.py`) |

**Verdict (2026-11-20): continue.** Every server-side item passed. The Mac
rows are pending owner evaluation and, as decided, do not block Phase 8B's
or Phase 10's server branches. If the Mac session later fails an item, the
node stays unpaired in daily use (`friday-hook` keeps the sessions panel
alive) and the item is re-planned in its own brief.

The two Mac rows do not block Phase 8B: its server branches can start
once the server half is green. 8B's transcript ingestion needs M2's
reader and grant, so 8B's own brief schedules that work after the Mac
session.

## Out of scope (explicit)

- Every gate-class or confirm-class node action, invocation grants bound
  to approvals, the node confirmation window, `srt`, AppleScript, AX,
  screenshots and keystrokes: Phase 10, ADR-014.
- Transcript **ingestion** into episodes and memory: 8B. Phase 9 reads
  on demand and stores nothing.
- The Pipecat voice child process, wake word and microphone use: Phase
  11. The menu-bar mic indicator is reserved only.
- A `sessions.summarize` gateway purpose (Q1).
- Signed server-key rotation announcements (Q3).
- A Windows/Linux node, or Tauri.
- Multiple nodes per owner as a supported setup. The model allows it
  and the UI lists it, but nothing is optimised for it.
- Node log upload.
- Remote policy editing of any kind (never, ADR-013 decision 5).
- Django Channels.
- A loopback HTTP listener on the Mac (never).

## Test strategy

**Discipline.** The same as Phases 2–8A (CLAUDE.md rules 5, 7, 8):
- ruff, pytest, vitest, import-linter (new contracts) and
  `makemigrations --check` are green per branch;
- plus `build_self_sources --check` and, from branch 3,
  `build_node_conformance --check`.

**What no test touches.** No test touches a real Mac, Keychain, Touch
ID, TCC, Claude Code, Codex, ccusage, GitHub or LLM. The tests use:
- the Python reference node (in-process transport, and a real localhost
  `wsproto` socket for the fake-server run);
- the conformance key pair;
- FakeAdapter for chat turns;
- fixture JSONL transcripts;
- real Postgres and Redis (the test DB and index), as the other suites
  do.

**Release gate.** The **conformance suite is the release gate for every
node branch** (ADR-013 consequences):
- it runs against the reference node in CI now;
- it runs against the Swift app in M1;
- any case change regenerates the files (`--check`) and must pass both.

**Role × endpoint coverage** for every new surface:
- the WS handshake matrix (branch 4);
- rotate and kill switch (branch 1);
- the pairing endpoint (branch 2);
- the node APIs and invocations (branches 4 and 5);
- the read tools by channel (branch 5).

They run against the five Phase 4 principals plus a `hook` PAT and a
node secret. Isolation is tested via a second (firm) workspace with its
own mapping rules and a non-owner member.

**Fuzzing.** `hypothesis` is not added. Fuzz-style coverage of the
capability argument schemas comes from the conformance families:
traversal, NUL, RTL override, shell metacharacters, oversize and type
confusion, generated per schema.

**Release-gate regressions** (escalate per the CRITICAL/HIGH rule):
- any server→node message type outside the frozen set, or anything
  policy-shaped;
- a node credential accepted on any surface but its own, or yielding
  `CHANNEL_WEB`;
- a revoked or killed node still connected after its next heartbeat,
  or one owner's kill switch closing another owner's node;
- a `result` accepted from a socket other than the one the invocation
  was sent on;
- an `invoke` sent without a valid signature, with a reused nonce, with
  `grant` non-null or with `class` ≠ `read`;
- a read whose path maps to another workspace reaching the node;
- an unmapped row shown;
- node output stored outside the chain row's hashes and sizes (the
  ephemeral, TTL-bound Redis result key of D6 excepted);
- an aggregate computed over rows of another workspace, or a
  node-supplied aggregate shown or passed on;
- a raw corpus secret in any row, log or gateway payload;
- a personal-class read sent to a provider without
  `personal_data_allowed`;
- a node-relayed event causing anything but an ack;
- a node call (any D7 terminal status, sent or not sent) without
  exactly one chain row and one `AuditLog` row, or a broken chain;
- a model call from `nodes`.

## Risks

- **R1 — The WebSocket server is a new exposed process.** It needs a
  new dependency (`wsproto`) and a new port behind the proxy. The
  mitigations:
  - a separate process, published on loopback only;
  - origin refusal;
  - per-socket frame limits;
  - fail-closed Redis;
  - exact pins with hashes;
  - the health check in preflight.

  Residual risk: a uvicorn/`wsproto` CVE. Upgrades go in a dedicated
  branch, as for Pipecat (§c.8).
- **R2 — A compromised server.** It can sign invokes, but only for
  catalogue reads that the node validates against its own schema and
  grants. It cannot reach policy, run anything, or get past a missing
  grant. The conformance suite pins this. In Phase 9 such a server can
  read what the node is granted to read. That is accepted, because the
  owner grants per directory and can revoke or kill-switch at once.
- **R3 — Transcripts carry secrets and injected text.** Both sides
  scrub, the output is fenced, nothing is stored, the output is never
  quotable, and personal-class reads go to local providers only. The
  corpus grows with every miss (8A R1).
- **R4 — Path mapping gaps.** Reads of unmapped directories are dropped,
  so Friday may say "no sessions" when sessions exist. The tool result
  says how many rows were dropped as unmapped (a count only), so the
  owner can add a rule. This is the same model as the 8A audit view.
- **R5 — Swift parity drift.** The Swift verifier or canonical JSON
  could diverge from Python. The shared vectors and case files, loaded
  from the repo by the Swift test target, plus the live fake server,
  bound it. Any case change must pass both.
- **R6 — Mac work slips.** Apple enrolment, notarization friction or TCC
  quirks can delay it. The server half is complete and conformance-tested
  without it, `friday-hook` keeps the sessions panel alive, and the
  go/no-go Mac rows are pending owner evaluation without blocking 8B's
  server branches.
- **R7 — `windows.list` and Screen Recording.** Titles need a TCC grant
  that also allows screen capture. Titles are off by default, the grant
  is only requested when the owner turns them on, and nothing captures
  pixels in Phase 9 (screenshots are Phase 10, gate-class).
- **R8 — Redis as the invoke bus.** A Redis outage disables node reads,
  which fail closed. Revocation still bites at the next heartbeat. No
  durable queue is wanted: nothing may run later (ADR-013 decision 6).
- **R9 — Credential theft on the Mac.** The secret is in the Keychain,
  device-only and not synced. The age cap is 90 days, rotation runs
  every 30 days, and immediate revocation and the kill switch exist. A
  stolen secret still cannot reach any surface but the node socket and
  rotate, and can only answer invokes.

## Open questions (with recommended answers)

- **Q1 — A `sessions.summarize` gateway purpose in Phase 9?** No.
  - The chat call already reads bounded, fenced results (≤ 8 000 chars
    per tool) inside one `ModelUsage`.
  - A separate summarisation call would add cost and a second prompt
    surface without a measured need.
  - Revisit in 8B, when transcript volume grows. It would be a new
    purpose in `AI_PURPOSE_TIERS`, called from `tools`, never from
    `nodes`.
- **Q2 — One `TrustedDevice` table, or a `NodeCredential` table?** One
  table, with `kind` (D1).
  - `revoke_all_devices` then covers nodes by construction.
  - Pairing reuses the code flow.
  - The spike's question ("does device trust stretch to a node?") is
    answered in the existing code.
  - Revisit only if node credentials move to Secure Enclave keys
    (challenge-response), which a later hardening may want.
- **Q3 — Server signing-key rollover?** Re-pair in Phase 9, because
  there is one owner and one Mac. Phase 10 may add a rollover message
  signed by the old key and verified by the node. It would need its own
  conformance cases and must not be policy-shaped.
- **Q4 — Should node reads be allowed over the CLI channel?** No. The
  results name firm projects and the owner's desktop state.
  `coding_sessions_status` (8A) is web-only for the same reason.
- **Q5 — Where do the per-row dropped counts show?** In the tool result
  (a count) and on the node detail page (the last 50 invocations with
  `dropped_unmapped` counts). There is no separate audit view in
  Phase 9.

## Owner steps (pending) — recorded 2026-11-20

The first three are from the stub and are kept.

1. **Apple Developer enrolment (d9).** Enrol in the Apple Developer
   Program (99 USD/year) and create a Developer ID signing identity on
   the Mac. The key never leaves the Mac and CI never holds it. This
   blocks M1 only.
2. **Spike S9.0 on the real Mac.** Run the pairing and revocation checks
   on the real Mac. In this brief that is M1 contract test 3 and M4
   item 2.
3. **Per-directory grants.** Grant the per-directory reads
   (`~/.claude/projects`, `~/.codex/sessions`) with Touch ID when wanted
   (M2).
4. **Plan review.** Have the independent Sonnet 5 reviewer approve this
   brief before branch 1.
5. **Signing key.** Generate `NODE_SIGNING_KEY` on the stack host with
   `deploy/docker/gen-secrets.sh` (branch 2), and keep it out of every
   file but the host's env.
6. **Expose the gateway.** Expose `node-gateway`'s `/ws/node/v1` through
   the existing overlay or TLS reverse proxy (branch 4 runbook).
   Optionally set `NODE_TLS_PIN_SHA256`.
7. **Start the Mac session.** Start a Claude Code session on the
   MacBook with "Mac session (deferred)" as its task list once branches
   1–4 are merged (M1), and 6 for M3.
8. **Optional macOS CI.** Decide whether a macOS CI runner may run
   `swift test` for `node/friday-node`, unsigned, with no signing
   secrets. If not, the Mac session runs it locally before each merge.
9. **Record the go/no-go.** Record the M4 measurements in the "Go/no-go
   record" above.

### Owner steps (pending) — `docs/p9-brief`, recorded 2026-11-20

1. Run the independent Sonnet 5 plan review of this brief and fix
   findings until APPROVE (CLAUDE.md "Reviews").
2. Confirm the platform-boundary split: the server branches now, and the
   Swift app in a later Mac session.

### Known follow-ups (LOW, from the `docs/p9-brief` plan review)

These were left open at merge time. Each implementing branch resolves the
one that touches it before its own review.

1. **Dollar amounts in canonical JSON (D5/D7).** D5 makes canonical JSON
   integers-only, but D7's `usage.ccusage` fields `total_usd` and `usd`
   are dollar amounts. State that they are integer micro-USD (e.g.
   `usd_micros`) or decimal strings, and say whether `output_sha256` is
   computed over canonical JSON of `output`, so the Swift port and the
   server's total recomputation agree on rounding.
2. **`nodes` must not import `approvals` in Phase 9 (D2 / branch 2).** The
   import-linter contract `tools must not import approvals` has no
   `allow_indirect_imports`, and branch 5 makes `tools` import `nodes`, so
   any `nodes` → `approvals` import breaks lint via
   tools → nodes → approvals. Phase 9 `nodes` imports no `approvals` code;
   if that changes, branch 5 adds an `ignore_imports` entry for the chain.
3. **Where the in-flight cap lives (D6).** `NODE_MAX_INFLIGHT` must be a
   Redis counter (INCR/DECR with a TTL) shared across web, worker and
   `node-gateway`, not a per-process counter. Add a test where callers in
   two processes hit the limit together.
4. **Node idle window wording (D1).** Read D1 as: nodes use a
   node-specific idle window (`NODE_IDLE_EXPIRY_DAYS`) instead of
   `DEVICE_IDLE_EXPIRY_DAYS`. `TrustedDevice.is_usable()` branches on
   `kind`.
5. **Workspace scoping of `Node` and `CapabilitySnapshot` (branch 2).**
   Both are `WorkspaceScopedModel`s in the owner's personal workspace
   (CLAUDE.md rule 1). Extend the `for_actor()` isolation test and the
   node API 404 test to cover them, not only `NodeInvocation`.

### Owner steps (pending) — `feat/p9-node-pairing`, recorded 2026-11-20

1. Run the independent Sonnet 5 review of `feat/p9-node-pairing` and fix
   findings until APPROVE (CLAUDE.md "Reviews").
2. Run `make migrate` on the daily stack (migration
   `accounts/0008_node_device_trust`; existing devices become
   `kind=browser`, existing codes `device_kind=browser`).
3. Optional: set `NODE_CREDENTIAL_MAX_AGE_DAYS`,
   `NODE_CREDENTIAL_ROTATE_AFTER_DAYS`, `NODE_IDLE_EXPIRY_DAYS`,
   `NODE_ROTATION_GRACE_SECONDS` and `NODE_ROTATE_RATE` only to change
   the defaults (90 / 30 / 30 days, 600 s, 10 per hour).
4. Spike S9.0 Keychain half (pending owner evaluation, Mac task M1): pair,
   rotate and revoke from the real Mac app against these endpoints.

Implementation notes (branch 1):
- `enroll_node_device` is the service; the anonymous `POST
  /api/v1/nodes/pair` endpoint that calls it is branch 2 (D4). It raises
  `NodeKillSwitchEngaged` (distinct from `InvalidPairingCode`) so branch 2
  decides how to answer it.
- `node_device_refusal(device)` returns `revoked`, `kill_switch`,
  `credential_expired`, `idle_expired` or `inactive_owner`, for the
  gateway's heartbeat re-check and its `bye` reason (branch 4).
  `core.revocation.parse_revocation` is the subscriber-side parser.
- A web session's `POST /api/v1/auth/pairing-codes` with
  `device_kind=node` answers 409 while the kill switch is engaged. The
  kill-switch toggle and the node pairing button on `/devices` are
  branch 7; branch 1 ships the "Mac düğümü" badge and node revoke wording.
- Residual (recorded in ADR-013): a lost rotate response leaves the node
  only the grace window on its old secret, then re-pairing. M1 must
  persist the new secret before finishing the rotate call.

### Known follow-ups (LOW, from the `feat/p9-node-pairing` review)

1. `backend/accounts/devices.py` — engaging an already-engaged kill switch
   re-publishes but writes no audit row, and a node that fails
   authentication with a rotated-out previous secret after its grace is
   audited only through the flood-capped failure path. Acceptable; for
   completeness, optionally record an audit entry (e.g.
   `accounts.node.kill_switch.reengaged`) when engage is called while
   already engaged.
2. `backend/accounts/views.py` — `NodeCredentialRotateView` authenticates
   the node secret twice (authenticator, then `rotate_node_secret`),
   doubling a DB lookup and `last_seen` touch per request. Harmless and
   race-safe (the rotation re-checks under the row lock). Optionally pass
   the already-authenticated credential into the rotate service to avoid
   the second lookup.

### Owner steps (pending) — `feat/p9-node-protocol`, recorded 2026-11-20

1. Run the independent Sonnet 5 review of `feat/p9-node-protocol` and fix
   findings until APPROVE (CLAUDE.md "Reviews").
2. Run `make migrate` on the daily stack (migrations `nodes/0001_initial`
   and `nodes/0002_append_only_chain`: the node tables, the append-only
   trigger on `nodes_nodeinvocation` and the seeded chain head).
3. Run `make secrets` (or `deploy/docker/gen-secrets.sh`) on the stack host
   to write `NODE_SIGNING_KEY` into `deploy/docker/.env`. Keep it out of
   every other file. Until branch 4 passes it to web, worker and
   `node-gateway`, set it in the web service's environment to pair. Rolling
   it later means re-pairing every node (Q3).
4. Run `uv sync --frozen` wherever the backend is installed outside Docker:
   `cryptography` and `jsonschema` are now direct, exact-pinned dependencies
   (same versions as before, `docs/mcp/dependencies/node-gateway.md`).
5. Mac session M1 (deferred): the Swift test target must load
   `backend/nodes/protocol/vectors/canonical.json` and `signing.json` and
   reproduce them byte for byte, decode `backend/nodes/protocol/schemas/v1/`
   strictly (unknown keys refused, full-match patterns, integers only) and
   pin `server_signing_keys` from the `POST /api/v1/nodes/pair` response.

Implementation notes (branch 2):
- **Follow-up 1 resolved.** `usage.ccusage` amounts are integer micro-USD
  (`total_usd_micros`, `usd_micros`); `output_sha256` is sha256 of the
  canonical JSON of `output`. Every v1 time field is integer Unix seconds.
- **Follow-up 2 resolved.** `nodes` imports no `approvals` code; the
  `nodes` contract still allows the edge (ADR-013 decision 9), and the app
  docstring and the contract comment say why it stays unused in Phase 9.
- **Follow-up 5 resolved.** `Node` and `CapabilitySnapshot` are
  `WorkspaceScopedModel`s in the owner's personal workspace;
  `nodes/tests/test_scoping.py` covers all three tables with `for_actor()`.
  (The node API 404 test belongs to the APIs, branch 4.)
- `node_id` is the node's `TrustedDevice` id (`Node.pk == Node.device_id`),
  matching the branch-1 rotate response.
- The schemas allow `class` ∈ {read, confirm, gate} and `grant` null or an
  object, so a hostile `class`/`grant` reaches the node's own check 13
  (`class_not_allowed`) instead of the generic `schema` refusal; the policy
  guard pins `invoke.grant` as its only reserved exception. The guard regex
  is widened from `catalogue_set` to `catalogue[._]set`.
- A node frame-error report (checks 1–4) is a `result` with
  `status=error`, the check's code and `invocation_id` null when the frame
  named none (no new message type).
- Pairing refusals: invalid/browser/used code or no owner → 400
  `invalid_code`; PIN off-LAN → 403 `pin_network_refused`; kill switch →
  409 `kill_switch_engaged`; no common protocol version → 400
  `unsupported_protocol_version`; signing key missing/invalid/test key →
  503 `signing_unavailable` (nothing consumed); Redis down → 503
  `throttle_backend_unavailable`.
- `NodeInvocation` also hashes `dropped_unmapped` (Q5) so branch 5 does not
  need a hash-format change. `nodes.audit_chain.append` refuses any
  `(status, refusal_code)` pair outside the D7 table and any nonce or
  output on a not-sent status.
- `published_test_key_ids()` (in `nodes/keys.py`) is where branch 3 adds the
  conformance key pair.

### Owner steps (pending) — `feat/p9-reference-node`, recorded 2026-11-20

1. Run the independent Sonnet 5 review of `feat/p9-reference-node` and fix
   findings until APPROVE (CLAUDE.md "Reviews").
2. No migration, no new setting and no new dependency: `wsproto==1.3.2`
   was already pinned by `feat/p10-reference-executor` and is reused
   unchanged (manifest row updated). Nothing to run on the daily stack.
3. Mac session M2 (deferred: Mac session): the Swift readers must pass the
   `v1_read_*` conformance cases against the fixture home (M2 "Contract
   tests" above) and `ScrubParityTests.swift` must reproduce
   `backend/nodes/conformance/vectors/scrub.json` byte for byte.
4. Mac session M3 (deferred: Mac session): re-express
   `nodes/tests/test_reference_spool.py` and
   `nodes/tests/test_reference_hook_relay.py` in Swift; the peer check uses
   `getpeereid`/`LOCAL_PEERCRED` instead of `SO_PEERCRED`.

Implementation notes (branch 3):
- **Base.** `feat/p10-reference-executor` had already shipped the session
  half (`node.py`, `transport.py`, the hostile fake gateway, the v1
  protocol cases, the vendored scrubber and `build_node_conformance`), so
  this branch extends those files instead of adding parallel ones (that
  branch's owner step 2).
- **Readers** (`nodes/reference/readers.py`): the eight v1 capabilities
  over fixture directories, with local directory grants off by default
  (`not_granted` in `hello`, and the reader refuses too). Reader failures
  (unknown session, repo not on the local list) answer
  `result{status: error}` with a null code: the v1 refusal enum has no
  reader code and the server's lookup (D9) runs first anyway. Codex
  `function_call`/`function_call_output` are counted on the assistant turn
  they lead to; a Claude tool-only line adds its counts to the turn before
  it. `usage.ccusage` is computed from the transcripts' usage fields (the
  ccusage stand-in), amounts in integer micro-USD rounded half-even.
- **Fixtures** (`nodes/reference/fixtures/transcripts/`, `desktop.json`):
  synthetic Claude and Codex JSONL as templates; `build_fixture_home`
  renders them under `~/.claude` and `~/.codex`. The fake secrets carry the
  corpus split marker `<<>>` in the committed files, and the case and vector
  files carry them base64 only.
- **Conformance.** 13 `v1_read_*` cases (prefixes at segment boundaries,
  `since_hours`, scrubbed detail with tool results/file contents/meta/
  sidechain/markers absent, last turns, unknown session, Codex list and
  detail, usage, git status, an unlisted repo, titles off and on, a
  directory not granted). New report field `reader_calls`; every case whose
  frames are all refusals or frame errors pins `reader_calls: 0` (99 cases
  across v1 and v2), so no reader runs on any refusal path, and the runner
  reports a node whose reader ran. v1 now has 49 cases; v2 is unchanged (91).
- **Spool and relay** (`spool.py`, `hook_relay.py`): 10 MiB (read as
  MiB, the brief's "10 MB"), 24 h, 0600/0700, oldest first, deleted only
  after ack, survives a restart; the relay socket 0600 in a 0700 directory,
  `SO_PEERCRED` uid check behind a seam (a failing check refuses), input
  over 256 KiB dropped whole, the event built exactly as `friday-hook`'s
  `build_event` (a parity test imports `friday_hook`, the one
  `ignore_imports` entry added to the `nodes` contract, test code only).
  `relay_stdin` is the reference `friday-node hook`: exits 0 within 3 s.
- **Node events.** `ReferenceNode.relay_hook_event`: live
  `event{spooled:false}` after `hello.ack` when nothing is in flight or
  spooled, otherwise spooled; spooled batches upload in order, one in
  flight; `event.ack` is never answered; heartbeats carry the spool depth.
- **`vectors/scrub.json`** (190 items): every corpus row
  (`corpus:<file>:<id>`) and every fixture string (`fixture:<sha256
  prefix>`, transcripts rendered with home `/Users/owner`), inputs base64,
  generated and `--check`ed by `build_node_conformance`.
- The production `NODE_SIGNING_KEY` validation already refuses the
  conformance key (`nodes.keys.published_test_key_ids`, Phase 10 branch 2);
  `test_the_conformance_keys_are_refused_as_server_keys` covers it.

### Known follow-ups (from `feat/p9-reference-node`)

1. **8B owner-turn readers.** `integrations/node_reference/owner_turns.py`
   (Phase 8B branch 8) still lives outside `nodes.reference`, with its own
   fixtures and the `integrations.cli` vendored scrubber. Moving it under
   `nodes/reference/readers.py` (and its two contracts into
   `nodes/capabilities/v1/`) touches `integrations/tests` and
   `tools/tests/transcript_nodes.py` and the `integrations → nodes` import
   contract, so it is left to branch 5 (`invoke_read`), as the 8B brief's
   owner steps already plan.
2. **LOW, `backend/nodes/reference/hook_relay.py`.** `build_hook_event`
   scrubs only `message`. The allow-listed string fields (`cwd`,
   `session_id` and so on) pass through unchanged. This matches
   `friday-hook`, and a parity test pins it, but a secret embedded in a
   `cwd` would go through. Optionally scrub or identifier-check `cwd` in
   `friday-hook` and the relay together, so parity is kept.
3. **LOW, `backend/nodes/reference/readers.py`.** `claude.sessions.read`
   picks the first file whose stem equals `session_id` across all project
   directories. A duplicate `session_id` in two projects resolves to
   whichever sorts first. This is theoretical, because only fixture data is
   read. Document the tie-break, or return the most recently active match.
4. **LOW, `backend/nodes/reference/spool.py`.** `_load` accepts a bool for
   `created_at` because `bool` is a subclass of `int`. A crafted spool file
   with `true` loads as `created_at=1`, and the age cap drops it, so this is
   harmless. Add `and not isinstance(created_at, bool)`.

### Owner steps (pending) — `feat/p9-node-gateway`, recorded 2026-11-20

1. Run the independent Sonnet 5 review of `feat/p9-node-gateway` and fix
   findings until APPROVE (CLAUDE.md "Reviews").
2. Run `uv sync --frozen` wherever the backend is installed outside Docker:
   `uvicorn==0.54.0` is now a direct, exact-pinned dependency (same version
   as before, `docs/mcp/dependencies/node-gateway.md`). No migration.
3. Rebuild and restart the stack (`make up` / `make up-daily`): the new
   `node-gateway` service starts on `127.0.0.1:8002`, and web, worker and
   node-gateway now all receive `NODE_SIGNING_KEY` (and
   `NODE_SIGNING_KEYS_RETIRED`) from `deploy/docker/.env`. Check
   `curl -s http://127.0.0.1:8002/healthz` shows `"redis": true` and a
   16-character `signing_key_id` (null = no usable key: every node
   handshake is refused 1013).
4. Expose `/ws/node/v1` (only that path) through the existing overlay or TLS
   reverse proxy (`docs/runbooks/friday-node.md`, Caddy example); never
   publish 8002 on 0.0.0.0. Optionally set `NODE_TLS_PIN_SHA256` for the
   closeout's preflight. This is the brief's owner step 6.
5. Optional: `NODE_INVOKE_TIMEOUT_SECONDS` (10), `NODE_RESULT_TTL_SECONDS`
   (30) and `NODE_MAX_INFLIGHT` (4) only to change the defaults; out-of-range
   values stop boot (`nodes.E005`–`E007`).
6. **Mac session M1 (deferred: Mac session).** The Swift app's WSS client
   must satisfy the gateway as the reference node does in
   `nodes/tests/test_gateway_*.py`: offer `friday-node.v2, friday-node.v1`,
   send `Authorization: Bearer <jnd_…>` and **no `Origin`**, send `hello`
   within 10 s and a heartbeat every 15 s (silence for 45 s is closed 4408),
   stay under 20 frames/s and 256 KiB per frame, answer each `invoke` with
   one `result` on the same socket only, treat `bye{revoked|kill_switch|
   credential_expired}` as "do not reconnect", and treat the close codes of
   the runbook's table (1013 = retry with backoff; 4401 = stop until the
   owner re-pairs or releases the kill switch). Run it against a dev stack's
   `node-gateway` (pair with `manage.py issue_pairing_code --node --strong`)
   and confirm that `GET /api/v1/nodes` shows it `connected` and that
   revoking it from `/api/v1/nodes/<id>/revoke` closes the app's socket
   within 2 s (M4 item 2).

Implementation notes (branch 4):
- **Base.** Phase 10 branch 3 had already shipped the transport-agnostic
  `nodes.gateway.NodeSession` and `nodes.bus` (presence, inbox). This branch
  adds the socket app around them (`NodeSocketApp`, same module), the read
  side of the session (a per-socket read in-flight set, result validation,
  `nodes:result:<invocation_id>` pushes) and replaces the 1013 placeholder
  in `config/asgi_nodes.py`; the Phase 10 action path is unchanged.
- **Handshake order:** `Origin` (4403) → subprotocol (4400) → Redis (1013)
  → usable signing key (1013, D4/D12: the gateway serves no node it could
  not sign for) → credential (4401). Refusals accept and then close, so the
  node sees the code (a close before accept is only an HTTP 403). The
  credential check is `accounts.devices.authenticate_node_secret`
  (`surface="gateway"`, audited and flood-capped there) plus the `Node`
  row of that device and owner.
- **Close codes:** a `bye` is followed by close 1000; an oversize frame is
  closed 1009 (read as "oversize frame" and "frame rate → 4429" being two
  limits); a first frame other than `hello`, three bad frames in 60 s or
  three results for unknown/other sockets' invocations send
  `bye{protocol_error}` and close 4400; silence beyond 45 s closes 4408.
- **The presence value is the session's protocol version**, so
  `invoke_read` signs a v1 or v2 read envelope to match the live socket. An
  envelope signed for a session of another version (a reconnect in
  between) is resolved `node_disconnected`.
- **Gateway verdicts on the result key** are `{"verdict": …}`:
  `node_disconnected`, `node_mismatch` (an envelope for another node:
  `server_refused:node_mismatch`, the Phase 10 code, rather than the
  brief's generic `error`, nothing sent), `result_schema` and
  `result_too_large` (the output is dropped, never pushed). `invoke_read`
  validates the output against the server contract a second time.
- **`invoke_read`** already writes exactly one chain row and one
  `nodes.invoke` AuditLog row per call for every D7 terminal status it can
  reach, plus `refuse_read`; `channel` is a keyword (default `web`), and any
  other channel is `server_refused:not_invocable` until branch 5 moves the
  8B owner-turn contracts (`SYSTEM_READ_CAPABILITIES` is empty). Branch 5
  adds the D9 pre-checks, the 8B step-0 actor check and the tools. A Redis
  error **after** the push resolves `timeout` (the envelope may have been
  delivered; `bus_unavailable` is a not-sent status).
- **`NODE_MAX_INFLIGHT`** is the Redis counter `nodes:inflight:<node_id>`
  (INCR + EXPIRE in one `MULTI`, DECR never below zero), so the cap holds
  across web, worker and the gateway (known follow-up 3, with a
  two-process test).
- **`event` frames** are acknowledged `dropped` with counts only (D10's "no
  sink registered") until branch 6 registers the memory sink (done on
  `feat/p9-node-events`). An event never leads to an invoke.
- **`hello`** stores `node_version`/`os_version` and counts as a heartbeat
  for `last_heartbeat_at`; heartbeats write the row (and the spool depth) at
  most once per 60 s (`nodes.presence`).
- **One process.** The registry of live sockets (one per node; a second
  connection replaces the first with `bye{replaced}`, and the replaced
  socket's teardown leaves the new one's presence alone) is per process: run
  one `node-gateway` replica (compose comment, runbook).
- **Selectors** (`nodes.selectors`): `node_status`, `node_summary`,
  `node_detail` and `node_health` (the HUD block branch 7 wires); the node
  APIs use `IsAuthenticated + IsWebChannel + IsOwner` and
  `Node.objects.for_actor(actor).filter(owner=actor)`.
- **Tests** drive `NodeSocketApp` in process through
  `nodes/tests/gateway_harness.py` (an asyncio loop in a background thread,
  as under uvicorn; `django_db(transaction=True)`), plus one real-socket run
  under uvicorn with the reference node's `wsproto` client.

### Owner steps (pending) — `feat/p9-node-events`, recorded 2026-11-20

1. Run the independent Sonnet 5 review of `feat/p9-node-events` and fix
   findings until APPROVE (CLAUDE.md "Reviews").
2. Restart `node-gateway` after deploying (`make up` / `make up-daily`):
   the memory sink is registered when `config.asgi_nodes` starts. No
   migration, no new dependency, no new setting.
3. Make sure the owner's `claude_code_hooks` source is enabled and has
   path-prefix rules (the 8A runbook): node-relayed events use exactly the
   same rules, and without them every event is acked `dropped`.
4. **Mac session M3 (deferred: Mac session).** Switch the Claude Code hook
   entries to `friday-node hook` as "Switching Claude Code hooks to the
   node" in `docs/runbooks/friday-node.md` describes, keeping `friday-hook`
   as the fallback (both may run together: each event is stored once).
   The Swift relay and spool must pass the server-side tests named in M3's
   contract tests; the manual pass (stop the node, run a session, restart,
   the events arrive once in `/sessions`) is **pending owner evaluation**.
5. **Mac session M2 (deferred: Mac session).** Emit
   `grant.availability_changed` with `{name, version, availability}` items
   whenever a local grant changes; check on `/devices` (branch 7) that the
   capability's availability text follows.

### Implementation notes — `feat/p9-node-events` (2026-11-20)

- **Sink registry** (`nodes/events.py`): `register_sink(kind, fn)` accepts
  only `claude_code.hook`; `config/asgi_nodes.py` `register_node_ports()`
  registers `memory.ingestion.hooks.ingest_hook_events` (a source test pins
  `config/asgi_nodes.py` as the only caller). The sink is called with
  `user=node.owner`, `bound_workspace_id=node.workspace_id`. `event.ack`
  maps the 8A counts: `stored`, `duplicate`, and every other outcome
  (`dropped_unmapped`, `dropped_paused`, `ignored`, `invalid`) as
  `dropped`; the per-reason counts go to the gateway's counters and log
  line only. A sink's counts are clamped to the batch size.
- **A failing sink sends no ack** (D10 is silent on this case): the node keeps the batch in flight, then spools it after
  the socket drops, and sends it again after reconnect; the event id
  dedupes it. A batch that always fails is bounded by the node's 24 h
  spool cap. `config/tests` and `nodes/tests` pin it.
- **A batch of 51** is refused by the protocol schema (`maxItems: 50`)
  before any sink: a frame error under D5's three-in-60 s rule, no ack.
- **Spooled pacing**: `SpoolThrottle` books 10 slots a second per socket;
  an 11th spooled batch within the second is handled (and acked) later,
  never refused. Live batches are not paced (the 20 frames/s socket limit
  still applies).
- **Ordering**: one socket's frames are handled in order and each is acked
  before the next is read, so spooled batches reach the sink in the order
  the node sent them; 8A's upsert already tolerates out-of-order events.
- **`grant.availability_changed`**: items are exactly `{name, version,
  availability}`. A change writes a successor `CapabilitySnapshot` (same
  `catalogue_sha256` as the `hello` it derives from) with the entry's
  `availability` and its verdict moved between invocable, `not_granted`
  and `unsupported`; a contract mismatch reason never moves. Unchanged
  items count `duplicate`, invalid or unknown ones `dropped`; no row when
  nothing changed. `services.record_capability_snapshot` now also writes a
  new row when the latest snapshot's entries differ from the `hello`'s
  verdicts, so a reconnect with the pre-change catalogue does not inherit
  the changed flags. The HUD reads `nodes.selectors.node_health`, so its
  `invocable_count` follows (branch 7 wires the HUD block itself).
- **Test placement**: the end-to-end tests through the real memory sink are
  `backend/config/tests/test_hooks_via_node.py` (the brief's Files line
  names `backend/memory/tests/test_hooks_via_node.py`, but a `memory` test
  importing `nodes` would break the "only tools, hud, ops may import nodes"
  contract, and `nodes` tests may not import `memory`). The memory-side
  file holds the sink contract under the node principal binding, without
  importing `nodes`.

### Known follow-ups (LOW, from the `feat/p9-node-events` review)

1. **LOW, `backend/nodes/gateway.py`.** The spooled-batch throttle awaits
   `asyncio.sleep` inside the per-socket receive loop (up to about 1 s), so
   heartbeats and other frames on that socket wait while a backlog is paced.
   Bounded by 10 batches/s and the 20 frames/s limit, so not blocking.
   Optionally track `last_frame_at` before sleeping, or document that pacing
   intentionally back-pressures the socket.
2. **LOW, `backend/nodes/events.py`.** `grant.availability_changed` is
   trusted node-supplied data and can flip a `not_granted` entry to
   invocable in the snapshot. This matches ADR-013 decision 5 and D5 check
   16 (the node still refuses ungranted reads itself), and entries with a
   contract-mismatch reason cannot move. Add a one-line note to
   `docs/runbooks/friday-node.md` that a compromised node can only affect
   its own snapshot flags.
3. **LOW, this brief.** The branch 6 Files line names
   `backend/memory/tests/test_hooks_via_node.py`, while the end-to-end tests
   live in `backend/config/tests/test_hooks_via_node.py` to respect import
   contracts. The deviation is documented in the implementation notes; no
   action required.
4. **LOW, `backend`.** The branch's own combined pytest run (nodes + config
   + memory test files) was stopped after more than 25 minutes under
   contention, with no failures up to 76 %; `config/tests/test_hooks_via_node.py`,
   `config/tests/test_asgi_nodes.py` and `memory/tests/test_hooks_via_node.py`
   were not confirmed to completion there. Run them once on an idle machine
   (the integration merge reruns the full suite).

### Known follow-ups (LOW, from the `feat/p9-node-gateway` review)

1. **LOW, `backend/nodes/gateway.py`.** An oversize inbound frame is closed
   1009, not the 4429 that the branch 4 Tests line literally names
   ("oversize frame, frame rate → 4429"). The deviation is recorded in the
   implementation notes and pinned by `test_an_oversize_frame_closes_1009`.
   Keep 1009 and make the Tests line match, or switch to 4429.
2. **LOW, `backend/nodes/gateway.py`.** The `hello` frame bypasses
   `_rate_exceeded` and the frame-error accounting. It is bounded by the
   10 s timeout and the size check, so it is not exploitable. Optionally
   route the first frame through the same limit path.
3. **LOW, `backend/nodes/gateway.py`.** All gateway DB work runs through
   `sync_to_async(thread_sensitive=True)`, so it is serialised on one thread
   across sockets. Fine for a single-owner fleet, but a scaling ceiling:
   document it in the runbook, or use a bounded thread pool if the fleet
   grows.
4. **LOW, `backend/nodes/gateway.py`.** When a socket is replaced, the old
   connection's inbox loop can pop an envelope after the new connection
   registers; that read ends as a caller timeout or `node_disconnected`
   rather than being delivered to the new socket. It fails closed, with no
   retry by design. No change needed; optionally cancel the old inbox task
   in `_register` before sending `bye`.

### Owner steps (pending) — `feat/p9-node-reads`, recorded 2026-11-20

1. Run the independent Sonnet 5 review of `feat/p9-node-reads` and fix
   findings until APPROVE (CLAUDE.md "Reviews").
2. No migration, no new setting and no new dependency. Restart web (and
   worker) after merging so the five read tools register at start-up.
3. Make sure the Claude Code hook path rules (8A, `/memory` → sources →
   `claude_code_hooks` → mapping rules) cover the directories you want
   Friday to see, **in the `~/…` form**: the node reports `cwd` as `~/…`
   under the Mac's home, and a `~/` rule never matches an absolute
   `/Users/<you>/…` key (or the reverse). A workspace without a rule never
   gets the node tools (D9 availability); unmapped rows are dropped and
   counted (`dropped_unmapped` on `GET /api/v1/nodes/<id>/invocations`).
4. Personal reads (`node_session_detail`, `node_desktop`) need a provider
   with `personal_data_allowed` (a local model). Without one, the turn ends
   "local model required" and nothing is read from the Mac — by design.
5. **Mac session M2 (deferred: Mac session).** The Swift readers must honour
   `cwd_prefixes` exactly as the reference node does: a list `invoke`
   always carries the conversation workspace's rule prefixes in `~/` form,
   and the server drops every row whose `cwd` resolves elsewhere anyway
   (`tools/tests/test_node_reads.py` pins the server side, including a node
   that ignores the prefixes and one that inflates `usage.ccusage` totals).
   Report `cwd` in `~/` form under the user's home. M4 evaluates the answer
   to "Claude Code oturumlarım ne yapıyor?" on the real Mac (pending owner
   evaluation).

Implementation notes (branch 5):
- **`invoke_read`** gains the Phase 8B **step-0 actor check** (a
  `data_class: personal` capability needs an active, human actor who owns
  the node and holds `owner` in the target workspace, else
  `server_refused:actor_not_owner`, nothing signed) and a **`row_filter`**
  hook: it runs on a valid `ok` output before the chain row is written, and
  its dropped count is hashed into the row (`dropped_unmapped`, Q5) and put
  in the `nodes.invoke` AuditLog metadata. `output_sha256`/`output_bytes`
  stay those of the node's own answer. A filter that raises ends the read
  `error:result_schema` with no output.
- **The read vocabulary** is `nodes.models.READ_TERMINAL_VOCABULARY`: the D7
  table plus `actor_not_owner` (8B) and `node_mismatch` (branch 4's gateway
  verdict). `_record_read` refuses any other pair, `refuse_read` accepts only
  `CALLER_REFUSAL_CODES` (`path_not_in_workspace`, `workspace_not_allowed`,
  `session_not_found`); `test_the_read_vocabulary_is_the_d7_table` pins it.
- **The tools** (`tools/node_reads.py`): `node_sessions` (both lists; `tool`
  = claude/codex/all, "all" calls only the invocable ones),
  `node_session_detail` (lookup list with `since_hours=168, limit=50`, then
  the read, one `trace_id`), `node_usage`, `node_git_status` (the path is
  normalised and resolved before invoking; the normalised form is sent),
  `node_desktop` (`view` = frontmost/windows/all). Turkish router
  descriptions. A node call that does not end `ok` is returned as a short
  Friday-authored Turkish status line with its code (for example "Mac'e şu
  an ulaşılamıyor." for `node_offline`), never node text, so the chat model
  can say what happened; a refused caller (wrong channel, not the owner,
  no usable node, no rule for the workspace) is a `ReadToolError` before
  `nodes` and writes no chain row.
- **Which node.** `nodes.selectors.read_node_for(owner)`: a usable node (not
  revoked, killed or expired), a live one first, else the latest usable one
  (a direct call then writes its `node_offline` row).
  `is_available(workspace)` uses the workspace's owner, checks the database
  first and touches Redis only when a usable node exists.
- **The row filter** uses the new `memory.ingestion.mapping.path_resolver(
  owner)` (the hooks rules loaded once per call; same answers as
  `workspace_for_path`, pinned by `memory/tests/test_mapping_node_helpers.py`).
  `prefixes_for_workspace` and `workspace_for_path` already existed (8B
  branch 8). More than 20 prefixes: the first 20 (sorted) are sent, the
  server filter still applies.
- **A `*.read` answered for another session** than the one asked is dropped
  whole (its turns counted as dropped).
- **Bounds.** The rendered text stays under `CONNECTOR_RESULT_MAX_CHARS`
  (8 000) at whole lines; sections share the room (transcript turns keep the
  newest), and a cut is marked ("N satır sığmadı").
- **Invocations API.** `GET /api/v1/nodes/<id>/invocations?cursor=<seq>`:
  50 rows per page, newest first, `next_cursor` = the last row's `seq` when
  the page is full; rows of workspaces the actor cannot see are left out;
  audited `nodes.invocations.viewed` (node id, count, cursor).
- **Tests** drive the real gateway read path in process
  (`tools/tests/node_reads_helpers.py`: a v1 `NodeSession` plus the reference
  node with fixed-time readers; `bus.wait_result` pumps the session), so
  every envelope the node received is asserted on. The chat-turn e2e lives
  in `config/tests/test_node_read_turns.py`, not `assistant/tests/` as the
  Files list says: it needs `assistant`, `tools` and `nodes` together, and
  only `config` may import all three (the "only tools, hud, ops may import
  nodes" and "tools ↛ assistant" contracts). The 8A prompt-injection
  corpus (`memory/tests/test_learning_night.py`) gains two transcript-shaped
  items.

### Owner steps (pending) — `feat/p9-node-ui`, recorded 2026-11-20

1. Run the independent Sonnet 5 review of `feat/p9-node-ui` and fix
   findings until APPROVE (CLAUDE.md "Reviews").
2. No migration, no new setting, no new dependency. Rebuild the frontend
   and restart web (`make up` / `make up-daily`).
3. On `/devices` → "Mac düğümleri": issue a node code ("Mac eşleştir" on
   the LAN, or the strong code from another network). It is shown once;
   enter it in the Mac app's pairing screen (**Mac session M1, deferred:
   Mac session**). Then check that the node shows `Bağlı`, that its
   capability list says "Mac'te izin verilmedi" for every directory you
   have not granted on the Mac, and that granting one on the Mac (M2)
   turns it into "Kullanılabilir" after the node's
   `grant.availability_changed` (pending owner evaluation).
4. Try the kill switch once from `/devices` (on, the node line in the HUD
   shows "acil durdurma açık" and the Mac app drops the socket within 2 s;
   off, it reconnects) and revoke a test node (M4 item 2, pending owner
   evaluation).

### Implementation notes — `feat/p9-node-ui` (2026-11-20)

- **HUD.** `hud.services.health_section(workspace, actor)` gains
  `node` (`hud.services.node_section`): `status`, `last_heartbeat_at`,
  `catalogue_sha256` (12 chars), `invocable_count`, `advertised_count`,
  `spool_depth`, `node_version` — nothing else (no node id, no path, no
  capability name). It reads `nodes.selectors.node_health(actor)`, which
  now goes through `nodes_for()` (`for_actor()` + `owner=actor`). The line
  is the actor's own node, so it shows in each of the owner's workspace
  streams (the stream is owner-only, `IsOwner`); presence flips change
  `health` and produce a delta (pinned). The `hud → nodes` import edge was
  already allowed by the Phase 10 D12 contract edit; only its comment
  now names D11 too.
- **BFF** (`frontend/src/app/api/nodes/`): `GET /api/nodes`,
  `GET /api/nodes/[id]`, `GET /api/nodes/[id]/invocations?cursor=<digits>`,
  `POST /api/nodes/[id]/revoke`, `GET/POST/DELETE /api/nodes/kill-switch`
  (→ `/api/v1/auth/nodes/kill-switch`, no body forwarded) and
  `POST /api/nodes/pairing-codes` (→ `/api/v1/auth/pairing-codes` with
  `device_kind=node` always, `kind` pin/strong only). Mutations are
  origin- and content-type-checked as the other BFF routes.
- **UI.** `components/nodes/NodesSection.tsx` on `/devices` (below the
  existing browser list, which keeps its "Mac düğümü" badge) and
  `components/nodes/NodeDetail.tsx` on `/devices/nodes/[nodeId]`. The
  capability list is text only (a test asserts no input, button, select
  or switch inside it). The invocation log renders only named fields:
  capability, status (+ refusal code), seq and time, workspace name,
  duration, the args hash, the output hash and size, dropped-unmapped
  count. Pairing buttons are disabled while the kill switch is engaged
  (the backend refuses with 409 anyway). Status colours use the semantic
  tones (`STATUS_TONES.node`, `.nodeInvocation`) and checked HUD text
  tokens (`NODE_HUD_TOKENS`); no new colour token, so no new contrast
  pair (pinned in `lib/theme/tokens.test.ts`). Layouts are single-column
  below `sm`.

### Known follow-ups (from `feat/p9-node-reads`)

1. **The 8B owner-turn move (the D7/D9 amendment's "branch 5 or a
   follow-up").** `claude.transcripts.owner_turns` and
   `codex.transcripts.owner_turns` still live in
   `integrations/node_reference/contracts/v1/`, `SYSTEM_READ_CAPABILITIES`
   in `nodes.services` is still empty, and the transcript pull still uses
   its `NodeReadPort` seam (`tools/transcript_ingest.py`, default
   `UnavailableNodePort`). Moving the two contracts into
   `nodes/capabilities/v1/` changes the v1 contract set (the snapshot
   tests, the reference node's catalogue and the conformance case files,
   `build_node_conformance --check`), moves `owner_turns.py` under
   `nodes.reference` (with its fixtures and the vendored scrubber), and
   adds a `nodes`-backed port that calls `invoke_read(channel=
   CHANNEL_SYSTEM, actor=<owner>)`. The step-0 actor check it needs is in
   place now. Its own branch, so the conformance regeneration is reviewed
   on its own.
2. **LOW.** `is_available` costs one Node query (and, with a usable node,
   one Redis `EXISTS` and a snapshot query) per node tool per turn: five
   tools, so up to five of each. Fine for one owner; cache per request if
   the tool set grows.

### Known follow-ups (LOW, from the `feat/p9-node-reads` review)

1. **LOW, `backend/nodes/tests/test_invoke_read.py`.**
   `test_a_non_web_channel_is_refused_until_branch_5` keeps a stale name now
   that branch 5 has landed. Rename it to describe the current behaviour: a
   non-web channel is refused as `not_invocable` for non-system capabilities.
2. **LOW, `backend/tools/node_reads.py`.** The `*.read` lookup asks only for
   the last 168 h and 50 rows, so a valid session older than that, or beyond
   the 50th row, gets `session_not_found`. Document the limit in the tool
   description or the runbook; optionally pass a larger limit up to the
   contract maximum, or look up by id if the contract allows.
3. **LOW, this brief.** The branch 5 Files list names `backend/assistant/tests/`
   for the chat-turn e2e and `nodes/serializers.py`. The e2e lives in
   `backend/config/tests/test_node_read_turns.py` (import contracts) and no
   serializers change was needed; the behaviour is covered. Note the actual
   location in the branch 5 section, or leave it.

### Known follow-ups (LOW, from the `feat/p9-node-ui` review)

1. **LOW, `frontend/src/components/nodes/NodesSection.tsx`.** `handleChanged`
   merges the revoke response (a summary) over the detail, so capability
   entries stay shown for a just-revoked node until the next reload. Cosmetic
   only; the backend refuses invocations for revoked nodes. Fix: call `load()`
   after a successful revoke, as `handleKillSwitch` does.
2. **LOW, `backend/hud/services.py`.** `node_section` reads
   `node_health(actor)` on every HUD stream tick, so each tick runs node
   queries plus a Redis presence read. This is bounded (a few nodes per owner)
   and a test pins that the stream writes no rows. Fix: if HUD tick cost
   matters later, cache the node block for a few seconds.

### Owner steps (pending) — `chore/p9-closeout`, recorded 2026-11-20

1. Run the independent Sonnet 5 review of `chore/p9-closeout` and fix
   findings until APPROVE (CLAUDE.md "Reviews").
2. **Preflight on the real dev stack (the done criterion's live half).**
   `make up`, then pair the reference node and run preflight as
   `docs/runbooks/friday-node.md` "Preflight node checks" shows
   (`issue_pairing_code --node --strong --plain`, `run_reference_node --code
   …`, `make preflight`). Expect `node_gateway`, `node_signing_key`,
   `node_link:Reference node`, `node_audit_chain` and `node_conformance` to
   pass (`node_tls_pin` warns until owner step 3). Then revoke the reference
   node on `/devices`. Pending owner evaluation.
3. **Optional TLS pin.** Compute the proxy's SPKI pin with the runbook's
   `openssl` pipeline and set `NODE_TLS_PIN_SHA256` and
   `PREFLIGHT_NODE_TLS_URL` in `deploy/docker/.env`; restart web.
4. No migration, no new dependency. New optional settings (all in
   `.env.example`): `PREFLIGHT_NODE_GATEWAY_URL` (the compose stack sets it
   for web), `NODE_TLS_PIN_SHA256`, `PREFLIGHT_NODE_TLS_URL`. Rebuild web and
   worker (`make up` / `make up-daily`) so the worker registers the
   `nodes.signing_key_id` task; until it does, `node_signing_key` fails
   with "no worker answered".
5. **Mac session (deferred: Mac session).** Start it as owner step 7 of
   this brief says; M1–M4 above are its task list, and the conformance suite
   plus the server-side contract tests named there are its acceptance
   tests. Record M4's measurements in the "Go/no-go record" (pending owner
   evaluation).

### Implementation notes — `chore/p9-closeout` (2026-11-20)

- **Preflight** (`ops/preflight.py`, `check_nodes`, registered as `nodes`
  after `learning`): six checks, read-only, no model call, nothing sent to a
  node. New fakeable seams beside `celery_ping`: `worker_signing_key_id`
  (sends the `nodes.signing_key_id` task by name and waits for its id),
  `served_spki_sha256` (TLS handshake without trust verification, SNI = the
  host; only the SPKI hash is compared, nothing is sent) and
  `node_conformance_drift` (`build_node_conformance --check` in process).
  The gateway's `/healthz` goes through the existing `http_get` seam.
- **Verdict rules.** `node_gateway`: `PREFLIGHT_NODE_GATEWAY_URL` empty =
  `warn`; unreachable, non-200, unexpected body or `redis: false` = `fail`.
  `node_signing_key`: web's key missing, invalid or a published
  test/conformance key = `fail` (reason only, never material); the
  gateway's or worker's id missing or different = `fail`; no worker answer =
  `fail`; an active (non-revoked) node with another `pinned_key_id` =
  `fail` ("re-pair"); a skipped comparison (no gateway URL, eager Celery) =
  `warn`. `node_link`: one row per active node, named
  `node_link:<node name>` (`pass` only when connected with a heartbeat row
  younger than 180 s and a snapshot; otherwise `warn`), or one `node_link`
  `warn` when none is paired. `node_tls_pin`: pin or URL unset = `warn`;
  malformed pin/URL, handshake error or mismatch = `fail`; the pin is
  base64 or hex (a `sha256/` prefix is accepted). Each check is guarded:
  one crash is one `fail` row, the others still report.
- **HUD.** No change needed: `record_last_result` already forwards failing
  check names, which now include the node names
  (`config/tests/test_preflight_hud_nodes.py`).
- **E2E** lives in `ops/tests/test_node_e2e.py` (spans `accounts`, `nodes`,
  `tools`, `assistant`, `memory`); it reuses `nodes/tests/gateway_harness`
  and `tools/tests/node_reads_helpers` and pins, after the revoke, that the
  same routed question degrades to plain chat (the router no longer sees a
  node tool) and that a reconnect with the revoked secret is closed 4401.
- **Existing tests touched:** `ops/tests/test_preflight.py` stubs the two
  new seams in `_stub_infra` and checks the voice check order by index (the
  node checks are now last).


### Known follow-ups (LOW, from the `chore/p9-closeout` review)

1. **LOW, `docs/prompts/phase-9-friday-node/brief.md`.** The closeout notes
   and the branch-table row are dated 2026-11-20, but the work landed later
   (merged 2026-11-23). Cosmetic only. Fix: optionally update the dates.
2. **LOW, `backend/ops/preflight.py`.** `_active_nodes` reads `Node` rows
   across all workspaces and owners without `for_actor()`. This is acceptable
   for operations tooling and is documented in the docstring; `check_nodes`
   details expose node names to the operator-only CLI. The HUD receives only
   failing check names, but those now include `node_link:<node name>`, so a
   user-chosen node name reaches the HUD. Fix: none required; optionally
   emit a generic `node_link` name in the HUD's failing list if node names
   are ever considered sensitive.
