# `home_assistant` connector — governance twin

Twin of `backend/connectors/manifests/home_assistant.py` and the
`` `home_assistant` connector `` row in `docs/mcp/allowlist.md`
(`docs/security/mcp-governance.md`, "Runtime connectors"). Brief: Phase 5
D8, R5, Q7; branch 7 `feat/p5-home-assistant`.

## Summary
- **Manifest:** `backend/connectors/manifests/home_assistant.py`, version `1`
- **Kind:** `native` — bridge `backend/connectors/bridges/home_assistant.py`,
  routed by `backend/connectors/transport.py`
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
- No sidecar and no image: the bridge is Jarvis code using `httpx`.
- "Protocol version" in the binding's health report is `rest`; the server
  version comes from `GET /api/config` (display only).

## Tools
| Jarvis name | Server tool | Kind | Schema pin (sha256) | Scope check | Gate / user-invocable | work_log |
|---|---|---|---|---|---|---|
| `ha_states` | `get_states` | read | `f24e86599d4763a6bf1fa3cb0093bcc51d8d3c942629106323df3288b4ad0822` | allowed domains (serializer) | — | — |
| `ha_call_service` | `call_service` | write | `230b3efed4a19051774cb937ce9303212a4183428d61632ad56d0bc3351708c7` | code allowlist ∩ `HA_ALLOWED_DOMAINS`, exact entity of the service's domain, per-service data serializer | `ha_call_service` / yes | **no** (`records_work_log=False`, D13) |

**Pins.** There is no remote `inputSchema`: each pin is the sha256 of the
bridge's own request contract (`tool_schemas()`, derived from the
serializers and allowlists). Changing the allowlist, a bound or an attribute
means: update the pin here and in the manifest, bump `version`, review — in
one commit. A mismatch disables the tool on the next pin check (sticky for
that manifest version).

### Code maximum (`SERVICES`)
| Domain | Services | Data (all bounded, unknown keys refused) |
|---|---|---|
| `light` | `turn_on`, `turn_off` | `brightness_pct` 1–100, `color_temp_kelvin` 2000–6500 (both optional) |
| `switch` | `turn_on`, `turn_off` | — |
| `fan` | `turn_on`, `turn_off`, `set_percentage` | `percentage` 0–100 |
| `cover` | `open_cover`, `close_cover`, `stop_cover`, `set_cover_position` | `position` 0–100 |
| `climate` | `set_temperature`, `set_hvac_mode` | `temperature` 16–30 (finite float), `hvac_mode` ∈ off/heat/cool/heat_cool/auto/dry/fan_only |
| `media_player` | `media_play`, `media_pause`, `volume_set` | `volume_level` 0–1 |
| `scene` | `turn_on` | — |
| `wake_on_lan` | `send_magic_packet` | `mac` ∈ `HA_WOL_ALLOWED_MACS` (no entity) |

Reads additionally show `sensor` and `binary_sensor` (read-only).
Deliberately absent: every `toggle` (its effect depends on the state at
execution, so the dialog could not say what happens).

**Never** (`NEVER_DOMAINS`, a test asserts they are outside the maximum):
`script`, `automation`, `shell_command`, `rest_command`, `homeassistant`,
`hassio`, `lock`, `alarm_control_panel`. No domain is ungated or
confirm-class (Q7 — that needs a new ADR).

`HA_ALLOWED_DOMAINS` (comma-separated) can only **narrow** the maximum
(intersection; unknown names ignored; unset = the maximum). It is re-checked
at execution: narrowed after the request → `connector_changed`, nothing
sent.

## The request, the dialog, the execution
1. **Request** (`POST /api/v1/approvals/requests`, `tool_name:
   ha_call_service`, `payload.arguments = {domain, service, entity_id, data}`):
   strict serializers → 400 for a disallowed domain/service, an unknown data
   key, an out-of-range or wrong-typed value, an entity of another domain.
   The entity is then read from HA: missing or HA unreachable → 400.
2. **Dialog** (`display_summary`, from the snapshot only): `Hizmet`
   (`light.turn_on`), `Varlık (entity_id)` (exact id), every `data.<key>` with
   its exact value, the entity's name labelled *Home Assistant'tan gelen
   metin*, and the state read at request time. `arguments_json` is the exact
   canonical JSON that will be sent.
3. **Execution** (Celery, once): re-validate against the allowlist as it is
   now; read the entity (gone → `failed`/`target_missing`, nothing sent);
   one `POST`; read the state back into the execution result. A timeout,
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
  devices out of scenes the Jarvis HA user can activate (HA-side), or drop
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
  approved; nothing else. HA itself sends nothing onward on Jarvis's behalf.
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
- Phase 5 branch 7, 2026-09-28 — pending the branch's Sonnet 5 review.
  Re-review on any pin change, allowlist widening or new domain.
