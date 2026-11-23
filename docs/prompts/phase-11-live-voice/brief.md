# Phase 11 — Local live voice (server side + Mac session)

> Status: **Draft (2026-11-20). Written on `docs/p11-brief` from the stub of
> the same date, which was taken from the owner-approved Friday 3.0
> roadmap. It needs its independent Sonnet 5 plan review before branch 1,
> because the implementing model never reviews its own brief.**
> Sources: `docs/research/2026-11-roadmap-proposal.md` §a (scenario 1–2),
> §B1 (node shape), §B2 (voice pipeline, ADR-015), §c.1 (latency budget),
> §c.2 (runaway guards), §c.3 (wake-word budget), §c.4 (offline and
> degraded behaviour), §c.5 (observability), §c.6 (testing), §c.8
> (Pipecat, openWakeWord, Piper, smart-turn rows), §g d1 and d3;
> MASTER_PLAN §AA "Friday 3.0 phases" (order 7).
> Parent decisions:
> - ADR-001: workspace axis, `for_actor()`.
> - ADR-002: modular monolith and its import contracts (`voice` and `hud`
>   are leaves; `nodes` reaches only `approvals`, `audit`, `workspaces`,
>   `accounts`, `core`).
> - ADR-003 and its 2026-11-20 amendment: single gateway door, one
>   `ModelUsage` per call, gateway-enforced audio budgets.
> - ADR-004 and ADR-008 decision 3: nothing executes without the gate;
>   nothing in this phase executes at all.
> - **ADR-011** (voice transport): voice is a transport around the
>   unchanged chat turn; no audio persisted; STT/TTS only on
>   `personal_data_allowed` providers. **Amended by this phase** (D3–D4:
>   a second, node-bound voice channel next to the web session).
> - **ADR-013** (node protocol): decision 1 (the Python voice pipeline is
>   a child process of `friday-node`), decision 6 (fail closed: on WS loss
>   the always-on mic is released), decision 8 (the node makes no model
>   call and holds no provider key).
> - ADR-014: unchanged; a voice turn still only proposes node actions.
> - **ADR-015** in full: decisions 1–3 (landed in Phase 6), decisions 4–6
>   completed here before always-on ships, decision 7 (accepted risk: no
>   speaker verification).
> - Roadmap decision **d3**: always-on only after the false-activation
>   budget is met (D11 makes it mechanical).
>
> **Stub decisions kept.** The stub's goal, its scope list (Pipecat child
> process with an exact pin and generated adapter tests; openWakeWord with
> the custom `hey_friday` model → Silero VAD → smart-turn v3 → MLX Whisper
> with a whisper.cpp fallback → an LLM step that calls Friday's streaming
> chat endpoint, never a provider → language-routed TTS; macOS
> voice-processing AEC and barge-in; always-on wake word with the menu-bar
> indicator, fail-closed mute and the global hotkey; ADR-015 decisions
> 4–6; Piper as a separate executable never imported, with a test; the
> openWakeWord model licence recorded), its go/no-go checkpoint ("FAR ≤ 0.2
> per hour on 24 h of household audio; self-trigger test green; latency
> targets local p50 ≤ 1.5 s, p95 ≤ 2.5 s met") and its owner step (record
> 24 h of household audio outside the repo) are kept unchanged. One stub
> item is made concrete rather than changed: "MLX Whisper (whisper.cpp
> fallback)" runs as the engine of the Mac's local OpenAI-compatible
> speech server behind the gateway, not inside the child (D2 explains
> why; the child still never calls a provider).
>
> **Platform boundary (owner decision 2026-11-20).** The implementation
> container is Linux, with no macOS and no Xcode. Two kinds of work follow
> from that:
> - **In scope now, with full branches (1–7 and the closeout):** everything
>   that can be built and tested here: the node voice credential and
>   channel (Django), the live-voice endpoints, the parent↔child protocol
>   `friday-voice/v1` (schemas, framing, vectors) with a **Python reference
>   parent double** that plays the Swift host's side, the Pipecat pipeline
>   itself (`node/friday-voice/`, pure Python, runs on Linux) with its
>   detectors behind ports, the barge-in and arming state machines, the
>   self-trigger rule, the recorded-audio replay harness with synthetic
>   fixture WAVs and an echo double, the FAR/FRR harness, the always-on
>   gate, the HUD and web surfaces, and the preflight checks.
> - **Not built here:** everything that must be built, signed or run on
>   macOS: the Swift voice host inside `friday-node` (spawning the child,
>   AVAudioEngine voice-processing AEC on the real microphone, playback
>   through the same engine, the menu-bar mic indicator, the global
>   hotkey, the Microphone TCC grant, passing the voice credential from
>   the Keychain-held node secret), the MLX Whisper speech server on the
>   Mac, notarization of the updated app, the nightly self-trigger run
>   with real models and real speakers, and every real-world measurement
>   (FAR on 24 h of household audio, FRR, latency, barge-in stop time,
>   CPU). These are listed under "Mac session (deferred)" and marked
>   "deferred: Mac session" in the branch table. The Swift host must pass
>   the `friday-voice/v1` conformance suite and the server-side contract
>   tests that branches 1–2 commit now.
>
> Go/no-go items that need the Mac or real-world owner data are recorded
> as **pending owner evaluation**. They do not block Phase 12.
>
> **Precondition.**
> - Phase 10 closeout merged on the integration branch (it is:
>   `chore/p10-closeout`, 2026-11-20). Phase 11 uses the protocol v2 event
>   path (D15), the `node_action` voice regressions (Phase 10 D13) and the
>   HUD health panel's node line (Phase 9 D11).
> - Phase 6 branch 3a (`feat/p6-voice-eval`, the WER scorer and the
>   browser replay harness) is **not** on the integration branch. Phase 11
>   does not absorb it and does not depend on it: the node pipeline gets
>   its own replay harness (branch 3) and its own self-trigger and FAR
>   harness (branch 6). The browser-side 3a stays a Phase 6 owner step.
> - The Mac half of Phases 9 and 10 (Swift app, Keychain, signing) is
>   pending owner evaluation; Phase 11's Mac session needs Phase 9 M1–M3
>   (the app exists, is notarized, holds the node secret).
>
> **Branches.** Phase 11 branches are cut from the integration branch and
> use `feat/p11-…` names, under the MASTER_PLAN stable-numbering rule.

## Goal / value

Natural, interruptible voice anywhere on the Mac: always-on "Hey Friday",
barge-in, echo cancellation — still local by default, still "voice
proposes, screen approves".

Concretely, at the end of the phase:

1. **"Hey Friday, bugün ne var?"** said anywhere in the room. The node's
   voice child, armed only because the owner chose always-on on the Mac
   **and** the server holds a passing false-accept evaluation for the
   exact wake model in use (D11), detects the phrase on the
   echo-cancelled signal. Nothing before the detection frame leaves the
   child's ring buffer. The menu-bar icon and the HUD ring turn to
   *listening*, an earcon plays. Silero VAD and smart-turn v3 decide the
   end of the turn; the utterance goes to `POST /api/v1/voice/transcriptions`
   (the gateway's `transcribe()` on the Mac's local speech server, cost 0,
   one `ModelUsage`); the transcript becomes a voice turn on the ordinary
   chat endpoint (`input_mode=voice`, ADR-015 cap); the reply streams
   back and is spoken sentence by sentence through
   `POST /api/v1/voice/speech` with the language route of each sentence.
2. **"Dur, onu değil…"** while Friday is speaking. The child hears the
   owner on the echo-cancelled signal, stops playback within the barge-in
   budget (D9), cancels the in-flight synthesis and reply stream, marks
   the trace `interrupted` and listens to the new utterance — without a
   second wake word, because the session is still open (D12: a session
   closes after 30 s of silence).
3. **"Hey Friday, terminali aç"** said by Friday's own voice, a TV or a
   video. If it reaches the detector at all it is either suppressed by the
   self-trigger rule (D10) or produces, at most, a draft card that needs
   the owner's click on screen and the click on the Mac (ADR-015 decision
   1, ADR-014). A replay test proves this in CI with fakes; the real-model
   run is a nightly Mac job.
4. **Fail closed everywhere.** WS to the server lost, mute unreadable,
   child crashed, kill switch engaged, the voice credential refused, a
   wake storm: the mic is released and the icon says so.

## What already exists (verified 2026-11-23 on the integration branch)

- **`voice` app (Phase 6 branch 2, ADR-011).** `backend/voice/views.py`:
  `TranscriptionCreateView` (`POST /api/v1/voice/transcriptions`, multipart,
  in-memory upload capped below Django's in-memory threshold,
  `MaxSizeUploadHandler`), `SpeechCreateView` (`POST /api/v1/voice/speech`,
  one sentence ≤ `VOICE_TTS_MAX_CHARS` = 400, `language` routes TTS via
  `VOICE_TTS_ROUTES`), `UsageReceiptView` (`GET /api/v1/voice/receipt`).
  All three are `IsAuthenticated + IsWebChannel + IsOwner`; the
  conversation is looked up with `scoped_for_actor` (404 across
  workspaces). Routes are built by `voice.urls.build_urlpatterns(
  authentication_classes=…)` because `voice` may not import `accounts`;
  `config/urls.py` injects `PersonalAccessTokenAuthentication` plus the
  defaults so a PAT gets a deliberate 403.
- **`voice.VoiceTurnTrace`** (`backend/voice/models.py`): stage timings
  only (`stt_ms`, `tts_first_ms` server-side; `end_of_turn_ms`,
  `llm_first_sentence_ms`, `playback_start_ms`, `total_ms` client-reported),
  keyed by `X-Friday-Trace`, no free-text column (an allow-list test pins
  the shape), pruned after `VOICE_TRACE_RETENTION_DAYS`.
- **Gateway audio (ADR-011, ADR-003 amendment).**
  `InferenceGateway.transcribe()` / `.synthesize()`
  (`backend/ai_core/gateway.py:363,417`) keep only
  `personal_data_allowed` providers (`_run_audio`, :495), enforce the
  audio budgets (`AI_AUDIO_PURPOSES = ("voice.stt", "voice.tts")`), and
  write one `ModelUsage` per call. The only real speech path is the
  `openai_compatible` adapter against a local OpenAI-compatible speech
  server (`docs/runbooks/voice-setup.md`: speaches with faster-whisper and
  Piper on the Mac). `ai_core/tests/test_sdk_guard.py` forbids importing
  `faster_whisper`, `ctranslate2`, `piper`, `onnxruntime` or `whisper`
  anywhere in `backend/`.
- **Chat turn and the voice cap.** `assistant/views.py`
  `ConversationMessageListView.post` (:255): `input_mode` is forced to
  `text` for any channel other than `CHANNEL_WEB` (:269–272); the turn's
  `X-Friday-Trace` joins every `ModelUsage` row; `apply_voice_capability_cap`
  (`assistant/services.py:735`) degrades any intent whose effect is not
  `read` or `proposal` to plain chat; drafts are emitted as `draft` SSE
  events. The node-action argument step (`assistant/services.py:440`,
  `router.node_action_args`) runs on `CHANNEL_WEB` only.
- **Channels and read rules.** `core/channels.py`: `CHANNEL_WEB` (the only
  high-trust channel), `CHANNEL_CLI`, `CHANNEL_OPENAI`, `CHANNEL_HOOK`,
  `CHANNEL_NODE`, `CHANNEL_TELEGRAM`, `CHANNEL_SYSTEM`; `scoped_for_actor`
  forces a `ChannelPrincipal`'s bound workspace. `tools/registry.py:221`
  `READ_CHANNELS`: firm reads on `{web, cli}`, personal reads on `{web}`
  only.
- **Credentials.** `accounts.PersonalAccessToken` (`kind` ∈ `cli`, `openai`,
  `hook`; `max_length=10`; workspace-bound; hard `expires_at`; soft
  revoke); `PersonalAccessTokenAuthentication` and its per-kind
  subclasses map kinds to channels (`KIND_CHANNELS`) and answer a wrong
  kind with 403 `token_kind_forbidden`. `NodeCredentialAuthentication`
  (`jnd_` secret of a `kind=node` `TrustedDevice`, kill switch released)
  reaches exactly one DRF view today (credential rotation) plus the node
  WebSocket; `accounts.devices.node_device_refusal` is the per-heartbeat
  re-check.
- **Node protocol (Phases 9–10).** `nodes.protocol.messages`:
  `NODE_TO_SERVER_V2 = {hello, heartbeat, result, event, confirmation,
  output}`, `SERVER_TO_NODE_V2 = SERVER_TO_NODE = {hello.ack,
  heartbeat.ack, invoke, event.ack, bye}` (frozen), `EVENT_KINDS =
  ("claude_code.hook", "grant.availability_changed")`, the
  `POLICY_SHAPED` guard. `nodes.events` routes event kinds (a sink for
  hook events, availability handled in `nodes`). The Python reference node
  (`nodes/reference/`) and the conformance suite (`nodes/conformance/`,
  `build_node_conformance --check`) are the executable spec the Swift app
  must pass. Phase 9 D-list item 9: the menu-bar icon reserves the mic
  state and always shows "off" until Phase 11 (no Microphone entitlement
  yet).
- **Browser voice (Phase 6 branches 3–5).** `frontend/src/lib/voice/`:
  `self-trigger.ts` (the 1.5 s window and the transcript-vs-recent-TTS
  match: Turkish lower-casing, NFC, punctuation stripped, wake phrase
  stripped, containment or ≥ 60 % token overlap), `wake-mode.ts` (arming
  states, idle disarm, the wake-storm rule `VOICE_WAKE_STORM_MAX` in
  `VOICE_WAKE_STORM_WINDOW_MS`), `segmenter.ts` (sentence chunking with
  the Turkish abbreviation list and Markdown-to-speech), `vad.ts`,
  `wake-engine.ts`, `model-manifest.json` (openWakeWord melspectrogram and
  embedding graphs and Silero VAD v5.1.2 pinned by sha256; the
  `hey_friday.onnx` keyword head with an empty hash until the owner
  trains it). `make voice-models` downloads them into a gitignored folder.
- **Drafts on screen.** `frontend/src/components/chat/drafts.ts`
  reconstructs task/tool drafts from persisted user messages **only for
  `channel === "web"`**; the card click is the only path to a task or an
  `ApprovalRequest`.
- **HUD.** `hud/services.py` sections `approvals`, `tasks`, `cost`,
  `health` (with `node_section`, Phase 9 D11), `terminal` (Phase 10 D12);
  `hud` is a read-only leaf that may import `nodes`, never `voice`.
- **Preflight.** `ops/preflight.py`: `check_voice_config` (speech rows,
  routes, audio budget, a pinned STT/TTS round trip on an in-memory WAV)
  and `check_voice_mute` (the Playwright muted-tab spec), plus node and
  control checks.
- **Not present.** No `node/` directory (the Swift app is created by the
  Phase 9 Mac session), no Pipecat, no Python audio pipeline outside the
  browser, no nightly CI workflow (`.github/workflows/ci.yml` only), no
  voice credential for anything but a web session.

## Boundary vs existing systems

- **Voice stays a transport (ADR-011 decision 1).** The live pipeline
  turns audio into a transcript before an ordinary chat turn and reply
  text into audio after it. No new intent, no new router step, no new
  path to `approvals`, `tasks` or `nodes` invocations. Every draft from a
  live turn is the same card, clicked on screen.
- **The gateway stays the only door (ADR-003).** The child calls three
  Friday endpoints (transcriptions, chat messages, speech) and nothing
  else that reaches a model. Detector models (openWakeWord, Silero VAD,
  smart-turn v3) run inside the child exactly as Phase 6 runs the first
  two inside the browser: they classify frames and emit no text or tokens
  (D2).
- **The node stays model-free and keyless (ADR-013 decision 8).** The
  child holds a short-lived voice credential minted by the node secret
  (D3), never a provider key and never the node secret itself.
- **The node protocol's server→node set stays frozen.** Live voice traffic
  does not ride the node WebSocket: the child speaks HTTPS to the voice
  endpoints (outbound only, no listener). The node WebSocket gains one
  **node→server** event kind, `voice.state` (D15), for the HUD.
- **Node policy stays local (ADR-013 decision 5).** Mic mode (off,
  push-to-talk, always-on), hotkey and mute are set in the node's own UI.
  The server can only **narrow** what the child may do (refuse always-on
  without a passing evaluation, storm mute), never widen it.
- **Workspace axis.** The node voice credential is bound to the owner's
  personal workspace, exactly like the node secret (Phase 9). Live
  conversations live there; firm data reaches them only through the
  existing personal overlay rules of the chat turn.
- **Raw audio is never persisted (ADR-011 decision 3).** The child keeps
  at most a 2 s pre-wake ring buffer in memory and the current utterance;
  the replay harness reads fixture WAVs and the owner's evaluation audio
  from paths outside the repo; nothing writes captured audio to disk.

## Decisions (with justification)

### D1 — Three places, one direction of trust

- **Swift host (`friday-node`, Mac session).** Owns the microphone (TCC),
  AVAudioEngine with voice processing enabled (macOS AEC + noise
  suppression on the input, the TTS playback as its echo reference),
  playback, the menu-bar mic indicator, the global push-to-talk hotkey,
  the mute switch, the node secret in the Keychain and the WebSocket. It
  spawns the child and talks to it over **stdio pipes only**.
- **Python child (`node/friday-voice/`, this phase).** A Pipecat pipeline
  (D7–D8) that receives AEC-cleaned 16 kHz mono PCM16 frames and host
  events from the host, runs the detectors and the turn logic, calls the
  three voice endpoints over HTTPS with the voice credential, and sends
  synthesized audio back to the host for playback. It opens **no
  listening socket** (a static test and a runtime test assert it), reads
  no file outside its own install and the model directory, and holds no
  secret but the voice credential, in memory.
- **Server (Django).** STT and TTS through the gateway, the chat turn
  unchanged, the voice credential, the always-on gate, the storm
  backstop, traces and the HUD.

Justification: AEC needs the playback signal as its reference, so capture
and playback must live in one process that the OS's voice-processing unit
sees; that is the Swift host, which also holds the TCC grant (d1/d9: a
signed app keeps stable grants, a Python interpreter does not). The
detectors and the turn logic are Python because Pipecat, openWakeWord,
Silero and smart-turn are Python, and because Python is testable here.
Putting HTTP in the child rather than the host keeps the host's Swift
surface small; the cost is one more process holding a credential, bounded
by D3.

### D2 — STT and TTS stay behind the gateway; detectors are not model calls

- The child never runs Whisper or a TTS engine. "MLX Whisper (whisper.cpp
  fallback)" from the stub becomes the engine of the Mac's local
  OpenAI-compatible speech server, a provider row of the gateway like the
  Phase 6 speaches row (`voice-stt-local`), chosen and measured in the Mac
  session (M2). Phase 6's speaches + faster-whisper stays the working
  fallback; whisper.cpp is the second fallback behind the same
  OpenAI-compatible shape (a `deploy/voice/` sidecar if needed, ADR-011
  consequences).
- **Justification.** (1) ADR-003: every speech call stays a gateway call
  with its `ModelUsage` row (cost 0, real latency), personal-data rule and
  audio budget, with no second door and no `record_external_usage`
  exception for audio. (2) ADR-013 decision 8: the node makes no model
  call; an STT engine inside the child would be one. (3) One code path:
  browser voice and node voice share the same transcription and speech
  endpoints, the same TTS routes and the same preflight round trip. Cost:
  one local HTTP hop (child → server → speech server); on the Mac dev
  stack this is loopback plus Docker Desktop's host bridge, a few tens of
  milliseconds inside the 350 ms STT budget. M4 measures it; if the hop
  alone breaks the budget the brief is amended, not the rule.
- **Detectors.** openWakeWord, Silero VAD and smart-turn v3 are ONNX
  classifiers over audio frames that output a score. They produce no
  text, no tokens and no cost, exactly like the browser's openWakeWord and
  Silero under ADR-011 decision 2. They are therefore not "LLM calls"
  under CLAUDE.md rules 2–3, and they run in the child. An ADR-013
  implementation note (branch 2) records this reading of decision 8 so it
  cannot drift: "a detector is an on-device classifier that emits only
  scores; anything that emits text or tokens is a model call and goes
  through the gateway".
- **Guards.** `node/friday-voice/tests/test_import_guard.py` forbids, in the
  child's own package: `piper`, `whisper`, `faster_whisper`, `mlx_whisper`,
  `mlx`, `ctranslate2`, `pywhispercpp`, `openai`, `anthropic`,
  `google.generativeai`, `litellm`, `kokoro`, and the Pipecat STT, TTS and
  LLM service modules (`pipecat.services.*`) — the child's only
  STT/LLM/TTS processors are its own HTTP clients of Friday (D8). The
  backend SDK guard (`ai_core/tests/test_sdk_guard.py`) is extended to
  scan the whole repository for `import piper` / `from piper` (the stub's
  Piper test).

### D3 — The node voice credential

- **Shape.** A new `PersonalAccessToken.Kind.NODE_VOICE = "node_voice"`
  (fits `max_length=10`), always bound to the owner's personal workspace,
  with a new nullable `node_device` FK to the `kind=node` `TrustedDevice`
  that minted it (`PROTECT`; null for every other kind, a check
  constraint enforces "set iff kind is `node_voice`"). Hard expiry
  `VOICE_LIVE_TOKEN_TTL_SECONDS` (default 900, refused at boot above
  3 600). At most one unrevoked `node_voice` token per node device:
  minting revokes the previous one in the same transaction.
- **Minting.** `POST /api/v1/auth/nodes/voice-token`, node secret only
  (`NodeCredentialAuthentication`, the rotation endpoint's pattern),
  throttled (`node_voice_mint`, default 30/hour). It refuses while the
  owner's kill switch is engaged or the device is revoked (the
  authenticator already does), returns the raw token once with its
  `expires_at`, and audits `node_voice_token.minted` (device id, token id;
  never the token). The host mints a token when it starts the child and
  ≤ 60 s before expiry, and hands it to the child **over the stdin
  control pipe** (`credential` message, D6) — never in argv, the
  environment, a file or the Keychain.
- **Authentication.** `NodeVoiceTokenAuthentication` (in `accounts`, a
  `PersonalAccessTokenAuthentication` subclass with `accepted_kind =
  NODE_VOICE`, surface `node_voice`) additionally calls
  `accounts.devices.node_device_refusal(token.node_device)` on **every
  request**, so revoking the node, engaging the kill switch or
  `revoke_all_devices` kills the voice credential on the next request.
  `revoke_all_devices` and the device revoke path also soft-revoke the
  device's `node_voice` tokens. The principal is
  `ChannelPrincipal(channel=CHANNEL_NODE_VOICE, bound_workspace_id=<personal>,
  token_id=<token id>)`.
- **Justification.** The node secret is long-lived and kept in the Keychain
  so that only the signed host can use it (ADR-013 decision 2); handing it
  to a Python child would widen its exposure to an interpreter, its
  dependencies and its crash dumps. A 15-minute, voice-only,
  device-bound, re-checked token limits a leaked child credential to "can
  talk to Friday as voice for ≤ 15 min, can never decide anything". A PAT
  kind rather than a new token model reuses the hashing, audit, expiry,
  kind-to-surface and `scoped_for_actor` machinery that 8A's `hook` kind
  already proved.

### D4 — The `node_voice` channel and what it reaches

- `core/channels.py` gains `CHANNEL_NODE_VOICE = "node_voice"` (low-trust:
  `is_low_trust` stays true; it is never `CHANNEL_WEB`). `Message.Channel`
  gains the value (`max_length=20` holds it).
- **Reaches, exactly:**
  - `POST /api/v1/conversations/{id}/messages` (chat turn): for this
    channel `input_mode` is **forced to `voice`** whatever the body says
    (the mirror of today's "forced to `text` off the web"). The ADR-015
    cap therefore always applies. GET on the same route is allowed (the
    child reads nothing back today, but a 403 on GET would break the
    shared view; the response is scoped to the bound workspace).
  - `POST /api/v1/voice/transcriptions`, `POST /api/v1/voice/speech`,
    `GET /api/v1/voice/receipt`: the permission becomes
    `IsWebOrNodeVoiceChannel + IsOwner` (a new `core.permissions` class;
    every other low-trust channel still gets the deliberate 403).
  - the new `GET /api/v1/voice/live/policy` and
    `POST /api/v1/voice/live/sessions` (D5, D11) — node voice only.
- **Refused** (403 `token_kind_forbidden` for the token on any other PAT
  surface, 401 on JWT-only surfaces): approvals, tasks, memory, tools,
  agents, harness, connectors, deploys, nodes endpoints, the facade, the
  hook ingest, the HUD stream, the node actions endpoint, credential
  rotation. A route-walk test (`config/tests/test_node_voice_surfaces.py`)
  enumerates every URL pattern and asserts the token reaches only the
  list above.
- **Personal reads.** `READ_CHANNELS[DATA_CLASS_PERSONAL]` becomes `{web,
  node_voice}`: "Bugün ne var?" must read the calendar. Justification: the
  credential descends from the same owner-paired device trust as a web
  session, it is re-checked per request, and the read is spoken on the
  owner's own Mac exactly as a web voice turn's is in Phase 6. The
  residual risk (someone in the room asks Friday to read the calendar
  aloud) is ADR-015 decision 7's accepted risk, unchanged in kind. Firm
  reads follow `{web, cli, node_voice}` for the same reason. A
  `ReadToolSpec` may still narrow itself (`channels=`) below this.
- **Node-action proposals.** The `router.node_action_args` step
  (`assistant/services.py:440`) accepts `CHANNEL_NODE_VOICE` as well as
  `CHANNEL_WEB`, so "testleri çalıştır" said to the Mac yields the same
  `node_action` draft. It is a proposal (Phase 10 D13): the trust check
  caps it at `warn` (`voice_evidence`), and execution still needs the
  card click, `/approvals` and the Mac window.
- **Drafts on screen.** `drafts.ts` reconstructs drafts for
  `channel ∈ {"web", "node_voice"}`; a live conversation opened at
  `/chat/<id>` shows its cards, and the `/voice` page links the last live
  conversations (D15). The child never clicks anything: it says "Kart ekranda"
  when a `draft` event arrives (the Phase 6 D8 sentence), and never
  "yapıldı" without a receipt.
- **Never:** a decision, a confirm, a task creation, an approval, a memory
  approval. ADR-011 decision 5 and ADR-015 decision 1 are unchanged; the
  channel adds a transport, not a capability.

### D5 — Live sessions and their conversation

- `POST /api/v1/voice/live/sessions` (node voice only) returns
  `{conversation_id, session_id, opened_at}`. It reuses the most recent
  conversation the node voice channel started in the bound workspace if
  its last message is younger than `VOICE_LIVE_CONVERSATION_REUSE_MINUTES`
  (default 30), else creates one (`create_conversation(..., channel=
  "node_voice")`, title "Canlı ses — <date>"). `session_id` is a UUID the
  child puts on every trace of the session; no session table exists (the
  session is a client concept bounded by D12's 30 s silence rule).
- Justification: one conversation per stretch of talk keeps context for
  follow-ups ("onu da ekle") without a new model; the reuse window is a
  setting because the right value is a matter of taste.

### D6 — Parent↔child protocol `friday-voice/v1`

- **Transport.** The host spawns the child with stdin/stdout pipes (stderr
  is the child's scrubbed log, D16). No sockets, no shared memory.
- **Framing.** Each frame is a 4-byte big-endian length (≤ 1 MiB) followed
  by a UTF-8 JSON header line and, for audio types, a binary payload
  whose length is given in the header (`pcm_bytes`, ≤ 64 KiB, 16 kHz mono
  PCM16 little-endian, even length). A malformed frame, an unknown type,
  an over-length frame or a header failing its schema is a protocol error:
  the receiver logs a code, drops the link and (host side) releases the
  mic. JSON schemas with `additionalProperties: false` and every string,
  array and integer bounded live in
  `node/friday-voice/friday_voice/protocol/schemas/v1/`; canonical vectors
  in `…/vectors/frames.json`.
- **Host → child (frozen set):** `hello` (protocol version, host version,
  sample rate, `mode` ∈ {off, push_to_talk, always_on}, mute state,
  `link_up`), `credential` (token, `expires_at`, server base URL, pinned
  CA fingerprint), `audio` (AEC-cleaned capture frame, `seq`,
  `captured_at_ms`, `aec` ∈ {on, off}), `playback` (`utterance_id`,
  `state` ∈ {started, ended, stopped}, `at_ms`), `mode` (new local mode),
  `mute` (`muted` bool or `unreadable`), `ptt` (`down`/`up`), `link`
  (`up`/`down`: the node WS state), `shutdown`.
- **Child → host (frozen set):** `ready`, `state` (`off`, `armed_ptt`,
  `armed_wake`, `listening`, `thinking`, `speaking`, `muted`,
  `storm_muted`, `error`; plus daily counters, D15), `play` (`utterance_id`,
  `seq`, `last`, PCM payload), `stop_playback` (`utterance_id`), `earcon`
  (`wake` / `end` / `error`, from a fixed list the host owns), `release_mic`
  (reason code), `log` (code + scrubbed short detail).
- **What the child cannot ask for.** No message changes the host's mode,
  hotkey, mute, device selection, credential or node policy; no message
  asks the host to open a URL, run anything or send anything on the WS
  other than the `voice.state` event it derives from `state`. A
  meta-test applies the `nodes.protocol` `POLICY_SHAPED` regex to every
  type and property name of `friday-voice/v1` (no reserved exception),
  and a frozen-set test pins both directions.
- **Fail closed.** The child treats `link: down`, `mute: unreadable`,
  `mute: true`, a host protocol error, stdin EOF or a missing credential
  as "stop consuming audio, drop buffers, send `release_mic`". The host
  treats child exit, a child protocol error, a missing `state` for 10 s
  (watchdog) or an unknown frame as "release the mic, show *off*, restart
  the child with backoff 1 s → 60 s".
- **Reference parent double.** `friday_voice/reference/host.py`: a Python
  implementation of the host's side (feeds WAV fixtures as `audio`
  frames at real or accelerated clock, simulates echo with a
  configurable residual (`EchoDouble`, D10), records `play` frames,
  scripts mute/ptt/link events, enforces the host's watchdog and
  fail-closed rules). It is the executable spec the Swift host mirrors.
- **Conformance suite.** `friday_voice/conformance/cases/*.json` in two
  families: *host cases* (the Swift host, driven by a scripted hostile
  child `friday-voice conformance-child`, must refuse oversize frames,
  unknown types, policy-shaped names, a `play` for an unknown utterance,
  PCM of odd length, and must release the mic on watchdog and on child
  exit) and *child cases* (the child, driven by a scripted hostile host,
  must release on every fail-closed input and never send anything before
  `credential`). `friday-voice conformance --check` regenerates nothing
  and fails on drift, the `build_node_conformance --check` pattern.
- Justification: stdio is the narrowest channel a parent can give a child
  (no port, no path, no peer to authenticate), and a length-prefixed,
  schema-checked frame stream is the same discipline as the node
  protocol, so the same review checklist applies.

### D7 — Pipecat: exact pin, generated surface snapshot, adapter tests

- `node/friday-voice/pyproject.toml` pins `pipecat-ai` to one exact
  version (chosen in branch 2's step 0: the latest release at least two
  weeks old, licence BSD-2-Clause confirmed) with `uv.lock` hashes;
  `onnxruntime` (CPU), `openwakeword`, `numpy`, `httpx` likewise. No
  Pipecat extras that pull provider SDKs.
- The child uses Pipecat only through `friday_voice/pipecat_adapter.py`
  (frame classes, `FrameProcessor`, `Pipeline`, `PipelineTask`, the VAD
  analyzer interface, the smart-turn analyzer). `scripts/snapshot_pipecat.py`
  writes `friday_voice/pipecat_surface.json` (the signatures, frame class
  names and fields we use, introspected from the installed version);
  `--check` fails when the installed Pipecat differs, and
  `test_pipecat_surface.py` exercises every adapter function against the
  pinned version.
- **Upgrades only in a dedicated branch** (`chore/p11-pipecat-<version>`):
  bump the pin, regenerate the snapshot, fix the adapter, run the replay
  and conformance suites. Roadmap §c.8: Pipecat's 0.0.x API churn.

### D8 — Pipeline stages and their ports

```
host audio ─▶ RingBuffer(2 s) ─▶ WakeGate ─▶ TurnCapture(VAD + smart-turn)
          ─▶ FridaySTT ─▶ SelfTriggerFilter ─▶ FridayChatLLM(SSE)
          ─▶ Segmenter ─▶ FridayTTS(lang route) ─▶ play frames ─▶ host
                    ▲ BargeInMonitor (VAD on AEC signal while speaking)
```

- **Ports with fakes** (`friday_voice/ports.py`): `WakeDetector`
  (`OpenWakeWordDetector` over the pinned ONNX graphs + `hey_friday.onnx`;
  `ScriptedWakeDetector`), `VoiceActivity` (`SileroVad`; `ScriptedVad`),
  `TurnEnd` (`SmartTurnV3` through Pipecat's local analyzer; `ScriptedTurnEnd`),
  `FridayClient` (`HttpFridayClient` over `httpx`; `FakeFriday` with
  scripted transcripts, SSE replies, draft events, 401/403/423/429
  responses and latencies), `Clock` (real; `ManualClock`).
- **Models are pinned and verified.** `node/friday-voice/models.json`
  lists the four ONNX files plus the smart-turn v3 model with sha256; a
  test asserts the shared entries (melspectrogram, embedding, Silero,
  `hey_friday`) equal `frontend/src/lib/voice/model-manifest.json` so the
  browser and the node run the same wake model. The child refuses to arm
  wake mode if any hash differs or is empty (the browser's rule).
  `make voice-models` gains a `--node` target writing into the node's
  model directory (outside the repo).
- **WakeGate.** Pre-wake audio exists only in the 2 s ring buffer. On a
  detection at or above the threshold (`VOICE_LIVE_WAKE_THRESHOLD`,
  default the Phase 6 browser value), the utterance starts at the first
  frame after the detection frame (no pre-roll, ADR-011 decision 2). No
  speech within 5 s of the wake → back to armed, nothing uploaded.
- **TurnCapture.** Silero VAD marks speech; after ≥ 200 ms of trailing
  silence smart-turn v3 scores the turn; complete → end of turn;
  incomplete → keep listening up to a 1.5 s hard silence cap; 30 s hard
  utterance cap (ADR-011 upload cap). Budget: end-of-turn ≤ 300 ms p50
  (§c.1).
- **FridaySTT** posts the utterance as WAV to
  `/api/v1/voice/transcriptions` with `X-Friday-Trace`. An empty
  transcript ends the turn silently.
- **FridayChatLLM** posts the transcript to
  `/api/v1/conversations/{id}/messages` (`input_mode: "voice"`) and
  consumes the SSE stream; `token` events feed the segmenter, a `draft`
  event queues "Kart ekranda" after the current sentence, `done`/`error`
  end the turn. It is the stub's "LLM step that calls Friday's streaming
  chat endpoint, never a provider".
- **Segmenter** is a Python port of `segmenter.ts` (sentence boundaries,
  20–300 chars, Turkish abbreviations, Markdown to speakable text). Shared
  vectors `node/friday-voice/vectors/segmenter.json` are run by pytest
  **and** by a new vitest case over `segmenter.ts`, so the two can never
  drift.
- **FridayTTS** posts each sentence to `/api/v1/voice/speech` with its
  `language` (`tr` / `en`) chosen per sentence by a small deterministic
  detector (`friday_voice/language.py`: Turkish letters `çğıöşü İ`,
  stop-word lists, the transcript's language as the tie-breaker); one
  request in flight plus one prefetched (Phase 6 D7). The server's
  `VOICE_TTS_ROUTES` picks the engine and voice. The returned WAV is
  normalised to 16 kHz mono PCM16 and sent as `play` frames.
- **Errors.** 401/403 → stop, `release_mic`, ask the host for a new
  credential once, then *error*; 423 (storm mute, D12) → *storm_muted*;
  429 (budget or throttle) → speak nothing, *error* with the code (the
  earcon only); network errors → one retry for STT/TTS, none for the chat
  turn (a turn is never sent twice).

### D9 — Barge-in

- While *speaking*, `BargeInMonitor` runs Silero VAD on the AEC-cleaned
  signal. Barge-in fires when speech probability ≥ `VOICE_LIVE_BARGE_IN_VAD`
  (default 0.7) persists ≥ `VOICE_LIVE_BARGE_IN_MIN_MS` (default 250 ms)
  and playback started ≥ 300 ms ago (the AEC convergence guard).
- On barge-in the child sends `stop_playback`, cancels the in-flight TTS
  requests and closes the chat SSE (the server finishes or aborts the
  reply as it does for a closed browser stream), marks the trace
  `interrupted_at_ms`, and starts a new utterance **with a 300 ms
  pre-roll** from the barge-in onset. The pre-roll is allowed because the
  session is already open after a wake (ADR-011 decision 2 governs
  pre-wake audio only); it never reaches back before the session.
- The new utterance goes through the self-trigger filter like any other
  (D10): an echo leak that the AEC let through and that matches what
  Friday was saying is discarded, the turn ends, the session continues.
- **Budget.** Barge-in onset → `stop_playback` sent ≤ 100 ms in the
  replay harness (CI, manual clock); host stop ≤ 100 ms after the frame
  (Mac M4). "Dur"/"Durdur"/"Stop" need no special handling: any speech
  stops playback.
- The state machine (`friday_voice/session.py`, pure, clock-injected) has
  the states of D6's `state` message and a transition table tested
  exhaustively (every state × every input → the expected state and the
  expected outbound frames).

### D10 — Self-trigger rule on the echo-cancelled signal (ADR-015 decision 4)

- In Phase 11 the wake detector is **fed the AEC-cleaned signal** (the
  ADR-015 option for full duplex) instead of being suppressed while TTS
  plays. Two filters stand behind it:
  1. **Window rule.** A wake detection while playback is active or within
     `VOICE_SELF_TRIGGER_MS` (1.5 s) after playback ended is marked
     *suspect*; its utterance is transcribed and **discarded** if the
     transcript matches recent TTS text (the browser's rule, D8 below).
     Discarded means: no chat turn, nothing spoken, counter
     `discarded_self` incremented.
  2. **Content rule.** Every transcript (wake-initiated or barge-in) that
     matches the last 60 s of spoken sentences is discarded, whatever the
     timing. This is stricter than the browser and costs nothing: Friday
     never needs to hear its own words back.
- **One implementation of "matches".** `friday_voice/self_trigger.py` is a
  port of `self-trigger.ts` (`normalizeSpeech` with Turkish lower-casing
  and NFC, wake-phrase stripping, containment or ≥ 60 % token overlap
  for ≥ 2 tokens). Shared vectors `node/friday-voice/vectors/self_trigger.json`
  run in pytest and in a new vitest case.
- **Echo double.** `EchoDouble` in the reference host mixes the child's
  own `play` PCM back into the `audio` frames with a configurable delay
  and residual gain (0 = perfect AEC, 1 = no AEC), so the self-trigger
  and barge-in tests cover both a good and a failing canceller.

### D11 — Always-on arming policy and the false-accept gate (d3, mechanical)

- **The child arms wake mode only when all hold:** (a) the host's local
  mode is `always_on` (set in the node UI, ADR-013 decision 5); (b)
  `GET /api/v1/voice/live/policy` says `always_on_permitted: true` for the
  sha256 of the wake model the child loaded; (c) `link: up`; (d) mute is
  readable and off; (e) no storm mute (D12); (f) every model hash
  verified. Otherwise it is `armed_ptt` (hotkey only) or `off`. The policy
  is re-read at session start and every 10 minutes; a `false` disarms at
  once.
- **`always_on_permitted`** is true only when `VOICE_LIVE_ALWAYS_ON_ENABLED`
  (default false) is set **and** a `voice.VoiceWakeEvaluation` row exists
  for that model sha256 with: kind `household`, `hours ≥ 24`,
  `far_per_hour ≤ VOICE_LIVE_MAX_FAR_PER_HOUR` (0.2); and a kind
  `positives` row with `frr ≤ VOICE_LIVE_MAX_FRR` (0.10). The response
  carries the reasons when false (`disabled`, `no_household_eval`,
  `far_above_budget`, `no_positive_eval`, `frr_above_budget`,
  `model_mismatch`, `storm_muted`).
- **`VoiceWakeEvaluation`** (`WorkspaceScopedModel`, personal workspace):
  `model_sha256`, `kind` ∈ {household, negative_set, positives},
  `hours` (decimal), `false_accepts`, `far_per_hour`, `positives`,
  `false_rejects`, `frr`, `threshold`, `report_sha256`, `recorded_by`,
  `recorded_at`. No audio, no transcript, no free text. Written only by
  `manage.py voice_wake_eval record` (owner shell; explicit numbers or
  `--from-report <json>` from branch 6's harness), audited
  (`voice.wake_eval_recorded`), append-only.
- **Justification.** d3 says always-on ships only after the budget is
  met. Making the server refuse to permit it until a measured row exists
  for the exact model turns a promise into a check; tying it to the model
  hash means retraining `hey_friday` re-closes the gate until it is
  re-measured. The server can only say no: it cannot turn always-on on
  if the owner chose push-to-talk on the Mac, so this is not a remote
  policy write (ADR-013 decision 5 holds).

### D12 — Storms, silence close, fail closed (ADR-015 decision 5, ADR-013 decision 6)

- **Session close.** After 30 s without speech the session closes:
  `listening`/`armed` → the arming state, the conversation id is kept for
  reuse (D5), an `end` earcon plays. A session also closes on
  `VOICE_LIVE_SESSION_MAX_MINUTES` (default 15) of continuous activity.
- **Wake storm (client).** More than 3 wake activations within 60 s
  without a dispatched turn → `storm_muted`: the child stops consuming
  audio, sends `release_mic` and `state: storm_muted`; it re-arms only
  after the owner un-mutes on the Mac (a `mute` true→false cycle). Same
  constants as the browser (`VOICE_WAKE_STORM_MAX = 3`, 60 s).
- **Storm backstop (server).** The child might be the thing that
  misbehaves, so the server counts transcription requests per node voice
  token in a sliding 60 s window (Redis). Above
  `VOICE_LIVE_STORM_MAX_STT_PER_MINUTE` (default 8: well above a fast
  real conversation, far below a storm) it sets `voice.VoiceLiveMute` for
  that node device (personal workspace; `engaged_at`, `reason`,
  `released_at`, `released_by`), audits it, and answers every node voice
  request with 423 `voice.live_muted` until the owner releases it **on
  screen**: `DELETE /api/v1/voice/live/mute` (web session, owner) from a
  banner on `/voice`. The policy endpoint reports `storm_muted`.
- **Fail closed.** Mute unreadable → muted; `link: down` (node WS lost,
  ADR-013 decision 6) → release the mic, close the session; credential
  refused → release; host watchdog (D6) → release; child crash → host
  releases and restarts with backoff. The `off`/`muted` states are
  visible on the menu-bar icon and the HUD ring (ADR-015 decision 2).

### D13 — Self-trigger test and the FAR/FRR harness

- **CI self-trigger test (release gate).** The replay harness plays the
  fixture `selftrigger_hey_friday_terminali_ac.wav` (synthesised by fake
  TTS from the text "Hey Friday, terminali aç" — a deterministic tone
  pattern with a scripted transcript, never a real recording) through
  the reference host as Friday's own playback, with the `EchoDouble` at
  residual 0.0, 0.3 and 1.0, and a scripted wake detector that fires on
  the echo. Assertions for every residual: no chat turn posted, no
  `draft`, nothing spoken in reply, `discarded_self` incremented; and,
  with the self-trigger filter disabled in a negative control, the same
  run **does** post a turn (so the test proves the filter, not the fake).
  A second case drives the real chat endpoint with the transcript as a
  voice turn and asserts at most a `node_action` draft (the Phase 10
  release-gate regression, now through the node voice channel).
- **Nightly self-trigger (Mac, M3).** Same scenario with the real
  openWakeWord model, real TTS through the speech server, real speakers
  and the real AEC. `friday-voice selftest --nightly` exits non-zero on
  any wake or posted turn; a launchd job on the owner's Mac runs it
  nightly and writes a one-line result the preflight check reads
  (`VOICE_LIVE_SELFTEST_RESULT_PATH`). Real models never run in GitHub CI
  (CLAUDE.md rule 5).
- **FAR/FRR harness.** `friday-voice wake-eval --negative <dir> --positives
  <dir> --model <onnx> --threshold <t>` streams audio files through the
  real `OpenWakeWordDetector` (no server, no network), counts detections
  (with the 2 s refractory period the pipeline uses) and writes a JSON
  report: hours, false accepts, FAR per hour, positives, false rejects,
  FRR, model and threshold, file-manifest sha256 — **no audio, no file
  names beyond hashes**. Tested here on synthetic fixtures with a
  scripted detector, and once with the real detector and the stand-in
  `hey_jarvis` head on a synthetic file (opt-in `-m real_models`, runs on
  Linux CPU). The owner's 24 h household audio and positive recordings
  are Mac steps; `manage.py voice_wake_eval record --from-report` turns a
  report into the D11 row.

### D14 — Latency trace

- `VoiceTurnTrace` gains `source` (`web` | `node`, default `web`),
  `wake_to_listen_ms` (detection → capture start, client), and
  `interrupted_at_ms` (nullable, ms after playback start). Still no
  free-text column; the allow-list test is updated. The child reports its
  stages with the last speech request exactly as the browser does
  (`timings`), and the speech endpoint accepts the new keys from either
  channel.
- `friday-voice replay --report` prints per-stage p50/p95 over a fixture
  set with `FakeFriday` latencies scripted at the §c.1 budgets; a stored
  baseline (`node/friday-voice/baselines/replay.json`) fails the run when
  the pipeline's own overhead (everything that is not a scripted server
  latency) regresses > 20 % p50 (§c.1 nightly rule, applied to the part
  this repo controls). Real end-to-end p50/p95 are Mac measurements (M4).

### D15 — HUD and the node's mic state

- `nodes.protocol` gains the node→server event kind `voice.state`
  (protocol v2 only; v1 nodes cannot send it): one item `{state, mode,
  session_open, wake_accepts_today, wake_discards_today,
  storm_mutes_today, at}` with every field bounded and enumerated; no
  audio, no text. The host derives it from the child's `state` messages
  (rate-limited to 1 per second, coalesced). The server→node set is
  untouched; the conformance cases gain the new kind (valid, oversize,
  unknown state, sent on v1 → frame error).
- `nodes.events` stores the latest state on the `Node` row
  (`voice_state`, `voice_mode`, `voice_state_at`, the three counters) —
  `nodes` data, no sink, no `voice` import. `hud.services.node_section`
  shows it; a state older than two heartbeats reads `unknown`.
- **No draft list in the HUD.** `hud` may not import `assistant` (its
  contract), and a draft list would need `Message` rows. The HUD
  therefore shows only the mic state and the counters; a live
  conversation's cards are on `/chat/<id>` (D4), and the HUD's existing
  approvals section shows any approval the owner then creates from them.
  The `/voice` page gains a "Canlı ses (Mac)" panel: the live policy
  verdict with reasons, the storm-mute banner with its release button,
  and links to the last live conversations.
- The FAR estimate shown in the HUD is `wake_accepts_today` minus
  dispatched turns per hour of armed time — an indicator, not a
  measurement; the gate (D11) uses only recorded evaluations.

### D16 — Package, dependencies, logs and contracts

- **Location.** `node/friday-voice/` at the repository root: its own
  `pyproject.toml` and `uv.lock`, package `friday_voice`, console script
  `friday-voice` (`run`, `replay`, `selftest`, `wake-eval`,
  `conformance`, `conformance-child`). It imports nothing from `backend/`
  (a test asserts it); the backend imports nothing from it. A new CI job
  `friday-voice` runs ruff, the format check, pytest and
  `friday-voice conformance --check` with Python 3.12 via `uv`.
- **Install on the Mac.** `make friday-voice-install` runs `uv sync
  --frozen` into `~/Library/Application Support/Friday/voice/`; the Swift
  host launches `…/.venv/bin/friday-voice run` and refuses to start it
  when the installed `uv.lock` sha256 differs from the one compiled into
  the host. The child is **not** inside the signed bundle (Python is not
  notarizable as a resource in a useful way), and Piper is never in
  either (GPL-3.0, roadmap §c.8): Piper stays the speech server's
  separate executable.
- **Dependency governance (ADR-010).** One manifest
  `docs/mcp/dependencies/friday-voice.md` (pipecat-ai, onnxruntime,
  openwakeword, Silero VAD model, smart-turn v3 model, httpx, numpy:
  exact versions, licences, hashes, update rule), and the allowlist entry.
  The openWakeWord licence is recorded from its LICENSE and model cards:
  code Apache-2.0; the melspectrogram and embedding graphs CC BY-NC-SA
  4.0 (Phase 6 branch 4 spike); the owner-trained `hey_friday` head is
  derived from the embedding graph and is treated as CC BY-NC-SA 4.0 too
  — fine for a single-owner, never-distributed system, a blocker if
  Friday is ever productized. smart-turn v3 and Silero licences are
  recorded the same way in branch 2's step 0.
- **Logs.** stderr only, a code plus a short detail scrubbed with the
  vendored ADR-012 scrubber (the reference node's `_scrubber_vendored.py`
  pattern: a generated, byte-identical copy checked by `--check`), never a
  transcript, reply text or token. The host writes them to its rotated
  local log (ADR-013 decision 12).
- **Import contracts.** `voice` keeps its contract (it still may not
  import `accounts`; the new authenticator is injected by `config/urls.py`
  as today). `accounts` gains the `node_voice` kind and its mint view
  (accounts already owns node credentials). `nodes` gains only the event
  kind and its fields. No new edge anywhere.

## ADR

No new ADR. The decisions extend existing ones:

- **ADR-011 amendment (branch 1):** "Amendment 2026-11 — the node voice
  channel": decision 1's "web channel only" becomes "the owner's web
  session or the node voice credential (D3–D4)"; decisions 2–6 unchanged;
  `input_mode` is forced to `voice` on the node voice channel.
- **ADR-013 implementation note (branch 2):** the voice child, the
  stdio-only link, the detector reading of decision 8 (D2), the
  `voice.state` event kind (branch 7).
- **ADR-015 implementation notes (branches 4–6, closeout):** decision 4
  on the echo-cancelled signal with the content rule (D10), decision 5's
  storm backstop (D12), decision 2 on the menu-bar icon and HUD (D15),
  the self-trigger test as a release gate, and the d3 gate (D11).

Nothing moves between gate-class and confirm-class.

## Scope (one branch each)

### 1. `feat/p11-node-voice-channel` — credential, channel, endpoint acceptance, ADR-011 amendment

**Scope.** D3, D4, D5:
- `accounts`: `PersonalAccessToken.Kind.NODE_VOICE`, `node_device` FK and
  its check constraint (migration), `NodeVoiceTokenAuthentication`,
  `KIND_CHANNELS[NODE_VOICE] = CHANNEL_NODE_VOICE`,
  `POST /api/v1/auth/nodes/voice-token` (mint, revoke-previous, audit,
  throttle), device revoke / `revoke_all_devices` / kill switch revoke the
  device's voice tokens;
- `core`: `CHANNEL_NODE_VOICE`, `IsWebOrNodeVoiceChannel`;
- `assistant`: `Message.Channel.NODE_VOICE` (migration), chat view forces
  `input_mode=voice` on the channel, the node-action argument step accepts
  it, and `ConversationMessageListView` alone gains the authenticator
  (ahead of the shared `_PAT_ALLOWLISTED_AUTHENTICATION_CLASSES`, which
  stay unchanged, so every other assistant view answers the token with
  the base PAT authenticator's 403 `token_kind_forbidden`);
- `tools/registry.py`: `READ_CHANNELS` widened as D4 says;
- `voice`: the three endpoints accept the channel; `GET /voice/live/policy`
  (returns `always_on_permitted: false, reasons: ["disabled"]` until
  branch 5) and `POST /voice/live/sessions`;
- `config/urls.py`: inject the authenticator into `voice` routes;
- settings and `.env.example`: `VOICE_LIVE_TOKEN_TTL_SECONDS`,
  `VOICE_LIVE_CONVERSATION_REUSE_MINUTES`, throttle rates;
- ADR-011 amendment.

**Files.**
- `backend/accounts/{models,authentication,devices,views,urls,serializers}.py`,
  `backend/accounts/migrations/00xx_node_voice_token.py`,
  `backend/accounts/tests/test_node_voice_token.py`;
- `backend/core/{channels,permissions}.py`, `backend/core/tests/`;
- `backend/assistant/{models,views,services}.py`, migration,
  `backend/assistant/tests/test_node_voice_turns.py`;
- `backend/tools/registry.py`, `backend/tools/tests/`;
- `backend/voice/{views,urls,services,serializers}.py`,
  `backend/voice/tests/test_live_endpoints.py`;
- `backend/config/urls.py`, `backend/config/settings/base.py`,
  `backend/config/tests/test_node_voice_surfaces.py`, `.env.example`;
- `docs/adr/ADR-011-voice-transport.md`.

**Tests.**
- Mint: node secret only (web session, PAT of every kind, voice token,
  anonymous → 401/403), revoked node and engaged kill switch refused,
  one live token per device (the second mint revokes the first), the raw
  token returned once and never logged or audited, TTL > 3 600 refused at
  boot.
- Authentication: expiry, revoke, device revoke after mint, kill switch
  after mint, `revoke_all_devices`, inactive owner → refused on the next
  request; wrong kind on every other PAT surface → 403.
- Route walk: the token reaches exactly the D4 list; every other route
  refuses it.
- Chat turn: `input_mode` forced to `voice` even when the body says
  `text`; the ADR-015 cap applies (a would-be executing intent degrades to
  chat); a personal connector read works on the channel and still fails
  on `cli`; a `node_action` draft is produced and is a draft only
  (Phase 10 regression re-run with the new channel); the conversation of
  another workspace (firm, other user) → 404.
- Voice endpoints: role × endpoint over the five Phase 4 principals plus
  `hook`/`node`/`node_voice` credentials; a firm-workspace conversation
  id with a voice token → 404.
- Sessions: reuse inside the window, a new conversation after it, never a
  conversation of another channel or workspace.
- Message serializer exposes `channel: "node_voice"`.

**Done when:** a node voice token minted with the reference node's secret
transcribes a fixture WAV, posts a voice turn and synthesizes its reply
against FakeAdapter and the fake speech provider, and the route walk is
green.

### 2. `feat/p11-voice-child-protocol` — package, pins, `friday-voice/v1`, reference host, conformance

**Step 0 (recorded in this brief before code):** choose and record the
exact `pipecat-ai`, `onnxruntime`, `openwakeword`, `httpx` versions (the
latest releases at least two weeks old), their licences from the LICENSE
files and model cards (openWakeWord, Silero, smart-turn v3), the
smart-turn v3 model URL and sha256, and confirm Pipecat installs and
imports on Linux without provider extras.

**Scope.** D1, D6, D7 (pins and snapshot), D16:
- `node/friday-voice/` package skeleton (`pyproject.toml`, `uv.lock`,
  console script, ruff config), `models.json` and its parity test against
  the frontend manifest;
- `friday_voice/protocol/` (framing, schemas v1, vectors), the frozen sets
  and the policy-shaped meta-test;
- `friday_voice/reference/host.py` (the host double, `EchoDouble`,
  watchdog, fail-closed rules), `friday_voice/conformance/` (cases,
  runner, hostile child and hostile host, `--check`);
- `friday_voice/pipecat_adapter.py` (empty surface for now),
  `scripts/snapshot_pipecat.py`, `pipecat_surface.json`;
- the import guard, the no-listener tests, the vendored scrubber copy and
  its `--check`;
- CI job `friday-voice`; `Makefile` targets `friday-voice-test`,
  `friday-voice-install`;
- `docs/mcp/dependencies/friday-voice.md`, `docs/mcp/allowlist.md` entry;
- the repo-wide `import piper` guard in `ai_core/tests/test_sdk_guard.py`;
- ADR-013 implementation note (child, stdio, detector reading).

**Files.**
- `node/friday-voice/{pyproject.toml,uv.lock,models.json,README.md}`,
  `node/friday-voice/friday_voice/{__init__,cli,ports,pipecat_adapter,log}.py`,
  `node/friday-voice/friday_voice/protocol/{framing,messages}.py`,
  `node/friday-voice/friday_voice/protocol/{schemas/v1/,vectors/}`,
  `node/friday-voice/friday_voice/reference/{host,echo}.py`,
  `node/friday-voice/friday_voice/conformance/{cases/,runner,hostile}.py`,
  `node/friday-voice/friday_voice/_scrubber_vendored.py`,
  `node/friday-voice/scripts/snapshot_pipecat.py`,
  `node/friday-voice/tests/`;
- `.github/workflows/ci.yml`, `Makefile`;
- `backend/ai_core/tests/test_sdk_guard.py`;
- `docs/mcp/dependencies/friday-voice.md`, `docs/mcp/allowlist.md`,
  `docs/adr/ADR-013-friday-node-protocol.md`.

**Tests.**
- Framing: round trip of every vector; over-length, short read, odd PCM
  length, header/payload length mismatch, invalid UTF-8, JSON with
  duplicate keys → protocol error with its code.
- Schemas: meta-test (`additionalProperties: false`, every string, array
  and integer bounded), frozen sets per direction, the `POLICY_SHAPED`
  regex over every name.
- Reference host: watchdog releases after 10 s without `state` (manual
  clock); child exit releases; unknown child frame releases; `play` for
  an unknown utterance is dropped and counted.
- Conformance: every host case passes against the reference host and
  every child case against a minimal reference child (the protocol shell
  of `friday_voice`, without the pipeline); `--check` fails on a changed
  case file.
- Guards: forbidden imports (D2 list) absent from `friday_voice/`; no
  `socket.bind`/`listen`, `asyncio.start_server`, `http.server` or
  `uvicorn` in the package (AST scan) and, at runtime, no listening socket
  after `friday-voice run` starts against the reference host
  (`psutil`-free check via `/proc/net` on Linux, skipped elsewhere); the
  package imports nothing from `backend/`.
- `models.json` parity with `model-manifest.json` for the shared entries.
- Scrubber copy byte-identical to `memory/ingestion/scrubber.py`'s
  generator output.

**Done when:** `friday-voice conformance` prints all-pass for both
families against the reference implementations, the CI job is green and
the case and vector files are what the Mac session's Swift test target
loads.

### 3. `feat/p11-pipecat-pipeline` — pipeline, gateway clients, replay harness, trace

**Scope.** D7 (adapter), D8, D14:
- the Pipecat pipeline with the ports and fakes of D8: `WakeGate`,
  `TurnCapture` (Silero + smart-turn v3 through the adapter),
  `FridaySTT`, `FridayChatLLM` (SSE), the segmenter port, the language
  detector, `FridayTTS`, the play-frame writer, the error handling;
- push-to-talk path (`ptt` down/up → capture, no wake) — always-on comes
  in branch 5, so wake mode is wired but the arming policy refuses it;
- `friday-voice run` (stdio, against a host) and `friday-voice replay`
  (fixtures, reference host, `FakeFriday` or `--server <url>` with a
  token for the closeout e2e);
- shared vectors for the segmenter (+ the vitest case);
- `VoiceTurnTrace` fields (`source`, `wake_to_listen_ms`,
  `interrupted_at_ms`), migration, the speech endpoint's `timings`
  allow-list, the allow-list test;
- the replay baseline and its regression check;
- synthetic fixture WAVs (tones, silence, scripted transcripts) only.

**Files.**
- `node/friday-voice/friday_voice/{pipeline,wake,turn,client,llm,segmenter,language,tts,audio}.py`,
  `node/friday-voice/friday_voice/fakes.py`,
  `node/friday-voice/fixtures/` (synthetic WAVs + scripts),
  `node/friday-voice/vectors/segmenter.json`,
  `node/friday-voice/baselines/replay.json`,
  `node/friday-voice/tests/`;
- `frontend/src/lib/voice/segmenter.vectors.test.ts`;
- `backend/voice/{models,serializers,services}.py`, migration,
  `backend/voice/tests/test_trace.py`.

**Tests.**
- Adapter: every function in `pipecat_adapter.py` against the pinned
  version; `snapshot_pipecat.py --check`.
- Pipeline with fakes: push-to-talk turn end to end (frames in → STT
  request with the WAV and trace header → chat request with
  `input_mode: voice` → sentences → TTS requests with per-sentence
  language → `play` frames in order); a `draft` event yields "Kart
  ekranda" and nothing else; an empty transcript yields nothing; a 401
  re-requests the credential once then releases; 423 → `storm_muted`;
  429 → error earcon and nothing spoken; a chat network error is never
  retried; 30 s utterance cap.
- Wake gate (scripted detector): no pre-roll (the first uploaded sample
  is the frame after detection), nothing uploaded for a wake without
  speech within 5 s, pre-wake audio never leaves the ring buffer (the
  fake client sees only post-wake samples).
- Turn end: scripted VAD/smart-turn sequences → end of turn at the right
  frame; hard caps.
- Segmenter and language: shared vectors in pytest and vitest.
- Trace: the child's timings land in `VoiceTurnTrace` with `source=node`;
  the shape allow-list holds.
- Replay: the fixture set runs in CI under a manual clock, reports
  per-stage numbers, and a synthetic 25 % overhead regression fails it.
- Real detectors (opt-in `-m real_models`, Linux CPU): Silero and
  smart-turn load from pinned hashes and score a fixture.

**Done when:** `friday-voice replay` runs the fixture set with fakes in
CI, and `friday-voice replay --server` completes a push-to-talk turn
against a local dev server with FakeAdapter and the fake speech provider.

### 4. `feat/p11-barge-in` — barge-in, self-trigger rule on the AEC signal

**Scope.** D9, D10:
- `friday_voice/session.py` (the full state machine with the exhaustive
  transition table), `BargeInMonitor`, cancellation of TTS and SSE, the
  pre-roll, `interrupted_at_ms`;
- `friday_voice/self_trigger.py` (window rule + content rule), shared
  vectors and the vitest case over `self-trigger.ts`;
- `EchoDouble` scenarios (residual 0 / 0.3 / 1.0);
- the ADR-015 implementation note for decision 4.

**Files.**
- `node/friday-voice/friday_voice/{session,barge_in,self_trigger}.py`,
  `node/friday-voice/vectors/self_trigger.json`,
  `node/friday-voice/fixtures/` (barge-in and echo scripts),
  `node/friday-voice/tests/`;
- `frontend/src/lib/voice/self-trigger.vectors.test.ts`;
- `docs/adr/ADR-015-voice-originated-turns.md`.

**Tests.**
- Transition table: every state × input → state and outbound frames.
- Barge-in: speech on the AEC signal for ≥ 250 ms during playback →
  `stop_playback` ≤ 100 ms after onset (manual clock), TTS and SSE
  cancelled (the fake records the aborts), next utterance includes 300 ms
  pre-roll and nothing from before the session; speech shorter than
  250 ms or within the first 300 ms of playback does not barge in.
- Echo: with residual 1.0 and no AEC, Friday's own playback never barges
  in on itself (the content rule discards the transcript; the session
  continues); with residual 0.3 the same; with 0.0 nothing is detected.
- Self-trigger: shared vectors; window rule (inside/outside 1.5 s,
  during playback); content rule over the last 60 s.

**Done when:** the barge-in and echo scenarios are green in CI at all
three residuals, and the shared self-trigger vectors pass in both
languages.

### 5. `feat/p11-always-on` — arming policy, false-accept gate, storms, fail closed

**Scope.** D11, D12:
- child: the arming policy (a)–(f), policy polling, wake-storm rule,
  30 s silence close, 15 min session cap, every fail-closed input of D6;
- server: `voice.VoiceWakeEvaluation`, `voice.VoiceLiveMute`, the policy
  endpoint's real verdict and reasons, the Redis storm backstop on the
  transcription endpoint for the node voice channel, 423 on every node
  voice request while muted, `DELETE /api/v1/voice/live/mute` (web, owner),
  `manage.py voice_wake_eval record` (explicit numbers; audited), settings
  `VOICE_LIVE_ALWAYS_ON_ENABLED`, `VOICE_LIVE_MAX_FAR_PER_HOUR`,
  `VOICE_LIVE_MAX_FRR`, `VOICE_LIVE_STORM_MAX_STT_PER_MINUTE`,
  `VOICE_LIVE_SESSION_MAX_MINUTES`;
- frontend `/voice`: the "Canlı ses (Mac)" panel (policy verdict and
  reasons, storm banner with the release button, last live
  conversations);
- ADR-015 note for decision 5 and the d3 gate.

**Files.**
- `node/friday-voice/friday_voice/{arming,policy}.py`,
  `node/friday-voice/tests/`;
- `backend/voice/{models,views,urls,services,serializers}.py`, migration,
  `backend/voice/management/commands/voice_wake_eval.py`,
  `backend/voice/tests/test_live_policy.py`,
  `backend/voice/tests/test_storm_backstop.py`;
- `backend/config/settings/base.py`, `.env.example`;
- `frontend/src/components/voice/LiveVoicePanel.tsx` (+ test),
  `frontend/src/lib/voice/api.ts`, the BFF route for the mute release;
- `docs/adr/ADR-015-voice-originated-turns.md`.

**Tests.**
- Arming: each of (a)–(f) false alone keeps wake mode off (parametrised);
  all true arms; a policy flip to false disarms at once; the server can
  never arm when the host mode is `push_to_talk` or `off`.
- Policy verdict: every reason code, model hash mismatch, FAR at the
  budget boundary (0.2 passes, 0.21 fails), `hours < 24` fails, FRR
  boundary, the setting off; evaluation rows are append-only and audited;
  the command refuses impossible numbers (negative, FAR inconsistent with
  hours and false accepts).
- Storms: client rule (4th activation within 60 s without a turn →
  `storm_muted`, `release_mic`; re-arm only after an un-mute cycle);
  server backstop (9th transcription in 60 s → `VoiceLiveMute`, audit,
  423 on every node voice endpoint incl. the chat turn, the policy says
  `storm_muted`; release by web owner only — node voice, PAT, a
  non-owner member → refused).
- Fail closed: `link: down`, `mute: unreadable`, stdin EOF, credential
  refused, host watchdog → `release_mic` and no further STT request (the
  fake records none).
- Silence close at 30 s and the 15 min cap (manual clock).
- Role × endpoint for the policy, mute release and sessions endpoints.
- Frontend: panel renders each verdict, the release button calls the BFF
  route and is absent when not muted.

**Done when:** with `VOICE_LIVE_ALWAYS_ON_ENABLED=true` and a recorded
passing evaluation for the fixture model hash, the replay harness arms
wake mode and completes a wake-initiated turn; without either it stays in
push-to-talk; a scripted storm mutes on both sides.

### 6. `feat/p11-wake-eval-harness` — self-trigger release gate, nightly selftest, FAR/FRR harness

**Scope.** D13:
- the CI self-trigger test (three residuals, negative control, the
  chat-endpoint case through the node voice channel);
- `friday-voice selftest` (`--ci` with fakes; `--nightly` with real
  models, real TTS through `--server`, the host's real audio path — on
  the Mac only; writes the one-line result file);
- `friday-voice wake-eval` (negative / positives / model / threshold →
  JSON report with hashes only) and `manage.py voice_wake_eval record
  --from-report` (validates the report, its model hash and its
  internal consistency);
- `docs/voice/live-eval.md`: how to record the 24 h household audio and
  the positives (outside the repo), run the harness, record the report,
  and schedule the nightly launchd job.

**Files.**
- `node/friday-voice/friday_voice/{selftest,wake_eval}.py`,
  `node/friday-voice/fixtures/selftrigger_*`,
  `node/friday-voice/tests/test_selftest.py`,
  `node/friday-voice/tests/test_wake_eval.py`;
- `backend/voice/management/commands/voice_wake_eval.py`,
  `backend/voice/tests/test_wake_eval_record.py`;
- `docs/voice/live-eval.md`.

**Tests.**
- The self-trigger release gate as D13 says (and the negative control
  proves the gate bites).
- `wake-eval` on synthetic fixtures with a scripted detector: refractory
  period, FAR per hour arithmetic, FRR, the report contains no file name
  and no audio; real detector with the stand-in head (`-m real_models`).
- `--from-report`: a tampered report (hash mismatch, hours vs. files,
  FAR not equal to false accepts / hours) is refused; a valid one writes
  exactly one row.
- `selftest --nightly` refuses to run without `--server` and a model
  directory; its exit code and result line are tested with fakes.

**Done when:** the self-trigger test is a required CI step and green, and
a report produced by `wake-eval` round-trips into a `VoiceWakeEvaluation`
row that the policy endpoint honours.

### 7. `feat/p11-hud-live-voice` — `voice.state` event, HUD mic state, live drafts on screen

**Scope.** D15 and D4's on-screen half:
- `nodes.protocol`: `EVENT_KINDS` gains `voice.state` (v2), the v2 event
  schema's per-kind item schema, conformance cases (valid, oversize,
  unknown state, on v1); `build_node_conformance` regenerated;
- `nodes`: `Node` voice fields (migration), `nodes.events` handling
  (coalesced, rate-limited per node, no sink), the reference node relays
  a scripted voice state;
- `hud`: `node_section` gains the mic state, mode, session flag and
  counters (stale → `unknown`); the HUD ring shows *listening/thinking/
  speaking* from the node when no browser voice is active;
- frontend: `drafts.ts` accepts `node_voice`; the chat list shows the
  channel badge; HUD node line renders the mic state;
- ADR-013 note (event kind), ADR-015 note (decision 2 on the node).

**Files.**
- `backend/nodes/protocol/{messages}.py`,
  `backend/nodes/protocol/schemas/v2/event.schema.json`,
  `backend/nodes/conformance/cases/v2/`,
  `backend/nodes/{models,events}.py`, migration,
  `backend/nodes/reference/node.py`, `backend/nodes/tests/`;
- `backend/hud/services.py`, `backend/hud/tests/`;
- `frontend/src/components/chat/drafts.ts` (+ tests),
  `frontend/src/components/hud/{HudPanels,HudOrb}.tsx` (+ tests),
  `frontend/src/types/api.ts`;
- `docs/adr/ADR-013-friday-node-protocol.md`,
  `docs/adr/ADR-015-voice-originated-turns.md`.

**Tests.**
- Protocol: frozen server→node set unchanged; the new kind only on v2;
  every field bounded and enumerated; policy-shaped guard; conformance
  `--check`.
- Events: stored on the node row, coalesced (two in the same second →
  one write), stale after two heartbeats, never reaches a sink, other
  node's or revoked node's event → refused as today.
- HUD: node line with each state; a node of another owner never shown.
- Drafts: a `node_voice` task/tool draft renders as a card and is
  confirmed only by click; a `cli` draft still does not render.

**Done when:** the reference node relays a scripted child state and the
HUD shows it, and a live voice draft appears as a clickable card on the
conversation page.

### 8. `chore/p11-closeout` — preflight, e2e, runbook, ADR notes, go/no-go record

**Scope.**
- **`manage.py preflight` live-voice checks** (a new `check_live_voice`):

  | Check | What it verifies |
  |---|---|
  | `voice_live_channel` | the `node_voice` kind and authenticator are registered on exactly the D4 surfaces (route walk at runtime); token TTL ≤ 3 600 |
  | `voice_live_policy` | `VOICE_LIVE_ALWAYS_ON_ENABLED` and the latest evaluations: **warn** "always-on not permitted: <reasons>" while no passing household + positives rows exist (pending owner evaluation), pass when they do, **fail** when the setting is on with FAR or FRR above budget |
  | `voice_live_storm` | an engaged `VoiceLiveMute` is a **warn** with its age |
  | `voice_live_selftest` | the nightly result file: pass when green and < 48 h old; **warn** when missing (Mac pending); **fail** when the last run failed |
  | `friday_voice_conformance` | the case and vector files in the repo match `friday-voice conformance --check` output recorded at build (hash list in `node/friday-voice/conformance.lock`) |
  | `node_voice_state` | per active node: a v2 node reports `voice.state`; **warn** when none does (Mac pending) |

- **E2E (CI, fakes):** the reference host + `friday-voice run` + a live
  dev server (uvicorn, FakeAdapter, fake speech provider), all over real
  HTTP: push-to-talk turn spoken back sentence by sentence; a voice
  turn naming a node action → draft only, card on `/chat/<id>`; barge-in
  mid-reply → playback stopped, trace `interrupted_at_ms` set, next turn
  answered; the self-trigger clip at residual 1.0 → no turn; kill switch
  engaged mid-session → next request refused, `release_mic`, HUD state
  `off`; storm → server mute, 423, release on screen re-enables.
- **Docs:** `docs/runbooks/live-voice.md` (install the child, the speech
  server with MLX Whisper, the always-on gate, storms and release, the
  nightly launchd job, reading traces, troubleshooting), ADR-011/013/015
  notes completed, MASTER_PLAN §AA row 11, `self_sources.json`
  regenerated, this brief's status table and go/no-go record.

**Files.**
- `backend/ops/preflight.py`, `backend/ops/tests/test_preflight_live_voice.py`,
  `backend/ops/tests/test_live_voice_e2e.py` (spawns the child from
  `node/friday-voice` via `uv run` in the CI job that has both);
- `.github/workflows/ci.yml` (the e2e step in the `friday-voice` job);
- `docs/runbooks/live-voice.md`, `docs/adr/ADR-01{1,3,5}-*.md`,
  `docs/MASTER_PLAN.md`, `backend/assistant/self_sources.json`,
  `docs/prompts/phase-11-live-voice/brief.md`.

**Tests.** Every new preflight check passes, warns and fails with fakes
(always-on enabled with FAR above budget, stale selftest, failed
selftest, engaged mute, conformance drift, a voice token reaching a
forbidden surface); the HUD reads the new check names; the e2e passes.

**Done when:** preflight is green on the fake stack apart from the
documented Mac warnings, the e2e is green in CI, and the go/no-go record
is written with the Mac rows pending owner evaluation.

## Branch table

| # | Branch | Scope | Depends on | Status |
|---|---|---|---|---|
| brief | `docs/p11-brief` | This brief (full, from the stub; stub goal, scope, checkpoint and owner step kept), MASTER_PLAN §AA row 11 Brief/Status | Phase 10 closeout; Phase 10 brief | **implemented 2026-11-20** (docs only; awaits independent Sonnet 5 plan review) |
| 1 | `feat/p11-node-voice-channel` | `node_voice` credential + mint, `CHANNEL_NODE_VOICE`, chat/voice endpoint acceptance, personal reads, node-action proposals, live sessions, route walk, ADR-011 amendment | Phase 10 closeout; brief approved | planned |
| 2 | `feat/p11-voice-child-protocol` | `node/friday-voice` package, pins + step 0, `friday-voice/v1` framing/schemas/vectors, reference host + echo double, conformance (host and child families), import/listener guards, CI job, dependency manifest, ADR-013 note | brief approved (parallel with 1) | planned |
| 3 | `feat/p11-pipecat-pipeline` | Pipecat pipeline, detector ports, gateway STT/chat/TTS clients, segmenter + language routing, push-to-talk, replay harness + baseline, trace fields | 1, 2 | planned |
| 4 | `feat/p11-barge-in` | Session state machine, barge-in, self-trigger window + content rule, shared vectors, echo scenarios, ADR-015 note (d4) | 3 | planned |
| 5 | `feat/p11-always-on` | Arming policy, `VoiceWakeEvaluation` gate, storms (client + server backstop), silence close, fail closed, `/voice` live panel, ADR-015 note (d5, d3) | 3 (parallel with 4) | planned |
| 6 | `feat/p11-wake-eval-harness` | CI self-trigger release gate, `selftest`, `wake-eval` FAR/FRR harness, `--from-report`, eval doc | 4, 5 | planned |
| 7 | `feat/p11-hud-live-voice` | `voice.state` event kind (v2), node voice fields, HUD mic state, `node_voice` drafts on screen, ADR-013/015 notes | 1 (parallel with 3–6) | planned |
| 8 | `chore/p11-closeout` | Preflight live-voice checks, e2e, runbook, ADR notes, MASTER_PLAN row, `self_sources.json`, go/no-go record | 1–7 | planned |
| M1 | `feat/p11-node-voice-host` | Swift voice host: child spawn + stdio framing, AVAudioEngine voice processing (AEC), playback, menu-bar mic indicator, global hotkey, mute, voice token mint + hand-over, Microphone TCC, `voice.state` relay; conformance host family | 1, 2, 7 merged; Phase 9 M1–M3 | deferred: Mac session |
| M2 | Speech server spike (MLX Whisper) | MLX Whisper behind an OpenAI-compatible server as a gateway row; Turkish WER spot check, latency; fallback recorded | 1 merged | deferred: Mac session (pending owner evaluation) |
| M3 | Nightly self-trigger on the Mac | `friday-voice selftest --nightly` with real models, speakers and AEC; launchd job | M1, M2; 6 merged | deferred: Mac session (pending owner evaluation) |
| M4 | Mac evaluation (go/no-go measurements) | FAR on 24 h household audio, FRR, latency p50/p95, barge-in stop time, CPU, TCC across updates | M1–M3 | deferred: Mac session (pending owner evaluation) |

**Minimum usable cut** if the phase runs long: 1, 2, 3, 7 and M1 —
push-to-talk on the Mac (hotkey) through the node child, spoken replies,
mic state on the HUD, drafts on screen. 4 adds barge-in; 5 and 6 are
required before always-on can be permitted at all (the D11 gate stays
closed without them, so the cut is safe by construction).

## Mac session (deferred)

These tasks are for a later Claude Code session **on the owner's
MacBook**, after the Phase 9 Mac session (M1–M3 of the Phase 9 brief) has
produced `node/friday-node/` and the Phase 10 Mac session has at least
merged its M1. They are self-contained: a fresh session starts from this
section, the merged branches 1–7 and the files they name.

**Preconditions:**
- Phase 9 M1–M3 merged (signed, notarized app; Keychain node secret; WSS
  session on protocol v2 at least for events);
- branches 1, 2, 3 and 7 merged here (credential mint endpoint,
  `friday-voice/v1` schemas, vectors and conformance cases, the working
  child, the `voice.state` event kind);
- `uv` installed on the Mac; `make friday-voice-install` run; `make
  voice-models --node` run with the owner-trained `hey_friday.onnx`
  (its sha256 pinned in both manifests);
- the signing key stays on the Mac; CI never holds it.

### M1 — `feat/p11-node-voice-host`: the Swift voice host

**Create:**
- `Sources/FridayNode/Voice/VoiceChildHost.swift`: spawns
  `~/Library/Application Support/Friday/voice/.venv/bin/friday-voice run`
  with stdin/stdout pipes and a clean environment (no inherited
  variables beyond `PATH`, `HOME`, `LANG`), verifies the installed
  `uv.lock` sha256 against the compiled-in value, restarts with backoff
  1 s → 60 s, watchdog 10 s, kills the child on quit, kill switch or WS
  loss;
- `Sources/NodeCore/Voice/VoiceFraming.swift` and
  `Sources/NodeCore/Voice/VoiceMessages.swift`: `friday-voice/v1` framing
  and the two frozen message sets, validated against
  `node/friday-voice/friday_voice/protocol/schemas/v1/`;
- `Sources/FridayNode/Voice/AudioEngine.swift`: `AVAudioEngine` with
  `inputNode.setVoiceProcessingEnabled(true)`, 16 kHz mono PCM16
  conversion, 20 ms `audio` frames, playback of `play` frames through the
  same engine (the AEC reference), `stop_playback` honoured ≤ 100 ms,
  earcons from a fixed bundled set;
- `Sources/FridayNode/Voice/MicIndicator.swift`: menu-bar icon states
  for `off`, `armed_ptt`, `armed_wake`, `listening`, `thinking`,
  `speaking`, `muted`, `storm_muted`, `error`; the Phase 9 reserved
  "off" state becomes live;
- `Sources/FridayNode/Voice/Hotkey.swift`: a global push-to-talk hotkey
  (configured in the node UI), `ptt` frames;
- `Sources/FridayNode/Voice/VoiceSettings.swift`: mic mode (off /
  push-to-talk / always-on), mute and hotkey in the node's local UI;
  changing to always-on requires LocalAuthentication (ADR-013 decision
  5); mute state read failures map to `mute: unreadable`;
- `Sources/NodeCore/Voice/VoiceCredential.swift`: mints the `node_voice`
  token with the Keychain node secret over HTTPS (pinned CA), passes it
  in a `credential` frame, refreshes ≤ 60 s before expiry, never writes
  it anywhere;
- `voice.state` relay to the WS (coalesced, ≤ 1/s);
- the Microphone entitlement and TCC usage string; notarized build;
- tests in `Tests/NodeCoreTests/Voice*`.

**Must satisfy:** `node/friday-voice/friday_voice/protocol/schemas/v1/`,
`…/protocol/vectors/frames.json`, D6 (framing, frozen sets, fail
closed), D12 (release on link loss, mute unreadable, child exit), D15
(event shape), ADR-013 decisions 1, 5, 6.

**Server-side contract tests it must pass:**
1. Offline conformance: every *host* case in
   `node/friday-voice/friday_voice/conformance/cases/` passes in
   `VoiceConformanceTests.swift` with the reference verdicts, none
   skipped.
2. Live conformance: `friday-voice conformance-child --cases <dir>` (the
   scripted hostile child) launched by a conformance build of the host
   (unsigned, `#if CONFORMANCE` only) prints all-pass, including the
   watchdog and child-exit releases.
3. Node conformance: `uv run python manage.py node_conformance_server
   --external --protocol 2` passes the `voice.state` cases added by
   branch 7.
4. Real stack: hotkey push-to-talk "bugün ne var" → spoken reply; a
   voice "testleri çalıştır" → a card on `/chat/<id>` and nothing else;
   kill switch mid-session → icon `off` within 2 s and the next request
   refused — the flows `backend/ops/tests/test_live_voice_e2e.py` runs
   against the reference host (closeout); the Swift host must produce the
   same frames, states and server rows.

### M2 — Speech server spike: MLX Whisper behind the gateway

Install an OpenAI-compatible server that serves MLX Whisper on the Mac
(candidate projects and their licences recorded at the time; exact
version pinned), add it as a speech row (`voice-stt-mlx`,
`personal_data_allowed=True`, cost 0) next to the Phase 6 speaches row,
route `voice.stt` to it, and record: Turkish WER spot check on ten
recordings (owner), p50/p95 STT latency for ≤ 8 s utterances, and the
fallback order (MLX → speaches faster-whisper → whisper.cpp sidecar).
Run `manage.py preflight` (`voice_config` round trip). Results go into
this brief's go/no-go record; the runbook names the chosen server.

### M3 — Nightly self-trigger on the Mac (pending owner evaluation)

Schedule `friday-voice selftest --nightly --server <url>` with a launchd
agent (nightly, Mac on power), playing the self-trigger clip through the
speakers with the host's real AEC and the real `hey_friday` model; the
result line lands in `VOICE_LIVE_SELFTEST_RESULT_PATH` for the
`voice_live_selftest` preflight check. Seven consecutive green nights are
the evidence for the checkpoint.

### M4 — Mac evaluation (go/no-go measurements)

1. **FAR** on 24 h of household audio (owner step 1, recorded outside
   the repo) with `friday-voice wake-eval --negative`; record with
   `voice_wake_eval record --from-report` (target ≤ 0.2 / h).
2. **FRR** on ≥ 50 owner "Hey Friday" positives across rooms and
   distances (target ≤ 10 %).
3. **Latency**, end of owner speech → first audio byte, from
   `VoiceTurnTrace` rows with `source=node` over ≥ 50 turns: p50 ≤ 1.5 s,
   p95 ≤ 2.5 s; if missed, the dominant stage is named and the target
   re-set here consciously (§c.3 rule).
4. **Barge-in**: onset → playback stopped ≤ 200 ms p95 over 20 trials;
   no self barge-in across 20 replies at normal speaker volume.
5. **CPU**: the child with always-on wake < 10 % of one core at rest.
6. **TCC**: the Microphone grant survives an update signed by the same
   Developer ID (Phase 9 M4 item 1 pattern).

## Go/no-go record (Phase 11 checkpoint)

Stub checkpoint: "FAR ≤ 0.2 per hour on 24 h of household audio;
self-trigger test green; latency targets (local p50 ≤ 1.5 s, p95 ≤ 2.5 s)
met".

| Item | How it is measured | Result |
|---|---|---|
| Self-trigger test (CI, fakes, three AEC residuals + negative control) | Branch 6 release gate | pending (branch 6) |
| Self-trigger nightly (real models, speakers, AEC) | M3, seven green nights | pending owner evaluation (Mac session) |
| FAR ≤ 0.2 / h on 24 h household audio | M4 item 1 → `VoiceWakeEvaluation` | pending owner evaluation (Mac session; owner step 1) |
| FRR ≤ 10 % | M4 item 2 | pending owner evaluation (Mac session) |
| Latency p50 ≤ 1.5 s, p95 ≤ 2.5 s (local) | M4 item 3 | pending owner evaluation (Mac session) |
| Replay pipeline overhead within baseline | Branch 3 replay check | pending (branch 3) |
| `friday-voice/v1` conformance, reference host and child | Branch 2 | pending (branch 2) |
| Swift host conformance | M1 | pending owner evaluation (Mac session) |
| Always-on gate refuses without evidence | Branch 5 tests + `voice_live_policy` preflight | pending (branch 5) |

Always-on stays off (the D11 gate closed) until the FAR, FRR and
nightly rows pass; push-to-talk through the node is usable as soon as
M1 lands. The Mac rows do not block Phase 12.

## Out of scope (explicit)

- Speaker verification and any voice decision, confirmation or approval
  (ADR-015 decisions 1 and 7, ADR-011 decision 5).
- Cloud realtime voice (OpenAI Realtime, Gemini Live) and the gateway
  WebSocket relay (d3: "later"; ADR-015 decision 8).
- STT or TTS inside the child, a provider SDK in the child, Piper inside
  any Friday process or bundle (D2, §c.8).
- Live voice into a firm workspace (the credential is personal-bound;
  firm data reaches the turn only through the existing overlay).
- Multiple nodes speaking at once (one node per owner is the Phase 9
  shape; a second node's voice child would work but is not tested for
  contention).
- Browser always-on (the in-tab wake word stays as Phase 6 left it).
- A node voice path over the node WebSocket, or any new server→node
  message type.
- Wake-phrase routing and the spoken morning digest (Phase 12).
- Storing audio for evaluation inside Friday (the owner's evaluation
  audio stays outside the repo and outside the database).
- A Windows or Linux desktop node (d1).

## Test strategy

**Discipline.** As Phases 2–10 (CLAUDE.md rules 5, 7, 8): ruff, ruff
format, pytest, vitest, import-linter, `makemigrations --check`,
`build_self_sources --check`, `build_node_conformance --check` and, new,
the `friday-voice` CI job (ruff, pytest, `friday-voice conformance
--check`, `snapshot_pipecat.py --check`) green per branch.

**What no test touches.** No real microphone, speaker, AEC, Mac,
Keychain, TCC, LLM, speech server, wake model trained by the owner,
household recording or network beyond localhost. The tests use the
reference host (`friday_voice.reference.host`) with `EchoDouble`,
scripted detectors, `FakeFriday`, synthetic fixture WAVs (tones and
silence with scripted transcripts), the manual clock, FakeAdapter and
the fake speech provider on the server side, the reference node for the
`voice.state` event, and real Postgres and Redis. Opt-in
`-m real_models` runs the real ONNX detectors (Silero, smart-turn v3,
openWakeWord with the public stand-in head) on Linux CPU; never in the
default run.

**Release gates.**
- The ADR-015 cap on the node voice channel: a voice turn reads and
  proposes only; `input_mode` cannot be lowered to `text` on the channel.
- The self-trigger test (D13) with its negative control.
- `friday-voice/v1` conformance (both families) and the frozen sets.
- The node protocol conformance with the `voice.state` cases.
- The route walk: the `node_voice` token reaches only D4's surfaces.
- No pre-roll before a wake, no audio written to disk, no listening
  socket in the child.

**Role × endpoint** for every new or widened surface (mint, transcriptions,
speech, receipt, chat turn on the channel, live policy, sessions, mute
release) against the five Phase 4 principals plus `cli`/`openai`/`hook`
PATs, a node secret, a `node_voice` token, a revoked node's token, a
kill-switched owner, and a second (firm) workspace with a non-owner
member.

**Release-gate regressions** (escalate per the CRITICAL/HIGH rule):
- any node voice request succeeding after the node is revoked, the kill
  switch engaged or the token expired;
- a `node_voice` token reaching approvals, tasks, memory writes, nodes,
  harness, deploys, connectors writes or the facade;
- a voice turn producing anything but a read or a proposal; a spoken
  confirmation deciding anything;
- audio uploaded from before the wake detection frame (outside D9's
  in-session pre-roll), or any audio persisted;
- always-on armed without a passing evaluation for the loaded model's
  hash, or while the host mode is not `always_on`;
- the child calling anything but Friday's three voice endpoints and the
  two live endpoints; a provider SDK or a speech engine imported by the
  child; `import piper` anywhere;
- the mic not released on link loss, mute unreadable, child exit or
  credential refusal;
- a new server→node message type, or a policy-shaped name in either
  protocol;
- a model call without a `ModelUsage` row.

## Risks

- **R1 — Room audio proposes things.** Always-on widens who can talk to
  Friday. Mitigations: proposals only (ADR-015), the trust check caps
  voice at `warn`, drafts need clicks on screen and the Mac. Residual:
  personal data read aloud on request (accepted risk, decision 7).
- **R2 — False accepts in a real household.** Mitigation: the D11 gate
  per model hash, the storm rules, the HUD counters. Residual: FAR drifts
  with new household sounds; re-measure after retraining or monthly.
- **R3 — AEC quality on the real Mac.** Voice processing differs by
  model, speaker volume and external mics. The content rule (D10)
  discards Friday's own words even when AEC fails (tested at residual
  1.0); barge-in may be less reliable, which degrades to "Durdur" by
  hotkey.
- **R4 — Pipecat churn.** Exact pin, the surface snapshot, adapter tests,
  upgrades in dedicated branches (§c.8).
- **R5 — Latency over the extra hop (D2).** The STT path is child →
  server → local speech server. If M4 shows the hop dominating, the
  brief is amended (e.g. the server co-located on the Mac), never the
  single-door rule.
- **R6 — A second credential on the Mac.** The child holds a 15-minute
  voice token in memory. Mitigations: device-bound, re-checked per
  request, voice-only surfaces, revoked with the node. Residual: a local
  attacker with the owner's uid could read the child's memory — the same
  attacker could read the browser's session; accepted.
- **R7 — Licence.** openWakeWord's models are non-commercial; the
  `hey_friday` head inherits that. Fine for personal use, a blocker for
  productization (recorded in the manifest and here).
- **R8 — Mac work slips.** Everything server- and child-side is tested
  without it; the gate keeps always-on off until the Mac evidence
  exists. The Mac rows do not block Phase 12.

## Open questions (with recommended answers)

- **Q1 — Should the child use the node WebSocket instead of HTTPS?** No.
  It would need new server→node types (reply text, audio), breaking the
  frozen set that is the node's main defence (Phase 10 D1). HTTPS
  outbound from the child keeps the node protocol unchanged.
- **Q2 — Should STT run in the child for latency?** No (D2): it would be
  a model call outside the gateway and inside the node. Revisit only via
  an ADR-003/ADR-013 amendment if M4 proves the hop breaks the budget on
  co-located hardware.
- **Q3 — One live conversation forever or per stretch?** Per stretch
  (D5), reused within 30 minutes; the setting can be raised.
- **Q4 — Should the server be able to turn always-on off remotely?** It
  already can, only by refusing (D11/D12); it can never turn it on.
- **Q5 — Should personal reads be allowed on the node voice channel?**
  Yes (D4), with the accepted risk recorded; the owner can narrow a
  single read tool's channels if one proves too sensitive aloud.

## Owner steps (pending) — recorded 2026-11-20

The first one is from the stub and is kept.

1. **Record 24 h of household audio** (kept outside the repo) for the
   false-accept measurement (M4 item 1).
2. **Plan review.** Have the independent Sonnet 5 reviewer approve this
   brief before branch 1.
3. **Train and pin `hey_friday.onnx`** (Phase 6 owner step still open)
   and pin its sha256 in both manifests; always-on cannot be permitted
   for an unpinned model.
4. **Record ≥ 50 "Hey Friday" positives** for FRR (M4 item 2), outside
   the repo.
5. **Choose the MLX Whisper server** (M2) and record its version and
   licence.
6. **Start the Mac session** with "Mac session (deferred)" as its task
   list once branches 1, 2, 3 and 7 (and Phase 9 M1–M3) are merged.
7. **Record the go/no-go**: M3 nightly evidence, M4 measurements and the
   `VoiceWakeEvaluation` rows; then set `VOICE_LIVE_ALWAYS_ON_ENABLED=true`
   if, and only if, the rows pass.

### Owner steps (pending) — `docs/p11-brief`, recorded 2026-11-20

1. Run the independent Sonnet 5 plan review of this brief and fix
   findings until APPROVE (CLAUDE.md "Reviews").
2. Confirm the platform-boundary split: server and child branches 1–8
   now; the Swift voice host, the MLX speech server, the nightly run and
   the measurements in a later Mac session.
3. Confirm D2 (STT/TTS stay behind the gateway; MLX Whisper is the Mac
   speech server's engine, not the child's) — the one place this brief
   makes a stub item concrete in a way the stub did not spell out.
4. Confirm D4's widening of personal reads to the node voice channel and
   D11's server-side always-on gate.
