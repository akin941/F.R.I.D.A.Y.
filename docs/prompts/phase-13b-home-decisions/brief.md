# Phase 13b — Owner decisions 2026-11-26: interim Mac server, R2, TPM, direct home-media class (server side + Mac session)

> Status: **Brief implemented 2026-11-26 on `docs/p13b-brief` (docs only;
> review round 1 findings addressed 2026-11-26) for the owner decisions of
> 2026-11-26, a follow-up to Phase 13 ("Home hub",
> `docs/prompts/phase-13-home-hub/brief.md`). It needs its independent
> Sonnet 5 plan review to reach APPROVE before branch 1a, because the
> implementing model never reviews its own brief.**
> Sources: the owner decisions of 2026-11-26 (recorded below as "Owner
> decisions kept"); the Phase 13 brief (D11–D15, "Open questions" Q1, Q5,
> Q6, "Owner steps (pending)", "Known follow-ups"); ADR-017 (home hub
> topology); `docs/runbooks/home-server-setup.md`; the loopback-only TLS
> path of `feat/p9-node-local-pairing` (merged into `integ/phase-13` as
> `ee4de71`: `deploy/docker/compose.local-tls.yml`, `Caddyfile.local`,
> `gen-local-tls.sh`, `docs/runbooks/friday-node.md` "A node on this Mac
> only"); `docs/research/2026-11-roadmap-proposal.md` §c.2 (the voice cap)
> and its failure-mode table; MASTER_PLAN §AA "Friday 3.0 phases" (this
> phase is added as row 9b, "13b Owner decisions").
> Parent decisions:
> - ADR-001: workspace axis, `for_actor()`. The direct flag, the direct
>   settings and the direct-call log are rows of the owner's **personal**
>   workspace only.
> - ADR-002: modular monolith. No new app; new models in `connectors`
>   only; `assistant` keeps reaching `connectors` only through the
>   `tools.home_ports` port (ADR-010 contract unchanged).
> - ADR-003 and its 2026-11-20 amendment: single gateway door. **Phase 13b
>   adds no gateway purpose and no model call**: the direct path is the
>   deterministic home matcher; a typed direct turn makes *fewer* model
>   calls (no router call), never more.
> - ADR-004 / ADR-008 decision 3 / ADR-009 / ADR-010: unchanged text; every
>   HA call outside ADR-018's closed class stays gate-class.
> - ADR-005: Postgres is the only source of truth — on the interim Mac as
>   on the home PC.
> - ADR-013 (node protocol): **unchanged**. No WS message, schema field or
>   capability contract; `node_protocol_frozen` must still pass.
> - ADR-014 (node action classes): **unchanged** for node actions; it gets
>   an ADR-018 cross-reference (written in this branch).
> - ADR-015 (voice-originated turns): decision 1 gets its single exception
>   through ADR-018; a cross-reference note is written in this branch.
> - ADR-016 (unattended runs): an unattended run can never make a direct
>   call (`deny_if_unattended` first, and the classifier refuses the
>   origin).
> - **ADR-017** (home hub topology): **amended 2026-11-26** in this branch
>   — interim server on the MacBook, R2 as the off-site target, TPM
>   auto-unlock with an owner-accepted residual, the ADR-018 cross
>   reference (section "Amendment — owner decisions 2026-11-26 (Phase
>   13b)").
> - **ADR-018** (new, Accepted 2026-11-26, owner decision, written in this
>   branch): the direct-execute home media class — the one, deliberate,
>   owner-approved narrowing exception to CLAUDE.md rule 4, ADR-014
>   decision 4 and ADR-015 decision 1. CLAUDE.md rule 4 gains one sentence
>   naming it (written in this branch; no other rule text changes).
>
> **Owner decisions kept (2026-11-26).** The task named a stub at
> `docs/prompts/phase-13b-home-decisions/brief.md`; **no stub existed** on
> the integration branch (the directory did not exist), so there were no
> stub decisions or owner steps to carry over. The owner decisions, as
> recorded in the cloud session, are kept unchanged as this brief's goal,
> scope and boundary:
> 1. **Interim server on the owner's MacBook:** until a home PC is bought,
>    the Friday Compose stack runs on the MacBook (Docker Desktop or
>    OrbStack); Friday is down while the Mac sleeps or is off. Runbook
>    section "Interim: server on the MacBook" (Compose on macOS, LAN bind,
>    private-CA TLS for the node gateway, nightly backups while awake, the
>    Mac's own node over the existing loopback-only TLS path, the later
>    move to a home PC = restore + re-point, as the VPS path). Preflight
>    home checks must not FAIL merely because the server is on macOS (no
>    LUKS/TPM there): FileVault is an owner-confirmed WARN; with tests.
> 2. **Off-site backup = Cloudflare R2** (resolves Phase 13 Q5): restic/age
>    to R2 as the default; `.env.example` gains the R2 variable **names**
>    only; preflight backup freshness checks the R2 repository when
>    configured.
> 3. **Disk unlock = TPM auto-unlock** (resolves Phase 13 Q6) on the future
>    home PC: `systemd-cryptenroll --tpm2-device=auto` with PCR 7, a
>    recovery key kept off the server; the residual risk (a stolen whole
>    machine boots) recorded in ADR-017 as owner-accepted.
> 4. **Direct-execute home media class** (overrides Phase 13 Q1; a
>    deliberate, owner-approved narrowing exception to CLAUDE.md rule 4 and
>    ADR-014 decision 4): HA `media_player` play, pause, play_pause, stop,
>    volume_up, volume_down, volume_set, volume_mute **only**, **only** on
>    entities the owner allowlisted on `/connectors` **and** flagged
>    "direct" (a new per-entity owner-set boolean, default off, settable
>    only by the owner through the UI/API, never by an agent, tool or model
>    output), run without an approval card, also from voice-originated
>    turns. Everything else stays gate-class exactly as today. Safeguards:
>    audit with origin (voice/chat/node) and entity; rate limits (30/min
>    global, 10/min per entity, env-configurable, documented in
>    `.env.example`); one owner kill switch (env + `/connectors` toggle);
>    `volume_set` clamped to an owner-set maximum (default 0.6); the
>    classifier is a closed allowlist in code (a new service needs an ADR
>    amendment); tests that a non-media domain, a non-flagged entity, an
>    agent setting the flag and the kill switch all still reach the gate.
>    ADR-018, ADR-014/ADR-017 cross references, one CLAUDE.md rule 4
>    sentence.
> 5. **Housekeeping** (docs only, this branch): the stale integration
>    branch name in the handoff, the spike branch name, the three Phase 13
>    LOW follow-ups. The proposed Phase 13 defaults and the Linux node's
>    typed-directory-name confirmation are **not** confirmed by the owner
>    and stay pending owner steps.
>
> **Platform boundary (owner decision 2026-11-20).** The implementation
> container is Linux, with no macOS and no Xcode.
> - **In scope now, with full branches (1a–1d, 2 and the closeout):** the
>   direct media class end to end on the server (model flag, classifier,
>   rate limits, kill switch, audit, the voice, `node_voice` and typed
>   paths, the `/connectors` UI and the HUD line), tested against the fake
>   HA (`httpx.MockTransport`), the Phase 11 reference voice host and the
>   real `friday-voice run` child; the macOS-tolerant preflight, the R2
>   configuration and status check, the portable backup scripts (tested
>   under a PATH shim that hides GNU-only tools), the launchd plist (parsed
>   and validated, never loaded), the Mac compose override (parsed by the
>   compose tests).
> - **Not built here:** anything that must run on macOS or real hardware —
>   running the stack under Docker Desktop/OrbStack on the real MacBook,
>   FileVault, `launchd`, Homebrew Caddy on the Mac's LAN address, the
>   real R2 bucket, pairing the real Swift node over loopback on the same
>   machine, the real TV, and the future home PC with TPM unlock. They are
>   listed under "Mac session (deferred)", marked "deferred: Mac session"
>   (or "deferred: owner hardware") in the branch table, appended to
>   `docs/runbooks/mac-session-handoff.md` as **Part F** by the closeout,
>   and must pass the server-side contract tests committed now. **No
>   Swift change is needed** (no protocol change); the Mac session uses
>   the existing `friday-node` app. Never touch `node/friday-node/**` in a
>   13b branch: a Mac session pushes Swift work to `integ/phase-13`
>   concurrently.
>
> Go/no-go items that need the Mac, R2, the real TV or the home PC are
> recorded as **pending owner evaluation** and do not block the backlog.
>
> **Precondition.** Phase 13 closeout merged on the integration branch (it
> is: `chore/p13-closeout`, 2026-11-26), with `feat/p9-node-local-pairing`
> (`ee4de71`). Phase 13b uses the Phase 13 HA entity allowlist
> (`connectors.HomeEntity`, `connectors.home_entities`), the bridge
> (`connectors/bridges/home_assistant.py`, manifest v2), the home matcher
> (`connectors.home_phrases`) and port (`tools.home_ports`), the
> `home.control` wake route, the ADR-015 cap (`assistant.services.
> apply_voice_capability_cap`), the home preflight rows (`check_home`,
> `check_home_control`) and the backup/drill scripts (`deploy/home/`).
>
> **Branches.** Cut from the local integration branch `integ/phase-13`;
> server branches keep the Phase 13 `feat/p13-…` prefix (the decisions
> extend Phase 13, MASTER_PLAN stable-numbering rule); the docs and
> closeout branches carry `p13b`.
>
> **Dates.** Every status and owner-step stamp in this brief is
> 2026-11-26 (the date of the owner decisions; the Phase 13 follow-up 3 on
> mixed dates is resolved the same way there).

## Goal / value

Friday becomes usable at home **now**, on the one machine the owner has,
with the same safety story it will have on the home PC — and pausing the
film stops costing two screen taps.

Concretely, at the end of the phase:

1. **The MacBook is the server, for now.** `make up-mac-server` brings up
   the daily stack, the home override and the loopback TLS proxy under
   OrbStack or Docker Desktop. The Mac's own `friday-node` pairs with
   `https://127.0.0.1:8443` and the typed pin; nothing listens on the LAN
   unless the owner starts the host Caddy for other PCs. `make preflight`
   shows `home_server_platform` **warn** ("interim server: Friday is down
   while the Mac sleeps") and `home_disk_encryption` **warn** ("FileVault
   owner-confirmed on 2026-11-27; no LUKS/TPM on macOS") — and **no
   failure caused by macOS**.
2. **Backups go to R2.** At 03:10, or on the first wake after it, the
   LaunchAgent runs `friday-backup`; the `age`-encrypted dump goes to the
   owner's R2 bucket with `restic`. `status.json` records the repository;
   `home_backup_fresh` passes only when the last backup is fresh **and**
   went to the configured R2 repository. The bucket token is only in the
   host's restic environment file; no container sees it.
3. **The home PC, later, boots by itself.** The runbook's step 1 enrols a
   recovery key and a PCR 7 TPM slot; the owner accepted that a stolen
   whole box boots to the login prompt (ADR-017 amendment 3). Moving from
   the Mac is a restore plus a re-point (runbook "Interim" I6).
4. **"Hey Friday, TV'yi duraklat."** On `/connectors` → Home Assistant the
   owner flags `media_player.salon_tv` "Doğrudan (onaysız)". Said on the
   MacBook: the TV pauses; no card, no `/approvals` entry; the audit log
   has `ha.direct_call` with origin `node`, the node id, the entity and
   the service; the HUD says "Doğrudan medya: açık · 1 cihaz · son 1 saatte
   1 çağrı". "Sesi yüzde 90'a ayarla" sets 60 % and says so. "Salondaki
   TV'yi aç" is still a card. The eleventh call in a minute is a card.
   One tap on "Doğrudan medyayı kapat" (or `HA_DIRECT_MEDIA_KILL_SWITCH=
   true`) and every one of these is a card again.

## What already exists (verified 2026-11-26 on the integration branch)

- **HA bridge** (`connectors/bridges/home_assistant.py`): `SERVICES` is the
  code maximum; `media_player` has `turn_on`, `turn_off`, `media_play`,
  `media_pause`, `select_source` (`SelectSourceData`, a live `source_list`
  value), `volume_set` (`VolumeSetData{volume_level: 0..1}`),
  `volume_mute` (`VolumeMuteData{is_volume_muted: bool}`), `volume_up`,
  `volume_down`. `media_play_pause` and `media_stop` are **absent**
  (Phase 13 D12: "toggles stay absent"). `NEVER_DOMAINS`,
  `READ_ONLY_DOMAINS`, `WAKE_ON_LAN` (MAC allowlist, no entity).
  `allowlisted_entity(workspace, entity_id)` (enabled rows only),
  `allowlist_refusal`, `check_allowlist` (the manifest's `scope_check`),
  `request_rows`, `target_present` (precondition), `_call_service` (strict
  re-validation, pre-send read, one POST, read back; `sent` semantics for
  `unknown_outcome`). `connectors.bridges.call_tool(manifest, *,
  server_tool, arguments, schema_sha256, timeout)` is the entry point the
  write executor uses (`connectors.writes.execute_claimed_write`).
- **Manifest** (`connectors/manifests/home_assistant.py`): version 2,
  `STATES_PIN`, `CALL_SERVICE_PIN` over `tool_schemas()`; `ha_call_service`
  is `TOOL_WRITE`, `gate_name="ha_call_service"`, `user_invocable=True`,
  `records_work_log=False`, `draft_arguments=True`.
- **Writes** (`connectors/writes.py`): every write is a `gated=True`
  `ToolSpec`; `ConnectorWriteExecution` has a **required one-to-one
  `approval_request`** — there is no execution record without an approval.
  `mark_lost_executions` moves a stale `running` row to `unknown_outcome`.
- **Allowlist** (`connectors.HomeEntity`, migration `0005_home_entity`):
  `entity_id`, `room`, `label`, `key`, `aliases`, `services`, `enabled`;
  `save()` re-checks the code maximum. `connectors.home_entities`:
  `create_entity` / `update_entity` / `delete_entity` (owner web only via
  `require_owner_web`, personal workspace, audited `ha.entity_allowed` /
  `_changed` / `_removed`), `entity_candidates`. Views
  `HomeEntityListView`, `HomeEntityDetailView`,
  `HomeEntityCandidatesView` (`/api/v1/connectors/home-assistant/
  entities[/<uuid>]`, `/candidates`); serializers `HomeEntityWriteSerializer`
  (unknown fields refused), `HomeEntitySerializer`. Registry-walk test
  `connectors/tests/test_ha_allowlist_gate.py` (nothing but the owner-web
  service writes `HomeEntity`).
- **Home matcher and port** (`connectors/home_phrases.py`,
  `tools/home_ports.py`): `resolve_home_command(workspace, actor, text) ->
  HomeResolution(outcome draft|ask|refuse, tool_name, arguments, reply,
  code)`; verbs map to one `(service, data)` (`durdur` → `media_pause`
  today); `check_call_draft` for typed `router.connector_args` drafts
  (`target_mismatch`). The port module imports nothing of ours.
- **Wake routes** (`assistant/wake_routes.py`): `EFFECT_READ`,
  `EFFECT_PROPOSAL`, `ALLOWED_EFFECTS`; `WakeRoute(name, effect,
  default_phrases, handler, matched_by_code)`; `CODE_ROUTES =
  (home.control,)` whose handler returns a `TOOL_REQUEST` draft;
  `validate_routes` at boot; `route_voice_turn(...)` runs for voice turns
  only (web voice and `node_voice`); typed turns are not wake-routed
  (Phase 12 rule).
- **The cap** (`assistant/services.py`): `INTENT_EFFECTS` (`TOOL_REQUEST`
  → proposal), `VOICE_ALLOWED_EFFECTS = {read, proposal}`,
  `apply_voice_capability_cap(classification, *, input_mode)`.
  `Message.Intent` choices (`assistant/models.py`): chat, memory_query,
  knowledge_query, task_draft, tool_request, connector_read (latest
  migration `0014_async_followups`). The chat view (`assistant/views.py`)
  applies the cap, runs at most one connector read, emits the `draft` SSE
  event for `TOOL_REQUEST`, and answers a route's fixed text with
  `static_reply_stream` (no gateway call, no `ModelUsage`).
- **Rate-window pattern**: `accounts.voice_live.count_transcription` — a
  sliding window of one-second buckets in the Django cache (Redis in
  production), `cache.add` + `cache.incr` + one `get_many`.
- **Unattended guard**: `core.unattended.deny_if_unattended`.
- **HUD**: `hud.services.SECTIONS` (… `health`, `nodes`),
  `health_section(workspace, actor)`; frontend `components/hud/
  HudPanels.tsx`. `/connectors`: `components/connectors/ConnectorsView.tsx`,
  `HomeEntitiesEditor.tsx` (+ tests).
- **Home ops**: `deploy/docker/compose.home.yml` (`HOME_*` settings,
  status directory bound read-only, nothing published),
  `deploy/docker/compose.local-tls.yml` + `Caddyfile.local` (Caddy on
  `127.0.0.1:8443`, three node paths, 404 elsewhere, no trust store),
  `Makefile` targets `up-daily`, `up-home`, `local-tls`, `up-local-tls`;
  `deploy/home/friday-backup.sh` (bash with `shopt -s inherit_errexit`
  (bash ≥ 4.4), `flock`, `stat -c`, `sha256sum` — GNU/Linux only today;
  `status.json {v:1, finished_at, dump, bytes, sha256,
  restic_snapshot}`), `friday-restore-drill.sh` (`sha256sum`),
  `friday-backup.{service,timer}`, `Caddyfile.example`. Preflight
  `check_home`: `home_lan_only`, `home_tls_pin`, `home_backup_fresh`,
  `home_restore_drill_age`, `home_disk_encryption` (`HOME_FDE_CONFIRMED_ON`
  attestation, never a fail — it already warns, so no row fails on macOS
  for disk encryption today, but its text says "LUKS"); `check_home_control`:
  `nodes_labels`, `ha_entities`, `linux_node_vendor`,
  `node_protocol_frozen`. Tests: `ops/tests/test_preflight_home.py`,
  `test_preflight_home_control.py`, `test_home_backup_restore.py`,
  `test_compose_no_public_ports.py`, `config/tests/test_compose_files.py`,
  `test_home_hub_e2e.py`, `test_home_voice_e2e.py`; CI job `home-ops`.
  Settings `HOME_BACKUP_STATUS_PATH`, `HOME_RESTORE_DRILL_STATUS_PATH`,
  `HOME_BACKUP_WARN_HOURS`/`FAIL_HOURS`, `HOME_DRILL_WARN_DAYS`/`FAIL_DAYS`,
  `HOME_FDE_CONFIRMED_ON` (`config/settings/base.py`, `.env.example`).
  No `HOME_SERVER_PLATFORM`, no off-site repository setting, no R2 name
  in `.env.example`, no launchd file.

## Boundary vs existing systems

- **One narrow class, not a new trust level.** The direct class is the
  eight services of ADR-018 on flagged `media_player` entities, reached by
  the deterministic matcher from owner-attended turns. It is not
  confirm-class, is not a `ToolSpec` flag, and is not available to any
  other tool, connector, node capability or channel.
- **No new protocol surface.** No WS message, schema field, capability
  contract or `friday-voice/v1` change; `node_protocol_frozen` and
  `build_node_conformance --check` stay green and unchanged.
- **No model call added.** The direct path is code; a typed turn that is
  direct skips `router.intent`. Every path asserts the `ModelUsage` count.
- **Gate behaviour unchanged.** Any call that is not direct goes through
  exactly the Phase 13 path (draft card → `/approvals` →
  `ConnectorWriteExecution`), byte for byte; the Phase 13 tests stay green
  (only the `durdur` vector moves to `media_stop`, D7).
- **Backups stay host operations.** Friday never starts, changes or reads
  a backup and never holds R2 credentials (ADR-017 decision 6); preflight
  reads the status file's non-secret repository identifier.
- **The interim Mac is still one server.** Nothing here supports two
  servers, a sync, or a node on the Mac holding Friday data.
- **Workplace products (ADR-006) and the fleet layer (§P)** are untouched.

## Decisions (with justification)

### D1 — The interim server is the existing stack on macOS, declared by the owner

- New setting `HOME_SERVER_PLATFORM` (`linux` | `macos`, default
  `linux`; any other value makes `home_server_platform` fail). A container
  cannot detect its host OS reliably (Docker Desktop and OrbStack run a
  Linux VM), so the owner declares it, as he already attests disk
  encryption (`HOME_FDE_CONFIRMED_ON`).
- `deploy/docker/compose.mac.yml` (override): `HOME_SERVER_PLATFORM:
  macos` into web and worker; publishes nothing. `make up-mac-server` =
  `compose.yml` + `compose.daily.yml` + `compose.home.yml` +
  `compose.local-tls.yml` + `compose.mac.yml`.
- The Mac's own node pairs over the loopback TLS proxy
  (`compose.local-tls.yml`, unchanged): strong code, typed SPKI pin.
  Other LAN PCs go through Homebrew Caddy on the host bound to the Mac's
  LAN address with the private CA (`Caddyfile.example`, unchanged), and
  macOS's application firewall. The Phase 13 closeout's open pairing-route
  decision (Caddy → BFF) applies there too and stays an owner step.
- Preflight on `macos` (branch 2): new row `home_server_platform` (pass
  `linux`, **warn** `macos` "interim server (ADR-017 amendment 1): Friday is
  down while the Mac sleeps", fail unknown); `home_disk_encryption` reads
  `HOME_FDE_CONFIRMED_ON` as the **FileVault** attestation and is always
  **warn** on `macos` ("FileVault owner-confirmed on <date>; no LUKS/TPM on
  macOS (interim)" or "FileVault not confirmed"), never pass and never
  fail. Every other `home_*` row is platform-independent and keeps its
  rules — a stale backup fails on a Mac as on a PC (that is real, not
  "merely macOS").

*Justification:* the owner has no home PC yet, and a Friday that waits for
one is not used. The stack already runs on Docker for Mac; what differs is
the disk encryption (FileVault, unseen by a container), the scheduler
(launchd), the GNU tools in the scripts, and the honest statement that
the server sleeps. Making the platform explicit keeps preflight from
pretending (no "LUKS confirmed" on a Mac) and from failing for the wrong
reason.

### D2 — Off-site backups go to Cloudflare R2; preflight checks the destination, not the bucket

- Default target: restic's S3 backend to R2
  (`RESTIC_REPOSITORY=s3:https://<account-id>.r2.cloudflarestorage.com/
  <bucket>`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
  `AWS_DEFAULT_REGION=auto`, `RESTIC_PASSWORD_FILE`), a bucket-scoped
  token. These live in the **host's** restic environment file only
  (`/etc/friday-backup/restic.env` on Linux,
  `~/.config/friday-backup/restic.env` on the Mac); `.env.example` lists
  their **names** under a "host-only — never set in deploy/docker/.env"
  heading, with empty values, and `deploy/home/restic.env.example` is the
  host template (names only). A test proves no compose file passes any
  `RESTIC_*`/`AWS_*` variable into a container.
- `friday-backup.sh` records the repository in `status.json` as a new
  optional field `repository` (the `RESTIC_REPOSITORY` value; the script
  refuses to record — and fails the run — if it contains `@` userinfo or a
  `?` query, so no credential ever reaches the status file). The format
  stays `v: 1` (additive; old files without the field stay readable).
- New Django setting `HOME_BACKUP_OFFSITE_REPOSITORY` (non-secret; same
  refusal of userinfo/query at preflight). `home_backup_fresh` when it is
  set: `repository` absent → **warn** ("the last backup did not record
  its repository — update friday-backup.sh"); different → **fail** ("the
  last backup went to <scheme+host>, not the configured off-site
  repository"); equal → the existing age rules, the detail naming "R2"
  when the host ends in `.r2.cloudflarestorage.com`. Unset → today's rules
  (and the runbook's "off-site pending").
- No preflight call to R2: the web container has no bucket credentials by
  design, and a status file written by the host after a successful
  `restic backup` is the evidence (the monthly off-site drill proves the
  remote copy, runbook step 9).

*Justification:* R2 is S3-compatible (restic supports it natively), has no
egress fees for restores and was already the recommended answer to Q5.
Checking the recorded destination catches the realistic failure (the
script quietly pointed at a local disk after an edit) without giving the
server a key that could delete the backups.

### D3 — TPM auto-unlock with PCR 7 and an off-server recovery key (home PC)

Runbook step 1 (written in this branch): `systemd-cryptenroll
--recovery-key` first (printed once; password manager + paper), then
`--tpm2-device=auto --tpm2-pcrs=7`; `dracut` instead of
`initramfs-tools` so the TPM token is used at boot (Ubuntu's default
initramfs does not read it); re-enrol after a firmware/Secure Boot change.
ADR-017 amendment 3 records the owner-accepted residual: the stolen
*whole* machine boots to the login prompt. No code: a container cannot
attest a TPM slot. `HOME_FDE_CONFIRMED_ON` keeps meaning "LUKS confirmed"
on `linux`.

*Justification:* the server must survive a power cut with nobody home;
PCR 7 binds the unlock to the Secure Boot state, so a changed boot chain
or a removed disk does not unlock; the recovery key keeps the owner able
to unlock after a firmware update.

### D4 — The direct class is ADR-018's closed allowlist, in code

`connectors/home_direct.py`:

```python
DIRECT_MEDIA_DOMAIN = "media_player"
DIRECT_MEDIA_SERVICES = frozenset({
    "media_play", "media_pause", "media_play_pause", "media_stop",
    "volume_up", "volume_down", "volume_set", "volume_mute",
})
```

- `DIRECT_MEDIA_DIGEST` = sha256 of the sorted names joined by `\n`; a
  test and the closeout's `ha_direct_media` preflight row compare it with
  the value recorded in ADR-018's implementation note (branch 1b writes the
  note). Changing the set without an ADR amendment fails CI and preflight.
- Only these eight; the domain is fixed; `data` is whatever the bridge's
  strict serializer for that service accepts (`NoData`, `VolumeSetData`,
  `VolumeMuteData`) — the direct class adds no field.
- The bridge's code maximum gains `media_play_pause` and `media_stop`
  (both `NoData`), so the eight exist at all. Outside the direct class
  they are gate-class like every service; the decision dialog for
  `media_play_pause` shows the live state and the outcome it implies
  ("şu an oynatılıyor → duraklatılır"), resolving the D12 objection to
  toggles for the card. `tool_schemas()` changes → `CALL_SERVICE_PIN`,
  `STATES_PIN` (if its listing changes) and manifest `version` 2 → 3 in the
  same reviewed commit, with `docs/mcp/home-assistant.md` and
  `docs/mcp/allowlist.md` (MCP governance).

*Justification:* the owner asked for a closed allowlist that only an ADR
amendment can widen; a code constant plus a pinned digest makes widening
a reviewed, visible change instead of a setting.

### D5 — The per-entity `direct` flag, owner-web only

- `HomeEntity.direct = BooleanField(default=False)` (migration
  `connectors` 0006). `HomeEntity.save()` refuses `direct=True` unless the
  domain is `media_player` and `services ∩ DIRECT_MEDIA_SERVICES` is
  non-empty (`ValidationError("ha_entity.direct_not_allowed")`) — on every
  write path, as the code maximum is today.
- Set only through `connectors.home_entities.create_entity(direct=…)` /
  `update_entity(direct=…)` (owner, web, personal workspace,
  `require_owner_web`), over the existing `POST`/`PATCH
  /api/v1/connectors/home-assistant/entities[/<uuid>]`
  (`HomeEntityWriteSerializer` gains `direct`; it already refuses unknown
  fields). A change of `direct` is audited **separately** as
  `ha.entity_direct_changed` (`{home_entity_id, entity_id, direct: bool,
  direct_services}`), so the audit view can list every widening.
  Unticking a service that was the entity's last direct-eligible one sets
  `direct=False` in the same write (audited).
- Never by an agent: no `ToolSpec`, `ReadToolSpec`, wake route, schedule
  kind, probe, connector manifest, node capability or management command
  writes `direct`, `HomeDirectSettings` or `HomeDirectCall` except the
  owner-web services and the direct executor's own log; the Phase 13
  registry walk (`test_ha_allowlist_gate.py`) is extended to all three.
  The `ha_call_service` argument serializer stays strict, so a model's
  `{"direct": true}` in draft arguments is a validation error, never a
  flag.

*Justification:* the owner's explicit, per-entity act is the safeguard; a
separate audit action makes "who made the TV direct, when" one query.

### D6 — Owner settings: the kill switch and the volume maximum

```
HomeDirectSettings(WorkspaceScopedModel, TimestampedModel)   # connectors
  workspace    FK (personal only; unique)
  kill_switch  bool  default False     # True = every direct call is a card
  volume_max   Decimal(3,2) 0.00–1.00  default HA_DIRECT_VOLUME_MAX (0.60)
```

- Created lazily on first read with the defaults; read with `for_actor()`.
- `GET/PATCH /api/v1/connectors/home-assistant/direct` (owner web only;
  audited `ha.direct_settings_changed` with the changed fields and
  values) returns `{kill_switch, env_kill_switch (read-only),
  state: on|off_owner|off_env, volume_max, rate_global_per_minute,
  rate_entity_per_minute (read-only, from env), services (read-only, the
  closed list), flagged_count}`.
- Env: `HA_DIRECT_MEDIA_KILL_SWITCH` (bool, default false),
  `HA_DIRECT_RATE_GLOBAL_PER_MINUTE` (30), `HA_DIRECT_RATE_ENTITY_PER_MINUTE`
  (10), `HA_DIRECT_VOLUME_MAX` (0.6, the initial owner value; validated
  0–1 at settings load). All in `config/settings/base.py` and
  `.env.example` with comments.
- **Kill switch semantics:** env **or** owner toggle on → state `off_*`;
  checked at classification and again under a row lock immediately before
  the send. There is no queue: the next utterance after the toggle is a
  card.

*Justification:* one switch the owner can reach from the UI in a second,
and one in the environment that works even if the database or the UI is
the problem.

### D7 — The deterministic path: the home matcher decides, the classifier admits

- `connectors.home_phrases` verbs: `STOP` (TR `durdur`, EN `stop`) →
  `media_stop` (**`durdur` moves from `media_pause` to `media_stop`**;
  `duraklat` stays pause); `PLAY_PAUSE` (TR `oynat duraklat`, EN `play
  pause`, `toggle play`) → `media_play_pause`. Vectors in
  `docs/voice/home-route-vectors.json` updated in the same commit.
- `connectors.home_direct.classify(*, workspace, actor, arguments,
  origin, channel) -> DirectVerdict(direct: bool, reason: str)`. `direct`
  only if **all** hold, otherwise `reason` is the first failing closed
  code:
  1. not unattended (`unattended`); `origin` ∈ {`voice`, `node`, `chat`}
     and the channel matches it (`voice` = web + `input_mode=voice`,
     `node` = `node_voice`, `chat` = web + typed) (`origin_not_direct`);
  2. the actor is the installation owner and the workspace is his
     personal one (`not_owner`);
  3. env kill switch off (`kill_switch_env`), owner kill switch off
     (`kill_switch_owner`);
  4. `domain == media_player` (`not_media_player`), `service ∈
     DIRECT_MEDIA_SERVICES` (`service_not_direct`);
  5. `allowlisted_entity` finds an enabled row (`entity_not_allowed`),
     `service ∈ entity.services` (`service_not_allowed`), `entity.direct`
     (`not_flagged`);
  6. the manifest is active and `ha_call_service`'s binding is enabled
     with tool status `ok` (`not_available`);
  7. volume rules (D9) (`volume_unknown`, `volume_at_max`);
  8. rate limits (D8) (`rate_limited`, `rate_store_unavailable`) — counted
     last, only for a call that is otherwise direct.
- **Only resolver output reaches the classifier.** The `home.control`
  route (voice, `node_voice`) and a new typed pre-router step (D10) pass
  the `HomeResolution` draft's exact arguments, and D10a makes that
  enforceable rather than a convention: the model router cannot emit the
  direct intent, only `home.control` may return it, and the executor
  re-derives the arguments from the stored message text. Router-extracted
  arguments (`router.connector_args`), a draft card the owner taps
  (`POST /approvals`), schedules, proactive runs, agents, the harness, CLI,
  the OpenAI facade, Telegram and hooks never call `classify`.
- A non-direct verdict is never a refusal: the turn continues exactly as
  Phase 13 would (voice: the draft card; typed: the router), and the
  reason is shown on the card ("Doğrudan değil: işaretli değil").

*Justification:* the matcher already turns the owner's words into exact
arguments without a model; reusing it means the direct path inherits its
vectors, its ambiguity rule (ask, never guess) and its allowlist checks.
Admission as a separate, ordered, closed-reason function makes every
"why was this a card" answerable and testable.

### D8 — Rate limits fail closed to the gate

- Two sliding one-minute windows of one-second buckets in the Django
  cache (the `accounts.voice_live.count_transcription` pattern):
  `connectors:ha-direct:<workspace_id>` and
  `connectors:ha-direct:<workspace_id>:<entity_id>`. A call is counted
  only when it is about to be sent; the 31st (global) or 11th (per
  entity) within 60 s is not direct (`rate_limited`), audited
  `ha.direct_rate_limited` (`{entity_id, service, origin, limit: global|
  entity}`) and becomes the card.
- Any cache error → `rate_store_unavailable` → the card. A limit of 0
  disables the direct class (documented).

*Justification:* a TV playing a video that says "Hey Friday, sesi aç" in
a loop must not drive the speaker; falling back to the card keeps the
owner's request possible while the storm is visible.

### D9 — Volume: clamp `volume_set`, guard `volume_up`

- Direct `volume_set` with `volume_level > volume_max` is sent as
  `volume_max`; `HomeDirectCall.requested_data` keeps the asked value and
  the reply says "Ses en fazla %60 olabilir; %60'a ayarladım."
- Direct `volume_up` only when the bridge's pre-send read (the one
  `_call_service` already makes) gives a numeric `volume_level` and
  `volume_level + 0.1 <= volume_max` (`DIRECT_VOLUME_UP_HEADROOM = 0.1`, a
  code constant). Otherwise not direct: `volume_unknown` /
  `volume_at_max` → the card (whose dialog shows the live volume).
  Because the read happens in the executor, the executor re-runs this rule
  on that read and, if it fails, sends nothing and returns `fallback`.
- `volume_down`, `volume_mute` (both ways) and the playback services have
  no volume rule. The maximum never applies to a gate-class call (the
  owner reads and approves the exact value).

*Justification:* the clamp is what makes "the TV said 'sesi sonuna kadar
aç'" harmless; a headroom rule is the closest a stateless step service
allows.

### D10 — Where the direct call runs: after the cap, in the turn, once

- `Message.Intent.HOME_DIRECT = "home_direct"` (assistant migration
  0015, choices only); `EFFECT_DIRECT_HOME = "direct_home"`;
  `INTENT_EFFECTS[HOME_DIRECT] = EFFECT_DIRECT_HOME`.
  `apply_voice_capability_cap` admits `EFFECT_DIRECT_HOME` for voice turns
  **only** when `intent_payload["tool_name"] == "ha_call_service"` (else
  chat); `VOICE_ALLOWED_EFFECTS` gains it with an ADR-018 comment. Typed
  turns pass the cap untouched, as today.
- `WakeRoute` gains `extra_effects: tuple = ()`; `home.control` declares
  `(EFFECT_DIRECT_HOME,)`; `validate_routes` refuses `extra_effects` on
  every other route and any extra effect but `direct_home` (boot error).
- Voice (web voice, `node_voice`): `_home_control` resolves as today;
  for a `draft` it asks the port `classify_home_direct(...)`; direct →
  `RouteResult(intent=HOME_DIRECT, intent_payload={"tool_name",
  "arguments", "origin", "node_id"}, reply_text=None)`; not direct →
  today's `TOOL_REQUEST` draft plus the reason.
- Typed (web, `input_mode=text`): `assistant.services.
  route_typed_home_direct(...)` runs **before** `router.intent`, calls the
  resolver through the port, and returns a `HOME_DIRECT` classification
  **only** for a direct draft; for anything else (miss, ask, refuse,
  non-direct draft) it returns `None` and the turn goes to the router
  exactly as in Phase 13 (the Phase 12 "typed turns are not wake-routed"
  rule holds for everything but this one class).
- The chat view, after the cap: `run_home_direct(...)` through the port
  (`tools.home_ports.register_direct_executor`), which calls
  `connectors.home_direct.execute(...)`:
  1. `deny_if_unattended("ha_direct_call")`;
  2. lock the `HomeDirectSettings` row; re-run `classify` (the payload is
     never trusted); if not direct → return `fallback(reason)`;
  3. count the rate windows; over → `fallback(rate_limited)` + audit;
  4. clamp `volume_set`; create `HomeDirectCall(status=running)`;
  5. `policy.precondition` (`target_present`, the allowlist and live
     state) then `connectors.bridges.call_tool(manifest, server_tool=…,
     arguments=…, schema_sha256=policy.schema_sha256, timeout=
     settings.CONNECTOR_WRITE_TIMEOUT_SECONDS)` — the same entry point and
     pin check as `execute_claimed_write`; the D9 `volume_up` re-check on
     the pre-send read refuses unsent;
  6. finalize the row once (`succeeded` / `failed` / `unknown_outcome`
     with the `ConnectorWriteExecution.ErrorType` vocabulary) and write
     `ha.direct_call`; never retried.
  A `fallback` before the send turns the turn into the `TOOL_REQUEST`
  draft card with the same arguments (reply "Bunu doğrudan yapmadım
  (<reason text>); onay kartı ekranda."). A failure **after** a send
  attempt is reported, never replaced by a card ("Salon TV yanıt vermedi;
  komut gitmiş olabilir, tekrar denemedim.").
- Reply texts are fixed Turkish templates (no model): "Salon TV
  duraklatıldı.", "Salon TV'nin sesi %40." etc. `static_reply_stream`; no
  gateway call, no `ModelUsage`. The `Message` row records `intent=
  home_direct` and the `HomeDirectCall` id in `intent_payload`.
- The existing recovery task (`connectors.tasks.
  recover_stale_connector_writes`, next to `mark_lost_executions`) also
  moves a `running` `HomeDirectCall` older than
  `CONNECTOR_WRITE_LEASE_SECONDS` to `unknown_outcome` (never re-sent).

*Justification:* executing after the cap keeps ADR-015's server-side
check as the single place that admits a voice effect; re-classifying in
the executor makes the kill switch and the flag authoritative at send
time; running synchronously in the turn is what makes "TV'yi duraklat"
feel instant, and the recovery scan covers a lost worker.

### D10a — `home_direct` is a code-only classification with server-side provenance

Adding `Message.Intent.HOME_DIRECT` to the choices would, on today's code,
make it a value the model router may return:
`assistant/intent.py` builds `_ALLOWED_INTENTS = frozenset(
Message.Intent.values)`, and `parse_intent` accepts any member. A
router reply `{"intent": "home_direct", "tool_name": "ha_call_service"}`
(a model error or an injected instruction in the conversation) would then
pass the D10 cap clause on a voice turn and reach the view's direct
branch. Three independent locks close that, each tested on its own:

1. **The router cannot emit it.** `assistant/intent.py` gains
   `CODE_ONLY_INTENTS = frozenset({Message.Intent.HOME_DIRECT})` and
   `_ALLOWED_INTENTS = frozenset(Message.Intent.values) -
   CODE_ONLY_INTENTS`; `parse_intent` therefore raises `IntentParseError`
   ("unrecognized intent value") for `home_direct`, and the caller
   degrades the turn to chat exactly like any parse failure (the
   module's existing rule). The seeded `friday_router_intent` prompt
   (`ai_core/management/commands/seed_ai_defaults.py`) does not list the
   value; a test asserts the prompt text never names it. The chat view
   also re-checks: a classification that reaches the direct branch must
   carry the in-process marker of lock 2, else it is chat.
2. **Only `home.control` may return it.** `wake_routes._finish` today
   compares the capped classification with the original and the route's
   declared `effect` with `ALLOWED_EFFECTS`, but never checks the
   *result's* intent against the route. It gains: the effect of
   `result.intent` (`INTENT_EFFECTS`) must be in `{route.effect} ∪
   route.extra_effects`, else the turn degrades to chat with no reply. As
   `validate_routes` allows `extra_effects` only on `home.control`
   (D10), no other route — phrase, `tool.*`, skill — can return
   `home_direct`. The typed step (`route_typed_home_direct`) is the one
   other constructor; both build the classification through a single
   `assistant.services.home_direct_classification(resolution, origin,
   node_id)` factory that attaches a module-private sentinel object
   (`_CODE_ORIGIN`) under a key that JSON cannot carry
   (`classification["_provenance"] is _CODE_ORIGIN`, identity check, never
   serialised, stripped before the `Message` row is saved). Router output
   is a parsed JSON dict and can never hold that object.
3. **The executor never trusts the payload's arguments.**
   `run_home_direct(*, workspace, actor, message, origin, channel,
   node_id)` takes the saved user `Message` (not arguments) and
   `connectors.home_direct.execute` re-runs `resolve_home_command(
   workspace, actor, message.content)` — the same text `_home_control`
   and the typed step resolved (`WakeTurn.content`, the whole utterance)
   — and requires outcome `draft`, the same `tool_name`, and arguments
   equal (canonical JSON) to the classification's. A mismatch, or a
   resolution that is now `ask`/`refuse`/`None`, sends nothing, writes
   no `HomeDirectCall`, audits `ha.direct_provenance_refused`
   (`{source_message_id, origin, reason}` — never the arguments' free
   text), and the turn answers with the matcher's own current result (its
   draft card, ask or refusal), never a card built from the refused
   arguments. `provenance_mismatch` joins the closed reason set as an
   executor-only reason (it is not one of `classify`'s D7 conditions).
   Only then does `execute` re-run `classify` on the re-resolved
   arguments (D10 step 2).

*Justification:* each lock alone would close the hole; together they mean
a future change to one of them (a new intent in the parser, a new route,
a new caller of the executor) cannot silently reopen it. Re-resolving is
cheap (the matcher is a pure function over the allowlist, no model) and
makes "the owner's words decided the arguments" a property of the send,
not of whoever built the classification.

### D11 — The audit trail and what the owner sees

- `HomeDirectCall(WorkspaceScopedModel, TimestampedModel)`: `entity` FK
  (SET_NULL), `entity_id`, `service`, `data`, `requested_data`, `origin`
  (`voice`/`node`/`chat`), `node` UUID (null unless `node`),
  `requested_by` FK, `source_message_id`, `status`, `error_type`,
  `state_before`, `state_after`, `read_back`, `started_at`,
  `finished_at`. No update except the single finalize; no delete path
  except workspace deletion.
- Audit actions: `ha.direct_call` (ids, origin, node id, entity id,
  service, data, requested data when clamped, status, error type — never
  HA free text), `ha.direct_rate_limited`, `ha.direct_provenance_refused`
  (D10a), `ha.entity_direct_changed`, `ha.direct_settings_changed`.
- `GET /api/v1/connectors/home-assistant/direct/calls?limit=` (owner web,
  `for_actor()`, newest first, ≤ 100).
- HUD: `health_section` gains a `home_direct` block `{state, flagged,
  calls_last_hour}` (DB only, no HA call); `HudPanels` renders one line.
  The HUD never toggles anything.
- `/connectors` → Home Assistant: per media-player row a "Doğrudan
  (onaysız)" switch (disabled with the reason for other domains or no
  eligible service), naming the services it covers; a new
  `HomeDirectSettings` panel: "Doğrudan medyayı kapat" (kill switch),
  the volume maximum (0–100 %), the env-kill notice, the closed list, the
  limits, and the last 20 direct calls.

*Justification:* the owner gave up a screen act per call; the audit row,
the list and the HUD line are how he still sees every call.

### D12 — Housekeeping (this branch)

- `docs/runbooks/mac-session-handoff.md`: the lines that tell the Mac
  session which branch to check out or merge say `integ/phase-13` (and
  `main` after the owner's merge); the spike branch is
  `feat/mac-a1-spike`. MASTER_PLAN §AA and the Phase 5/6 briefs keep
  `claude/inspiring-franklin-pw7avo` as history with a "(former
  repository)" note; ADR-013's spike note names `feat/mac-a1-spike`.
- Phase 13 follow-ups: (1) D11 states that Wake-on-LAN takes no entity and
  stays on `HA_WOL_ALLOWED_MACS`; (2) the `last_active_at` Risks row is
  present and pinned (confirmed); (3) Phase 13 stamps are 2026-11-26 and
  the "swarm date stamp" remarks are gone. Phase 13 Q1 records the owner's
  override (ADR-018); Q5 and Q6 are marked resolved; D12 notes the narrow
  exception.
- **Not changed:** the proposed Phase 13 defaults (15 min last-active, 5
  aliases, 16 HUD nodes, backup warn 26 h / fail 72 h, drill warn 35 d /
  fail 90 d, `source_list` 30) and the Linux node's typed-directory-name
  confirmation stay **pending owner steps** (below).

## ADR

- **ADR-018 (new, Accepted 2026-11-26, owner decision): "Direct-Execute
  Home Media Class"** (`docs/adr/ADR-018-direct-home-media-class.md`),
  written in this branch. Branch 1b adds its implementation note (the
  digest, file names), 1c extends it with the D10a locks; the closeout
  adds its closeout note.
- **ADR-017 amended 2026-11-26** in this branch (interim Mac server, R2,
  TPM residual, ADR-018 cross reference).
- **ADR-014 and ADR-015:** cross-reference notes to ADR-018 in this
  branch; their decisions are not rewritten.
- **CLAUDE.md rule 4:** one sentence naming the single exception and
  ADR-018, in this branch.
- `backend/assistant/self_sources.json` is regenerated in this branch
  (`build_self_sources --check`), because CLAUDE.md, the ADRs and
  MASTER_PLAN changed.

## Scope (one branch each)

### Branch 1 series (1a–1d) — the direct media class, in four reviewable units

The direct class is the safety-critical part of the phase (the single
CLAUDE.md rule 4 exception), so it ships as four sequenced branches, each
merged (full suite green, independent Sonnet 5 review APPROVE) before the
next is cut from the updated integration branch. **Nothing is direct until
1c merges**: 1a only adds two gate-class services, 1b only stores and
admits (no executor is registered, so the port answers "never direct"),
and 1d is presentation. "Branch 1" elsewhere in this brief means the
series as a whole.

### 1a. `feat/p13-media-play-pause-stop` — two gate-class services, manifest v3, phrases

**Scope.** D4 (the bridge half), D7 (the matcher verbs):
- bridge: `media_play_pause`, `media_stop` in `SERVICES["media_player"]`
  (`NoData`); the `media_play_pause` decision-dialog row showing the live
  state and the outcome it implies; both are **gate-class** like every
  service (no direct code exists yet);
- manifest `version` 2 → 3, new `CALL_SERVICE_PIN` (and `STATES_PIN` if
  its listing changes) in the same commit; `docs/mcp/home-assistant.md`
  and `docs/mcp/allowlist.md` (MCP governance);
- `connectors.home_phrases`: `STOP` (`durdur`/`stop` → `media_stop`;
  `durdur` moves off `media_pause`), `PLAY_PAUSE` (`oynat duraklat`,
  `play pause`, `toggle play` → `media_play_pause`); an entity without
  the service → the existing "izin yok" refusal; vectors in
  `docs/voice/home-route-vectors.json`.

**Files.** `backend/connectors/bridges/home_assistant.py`,
`backend/connectors/manifests/home_assistant.py`,
`backend/connectors/home_phrases.py`,
`backend/connectors/tests/test_ha_media.py` (+ `test_home_phrases.py`,
the manifest pin tests, the HA dialog tests updated),
`docs/voice/home-route-vectors.json`, `docs/mcp/home-assistant.md`,
`docs/mcp/allowlist.md`; frontend only if the dialog row needs a label
(the approvals dialog component + its test).

**Tests.** The two services validate with `NoData` and refuse any data;
a voice "durdur" on an allowlisted TV → a `media_stop` **card**, approve →
one POST to the fake HA; "oynat duraklat" → `media_play_pause` card whose
dialog shows "şu an oynatılıyor → duraklatılır"; an entity whose
`services` lacks `media_stop` → refusal, no card; the pins match
`tool_schemas()`; the Phase 13 home tests green except the `durdur`
vector.

**Done when:** the two services exist as ordinary gate-class HA services
under manifest v3, and the matcher maps "durdur" and "oynat duraklat" to
them.

### 1b. `feat/p13-direct-media-model` — flag, settings, call log, closed set, classifier, rate limits, owner API

**Scope.** D4 (closed set + digest), D5, D6, D7 (`classify`), D8, D9
(the classification half), D11 (models, owner endpoints):
- models + migration `connectors` 0006: `HomeEntity.direct` (with the
  `save()` refusal), `HomeDirectSettings`, `HomeDirectCall` (model only;
  no writer yet but the tests' factory);
- `connectors/home_direct.py`: `DIRECT_MEDIA_DOMAIN`,
  `DIRECT_MEDIA_SERVICES`, `DIRECT_MEDIA_DIGEST`, the closed reason set,
  `classify` (D7 order), the rate windows (`count_direct_call`, D8), the
  volume rules on a given state (D9), the settings service
  (`get_settings`/`update_settings`, owner web, audited), the calls
  selector;
- `connectors.home_entities`: `direct` on create/update, the separate
  `ha.entity_direct_changed` audit, the clear-on-last-eligible-service
  rule;
- `tools/home_ports.py`: `register_direct_classifier`,
  `classify_home_direct` (unregistered or raising → never direct);
  `register_direct_executor`/`run_home_direct` are declared with the
  same rule but **nothing registers an executor in 1b**;
  `connectors/apps.py` registers the classifier only;
- serializers/views/urls: `direct` on the entity endpoints;
  `GET/PATCH /api/v1/connectors/home-assistant/direct`,
  `GET /api/v1/connectors/home-assistant/direct/calls`;
- settings + `.env.example`: the four `HA_DIRECT_*` variables with
  comments (0 disables; kill switch default false);
- ADR-018 implementation note (digest, file names).

**Files.**
- `backend/connectors/{models,home_direct,home_entities,serializers,views,urls,apps}.py`,
  `backend/connectors/migrations/0006_home_direct.py`,
  `backend/connectors/tests/{test_home_direct_classifier,test_home_direct_rate_limit,test_home_direct_api,test_home_direct_flag}.py`
  (+ `test_ha_allowlist_gate.py` extended);
- `backend/tools/home_ports.py`, `backend/tools/tests/test_home_ports.py`;
- `backend/config/settings/base.py`, `.env.example`;
- `docs/adr/ADR-018-direct-home-media-class.md` (implementation note).

**Tests.**
- **Closed allowlist:** `DIRECT_MEDIA_SERVICES` equals the eight names
  and its digest equals the ADR-018 note's value; every name is in
  `SERVICES["media_player"]` (needs 1a); adding a name without updating
  the pin fails.
- **Classifier table:** each closed reason produced by exactly its
  condition, in the D7 order; unattended run → `unattended`; origins
  `chat`/`voice`/`node` each only with their channel; CLI, OpenAI facade,
  Telegram, hook, `system` channels → `origin_not_direct`; a non-media
  domain, a non-flagged entity, a flagged entity with `turn_on`,
  `turn_off` or `select_source`, a scene, Wake-on-LAN → not direct; kill
  switch on (env, owner, each alone) → `kill_switch_*`.
- **Rate limits** (frozen clock): 10 per entity then the 11th →
  `rate_limited` + `ha.direct_rate_limited`; 30 across entities then the
  31st; the window slides (61 s later direct again); the cache raising →
  `rate_store_unavailable`; env limits honoured; 0 disables.
- **Volume (classification):** `volume_up` at 0.45 / max 0.6 → direct;
  at 0.55 → `volume_at_max`; no `volume_level` → `volume_unknown`.
- **Model:** `HomeEntity(direct=True)` on `light.*`, or on a media player
  without a direct-eligible service → `ValidationError`; unticking the
  last eligible service clears `direct` with an audit row.
- **An agent attempting to set the flag:** a PAT of every kind (`cli`,
  `hook`, `node`, `node_voice`, `openai`), a harness/agent principal, a
  Telegram relay, a firm-workspace member, another owner → 401/403/404
  and the row unchanged; a model draft carrying `"direct": true` in
  `ha_call_service` arguments → serializer refusal; the registry walk
  finds no writer of `direct` or `HomeDirectSettings` but the owner-web
  services and no writer of `HomeDirectCall` at all (1c adds exactly one).
- **Role × endpoint:** `GET/POST/PATCH` entities (`direct` field), `GET/
  PATCH /direct`, `GET /direct/calls` × {owner web session, owner PATs of
  every kind, agent principal, firm member, other owner, anonymous}.
- **Isolation:** two owners, each with a flagged TV: neither classifies,
  lists or toggles the other's; the firm workspace has no direct
  settings.
- **Port:** no executor registered → `run_home_direct` reports "not
  direct" and sends nothing (so 1b alone changes no turn).

**Done when:** the owner can flag an entity and set the kill switch and
volume maximum through the API with full role × endpoint coverage, and
`classify` answers every D7 case — while no turn behaves differently yet.

### 1c. `feat/p13-direct-media-exec` — executor, assistant intent, cap, wake route, typed path, provenance

**Scope.** D9 (executor re-check), D10, D10a, the recovery scan:
- `connectors.home_direct.execute` (D10 steps 1–6 with the D10a
  re-resolution first), `HomeDirectCall` lifecycle, `ha.direct_call` /
  `ha.direct_provenance_refused`; `connectors/apps.py` registers the
  executor; `connectors/tasks.py` recovery of stale `running` rows;
- `assistant`: `Message.Intent.HOME_DIRECT` + migration `assistant`
  0015 (choices only), `EFFECT_DIRECT_HOME`, the cap clause,
  `CODE_ONLY_INTENTS` in `intent.py` (D10a lock 1),
  `WakeRoute.extra_effects` + `validate_routes` + the `_finish` effect
  check (lock 2), the `home_direct_classification` factory and sentinel,
  `_home_control`, `route_typed_home_direct`, the view branch, the reply
  templates.

**Files.**
- `backend/connectors/{home_direct,tasks,apps}.py`,
  `backend/connectors/tests/test_home_direct_execution.py`;
- `backend/assistant/{models,intent,services,views,wake_routes}.py`,
  `backend/assistant/migrations/0015_home_direct_intent.py`,
  `backend/assistant/tests/{test_home_direct_routes,test_home_direct_node_voice,test_home_direct_typed,test_voice_cap_home_direct,test_home_direct_provenance}.py`
  (+ the intent-parser tests and the router-prompt seed test);
- `docs/adr/ADR-018-direct-home-media-class.md` (implementation note:
  the three locks).

**Tests.** (fake HA via `httpx.MockTransport`; the Phase 11 reference
voice host and the real `friday-voice run` child for `node_voice`.)
- **Execution:** one `POST /api/services/media_player/media_pause`, no
  `ApprovalRequest`, no `ConnectorWriteExecution`, one
  `HomeDirectCall(succeeded)` and one `ha.direct_call` with origin, node
  id (for `node`) and entity; state read back; timeout after the POST →
  `unknown_outcome`, never re-sent; HA 4xx → `failed`; binding disabled
  or pin mismatch → nothing sent, card; entity removed between turns →
  card; `running` row recovered to `unknown_outcome`.
- **Volume (execution):** `volume_set 0.9` with max 0.6 → sent 0.6,
  requested 0.9 stored, reply says so; owner max 0.3 → 0.3; the pre-send
  read disagreeing with the classification → nothing sent, card.
- **Still gate-class end to end (the owner's required proofs):** the
  parametrised table of the test strategy over web voice, `node_voice`
  and typed: a non-media domain, a non-flagged entity, a flagged entity
  with a non-direct service, a scene, Wake-on-LAN, kill switch on (env;
  owner) and flipped between classification and send, over the limit →
  a card (voice) or the router path (typed), no HA request, no
  `HomeDirectCall`.
- **Router cannot emit it (D10a lock 1):** `parse_intent` on
  `{"intent":"home_direct","tool_name":"ha_call_service"}` raises
  `IntentParseError`; a stubbed `router.intent` returning that payload
  on a **voice** turn whose text the matcher does not resolve and on a
  **typed** turn → the turn is chat, no HA request, no `HomeDirectCall`,
  no card; the seeded router prompt never names `home_direct`.
- **Only `home.control` (lock 2):** a test route (phrase, `tool.*`)
  whose handler returns `HOME_DIRECT` → chat, no reply; `validate_routes`
  refuses `extra_effects` on any route but `home.control` and any extra
  effect but `direct_home`; a `HOME_DIRECT` classification without the
  sentinel (a plain dict, e.g. round-tripped through JSON) reaching the
  view → chat.
- **Provenance (lock 3):** a hand-built `HOME_DIRECT` classification with
  the sentinel but arguments that differ from the resolver's output for
  the stored text (another flagged entity, `volume_set 1.0` instead of
  the spoken value, a different service) → refused
  (`provenance_mismatch`), no HA request, no `HomeDirectCall`,
  `ha.direct_provenance_refused` written, the reply is the matcher's own
  current result; stored text that now resolves to `ask` (a second TV
  flagged since) → no call, the ask.
- **Cap:** a `HOME_DIRECT` classification naming another tool on a voice
  turn → chat.
- **Voice/typed paths:** web voice "TV'yi duraklat" → no `draft` event, a
  fixed reply, `ModelUsage` count unchanged; `node_voice` through the
  reference voice host → origin `node`, the speaking node's id; typed web
  "TV'yi duraklat" → direct with **no** `router.intent` call; typed
  "salondaki TV'yi aç" or any non-direct text → the Phase 13 router path
  unchanged (its tests green); ambiguous ("TV'yi duraklat" with two
  flagged TVs) → the ask template, no call.

**Done when:** "TV'yi duraklat" by web voice, `node_voice` and typed chat
pauses a flagged TV against the fake HA with one call, no card and an
audit row naming the origin and the entity — every case in "Still
gate-class" demonstrably reaches the approval gate, and no model output
or hand-built payload can produce a direct call.

### 1d. `feat/p13-direct-media-ui` — HUD line and `/connectors` controls

**Scope.** D11 (presentation):
- HUD: `health_section` gains the `home_direct` block `{state, flagged,
  calls_last_hour}` (DB only, no HA call);
- frontend: `HomeEntitiesEditor` "Doğrudan (onaysız)" switch (only on
  eligible rows, disabled with the reason elsewhere), the
  `HomeDirectSettings` panel ("Doğrudan medyayı kapat", volume maximum
  0–100 %, env-kill notice, the closed list, the limits, the last 20
  calls), the `HudPanels` line, API client types.

**Files.** `backend/hud/services.py`,
`backend/hud/tests/test_home_direct_line.py`;
`frontend/src/components/connectors/{HomeEntitiesEditor,HomeDirectSettings}.tsx`
(+ tests), `frontend/src/components/hud/HudPanels.tsx` (+ test), the
connectors API client module.

**Tests.** HUD `home_direct` states `on` / `off_owner` / `off_env` /
`not_configured`, counts, no HA request; vitest: the switch only on
eligible rows and its PATCH, the kill switch and volume panel, the
env-kill notice disabling the toggle, the calls list, the HUD line; the
HUD offers no toggle.

**Done when:** the owner sees and controls the direct class on
`/connectors` and sees its state and call count on the HUD.

### 2. `feat/p13-interim-mac-server` — macOS-tolerant preflight, R2 destination check, portable scripts, launchd

**Scope.** D1, D2:
- settings + `.env.example`: `HOME_SERVER_PLATFORM`,
  `HOME_BACKUP_OFFSITE_REPOSITORY`; the host-only R2 names block
  (`RESTIC_REPOSITORY`, `RESTIC_PASSWORD_FILE`, `AWS_ACCESS_KEY_ID`,
  `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION`), names only, empty;
- `ops/preflight.py`: `home_server_platform` row (in `check_home`, after
  `home_disk_encryption`); `home_disk_encryption` platform-aware;
  `home_backup_fresh` repository check;
- `deploy/home/friday-backup.sh`, `friday-restore-drill.sh`: portable
  (bash ≥ 4.4 check with a clear message; `flock` when present, else a
  `mkdir` lock directory with a stale-PID check; `sha256sum` or
  `shasum -a 256`; size by `wc -c`; no `stat -c`); `repository` in
  `status.json` with the userinfo/query refusal;
- `deploy/home/restic.env.example` (names only);
  `deploy/home/macos/com.friday.backup.plist` (LaunchAgent, 03:10,
  `/opt/homebrew/bin/bash`, `FRIDAY_*` paths as placeholders, logs under
  `~/Library/Logs/friday-backup.log`); `deploy/docker/compose.mac.yml`;
  `compose.home.yml` passes `HOME_SERVER_PLATFORM` and
  `HOME_BACKUP_OFFSITE_REPOSITORY`; `Makefile` `up-mac-server`;
- `docs/runbooks/home-server-setup.md`: replace the "*(ships with
  `feat/p13-interim-mac-server`)*" markers with the shipped names; step 12
  table rows.

**Files.**
- `backend/config/settings/base.py`, `.env.example`;
- `backend/ops/preflight.py`,
  `backend/ops/tests/{test_preflight_home_macos,test_preflight_home_offsite}.py`
  (+ `test_preflight_home.py` updated for the new row);
- `deploy/home/{friday-backup.sh,friday-restore-drill.sh,restic.env.example}`,
  `deploy/home/macos/com.friday.backup.plist`,
  `backend/ops/tests/{test_home_backup_portable,test_launchd_plist,test_no_bucket_secrets_in_compose}.py`
  (+ `test_home_backup_restore.py` for the `repository` field);
- `deploy/docker/{compose.mac.yml,compose.home.yml}`, `Makefile`,
  `backend/config/tests/test_compose_files.py`;
- `docs/runbooks/home-server-setup.md`.

**Tests.**
- **macOS tolerance:** with `HOME_SERVER_PLATFORM=macos`, no LUKS, valid
  fresh status files and LAN hosts → `check_home` has **no** `fail` row;
  `home_disk_encryption` is `warn` with and without
  `HOME_FDE_CONFIRMED_ON` (text names FileVault, never LUKS);
  `home_server_platform` `warn`; with `linux` both rows behave exactly as
  before (existing tests unchanged); an unknown platform value → `fail`.
- **R2 destination:** `HOME_BACKUP_OFFSITE_REPOSITORY` unset → today's
  rules; set and equal → pass ("R2"); `repository` absent → warn;
  different → fail; a configured value or a recorded value with userinfo
  or a query → fail without echoing it; details never contain a key.
- **Portable scripts:** the backup round trip
  (`test_home_backup_restore.py`) also under a PATH shim that hides
  `flock`, `sha256sum` and GNU `stat` and provides a `shasum` double →
  same `status.json` (with `repository`), lock honoured (a second run
  while the lock directory exists is refused), stale lock recovered;
  `bash` < 4.4 (a shim reporting 3.2) → clear refusal; a
  `RESTIC_REPOSITORY` with `user:pass@` → the run fails, no status file.
  Shellcheck stays clean (CI).
- **launchd plist:** `plistlib` parses it; `Label`, `ProgramArguments`
  (`/opt/homebrew/bin/bash` + the script), `StartCalendarInterval {Hour:
  3, Minute: 10}`, no `KeepAlive`, `EnvironmentVariables` holds only
  `FRIDAY_*` paths (no `AWS_*`, `RESTIC_PASSWORD*`).
- **Compose:** `compose.mac.yml` publishes nothing; the
  `up-mac-server` file combination parses (`docker compose config`-style
  test in `test_compose_files.py`) with `HOME_SERVER_PLATFORM=macos` on
  web; no compose file passes `RESTIC_*` / `AWS_*` to a service;
  `.env.example` lists the five R2 names with empty values.

**Done when:** a macOS-declared server reports warnings, not failures, for
what macOS cannot provide; `home_backup_fresh` proves the last backup went
to the configured R2 repository; and the scripts run unchanged under the
macOS tool set double.

### 3. `chore/p13b-closeout` — preflight row, e2e, handoff Part F, MASTER_PLAN

**Scope.**
- preflight `check_home_direct` (registered after `check_home_control`):
  `ha_direct_media` — **pass**: kill switch off (env and owner), ≥ 1
  flagged entity, each flagged entity enabled, `media_player`, with ≥ 1
  direct-eligible service, and `DIRECT_MEDIA_SERVICES`' digest equal to
  ADR-018's; **warn**: kill switch on (env or owner), none flagged, HA not
  configured; **fail**: a flagged entity outside `media_player` or
  without an eligible service (should be impossible), or the digest
  differs. Detail: state, count, limits, volume maximum. Read-only, no HA
  call, no model;
- e2e `backend/ops/tests/test_home_direct_e2e.py` (live test server,
  fake HA through the bridge's `MockTransport` seam): web voice and a
  `node_voice` turn through the reference voice host + a real
  `friday-voice run` child → "TV'yi duraklat" on a flagged TV → exactly
  one POST, no `ApprovalRequest`, `HomeDirectCall` + `ha.direct_call`
  with origin `node` and the node id; unflagged → card → approve → one
  call (Phase 13 unchanged); kill switch toggled on `/connectors` → the
  next utterance is a card; the 11th call in a minute → card; "sesi yüzde
  90" → 0.6; typed direct; `node_protocol_frozen` still passes; a
  macOS-declared preflight run with R2-recorded status files → no `fail`
  in `check_home`, `ha_direct_media` pass;
- docs: this brief's branch table and go/no-go rows; MASTER_PLAN §AA row
  9b status; `docs/runbooks/mac-session-handoff.md` **Part F** (the Mac
  session items below: running the stack on the Mac, the loopback node,
  the real TV); ADR-018 closeout note; `self_sources.json` regenerated;
  `docs/runbooks/home-server-setup.md` step 12 rows for
  `home_server_platform` and `ha_direct_media`.

**Files.** `backend/ops/preflight.py`,
`backend/ops/tests/{test_preflight_home_direct,test_home_direct_e2e}.py`,
`docs/prompts/phase-13b-home-decisions/brief.md`, `docs/MASTER_PLAN.md`,
`docs/runbooks/{mac-session-handoff,home-server-setup}.md`,
`docs/adr/ADR-018-direct-home-media-class.md`,
`backend/assistant/self_sources.json`.

**Done when:** the e2e is green, preflight shows `ha_direct_media` and
`home_server_platform`, the handoff has Part F, and the MASTER_PLAN row
says "server side done; go/no-go pending owner evaluation".

## Branch table

| # | Branch | Scope | Depends on | Status |
|---|---|---|---|---|
| brief | `docs/p13b-brief` | This brief (no stub existed; the owner decisions of 2026-11-26 kept as goal, scope and boundary), ADR-018 (Accepted 2026-11-26), ADR-017 amendment, ADR-014/ADR-015 cross references, CLAUDE.md rule 4 sentence, runbook "Interim: server on the MacBook" + R2 + TPM, housekeeping (handoff branch names, `feat/mac-a1-spike`, "(former repository)" notes, Phase 13 follow-ups 1–3, Q1/Q5/Q6 notes), MASTER_PLAN §AA row 9b + §O d12, `self_sources.json` regenerated | Phase 13 closeout | **implemented 2026-11-26** (docs only; awaits the independent Sonnet 5 plan review to APPROVE) |
| 1a | `feat/p13-media-play-pause-stop` | `media_play_pause`/`media_stop` in the code maximum as gate-class services (manifest v3, pins, governance docs), the play_pause dialog row, `STOP`/`PLAY_PAUSE` verbs and vectors | brief approved | not started |
| 1b | `feat/p13-direct-media-model` | `HomeEntity.direct`, `HomeDirectSettings`, `HomeDirectCall` (`connectors` 0006), closed allowlist + digest, `classify`, rate windows, volume rules, settings service, entity `direct` field + `/direct` + `/direct/calls` endpoints with role × endpoint, registry walk, the classifier port, `HA_DIRECT_*` settings, ADR-018 implementation note | 1a | not started |
| 1c | `feat/p13-direct-media-exec` | `execute` (re-resolution, kill-switch re-check, clamp, one send, audit), recovery scan, executor port, `HOME_DIRECT` intent (`assistant` 0015) + code-only parser lock, cap clause, `extra_effects` + `_finish` check, sentinel factory, voice/`node_voice`/typed paths, provenance tests | 1b | not started |
| 1d | `feat/p13-direct-media-ui` | HUD `home_direct` line, `/connectors` direct switch and settings panel, vitest | 1b (merges after 1c, so nothing is shown as "on" before it can run) | not started |
| 2 | `feat/p13-interim-mac-server` | `HOME_SERVER_PLATFORM`, `home_server_platform`, macOS-aware `home_disk_encryption`, `HOME_BACKUP_OFFSITE_REPOSITORY` + the R2 destination check, R2 names in `.env.example`, portable backup/drill scripts, `restic.env.example`, launchd plist, `compose.mac.yml`, `make up-mac-server` | brief approved (independent of 1) | not started |
| 3 | `chore/p13b-closeout` | `ha_direct_media` preflight row, home-direct e2e, handoff Part F, MASTER_PLAN row, ADR-018 closeout note, `self_sources.json`, runbook step 12 | 1a–1d, 2 | not started |
| M1 | Interim server on the real MacBook | Runbook "Interim" I1–I5: OrbStack/Docker Desktop, `make up-mac-server`, FileVault attestation, the R2 bucket, the LaunchAgent, the first backup and restore drill, one drill from R2 | 2 merged | deferred: Mac session (pending owner evaluation) |
| M2 | Mac node on the same machine + the real TV | Loopback pairing (I3) of the existing Swift app; flag the TV; direct pause/volume by voice; kill switch; rate limit; unflagged → card | 1a–1d, 2 merged; M1; Phase 9 B1 | deferred: Mac session (pending owner evaluation) |
| M3 | Home PC with TPM unlock; move off the Mac | Runbook step 1 (TPM, recovery key, dracut) and "Interim" I6 (restore + re-point) on the real home PC | M1; the owner buys the PC | deferred: owner hardware (pending owner evaluation) |

**Sequencing and migrations.** 1a → 1b → 1c → 1d, each cut from the
integration branch after its predecessor merged (1d may be developed in
parallel with 1c once 1b is merged, but merges after 1c). 1a adds no
migration; 1b adds `connectors` 0006; 1c adds `assistant` 0015; 1d and 2
add none. Migration numbers are placeholders: each branch runs
`makemigrations` on the latest integration branch and, before merge,
rebases and renumbers onto the newest leaf of every app it touches
(`makemigrations --check --dry-run` green, no merge migrations). Branch 2
is independent of the 1-series and may be developed concurrently; 1b and
2 both touch `.env.example` and `config/settings/base.py` (separate
blocks), so the second to merge resolves that trivially. None touches
`node/friday-node/**`.

## Mac session (deferred)

Phase 13b changes **no protocol surface and no Swift code**. The Mac
session uses the existing `friday-node` app and the server branches above.
The closeout appends this list to `docs/runbooks/mac-session-handoff.md`
as **Part F**. Check out `integ/phase-13` (or `main` after the owner's
merge).

**Preconditions:** branches 1a–1d and 2 merged; Phase 9 B1 (the Swift app
pairs and holds a session) for M2; Phase 11 B7 (the voice host) for the
voice half of M2.

### M1 — The interim server on the MacBook

**Do** (`docs/runbooks/home-server-setup.md` "Interim: server on the
MacBook" I1, I2, I4 optional, I5):
1. FileVault on (`fdesetup status`); OrbStack or Docker Desktop; Homebrew
   `bash age restic caddy`.
2. `deploy/docker/.env`: `HOME_SERVER_PLATFORM=macos`,
   `HOME_BACKUP_HOST_DIR`, `HOME_FDE_CONFIRMED_ON`, `DJANGO_ALLOWED_HOSTS`
   with the Mac's `.local` name; `make up-mac-server`.
3. R2: bucket + bucket-scoped token; `~/.config/friday-backup/restic.env`
   from `deploy/home/restic.env.example`; `restic init`;
   `HOME_BACKUP_OFFSITE_REPOSITORY` in `deploy/docker/.env`.
4. The LaunchAgent (`deploy/home/macos/com.friday.backup.plist`); one
   manual run; close the lid overnight once and confirm the catch-up run
   on wake (`~/Library/Logs/friday-backup.log`, `status.json`).
5. The first restore drill, and one from the R2 copy.
6. `make preflight`: no `fail` row caused by macOS; `home_server_platform`
   and `home_disk_encryption` warn; `home_backup_fresh` pass with "R2";
   `home_restore_drill_age` pass.

**Contract (already enforced here):** branch 2
`ops/tests/test_preflight_home_macos.py`, `test_preflight_home_offsite.py`,
`test_home_backup_portable.py`, `test_launchd_plist.py`; the closeout e2e's
macOS preflight run.

### M2 — The Mac's own node and the real TV (go/no-go)

**Do:**
1. Pair the MacBook's `friday-node` with the server on the same machine
   over the loopback TLS path (runbook I3: `make local-tls`, strong code,
   typed pin, `https://127.0.0.1:8443`); `node_link` connected,
   `node_tls_pin` pass.
2. On `/connectors`, flag the real TV "Doğrudan (onaysız)"; volume maximum
   as the owner likes.
3. By voice on the Mac (real mic, Phase 11 voice host): "TV'yi duraklat",
   "devam ettir", "durdur", "sesi kıs", "sesi yüzde 90'a ayarla" → each
   one call, no card; record the time from end of speech to the TV
   reacting, and that 90 % became the maximum.
4. "Salondaki TV'yi aç" → a card (power is gate-class); an unflagged
   speaker → a card.
5. Toggle "Doğrudan medyayı kapat" → the next utterance is a card; toggle
   back. Say eleven volume commands within a minute → the eleventh is a
   card.
6. Review `/connectors` "Son doğrudan çağrılar" and the audit log: every
   row has origin `node` and the right entity.

**Contract (already enforced here):** branch 1c
`assistant/tests/test_home_direct_node_voice.py` and
`test_home_direct_provenance.py`, branch 1b
`connectors/tests/test_home_direct_rate_limit.py`, the closeout
`ops/tests/test_home_direct_e2e.py`.

### M3 — The home PC with TPM unlock (owner hardware, not the Mac)

**Do:** buy the PC; runbook steps 0–6 with step 1's TPM enrolment
(recovery key first, PCR 7, `dracut`), a reboot that unlocks without a
prompt, and one that asks after a Secure Boot change; then "Interim" I6:
final backup on the Mac, restore on the PC, the same `NODE_SIGNING_KEY`
and CA, re-point (or re-pair) the Mac node, unload the LaunchAgent,
`HOME_SERVER_PLATFORM=linux`; `make preflight` with every `home_*` row
pass.

**Record** M1–M3 results in this brief's "Go/no-go record" on a docs
branch, reviewed like every brief change.

## Go/no-go record (Phase 13b checkpoint)

Checkpoint: "For two weeks the owner uses direct media daily with zero
direct calls outside ADR-018's class or on unflagged entities (audit
review), the kill switch is proven, and the interim Mac server backs up to
R2 with a passing drill from the R2 copy."

| Item | How it is measured | Result |
|---|---|---|
| Closed class; non-media, non-flagged, agent flag attempts and kill switch reach the gate | branch 1b + 1c tests | pending (branches 1b, 1c) |
| The router cannot emit `home_direct`; forged payloads refused | branch 1c tests (D10a) | pending (branch 1c) |
| Direct calls audited with origin and entity; no `ApprovalRequest` | branch 1c + closeout e2e | pending (branch 1c, closeout) |
| Rate limits and volume clamp fail closed to the card | branch 1b + 1c tests | pending (branches 1b, 1c) |
| No macOS-caused preflight failure; R2 destination checked | branch 2 tests + closeout e2e | pending (branch 2, closeout) |
| No protocol change | `node_protocol_frozen` | pending (closeout) |
| Interim server on the real Mac, backups to R2, drill from R2 | M1 | pending owner evaluation |
| Real-TV direct media, kill switch, rate limit; latency recorded | M2 | pending owner evaluation (Mac session) |
| Two weeks of use, zero out-of-class direct calls | M2 + audit review | pending owner evaluation |
| Home PC with TPM unlock; move off the Mac | M3 | pending owner evaluation (owner hardware) |

Pending rows do not block the backlog. A direct call found outside the
class, or on an unflagged entity, stops the phase: the owner sets the kill
switch and the classifier is re-planned before anything else is added.

## Out of scope (explicit)

- Any direct service beyond ADR-018's eight; any direct non-media domain;
  power, input, scenes, lights, climate, covers, locks, alarms, WoL
  direct; a domain-wide or "all entities" direct flag; a direct class for
  node actions or any other connector.
- Direct calls from router-extracted arguments, tapped cards, schedules,
  proactive runs, agents, the harness, CLI, the OpenAI facade, Telegram,
  hooks; batch or scene-like "pause everything".
- Speaker verification; per-person permissions.
- Any WS message, schema field, capability contract or `friday-voice/v1`
  change; any Swift change.
- Running two Friday servers (Mac and PC at once); syncing; automatic
  server failover.
- Friday reading R2, holding R2 credentials or starting a backup; a live
  preflight call to the bucket.
- Automating FileVault, TPM enrolment or launchd from Friday; any
  attestation a container cannot make.
- Public exposure of the interim Mac (port forwarding, tunnels,
  Tailscale Funnel).

## Test strategy

**Discipline.** The same as Phases 2–13 (CLAUDE.md rules 5, 7, 8): ruff,
pytest, vitest, import-linter and `makemigrations --check` green on every
branch; the full backend suite once before each merge; role × endpoint
tests for every new endpoint; no real LLM, MCP, GitHub, Google, HA, R2,
VPS, DNS, bucket or hardware in tests.

- **Doubles.** The fake HA (`httpx.MockTransport` for unit tests, the
  bridge seam for the e2e); the Phase 11 reference voice host and the real
  `friday-voice run` child; the reference node for `node_voice` origin;
  a frozen clock for the rate windows; a PATH shim (no `flock`, no
  `sha256sum`, no GNU `stat`, a `shasum` double, a fake old `bash`) for
  the macOS script run; a local restic repository and throwaway age keys
  (the existing round trip); `plistlib` for the LaunchAgent.
- **Code-only provenance (D10a).** Each lock has its own test: the parser
  refuses `home_direct`; a non-`home.control` route returning it is chat;
  a sentinel-less classification is chat; a payload whose arguments
  differ from the re-resolved stored text is refused with no HA request.
  These run on voice, `node_voice` and typed turns with a stubbed router.
- **"Still gate-class" as a parametrised table.** One test module walks
  every negative condition of D7 for each origin and asserts: no HA
  request, a `draft` event (voice) or the router path (typed), and no
  `HomeDirectCall`. The table is the brief's D7 list; a new condition
  without a row fails a completeness check against the closed reason set.
- **Invariants as walks.** The Phase 13 registry walk is extended: nothing
  but the owner-web services writes `direct` or `HomeDirectSettings`, and
  nothing but `connectors.home_direct.execute` writes `HomeDirectCall`.
- **No model calls added.** Every direct path asserts the `ModelUsage`
  count is unchanged by the route and, for typed direct turns, that
  `router.intent` was not called.
- **Isolation.** Two owners with flagged TVs, plus a firm workspace with a
  member: nothing of one is classifiable, callable, listable or togglable
  by the other.
- **Phase 13 regression.** The Phase 13 home tests (`test_ha_*`,
  `test_home_routes*`, `test_home_hub_e2e.py`, `test_home_voice_e2e.py`,
  `test_preflight_home*.py`) stay green unchanged, except the `durdur`
  vector (now `media_stop`) and the manifest pins (v3).

## Risks

| Risk | Mitigation |
|---|---|
| A TV, a video or a guest triggers direct calls (no speaker verification) | Only eight media services on owner-flagged entities; volume clamp; 10/min per entity, 30/min global, fail closed to the card; every call audited and listed; kill switch in the UI and env; owner-accepted residual in ADR-018 |
| An agent or model output widens the class | Closed set in code with a pinned digest (test + preflight); the flag and settings writable only by owner-web services (registry walk, role × endpoint); strict serializers refuse unknown fields; model-extracted arguments never reach the classifier (enforced by D10a) |
| The model router (or text injected into the conversation) returns `home_direct` | D10a: the parser refuses the code-only intent (→ chat); only `home.control` may return its effect; the classification needs an in-process sentinel JSON cannot carry; the executor re-resolves the stored message text and refuses any argument mismatch (branch 1c tests on voice and typed turns) |
| A large safety-critical change is hard to review | Split into 1a–1d, nothing direct until 1c, each with its own independent review |
| The direct path silently drifts into other domains | `classify` checks the domain and the closed set first; the parametrised "still gate-class" table; `ha_direct_media` fails on a flagged non-media entity |
| `volume_up` overshoots the maximum on a device with a large step | Headroom rule (0.1) on the live read; the overshoot is at most one device step; recorded as residual in ADR-018; owners with such a device can untick `volume_up` |
| Kill switch toggled while a call is in flight | Re-checked under a row lock right before the send; there is no queue; a call already sent completes and is audited |
| Rate-limit store down | `rate_store_unavailable` → card (never direct) |
| A lost worker leaves a direct call `running` | Recovery scan → `unknown_outcome`, never re-sent, shown in the calls list |
| "durdur" changes meaning (pause → stop) | Vectors updated; documented in `docs/mcp/home-assistant.md` and the editor; if an entity lacks `media_stop`, the route refuses ("izin yok") instead of guessing |
| The interim Mac sleeps and Friday is down | Accepted by the owner (ADR-017 amendment 1); `home_server_platform` warns; nodes fail closed; backups catch up on wake; stale backups still warn/fail |
| FileVault off on the interim Mac | Owner attestation only (a container cannot see it); `home_disk_encryption` stays a warning on macOS so it is never mistaken for verified; runbook I1 |
| Two servers after the move (Mac and PC both running) | Runbook I6 stops and wipes the Mac stack only after a passing drill on the PC; never run both |
| R2 credentials leak into the stack | Names only in `.env.example` under a host-only heading; a test refuses `RESTIC_*`/`AWS_*` in any compose service; the status file refuses userinfo |
| Backups quietly go to the wrong repository | `status.json.repository` vs `HOME_BACKUP_OFFSITE_REPOSITORY` → fail |
| TPM unlock lets a stolen box boot | Owner-accepted residual (ADR-017 amendment 3); recovery key off the server; age private key never on the server; revoke nodes and rotate `NODE_SIGNING_KEY` after a theft |
| Ubuntu initramfs ignores the TPM token | Runbook uses `dracut` and asks to verify with a reboot; M3 records it |
| The Mac node loses its loopback pin on the move to the PC | Runbook I6: re-point or re-pair; Phase 13 M1 re-point keeps other pins |

## Open questions (with recommended answers)

- **Q1 — Rate-limit overflow: card or refusal?** Recommended: the card
  (the owner's request stays possible; the storm is visible as cards).
- **Q2 — `volume_up` headroom 0.1?** Recommended: yes, a code constant;
  revisit after M2 with the real TV's step.
- **Q3 — Should a direct call speak a confirmation on `node_voice`?**
  Recommended: yes, the short fixed template (the owner hears that it was
  understood); no TTS of HA text.
- **Q4 — Auto-detect the server platform?** Recommended: no; a container
  cannot tell Docker Desktop's VM from a Linux host reliably — the owner
  declares `HOME_SERVER_PLATFORM`.
- **Q5 — Typed chat as a direct origin?** The owner's decision names
  voice, chat and node; recommended: yes, through the matcher only (D7),
  so a typed "TV'yi duraklat" is as fast as the spoken one.

## Owner steps (pending) — recorded 2026-11-26

These cannot happen in a cloud session (they need the owner's hardware,
accounts or judgement):

1. Before branch 1a: have the independent Sonnet 5 plan review of this
   brief (with ADR-018, the ADR-017 amendment, the CLAUDE.md sentence and
   the runbook changes) reach APPROVE.
2. Confirm ADR-018 as written (Accepted 2026-11-26) and the one-sentence
   CLAUDE.md rule 4 exception; confirm the ADR-017 amendment, including
   the TPM residual.
3. Confirm or change the Phase 13b defaults: 30/min global, 10/min per
   entity, volume maximum 0.6, `volume_up` headroom 0.1, "durdur" →
   `media_stop`, rate-limit overflow → card (Q1).
4. **Still pending from Phase 13 (not confirmed by the owner):** the
   proposed defaults — last-active window 15 min, ≤ 5 aliases per
   node/room/entity, ≤ 16 nodes in the HUD panel, backup warn 26 h / fail
   72 h, restore drill warn 35 d / fail 90 d, `source_list` ≤ 30 entries.
5. **Still pending from Phase 13:** the Linux node's local-policy
   confirmation (TTY + typed directory basename) as the accepted stand-in
   for Touch ID (Phase 13 Q4).
6. **Still pending from Phase 13:** the node-pairing route through the
   home Caddy (Phase 13 closeout owner step 2) — it now also decides how
   LAN PCs pair with the interim Mac server (runbook I4).
7. After branches 1c and 1d: flag the TV(s) "Doğrudan" on `/connectors` and set the
   volume maximum (nothing is direct until then).
8. Create the R2 bucket and a bucket-scoped token; fill the host restic
   environment file; set `HOME_BACKUP_OFFSITE_REPOSITORY` (after branch
   2).
9. Mac session M1, M2; owner hardware M3 (buy the home PC; verify the TPM
   commands on the real Ubuntu install).
10. Merge `integ/phase-13` to `main` when the owner is ready (handoff D4).
11. After two weeks: record the go/no-go rows.

### Owner steps (pending) — `docs/p13b-brief`, recorded 2026-11-26

1. Start the independent Sonnet 5 plan review of this brief and of the
   documents written in the same branch (ADR-018, ADR-017 amendment,
   ADR-014/ADR-015 notes, CLAUDE.md rule 4, `home-server-setup.md`
   "Interim", step 1 TPM, step 8 R2, the handoff and MASTER_PLAN edits);
   branch 1a does not start before APPROVE. Review round 1 (2026-11-26):
   the HIGH router-provenance finding is addressed by D10a, the
   branch-size finding by the 1a–1d split, the kill-switch label is
   unified ("Doğrudan medyayı kapat") in ADR-018.
2. Note that the runbook's macOS and TPM commands were written in a Linux
   container and never run on a Mac or the home PC: M1 and M3 exercise
   them; fix any step that differs in a docs branch.
3. Owner steps 2–6 above are judgement calls for the owner; nothing in
   this branch depends on them being answered first.

Nothing in this branch needs the Mac, real credentials, paid calls or real
hardware: it is documentation only.
