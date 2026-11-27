# ADR-018: Direct-Execute Home Media Class

## Status
Accepted 2026-11-26 — owner decision of 2026-11-26, recorded in the cloud
session and written down with the Phase 13b brief
(`docs/prompts/phase-13b-home-decisions/brief.md`, decision D4, branch
`docs/p13b-brief`). It overrides the recommendation of Phase 13 Q1
(`docs/prompts/phase-13-home-hub/brief.md`, "Open questions") and Phase 13
D12's "every call stays gate-class" **for the one class below only**.

This ADR is a deliberate, owner-approved, **narrowing exception** to
CLAUDE.md rule 4 ("no write … without the approval gate"), to ADR-014
decision 4 and to ADR-015 decision 1 (a voice-originated turn never
executes). CLAUDE.md rule 4 names this ADR as its single exception. It
amends nothing else: ADR-004 (approval gate rules), ADR-008 decision 3 and
ADR-009 (the confirm-class list), ADR-010 (connectors), ADR-013 (node
protocol), ADR-014 classes for node actions and ADR-017 (home hub) keep
their text; ADR-014, ADR-015 and ADR-017 get cross-reference notes.

## Context
Phase 13 made every Home Assistant (HA) service call gate-class: a draft
card, an `ApprovalRequest` decided on `/approvals`, one execution, the
state read back (Phase 13 D12, branch 5). Its home voice route
("salondaki TV'yi aç") only proposes (Phase 13 D13, ADR-015). That is the
right default for power, inputs, scenes, lights, climate, locks and
anything else whose effect is hard to undo or outside the room.

For the owner's media players it is two screen acts to pause a film. The
owner decided on 2026-11-26 that pausing, resuming, stopping and changing
the volume of a media player he explicitly marked is low-risk enough to run
without a card, also when he says it to Friday — accepting that a TV, a
video or another person in the room can trigger it (there is no speaker
verification, ADR-015 decision 7). The safeguards below keep the blast
radius to "the volume of a flagged speaker changed, at most N times a
minute, never louder than the owner's maximum".

## Decision
1. **A closed allowlist of services, in code.** The direct class is
   exactly these `media_player` services, and nothing else:

   | Owner's word | HA service |
   |---|---|
   | play | `media_player.media_play` |
   | pause | `media_player.media_pause` |
   | play_pause | `media_player.media_play_pause` |
   | stop | `media_player.media_stop` |
   | volume_up | `media_player.volume_up` |
   | volume_down | `media_player.volume_down` |
   | volume_set | `media_player.volume_set` (clamped, decision 6) |
   | volume_mute | `media_player.volume_mute` (mute and unmute) |

   The set is a `frozenset` constant (`connectors.home_direct.
   DIRECT_MEDIA_SERVICES`) pinned by a test and a preflight row against the
   digest recorded in this ADR's implementation note. **Adding a service,
   a domain or a data field needs a new amendment of this ADR**; there is
   no setting, flag, manifest field or table that widens it.
   `media_play_pause` and `media_stop` join the bridge's code maximum
   (`connectors.bridges.home_assistant.SERVICES`) so they exist at all;
   outside the direct class they are gate-class like every other service.
2. **Only owner-flagged entities.** A call is direct only when its target
   is an **enabled** `connectors.HomeEntity` of the owner's personal
   workspace (the Phase 13 allowlist on `/connectors`), whose `services`
   include the service, **and** whose new boolean `direct` is true. The
   flag defaults to false, exists only on `media_player` entities (the
   model refuses it elsewhere) and is set **only by the installation
   owner over the web** (`require_owner_web`, the existing owner-web
   entity endpoint), audited `ha.entity_direct_changed`. No agent, tool,
   wake route, schedule, probe, connector, model output, node, token or
   low-trust channel can set it, the kill switch or the volume maximum
   (CLAUDE.md rule 4: an agent never widens its own permissions);
   registry-walk and role × endpoint tests pin that.
3. **Everything else stays gate-class, exactly as today.** Power on/off,
   `select_source`, scenes, lights, switches, fans, covers, climate,
   Wake-on-LAN, any non-media domain, any `NEVER_DOMAINS` entity (locks,
   alarms, scripts, automations — still never callable), any non-flagged
   entity, any call the classifier cannot prove direct (decisions 5–7),
   and every call that does not come from the deterministic home matcher
   (decision 4) is a draft card → `/approvals` → one execution, unchanged.
4. **Only from the owner's own words, through code.** A direct call is
   reached only through the deterministic home matcher
   (`connectors.home_phrases`, Phase 13 D13) over the text of an
   owner-attended turn: a web voice turn (origin `voice`), a `node_voice`
   turn from a paired node (origin `node`) or a typed web chat turn
   (origin `chat`). Arguments extracted by a model (`router.
   connector_args`), a draft card the owner taps, an unattended run
   (ADR-016, `deny_if_unattended`), and the low-trust channels (CLI, the
   OpenAI-compatible facade, Telegram, hooks, node reads) are never
   direct. The route stays model-free: no gateway call, no `ModelUsage`.
   The direct classification is **code-only**: the model router can never
   emit it (its intent parser refuses the value and degrades the turn to
   chat), only the home route may return it, and the executor never trusts
   the classification's arguments — it re-resolves the stored text of the
   owner's message through the matcher and sends nothing unless the
   result is identical (Phase 13b brief D10a).
5. **Rate limits.** At most `HA_DIRECT_RATE_GLOBAL_PER_MINUTE` (default
   30) direct calls per minute for the owner and
   `HA_DIRECT_RATE_ENTITY_PER_MINUTE` (default 10) per entity. A call over
   either limit, or with the counter store unavailable, is **not** run
   directly: it becomes the normal gate-class draft card (fail closed to
   the gate), audited `ha.direct_rate_limited`.
6. **Volume maximum.** `volume_set` is clamped to the owner-set maximum
   (`HomeDirectSettings.volume_max`, default `HA_DIRECT_VOLUME_MAX` =
   0.6, owner-set on `/connectors`, range 0–1). A direct `volume_up` runs
   only when the live `volume_level` is known and at least 0.1 below the
   maximum; otherwise it is a draft card. Unmuting is direct; the
   maximum does not apply to a gate-class call, whose exact value the
   owner reads in the dialog.
7. **One owner kill switch.** `HA_DIRECT_MEDIA_KILL_SWITCH=true` in the
   environment **or** the "Doğrudan medyayı kapat" toggle on `/connectors`
   (`HomeDirectSettings.kill_switch`) turns every would-be direct call
   back into a gate-class draft card, from the next classification on.
   Both are checked when the call is classified and again immediately
   before it is sent; there is no queue of pending direct calls to drain.
8. **Every direct call is audited.** One `connectors.HomeDirectCall` row
   and one `ha.direct_call` audit row per call, with the origin
   (`voice` / `node` / `chat`), the node id for `node`, the entity id, the
   service, the (clamped) data, the requested value when clamped, the
   outcome and the state before and after. Like every connector write the
   call is sent at most once and never retried: a timeout after the send
   is `unknown_outcome`. Direct calls are listed on `/connectors` and
   counted on the HUD; they are audit-worthy, not memory-worthy
   (`records_work_log=False`, as for every HA call).
9. **The same bridge and the same checks.** A direct call goes through the
   existing Home Assistant bridge with the manifest's pins, an enabled
   binding whose tool status is `ok`, the strict per-service serializer,
   the entity allowlist check and the bridge's own pre-send read. Any
   refusal there ends the call (no card is substituted after a send was
   attempted).

## Consequences
- "Hey Friday, TV'yi duraklat" pauses a flagged TV with no screen act.
  "Salondaki TV'yi aç" (power) is still a card. The owner chooses per
  entity; nothing is direct until he flags an entity.
- A TV, a video or a guest can pause, resume, stop or change the volume
  of a flagged player, up to the rate limit and never above the volume
  maximum. That is the accepted residual risk of this ADR (with ADR-015
  decision 7: no speaker verification). The kill switch is one tap or one
  environment variable away.
- A device whose own `volume_up` step is larger than 0.1 can overshoot the
  maximum by at most one step; recorded as a residual in the brief's risks.
- The approval count on `/approvals` drops for media; the audit trail does
  not (decision 8).
- Widening this class later is an ADR amendment with its own owner
  decision, never a configuration change.

## Alternatives considered
- **Keep everything gate-class (Phase 13 Q1's recommendation).** Rejected
  by the owner: two screen acts per pause.
- **Confirm-class (one click, ADR-008 decision 3).** Rejected: a voice
  turn could still not use it (ADR-015 decision 1), which is the case the
  owner wants; and confirm-class is for in-app, reversible rows.
- **A domain-wide flag ("all media players direct").** Rejected: HA
  discovers speakers the owner does not own or use; the allowlist is per
  entity for the same reason as Phase 13 D11.
- **Letting the model's extracted arguments run directly.** Rejected: the
  deterministic matcher over the owner's words is the only path; model
  output never executes (CLAUDE.md rule 4).

## Implementation note — branch 1b `feat/p13-direct-media-model` (2026-11-27)

The model, the closed set, the classifier, the rate windows and the owner
API; **no executor** (branch 1c), so nothing is direct yet and no turn
behaves differently.

- **Closed set and digest.** `backend/connectors/home_direct.py`:
  `DIRECT_MEDIA_DOMAIN = "media_player"`, `DIRECT_MEDIA_SERVICES` (the
  eight services of decision 1), `DIRECT_MEDIA_DIGEST` = sha256 of the
  sorted names joined by `\n`:

  `ADR-018 digest: 9ca85ee245440d6c31779bf26dfd256489eeafff83e3e678bc3c7572ad2d3183`

  `home_direct.ADR_018_DIGEST` carries the same value;
  `connectors/tests/test_home_direct_classifier.py` asserts code, constant
  and this line agree (the closeout's `ha_direct_media` preflight row will
  compare them too). Changing the set without amending this ADR fails CI.
- **Models** (`connectors` migration `0006_home_direct`):
  `HomeEntity.direct` (default false; `save()` refuses it,
  `ha_entity.direct_not_allowed`, unless the entity is a `media_player`
  whose services include a closed-set service); `HomeDirectSettings`
  (personal workspace, one row; `kill_switch`, `volume_max` 0–1, default
  `HA_DIRECT_VOLUME_MAX`); `HomeDirectCall` (the call log; the FK to the
  allowlist row is `home_entity` because `entity_id` is the HA id column;
  no writer in 1b).
- **Classifier** `home_direct.classify(*, workspace, actor, arguments,
  origin, channel, input_mode, state=…)` — the brief's D7 order with one
  added closed reason, `arguments_invalid` (the bridge's strict
  `CallServiceArguments` refuses the arguments: a widened `data`, a
  `"direct": true` key, a domain narrowed away by `HA_ALLOWED_DOMAINS`),
  checked right after the domain and service. Origins: `chat` = web +
  `input_mode=text`, `voice` = web + `voice`, `node` = `node_voice` +
  `voice`; anything else is `origin_not_direct`. Read-only: the rate
  windows are only peeked (`peek_direct_calls`); the single increment per
  call is `count_direct_call`, the executor's step right before the send,
  which takes its increment back when the call is over a limit (the brief
  review's follow-up 1). Without a live state a `volume_up` is admitted
  with `needs_live_volume=True` and the executor must apply
  `volume_refusal` to its pre-send read (decision 6).
- **Port** `tools.home_ports`: `register_direct_classifier` /
  `classify_home_direct` (unregistered or raising → `not_available`) and
  `register_direct_executor` / `run_home_direct` (unregistered → `not_direct`;
  raising → `error`, never a card). `ConnectorsConfig.ready` registers the
  classifier only.
- **Owner API** (web session of the installation owner, personal workspace):
  `direct` on `POST`/`PATCH /api/v1/connectors/home-assistant/entities`
  (audited `ha.entity_direct_changed`; unticking the last eligible service
  clears the flag in the same write), `GET`/`PATCH
  /api/v1/connectors/home-assistant/direct` (audited
  `ha.direct_settings_changed`), `GET
  /api/v1/connectors/home-assistant/direct/calls?limit=` (≤ 100).
- **Settings**: `HA_DIRECT_MEDIA_KILL_SWITCH` (false),
  `HA_DIRECT_RATE_GLOBAL_PER_MINUTE` (30), `HA_DIRECT_RATE_ENTITY_PER_MINUTE`
  (10; 0 disables the class), `HA_DIRECT_VOLUME_MAX` (0.6, validated 0–1 at
  load) in `config/settings/base.py` and `.env.example`.

## Implementation note — branch 1c `feat/p13-direct-media-exec` (2026-11-20)

The executor, the code-only intent and the three provenance locks (Phase
13b brief D9, D10, D10a). From this branch on a flagged TV is direct.

- **Intent.** `assistant.Message.Intent.HOME_DIRECT = "home_direct"`
  (`assistant` migration `0015_home_direct_intent`, choices only);
  `assistant.services.EFFECT_DIRECT_HOME = "direct_home"`,
  `INTENT_EFFECTS[HOME_DIRECT] = direct_home`, and
  `VOICE_ALLOWED_EFFECTS` gains it — the only executing effect of the
  table. `apply_voice_capability_cap` keeps a voice `home_direct` only when
  `intent_payload["tool_name"] == "ha_call_service"` (else chat).
- **Lock 1 — the router cannot emit it.** `assistant/intent.py`:
  `CODE_ONLY_INTENTS = {home_direct}`, `_ALLOWED_INTENTS =
  Message.Intent.values - CODE_ONLY_INTENTS`; `parse_intent` raises
  `IntentParseError` ("unrecognized intent value") and the turn is chat.
  The seeded `friday_router_intent` prompt never names it (pinned).
- **Lock 2 — only `home.control`, only with the marker.**
  `WakeRoute.extra_effects` (only `home.control`, only `direct_home`;
  `validate_routes` refuses anything else at boot) and `_finish` checks
  every result's own effect against `{read, route.effect} ∪
  route.extra_effects` (a read result narrows and is always allowed).
  `assistant.services.home_direct_classification` is the single factory
  (used by `_home_control` and `route_typed_home_direct`); it attaches the
  module-private `_CODE_ORIGIN` object under `_provenance`. The chat view
  runs the direct branch only when `is_code_home_direct` finds that object
  by identity; anything else with the intent is chat. `strip_provenance`
  removes it before the `Message` row is saved.
- **Lock 3 — the executor never trusts the payload.**
  `connectors.home_direct.execute(*, workspace, actor, message, origin,
  channel, node_id)` takes the saved user message: it must be this
  actor's `home_direct` message of this workspace and channel, with the
  same origin and node; `connectors.home_phrases.resolve_home_command` is
  re-run on `message.content` and must return the same `ha_call_service`
  draft with canonically equal arguments. Otherwise nothing is sent, no
  `HomeDirectCall` is written, `ha.direct_provenance_refused`
  (`{source_message_id, origin, reason: provenance_mismatch, detail}` —
  `detail` a closed token, never argument text) is audited, and the turn
  answers with the matcher's current result (its card, ask or refusal).
- **Execution order.** `deny_if_unattended("ha_direct_call")` → lock 3 →
  `classify` (pre-check, no audit) → `precondition_state` (the bridge's
  shared half of `target_present`: strict arguments, the allowlist, one
  read of the entity; `target_unavailable` → the card) → under
  `HomeDirectSettings`' row lock: `classify` again with the live state
  (kill switches, flag, the decision 6 `volume_up` rule on this pre-send
  read), `count_direct_call` (the only increment; over a limit →
  `ha.direct_rate_limited`, the card), `clamp_volume`, one
  `HomeDirectCall(running)` committed → one `connectors.bridges.call_tool`
  (the manifest's pinned schema, `CONNECTOR_WRITE_TIMEOUT_SECONDS`) →
  one fenced finalize (`succeeded` / `failed` / `unknown_outcome`, the
  `ConnectorWriteExecution.ErrorType` vocabulary) audited
  `ha.direct_call`. Never retried. A failure before anything reached the
  transport (`pin_mismatch`, …) is recorded `failed` and the turn is the
  card; a failure after a send attempt is reported ("komut gitmiş
  olabilir, tekrar denemedim"), never replaced by a card (decision 9).
  The call row keeps a state summary only (`state`, `volume_level`,
  `is_volume_muted`) — no Home Assistant names or titles.
- **Executor-only reasons.** `provenance_mismatch` and
  `target_unavailable` (the pre-send read refused or failed, or the send
  was refused unsent). The closed reason set is `CLASSIFY_REASONS` +
  `EXECUTOR_REASONS`; each has a Turkish card text.
- **Recovery.** `connectors.tasks.recover_stale_connector_writes` also runs
  `home_direct.mark_lost_direct_calls`: a `running` call older than
  `CONNECTOR_WRITE_LEASE_SECONDS` → `unknown_outcome` / `worker_lost`,
  audited, never re-sent.
- **Ports.** `ConnectorsConfig.ready` registers the classifier and the
  executor; `tools.home_ports.DirectRun` gains `tool_name` / `arguments`
  (the fallback card's re-resolved arguments).
- **Paths.** Web voice and `node_voice`: `home.control` classifies a draft
  through the port; direct → `home_direct` (origin `voice` / `node`, the
  voice token's device as the node id); not direct → the Phase 13 card
  carrying `direct_reason`. Typed web: `assistant.services.
  route_typed_home_direct` runs before `router.intent` and returns a
  classification only for a direct draft (origin `chat`); every other typed
  turn reaches the router as in Phase 13. The view saves the user message,
  then `run_direct_turn` calls the executor and finalises the message's
  intent (`home_direct`, or `tool_request` for the fallback card); the
  reply is a fixed Turkish template through `static_reply_stream` — no
  gateway call, no `ModelUsage`.
- **Tests.** `connectors/tests/test_home_direct_execution.py`;
  `assistant/tests/test_home_direct_{routes,node_voice,typed,provenance}.py`,
  `test_voice_cap_home_direct.py`; `ops/tests/test_home_direct_voice_e2e.py`
  (the reference voice host and a real `friday-voice run` child); the
  registry walk (`connectors/tests/test_ha_allowlist_gate.py`) now pins the
  call log's writers to `execute`'s two helpers and the recovery scan.

## Implementation note — branch 1d `feat/p13-direct-media-ui` (2026-11-20)

Presentation only (Phase 13b brief D11): nothing becomes direct, and no
new writer of `direct`, `HomeDirectSettings` or `HomeDirectCall` exists.

- **HUD.** `health.home_direct = {state, flagged, calls_last_hour}`,
  `state` ∈ {`on`, `off_owner`, `off_env`, `not_configured`}, read through
  the read-only `tools.home_ports.home_direct_status` port
  (`connectors.home_direct.hud_status`); one import-linter edge
  `hud.services -> tools.home_ports`. DB only, never a Home Assistant
  request; the HUD shows the line and offers no toggle.
- **`off_env`** also covers a rate limit of 0 (decision 5's "0 disables"):
  `direct_state` now agrees with the classifier.
- **`/connectors`.** The per-entity "Doğrudan (onaysız)" switch (eligible
  media players only; turning it on is confirmed) and the owner panel
  (kill switch, volume maximum, env-kill notice, the closed list, the
  limits, the last 20 calls), over the existing owner-web endpoints.
- **Card reason.** A Home Assistant card that was not direct shows
  "Doğrudan değil: <reason>" for the reasons that concern a flagged or
  flaggable player; the frontend's texts are pinned to `REASON_TEXT`.
