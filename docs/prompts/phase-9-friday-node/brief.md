# Phase 9 — Friday Node v1, read-only (server side + Mac session)

> Status: **Draft (2026-11-20). Written on `docs/p9-brief` from the stub of
> the same date, which was taken from the owner-approved Friday 3.0 roadmap.
> It needs its independent Sonnet 5 plan review before branch 1, because
> the implementing model never reviews its own brief.**
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
  - Revoking calls `core.revocation.publish_device_revoked(device_id)`.
    This is a best-effort Redis `PUBLISH` on `friday:device-revoked`.
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
    socket is closed (published like a revocation). Pairing a new node
    is refused too.
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
  only by the rotate endpoint and, through `nodes`, by the pair-time key
  fetch and the WebSocket handshake. Failures are audited with the same
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
import Django or any project package outside `nodes.protocol`. Two things
enforce this: a contract over the project packages, and a test that
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
    reachable, connected node count);
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
    to the result key with a 30 s TTL.
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
  `friday:device-revoked`, and to the kill-switch publication that uses
  the same channel with `device_id="*"`. It sends `bye` and closes the
  matching sockets within 2 s.
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
- **The six v1 read capabilities.** All are `class: read` (ADR-014).

  | Capability | Args | Result (bounded) | `data_class` | Workspace resolved from |
  |---|---|---|---|---|
  | `claude.sessions.list` | `since_hours` (1–168), `limit` (≤ 50) | `[{session_id, cwd, started_at, last_activity_at, message_count, model, status_hint}]`; no text | firm | `cwd` per row |
  | `claude.sessions.read` | `session_id`, `last_turns` (1–20) | `[{role (user/assistant), at, text ≤ 2000}]`; tool calls and results as counts only | personal | the session's `cwd` |
  | `codex.threads.list` | as `claude.sessions.list` | as above (`thread_id`) | firm | `cwd` per row |
  | `codex.threads.read` | `thread_id`, `last_turns` (1–20) | as `claude.sessions.read` | personal | the thread's `cwd` |
  | `usage.ccusage` | `period` (`today`/`week`/`month`) | `{total_usd, tokens_in, tokens_out, by_project:[{cwd, usd}]}` | firm | `cwd` per row |
  | `git.status` | `repo_path` | `{branch, ahead, behind, dirty_files_count, untracked_count, last_commit_sha, last_commit_at}`; never file names or diffs | firm | `repo_path` |
  | `apps.frontmost` | none | `{bundle_id, app_name}` | personal | none |
  | `windows.list` | `limit` (≤ 30) | `[{bundle_id, app_name, window_count, on_screen}]`; titles only if the owner enabled them locally (D13) | personal | none |

  These are eight wire capabilities in six families, the ADR-014 read
  list.
- **`invoke_read(*, node, capability, args, workspace, actor, trace_id)
  -> ReadOutcome`** runs these steps:
  1. Check the snapshot entry is invocable.
  2. Validate `args` against the server contract.
  3. Write a pending in-memory record (nothing in the DB).
  4. Sign and push.
  5. Wait.
  6. Validate the result against `result_schema`.
  7. Write exactly one `NodeInvocation` chain row (D8) at the terminal
     state: `ok`, `refused:<code>`, `error`, `timeout`, `node_offline`,
     `node_disconnected`, `busy` or `bus_unavailable`.
  8. Write one `nodes.invoke` `AuditLog` row with the capability, args
     hash, nonce, status, duration, workspace and trace id. It never
     contains args or output content.

  It returns the validated output to the caller and **stores none of
  it**.

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
  `prev_hash` included. The genesis `prev_hash` is 64 zeros.
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
    refused **before** invoking (`path_not_in_workspace`).
  - For `*.read`, the server first runs the matching `*.list` to learn
    the session's `cwd`, then applies the same rule.
  - List results are filtered row by row. Unmapped rows and rows of
    another workspace are dropped and counted, never shown.
  - `apps.frontmost` and `windows.list` have no path. They are personal
    and allowed only in the owner's personal workspace.
- **Second scrub.** Every string in a result passes through
  `memory.ingestion.scrubber.scrub` with the target workspace's
  `ScrubTerm`s before it is fenced. The node scrubs first (M2), so this
  is defence in depth, as in 8A D3.
- **Fencing.** Output is rendered as
  `<connector_result source="friday-node">`, with `neutralize_fence` and
  `strip_invisible_formatting`, capped at 8 000 characters per tool
  result (truncation is marked).
- **Prompt injection.** Transcripts are attacker-reachable text. They
  are fenced, never quotable as provenance (8A D9), and never stored.
- **No separate summarisation call** (Q1). The chat model answers from
  the fenced results inside the normal `chat` call. Rule 3 is unchanged:
  one `ModelUsage` row for that call.

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
   Tool results and file contents are never returned. Every string is
   scrubbed with a port of the 8A scrubber that passes the same corpus.
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
  device (mocked Redis), and a Redis error never fails the revoke.
- **Kill switch:**
  - engaged means authentication fails and pairing is refused;
  - released restores it without re-pairing;
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
    Linux; the Swift app uses `LOCAL_PEERCRED`) as the reference for M3.
- `nodes/conformance/`:
  - case definitions in Python, and generated case files
    `cases/v1/*.json` (each case lists server frames, the expected node
    frames and refusal codes, and asserts that no reader ran);
  - `manage.py build_node_conformance [--check]`, so the files cannot
    drift from the definitions (CI step);
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
- `backend/nodes/reference/{__init__,node,transport,readers,spool,hook_relay}.py`;
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
- Purity: no Django import (subprocess).

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
- `deploy/docker/{compose.yml,compose.daily.yml}`;
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
  second connection replaces the first.
- **Invoke bus:**
  - round trip;
  - timeout → `timeout`, and the late result is discarded;
  - disconnect mid-call → `node_disconnected`, with no retry (an
    assertion on the reference node's received frames);
  - `NODE_MAX_INFLIGHT`+1 → `busy`;
  - no presence → `node_offline` immediately, with nothing pushed;
  - Redis down → `bus_unavailable`, and handshake 1013.
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
  `memory.ingestion.mapping.workspace_for_path`, the pre-invoke path
  refusal, row filtering, the second scrub, fencing and the size caps.
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
  - `apps.frontmost` in a firm workspace is refused.
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
  - exactly one `NodeInvocation` and one `AuditLog` row per call, for
    every terminal status;
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
  | `node_signing_key` | Configured, valid, not the conformance key; key id matches what active nodes pinned at pairing (stored on `Node.pinned_key_id`) |
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
- `backend/ops/preflight.py`, `backend/ops/tests/test_preflight_nodes.py`,
  `backend/ops/tests/test_node_e2e.py`;
- `docs/runbooks/friday-node.md`, `docs/adr/ADR-013-friday-node-protocol.md`;
- `docs/MASTER_PLAN.md`, `backend/assistant/self_sources.json`;
- `docs/prompts/phase-9-friday-node/brief.md`.

**Tests.**
- Every new preflight check passes, fails and warns with fakes:
  - unreachable gateway;
  - conformance key configured;
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
| brief | `docs/p9-brief` | This brief (full, from the stub), MASTER_PLAN §AA row 9 Brief/Status, regenerated `self_sources.json` | Phase 8A closeout | **implemented 2026-11-20** (docs only; awaits the independent Sonnet 5 plan review) |
| 1 | `feat/p9-node-pairing` | Spike S9.0: `TrustedDevice.kind=node`, node pairing codes, `jnd_` secrets, rotation with grace, age cap, revocation fan-out, kill switch, `CHANNEL_NODE`, URL walk, ADR-013 spike note | brief approved | not started |
| 2 | `feat/p9-node-protocol` | `nodes` app + contracts, protocol v1 schemas, canonical JSON, Ed25519 envelopes, capability contracts, pairing endpoint, signing key config, hash-chained `NodeInvocation`, dependency manifest | 1 | not started |
| 3 | `feat/p9-reference-node` | Python reference node, conformance cases + `--check`, hostile fake gateway, reference spool and Unix-socket relay | 2 | not started |
| 4 | `feat/p9-node-gateway` | `asgi_nodes` + uvicorn/`wsproto`, `node-gateway` service, handshake, `hello`/snapshot, heartbeat/presence, invoke bus, revocation/kill-switch fan-out, limits, node APIs | 1, 2, 3 | not started |
| 5 | `feat/p9-node-reads` | `invoke_read` end to end, five read tools, path → workspace resolution, second scrub, fencing, chain + audit rows, invocations API | 4 | not started |
| 6 | `feat/p9-node-events` | `event` sink wiring into `ingest_hook_events`, cross-relay dedupe, spool upload acks, availability events | 4 (parallel with 5) | not started |
| 7 | `feat/p9-node-ui` | `/devices` node section, invocation log page, HUD node line, `hud → nodes` contract | 4, 5 (6 for availability) | not started |
| 8 | `chore/p9-closeout` | Preflight node checks, e2e, runbook, ADR-013 notes, MASTER_PLAN row, go/no-go record | 1–7 | not started |
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
- `scripts/build-sign-notarize.sh` (`codesign --options runtime
  --timestamp`, `notarytool submit --wait`, `stapler staple`);
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
   --port 8765 --tls-self-signed` on the Mac, with the app started in
   conformance mode (`FRIDAY_NODE_CONFORMANCE=1`, pinning the fake
   server's self-signed SPKI and the conformance key). It must print
   all-pass.
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
  separately (path in local policy, hash checked), with
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
  immediately plus a `grant.availability_changed` event.
- The scrubber passes `backend/memory/tests/scrubber_corpus/` at 100%
  secrets recall: `friday-node scrub` over every corpus item compared
  with the labels, in a script `node/scripts/scrubber-corpus.sh`.

**Contract tests:**
- conformance cases `not_granted` and the args-schema family;
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
- `Sources/NodeCore/Spool.swift`: ≤ 10 MB, ≤ 24 h, file 0600, events
  only, upload via `event{spooled:true}`, delete on `event.ack`.

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
| Spike S9.0, server half | Branch 1 tests (six criteria) | pending (branch 1) |
| Spike S9.0, Keychain half | M4 item 2 | pending owner evaluation (Mac session) |
| Compromised-server suite, reference node | Branch 3 conformance run | pending (branch 3) |
| Compromised-server suite, Swift app | M1 contract tests 1–2 | pending owner evaluation (Mac session) |
| Notarized build keeps TCC grants across updates | M4 item 1 | pending owner evaluation (Mac session) |

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
- a revoked or killed node still connected after its next heartbeat;
- an `invoke` sent without a valid signature, with a reused nonce, with
  `grant` non-null or with `class` ≠ `read`;
- a read whose path maps to another workspace reaching the node;
- an unmapped row shown;
- node output stored outside the chain row's hashes and sizes;
- a raw corpus secret in any row, log or gateway payload;
- a personal-class read sent to a provider without
  `personal_data_allowed`;
- a node-relayed event causing anything but an ack;
- a missing or broken chain row;
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
