# `home_assistant` connector — governance twin

Twin of `backend/connectors/manifests/home_assistant.py` and the
`` `home_assistant` connector `` row in `docs/mcp/allowlist.md`
(`docs/security/mcp-governance.md`, "Runtime connectors"). Brief: Phase 5
D8, R5, Q7; branch 7 `feat/p5-home-assistant`.

## Summary
- **Manifest:** `backend/connectors/manifests/home_assistant.py`, version `3` (Phase 13b branch 1a, `feat/p13-media-play-pause-stop`, 2026-11-20: `media_play_pause` and `media_stop`, gate-class, new `call_service` pin; v2 was Phase 13 branch 5, `feat/p13-ha-home-control`, 2026-11-20: entity allowlist, media/TV services)
- **Kind:** `native` — bridge `backend/connectors/bridges/home_assistant.py`,
  exported as the manifest module's `BRIDGE` (`backend/connectors/bridges/__init__.py`)
- **Status:** active
- **Data class:** `personal` (personal workspaces only; web-only reads; only
  providers with `personal_data_allowed`; a read marks the conversation
  `private` + `personal_data`)
- **Workspace types:** personal (a firm workspace cannot bind: refused by the
  manifest validation, the binding service and the API — tested)
- **Read channels:** web

## Why native REST, not Home Assistant's MCP server
HA's MCP server exposes intent tools (`HassTurnOn` by name or area) that HA
resolves *after* the decision, so the dialog could not say which entity
changes. The REST API is exact: `GET /api/states[/<entity_id>]`,
`POST /api/services/<domain>/<service>` with an explicit `entity_id`. No
WebSocket (no subscriptions needed).

## Source and maintenance
- Upstream: Home Assistant core REST API (`/api/...`), Apache-2.0, official.
- No sidecar and no image: the bridge is Friday code using `httpx`.
- "Protocol version" in the binding's health report is `rest`; the server
  version comes from `GET /api/config` (display only).

## Tools
| Friday name | Server tool | Kind | Schema pin (sha256) | Scope check | Gate / user-invocable | work_log |
|---|---|---|---|---|---|---|
| `ha_states` | `get_states` | read | `772b49f1813b8f1bbd3e9357a8a03d063ce5b3fb3924c28e317eade8bfdaf2d6` | allowed domains (serializer) | — | — |
| `ha_call_service` | `call_service` | write | `968bd894bfcb584ca03bc5d05d74e3a6576c95d5d8d0a50b731db563bdf001cd` | code allowlist ∩ `HA_ALLOWED_DOMAINS`, exact entity of the service's domain, per-service data serializer; **the owner's entity allowlist** (`HomeEntity`: enabled row, service ticked — `check_allowlist`) | `ha_call_service` / yes | **no** (`records_work_log=False`, D13) |

**Pins.** There is no remote `inputSchema`: each pin is the sha256 of the
bridge's own request contract (`tool_schemas()`, derived from the
serializers and allowlists). Changing the allowlist, a bound or an attribute
means: update the pin here and in the manifest, bump `version`, review — in
one commit. The health check (on enable and on the 15-minute beat,
`BRIDGE.check_health`: `GET /api/config` + the contract hashes) reports a
changed contract as `mismatch` (audited `connector.pin_mismatch`), and every
call re-checks the pin before any request — a mismatch found by a call
disables that tool (sticky for that manifest version).

### Code maximum (`SERVICES`)
| Domain | Services | Data (all bounded, unknown keys refused) |
|---|---|---|
| `light` | `turn_on`, `turn_off` | `brightness_pct` 1–100, `color_temp_kelvin` 2000–6500 (both optional) |
| `switch` | `turn_on`, `turn_off` | — |
| `fan` | `turn_on`, `turn_off`, `set_percentage` | `percentage` 0–100 |
| `cover` | `open_cover`, `close_cover`, `stop_cover`, `set_cover_position` | `position` 0–100 |
| `climate` | `set_temperature`, `set_hvac_mode` | `temperature` 16–30 (finite float), `hvac_mode` ∈ off/heat/cool/heat_cool/auto/dry/fan_only |
| `media_player` | `turn_on`, `turn_off`, `media_play`, `media_pause`, `media_play_pause`, `media_stop`, `select_source`, `volume_set`, `volume_mute`, `volume_up`, `volume_down` | `volume_level` 0–1; `source` ≤ 60 chars, must equal a live `source_list` value; `is_volume_muted` a JSON boolean; the rest none |
| `scene` | `turn_on` | — |
| `wake_on_lan` | `send_magic_packet` | `mac` ∈ `HA_WOL_ALLOWED_MACS` (no entity) |

Reads additionally show `sensor` and `binary_sensor` (read-only).
Deliberately absent: every `toggle` (its effect depends on the state at
execution, so the dialog could not say what happens).

### `media_play_pause` and `media_stop` (Phase 13b D4, manifest v3)
Both take no data and are **gate-class like every service** (a card,
`/approvals`, one `ConnectorWriteExecution`; nothing is direct in branch
1a — the ADR-018 direct class arrives in branches 1b/1c and does not change
this table).

- `media_play_pause` is the one state-dependent service in the maximum.
  Home Assistant pauses a `playing` player and plays any other, so the
  decision dialog shows the state read at request time **and the outcome it
  implies** — the row `Sonuç (istek anındaki duruma göre)`, e.g. "şu an
  oynatılıyor → duraklatılır" or "şu an duraklatılmış → oynatılır". The
  execution precondition (`target_present`) re-reads the entity and
  refuses the send when the live state now implies the **other** outcome
  (`connector_changed`, nothing sent), so the owner never approves one
  effect and gets its opposite. Residual: the player can still change
  between the precondition read and the POST (milliseconds; the bridge's
  pre-send read is recorded as `state_before`).
- `media_stop` stops playback (on many players the same as pause, on some
  it ends the session).
- **Voice/typed phrases** (`connectors.home_phrases`, D7 as amended by
  the owner 2026-11-27): Turkish "durdur" means `media_pause`, the same as
  "duraklat" (some TVs close the app on `media_stop`); English "stop"
  means `media_stop` (explicit); "pause" stays `media_pause`; "oynat
  duraklat" / "play pause" / "toggle play" mean `media_play_pause`. An entity whose ticked services
  lack the one asked for gets the fixed "izin yok" refusal, never a
  different service. Vectors: `docs/voice/home-route-vectors.json`.
- **Upgrading:** existing `HomeEntity` rows do not gain the new services;
  the owner ticks them on `/connectors` → Home Assistant (owner step). A
  TV that keeps only `media_pause` is paused by "durdur" and answers
  English "stop" with the refusal.
  Approvals pending under v2 fail their snapshot check after the upgrade
  (`connector_changed`, nothing sent), as for every manifest bump.

**Never** (`NEVER_DOMAINS`, a test asserts they are outside the maximum):
`script`, `automation`, `shell_command`, `rest_command`, `homeassistant`,
`hassio`, `lock`, `alarm_control_panel`. No domain is ungated or
confirm-class (Q7 — that needs a new ADR).

`HA_ALLOWED_DOMAINS` (comma-separated) can only **narrow** the maximum
(intersection; unknown names ignored; unset = the maximum). It is re-checked
at execution: narrowed after the request → `connector_changed`, nothing
sent.

### The owner's entity allowlist (Phase 13 D11, manifest v2)
A domain allowlist lets `media_player` mean every speaker HA discovered;
since manifest version 2 every **entity** call also needs a
`connectors.HomeEntity` row of the request's (personal) workspace:
`entity_id`, the owner's own room / label / ≤ 5 aliases, the `services`
the owner ticked (a subset of the code maximum for that domain, never a
`NEVER_DOMAINS`, read-only or Wake-on-LAN entity — `HomeEntity.save`
re-checks it on every write path), and `enabled`.

- **Fail closed:** an empty allowlist means no entity call is possible. An
  existing install upgraded to v2 can call nothing until the owner adds
  entities on `/connectors` → Home Assistant (owner step). Wake-on-LAN has
  no entity and stays governed by `HA_WOL_ALLOWED_MACS` alone (unchanged).
- **Checked three times:** at request time (`scope_check` =
  `check_allowlist`: 400 `entity_not_allowed` / `service_not_allowed`), as
  the execution precondition (`target_present`: removed, disabled or
  service unticked while the approval was pending → `connector_changed`,
  nothing sent), and by the draft path of the home routes (branch 6 calls
  `allowlist_refusal`).
- **Who writes it:** only the owner, over the web
  (`/api/v1/connectors/home-assistant/entities`, `require_owner_web`;
  agents, PATs, node tokens, members and low-trust channels refused);
  audited `ha.entity_allowed` / `ha.entity_changed` / `ha.entity_removed`
  (ids, keys, domain and service names only). No tool, wake route,
  schedule, probe or connector writes it (registry walk test
  `test_ha_allowlist_gate.py`). The editor's pick list is one audited
  `ha_states` read (`/api/v1/connectors/home-assistant/candidates`); adding
  an entity is always the owner's act.
- `ha_states` scope is unchanged (the allowed domains); allowlisted entities
  are annotated with the owner's `room` / `label` / `allowed_services`
  (Friday rows, `ToolPolicy.annotate`).

### Home routes and typed drafts (Phase 13 D13, branch 6)
- **Voice:** the `home.control` wake route (`connectors.home_phrases`,
  reached by `assistant` through `tools.home_ports`) parses a home command
  against the owner's enabled allowlist rows and rooms and yields at most a
  `ha_call_service` draft card with exact arguments (or the ask / a fixed
  refusal). No model call, no HA request, no approval; the owner still taps
  the card and decides on `/approvals`. An empty allowlist means no home
  command matches (the turn goes to the router).
- **Typed:** `ha_call_service` now sets `draft_arguments`: the router drafts
  the card's arguments with `router.connector_args` (personal-data routed;
  the allowlist rows -- id, room, label, aliases, services -- are shown to
  it as data). Before the card exists, `check_call_draft` refuses a draft
  outside the allowlist or for another entity than the room/label the
  owner named (`target_mismatch`). `draft_arguments` is not part of the
  pinned request contract (`tool_schemas()`); pins and version unchanged.
- **Sources by voice:** "HDMI 2'ye geç" drafts `select_source` with the
  source as transcribed (case kept, Turkish dative ending removed); it must
  still equal a live `source_list` value at request time
  (`source_not_available`), so a misheard source is refused when the owner
  taps the card, never guessed.

### `source` / `source_list` (D12)
`source` and `source_list` survive a state read; `source_list` is bounded
to 30 distinct inputs of ≤ 60 characters, and an input that is longer, not
text or carries invisible formatting is **dropped, never rewritten** (so a
listed name is always one HA sent verbatim). `select_source` must name one
of them exactly: read at request time (400 `source_not_available`),
re-checked as the precondition and once more right before the POST (gone →
`connector_changed`, unsent). `media_title` and `entity_picture` stay out
(content and token-bearing URLs). A source name is only an exact-match
argument, never an instruction.

## The request, the dialog, the execution
1. **Request** (`POST /api/v1/approvals/requests`, `tool_name:
   ha_call_service`, `payload.arguments = {domain, service, entity_id, data}`):
   strict serializers → 400 for a disallowed domain/service, an unknown data
   key, an out-of-range or wrong-typed value, an entity of another domain;
   then the entity allowlist (400 `entity_not_allowed` /
   `service_not_allowed`). The entity is then read from HA: missing, HA
   unreachable or (for `select_source`) a source not offered now → 400.
2. **Dialog** (`display_summary`, from the snapshot only): `Hizmet`
   (`light.turn_on`), `Varlık (entity_id)` (exact id), every `data.<key>` with
   its exact value, the owner's `Oda` and `Etiket` (Friday rows; the
   approval card's header shows *room · label · service*), the entity's name
   labelled *Home Assistant'tan gelen metin*, and the state read at request
   time. `arguments_json` is the exact canonical JSON that will be sent.
3. **Execution** (Celery, once): the framework's snapshot check (binding,
   manifest version, pin), then the tool's `precondition`
   (`bridges.home_assistant.target_present`): re-validate against the
   allowlist as it is now — code maximum, `HA_ALLOWED_DOMAINS`, the MAC list
   and the entity allowlist (narrowed → `failed`/`connector_changed`) — and read
   the entity (gone → `failed`/`target_missing`; HA unreachable → `failed`
   with its transport code; `select_source` input gone →
   `connector_changed`) — nothing sent in any of these; the bridge
   re-reads the entity and the source before the one `POST` (defence in
   depth); read the state back into the execution result. A timeout,
   transport error or 5xx once the POST was handed to the transport →
   `unknown_outcome`, never re-sent; a connect failure (nothing reached HA)
   or 401/403 → `failed`; an HA 4xx → `failed`/`tool_error`.

## Credentials
- Env var names only: `HA_BASE_URL` (e.g. `http://homeassistant.local:8123`,
  no userinfo/query), `HA_TOKEN` (a long-lived access token).
- Read from the process environment at call time — never settings, Postgres,
  a payload, an audit row or a log line (test:
  `test_the_token_is_nowhere_but_the_authorization_header`).
- **Scenes are owner-configured intents (residual risk).** `scene.turn_on`
  is in scope (brief D8), but Home Assistant resolves a scene's effects at
  activation time and the bridge cannot preview them: a scene the owner
  configured in HA may set entities in domains that are otherwise never
  callable here (`lock`, `alarm_control_panel`, ...). `NEVER_DOMAINS` is
  therefore not a guarantee once scenes exist. Mitigation: keep security
  devices out of scenes the Friday HA user can activate (HA-side), or drop
  `scene` from `HA_ALLOWED_DOMAINS`.
- **R5 — the token is admin-equivalent** (HA has no scoped tokens): create it
  for a dedicated **non-admin** HA user, keep HA on the LAN/Tailscale only,
  rotate it when a device is lost. The code allowlist is the real bound.

## Sandbox and network
- Runs in the web (reads, request-time state) and worker (writes, pin beat)
  processes; egress only to `HA_BASE_URL`.
- `httpx` client per operation: `follow_redirects=False` (a redirect is a
  `protocol_error`), `trust_env=False` (no env proxies), bodies capped at
  `CONNECTOR_RESPONSE_MAX_BYTES` while streaming, timeouts from the
  connector settings.

## Outbound data
- To HA: entity ids, service names and bounded data values the owner
  approved; nothing else. HA itself sends nothing onward on Friday's behalf.
- Personal data exposure: states of home devices; only to personal-data
  providers (D5).

## Prompt-injection surface
- HA-supplied strings (friendly names, states, attribute values) are
  owner-controlled but still low-trust: only whitelisted attributes of
  allowed-domain entities survive (`entity_picture`, which can carry an
  access token, is dropped), strings are stripped of invisible formatting and
  bounded to 200 characters, at most 200 entities; results are fenced as
  `<connector_result source="home_assistant">` data.

## Logging
- `connector.read`, `connector.write.succeeded|failed|unknown_outcome`,
  `connector.pin_mismatch`, `connector.binding.enabled|disabled` — metadata
  only (see the governance doc's table). Bridge log lines carry the
  operation and exception class, never text, URLs with secrets, arguments or
  results. No `work_log` memory for HA writes.

## Review
- Phase 5 branch 7, 2026-09-28 — reviewed (Sonnet 5); reconciled
  2026-11-20 with the native-bridge contract that landed with branches 5/8
  (`BRIDGE` + `check_health`, `ToolPolicy.precondition`) — re-review pending.
  Re-review on any pin change, allowlist widening or new domain.
- Phase 13 branch 5 (`feat/p13-ha-home-control`), 2026-11-20 — manifest v2:
  the entity allowlist, `media_player` power/input/mute/volume steps,
  `source`/`source_list`, new pins (this widening is the reviewed commit;
  the independent Sonnet 5 review is pending).
- Phase 13 branch 6 (`feat/p13-home-voice-routes`), 2026-11-20 — the
  `home.control` voice route and `draft_arguments` with the typed
  `target_mismatch` check; no pin, schema or domain change (independent
  Sonnet 5 review pending).
