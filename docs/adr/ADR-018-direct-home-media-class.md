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
