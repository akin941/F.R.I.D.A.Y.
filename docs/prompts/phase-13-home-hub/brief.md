# Phase 13 — Home hub: home server, many nodes, one Friday (server side + Mac session)

> Status: **Draft (2026-11-26). Written on `docs/p13-brief` for the new
> phase the owner decided on 2026-11-26 (MASTER_PLAN §O decision d11,
> ADR-017). It needs its independent Sonnet 5 plan review to reach APPROVE
> before branch 1, because the implementing model never reviews its own
> brief.**
> Sources: the owner decision of 2026-11-26 (recorded below as "Owner
> decision kept"); `docs/research/2026-11-roadmap-proposal.md` §B1 (node
> shell and Tauri-only-for-Windows, d1), §c.4 (owner away = deny), §c.7
> (node identity), the failure-mode table (server down → node fails
> closed); MASTER_PLAN §AA "Friday 3.0 phases" (this phase is added as row
> 9, "13 Home hub"); the Phase 9 brief ("Out of scope": *multiple nodes per
> owner as a supported setup — the model allows it and the UI lists it, but
> nothing is optimised for it*; *a Windows/Linux node*); the Phase 5 brief
> branch 7 (the Home Assistant bridge) and `docs/mcp/home-assistant.md`.
> Parent decisions:
> - ADR-001: workspace axis, `for_actor()`. Rooms, home entities and node
>   labels are rows of the owner's **personal** workspace only.
> - ADR-002: modular monolith. No new kernel app; one new model in
>   `workspaces`, one in `connectors`, new fields on `nodes.Node` (D16).
> - ADR-003 and its 2026-11-20 amendment: single gateway door. **Phase 13
>   adds no gateway purpose and no model call**: node resolution and the
>   home routes are deterministic (D7, D13).
> - ADR-004 / ADR-008 decision 3 / ADR-009: every Home Assistant service
>   call stays gate-class (D12); the confirm-class list does not grow.
> - ADR-005: Postgres on the home server is the only source of truth.
> - ADR-010: the HA bridge, its code maximum and its pins (D11, D12).
> - **ADR-013** (node protocol): **unchanged**. Phase 13 adds no WS
>   message, no schema field and no capability contract; every node,
>   including the new Linux node, runs the existing conformance suite.
> - **ADR-014** (node action classes): **unchanged**, applied per node: a
>   gate-class action is a web approval plus a click on *that* node's own
>   window (D9).
> - **ADR-015** (voice-originated turns): a voice turn reads and proposes
>   only. "Salondaki TV'yi aç" yields a draft card, never a call (D13).
> - **ADR-017** (new, Accepted 2026-11-26, written in this branch): home
>   hub topology — one always-on server on a home Linux PC, every PC a
>   paired node, LAN-only reach (Tailscale optional), node WSS still TLS
>   with a pinned certificate or private CA, full-disk encryption, nightly
>   encrypted off-site backups with a tested restore, an explicit later
>   path to a VPS without changing the node protocol.
>   **Numbering deviation:** the task text named it "ADR-016", but ADR-016
>   is Phase 12's "Proactive Runs, Schedules and Instruction Skills"; the
>   home hub ADR is therefore ADR-017 (recorded in the ADR and in owner
>   step 2).
>
> **Owner decision kept (2026-11-26).** There was no stub file for this
> phase on the integration branch (`docs/prompts/phase-13-home-hub/` did
> not exist); the phase was created directly by the owner decision. Its
> content is kept unchanged as this brief's goal, scope and boundary:
> - one always-on Friday server on a **home PC** (Linux, the Docker Compose
>   stack: Django, Postgres+pgvector, Redis, Celery, node-gateway) on the
>   home LAN; Friday is used only at home; every PC (Macs now,
>   Linux/Windows later) is a paired node of that one server, so memory and
>   identity are the same on every PC; reach = LAN only by default,
>   Tailscale optional; node WSS still TLS with a pinned server cert or
>   private CA; full-disk encryption on the server; nightly encrypted
>   `pg_dump` + restic/age to an off-site target (Cloudflare R2 optional)
>   with a tested restore; an explicit later migration path to a VPS;
>   rejected: Supabase, object storage as the database, the server on one
>   Mac;
> - **multi-node as a first-class setup**: node naming, roles, rooms;
>   per-node presence in the HUD; "which PC" resolution for voice and chat
>   (explicit name, last active, ask when ambiguous); per-node capability
>   catalogues; per-node audit; every invocation targets exactly one node,
>   never a broadcast write; ADR-014 classes unchanged;
> - a **Linux node** built from the Python reference node as a separate
>   package with read capabilities and the same conformance suite (a
>   Windows node is a stub, deferred);
> - **smart home** through the existing Home Assistant bridge: media/TV
>   control (power, volume, source, play/pause) and scene/light control as
>   allowlisted HA service calls, behind the approval gate, with an
>   explicit owner-configured allowlist of entities; voice routes
>   ("salondaki TV'yi aç") that only propose;
> - **home only**: no public exposure; preflight checks for the home
>   server (LAN reachability, TLS pin, backup freshness, restore drill age).
> The decision recorded no owner steps; the ones this phase needs are under
> "Owner steps (pending)".
>
> **Platform boundary (owner decision 2026-11-20).** The implementation
> container is Linux, with no macOS and no Xcode.
> - **In scope now, with full branches (1–7 and the closeout):** rooms and
>   node labels, "which PC" resolution, per-node presence and audit
>   surfaces (Django, HUD, Next.js), the Linux node package (pure Python,
>   runs and is tested here against the hostile conformance gateway), the
>   Home Assistant media/scene extension and its entity allowlist (tested
>   against a fake HA server), the home voice routes (tested through the
>   Phase 11 reference voice host and the real `friday-voice` child), the
>   home-server ops artefacts (compose override, backup and drill scripts
>   tested against the CI Postgres, preflight rows).
> - **Not built here:** anything that must be built, signed or run on
>   macOS — the small Swift change to `friday-node` (pairing `platform`,
>   re-pointing the server URL behind Touch ID), the real-microphone run of
>   "which PC" and the home routes, and real-hardware evaluations (the
>   owner's home PC with LUKS and a real restore drill, two real PCs at
>   once). They are listed under "Mac session (deferred)", marked
>   "deferred: Mac session" in the branch table, appended to
>   `docs/runbooks/mac-session-handoff.md` as **Part E**, and must pass the
>   server-side contract tests committed now. A **Windows node** is not
>   built in this phase at all (stub README only, D10).
>
> Go/no-go items that need the Mac, the owner's home PC or real-world use
> are recorded as **pending owner evaluation** and do not block the backlog.
>
> **Precondition.**
> - Phase 12 closeout merged on the integration branch (it is:
>   `chore/p12-closeout`, 2026-11-20). Phase 13 uses the Phase 9 node stack
>   (pairing, protocol v1/v2, gateway, read tools, `/devices`, the HUD node
>   line), the Phase 10 `node_action` gate, the Phase 11 `node_voice`
>   channel and reference voice host, the Phase 12 wake-route table and
>   `tool.*` routes, and the Phase 5 HA bridge.
> - **Execution order:** MASTER_PLAN §AA row 9 — the phase is *used* after
>   the Phase 9 Mac session (B1–B3: a real Swift node exists, so there is
>   something to pair with the home server). Its server branches do not
>   need the Mac and may start once this brief is approved.
>
> **Branches.** Cut from the integration branch, `feat/p13-…` names
> (MASTER_PLAN stable-numbering rule).

## Goal / value

One Friday for the whole home: a quiet Linux box in the cupboard runs the
server; every PC in the house is a node of it; Friday knows which PC and
which room you mean; the TV and the lights are one approved tap away; and
the owner's memory survives a dead disk.

Concretely, at the end of the phase:

1. **The home server.** `friday-home.local` (a mini PC, LUKS-encrypted,
   DHCP-reserved address) runs the daily-driver Compose stack plus the home
   override. Caddy on the host serves the frontend and `/ws/node/v1` over
   TLS from the owner's private CA on the LAN address only; `ufw` admits
   the home subnet only. Every night at 03:10 an `age`-encrypted
   `pg_dump` goes off-site with `restic`; once a month the owner runs the
   restore drill. `make preflight` shows `home_lan_only`, `home_tls_pin`,
   `home_backup_fresh`, `home_restore_drill_age` and `home_disk_encryption`.
2. **Two PCs, one memory.** The owner's MacBook (Swift `friday-node`) and
   the Linux desktop in the study (`friday-node-linux`) are both paired.
   On `/devices` they are named "MacBook" (room *çalışma odası*, role
   *laptop*) and "Masaüstü" (room *çalışma odası*, role *workstation*). The
   HUD's **Düğümler** panel shows both: connected, last heartbeat, voice
   state, invocable capabilities.
3. **"Which PC".** Typed on the web: "Masaüstünde Claude Code oturumlarım
   ne yapıyor?" → the `node_sessions` read goes to *Masaüstü* only (explicit
   name). Said to the MacBook: "Claude Code oturumlarım ne yapıyor?" → the
   speaking node, *MacBook*. Typed with both online and neither named or
   recently active: Friday answers "Hangi bilgisayar: MacBook mu, Masaüstü
   mü?" and calls nothing. "Masaüstünde testleri çalıştır" → a `node_action`
   draft for *Masaüstü* only (it advertises no action capability, so the
   draft is refused with "Masaüstü bu eylemi desteklemiyor" — never moved
   silently to the MacBook).
4. **The TV.** On `/connectors/home-assistant` the owner allowlists
   `media_player.salon_tv` (room *salon*, alias "TV", services `turn_on`,
   `turn_off`, `select_source`, `volume_set`, `volume_mute`, `media_play`,
   `media_pause`) and `scene.film_gecesi` (alias "film gecesi"). "Hey
   Friday, salondaki TV'yi aç" (on the MacBook) → a draft card "Salon · TV ·
   aç (`media_player.turn_on`)"; the owner taps it on screen, approves it on
   `/approvals`, the call runs once and the state is read back. "Film gecesi
   modunu aç" → the scene draft. An entity that is not allowlisted is never
   offered and never called.
5. **The VPS escape hatch.** `docs/runbooks/home-server-setup.md` §11 is a
   tested checklist: same stack, restore from backup, same signing key,
   same CA, re-point each node locally — no protocol change.

## What already exists (verified 2026-11-26 on the integration branch)

- **Deployment.** `deploy/docker/compose.yml` (dev) and
  `compose.daily.yml` (gunicorn, `config.settings.prod`, `next start`,
  node-gateway with prod settings): web, worker (embedded beat),
  `node-gateway` (`uvicorn config.asgi_nodes:application --ws wsproto`,
  port 8002), postgres (`pgvector/pgvector:pg16`, user/db `jarvis`, volume
  pinned to the pre-rename name), redis (AUTH), connector sidecars in the
  `connectors` profile. **Every port is published on `127.0.0.1` only.**
  `make up-daily`, `make preflight`. No backup, no restore and no TLS
  termination exist in the repo; `docs/runbooks/friday-node.md` assumes "the
  TLS reverse proxy that already fronts Friday" (Caddy example) and pins it
  with `NODE_TLS_PIN_SHA256` + `PREFLIGHT_NODE_TLS_URL` (`node_tls_pin`).
- **LAN gating.** `DEVICE_PAIRING_NETWORKS` (loopback, RFC 1918, ULA,
  Tailscale CGNAT) gates PIN pairing (`accounts/devices.py`); the client
  IP is trusted only from the BFF (`FRIDAY_BFF_SHARED_SECRET`,
  `TRUSTED_CLIENT_IP_HEADER`); `DEVICE_PAIRING_ALLOW_UNKNOWN_IP` defaults
  to false in production.
- **Nodes.** `nodes.models.Node` (`id` = its `TrustedDevice` id; `name`
  ≤ 100 chars, set at pairing from `node_name`, no uniqueness;
  `protocol_version`, `pinned_key_id`, `node_version`, `os_version`,
  `last_heartbeat_at`, spool depth, the Phase 11 `voice_*` fields). No
  room, role, platform or alias. `CapabilitySnapshot` is already **per
  node**; `NodeInvocation` (hash-chained, append-only, `node` FK) and
  `NodeActionExecution` are per node. Presence is the Redis key
  `nodes:presence:<node_id>` (`nodes/presence.py`, TTL 45 s).
  `POST /api/v1/nodes/pair` (`NodePairView`, `NodePairSerializer`:
  `node_name`, `protocol_versions`), `GET /nodes`, `GET /nodes/<id>`
  (`NodeDetailView` has no `PATCH` today), `/revoke`, `/actions`, the invocation log
  (`nodes.selectors.invocation_page`).
- **Which node today.** `nodes.selectors.read_node_for(owner)` picks *one*
  node for every read tool: the first usable one with a presence key, else
  the most recently paired usable one (`tools/node_reads.py`
  `_execute` / `_available`). `nodes.selectors.node_health(owner)` ranks
  the owner's nodes and returns **one** block, which
  `hud.services.node_section` renders as the HUD's single node line. For
  actions, `tools.node_actions.proposal_targets` lists the owner's nodes
  with their invocable action capabilities and the router's
  `router.node_action_args` call (`assistant/services.py`
  `propose_node_action`) **lets the model pick `node_id`**;
  `validate_proposal` checks only that the node is the actor's and the
  capability invocable there. Nothing knows which node a `node_voice` turn
  came from, although the token carries it
  (`PersonalAccessToken.node_device_id`, `accounts/authentication.py`).
- **Reference node.** `backend/nodes/reference/` (pure Python: `node`,
  `transport` over `wsproto`, `readers` for the eight v1 read capabilities
  over a fixture home, `local_policy`, `spool`, `hook_relay`, `verifier_v2`,
  `confirm`, `executor`, `sandbox`); deps `cryptography`, `jsonschema`,
  `wsproto`; import-linter contracts keep it free of project packages.
  `backend/integrations/node_reference/` holds the 8B owner-turn readers.
  The conformance suite (`nodes.conformance`, `build_node_conformance
  --check`) and `manage.py node_conformance_server [--external]
  [--tls-self-signed] [--protocol 1|2]` (the hostile fake gateway an
  external node connects to) exist; `node/friday-voice/` shows the pattern
  for a separately packaged uv project with its own `conformance.lock`.
- **Home Assistant.** `connectors/bridges/home_assistant.py`: native REST,
  `SERVICES` is the code maximum (`light` on/off with brightness and
  colour temperature, `switch`, `fan`, `cover`, `climate`, `media_player`
  `media_play` / `media_pause` / `volume_set`, `scene.turn_on`,
  `wake_on_lan.send_magic_packet`), `NEVER_DOMAINS`, `READ_ONLY_DOMAINS`,
  `ATTRIBUTE_WHITELIST` (no `source`/`source_list`), `HA_ALLOWED_DOMAINS`
  narrows by domain, `HA_WOL_ALLOWED_MACS`; **no per-entity allowlist**:
  any entity of an allowed domain may be the target of a draft.
  `connectors/manifests/home_assistant.py`: manifest v1, `ha_states`
  (read, web only) and `ha_call_service` (gate-class, user-invocable,
  `renderer`, `request_rows`, `precondition=target_present`,
  `records_work_log=False`), pins `STATES_PIN` / `CALL_SERVICE_PIN` over
  `tool_schemas()`. No HA call is confirm-class (Phase 5 Q7).
- **Voice.** `assistant/wake_routes.py`: the deterministic route table
  (effects `read` / `proposal` only, boot-validated), `normalize_speech`
  (Turkish lower-casing, NFC), owner aliases (`WakeRouteAlias`), `tool.*`
  routes for user-invocable gated tools that yield the same `tool_request`
  draft card the router would; voice turns only (web voice and
  `node_voice`); `apply_voice_capability_cap` (ADR-015).
- **HUD.** `hud.services.SECTIONS = ("approvals", "tasks", "cost",
  "health", "terminal", "notices", "digest")`; the `health` section embeds
  the single `node` line. Frontend: `components/hud/HudPanels.tsx`,
  `HudNodeLine`, `components/nodes/NodesSection.tsx` and `NodeDetail.tsx`
  (`/devices`, `/devices/nodes/<id>`), `components/approvals/renderers/
  node-action.tsx` and `connector-write.tsx`.
- **Preflight.** `ops/preflight.py` `CHECKS` (… `nodes`, `control`,
  `live_voice`, `proactive`, `n8n`, `skills`); `check_nodes` rows
  `node_gateway`, `node_signing_key`, `node_link`, `node_tls_pin`,
  `node_audit_chain`, `node_conformance`. `manage.py
  verify_node_audit_chain` walks the chain.

## Boundary vs existing systems

- **Not a second server, not a sync.** There is one server. A node keeps
  no Friday data beyond its credential, local policy and spool (ADR-013).
- **No new protocol surface.** No WS message type, no schema field, no
  capability contract, no `friday-voice/v1` change. `build_node_conformance
  --check` and `node/friday-voice/conformance.lock` are unchanged by every
  Phase 13 branch (a closeout check). The pairing HTTP endpoint gains one
  optional field (`platform`, D3) — an HTTP request field the existing
  Swift app need not send.
- **No fan-out.** No code path sends one request to several nodes (D7).
  "What runs on all my PCs" is answered from the server-side, hook-fed
  `memory.CodingSession` read model (8A sessions panel), never by
  invoking every node.
- **No new gateway purpose, no model call.** Resolution and home routes
  are code. The existing `router.intent`, `router.connector_args` and
  `router.node_action_args` calls are unchanged in number; their prompts
  gain data (node names, rooms), not instructions (D7, D13).
- **Home Assistant stays a connector.** Friday calls HA's REST API through
  the bridge; it does not become a HA integration, does not run
  automations or scripts (still `NEVER_DOMAINS`), and does not read HA's
  areas as authority — rooms are Friday's own owner-set rows (D2).
- **Backups are not a Friday action.** The backup timer is a host
  operation the owner configures (ADR-017 decision 6); no agent, tool,
  schedule or model can start, change or read a backup. Preflight reads a
  status file the script writes; it never reads a dump.
- **Workplace products (ADR-006) and the fleet layer (§P)** are untouched.

## Decisions (with justification)

### D1 — The topology is ADR-017; the repo ships the artefacts, not the box

The server is one home Linux PC running the existing Compose stack
(ADR-017 decisions 1–7). The repo provides what makes that box repeatable
and checkable: `docs/runbooks/home-server-setup.md` (written in this
branch), `deploy/docker/compose.home.yml`, `deploy/home/` scripts and the
`home_*` preflight rows (branch 7). Buying, installing and encrypting the
box is an owner step.

*Justification:* the stack already runs on any Docker host; what was
missing was a decided place, LAN-only reach, TLS, and a backup that is
proven by restoring it. Keeping the box itself out of code avoids
pretending a container can attest LUKS or a router reservation.

### D2 — Rooms: `workspaces.Room`, personal workspace only

```
Room(WorkspaceScopedModel, TimestampedModel)
  workspace  FK workspaces.Workspace (PROTECT; type must be personal)
  name       CharField(60)            # "Salon", "Çalışma odası"
  key        CharField(60)            # core.speech.match_key(name), unique per workspace
  aliases    JSONField(list[str], ≤ 5, each 2–40 chars, keys unique per workspace)
  sort_order PositiveSmallIntegerField
```

- Created, renamed and deleted only by the owner over the web
  (`core.owner_web.require_owner_web`), audited (`home.room_created` /
  `_renamed` / `_deleted`, ids and keys only). A room in use by a node or
  a home entity is not deletable (`409 room_in_use`).
- `POST/GET /api/v1/workspaces/<id>/rooms`, `PATCH/DELETE
  /api/v1/workspaces/<id>/rooms/<room_id>`; a firm workspace answers
  `404` (rooms are personal: a firm has no "salon").
- The Turkish locative forms ("salonda", "salondaki", "çalışma
  odasındaki") are matched by `core.speech.match_room` (D6), not stored.

*Justification:* both `nodes` and `connectors` need rooms, and the
import-linter contracts forbid either from importing the other
("nodes may import only approvals, audit, workspaces, accounts, core";
"connectors may import only core, workspaces, audit, ai_core, tools,
memory"). `workspaces` is the one kernel app both may import, and a room
is a property of the owner's personal space. HA's own areas are not
reused: they are HA's configuration, readable by anyone holding HA admin,
and would make Friday's resolution depend on a remote, mutable source.

### D3 — Node labels: unique name, room, role, platform, aliases

New `Node` fields (nodes migration 0005):

| Field | Type | Set by |
|---|---|---|
| `name` | existing; now unique per owner on `name_key` | pairing (`node_name`), then the owner on `/devices` |
| `name_key` | `CharField(100)`, `core.speech.match_key(name)`, indexed; unique per owner **among usable nodes** (not revoked, killed or expired, `accounts.devices.node_device_refusal`), checked in `nodes.services` under `select_for_update` on the owner's nodes at pairing and at relabel. No DB constraint: revocation lives on `TrustedDevice` (also flipped by `accounts.revoke_all_devices`, which may not import `nodes`), so a partial index cannot express it; `nodes_labels` (D15) catches a duplicate | server |
| `room` | FK `workspaces.Room`, `PROTECT`, nullable (a room in use is not deletable, D2) | the owner |
| `role` | `workstation` / `laptop` / `media` / `server` / `other` (default `other`) | the owner |
| `platform` | `macos` / `linux` / `windows` (default `macos`) | pairing (`platform`, optional), never edited afterwards |
| `aliases` | JSON list ≤ 5, like `Room.aliases`; keys unique across the owner's node names and aliases | the owner |
| `last_active_at` | `DateTimeField(null)` | server (D6) |

- `NodePairSerializer` gains optional `platform` (choices above; absent =
  `macos`, so the existing Swift app pairs unchanged). A duplicate
  `node_name` at pairing gets a numeric suffix ("MacBook 2"), never a
  refusal (pairing must not fail on a label).
- `PATCH /api/v1/nodes/<id>` `{name?, room_id?, role?, aliases?}`: owner,
  web channel, the node's owner only; `node`/`node_voice`/`cli`/`hook`/
  `openai` tokens, members, agents and the low-trust channels are refused
  (`403`); audited `nodes.relabelled` with old/new keys. **No server
  message is sent to the node**: labels are server-side display and
  resolution data, not node policy (ADR-013 decision 5 holds).
- Migration: existing nodes get `name_key` from `name`; duplicates among
  an owner's nodes are suffixed in the data migration (recorded in the
  branch notes); `platform = macos`.

*Justification:* resolution by name (D6) needs names that cannot collide;
rooms make "salondaki" work for PCs and devices alike; `role` lets the HUD
group and lets the owner say "medya bilgisayarı" (`role` words are aliases
of the role, D6). `platform` explains why a Linux node offers no actions.
Keeping labels out of the protocol means no Swift change for any of it.

### D4 — Per-node presence: the HUD `nodes` section

- `nodes.selectors.node_presence(owner, *, now=None) -> list[dict]`
  through `nodes_for()` (`for_actor()`): one entry per non-revoked node
  (revoked nodes listed only on `/devices`), ordered connected first, then
  by room `sort_order`, then name: `{id, name, room, role, platform,
  status, last_heartbeat_at, last_active_at, invocable_read_count,
  invocable_action_count, voice: voice_summary(...)}`. No path, no
  capability argument, no output. At most `NODE_PRESENCE_MAX` (16).
- `hud.services.SECTIONS` gains `nodes`; `nodes_section(actor)` renders
  the list. The existing `health.node` line is kept unchanged (the "most
  relevant node", for the compact HUD) so existing clients and tests stay
  valid.
- Frontend: `HudNodesPanel` (one row per node: name, room badge, platform
  icon, status dot with text — never colour alone — voice state, counts);
  the HUD layout shows it when more than one node exists and keeps
  `HudNodeLine` otherwise.

*Justification:* "per-node presence in the HUD" is asked for directly;
the one-block `node_health` was Phase 9's "allowed but not optimised"
compromise. Adding a section instead of reshaping `health.node` keeps the
Phase 9–11 HUD contracts intact.

### D5 — Per-node capability catalogues are already per node; show and use them

`CapabilitySnapshot` is per node and `invocable_entry` is per (node,
contract). Phase 13 adds no storage: it (a) makes availability per node
(`nodes.selectors.invocable_capabilities(node)` is the input of D6's
filter), (b) lists every node's invocable capabilities in the router's
targets block with its name and room (data), and (c) shows each node's
catalogue with its verdicts on `/devices/nodes/<id>` (exists) plus a
"which nodes can" column on `/devices`. A read tool `is_available` when at
least one usable, online node has one of its capabilities (today: the one
node `read_node_for` picks).

### D6 — "Which PC": one deterministic resolver in `nodes.targeting`

```
resolve_target(owner, *, capabilities, source_text="", origin_node_id=None,
               requested_node_id=None, now=None) -> Target
Target = Resolved(node, reason) | Ambiguous(candidates, reason)
       | Refused(code, node=None)
```

Order (first rule that decides wins):

1. **Explicit.** `requested_node_id` (a node id from a typed card or the
   router's argument output) or a **mention** in `source_text`:
   `core.speech.find_mentions(source_text, vocabulary)` over the owner's
   node names, aliases, role words (`media` → "medya bilgisayarı", …) and
   room names in locative form ("salondaki bilgisayar", "çalışma
   odasındaki Mac"), whole-word, longest match wins, normalized with the
   wake-route normalizer.
   - one node matched → that node if usable, online and it has one of
     `capabilities`; otherwise **`Refused`** with that node named
     (`node_offline`, `node_lacks_capability`, `node_unusable`) — **never
     a silent fall-through to another node**;
   - a room or role matched with several nodes → `Ambiguous` among them;
   - `requested_node_id` and a mention disagree → `Refused
     (target_mismatch)`: the model's choice never overrides the owner's
     words.
2. **The speaking node.** `origin_node_id` (a `node_voice` turn: the
   token's `node_device_id`) when that node is usable, online and has a
   capability → it (`reason=origin`).
3. **Last active.** Among usable, online nodes with a capability: exactly
   one → it (`reason=only_candidate`); several → the one whose
   `last_active_at` is within `NODE_LAST_ACTIVE_WINDOW_SECONDS` (default
   900) and strictly later than every other candidate's
   (`reason=last_active`).
4. **Ask.** Otherwise `Ambiguous(candidates)`: the turn calls no node and
   answers with a fixed template listing the candidate names ("Hangi
   bilgisayar: MacBook mu, Masaüstü mü?"); the owner's reply naming a PC
   is a new turn (the router sees the conversation history as it does
   today) whose own text resolves by rule 1. No candidate at all →
   `Refused(not_available)`.

`last_active_at` is written (at most once a minute per node, like
`last_heartbeat_at`) when the owner demonstrably used that PC: a
node-relayed hook event (`nodes.events`, Claude Code activity on it), a
`node_voice` transcription request from its token, and a confirmation
click result on its window. Heartbeats never count (every online node
heartbeats).

Callers:
- **Read tools** (`tools/node_reads.py`): `read_node_for` is replaced by
  `resolve_target`; the chat turn passes a `ToolCallContext(source_text,
  origin_node_id, input_mode)` (new, `tools.registry`, optional keyword so
  every other `ReadToolSpec` is untouched); the router's connector-args
  output may carry an optional `node` name, treated as untrusted and
  checked by rule 1's mismatch test. An `Ambiguous` result becomes the
  `ReadToolError("node_ambiguous")` the turn renders as the ask template;
  the audit row records `node_ambiguous` and the candidate ids.
- **Node actions** (`tools.node_actions.proposal_targets` /
  `validate_proposal`): the targets block marks each node's name and room;
  `validate_proposal` runs `resolve_target` with the model's `node_id` as
  `requested_node_id` and the owner's message as `source_text`; a mismatch
  or ambiguity yields no draft (plain-chat ask). The approval request's
  payload keeps exactly one `node_id`, as today.
- **`node_voice` turns** pass `origin_node_id` from
  `request.auth.node_device_id` (`assistant/views.py`), through the tools
  port (assistant never imports `nodes`, Phase 9 D2 contract).

*Justification:* the owner asked for "explicit name, last-active, ask when
ambiguous". Code-only resolution is testable, costs no model call and
cannot be argued with by injected text: a transcript that says "use the
other PC" is data in a fenced block, never `source_text` (which is only
the owner's own message). Refusing rather than falling through prevents
the one surprising outcome that matters: a command run on the wrong PC.

### D7 — Exactly one node per invocation; no broadcast

- `nodes.services.invoke_read`, `invoke_confirm` and `dispatch_gated`
  already take one node; Phase 13 pins it: a test walks every registered
  `ToolSpec` / `ReadToolSpec` with two online nodes and asserts each tool
  call produces chain rows for **one** `node_id` only, and `NodeAction
  Execution` keeps one `node_id` FK (no list field anywhere).
- The grant is bound to `node_id` (protocol v2); the multi-node e2e
  proves a grant for node A presented by node B is refused `wrong_node`
  and that an approval for A never dispatches to B.
- No "all PCs" variant of any tool in Phase 13 (Out of scope).

### D8 — Per-node audit

- `GET /api/v1/nodes/<id>/audit?before=<cursor>`: the node's chain rows
  (`invocation_page`, exists) merged with its `AuditLog` rows
  (`nodes.paired`, `nodes.revoked`, `nodes.relabelled`, kill switch,
  credential rotation, `nodes.refused`, `node_ambiguous` refusals,
  `voice.live_muted` for its device), newest first, `for_actor()`, owner
  only. Codes, hashes, names, never args or output content.
- `NodeDetail` gains a "Denetim" tab over it.
- Every new audit row carries `node_id` in its metadata so the filter is
  an index lookup (`AuditLog` metadata JSON; a GIN or expression index if
  the branch measures a need).

### D9 — ADR-014 per node, unchanged

- Classes do not change. A gate-class action is approved on `/approvals`
  and clicked on the **target node's own** confirmation window; the
  approval card (`node-action.tsx`) shows the node's name, room and
  platform in its header so the owner approves "Masaüstü (çalışma odası,
  Linux)", not an id.
- A Linux node is v1 and advertises read capabilities only; a v2 action
  contract advertised by a v1 node is already classified not invocable
  (`classify_catalogue` by protocol version). A test pins that the Linux
  node's catalogue yields zero invocable action capabilities.
- The node kill switch and revoke stay per device; "revoke all" stays
  `revoke_all_devices`.

### D10 — The Linux node: `node/friday-node-linux`, a separate package

A productionised node built from the reference node, **read-only**:

- **Package.** `node/friday-node-linux/` is its own uv project (Python
  3.12, exact pins: `cryptography`, `jsonschema`, `wsproto`; nothing
  else), console script `friday-node-linux`, like `node/friday-voice/`.
- **Shared code, vendored and locked.** It must not import the backend.
  `scripts/vendor.py` copies `backend/nodes/protocol/`,
  `backend/nodes/capabilities/v1/`, `backend/nodes/reference/{node,
  transport,readers,local_policy,spool,hook_relay,_scrubber_vendored}.py`
  and the 8B owner-turn readers and contracts
  (`backend/integrations/node_reference/owner_turns.py`,
  `backend/integrations/node_reference/contracts/v1/`) into
  `friday_node_linux/_vendor/` and writes `vendor.lock` (sha256 per
  file); `vendor.py --check` (CI) fails on drift, so the reference node
  stays the single spec and the Linux node cannot diverge silently.
- **Capabilities.** The v1 read set over the **real** home directory:
  `claude.sessions.list/read`, `codex.threads.list/read`,
  `usage.ccusage` (when `ccusage` is on `PATH`, else `availability:
  unavailable`), `git.status` (repos in the local policy only), and the 8B
  owner-turn reads (`claude.transcripts.owner_turns`,
  `codex.transcripts.owner_turns`). `apps.frontmost` and `windows.list`
  are advertised `unavailable` (no portable, consent-respecting Wayland
  equivalent; X11 is not worth a special case). No v2 capability.
- **Local policy, local only.** `~/.config/friday-node/policy.json`
  (0600): per-directory read grants (off by default, ADR-013 decision 11),
  registered repos. Changed only by `friday-node-linux grant|revoke|repo
  add|repo remove`, run by the local user at an interactive TTY, which
  shows the exact change and requires typing the directory's basename to
  confirm — the Linux stand-in for Touch ID. The CLI writes the file
  atomically (temp file + rename, 0600); the daemon re-reads it on
  `SIGHUP` (only the same uid or root can signal it); no protocol message
  can reach it.
- **Credential.** The node secret is stored in
  `$XDG_STATE_HOME/friday-node/credential` (0600, refused if group/other
  readable or owned by another uid); Secret Service storage is a later
  option (Q4). Pinned at pairing: the server signing key and the TLS SPKI
  or private CA certificate (`--ca`), exactly as the Swift node.
- **Process.** `friday-node-linux pair --server https://friday-home.local
  --code <code>`; `friday-node-linux run` as a systemd **user** unit
  (`deploy/home/friday-node-linux.service`, `Restart=on-failure`,
  `NoNewPrivileges=yes`, `ProtectSystem=strict`, `ReadWritePaths` = its
  state dir); `friday-node-linux hook` (the Claude Code hook relay into its
  0600 Unix socket, same peer-uid check); `config set server-url` (the VPS
  re-point, ADR-017 decision 7; keeps the pins, refuses a server whose
  pins differ).
- **Conformance.** CI runs `manage.py node_conformance_server --external
  --tls-self-signed --protocol 1 --hook-socket …` with
  `friday-node-linux conformance --hook-socket …` connecting to it; every
  v1 case must pass, plus the compromised-server family (unknown
  capability, out-of-schema args, replay, bad signature, policy-mutation
  frame). The same suite the Swift app passes.
- **Windows.** `node/friday-node-windows/README.md` only: why it is
  deferred (d1: Tauri v2 "only if a Windows node is ever needed"), the
  contract it must pass (this conformance suite), and that it needs its
  own brief. No code.

*Justification:* the reference node already implements the whole v1 node
side and is pure Python; turning it into a daemon is packaging, policy
storage and a real reader root, not a rewrite. Vendoring with a lock (the
`friday-voice` and `friday-hook` pattern) keeps one spec. Read-only keeps
ADR-014 trivially satisfied: a Linux executor would need its own
sandbox evidence (the `srt` question) and is a later brief.

### D11 — Home Assistant: an owner-configured entity allowlist

```
HomeEntity(WorkspaceScopedModel, TimestampedModel)       # connectors
  workspace  FK (personal only)
  entity_id  CharField(141)  # ENTITY_RE; unique per workspace
  room       FK workspaces.Room (PROTECT, nullable)
  label      CharField(60)   # "TV", "tavan lambası"
  aliases    JSON list ≤ 5
  services   JSON list ⊆ SERVICES[domain] (code maximum ∩ HA_ALLOWED_DOMAINS)
  enabled    bool
```

- `ha_call_service` is refused unless `entity_id` is an **enabled**
  `HomeEntity` of the request's workspace and `service` is in its
  `services` (`entity_not_allowed` / `service_not_allowed`), at draft
  time (`validate`), at request time and again as the execution
  precondition (`target_present` grows the allowlist check), so removing
  an entity cancels a pending approval's execution (`connector_changed`).
- **Fail closed for existing installs:** an empty allowlist means no HA
  call is possible. The `ha_entities` preflight row (D15, closeout) warns
  "no entity allowlisted"; the branch notes and
  `docs/mcp/home-assistant.md` say so.
- Owner-web CRUD at `/api/v1/connectors/home-assistant/entities`
  (`require_owner_web`; agents, tokens, members, low-trust channels
  refused; audited `ha.entity_allowed` / `_changed` / `_removed`). The
  editor (`/connectors` → Home Assistant) offers entities from a live
  `ha_states` read (allowed domains) so the owner picks real ids; adding
  one is the owner's act, never a suggestion auto-applied.
- `ha_states` reads are unchanged in scope (allowed domains); allowlisted
  entities are annotated with room and label.
- An agent can never change the allowlist (CLAUDE.md rule 4): no tool,
  route, schedule or connector writes `HomeEntity`; a registry walk test
  pins it.

*Justification:* the owner asked for an explicit entity allowlist; a
domain allowlist lets "media_player" mean every speaker, the neighbour's
Chromecast HA discovered, or a test entity. Per-entity services also let
the owner allow "volume" on the TV without "off" on the router's smart
plug.

### D12 — Media/TV, scene and light services; all gate-class

`SERVICES` grows (code maximum; `HA_ALLOWED_DOMAINS` and the entity's
`services` still narrow):

| Domain | Added services | `data` serializer |
|---|---|---|
| `media_player` | `turn_on`, `turn_off`, `select_source`, `volume_mute`, `volume_up`, `volume_down` | `SelectSourceData{source}` — must equal one of the entity's live `source_list` values, read at request time and re-checked as the precondition; `VolumeMuteData{is_volume_muted: bool}`; the rest `NoData` |
| `light` | (unchanged: `turn_on` with brightness/colour temperature, `turn_off`) | — |
| `scene` | (unchanged: `turn_on`) | — |

- `ATTRIBUTE_WHITELIST` gains `source`, `source_list` (bounded: ≤ 30
  items, each ≤ 60 chars, invisible formatting stripped). `media_title`
  and `entity_picture` stay out (content and token-bearing URLs).
- Toggles stay absent (`media_play_pause`, `toggle`): their outcome
  depends on state at execution, so the dialog could not say what happens.
- Pins and manifest: `tool_schemas()` changes, so `CALL_SERVICE_PIN`,
  `STATES_PIN` and the manifest `version` (1 → 2) change in the same
  reviewed commit; `docs/mcp/home-assistant.md` and the allowlist doc are
  updated (MCP governance).
- **Classification: every call stays gate-class** — an `ApprovalRequest`
  decided on `/approvals`, executed at most once, state read back. The
  approval dialog (`request_rows`) shows room, label, entity id, service
  and data, plus the live current state.

*Justification:* a TV or a light is outside Friday: not an in-app row, not
reversible by Friday's trash, and the confirm-class list grows only by an
ADR (ADR-008 decision 3, ADR-009, Phase 5 Q7). The cost is one tap on
`/approvals` per change; Q1 records whether a narrow confirm-class
exception for volume/play/pause should ever be proposed (recommended: not
before two weeks of real use show the approval count is a burden).

### D13 — Home voice and chat routes that only propose

- **`home.control` wake route** (voice turns: web voice and `node_voice`),
  effect `proposal`, added to `assistant/wake_routes.py` and validated at
  boot like every route. Its matcher is code in `connectors.home_phrases`
  (Turkish first, English second):
  `"<room>(da|de|ta|te)(ki)? <label|alias> (aç|kapat|sesini aç|sesini
  kapat|sesini kıs|sesini yükselt|duraklat|devam ettir|oynat|<source>(a|e|
  ya|ye) geç|<n>(e|a) ayarla)"`, `"<scene alias> (modunu|sahnesini)? aç"`,
  and the English equivalents ("turn on the living room TV"). The verb
  table maps to exactly one `(service, data)`; a number is accepted only
  where the serializer has a bounded number (volume 0–100 % → 0–1).
- **Resolution** against the owner's `HomeEntity` rows (room + label/alias,
  or a unique label): one match → a `tool_request` draft card for
  `ha_call_service` with those exact arguments; several → the ask
  template ("Salonda iki TV var: Salon TV, Projeksiyon — hangisi?"); none
  or a service not allowed for that entity → a fixed refusal
  ("Bunu yapmaya iznim yok: Salon TV için 'kapat' açık değil.") with no
  draft. The draft goes through the normal user-invocable gated tool path
  (the owner taps the card, then decides on `/approvals`).
- **Typed turns** are not wake-routed (Phase 12 rule kept). The router's
  `tool_request` → `router.connector_args` path drafts the same card; the
  draft is validated against the allowlist (D11), and the server offers
  the resolved entity when the model's `entity_id` disagrees with a
  room/label mention in the owner's message (`target_mismatch`, no draft).
- The cap: `apply_voice_capability_cap` still runs; nothing in a route can
  approve or execute (ADR-015 decision 1). An implementation note is added
  to ADR-015 by the branch.
- **PC words** ("çalışma odasındaki Mac'te testleri çalıştır") are not a
  home route: they go through the existing router + D6.

*Justification:* "salondaki TV'yi aç" must be fast and must never be
guessed by a model; the existing wake-route machinery gives a no-model,
boot-validated, proposal-only path. Matching on Friday's own room/label
rows (not HA's friendly names) keeps the vocabulary owner-set.

### D14 — Home-server ops artefacts and status files

- `deploy/docker/compose.home.yml` (override on top of `compose.yml` +
  `compose.daily.yml`): `HOME_*` settings into web/worker, read-only bind
  of the status directory `/var/backups/friday/status/` (holding only
  `status.json` and `drill.json`) into web, nothing
  published on `0.0.0.0` (a test parses every compose file and fails on a
  non-loopback `ports:` entry).
- `deploy/home/friday-backup.sh`, `friday-restore-drill.sh`,
  `friday-backup.{service,timer}`, `Caddyfile.example` — the runbook's
  scripts, made shellcheck-clean, with the status-file format pinned:
  `status.json {v:1, finished_at, dump, bytes, sha256, restic_snapshot}`,
  `drill.json {v:1, drilled_at, dump, result}`.
- Tests run the backup script against the CI Postgres with a throwaway age
  key and a local restic repository (no network), then the drill into a
  scratch database, and assert both status files and that the restored
  database passes `migrate --check` and `verify_node_audit_chain`. If
  `age`/`restic` are not installed in CI, the test installs pinned
  binaries in the job or is marked to run in the dedicated `home-ops` CI
  job (recorded in the branch notes); never against a real bucket.

### D15 — Preflight rows for the phase

Added to `ops/preflight.py` (`check_home` in branch 7; `check_nodes`
extended and a `check_home_control` for HA in the closeout):

| Row | Pass | Warn | Fail |
|---|---|---|---|
| `home_lan_only` | every `DJANGO_ALLOWED_HOSTS` entry and the `PREFLIGHT_NODE_TLS_URL` host resolve only to loopback, RFC 1918, ULA or Tailscale CGNAT addresses | URL not set (dev) | a public address, `*` or an empty host list in prod |
| `home_tls_pin` | the LAN URL serves `NODE_TLS_PIN_SHA256` (reuses `node_tls_pin`'s comparison) | not configured | mismatch / handshake error |
| `home_backup_fresh` | `status.json` ≤ `HOME_BACKUP_WARN_HOURS` (26) old, well-formed | 26–`HOME_BACKUP_FAIL_HOURS` (72), or not configured | older, malformed |
| `home_restore_drill_age` | `drill.json` ≤ 35 days, `result: pass` | 35–90 days, or not configured | > 90 days or last drill failed |
| `home_disk_encryption` | `HOME_FDE_CONFIRMED_ON` set | not set | — |
| `nodes_labels` | every non-revoked node has a unique name; rooms set when > 1 node | a node without a room (multi-node) | duplicate `name_key` (should be impossible) |
| `ha_entities` | ≥ 1 enabled `HomeEntity` and each still exists in HA (one `ha_states` read) | HA not configured / none allowlisted | an allowlisted entity outside the code maximum or a NEVER domain |
| `linux_node_vendor` | `node/friday-node-linux/vendor.lock` matches the backend sources | checkout absent | drift |

Every row is read-only; none sends anything to a node or calls a model.

### D16 — Import contracts and shared text helpers

- `core/speech.py` (stdlib only): `normalize_speech`, `match_key`,
  `find_mentions(text, vocabulary)`, `match_room(text, rooms)` — moved from
  `assistant/wake_routes.py` (which re-exports them; the shared vectors
  `docs/voice/wake-route-vectors.json` keep passing) so `nodes` and
  `connectors` can use the same normalizer without importing `assistant`.
- No new edges between kernel apps: `nodes → workspaces`, `connectors →
  workspaces`, `tools → nodes`, `assistant → tools` all exist. `hud →
  nodes` exists. A contract is added: `node/friday-node-linux` is outside
  `backend/` and a purity test (subprocess import) proves its package
  imports no backend package.

## ADR

- **ADR-017 (new, Accepted 2026-11-26): "Home hub topology — one home
  server, many nodes"**, written in this branch
  (`docs/adr/ADR-017-home-hub-topology.md`). Numbered 017 because ADR-016
  is taken (see the header).
- ADR-013, ADR-014, ADR-015: **not amended.** Branch 2 adds an ADR-013
  implementation note (resolution and one-node invariant); branch 5 a
  `docs/mcp/home-assistant.md` governance update; branch 6 (home routes)
  an ADR-015 implementation note; the closeout an ADR-017 closeout note.

## Scope (one branch each)

### 1. `feat/p13-node-registry` — rooms, node labels, platform, per-node audit API

**Scope.** D2, D3, D8 (API), D16 (`core.speech`):
- `workspaces.Room` + migration, owner-web services and views, audit
  events;
- `nodes.Node` fields `name_key`, `room`, `role`, `platform`, `aliases`,
  `last_active_at` (migration 0005 with the duplicate-name data step);
- `NodePairSerializer.platform`; duplicate-name suffixing at pairing;
- `PATCH /api/v1/nodes/<id>` (labels), `GET /api/v1/nodes/<id>/audit`;
- `core/speech.py` (moved normalizer + `find_mentions` / `match_room`),
  `assistant/wake_routes.py` re-exporting;
- frontend: `/settings/home` (rooms editor), `/devices` node edit dialog
  (name, room, role, aliases), `NodeDetail` "Denetim" tab.

**Files.**
- `backend/workspaces/{models,services,views,urls,serializers}.py`,
  `backend/workspaces/migrations/0003_room.py`,
  `backend/workspaces/tests/{test_rooms,test_rooms_api}.py`;
- `backend/nodes/{models,serializers,services,selectors,views,urls}.py`,
  `backend/nodes/migrations/0005_node_labels.py`,
  `backend/nodes/tests/{test_node_labels,test_node_audit_api,test_pairing_platform}.py`;
- `backend/core/speech.py`, `backend/core/tests/test_speech.py`,
  `backend/assistant/wake_routes.py`;
- `frontend/src/app/(shell)/settings/home/page.tsx`,
  `frontend/src/components/home/RoomsEditor.tsx` (+ test),
  `frontend/src/components/nodes/{NodesSection,NodeDetail,NodeLabelDialog}.tsx`
  (+ tests), `frontend/src/lib/api/` types.

**Tests.**
- Rooms: CRUD; key uniqueness across names and aliases; `room_in_use`;
  firm workspace `404`; two owners isolated (`for_actor()`).
- Labels: uniqueness of `name_key` per owner among non-revoked nodes
  (a revoked node's name may be reused); aliases may not collide with
  another node's name or alias; pairing with a duplicate name suffixes;
  `platform` absent → `macos`, invalid → `400`; the data migration on two
  same-named nodes.
- No protocol effect: relabelling sends nothing on the bus
  (`nodes.bus` spy) and the node's next `hello` is unaffected.
- Audit API: chain rows + `AuditLog` rows of that node only, newest
  first, paging, no args/output fields.
- Role × endpoint: rooms, node `PATCH`, node audit × the five Phase 4
  principals, `cli`/`openai`/`hook` PATs, node secret, `node_voice` token,
  kill-switched owner, a firm member, another owner's node (`404`).
- `core.speech`: the wake-route vectors pass through the moved functions;
  Turkish locatives ("salondaki", "çalışma odasındaki", "Masaüstünde")
  match; `lint-imports` shows `core` still stdlib-only.
- vitest: the rooms editor, the label dialog, the audit tab.

**Done when:** the owner can create rooms, name/room/role/alias each node
on `/devices`, a Linux pairing records `platform=linux`, each node's audit
is listed, and every role × endpoint test is green.

### 2. `feat/p13-node-targeting` — "which PC", one node per call

**Scope.** D5 (availability), D6, D7, D9 (approval header data):
- `nodes/targeting.py` (`resolve_target`, the `Target` types, the ask
  template data);
- `last_active_at` writers: hook events in `nodes/events.py`; the
  confirmation result in `nodes/services.record_gated_progress`; the
  `node_voice` transcription request through a callable that
  `config/urls.py` injects into the `voice` views next to the existing
  `node_voice_permissions=` injection (`voice` may import only `core`,
  `workspaces`, `ai_core`, `assistant`, so it imports nothing new; the
  composition root may import `nodes`);
- `tools.registry.ToolCallContext` (optional keyword on `ReadToolSpec`
  execution and `is_available`), `assistant/services.py` /
  `assistant/views.py` passing `source_text`, `origin_node_id`,
  `input_mode`;
- `tools/node_reads.py` on `resolve_target` (replacing `read_node_for`),
  the `node_ambiguous` ask reply, optional untrusted `node` argument;
- `tools/node_actions.py` `proposal_targets` (names, rooms, platform) and
  `validate_proposal` (resolver check), `describe_request` adding
  `node_name`, `node_room`, `node_platform`;
- the targets block the router call already appends as data
  (`assistant/services.py` `_node_action_targets_block`) carries each
  node's name, room and platform; the prompt text, purpose and prompt
  version are unchanged (data, not instructions);
- ADR-013 implementation note.

**Files.**
- `backend/nodes/targeting.py`, `backend/nodes/events.py`,
  `backend/nodes/services.py`, `backend/nodes/selectors.py`,
  `backend/nodes/tests/{test_targeting,test_last_active}.py`;
- `backend/tools/{registry,node_reads,node_actions}.py`,
  `backend/tools/tests/{test_node_targeting_reads,test_node_targeting_actions,test_one_node_invariant}.py`;
- `backend/assistant/{services,views}.py`,
  `backend/assistant/tests/test_which_pc.py`;
- `backend/voice/views.py` (port call only), `backend/config/urls.py`
  (injection);
- `frontend/src/components/approvals/renderers/node-action.tsx` (+ test);
- `docs/adr/ADR-013-friday-node-protocol.md` (note).

**Tests.**
- Resolver table (one test per row of D6 with two and three nodes):
  explicit name / alias / room-with-one-node / room-with-two-nodes (ask) /
  role word; explicit offline or lacking capability → `Refused` naming it
  (never another node); model id vs mention mismatch → `target_mismatch`;
  origin node wins over last-active; origin offline → falls to rule 3;
  last-active within/outside the window, ties → ask; no candidate →
  `not_available`; revoked/killed/expired nodes never candidates.
- Injection: a fenced connector result or transcript saying "Masaüstünde
  çalıştır" does not change the resolution (only `source_text` counts);
  a `node` argument from the router naming another owner's node → refused.
- `last_active_at`: hook event bumps, heartbeat does not, rate-limited to
  one write a minute, another owner's event cannot bump.
- One-node invariant: with two online reference nodes, every registered
  tool's call yields chain rows for one node only; `node_sessions` on
  "Masaüstünde" sends nothing to the MacBook (its reference node records
  zero invokes).
- Voice: a `node_voice` turn from node A resolves to A; the same text typed
  on the web with A and B online and neither recently active → the ask
  template, zero `NodeInvocation` rows, zero `ModelUsage` beyond the
  turn's existing router calls.
- Actions: draft for B only when the message names B; an approval for A
  dispatched while B is the active node still goes to A; the approval card
  shows A's name/room/platform.
- Role × endpoint unchanged for the existing node endpoints (regression).

**Done when:** every D6 rule is pinned by a test, the one-node invariant
test walks the whole registry, and no node call can be made without a
resolved single target.

### 3. `feat/p13-hud-presence` — per-node presence panel and `/devices` overview

**Scope.** D4, D5 (surfaces):
- `nodes.selectors.node_presence`; `hud.services.nodes_section`,
  `SECTIONS += ("nodes",)`; the HUD stream and REST payload;
- frontend `HudNodesPanel` (+ HUD layout rule: panel when > 1 node, line
  otherwise), `/devices` table columns room / role / platform / "can"
  (read / action counts), platform icons with text labels;
- accessibility: status as text plus icon, keyboard-reachable rows,
  `aria-live="polite"` only for connect/disconnect changes.

**Files.**
- `backend/nodes/selectors.py`, `backend/nodes/tests/test_node_presence.py`;
- `backend/hud/services.py`, `backend/hud/tests/test_nodes_section.py`;
- `frontend/src/components/hud/{HudNodesPanel,HudPanels,HudView}.tsx`
  (+ tests), `frontend/src/components/nodes/NodesSection.tsx` (+ test),
  `frontend/src/lib/api/` HUD types.

**Tests.**
- Presence: ordering (connected, room order, name), revoked excluded,
  cap at 16, Redis down → every node `offline` (fail closed), voice state
  stale → `unknown`, no path/argument/output keys in the payload (a key
  allowlist test).
- Isolation: two owners, each sees only their nodes; a firm workspace
  stream shows the owner's own nodes only (as the Phase 9 line does).
- HUD contract: existing `health.node` payload unchanged (snapshot test);
  `nodes` section present for every stream.
- vitest: panel vs line switch, status text present (not colour only),
  axe check on the panel.

**Done when:** two reference nodes paired in a test show as two HUD rows
with correct live status and the existing HUD tests are unchanged.

### 4. `feat/p13-linux-node` — `friday-node-linux`, read-only, conformance-gated

**Scope.** D10:
- `node/friday-node-linux/` uv project: `friday_node_linux/{cli,daemon,
  credential,policy,readers_root,conformance}.py`, `_vendor/` +
  `scripts/vendor.py` + `vendor.lock`, `pyproject.toml`, `uv.lock`,
  `README.md` (install, pair, grant, systemd user unit, re-point,
  uninstall), `deploy/home/friday-node-linux.service`;
- CI job `linux-node`: `uv sync --frozen`, `ruff`, `pytest`,
  `vendor.py --check`, and the external conformance run against
  `node_conformance_server --external --tls-self-signed --protocol 1`;
- `node/friday-node-windows/README.md` (deferred stub);
- `Makefile` targets `linux-node-test`, `linux-node-install`.

**Files.**
- `node/friday-node-linux/**` as above, `node/friday-node-linux/tests/
  {test_policy,test_credential,test_cli_grant,test_readers_real_root,
  test_daemon_failclosed,test_purity,test_vendor_lock}.py`;
- `.github/workflows/ci.yml` (the job), `Makefile`;
- `backend/nodes/conformance/` — **no change** (the suite is reused);
- `node/friday-node-windows/README.md`.

**Tests.**
- Conformance: every v1 case and the compromised-server family pass
  against the packaged node over a real localhost TLS socket.
- Policy: no protocol frame changes `policy.json` (a hostile gateway
  sends every message type with policy-shaped payloads; the file's mtime
  and hash are unchanged); `grant` refuses without a TTY or with a wrong
  confirmation word; a policy file with mode wider than 0600 or another
  owner is refused at load.
- Credential: mode 0644 or another owner → refuses to start; the secret
  never appears in logs (log capture scan).
- Readers over a real temporary `$HOME` (not the fixture factory): grants
  off → `not_granted`; on → bounded, scrubbed rows; `cwd_prefixes`
  honoured; symlink out of a granted directory refused.
- Fail closed: server gone → no invokes served, spool bounded (10 MiB,
  24 h), reconnect backoff 1 s → 60 s with jitter (fake clock).
- Purity: importing `friday_node_linux` in a subprocess loads no
  `nodes`, `django` or other backend module; `vendor.py --check` fails on a
  one-byte change.
- Server side: pairing with `platform=linux` (branch 1) and a `hello` from
  the packaged node yields a snapshot with zero invocable action
  capabilities (D9).

**Done when:** the packaged Linux node pairs with a test server, serves
`node_sessions` from a real temporary home, passes the conformance suite
in CI, and `vendor.lock` guards drift.

### 5. `feat/p13-ha-home-control` — entity allowlist, media/scene services, pins v2

**Scope.** D11, D12:
- `connectors.HomeEntity` + migration, owner-web services/views/urls,
  audit events;
- `connectors/bridges/home_assistant.py`: new `media_player` services and
  serializers, `source`/`source_list` attributes, allowlist checks in
  validation, request rows and the precondition, live `source_list`
  check;
- `connectors/manifests/home_assistant.py`: version 2, new pins;
- `docs/mcp/home-assistant.md`, `docs/mcp/allowlist.md` (governance);
- frontend: Home Assistant entity editor on `/connectors`
  (pick from a live `ha_states` read, room, label, aliases, services
  checkboxes limited to the code maximum), `connector-write.tsx` header
  with room + label.

**Files.**
- `backend/connectors/{models,services,views,urls,serializers}.py`,
  `backend/connectors/migrations/0005_home_entity.py`,
  `backend/connectors/bridges/home_assistant.py`,
  `backend/connectors/manifests/home_assistant.py`,
  `backend/connectors/tests/{test_ha_entities,test_ha_entities_api,test_ha_media,test_ha_allowlist_gate}.py`
  (+ the existing HA tests updated for the allowlist);
- `docs/mcp/home-assistant.md`, `docs/mcp/allowlist.md`;
- `frontend/src/components/connectors/{ConnectorsView,HomeEntitiesEditor}.tsx`
  (+ tests), `frontend/src/components/approvals/renderers/connector-write.tsx`
  (+ test).

**Tests.** (fake HA server, `httpx.MockTransport`; never a real HA)
- Allowlist: an allowed-domain entity not in `HomeEntity` → refused at
  draft, request and precondition; a service outside the entity's list →
  refused; removing the entity while the approval is pending → execution
  `connector_changed`, nothing sent; empty allowlist → nothing possible.
- Code maximum: `HomeEntity.services` cannot hold a service outside
  `SERVICES[domain]` or a `NEVER_DOMAINS` entity (model + serializer
  validation); `HA_ALLOWED_DOMAINS` still narrows.
- Media: `select_source` with a source not in the live `source_list` →
  refused; `source_list` changed between request and execution →
  `connector_changed`; `volume_mute` requires a bool; toggles absent.
- Gate: every new service is gate-class; executes once after approval;
  a timeout after POST → `unknown_outcome`, never re-sent; state read
  back.
- Pins: the pin test recomputes `tool_schemas()` and matches the manifest
  v2 pins; changing a serializer without the pin fails.
- Registry walk: no tool, route, schedule or connector path writes
  `HomeEntity`.
- Role × endpoint: entity CRUD × the principals of branch 1's list.

**Done when:** "turn on the salon TV" can be drafted, approved and executed
once against the fake HA with the entity allowlisted, and refused in
every other case.

### 6. `feat/p13-home-voice-routes` — `home.control` route, typed-turn validation

**Scope.** D13:
- `connectors/home_phrases.py` (parser: verbs, rooms, labels/aliases,
  sources, scenes; Turkish and English; numbers bounded);
- `assistant/wake_routes.py`: the `home.control` route (effect
  `proposal`), resolution via a `connectors` port registered into
  `tools` (assistant → tools → port; `connectors` stays unimported by
  `assistant`, ADR-010 contract), the ask / refusal templates;
- typed-turn mismatch check on `router.connector_args` output for
  `ha_call_service`;
- shared phrase vectors `docs/voice/home-route-vectors.json` (pytest and,
  if the web voice client normalizes, vitest);
- ADR-015 implementation note.

**Files.**
- `backend/connectors/home_phrases.py`,
  `backend/connectors/tests/test_home_phrases.py`;
- `backend/tools/home_ports.py`, `backend/assistant/wake_routes.py`,
  `backend/assistant/services.py`,
  `backend/assistant/tests/{test_home_routes,test_home_routes_node_voice}.py`;
- `docs/voice/home-route-vectors.json`;
- `docs/adr/ADR-015-voice-originated-turns.md` (note).

**Tests.**
- Vectors: "salondaki TV'yi aç", "salon tv'yi kapat", "TV'nin sesini kıs",
  "televizyonu HDMI 2'ye geç", "sesi yüzde 30'a ayarla", "film gecesi
  modunu aç", "turn off the living room lights", fillers and the wake
  phrase stripped; non-matches fall through to the router.
- Resolution: unique → draft card with exact `entity_id`/`service`/`data`;
  two TVs in the salon → ask; unknown room → refusal; disallowed service →
  refusal; no `ApprovalRequest`, no HA request, no `ModelUsage` from the
  route.
- Cap: a `node_voice` "salondaki TV'yi aç" through the reference voice host
  and the real `friday-voice run` child → a draft card only; the
  execution needs the on-screen approval (no voice path reaches
  `approve`).
- Typed: router draft whose `entity_id` contradicts "salondaki" →
  `target_mismatch`, no draft.
- Boot: `validate_routes` still refuses a route with another effect.

**Done when:** the D13 vectors pass, every voice home command yields at
most a draft, and the typed path cannot draft for a different entity than
the one the owner named.

### 7. `feat/p13-home-server-ops` — compose override, backup/drill scripts, home preflight

**Scope.** D14, D15 (`home_*` rows):
- `deploy/docker/compose.home.yml`; `deploy/home/{friday-backup.sh,
  friday-restore-drill.sh,friday-backup.service,friday-backup.timer,
  Caddyfile.example}`;
- settings + `.env.example`: `HOME_BACKUP_STATUS_PATH`,
  `HOME_RESTORE_DRILL_STATUS_PATH`, `HOME_BACKUP_WARN_HOURS` (26),
  `HOME_BACKUP_FAIL_HOURS` (72), `HOME_DRILL_WARN_DAYS` (35),
  `HOME_DRILL_FAIL_DAYS` (90), `HOME_FDE_CONFIRMED_ON`;
- `ops/preflight.py` `check_home` (`home_lan_only`, `home_tls_pin`,
  `home_backup_fresh`, `home_restore_drill_age`, `home_disk_encryption`)
  in `CHECKS`;
- `docs/runbooks/home-server-setup.md` updated to point at the shipped
  scripts.

**Files.**
- `deploy/docker/compose.home.yml`, `deploy/home/*`,
  `deploy/home/tests/{test_backup_restore.py,test_compose_no_public_ports.py}`
  (or `backend/ops/tests/` if the job layout prefers it);
- `backend/config/settings/base.py`, `.env.example`;
- `backend/ops/preflight.py`, `backend/ops/tests/test_preflight_home.py`;
- `docs/runbooks/home-server-setup.md`.

**Tests.**
- Compose: every `ports:` entry in every compose file is `127.0.0.1:`
  (parsed YAML); `compose.home.yml` publishes nothing.
- Backup/restore round trip on the CI Postgres with a throwaway age key
  and a local restic repo: dump encrypted (no plaintext `PGDMP` header on
  disk), `status.json` shape, drill restores into a scratch DB,
  `migrate --check` and `verify_node_audit_chain` pass, `drill.json`
  written; a tampered dump makes the drill fail and write
  `result: fail`.
- Preflight: each `home_*` row's pass / warn / fail with fake status files
  and fake resolvers (a public IP in `DJANGO_ALLOWED_HOSTS` → fail; `*` →
  fail; RFC 1918 / ULA / CGNAT → pass); no network beyond a local TLS
  test server for `home_tls_pin`; never a real bucket or DNS.
- Shellcheck on the scripts (CI step).

**Done when:** the runbook's scripts are in the repo and tested, and
`make preflight` reports the five `home_*` rows.

### 8. `chore/p13-closeout` — e2e, preflight rows, runbooks, ADR notes, MASTER_PLAN

**Scope.**
- Preflight rows `nodes_labels`, `ha_entities`, `linux_node_vendor` (D15)
  and the "no protocol change" check (`build_node_conformance --check`
  and `friday-voice` lock unchanged versus the Phase 12 closeout hashes);
- the home-hub e2e `backend/ops/tests/test_home_hub_e2e.py`: a live test
  server with two nodes (the Python reference node as "MacBook", v2; the
  packaged `friday-node-linux` as "Masaüstü", v1), rooms and labels set;
  web typed read naming each node → one node each; ambiguous typed read →
  ask, zero invocations; `node_voice` turn via the reference voice host +
  a real `friday-voice run` child from the MacBook → origin node; a
  `node_action` for the MacBook approved and clicked on its confirmation
  double while the Linux node is active → runs on the MacBook only; the
  Linux node refuses an action draft (`node_lacks_capability`); HUD
  `nodes` section shows both; "salondaki TV'yi aç" by voice → draft →
  web approval → one call to the fake HA → state read back; a
  non-allowlisted entity never drafted; backup → drill round trip; every
  `home_*` row evaluated;
- runbooks: `home-server-setup.md` (final), `friday-node.md` (multi-node,
  Linux node, re-point), `node/friday-node-linux/README.md` reviewed;
- ADR-017 closeout note; MASTER_PLAN §AA row 13 status; `self_sources.json`
  regenerated; this brief's go/no-go rows;
- `docs/runbooks/mac-session-handoff.md` Part E ticked where server-side.

**Done when:** the e2e is green, preflight shows every Phase 13 row, and
the MASTER_PLAN row says "server side done; go/no-go pending owner
evaluation".

## Branch table

| # | Branch | Scope | Depends on | Status |
|---|---|---|---|---|
| brief | `docs/p13-brief` | This brief (full; the owner decision of 2026-11-26 kept as goal, scope and boundary; no stub existed), ADR-017 (Accepted 2026-11-26; numbered 017 because ADR-016 is taken), `docs/runbooks/home-server-setup.md`, MASTER_PLAN §AA row 13 + §O d11, `docs/runbooks/mac-session-handoff.md` Part E, `backend/assistant/self_sources.json` regenerated (`build_self_sources --check`) | Phase 12 closeout | **implemented 2026-11-20** (swarm date stamp; content per the owner decision of 2026-11-26; docs only; awaits the independent Sonnet 5 plan review to APPROVE) |
| 1 | `feat/p13-node-registry` | `workspaces.Room`, node name/room/role/platform/aliases, pairing `platform`, node `PATCH`, per-node audit API, `core.speech`, `/settings/home`, `/devices` edit + audit tab | brief approved | **implemented 2026-11-20** (swarm date stamp; see "Branch 1 notes"; awaits the independent Sonnet 5 review) |
| 2 | `feat/p13-node-targeting` | `nodes.targeting.resolve_target`, `last_active_at`, `ToolCallContext`, read tools and node-action proposals on the resolver, ask template, one-node invariant, approval card node header, ADR-013 note | 1 | **implemented 2026-11-20** (swarm date stamp; see "Branch 2 notes"; awaits the independent Sonnet 5 review) |
| 3 | `feat/p13-hud-presence` | `node_presence`, HUD `nodes` section + `HudNodesPanel`, `/devices` overview columns | 1 (parallel with 2) | **implemented 2026-11-20** (swarm date stamp; see "Branch 3 notes"; server and web only, nothing needs the Mac; the real two-PC HUD run is part of M2, pending owner evaluation; awaits the independent Sonnet 5 review) |
| 4 | `feat/p13-linux-node` | `node/friday-node-linux` (vendored + locked, read-only, local policy CLI, credential file, systemd user unit, re-point), CI conformance job, Windows stub README | 1 (pairing `platform`) | **implemented 2026-11-20** (`node/friday-node-linux`: vendored reference node + `vendor.lock`, v1 read-only daemon, 0600 policy + TTY-confirmed CLI, 0600 credential + TLS pins + rotation, re-point, hook relay + spool, systemd user unit; 50/50 v1 conformance cases over real localhost TLS; `linux-node` CI job; Windows stub; see "Branch notes and owner steps (pending) — `feat/p13-linux-node`"; real Linux PC: M2, pending owner evaluation) |
| 5 | `feat/p13-ha-home-control` | `connectors.HomeEntity` allowlist, media_player power/source/mute/volume steps, `source_list`, manifest v2 pins, entity editor, governance docs | 1 (rooms) | **implemented 2026-11-20** (see "Branch 5 notes"; fail closed: no HA entity call until the owner allowlists entities; awaits the independent Sonnet 5 review) |
| 6 | `feat/p13-home-voice-routes` | `connectors.home_phrases`, `home.control` wake route (proposal only), typed-turn mismatch check, vectors, ADR-015 note | 2, 5 | **implemented 2026-11-20** (see "Branch 6 notes"; no model call, no HA request, no approval from the route; typed `target_mismatch`; e2e with the reference voice host + real `friday-voice run` child; real mic/Whisper: M2, pending owner evaluation; awaits the independent Sonnet 5 review) |
| 7 | `feat/p13-home-server-ops` | `compose.home.yml`, `deploy/home/` backup/drill/timer/Caddy, `home_*` preflight rows and settings | brief approved (independent) | **implemented 2026-11-20** (compose override + `make up-home`, `deploy/home/` scripts/units/Caddyfile, `check_home` five rows, settings + `.env.example`, `home-ops` CI job, runbook updated; real hardware: M3, pending owner evaluation) |
| 8 | `chore/p13-closeout` | `nodes_labels` / `ha_entities` / `linux_node_vendor` rows, no-protocol-change check, home-hub e2e, runbooks, ADR-017 note, MASTER_PLAN row, `self_sources.json`, go/no-go record | 1–7 | planned |
| M1 | Swift `friday-node`: pairing `platform`, server re-point | Send `platform: "macos"` at pairing; "Sunucu adresini değiştir" behind Touch ID keeping the pins; conformance unchanged | 1 merged; Phase 9 B1 | deferred: Mac session |
| M2 | Multi-node and home-route run on real hardware | Real mic "which PC" and "salondaki TV'yi aç" on the Mac; Mac + real Linux PC paired to the real home server; HUD presence | 2, 3, 4, 6 merged; M1; Phase 11 B7 | deferred: Mac session (pending owner evaluation) |
| M3 | Home server on the owner's home PC | Runbook steps 1–9 on real hardware: LUKS, reservation, Caddy + private CA, ufw, backup to the chosen off-site target, first restore drill (and one from off-site) | 7 merged | deferred: owner hardware (pending owner evaluation) |
| W | Windows node | Not built; own brief later (d1: Tauri v2), must pass the same conformance suite | — | deferred (out of Phase 13) |

**Sequencing and migrations.** Branch 1 adds `workspaces` 0003 and
`nodes` 0005; branches 2 and 3 add none; 5 adds `connectors` 0005. Migration numbers here are
placeholders: each branch runs `makemigrations` on the latest integration
branch and, before merge, rebases and renumbers onto the newest leaf of
every app it touches (`makemigrations --check --dry-run` green, no merge
migrations). 2 and 3 may be developed concurrently but merge one at a
time (both touch `nodes/selectors.py`). 4 and 7 are independent of 2/3/5.

### Branch 1 notes — `feat/p13-node-registry` (implemented 2026-11-20)

What landed (all server side and web; nothing needs the Mac):
- `core/speech.py` (stdlib only): `normalize_speech`, `match_key` (moved;
  `assistant.wake_routes` re-exports them, the shared vectors pass through
  both paths), `phrase_key`, `find_mentions(text, vocabulary)` (whole words,
  longest phrase first, the last word may carry one closed-set Turkish case
  suffix incl. buffer `n`, `-ki`, final-consonant softening and the
  apostrophe split "Mac'te") and `match_room(text, rooms)` (locative forms
  only: "salonda", "salondaki", "çalışma odasındaki", "Salon'daki").
- `workspaces.Room` (migration `workspaces/0003_room`) and
  `workspaces/rooms.py` (the brief's `services.py` content lives in its own
  module next to it): owner-web CRUD, keys unique across names and aliases
  under the workspace-row lock, audit `home.room_created` /
  `home.room_renamed` / `home.room_changed` (aliases / order; not in the
  brief's list, added so an alias change is audited too) /
  `home.room_deleted`; `GET/POST /api/v1/workspaces/<id>/rooms`,
  `PATCH/DELETE …/rooms/<room_id>`; a firm or another user's workspace is
  404. The shared label rules (`clean_label`, `clean_aliases`) are reused
  by `nodes` for node names and aliases.
- `nodes.Node` fields `name_key`, `room` (PROTECT), `role`, `platform`,
  `aliases`, `last_active_at` (migration `nodes/0005_node_labels`, data
  step keys every name and suffixes duplicates among an owner's
  non-revoked nodes, oldest first, renaming the device row too; tested by
  running the step over upgraded-shaped rows). `last_active_at` is only
  stored here; branch 2 writes it.
- Pairing: optional `platform` (absent = `macos`; invalid = 400 before any
  code is consumed), a blank name gets the platform's default ("Linux
  düğümü"), a duplicate gets " 2", " 3" … (never refused); `nodes.node.paired`
  now carries `node_id`, `name_key`, `platform`.
- `PATCH /api/v1/nodes/<id>` (`nodes.services.relabel_node`): owner, web,
  human; unknown fields (incl. `platform`) are 400; nothing is published on
  the bus and the node's next `hello` is unchanged (gateway-harness test);
  audit `nodes.relabelled` with fields, old/new keys, room ids, roles.
- `GET /api/v1/nodes/<id>/audit?before=<cursor>`: chain rows (`kind:
  invocation`) merged with AuditLog rows about the node (`kind: event`:
  resource = the node, or `node_id` / `device_id` / `node_device_id` in the
  metadata, plus the owner's kill-switch rows), newest first, 50 a page,
  cursor `<µs since epoch>.<uuid>`; details are an allowlist of codes, keys
  and ids (no client IP, no args, no output); `nodes.invoke` twins and
  "viewed" rows are left out; every view is audited `nodes.audit.viewed`.
  No metadata index was added: the per-node row counts are small; revisit
  if the closeout measures a need (D8 allows it).
- Frontend: `/settings/home` (`RoomsEditor`), the `/devices` node card
  shows room · role · platform · aliases and a "Düzenle" dialog
  (`NodeLabelDialog`), `/devices/nodes/<id>` has "Çağrılar" / "Denetim"
  tabs; BFF routes for rooms, node `PATCH` and the audit; Turkish messages
  for the new codes.

Deviations recorded:
- Error codes are dotted like the rest of the registry: `label.invalid`,
  `label.limit`, `label.conflict` (409), `label.unknown_room`, and
  `room.in_use` (409) for the brief's `room_in_use`.
- "Usable" for label uniqueness: a node cut off **only by the kill switch**
  keeps its name and aliases reserved (the switch is released without
  re-pairing, so a name freed meanwhile would come back as a duplicate);
  revoked and expired nodes free theirs. Pairing is refused while the switch
  is engaged anyway.
- The label lock is the owner's personal-workspace row (then the owner's
  node rows): it exists before the first node does, so two concurrent
  first pairings cannot both take "MacBook".
- An alias may not repeat the node's (or room's) own name.

Owner steps (pending) — `feat/p13-node-registry`, recorded 2026-11-20:
1. Have the independent Sonnet 5 review of this branch reach APPROVE.
2. After merge and `migrate` on the real server: check `/devices` for any
   node the data step renamed ("MacBook 2") and relabel it as wanted;
   create the rooms on `/settings/home` and assign each node a room and
   role (branch 2's resolution uses them).
3. Mac session M1: the Swift app should send `platform: "macos"` at
   pairing (optional — absent already means macOS); the server contract is
   pinned by `nodes/tests/test_pairing_platform.py`.

Known follow-ups — `feat/p13-node-registry` (unresolved LOW review notes,
recorded at merge into `integ/phase-13` 2026-11-26):
1. **Revoked nodes keep their room (`workspaces.Room` / `Node.room`
   PROTECT).** A revoked or expired node keeps its room FK, so the room
   reports `room.in_use` (409) forever: the owner cannot delete it, and the
   `PATCH` that clears `room_id` may not be usable on a revoked node. Fix:
   clear `Node.room` on revocation, or leave revoked/expired nodes out of
   the in-use check; at least document it and allow `PATCH room_id=null`
   on revoked nodes.
2. **`relabel_node` re-checks unchanged labels.** An aliases-only or
   name-only `PATCH` checks the node's own existing name and aliases
   against `taken_label_keys`, so a legacy node that collides only with a
   kill-switch-held node gets 409 on an unrelated alias edit (edge case).
   Fix: check only the keys that changed (new name key, newly added alias
   keys).
3. **`node_audit_events` scans AuditLog metadata.** The query ORs
   `metadata__node_id`, `metadata__device_id` and `metadata__node_device_id`
   with no index, which becomes a scan as AuditLog grows. Fix: add a GIN or
   expression index on `AuditLog.metadata`, or narrow by a time window or
   `resource_id` before the OR (the closeout measurement under D8).

### Branch 2 notes — `feat/p13-node-targeting` (implemented 2026-11-20)

What landed (all server side and web; nothing needs the Mac, no protocol
change):
- `nodes/targeting.py`: `resolve_target` (`Resolved` / `Ambiguous` /
  `Refused`), `eligible_nodes`, `is_usable`, `invocable_names`, the ask
  template (`ask_text`, Turkish question-particle vowel harmony: "MacBook
  mu, Masaüstü mü?") and the refusal texts (`reply_text`). Vocabulary: the
  labelled nodes' names and aliases (usable and kill-switched ones;
  revoked/expired names are free and never matched), every room of the
  owner's personal workspace in locative form, role words
  (`ROLE_WORDS`) and platform words ("Mac'te", "Linux'ta").
  `nodes.selectors.read_node_for` is gone.
- `nodes/activity.py` `mark_node_active` (one write a minute per node),
  called from `nodes.events` (a non-empty hook batch the sink accepted),
  `nodes.services.record_gated_progress` (a confirmation `approved` /
  `denied`, or a result whose `confirm_state` is one of them) and the
  `voice` transcription route (`on_node_activity`, injected by
  `config/urls.py` next to the existing guards). Heartbeats, hellos and
  other event kinds never write it.
- `tools.registry.ToolCallContext(source_text, origin_node_id,
  input_mode)`, `ReadToolSpec.accepts_context` (default False: every other
  read tool is called exactly as before), `ReadToolError.reply`;
  `run_read_tool` / `list_read_tools` pass `context=` only to specs that
  accept it. `assistant.views` builds the context from the turn's own
  words (never history, connector, transcript or node text) and, on a
  `node_voice` turn, the token's node device; `classify_intent` /
  `propose_node_action` / `prepare_connector_read` carry it; a fixed
  reply ends the turn through `static_reply_stream` (no chat completion,
  no `ModelUsage` beyond the router's own calls).
- `tools/node_reads.py`: every read on `resolve_target`; the optional
  untrusted `node` argument on every argument serializer; the result text
  ends with `node: <name>`; the `read_tool.read` audit row carries
  `node_id`, `target_reason` or `candidates`.
- `tools/node_actions.py`: `proposal_targets` lists usable nodes only (not
  killed or expired either) with `node_room`, `node_role`,
  `node_platform`; `validate_proposal(..., context=)` runs the resolver
  with the model's `node_id` as `requested_node_id` and returns
  `{"refusal", "reply"}` for a mismatch, an ambiguity or a refusal naming
  the PC; `describe_request` adds `node_room`, `node_platform`. The
  approval card (`node-action.tsx`) renders "Masaüstü (Çalışma odası,
  Linux)"; its row label is now "Bilgisayar" (was "Mac").
- `core.channels.ChannelPrincipal.node_device_id` (set by the PAT
  authenticator for a `node_voice` token, None otherwise): the speaking
  node, readable by `assistant` and `voice` without a new import edge.
- Tests: `nodes/tests/test_targeting.py` (the D6 table with two and three
  nodes), `nodes/tests/test_last_active.py`, `nodes/tests/two_nodes.py`
  (the shared `make_two_nodes` world), `config/tests/test_node_voice_last_active.py`,
  `tools/tests/test_node_targeting_reads.py`,
  `tools/tests/test_node_targeting_actions.py`,
  `tools/tests/test_one_node_invariant.py` (registry walk),
  `assistant/tests/test_which_pc.py`, `node-action.test.tsx`.
- ADR-013 implementation note.

Deviations recorded:
- **The model's node never decides.** The brief lists `requested_node_id`
  under rule 1; implemented as an untrusted *check* instead: the
  resolution runs without it and it must agree (`target_mismatch`), else
  an unnamed pick among two online nodes would still be the model's
  choice. A node id the owner picked himself (a card click) does not go
  through the resolver at all (`create_request` / the actions view).
- **A named offline (or lacking) node on a read** is still sent the read,
  to that node only, so its own chain row records `node_offline` /
  `server_refused:not_invocable` (Phase 9's "every call writes its chain
  row"); never another node. For a node-action proposal it is a refusal
  naming the PC and no draft. The owner's only node, offline, is named the
  same way (`Refused(node_offline, node)`), not `not_available`.
- **Room / role / platform matching several nodes** narrows to the
  eligible ones first: exactly one online → that node; several → ask;
  none → `not_available`. A room of the owner with no node in it is
  `no_node_in_room` (never another PC); a role or platform word that no
  node has is ignored (common words). A bare "masaüstü" is not a role word
  (it also means the screen); "masaüstü bilgisayar" is.
- **Two node names in one message** → ask among them.
- **Read tools stay web-only** (Phase 9 Q4): the origin rule applies to
  node-action proposals of `node_voice` turns; a `node_voice` turn is not
  offered node reads, unchanged.
- **The ask follow-up.** The router sees only the current message, not the
  history, so the ask template tells the owner to repeat the request with
  the PC's name ("Bilgisayarın adını söyleyerek isteğini yeniden iletir
  misin?").
- **Audit.** Read refusals are recorded on the tool's `read_tool.read` row
  (`outcome`, `candidates`, `node_id`); `validate_proposal` still writes
  nothing (unchanged contract), so a proposal-time refusal leaves no row.
- **`voice` writer** fires once the transcription's conversation is
  found (the request is the evidence), before the STT call; a failing
  writer never fails the transcription.

Owner steps (pending) — `feat/p13-node-targeting`, recorded 2026-11-20:
1. Have the independent Sonnet 5 review of this branch reach APPROVE.
2. After merge on the real server: give each PC a room and, where a short
   word is natural, an alias on `/devices` (e.g. "medya bilgisayarı"
   as the media PC's alias); confirm the 15-minute last-active window
   (`NODE_LAST_ACTIVE_WINDOW_SECONDS`, Q2) or change it.
3. Mac session M2 (deferred: Mac session, pending owner evaluation): real
   microphone "which PC" runs against the real home server — "Masaüstünde
   testleri çalıştır" spoken to the Mac proposes for the desktop; an
   unnamed request spoken to the Mac proposes for the Mac (origin rule);
   record misses. The Swift app needs no change for this branch: the
   server contract it must keep passing is the unchanged conformance suite
   plus `nodes/tests/test_targeting.py` and
   `tools/tests/test_one_node_invariant.py` (server side).

Known follow-ups — `feat/p13-node-targeting` (unresolved LOW review notes,
recorded at merge into `integ/phase-13` 2026-11-26):
1. **Formatter reflow churn in `node-action.tsx`.** The diff of
   `frontend/src/components/approvals/renderers/node-action.tsx` carries
   large prettier-style reflow hunks (import lists, JSX wrapping) next to
   the semantic change (`nodeHeader`, the node row). Harmless, but it makes
   review harder. Fix: revert the reflow-only hunks, or confirm the repo's
   frontend formatter produces this output so later diffs stay minimal.
2. **`node: <name>` footer outside the size accounting
   (`backend/tools/node_reads.py`).** The line is appended after
   `report.render()` applied its size and truncation accounting, so
   `audit.size` and `truncated` exclude it (by at most about 50 bytes).
   Fix: add the line before rendering, or document that the size excludes
   the footer.
3. **Named node read sent anyway (`backend/nodes/targeting.py`).** A named
   node that is offline or lacks the capability is still sent the read so
   its own chain row records the outcome, while the resolver returns
   `Refused` (the documented deviation above; safe: that node only, no
   fall-through). Keep it; add a one-line comment next to `_SENT_ANYWAY`
   (`backend/tools/node_reads.py`) pointing at the test that pins it.

### Branch 3 notes — `feat/p13-hud-presence` (implemented 2026-11-20)

What landed (server side and web; nothing needs the Mac, no migration):
- `nodes.selectors.node_presence(owner, *, now=None)` (and
  `node_presence_page`, which also returns the total): through `nodes_for()`
  (`for_actor()` plus `owner=`), one entry per non-revoked node (revoked and
  expired nodes stay on `/devices` only; a kill-switched node stays listed
  with `kill_switch` and voice `off`), ordered online first, then room
  `sort_order` (nodes without a room last), then name key; capped at
  `NODE_PRESENCE_MAX = 16` **after** ordering (an online node is never cut);
  exactly the keys `PRESENCE_KEYS` (`id, name, room{id,name}, role,
  platform, status, online, last_heartbeat_at, last_active_at,
  invocable_read_count, invocable_action_count, voice`). One presence read
  per node; Redis down reads every node `disconnected` (fail closed); the
  voice block is `voice_summary` (stale or offline → `unknown`).
- `nodes.selectors.invocable_counts(snapshot)`: invocable read vs action
  (confirm + gate) counts; `node_summary` (the `/devices` list and detail)
  carries both too (D5 "which nodes can" column).
- `hud.services.nodes_section(actor)` → `{items, total, online}`;
  `SECTIONS += ("nodes",)`; in every snapshot and in deltas when it changes;
  the owner's own nodes in each of the owner's workspace streams (firm too),
  never another user's. `health.node` is unchanged (payload pinned by
  `hud/tests/test_nodes_section.py::test_the_health_node_line_is_unchanged`).
- Frontend: `HudNodesPanel` (row = link to `/devices/nodes/<id>`; room
  badge; platform icon + label; status icon + word, never colour alone; role
  and "N okuma · M eylem"; mic state; "+N düğüm daha" past the cap; one
  `aria-live="polite"` region that announces only connect/disconnect
  transitions), shown by `HudView` when `nodes.total > 1`, in which case
  `HealthPanel` hides its single node line (`hideNodeLine`); with one node,
  or a backend without the section, the line stays. `/devices` gains the
  `NodesOverview` table (name, status, room, role, platform icon + label,
  "Yapabildikleri" counts) above the node cards. HUD stream client:
  `nodes` parsed in snapshots and deltas (`EMPTY_NODES` default).
- Tests: `nodes/tests/test_node_presence.py` (ordering, revoked excluded,
  kill switch, cap 16 + total, Redis down, voice stale, credential
  expiring, two-owner isolation, key allowlist, no writes),
  `nodes/tests/test_node_presence_live.py` (two reference nodes paired
  through the service and connected to the gateway harness → two rows with
  live status; one drops → only its row flips), `hud/tests/
  test_nodes_section.py` (section in every stream, firm stream, deltas,
  isolation, health line unchanged, role × endpoint of `/hud/events` with the
  section), the two-node helper `nodes/tests/presence_nodes.py`
  (`make_two_nodes`: "Çalışma odası", v2 macOS "MacBook" with actions, v1
  Linux "Masaüstü" read-only — the brief's `two_nodes` fixture, a plain
  helper so branch 2 and the closeout can import it without a conftest
  clash); vitest `HudNodesPanel.test.tsx` (rows, text status, cap, live
  announcements, axe), `HudView.test.tsx` (panel vs line), `events.test.ts`,
  `NodesSection.test.tsx` (overview columns).

Deviations recorded:
- Two existing HUD tests changed one assertion each
  (`hud/tests/test_node_line.py::test_the_section_changes_when_presence_flips`,
  `hud/tests/test_node_voice_line.py::test_a_state_change_is_a_delta`): their
  delta key set is now `{"type", "health", "nodes"}`, because a presence or
  mic change now legitimately changes the new section too. The `health.node`
  payload itself is unchanged; the brief's "existing HUD tests are
  unchanged" holds for every other assertion.
- "Connected first" orders by the live presence key (`online`), so a
  connected node whose credential is due for rotation
  (`credential_expiring`) also sorts first.
- The section is a dict (`{items, total, online}`), not a bare list, so the
  client's object-section delta parsing applies and the panel can say
  "+N more" past the cap.
- `axe-core` 4.12.1 (already in the lockfile as a dependency of
  `eslint-plugin-jsx-a11y`) is now a pinned direct dev dependency for the
  panel's axe check (`color-contrast` disabled in jsdom; the HUD tokens are
  checked by `tokens.test.ts`). No new package is downloaded.

Owner steps (pending) — `feat/p13-hud-presence`, recorded 2026-11-20:
1. Have the independent Sonnet 5 review of this branch reach APPROVE.
2. M2 (pending owner evaluation): with the MacBook and the Linux desktop
   paired to the real home server, open `/hud` and check the "Düğümler"
   panel shows both rows with the right live status, room, platform and mic
   state, that unplugging one flips only its row within one stream tick, and
   that VoiceOver announces only the connect/disconnect; check `/devices`'
   overview counts against each node's detail.
3. Nothing in this branch needs the Mac: the Swift app sends nothing new
   (presence is server-side); the server contract is pinned by
   `nodes/tests/test_node_presence_live.py` against the Python reference
   node.

Known follow-ups — `feat/p13-hud-presence` (unresolved LOW review notes,
recorded at merge into `integ/phase-13` 2026-11-26):
1. **Two existing HUD tests adapted (`backend/hud/tests/test_node_line.py`,
   `backend/hud/tests/test_node_voice_line.py`).** The Done-when says
   existing HUD tests stay unchanged; both had their delta key set extended
   with `nodes` (a forced consequence of the new section; `health.node`
   payload unchanged). Fix: none in code; note the two adapted assertions in
   the PR description (recorded under "Deviations recorded" above).
2. **Per-node queries before the cap (`backend/nodes/selectors.py`).**
   `node_presence_page` reads `latest_snapshot`, `presence.is_online` and
   `voice_summary` for every live node before capping at 16, on every HUD
   tick (N+1, bounded by the owner's node count). Fix (optional): sort,
   slice to the cap, then fetch per-node data only for the listed nodes;
   consider batching the presence reads.
3. **Liveness hidden for `credential_expiring` (`backend/nodes/selectors.py`).**
   A connected node whose credential is expiring reports status
   `credential_expiring` (its `online` flag stays true); the panel shows
   only the status word. Acceptable as is; optionally show the online flag
   in the row text.
- Merge note: branch 2 and branch 3 each added a `nodes/tests/two_nodes.py`
  with a different `make_two_nodes`; at the merge branch 3's helper moved
  to `nodes/tests/presence_nodes.py` (same content; its two importers,
  `nodes/tests/test_node_presence.py` and `hud/tests/test_nodes_section.py`,
  updated). Unifying the two helpers is a later cleanup.

### Branch 5 notes — `feat/p13-ha-home-control` (implemented 2026-11-20)

What landed (all server side and web; nothing needs the Mac or a real HA —
every test runs against the `httpx.MockTransport` fake):
- `connectors.HomeEntity` (migration `connectors/0005_home_entity`):
  `entity_id` (unique per workspace), `room` (PROTECT → `workspaces.Room`,
  so a room with an entity answers `room.in_use`), `label` + `key`
  (`phrase_key`), `aliases` (≤ 5, shared label rules), `services`,
  `enabled`. `HomeEntity.save()` re-checks the code maximum on **every**
  write path (`bridge.code_maximum_refusal`: no `NEVER_DOMAINS`, read-only
  or Wake-on-LAN entity, no service outside `SERVICES[domain]`, no repeats,
  never empty).
- `connectors/home_entities.py` (the brief's `services.py` content, in its
  own module like branch 1's `workspaces/rooms.py`): owner-web CRUD
  (`require_owner_web`, personal workspace only), `HA_ALLOWED_DOMAINS`
  narrows what can be added, workspace-row lock for uniqueness, audit
  `ha.entity_allowed` / `ha.entity_changed` (field names) /
  `ha.entity_removed` (ids, keys, domain, service names only), and
  `entity_candidates` (the editor's pick list = one normal, audited
  `ha_states` read; nothing auto-applied). API:
  `GET/POST /api/v1/connectors/home-assistant/entities?workspace=`,
  `PATCH/DELETE …/entities/<id>`, `GET …/candidates?workspace=`. Error codes
  `ha_entity.invalid` / `.not_allowed` / `.conflict` (409) /
  `.read_failed` (502) plus the shared `label.*`.
- Bridge: `media_player` gains `turn_on`, `turn_off`, `select_source`
  (`SelectSourceData{source}`, text ≤ 60, no invisible formatting),
  `volume_mute` (`VolumeMuteData{is_volume_muted}`, JSON boolean only),
  `volume_up`, `volume_down`; toggles stay absent. `ATTRIBUTE_WHITELIST` +
  `source`, `source_list` (≤ 30 distinct items ≤ 60 chars).
  The allowlist is checked at request time (`ToolPolicy.scope_check =
  check_allowlist`, 400 `entity_not_allowed` / `service_not_allowed`
  before any network traffic), as the precondition (`target_present`, using
  the binding's workspace; removed / disabled / unticked while pending →
  `connector_changed`, nothing sent) and is exposed as
  `bridge.allowlist_refusal(workspace, arguments)` for branch 6's draft
  path. `select_source` must name a live `source_list` value: request time
  (400 `source_not_available`), precondition and the bridge's own pre-POST
  read (`connector_changed`, unsent). The dialog gains the owner's `Oda` /
  `Etiket` rows.
- `ha_states` scope unchanged; allowlisted entities are annotated with
  `allowlisted`, `label`, `room`, `allowed_services` through a new, small
  framework hook `ToolPolicy.annotate(workspace, structured)` (sync reads
  of native bridges only, validated at boot).
- Manifest version 2, new pins (`STATES_PIN 772b49f1…`, `CALL_SERVICE_PIN
  a3487095…`); `docs/mcp/home-assistant.md` (entity allowlist, sources,
  dialog, v2 review line) and `docs/mcp/allowlist.md` updated in the same
  commit series.
- Frontend: `HomeEntitiesEditor` under the Home Assistant card on
  `/connectors` (live pick list, label, room, aliases, services checkboxes
  limited to the code maximum, nothing ticked by default, enable/disable,
  remove behind a confirmation); `connector-write.tsx` header "Salon · TV ·
  media_player.turn_on" from the `Oda` / `Etiket` / `Hizmet` rows; BFF
  routes under `/api/connectors/home-assistant/`; Turkish messages for the
  new codes.

Tests: `connectors/tests/test_ha_entities.py` (service, code maximum on the
model, labels, rooms, scoping), `test_ha_entities_api.py` (CRUD, codes,
candidates, role × endpoint over the 12 principals of branch 1's list),
`test_ha_media.py` (D12 services, strict serializers, `source_list` bounds,
the done-when "turn on the salon TV" drafted → approved → executed once →
read back, timeout → `unknown_outcome` never re-sent), and
`test_ha_allowlist_gate.py` (refusals at request / precondition, narrowing
while pending, empty allowlist, Wake-on-LAN with an empty allowlist still on
`HA_WOL_ALLOWED_MACS` — the plan review's follow-up 1 — v2 pins, a
serializer change without the pin fails, and the registry walk: a source
scan proves only `connectors/home_entities.py` writes `HomeEntity`, and no
registered tool / read tool / manifest hook lives in that module). The
Phase 5 HA tests allowlist the fake's entities in their `bound` fixture.

Deviations recorded:
- Service module `connectors/home_entities.py` instead of
  `connectors/services.py` (the existing module is the binding service).
- The draft-time check: `ha_call_service` still has no `draft_arguments`
  in this branch (no typed-turn argument extraction yet); branch 6 calls
  `allowlist_refusal` from its resolver and decides whether to switch
  `draft_arguments` on together with the typed-turn mismatch check.
- `source_list` entries that are too long, not text or carry invisible
  formatting are **dropped, not stripped**, so every offered source is a
  value HA sent verbatim (a stripped name might not match on HA's side).
- The registry walk is a source scan plus a tool-registry walk inside
  `connectors/tests` (an `assistant` import from there would break the
  import contracts; wake `tool.*` routes are built from the same tool
  registry and are covered by it).

Owner steps (pending) — `feat/p13-ha-home-control`, recorded 2026-11-20:
1. Have the independent Sonnet 5 review of this branch reach APPROVE
   (it widens the HA contract: manifest v2, new pins).
2. After merge and `migrate`: **every HA entity call is refused until the
   allowlist is filled** (fail closed, intended). On `/connectors` →
   Home Assistant izin listesi: read the entities, add e.g.
   `media_player.salon_tv` (room *Salon*, label "TV", the services wanted)
   and `scene.film_gecesi` (label "film gecesi"). Wake-on-LAN keeps working
   through `HA_WOL_ALLOWED_MACS`.
3. On the real HA: confirm the TV's `source_list` names (they must match
   exactly for `select_source`) and that the dedicated non-admin HA user
   can call the new `media_player` services; nothing here ran against a
   real HA.

Known follow-ups (LOW, from the independent review; recorded at the
integration merge 2026-11-26):
1. **Frontend vitest suites not run in the review worktree**
   (`frontend/src/components/connectors/HomeEntitiesEditor.tsx`). The
   HomeEntitiesEditor, connector-write renderer and ha-entity-routes suites
   were not executed during review (no `node_modules` in the review
   sandbox); backend only was verified there. Fix: run `make test`
   (vitest) in CI or locally to confirm them green.
2. **Allowlist removal is a hard delete**
   (`backend/connectors/home_entities.py` `delete_entity`). The row is
   owner-re-addable configuration, not user content, and is audited as
   `ha.entity_removed`; ADR-009's reversible-trash rule is arguably out of
   scope. Fix: note in `docs/mcp/home-assistant.md` that allowlist removal
   is intentionally a hard delete (fail-closed config), or switch to
   `enabled=False` semantics.
3. **`HomeEntity.save()` maximum bypassable by bulk ORM writes**
   (`backend/connectors/models.py`). `bulk_create` / `QuerySet.update`
   skip the code maximum; call-time checks (request, precondition, the
   bridge's pre-send read) and serializer validation against `SERVICES`
   still enforce the allowlist. Residual risk is a direct ORM write. No
   action needed: the registry-walk test pins that only `home_entities`
   writes the table.

### Branch 6 notes — `feat/p13-home-voice-routes` (implemented 2026-11-20)

What landed (server side and a shared-vector vitest; nothing needs the Mac,
a real HA or a real model):
- `backend/connectors/home_phrases.py`: the pure parser
  (`parse_home_command`, Turkish first, English second, over the owner's
  `HomeEntity` labels/aliases -- plus their -lar/-ler and -s plurals -- and
  `Room` names/aliases through `core.speech.find_mentions`; one leading
  wake phrase and trailing fillers stripped with tables pinned equal to
  `assistant.wake_routes`); the verb table maps each verb to exactly one
  action (`on`, `off`, `mute`, `unmute`, `volume_up/down`, `play`, `pause`,
  `source`, `volume`, `level`), numbers only 0–100 where a serializer bounds
  one (volume → 0–1, light brightness 1–100, fan/cover 0–100); `service_for`
  maps (action, domain) to one code-maximum service. The read-only resolver
  (`resolve_home_command`) over the enabled allowlist of the **personal**
  workspace through `for_actor()`: one entity → `draft` (arguments validated
  by `CallServiceArguments` and `allowlist_refusal`), several → `ask`
  ("2 cihaz eşleşti: Salon Projeksiyon, Salon TV. Hangisi?"), unknown
  locative room / device not in the named room / service not ticked /
  `HA_ALLOWED_DOMAINS` narrowed / HA binding off → a fixed refusal
  ("Bunu yapmaya iznim yok: Salon TV için 'kapat' açık değil."). An empty
  allowlist or an unparsed utterance is a miss (normal router).
  `check_call_draft` (typed draft check) and `call_vocabulary` (allowlist
  rows as data for the argument step).
- `backend/tools/home_ports.py`: the three ports (`register_home_resolver`,
  `register_draft_check`, `register_draft_vocabulary`), registered by
  `ConnectorsConfig.ready`; `assistant` reaches them through `tools` only
  (import contracts unchanged and green). A raising resolver is a miss, a
  raising check refuses (fail closed).
- `assistant/wake_routes.py`: `WakeRoute.matched_by_code`, `CODE_ROUTES =
  (home.control,)` (effect `proposal`), tried after the phrase and `tool.*`
  routes and before `skill.*` aliases; `validate_routes` validates it at
  boot (no phrases, effect read/proposal). A draft is the `tool_request`
  intent with `{"arguments": ...}` and a fixed spoken confirmation ("Onay
  kartı hazır: Salon TV · aç. Kararı ekranda verirsin."), so the turn makes
  no model call at all; the cap (`apply_voice_capability_cap`) still runs.
  `home.control` is not listed for aliases and refuses one.
- Typed turns: `ha_call_service` sets `draft_arguments` (a Friday-side flag,
  not in `tool_schemas()`; pins and manifest version unchanged). In
  `assistant.services.draft_tool_payload` the argument step's description
  gains the allowlist rows as data, and after the serializer the port's
  draft check runs: outside the allowlist or a different entity than the
  owner's room/label mention → `DraftRefused` → plain chat with Friday's
  `target_mismatch` text, no card. The same check covers a `tool.ha_call_service`
  alias route.
- `docs/voice/home-route-vectors.json` (vocabulary, `normalize` rows checked
  by pytest and `frontend/src/lib/voice/home-route-vectors.test.ts`, `parse`
  rows incl. the brief's examples and non-matches); ADR-015 implementation
  note; `docs/mcp/home-assistant.md` "Home routes and typed drafts" + review line.

Tests: `connectors/tests/test_home_phrases.py` (vectors, `service_for`
inside the code maximum, resolver draft/ask/refusals, no rows / HA requests
/ ModelUsage, scoping, firm workspace, the typed check, ports),
`assistant/tests/test_home_routes.py` (through the chat view: draft card
with exact arguments and zero model calls, ask, refusals, miss → router,
member never routed, card → pending `ApprovalRequest` only via web, cap
degradation, typed turn not routed, typed `target_mismatch`, typed match
with the allowlist in the argument prompt, boot check),
`assistant/tests/test_home_routes_node_voice.py` (node voice draft only,
only the two messages written, body cannot opt out, a node voice token can
neither create nor approve the request) and `ops/tests/test_home_voice_e2e.py`
(the reference voice host + a real `friday-voice run` child over HTTP:
"salondaki TV'yi aç" → card on screen, no router/chat call, no approval, no
HA request; skipped without `node/friday-voice/.venv` unless
`FRIDAY_VOICE_E2E=1`).

Deviations recorded:
- Parser and resolver in one module (`connectors/home_phrases.py`); the
  port module is `tools/home_ports.py` as the brief names it.
- The ask template is "N cihaz eşleşti: A, B. Hangisi?" (no room-locative
  inflection in templates: vowel harmony on owner-set names is not
  guessed).
- A spoken source is drafted as transcribed (case kept, dative ending
  removed); exactness is enforced by the existing request-time
  `source_list` check, never fuzzy-matched.
- The draft reply is Friday's fixed text (no chat completion), unlike the
  `tool.*` routes, so the home route costs no model call.

Owner steps (pending) — `feat/p13-home-voice-routes`, recorded 2026-11-20:
1. Have the independent Sonnet 5 review of this branch reach APPROVE.
2. After branch 5's allowlist is filled (labels/aliases the way you speak:
   e.g. TV with alias "televizyon", the ceiling light with "ışık"; rooms
   with aliases such as "living room"), try "salondaki TV'yi aç" by web
   voice and on the Mac (M2): a card only; decide on `/approvals`.
3. Confirm the TV's `source_list` spelling matches what Whisper writes
   ("HDMI 2"); if not, the card is refused at tap time (`source_not_available`)
   — record which inputs need a different spoken form.
4. Real microphone / Whisper Turkish recognition of the home vectors is part
   of M2 (pending owner evaluation; does not block branch 8).

**Minimum usable cut** if the phase runs long: 7 (the home server is safe
and backed up), 1 and 2 (several PCs, the right one each time). 3 adds
presence, 4 the Linux node, 5–6 the TV.

## Mac session (deferred)

Phase 13 changes **no protocol surface**, so the Swift work is small. It
runs in a later Claude Code session on the owner's MacBook, after the
Phase 9 Mac session's B1 (the Swift shell, identity, session and
verifier) has merged. The same list is appended to
`docs/runbooks/mac-session-handoff.md` as **Part E**.

**Preconditions:**
- Phase 9 B1 merged (the Swift app pairs and holds a session); for M2 also
  Phase 9 B2–B3 and Phase 11 B7 (the voice host).
- Branches 1–4 and 6 merged here; the home server (M3) running, or the
  stack on the Mac for M1's contract run.

### M1 — `feat/p13-node-app-pairing-platform`: pairing platform and server re-point

**Create / change (Swift, `node/friday-node/`):**
- `Pairing/PairingRequest.swift`: add `"platform": "macos"` to the pairing
  request body (optional on the server; absent still means `macos`).
- `Settings/ServerAddressView.swift` + `Settings/ServerAddress.swift`:
  "Sunucu adresini değiştir" — the new base URL entered locally, confirmed
  with LocalAuthentication (Touch ID or the macOS password); the stored
  signing-key pin and TLS pin (SPKI or CA) are **kept**; on the next
  connect a server whose pins differ is refused with "Sunucu kimliği
  değişti — yeniden eşleştirin" and nothing is sent. No server message can
  change the address (ADR-013 decision 5).
- Menu: show the server host the node is connected to (e.g.
  `friday-home.local`), read from local settings only.

**Contract it must satisfy (already enforced here):**
- `backend/nodes/tests/test_pairing_platform.py` (branch 1): a request with
  `platform: "macos"` and one without both pair as `macos`.
- The node conformance suite **unchanged**: `manage.py
  node_conformance_server --external --tls-self-signed --protocol 2` all
  green, including the policy-mutation family (a frame cannot change the
  server address); `build_node_conformance --check` unchanged.
- The re-point behaviour mirrors `friday-node-linux config set server-url`
  (branch 4 tests `test_daemon_failclosed.py::test_repoint_keeps_pins`):
  same pins → connects; different SPKI → refuses before `hello`.
- `swift test` green on the Mac.

### M2 — Multi-node and home routes on real hardware (go/no-go)

**Do:**
1. Pair the MacBook and one real Linux PC (`friday-node-linux`, branch 4)
   to the real home server; label them on `/devices` (rooms, roles).
2. HUD: both rows show the right status within one heartbeat of a
   connect/disconnect (unplug the Linux PC's network: `offline` within
   45 s).
3. "Which PC" by voice on the Mac (real mic, Phase 11 voice host): ten
   reads without a name → all resolved to the MacBook (origin); ten
   naming "Masaüstü" → all to the Linux PC; record the chain rows. **Zero
   wrong-node invocations** is the bar.
4. Typed on the Linux PC's browser with both online and idle: the ask
   template appears, nothing is invoked.
5. "Hey Friday, salondaki TV'yi aç", "TV'nin sesini kıs", "film gecesi
   modunu aç" on the real TV/HA: each a draft card; approve on screen;
   exactly one HA call each; record STT misses (text only) and add owner
   aliases rather than fuzzy matching.

**Contract (already enforced here):** branch 2 `test_which_pc.py`,
branch 6 `test_home_routes_node_voice.py`, the closeout
`test_home_hub_e2e.py`.

### M3 — The home server on the owner's home PC (owner hardware, not the Mac)

**Do:** `docs/runbooks/home-server-setup.md` steps 1–9 on the real mini PC
(LUKS, DHCP reservation, `friday-home.local`, the stack with
`compose.home.yml`, Caddy with the private CA, `ufw` LAN-only, nightly
backups to the chosen off-site target — or a second disk recorded as
"off-site pending" — and the first restore drill, plus one drill from the
off-site copy). Re-pair (or re-point, M1) the Mac node to the home
server. `make preflight`: every `home_*` row pass.

**Record** M1–M3 results in this brief's "Go/no-go record" on a docs
branch, reviewed like every brief change.

## Go/no-go record (Phase 13 checkpoint)

Checkpoint: "Friday runs on the home server for two weeks and is used
from at least two PCs with one memory; no node call ever reaches the
wrong PC; a restore drill from the off-site backup has passed."

| Item | How it is measured | Result |
|---|---|---|
| Resolution rules (explicit, origin, last active, ask) | branch 2 resolver table | pending (branch 2) |
| One node per invocation; no broadcast | branch 2 invariant test + closeout e2e | pending (branch 2, closeout) |
| Linux node conformance | branch 4 CI job | pending (branch 4) |
| HA entity allowlist fail-closed; media/scene gate-class | branch 5 tests | pending (branch 5) |
| Home voice routes propose only | branch 6 tests + closeout e2e | pending (branch 6) |
| Backup → restore round trip; no public port in any compose file | branch 7 tests | pass in CI (branch 7, 2026-11-20: `ops/tests/test_home_backup_restore.py` against a scratch Postgres + local restic repo, `test_compose_no_public_ports.py`); the real box is M3 |
| Two weeks on the home server, ≥ 2 PCs, zero wrong-node chain rows | M2 + `verify_node_audit_chain` + owner review of `/devices` audit | pending owner evaluation |
| Restore drill from off-site passed | M3 `drill.json` | pending owner evaluation |
| Real-mic "which PC" and home routes | M2 | pending owner evaluation (Mac session) |

Pending rows do not block the backlog (Phase 7 Workshop) or the "later"
list. A wrong-node invocation found in M2 stops the phase: the resolver
rule involved is re-planned before anything else is added.

## Out of scope (explicit)

- Any new WS message, schema field, capability contract or
  `friday-voice/v1` change; any server-side write of node policy.
- Fan-out: a read or action across several nodes in one call ("tüm
  bilgisayarlarda …"); a "default node" setting that overrides an explicit
  name.
- Node actions on Linux or Windows (executors, sandbox evidence): a later
  brief with its own ADR-014 evidence.
- A Windows node (stub README only) and any Tauri code.
- Public exposure of any kind: port forwarding, a public certificate, a
  tunnel service, Tailscale Funnel. Running two Friday servers.
- Automatic backup scheduling or restore by Friday; any Friday access to
  backup content; managed database services (Supabase, RDS).
- HA scripts, automations, locks, alarms (`NEVER_DOMAINS` unchanged), HA's
  MCP server, HA areas as authority, toggles, `media_title` reads, camera
  streams, any confirm-class HA service.
- Presence detection of *people* (which room the owner is in); speaker
  identification.
- Multi-user households: rooms and entities belong to the one owner's
  personal workspace.

## Test strategy

**Discipline.** The same as Phases 2–12 (CLAUDE.md rules 5, 7, 8): ruff,
pytest, vitest, import-linter and `makemigrations --check` green on every
branch; the full backend suite once before each merge; role × endpoint
tests for every new endpoint; no real LLM, MCP, GitHub, Google, HA, VPS,
DNS, bucket or hardware in tests.

- **Doubles.** The Python reference node (v1 and v2, several instances in
  one test with distinct device ids) and the packaged Linux node over a
  real localhost TLS socket; the hostile conformance gateway; the Phase 11
  reference voice host and the real `friday-voice run` child against a
  live test server; a fake HA server (`httpx.MockTransport` for unit
  tests, a localhost ASGI fake for the e2e); a local restic repository and
  throwaway age keys for the backup round trip; fake resolvers for
  `home_lan_only`.
- **Two-node fixtures.** A `two_nodes` pytest fixture (owner, rooms, a v2
  "MacBook" and a v1 "Masaüstü", presence keys in the test Redis, snapshots)
  shared by branches 2, 3 and the closeout, so resolution and presence are
  always tested with more than one node.
- **Invariants as walks.** The one-node invariant and "nothing writes
  `HomeEntity` / node labels / rooms except the owner-web services" are
  registry walks over every `ToolSpec`, `ReadToolSpec`, wake route,
  probe, schedule kind and connector manifest, so a later tool cannot
  silently break them.
- **No model calls added.** A test per resolver path and per home route
  asserts the `ModelUsage` count is unchanged by the resolution itself.
- **Isolation.** Two owners, two personal workspaces, each with rooms,
  nodes and home entities; plus a firm workspace with a member: no row of
  one is visible to, resolvable by or callable for the other.

## Risks

| Risk | Mitigation |
|---|---|
| The wrong PC runs a command | Explicit mention beats everything and never falls through (D6 rule 1); the model's `node_id` cannot override the owner's words; the approval card names the node and room; the target node's own window must be clicked (ADR-014); M2's zero-wrong-node bar |
| A compromised node keeps itself "last active" (`last_active_at` is bumped by node-relayed hook events) | It only ranks candidates nobody named: an explicit mention and the speaking node always win (pinned by `nodes/tests/test_targeting.py::test_a_node_bumping_last_active_cannot_change_an_explicit_or_origin_resolution`); the approval card names the PC; the target node's own confirmation window must be clicked; one write a minute |
| Ambiguity asks too often and annoys | Origin node for voice, a single candidate, and the 15-minute last-active rule decide most turns; the window is a setting; aliases make naming short |
| Home server disk dies or is stolen | LUKS; nightly age-encrypted off-site backups; monthly drill; preflight freshness and drill age |
| Backup key lost | The age private key is in the password manager and an offline copy (runbook step 8); the drill proves it is usable monthly |
| Backups silently stop | `home_backup_fresh` warns at 26 h and fails at 72 h; the timer is `Persistent=true` |
| LAN exposure creeps (a port bound to `0.0.0.0`, Docker bypassing ufw) | Compose test refuses non-loopback ports; Caddy on the host bound to the LAN address; `home_lan_only`; runbook warns about Docker and ufw |
| TLS key change strands every node | Reuse the server key on renewal (runbook §5); `node_tls_pin` / `home_tls_pin`; re-pair documented |
| Linux node diverges from the spec | Vendored code with `vendor.lock` checked in CI; the same conformance suite as the Swift app |
| Linux local policy weaker than Touch ID | TTY + typed confirmation + 0600 file + same-uid `SIGHUP`; read-only capabilities only; recorded as an accepted difference (Q4) |
| HA allowlist migration breaks existing setups | Fail closed is intended; preflight warns; runbook and governance doc say how to allowlist; no domain becomes callable without an entity row |
| A HA source name used for injection (`source_list` text) | Bounded, invisible formatting stripped, fenced like every connector result; a source is only an exact-match argument, never an instruction |
| Turkish suffix variety breaks home phrases | Vector file with real forms; owner aliases; no fuzzy matching (ambiguity asks); M2 records misses |
| VPS migration later needs a protocol change after all | ADR-017 decision 7 and the runbook checklist keep the same signing key and CA; branch 4 tests re-point with pins; M1 mirrors it on the Mac |

## Open questions (with recommended answers)

- **Q1 — Confirm-class for low-risk media verbs (play/pause/volume)?**
  Recommended: **no** for Phase 13. Gate-class per ADR-008 decision 3 /
  ADR-009; revisit after two weeks of real use with the approval count
  from `/approvals` history, and only through an ADR amendment.
- **Q2 — Last-active window?** Recommended: 15 minutes
  (`NODE_LAST_ACTIVE_WINDOW_SECONDS=900`), confirmed by the owner in the
  plan review.
- **Q3 — Should a room hold a "default PC"?** Recommended: no; a room with
  one PC already resolves (rule 1), and a default would override the
  ask-when-ambiguous rule the owner chose.
- **Q4 — Linux credential storage: file vs Secret Service?** Recommended:
  0600 file now (works headless, testable here); Secret Service as a later
  option if the owner runs the node on a desktop session.
- **Q5 — Off-site target?** Recommended: Cloudflare R2 (S3 API, no egress
  fees for restores) but any restic backend works; a second local disk is
  acceptable until chosen, recorded as "off-site pending".
- **Q6 — TPM auto-unlock for LUKS?** Recommended: the owner decides; the
  runbook documents the trade-off. Default: passphrase at boot.

## Owner steps (pending) — recorded 2026-11-26

These cannot happen in a cloud session (they need the owner's hardware,
accounts or judgement):

1. Before branch 1: have the independent Sonnet 5 plan review of this
   brief reach APPROVE.
2. Confirm the ADR numbering deviation (ADR-017, because ADR-016 is
   Phase 12's) and ADR-017's content as Accepted 2026-11-26.
3. Choose and buy (or assign) the home PC; decide the off-site target
   (Q5) and LUKS unlock (Q6).
4. After branch 7: run `docs/runbooks/home-server-setup.md` on it (M3);
   keep the CA root key and the age private key off the server.
5. After branch 5: allowlist the HA entities on `/connectors` (until then
   no HA call is possible — fail closed).
6. Mac session M1 after Phase 9 B1; M2 after Phase 11 B7.
7. After two weeks on the home server with two PCs: record the go/no-go
   rows.

### Owner steps (pending) — `docs/p13-brief`, recorded 2026-11-20

1. Start the independent Sonnet 5 plan review of this brief (and of
   ADR-017 and `docs/runbooks/home-server-setup.md`, written in the same
   branch); branch 1 does not start before APPROVE.
2. Confirm the defaults this brief proposes or change them in the review:
   last-active window 15 min, ≤ 5 aliases per node/room/entity, ≤ 16 nodes
   in the HUD panel, backup warn 26 h / fail 72 h, drill warn 35 d / fail
   90 d, `source_list` ≤ 30 entries.
3. Confirm the Linux node's local-policy confirmation (TTY + typed
   basename) as the accepted stand-in for Touch ID (Q4, Risks).
4. Note that nothing in this branch was run against real hardware: the
   runbook's commands are written for Ubuntu LTS and must be exercised on
   the real home PC (M3); fix any step that differs there in a docs
   branch.

Nothing in this branch needs the Mac, real credentials, paid calls or real
hardware: it is documentation only.

### Branch notes and owner steps (pending) — `feat/p13-home-server-ops`, recorded 2026-11-20

**What landed.** `deploy/docker/compose.home.yml` (HOME_* settings into
web and worker; the `status/` subdirectory of the backup directory, which
holds only `status.json` / `drill.json`, bound read-only into web as a
directory, `create_host_path: false`; publishes nothing) and `make up-home`;
`deploy/home/friday-backup.sh`, `friday-restore-drill.sh`,
`friday-backup.{service,timer}`, `Caddyfile.example` (shellcheck-clean;
every location an env var with the runbook default; `FRIDAY_PG_DUMP_MODE=
direct` / `FRIDAY_DRILL_MODE=direct` are the seams the tests use instead of
Docker); `ops/preflight.py` `check_home` (`home_lan_only` with the
`resolve_host` seam, `home_tls_pin` = `node_tls_pin`'s comparison,
`home_backup_fresh`, `home_restore_drill_age`, `home_disk_encryption`),
registered last in `CHECKS`; the seven `HOME_*` settings in `base.py`,
`.env.example` and `deploy/docker/.env.example`; the runbook points at the
shipped files.

**Decisions taken in the branch.**
- Tests live in `backend/ops/tests/` (the brief allowed it):
  `test_preflight_home.py`, `test_compose_no_public_ports.py`,
  `test_home_backup_restore.py`. The round trip needs `age`, `restic` and
  the pg16 client: it skips where they are missing and runs in the new
  `home-ops` CI job (apt-installed, `FRIDAY_HOME_OPS=1` makes a missing
  tool a failure), which also runs `shellcheck`. Local run in this
  container: all green with apt's age 1.1.1 / restic 0.16.4.
- `pyyaml` is added to the backend **dev** group only (the compose guard
  parses YAML; no runtime dependency).
- A configured status path whose file is missing **fails** (a backup that
  never ran must not look "not configured"); only an empty setting warns.
- `home_lan_only` treats an unresolvable `.local` name as link-local (mDNS
  does not resolve in a container) and warns on other unresolvable names;
  a leading-dot host (`.example.net`) counts as a wildcard and fails.
- The drill checks the dump's sha256 against `status.json` before
  decrypting (age's MAC also refuses a tampered file) and, in direct mode,
  refuses a scratch database that already has tables. When `status.json`
  does not name the dump (an older or off-site one) it logs that the
  comparison was skipped.
- Review round 1 (2026-11-26): the status files live in
  `$FRIDAY_BACKUP_DIR/status/` (`FRIDAY_STATUS_DIR`) and compose binds that
  **directory**, not each file: the scripts replace the files atomically
  (rename), and a single-file bind mount pins the old inode, so web would
  have read a stale status forever. A compose test fails on any bind
  source ending in `.json`. `home_lan_only`'s warn detail for an
  unresolvable non-`.local` name now says it was not verified LAN-only.
- Caddy forwards `X-Real-IP` (overwritten by the proxy); the runbook's
  `TRUSTED_CLIENT_IP_HEADER` changed from `X-Forwarded-For` to `X-Real-IP`.

**Owner steps (pending).**
1. On the real home PC (M3): install the scripts and units as the runbook
   steps 8–9 say, run the first backup and the first drill (and later one
   from the off-site copy), then `make up-home` and `make preflight` — every
   `home_*` row pass. The docker-mode paths of both scripts (`docker compose
   exec postgres pg_dump`, the throwaway drill container on
   `friday_default`, the one-off `web` check) and the systemd hardening
   (`ProtectSystem=strict`, `ProtectHome`, `CacheDirectory`) were not run
   here (no Docker daemon / systemd in this container); fix any difference
   in a docs/ops branch.
2. Set `HOME_FDE_CONFIRMED_ON` in `deploy/docker/.env` after checking LUKS
   (runbook step 1).
3. Set `NODE_TLS_PIN_SHA256` + `PREFLIGHT_NODE_TLS_URL` to the LAN URL so
   `home_tls_pin` and `home_lan_only` pass instead of warn.
4. Choose the off-site restic target (Q5); until then a second disk,
   recorded as "off-site pending".

Known follow-ups — `feat/p13-home-server-ops` (unresolved LOW review notes,
recorded at merge into `integ/phase-13` 2026-11-26):
1. **`deploy/home/friday-restore-drill.sh` — drill network name.** The
   docker-mode drill defaults `FRIDAY_DRILL_NETWORK` to `friday_default`.
   `compose.yml` sets `name: friday`, so this should resolve, but the
   docker path was not run here (no Docker daemon); recorded as owner step
   M3. Fix: confirm on the real box that the network name matches, and fix
   it in a docs/ops branch if not.
2. **`backend/ops/preflight.py` — `home_lan_only` only warns on an
   unresolvable name.** A non-`.local` host in `DJANGO_ALLOWED_HOSTS` that
   this container cannot resolve is a warn, not a failure, so a public name
   that does not resolve here passes as a warn (the warn text says it was
   not verified). Acceptable as is; optionally fail on unresolved names
   once the home box is running.

### Branch notes and owner steps (pending) — `feat/p13-linux-node`, recorded 2026-11-20

**What landed.** `node/friday-node-linux/` (own uv project, Python 3.12,
exact pins `cryptography==50.0.1`, `jsonschema==4.26.0`, `wsproto==1.3.2` —
the backend's — plus dev `pytest`/`ruff`; console script
`friday-node-linux`):
- `scripts/vendor.py` copies the backend's node code into
  `friday_node_linux/_vendor/` (imports rewritten to the vendored package,
  a generated header line on each `.py`, data files byte for byte) and
  writes `vendor.lock` (sha256 of each backend source and of its copy);
  `--check` fails on a one-byte change, a missing/extra file, a stale lock
  or a conformance node id that differs from `nodes/conformance/cases.py`;
- `daemon.py`: `LinuxNode` (the vendored `ReferenceNode` with
  `friday-node-linux 0.1.0` / `Linux <release>` in `hello`), offering
  **`friday-node.v1` only**, no executor (`NoExecutor`);
  `PinnedNodeClient` (TLS handshake + pin check before the WebSocket
  request, i.e. before the Bearer secret); `Backoff` (uniform in
  [1, min(60, 2^n)] s, reset after a session of ≥ 30 s); `Daemon`
  (sessions, `SIGHUP` reload between sessions, hook relay thread into the
  vendored spool, pid file, `bye{revoked|kill_switch|credential_expired}` →
  exit 0, no reconnect);
- `readers_root.py`: `LinuxReaders` over the real `$HOME` (the vendored
  `Readers` + availability rules + the 8B owner-turn reads over a vetted
  symlink view, removed after each call) and the read-only catalogue;
- `policy.py` (`~/.config/friday-node/policy.json`, `{v, grants: {claude,
  codex}, repos: [{name, root}]}`, strict, 0600, atomic) and the CLI's
  `grant|revoke|repo add|repo remove` (interactive TTY, the exact change
  printed, the directory's basename typed; the running daemon gets
  `SIGHUP`); `policy show`, `status`;
- `credential.py` / `tls.py` / `api.py`: the 0600 credential, pins = the
  private CA (`--ca`) and/or the SPKI sha256 (`--tls-pin-sha256`, hex or
  base64), `pair` (`platform: linux`, `protocol_versions: [1]`, the pin
  checked before the code is sent), `config set server-url` (the new
  server must pass the existing pins; all pins kept), `rotate` and the
  daemon's automatic rotation when `rotate_after_days` is reached
  (`POST /api/v1/auth/nodes/credential/rotate`, saved before use, retried
  hourly on failure), `unpair`;
- `conformance.py`: `friday-node-linux conformance --hook-socket … --gateway
  host:port --ca …`, the external-hook client building each case's node
  with the daemon's own `build_node`; `scripts/external_conformance.py` runs
  `manage.py node_conformance_server --external --tls-self-signed
  --protocol 1` against it (**50/50 v1 cases pass**, including the
  compromised-server family);
- `deploy/home/friday-node-linux.service` (systemd **user** unit:
  `NoNewPrivileges`, `ProtectSystem=strict`, `StateDirectory` /
  `RuntimeDirectory` the only writable paths, `Restart=on-failure`,
  `ExecReload` = `SIGHUP`); `node/friday-node-windows/README.md` (stub);
  `make linux-node-test|linux-node-conformance|linux-node-install`; the CI
  job `linux-node` (ruff, format, `vendor.py --check`, pytest, external
  conformance, then the backend tests with `FRIDAY_LINUX_NODE=1`);
  `docs/mcp/dependencies/friday-node-linux.md` and an allowlist row.
- Tests: `node/friday-node-linux/tests/` (`test_policy`, `test_cli_grant`,
  `test_credential` (file, pairing, re-point, log scan), `test_rotation`,
  `test_readers_real_root`, `test_daemon_failclosed`, `test_purity`,
  `test_vendor_lock`, `test_systemd_unit`; 103 tests); backend
  `nodes/tests/test_linux_node_external.py` (every v1 case through the
  external hook; the real daemon receives every policy-shaped and every
  non-frame-error v1 frame: `policy.json` bytes/mtime and the credential
  unchanged, reconnect after the frame-error close, `bye{revoked}` ends it)
  and `config/tests/test_linux_node_gateway.py` (a live TLS server: Django
  + the real gateway process on one port; the packaged node pairs over
  HTTPS with `platform=linux`, its `hello` yields a v1 snapshot with **zero
  invocable action capabilities**, `claude.sessions.list` and the
  `node_sessions` tool are served from a real temporary `$HOME`, revocation
  ends the daemon). Both backend files run the package as a subprocess from
  its own `.venv` and skip where it is missing. `backend/nodes/conformance/`
  and every protocol surface are unchanged.

**Decisions and deviations taken in the branch.**
- **Vendored closure.** D10 listed `node, transport, readers, local_policy,
  spool, hook_relay, _scrubber_vendored`; `node.py` itself imports
  `confirm`, `executor`, `sandbox`, `stream_scrub`, `verifier_v2` and
  `worktree`, and `capabilities/__init__.py` loads `v2/` too, so the whole
  closure is vendored (the v2 paths are unreachable on a v1-only session);
  `nodes/reference/fixtures/` and `nodes/conformance/keys/` are vendored
  for the `conformance` command only; `owner_turns.py` needs
  `integrations/cli/_scrubber_vendored.py`.
- **`unavailable` → `unsupported`.** The protocol's availability enum is
  `granted | not_granted | unsupported`; `apps.frontmost`, `windows.list`
  and (without `ccusage` on `PATH`) `usage.ccusage` are `unsupported`.
  `git.status` is `not_granted` until a repo is registered.
- **`usage.ccusage`** is gated on `ccusage` being on `PATH` (as D10 says)
  but the amounts come from the vendored reference reader over the granted
  transcripts (the spec the server recomputes); `ccusage` is never run.
- **Symlinks.** A symlinked `~/.claude` / `~/.codex` (the owner's own
  layout) is followed; a symlinked `projects/` / `sessions/` root and any
  symlinked file or directory inside it is refused (the reference rule;
  the owner-turn view applies the same vetting).
- **Conformance stand-ins.** The `conformance` command uses the case clock,
  the vendored fixture home, the fixture desktop (so `apps.frontmost` /
  `windows.list` cases run), `ccusage` treated as present, and the
  conformance node id / signing key; everything else is the daemon's code.
- **No TOFU.** `pair` refuses without `--ca` or `--tls-pin-sha256`; there
  is no system trust store and no plaintext mode.
- **Policy reload** closes the current session and reconnects with the new
  catalogue (the policy never changes during a session, so the
  conformance fingerprint rule holds on the real daemon too).
- **Credential rotation** was not in D10 but the server caps a secret at
  90 days (`NODE_CREDENTIAL_MAX_AGE_DAYS`); without it the daemon would need
  a re-pair every quarter, so it rotates like the Swift node will.

**Known follow-ups (for the closeout or a later branch).**
1. **The 8B owner-turn contracts are not servable yet.** They are still
   staged in `integrations/node_reference/contracts/v1/` (the server
   classifies them `unknown_capability`), and their `cwd` /
   `cwd_prefixes` patterns (`^(/|~$|~/)`) are prefix patterns: under the
   protocol's strict full-match validator the node's own check 15 refuses
   every absolute prefix. The Linux node advertises and implements both
   reads (its reader checks the result with jsonschema's search semantics,
   as the 8B tests do); moving the contracts into `nodes/capabilities/v1/`
   with full-match patterns (`^(/|~$|~/).*`) and re-vendoring makes them
   work end to end.
2. **Pairing and rotation through the home Caddy.** `Caddyfile.example`
   sends everything except `/ws/node/v1` to the BFF (port 3001), which has
   no `/api/v1/nodes/pair` or `/api/v1/auth/nodes/credential/rotate`
   route; the node (and the Swift app, M1) needs those two paths routed to
   Django (`127.0.0.1:8001`). Not changed here because it decides how the
   client IP is trusted for PIN pairing (`TRUSTED_CLIENT_IP_HEADER` /
   `FRIDAY_BFF_SHARED_SECRET`); decide it in the closeout or a docs/ops
   branch. Until then, pairing a node through the home Caddy is expected to
   fail (the BFF has no such route); the tests here pair against Django
   directly.
3. The `linux_node_vendor` preflight row (D15) is the closeout's; it can
   call `scripts/vendor.py --check` (text only, no import).

**Owner steps (pending).**
1. Have the independent Sonnet 5 review of this branch reach APPROVE.
2. Confirm the TTY + typed-basename confirmation as the Linux stand-in for
   Touch ID (brief Q4 / Risks; also `docs/p13-brief` owner step 3).
3. On the real Linux desktop (M2, pending owner evaluation): `make
   linux-node-install`, `pair` against the home server with its private CA,
   `grant claude`, enable the user unit, and check `/devices` shows it as
   *Linux* with the read capabilities invocable; check that the unit's
   sandboxing (`ProtectSystem=strict`, `PrivateDevices`,
   `RestrictNamespaces` in a **user** manager) works on that distribution —
   none of it was run here (no systemd in this container) — and drop a
   directive that the user manager refuses, in a docs/ops branch.
4. Add the `friday-node-linux hook` command hook to `~/.claude/settings.json`
   on that PC (README) once the node is paired.

Nothing in this branch needs the Mac: there is no "Mac session" item for it
(the Swift app's pairing `platform` is M1, unchanged).

Known follow-ups — `feat/p13-linux-node` (unresolved LOW review notes,
recorded at merge into `integ/phase-13` 2026-11-26):
1. **`node/friday-node-linux/friday_node_linux/readers_root.py` — owner-turn
   output validation.** The owner-turn reads validate output with plain
   `jsonschema` search semantics because the staged 8B contracts use prefix
   patterns; documented in a code comment, not fixed here. Fix: track the
   follow-up that moves these contracts into `nodes/capabilities/v1` with
   full-match patterns, then drop the `jsonschema` special case.
2. **`node/friday-node-linux/friday_node_linux/daemon.py` — `signal_daemon`
   process match.** The daemon is identified by a substring match of
   `friday-node-linux` / `friday_node_linux` in `/proc/<pid>/cmdline`. This
   guards against a recycled pid but could match another process of the
   same user with that string in its arguments; the pid file is 0600 and the
   signal is only `SIGHUP`, so the impact is negligible. Fix (optional):
   compare `argv[0]` and the `run` subcommand exactly.
3. **`.github/workflows/ci.yml` — `linux-node` job database.** The job
   starts Postgres and Redis with `friday`/`friday` credentials; the backend
   tests need a database created with the job's `DATABASE_URL`. Not
   verified in CI (locally, with the `jarvis` credentials, the 5 backend
   tests passed). Fix: confirm the first CI run on the branch is green; no
   code change unless it fails.

### Known follow-ups (unresolved LOW review notes, recorded at merge 2026-11-26)

These came out of the plan review and were not fixed before merge into
`integ/phase-13`. Each is to be resolved in a docs branch or by the branch
that implements the affected decision.

1. **D11 / branch 5 — Wake-on-LAN vs the entity allowlist.** D11 says
   `ha_call_service` is refused unless `entity_id` is an enabled
   `HomeEntity`, but the existing bridge's `wake_on_lan` service takes no
   entity (`CallServiceArguments` rejects `entity_id` for WoL and uses
   `HA_WOL_ALLOWED_MACS`). Fix: state in D11 and the branch 5 tests that WoL
   stays governed by `HA_WOL_ALLOWED_MACS` (no `HomeEntity`), gate-class and
   unchanged; add a test that WoL still works with an empty entity
   allowlist and still needs an allowlisted MAC.
2. **D6 / Risks — `last_active_at` is node-controlled.** (Resolved in
   branch 2: Risks row added; pinned by `nodes/tests/test_targeting.py`.)
   It is bumped from
   node-relayed hook events, so a compromised node could keep itself "last
   active" and win rule 3 for un-named requests. The mitigations hold
   (approval card names the node, the node's own confirmation window,
   explicit mention beats everything) but are not listed in Risks and no
   test covers them. Fix: add a Risks row; add a test that a node bumping
   `last_active_at` cannot change an explicit or origin resolution;
   optionally cap hook-driven bumps to nodes whose catalogue has the
   capability.
3. **Branch table / MASTER_PLAN row — mixed dates.** The brief row says
   "implemented 2026-11-20 (swarm date stamp)" and the owner steps are
   headed "recorded 2026-11-20", while the owner decision and ADR are dated
   2026-11-26. Fix: use 2026-11-26 consistently, or drop the "swarm date
   stamp" remark.
4. **ADR number.** The task spec asked for ADR-016, but ADR-016 already
   exists (Phase 12 proactive runs), so this is ADR-017; the deviation is
   documented in the ADR, this brief, MASTER_PLAN d11 and the handoff, and
   `self_sources.json` is updated. No fix required; owner step 2 covers the
   confirmation.
