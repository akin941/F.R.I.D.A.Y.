# Phase 6 — Voice (ex-MVP 12) and the visual identity layer

> Status: **Approved (2026-09-27) — Sonnet 5 plan-review round 2
> APPROVE after one REVISE round (see "Plan-review rounds"; Codex CLI is
> retired from the development workflow)**. Implementation waits for the
> precondition below.
> **Amended 2026-11-20 (Friday 3.0, owner-approved):** see "Amendment
> 2026-11-20" below — OpenRouter audio spike and disabled cloud audio rows
> (d2), language-routed TTS, gateway audio caps (ADR-003 amendment), a
> Turkish evaluation set and replay harness (new branch 3a), a latency
> budget with a per-turn trace, the ADR-015 voice-turn safety rules, the
> MASTER_PLAN "Roadmap update 2026-11-20" voice additions, and HUD v1
> (branch 5, `feat/p6-hud`, replaces the visual-identity branch). Source:
> `docs/research/2026-11-roadmap-proposal.md` §B2/B3/B5, §c.1–c.6. The
> amendment awaits its own Sonnet 5 plan review before branch 1 starts.
> Parent decisions: ADR-001 (workspace axis), ADR-002 (import contracts),
> ADR-003 (single gateway door, one `ModelUsage` per call), ADR-004 +
> ADR-008 decision 3 (explicit, separate owner decision; gate-class vs
> confirm-class), ADR-005 (`private` contract; conversation-derived
> memories stay `pending_approval`), ADR-007 (voice promoted, local
> Ollama), ADR-008 decision 5 (local wake word, audio never leaves the
> device before it; local faster-whisper + Piper; visual identity after
> voice works), ADR-009 (Phase 4: device-trust web channel, no
> confirm-class growth by precedent), ADR-010 (Phase 5: personal-data
> provider rule), `docs/VISION_FRIDAY_2.md` §6 rows 6 and 6+,
> MASTER_PLAN §Z, §AA ("Phase 4–6 outline") and §O (STT/TTS provider,
> binary-audio storage — both resolved 2026-07-17),
> `docs/prompts/mvp-12-voice/brief.md` (dormant; this brief supersedes
> its branches 2b, 3 and 4).
> **Precondition — hard:** Phase 5 closeout merged (`integ/phase-5` →
> `main`). Phase 6 consumes symbols that do not exist on `main` today:
> Phase 4 branch 2 (`WebSessionPrincipal`, default-deny
> `request_channel()`, `core.permissions.IsWebChannel`, `User.is_owner`),
> Phase 4 branch 3 (design tokens incl. the brand accent, TanStack Query,
> safe Markdown, grouped nav — which removes "Sesli" from the nav with the
> route kept), Phase 4 branch 5 (`/approvals/[requestId]` decision route),
> Phase 5 branch 2 (`ModelProvider.personal_data_allowed`,
> `set_provider_personal_data`, `UNTIERED_PURPOSES` coverage test). If
> Phase 5 ships without `personal_data_allowed`, Phase 6 branch 1 adds
> the field itself (same shape as Phase 5 D5); nothing else in this brief
> changes. Phase 6 branches are cut from `main` and integrate on
> `integ/phase-6`.

## Goal / value

Every Friday exchange so far is typed and read. Phase 6 makes it spoken —
say "Hey Friday", speak, hear the answer — **entirely on the owner's own
hardware**: the wake word runs in the browser, speech-to-text
(faster-whisper) and text-to-speech (Piper) run on the owner's Mac next
to Ollama, and nothing about the turn itself changes: the transcript goes
through the same intent router, context builder, drafts and approval gate
as a typed message. Only after that loop works does the orb become the
animated "Friday" presence (ADR-008 decision 5: "a visual shell before
the pipeline works would be an empty costume"). Usable outcome
(MASTER_PLAN §AA): "The real Friday experience."

## What already exists (verified 2026-09-27)

- **Gateway spine (MVP 12, `4f34da2`).** `ModelProvider.Capability.STT`/
  `.TTS` (`backend/ai_core/models.py:22-23`); `InferenceGateway.transcribe()`
  (`backend/ai_core/gateway.py:287`) and `.synthesize()` (`:323`) through
  the shared `_run_audio()` (`:358`), which selects providers by capability
  (`_select_providers`, `:455`), retries/falls back and writes exactly one
  `ModelUsage` with `audio_seconds` in `context_report` (`:418`). Adapter
  contract: `TranscribeAdapterResult`/`SynthesizeAdapterResult`
  (`ai_core/adapters/base.py:35,55`); the base `transcribe`/`synthesize`
  raise `not_supported` (`base.py:124,131`). Only `FakeAdapter` implements
  them (`adapters/fake.py:257,265`); tests use purposes `voice.stt`/
  `voice.tts` (`ai_core/tests/test_gateway.py:609-740`).
- **Gaps in the spine.** `OpenAICompatibleAdapter`
  (`ai_core/adapters/openai_compat.py:26`) implements only `complete`,
  `stream`, `embed` (`:42,62,104`) — no real provider can serve STT/TTS
  today. `transcribe()` takes no `params` (no language hint). No STT/TTS
  provider — fake or real — is seeded by `seed_ai_defaults`
  (`ollama-local` at `seed_ai_defaults.py:523` is the pattern to mirror).
  `_estimate_cost` (`gateway.py:778`) is token-priced, so audio rows cost
  0 — correct for local providers, the only kind Phase 6 allows (D5).
- **Frontend.** `components/voice/FridayOrb.tsx` (Canvas 2D, states
  `idle | thinking | speaking`, synthetic jitter for "speaking") and
  `VoiceView.tsx`, which sends typed text through the real chat SSE
  endpoint but ignores `draft` events and shows a disabled mic button
  (`VoiceView.tsx:153`); route `app/(shell)/voice/page.tsx`; nav entry
  `SidebarContent.tsx:15`. No `getUserMedia`, `AudioContext` or audio
  dependency exists anywhere in `frontend/src`.
- **Upload precedent.** `knowledge/views.py:152` `_MaxSizeUploadHandler`
  aborts multipart parsing once a byte cap is crossed (chunked uploads
  included); the BFF has `rejectNonMultipart` (`frontend/src/lib/csrf.ts`)
  used by the knowledge file-upload route. `FILE_UPLOAD_MAX_MEMORY_SIZE`
  is unset (Django default 2.5 MB: smaller uploads never touch disk).

## Boundary vs existing systems

- **Voice is a transport, not a pipeline.** The chat turn stays
  `POST /api/v1/conversations/{id}/messages`
  (`assistant/views.py:197`, SSE, `chat` throttle at `:144`):
  `classify_intent` → `build_gateway_stream` → reply stream → draft events
  (`:252`) → persistence and extraction scheduling
  (`assistant/services.py:500`). Voice adds two edges around it — audio →
  transcript before, reply text → audio after — in a new `voice` kernel
  app. No new intent, router prompt, context source or SSE event type.
- **The approval gate keeps its shape.** No new gated tool, no new
  confirm-class action, no spoken decision (D8). Drafts produced by a
  voice turn are the same `TaskDraftCard`/`ToolRequestCard`
  (`frontend/src/components/chat/`) clicked on screen.
- **`ai_core` stays the single door.** Speech engines run out of process
  behind an OpenAI-compatible HTTP server; the backend imports no speech
  library (the `test_sdk_guard.py` pattern is extended, D4).
- **Workspace axis unchanged.** Voice endpoints act on a conversation the
  actor can reach through `for_actor()`; that conversation's workspace is
  the `ModelUsage.workspace` of every STT/TTS call. No voice table is
  created at all — audio is never persisted (D2).

## Decisions (with justification)

### D1 — Two web-only endpoints around the unchanged chat turn

Flow: capture → `POST /api/v1/voice/transcriptions` (multipart: `audio`,
`conversation`) → `{text, audio_seconds, language}` → the client sends
`text` through the existing messages endpoint with `input_mode="voice"` →
SSE reply → the client segments the text into sentences →
`POST /api/v1/voice/speech` (JSON: `conversation`, `text`) per sentence →
`audio/wav` → playback. Two requests instead of one "voice turn" endpoint
because the messages view already owns the turn's ordering invariants
(classification before context, context before the user row,
single-channel resolution — comments at `assistant/views.py:203-216`);
duplicating them for audio would fork the pipeline ADR-007 promised to
keep single. It also lets the owner see (and, in push-to-talk, correct)
the transcript before anything is sent.

### D2 — Synchronous, bounded, in-memory transcription (replaces MVP 12's Celery job)

The MVP 12 brief planned a lease-fenced Celery transcription job. For a
spoken turn that adds queue latency and polling for a job whose only
durable output is text the client immediately sends on. Phase 6
transcribes **in the request thread**: an utterance is capped at
`VOICE_MAX_UTTERANCE_SECONDS` (30 → ≤ 960 KB of 16 kHz PCM16, D3), a
local faster-whisper `small`/`medium` model transcribes that in ~1–3 s on
an M-series Mac, and the Phase 4 daily stack sizes gunicorn gthread for
48 concurrent slots (Phase 4 R4b). Voice adds blocking requests to that
pool (STT 1–3 s per utterance, TTS per sentence), bounded by one owner
and the `voice_stt`/`voice_tts` throttles; branch 3's done-when re-runs
the Phase 4 R4b load check with voice requests mixed into the SSE and
polling load rather than assuming the headroom. The upload cap sits below Django's
2.5 MB memory threshold and is enforced by the `_MaxSizeUploadHandler`
pattern (moved from `knowledge/views.py:152` to `core/uploads.py`, both
callers use it), so audio **never touches disk**: it lives in request
memory, is passed as bytes to the gateway, and is dropped when the
request ends. This is strictly tighter than §O's "bounded temp file,
deleted on completion" — nothing to delete, nothing a crash can leave
behind — so no recovery beat and no job row are needed. The endpoint
records no audit row per utterance: the `ModelUsage` row is the record
(size, duration, latency, provider), and it never holds audio or text.

### D3 — One wire format: client-encoded 16 kHz mono PCM16 WAV

`MediaRecorder` output differs by browser (WebM/Opus in Chromium, MP4/AAC
in Safari) and would push container decoding into the speech server. The
wake-word pipeline (D6) already needs raw 16 kHz mono PCM from an
`AudioWorklet`, so the client encodes that PCM as a WAV itself (a ~40-line
encoder). The server validates with stdlib `wave` over `BytesIO`: RIFF/
WAVE, PCM, 16-bit, mono, 16 000 Hz, frame count > 0 and ≤ the duration
cap, and the declared `data` size must match the bytes actually read
(`nframes × 2` equals the PCM payload length; trailing or missing bytes
are rejected), so `audio_seconds` in `ModelUsage` is derived from audio
that was really received, not from a client-controlled header alone —
anything else is `415 voice.unsupported_audio` before any model
call. `audio_seconds` is computed from the header by the adapter (D4), not
taken from the speech server. TTS output is validated the same way
(any PCM16 WAV rate, ≤ `VOICE_TTS_MAX_SECONDS`), so the browser only ever
receives a checked `audio/wav`.

### D4 — Local speech server behind the existing `openai_compatible` provider type

- **Adapter.** `OpenAICompatibleAdapter` gains `transcribe()`
  (`client.audio.transcriptions.create(model=..., file=("utterance.wav",
  bytes, "audio/wav"), language=params.get("language"),
  response_format="json")`) and `synthesize()`
  (`client.audio.speech.create(model=..., voice=params["voice"],
  input=text, response_format="wav")`), errors mapped through the existing
  `_wrap_error`. The `openai` SDK is already the adapter's dependency, so
  no new provider SDK and no new `ProviderType`. `transcribe()` and the
  base contract gain `params=None` (language), mirroring `synthesize()`.
- **Server (step 0 spike, branch 1).** Preferred: *speaches* (formerly
  faster-whisper-server), which serves faster-whisper at
  `/v1/audio/transcriptions` and Piper voices at `/v1/audio/speech`, run
  natively on the Mac like Ollama. The spike records in this brief before
  code: current project name/licence/release, whether Piper (not only
  Kokoro) is still served, the exact model ids for a Turkish Piper voice
  (e.g. `tr_TR-dfki-medium`) and a Whisper model, the WAV response shape,
  and cold-start/warm latency on the owner's Mac. Fallback if it fails:
  `deploy/voice/` — a ~150-line FastAPI sidecar wrapping `faster-whisper`
  and `piper-tts` with the same two endpoints, versioned in this repo and
  run on the host (never inside the Django image).
- **Seeds and env.** `seed_ai_defaults` adds `fake-stt-local`/
  `fake-tts-local` (priority 0, `AI_FAKE_PROVIDER`-gated, for e2e,
  **seeded `personal_data_allowed=True`** — D5 filters every audio call,
  so an unflagged fake would turn every happy-path test into
  `NoProviderAvailable`; the fail-closed tests use a separate,
  deliberately disallowed stub provider, never the same fakes, and a
  test pins that the field's model default stays False) and,
  when `VOICE_API_KEY` is set (placeholder, as `OLLAMA_API_KEY`),
  `voice-stt-local`/`voice-tts-local` (`openai_compatible`, `base_url`
  `VOICE_BASE_URL` default `http://host.docker.internal:8000/v1`,
  `VOICE_STT_MODEL_ID`, `VOICE_TTS_MODEL_ID`, `personal_data_allowed=True`).
  Voice/language knobs (`VOICE_TTS_VOICE`, `VOICE_STT_LANGUAGE` default
  `tr`, the caps) are settings passed as `params` by the `voice` app.
  `.env.example` and `compose.yml` gain a commented block beside Ollama's.
- **Guard.** `ai_core/tests/test_sdk_guard.py` gains a second pattern:
  `faster_whisper`, `ctranslate2`, `piper`, `onnxruntime`, `whisper` are
  never imported anywhere in `backend/` — speech engines are out of
  process by construction.

### D5 — Audio calls are personal data, always; enforced in the gateway

A voice recording is biometric and may carry third parties' voices. So
`_run_audio` (`gateway.py:358`) keeps only providers with
`personal_data_allowed=True` for STT **and** TTS, unconditionally — no
kwarg can relax it — and fails closed with `NoProviderAvailable` and a
`ModelUsage` row `routing_reason="personal_data_required"`,
`error_type="no_provider"` (Phase 5 D5's shape). TTS is included because
it speaks reply text that may come from a personal connector read (Phase
5 D5) and because a later "cloud voice" row must be a deliberate
`set_provider_personal_data` act, not a priority accident. The
transcript, once text, is treated exactly like typed text: the
conversation's own `private` / `personal_data` flags govern the chat call
as today; voice neither sets nor clears them. `context_report` for audio
rows adds `input_bytes`, `language`, `input_mode` — never audio, never
text. Purposes `voice.stt`/`voice.tts` join `UNTIERED_PURPOSES` (Phase 5
D11): capability, not tier, selects audio providers.

### D6 — Wake word in the browser; nothing leaves the device before it fires

- **Engine: openWakeWord ONNX models via `onnxruntime-web`** (the one new
  frontend runtime dependency, WASM backend). openWakeWord's keyword
  heads are small per-phrase models (a custom-trained `hey_friday` one
  here — see the 2026-11-20 note under branch 4); its pipeline (melspectrogram →
  shared embedding → keyword head) is three small ONNX graphs, ported to
  TypeScript in-house and run in a Worker fed by an `AudioWorklet`
  (16 kHz mono frames). Rejected: **Porcupine** (needs a Picovoice
  AccessKey in the browser plus online licence validation — a secret and a
  network dependency in a local-first path; proprietary models); the
  **Web Speech API** (`SpeechRecognition` streams audio to the browser
  vendor's cloud in Chromium — exactly what ADR-008 forbids); server-side
  detection (pre-wake audio would leave the device).
- **Models are self-hosted and pinned.** `make voice-models` downloads the
  four ONNX files (three openWakeWord graphs + Silero VAD) from pinned
  release URLs, verifies sha256, and places them in
  `frontend/public/voice-models/` (gitignored — the pre-trained openWakeWord
  models carry a non-commercial licence, R3, and are not redistributed in
  this repo). The client refuses to arm if a model's hash differs from
  `lib/voice/model-manifest.ts`. No CDN: a test asserts every model URL is
  same-origin.
- **The privacy invariant, made mechanical.** Pre-wake audio exists only
  in the Worker's ring buffer (≤ 2 s, overwritten continuously). On
  detection the capture state machine starts a **new** utterance buffer at
  the first frame *after* the detection frame — no pre-roll, so not even
  the wake phrase itself is uploaded. End of utterance: Silero VAD (MIT,
  same ORT session infrastructure) silence ≥ 800 ms, or the 30 s cap, or
  no speech within 5 s of the wake (→ back to armed, nothing uploaded).
- **Arming is explicit and visible.** Push-to-talk is the default mode.
  Wake mode is armed only from a user gesture (also required by browser
  autoplay/`AudioContext` policy), per tab, never restored on page load,
  and disarmed on logout, device revocation (next 401) and after
  `VOICE_WAKE_IDLE_MINUTES` (default 60, client setting) without a
  detection. While the mic is open the shell shows a persistent indicator
  and the tab title is prefixed "● Dinliyor". Wake mode lives in a
  shell-level `VoiceProvider` (`app/(shell)/layout.tsx`), so navigating
  between pages keeps it armed.
- **Secure context.** `getUserMedia` needs HTTPS or `localhost`; LAN use
  goes through the Phase 4 Q6 topology (Tailscale/TLS proxy). Production
  headers (`lib/security-headers.ts`, Phase 4 branch 1) gain
  `Permissions-Policy: microphone=(self), camera=(), geolocation=()`. The
  current CSP is `frame-ancestors` only; if a `script-src` is ever added it
  must include `'wasm-unsafe-eval'` (noted in that file).
- **WASM threading.** Default: the single-threaded SIMD build of
  `onnxruntime-web` (`numThreads=1`), which needs no cross-origin
  isolation. Multi-threaded WASM would require `Cross-Origin-Opener-
  Policy: same-origin` + `Cross-Origin-Embedder-Policy: require-corp`,
  which can break cross-origin loads (fonts, any third-party asset); it
  is adopted only if the branch-4 spike shows single-threaded misses the
  CPU target, and then with COOP/COEP added to the header list and a
  cross-origin fallout check recorded in the spike.

### D7 — Speech out: sentence-chunked, client-driven, half-duplex

The reply streams as text (unchanged SSE). The client segments it at
sentence boundaries (`.`/`!`/`?`/newline, 20–300 chars, Turkish
abbreviations list), converts Markdown to speakable text (code blocks →
"kod ekranda", links → their label, tables skipped with "tablo ekranda"),
and synthesizes sequentially with one request in flight plus one
prefetched, so the first sentence plays while the rest still streams.
`POST /voice/speech` accepts free text (≤ `VOICE_TTS_MAX_CHARS`, 400)
rather than a message id because the assistant message is not persisted
until the stream ends (`assistant/services.py:458`); this is acceptable
for an owner-only, web-only, throttled, local-only endpoint whose worst
case is speaking arbitrary text to the requester. Playback is one
`AudioContext` with an `AnalyserNode` (feeds the orb, D10). **Half-duplex:**
wake detection and capture pause while Friday speaks (echo would
self-trigger); "Durdur" and `Escape` stop playback and abort pending
synthesis (`AbortController`). Barge-in by voice is out of scope (Q4).

### D8 — Voice never decides and never confirms

ADR-008 decision 3 requires an explicit, separate, audited owner decision
for gate-class actions and an explicit human confirm for confirm-class
rows. A recognized utterance is neither: STT errors, a TV, or a guest can
produce "evet, onayla". Therefore:

- no voice endpoint reaches `approvals`, `tasks`, `tools`, `agents`,
  `harness`, `connectors` or `deploys` (import-linter, D11);
- voice turns render `draft` events as the same cards ChatView does
  (`ChatView.tsx:266,274`); confirming a task draft or creating a tool
  request is a pointer/keyboard click, exactly as in chat;
- `ToolRequestCard` keeps `canDecide={false}` (`ToolRequestCard.tsx:74`);
  the decision stays on `/approvals/[requestId]` (Phase 4 branch 5), which
  the card deep-links. Friday may *say* "onay isteği ekranda" but never
  reads a decision back as done;
- nothing in the client maps a transcript to a click (a test scans
  `lib/voice/` and `components/voice/` for calls to the task-confirm,
  approval-create or decision helpers).

This answers Phase 5 Q7 (HA via voice) and Phase 4 Q4 (step-up) — see Q2,
Q3. ADR-011 records it so the rule does not erode by precedent.

### D9 — Channel, identity and provenance

Voice endpoints are `IsWebChannel` + owner (Phase 4 branch 2): a trusted
device's web session only. PATs (`cli`, `openai`), the facade and Telegram
are refused (403); the facade stays audio-free (Phase 4 out of scope).
`Message.Channel` is **not** extended — channel means trust, and voice
arrives over the web session. Instead `Message.input_mode`
(`text`|`voice`, default `text`) records provenance for display (a mic
badge in chat history) and analysis; it is client-asserted, never
trust-bearing, and forced to `text` for every non-web channel. Throttle
scopes: `voice_stt` (`20/min`) and `voice_tts` (`120/min`, sentence
chunking), Redis-backed like Phase 4's, failing closed.

### D10 — Orb on real signals first; the visual identity layer last

- **With the loop (branch 3):** `FridayOrbState` grows to `idle | armed |
  listening | transcribing | thinking | speaking | error`; `listening` is
  driven by mic RMS from the worklet, `speaking` by the TTS `AnalyserNode`
  — the synthetic jitter goes. Still the existing Canvas-2D component.
- *(Replaced 2026-11-20 by Amendment A8, HUD v1 in `feat/p6-hud`; kept
  for history.)* **Identity layer (branch 5), only after branches 3–4 are
  "done" on the Mac:** an in-house WebGL2 fragment-shader orb (no three.js/framer-motion,
  continuing MVP 12 §1's no-animation-dependency stance) with the Canvas-2D
  orb as fallback; colors from the Phase 4 brand token; a mini-orb in the
  shell header mirroring global voice state; a full-screen voice mode on
  `/voice` with live captions (transcript and reply) and inline draft
  cards; short earcons synthesized with WebAudio oscillators (no audio
  assets). Constraints: `prefers-reduced-motion` → static orb with a text
  state label; rendering pauses when the tab is hidden; ≤ 2 ms per frame on
  the owner's Mac; `aria-live` state announcements; captions always on.
  Explicitly not a face, avatar or 3D character.

### D11 — `voice` kernel app and contracts

`voice` may import `core`, `workspaces`, `ai_core` and
`assistant` (conversation lookup and `Message.InputMode` only).
Contracts: `voice` must not import `approvals`, `tasks`, `tools`,
`agents`, `skills`, `memory`, `knowledge`, `integrations`, `harness`,
`connectors`, `deploys`; no app imports `voice` (leaf, like `harness`);
`audit` is deliberately not in that list: D2 records no audit row per
utterance, and adding it later needs a named event. Separately, `voice`
is added to the `ai_core`/`audit` forbidden lists and to the
"no other app may import harness" source list (`backend/pyproject.toml`).

## ADR-011 (branch 1) — Voice transport

Records: (1) voice is an input/output transport onto the unchanged
conversation pipeline, web channel only; (2) wake-word detection runs in
the browser and no audio leaves the device before it fires — no pre-roll,
no cloud speech API, including the browser's Web Speech API; (3) raw
audio is never persisted and never written to disk by Friday (tightens
§O); (4) STT/TTS calls require `personal_data_allowed` providers,
enforced in the gateway; (5) a spoken utterance is never the explicit
decision of ADR-008 decision 3 nor a confirm-class confirmation — voice
cannot approve, reject, confirm or cancel; (6) the `voice` app and its
contracts. It moves nothing between gate-class and confirm-class and
amends ADR-007/§O (async-job plan and temp file superseded). ADR-002's
kernel-list note gains `voice`. (2026-11-20: ADR-011 cross-references
ADR-015, already Accepted, for the voice-turn capability cap and the mic
rules; it does not restate them. Point (4) reads "require
`personal_data_allowed` providers" — a cloud audio row qualifies only after
the owner's deliberate toggle, d2.)

## Amendment 2026-11-20 (Friday 3.0, owner-approved)

The owner accepted the Friday 3.0 roadmap
(`docs/research/2026-11-roadmap-proposal.md`) on 2026-11-20, including
decisions d2 (cloud audio is a per-provider owner toggle, local by default)
and d3 (push-to-talk / in-tab wake word until Phase 11's false-activation
budget). D1–D11 stand except where an item below says what it replaces.

### A1 — OpenRouter audio: spike, disabled rows, no new door

- **Spike (branch 1, step 0b, before code).** Against OpenRouter's current
  official documentation and one real call each (owner key, paid —
  owner step): `/api/v1/audio/transcriptions` and `/audio/speech` request
  and response shapes; multipart upload stability for a 30 s WAV; whether
  responses (streamed and non-streamed) carry `usage.cost`; which Turkish
  TTS voices exist; whether any real-time bidirectional audio endpoint
  exists (the proposal found none; **unverified**). Results are recorded
  in this brief before code. If cost is not in the response, a Celery task
  corrects the `ModelUsage` cost from the generation lookup (the row is
  written first with the configured price estimate, ADR-003 amendment).
- **Rows.** `seed_ai_defaults` adds `openrouter-stt`/`openrouter-tts`
  (`openai_compatible`, `base_url` `https://openrouter.ai/api/v1`,
  `api_key_env_var="OPENROUTER_API_KEY"`) seeded **`enabled=False` and
  `personal_data_allowed=False`** — D5's gateway filter therefore never
  selects them until the owner turns both on with
  `set_provider_personal_data` (d2). The text `openrouter` provider row is
  seeded by the Phase 5 closeout (Friday 3.0 table, order 1). `.env.example`
  gains the key **name** only.
- **No second door.** OpenRouter is one more provider row behind the
  existing `OpenAICompatibleAdapter` (D4); no LiteLLM/OmniRoute proxy
  (MASTER_PLAN §O, 2026-11-20).

### A2 — TTS routing by language; the Turkish fallback is chosen by measurement

`voice.tts` calls pass `params={"language": ..., "voice": ...}`; a
settings map `VOICE_TTS_ROUTES` (language → provider name + voice id,
default `tr` → `voice-tts-local` / Piper Turkish, `en` → the same server's
English voice) picks the provider row for the turn, still only among
`personal_data_allowed` rows. If the Piper Turkish voice scores below 4/5
in the owner's listening test (branch 3a), `tr` routes to `openrouter-tts`
— which still needs the owner's d2 toggle; without it Friday keeps Piper
and records the gap. The client never chooses the provider.

### A3 — Gateway audio caps (ADR-003 amendment 2026-11-20)

Branch 1 adds the budget primitive to `ai_core`: per-purpose daily budget,
per-session cap and monthly cap, checked **before** provider selection;
refusal → `BudgetExceeded`, one `ModelUsage` row
`error_type="budget_exceeded"`, cost 0; the voice endpoints map it to `429
voice.budget_exceeded` with a Turkish message. Defaults: audio monthly cap
20 USD (`AI_BUDGET_AUDIO_MONTHLY_USD`), per voice session 1 USD, owner-
editable; local providers cost 0 and never trip it. Audio cost for priced
providers comes from the response or the configured per-second /
per-character price (`_estimate_cost` gains an audio branch; today it is
token-priced, `gateway.py`). A coverage test asserts every audio purpose
that has a non-zero-cost provider row has a budget. Runaway guards from
ADR-015: a voice session closes after 30 s of silence; more than 3
activations in 60 s without a dispatched turn auto-mutes.

### A4 — Turkish evaluation set and replay harness (new branch 3a)

- **Set (owner step):** about 200 owner-recorded utterances — roughly 100
  Turkish, 50 code-switched ("repo'yu aç", "PR'ı merge et", "deploy'u
  durdur") and 50 English, including firm and project names, recorded in a
  quiet room and with room noise (TV, fan) — plus 2 h of negative audio
  (Turkish and English TV, podcasts, calls) for wake-word false accepts.
  Stored **outside the repo** on the owner's Mac (biometric data; D2's
  "audio never persisted by Friday" is about the product, the eval set is
  the owner's own file set) with a manifest (file name, sha256, reference
  transcript, condition) that *is* versioned: `docs/voice/eval-manifest.md`
  holds only names, hashes and reference text.
- **Harness:** `manage.py voice_eval --set <dir>` (WER per condition via a
  small in-repo edit-distance scorer; Turkish normalisation of case,
  punctuation and `İ/ı`) and a recorded-audio **replay harness** that feeds
  golden WAVs through the pipeline with fakes. CI runs the harness on a
  handful of synthetic fixture WAVs (tones/silence plus fake STT
  responses) — never on the owner's recordings and never with a real
  model.
- **Exit targets (phase closeout; initial targets, re-settable after the
  first measurement, any change recorded here):** WER ≤ 12% Turkish quiet,
  ≤ 18% Turkish with noise, ≤ 15% code-switched; Turkish TTS rated ≥ 4/5
  by the owner for intelligibility and naturalness; wake word (branch 4,
  in-tab) FRR ≤ 10% and FAR ≤ 0.5 per hour on the negative set.

### A5 — Latency budget and the per-turn trace

Target, end of owner speech → first audio byte, local path: **p50 ≤ 1.5 s,
p95 ≤ 2.5 s** (phase exit; branch 3's ≤ 3 s / ≤ 6 s rule stays the
per-branch ship rule).

| Stage | Budget (p50) |
|---|---|
| End-of-turn detection (VAD / finish window) | 300 ms |
| STT (local Whisper, ≤ 8 s utterance) | 350 ms |
| Gateway LLM, time to first sentence | 550 ms |
| First-sentence TTS | 250 ms |
| Transport + playback start | 50 ms |

One **trace id** (client-generated UUID, sent as `X-Friday-Trace` on the
transcription, message and speech requests) is stored in the
`context_report` of each `ModelUsage` row of the turn. The client reports
its own stage timings (end-of-turn, playback start) with the last speech
request. **Amends D2:** the "no voice table" sentence becomes "no voice
table holds audio or text" — branch 2 adds `voice.VoiceTurnTrace`
(`WorkspaceScopedModel`: trace id, conversation, stage timings in ms,
`input_mode`, created_at; never audio, transcript or reply text; pruned
after 30 days). The nightly replay run fails if p50 regresses more than
20% from the stored baseline.

### A6 — Voice-turn safety (ADR-015 decisions 1–5)

- **Capability cap, server-side.** A turn posted with
  `input_mode="voice"` may run read tools and produce drafts/proposals
  only; the messages view refuses to execute any confirm-class or
  gate-class path for it (today no chat turn executes without a click;
  the cap makes that an asserted invariant per turn, so a later
  auto-execution feature cannot apply to voice by accident). `input_mode`
  stays client-asserted (D9): the cap protects the honest voice path
  against room audio, it adds no trust to a typed turn.
- **Voice routes that write become proposals.** "Hatırla ki …" creates a
  memory **proposal** (`pending_approval`, shown as a card the owner
  approves by click) through the existing memory API from the client —
  never through the `voice` app, which may not import `memory` (D11) —
  and never an approved row; branch 3 adds the minimal memory-service
  path for an owner-originated pending row if the current create path
  cannot express it. "Bu ne kadar tuttu?" is a read.
- **Mic indicator and fail-closed mute.** D6's indicator stands; a mute
  toggle in the shell is checked inside the capture path and inside the
  single speak function; if the mute state cannot be read or the SSE/turn
  connection drops, capture stops and the mic track is released.
- **Self-trigger rule (browser, half-duplex).** D7's pause of wake
  detection during playback stands; additionally a detection within 1.5 s
  after playback ends whose transcript matches recent TTS text is
  discarded. A CI replay test plays a fake-TTS "Hey Friday, terminali aç"
  clip through the harness with a mocked detector and asserts no upload
  and no proposal; the real-model version runs nightly.
- **Wake storms:** > 3 activations in 60 s without a dispatched turn →
  auto-mute and a notice.

### A7 — MASTER_PLAN "Roadmap update 2026-11-20" voice additions, placed

- Branch 3: end-of-speech **finish window** `VOICE_FINISH_MS` (default
  900 ms; a mid-sentence pause appends instead of dispatching); short
  **interrupt words** ("dur", "bekle", "stop") bypass the buffer and stop
  playback; a **mute flag** checked inside the single speak function (tests
  never hear audio); deterministic client-side **voice routes** checked
  before the brain (instant UI commands only — never approvals, D8);
  **"a yes is not a done"**: a spoken confirmation only references an
  action whose receipt (task id, approval id, execution status) exists;
  voice routes "hatırla ki …" (A6) and "bu ne kadar tuttu?" (ModelUsage
  receipt).
- Branch 4: D6's VAD silence window for wake mode aligns with
  `VOICE_FINISH_MS`.
- Closeout: `manage.py preflight` gains voice checks — STT/TTS round trip
  on a fixture WAV through the gateway (`ops.preflight`), provider rows and
  budget configuration present, a `?mute=1` voice tab is silent.

### A8 — HUD v1 replaces the visual-identity branch (branch 5, `feat/p6-hud`)

Replaces D10's second bullet. A full-screen `/hud` route in Next.js, driven
by one event stream (SSE) of voice state, approvals, tasks, cost and
health. **Orb:** React Three Fiber + a GLSL shader, seeded from the
ElevenLabs UI Orb component (MIT; licence recorded); this supersedes
D10's "no three.js" stance **for the `/hud` route only** — the three.js
bundle is code-split and dynamically imported there, the Canvas-2D orb
stays the fallback (WebGL unavailable, reduced motion) and the rest of the
app stays dependency-free. **States:** idle · armed · listening ·
thinking · tool-running · awaiting-approval · speaking · error · muted ·
disconnected · offline-local (D10's `transcribing` is shown as
*thinking* with a "yazıya dökülüyor" caption; *disconnected* in Phase 6
means the HUD event stream or the turn connection is lost; Phase 9 adds the
node link). **Panels:** transcript and captions, today's
tasks, approvals (deep links to `/approvals/[requestId]`, no decide
button on the HUD, D8), live cost (today's `ModelUsage`), and a **health
panel** (mic state, provider status, last preflight result, budgets used;
node link added in Phase 9). A sessions panel slot is left for Phase 8A.
**Accessibility and budget:** `prefers-reduced-motion` → static orb with
a colour state; captions always available; every state also shown as
text with `aria-live`; frame cap 30 fps active, 5 fps idle, 0 when the
tab is hidden; GPU under 5% on the owner's Apple-silicon laptop when idle
(measured in the manual Mac pass); on battery no always-on animation
unless the owner turns it on. Every HUD read endpoint goes through
`for_actor()`. Desktop WKWebView overlay: "later" list.

### A9 — Phase exit criteria (closeout)

A4 targets met or consciously re-set (recorded here); A5 local p50 ≤ 1.5 s
or re-set with the dominant stage named; A3 caps enforced (tests + one
manual refusal on the Mac); A6 release gates green; preflight green.

### Owner steps (pending) — recorded 2026-11-20

1. Decide at phase start which cloud audio rows may be enabled and
   confirm the monthly audio cap (d2).
2. Run the A1 spike with the owner's OpenRouter key (paid calls) and
   record the results here.
3. Record the A4 evaluation set and the negative audio on the Mac; rate
   the Turkish TTS voice.
4. Train the custom `hey_friday` model offline (branch 4 note).
5. The manual Mac passes: latency (A5), GPU/battery (A8), reduced motion.

### Branch 1 — spike results and implementation notes (2026-11-20)

**Step 0b — OpenRouter audio (A1), from OpenRouter's official docs only.**
Verified by web search of `openrouter.ai` on 2026-11-20 (the docs pages
themselves could not be fetched from the build sandbox; no real call was
made — paid calls are an owner step):

| Item | Finding | Consequence in code |
|---|---|---|
| STT endpoint | `POST /api/v1/audio/transcriptions` **exists**. JSON body with base64 `input_audio`, and it also accepts OpenAI-style `multipart/form-data` (OpenAI SDKs work against the OpenRouter base URL). Params: `model` (e.g. `openai/whisper-1`), `language`, `temperature`, `response_format` `json`/`verbose_json` (`text`/`srt`/`vtt` → 400). ~60 s upstream processing timeout; no audio URLs. | One code path: the adapter's multipart `transcribe()`; a 30 s WAV is far below the timeout. |
| STT usage/cost | Response `{text, usage}`; `usage` carries `seconds`, token counts for token-priced models, and `cost` (USD). | `usage.cost` → `reported_cost` → `ModelUsage.estimated_cost` (`cost_source="provider"`). |
| TTS endpoint | `POST /api/v1/audio/speech` **exists**, OpenAI-Audio-Speech compatible (`model`, `input`, `voice`). Returns raw audio bytes, not JSON. `response_format` is `mp3` or `pcm` only (default `pcm`; some models support only one); **no `wav`**. PCM is 16-bit LE; rate is model-dependent and reported in the `Content-Type` (e.g. `audio/pcm;rate=24000;channels=1`). | `openrouter-tts` uses `response_format="pcm"` (`VOICE_TTS_PROVIDER_PARAMS`); the adapter wraps PCM into a checked WAV (D3 holds: the browser only ever gets `audio/wav`). |
| TTS cost | No cost/usage in the response; `X-Generation-Id` header present. | Row written with the configured per-character price; `ai_core.tasks.correct_usage_cost` replaces it from `GET /api/v1/generation?id=` (`total_cost`). |
| Turkish TTS voices | **Unverified** — voice ids are model-specific; no Turkish list found. | Owner step (below). |
| Realtime bidirectional audio | None found in the docs (still **unverified**, as in the proposal). | Nothing built; realtime stays on the "later" list. |

**Step 0 — speech server (D4).** speaches (formerly faster-whisper-server)
is current and still lists **both Piper and Kokoro** for TTS
(project listing updated 2026-04); `/v1/audio/transcriptions` and
`/v1/audio/speech` are its OpenAI-compatible surface. Not verifiable from
the sandbox and therefore owner steps: the current release/licence, the
exact model ids for a Turkish Piper voice and a Whisper model as speaches
names them, the WAV response shape (the adapter also accepts a
streaming-style WAV header with an unknown length and rebuilds it from the
received PCM), and cold/warm latency on the Mac. Seed defaults
(`Systran/faster-whisper-small`, `speaches-ai/piper-tr_TR-dfki-medium`,
voice `tr_TR-dfki-medium`) are placeholders until then — all env-overridable.
The `deploy/voice/` fallback sidecar is **not** built: nothing so far says
speaches fails.

**Implementation notes.**
- `ai_core/audio.py`: stdlib-`wave` validation (RIFF size = bytes received,
  `data` fully present, no trailing bytes, PCM16, rate/channels, frames > 0,
  duration cap), `pcm_to_wav`, `silent_wav` (the fake TTS now returns a
  real silent WAV).
- Audio entry points no longer accept `personal_data` at all (D5: no kwarg
  can relax it; passing one is a `TypeError`, tested).
- Budgets (A3): `ai_core.AIBudget` (purposes, daily/monthly/session
  limits, owner-editable in the admin); the seeded `audio` row covers
  `voice.stt`/`voice.tts` (20 USD/month, 1 USD/session). Checked in every
  entry point before provider selection. A call is refused when some
  eligible candidate is priced and the window is spent or the call's
  worst-case estimate (dearest candidate × seconds or characters) would
  cross it; a session window applies to calls passing `budget_session`
  (branch 2 passes the voice session id). Chat/embedding calls have no
  pre-call estimate, so a chat budget refuses only once spent. Known
  residual: two concurrent calls can overshoot a cap by one call's cost.
- `ModelProvider` gains `price_per_audio_second` and `price_per_mchar`
  (migration `ai_core/0007`). OpenRouter seed prices are placeholders
  (whisper-1 0.006 USD/min; TTS 15 USD per 1M chars) corrected per call.
- `set_provider_personal_data` gains `--activate`/`--deactivate` so the d2
  toggle (flag + `is_active`) is one audited act.
- `VOICE_TTS_ROUTES` (A2) lives in settings; a routed provider that is not
  eligible is skipped, never forced; a route's voice id goes only to the
  routed provider.

**Owner steps (pending) — `feat/p6-speech-gateway`, recorded 2026-11-20.**
1. Step 0 on the Mac: install speaches, confirm release/licence and that
   Piper is served, record the exact STT/TTS model ids and voice ids, the
   WAV response shape and cold/warm latency here; set `VOICE_*` in `.env`.
2. Step 0b with the owner's OpenRouter key (paid): one real STT call (30 s
   WAV, multipart) and one TTS call; confirm `usage.cost` on STT, the
   PCM `Content-Type` rate on TTS, that `/generation?id=` returns
   `total_cost` for an audio generation, and which voices speak Turkish
   acceptably; correct the seeded OpenRouter prices/model ids if needed.
3. Done-when on the Mac: `InferenceGateway().transcribe/synthesize`
   round-trips a Turkish sentence through the local server with one
   `ModelUsage` each (runbook §3).
4. Confirm the monthly audio cap (d2; 20 USD default) in the admin.

## Scope (one branch each)

### 1. `feat/p6-speech-gateway` — ADR-011, adapter, personal-data rule, seeds

**Step 0 — speech-server spike** (D4), recorded in this brief before code.
**Scope.** ADR-011; `OpenAICompatibleAdapter.transcribe/synthesize`;
`params=` on `transcribe` (base, fake, gateway); WAV validation helpers
(`ai_core/audio.py`: parse/validate, duration) used by the adapter for
`audio_seconds` and output checks; `_run_audio` personal-data filter;
seeds and env (D4); `UNTIERED_PURPOSES` gains `voice.stt`/`voice.tts`;
SDK-guard extension; `docs/runbooks/voice-setup.md` (install/run the
speech server, pull models, env, verify with `manage.py shell`).
**Files.** `docs/adr/ADR-011-voice-transport.md`,
`docs/adr/ADR-002-modular-monolith.md` (note), `backend/ai_core/{gateway,
audio}.py`, `ai_core/adapters/{base,openai_compat,fake}.py`,
`ai_core/management/commands/seed_ai_defaults.py`,
`ai_core/tests/`, `config/settings/base.py`, `.env.example`,
`deploy/docker/compose.yml`, `docs/runbooks/`.
**Tests.** Adapter against `httpx.MockTransport` (the `openai` client's
`http_client` seam): request shape (multipart file, model, language,
`response_format`), error mapping, `audio_seconds` from the header not
the response, non-WAV/oversized TTS output rejected; gateway: a
cloud-only STT/TTS provider set → `NoProviderAvailable`, one
`ModelUsage` with `personal_data_required`, adapter call count 0; an
allowed provider selected even at lower priority than a disallowed one;
`context_report` has no text/audio keys; seeds idempotent with
`VOICE_API_KEY` set/unset; fake STT/TTS seeded; SDK guard.
**Done when:** ADR-011 accepted; on the Mac, `InferenceGateway().
transcribe/synthesize` round-trips a Turkish sentence through the local
server with one `ModelUsage` each.
**Amendment additions (2026-11-20).** Step 0b OpenRouter spike (A1);
`openrouter-stt`/`openrouter-tts` rows seeded disabled and disallowed;
`VOICE_TTS_ROUTES` (A2); the budget primitive, audio cost estimation and
`BudgetExceeded` (A3, ADR-003 amendment); `.env.example` names
(`OPENROUTER_API_KEY`, `AI_BUDGET_AUDIO_MONTHLY_USD`, per-session cap).
Extra files: `ai_core/{budgets,models}.py` + migration. Extra tests: the
OpenRouter rows are never selected while disabled or disallowed (adapter
call count 0); a call over the daily/session/monthly cap is refused before
provider selection with one `budget_exceeded` row; local cost-0 calls never
trip a cap; cost taken from the response when present, else from the
configured price; the audio-budget coverage test; TTS routed by language
only among allowed rows (all against `httpx.MockTransport`/FakeAdapter, no
real OpenRouter call).
**Known follow-ups (branch 1 review, LOW, unresolved at merge).**
- `ai_core/adapters/openai_compat.py`: `synthesize()` buffers the whole TTS
  response body (`raw.http_response.content`) before the
  `VOICE_TTS_MAX_SECONDS` check, so a misbehaving speech server or provider
  could force a large in-memory read (owner-configured server, output bounded
  after buffering, so the risk is small). Fix: optionally cap bytes before
  parsing (e.g. `max_seconds * 96000 * 2 + 1 KiB` for 96 kHz stereo) by
  streaming or checking `Content-Length` first.
- `ai_core/gateway.py`: `budget_session` is caller-supplied and stored in
  `context_report` (truncated to 64 chars). Branch 2 must derive it from a
  server-held voice session id (or conversation id), never from a request
  field, so a client cannot dodge the per-session cap with a fresh value per
  call; add a test for that.
- `ai_core/gateway.py`: with both OpenRouter TTS and local TTS enabled, a
  local-provider failure falls back to OpenRouter with the personal text and no
  per-call notice. Allowed by D2 and recorded in the `ModelUsage` attempts
  list, but it is a silent cloud fallback. Fix: document it in
  `docs/runbooks/voice-setup.md`, or add a setting disabling cross-provider
  fallback for audio.
- This brief, step 0b: the OpenRouter spike rests on web search only (docs
  pages could not be fetched); endpoint shapes, pcm/mp3 formats and the
  `usage.cost` field are unverified against a live call, and seeded model ids
  and prices are placeholders. Keep owner steps 1–4 open until done; both
  OpenRouter rows stay disabled and disallowed by default meanwhile.

### 2. `feat/p6-voice-api` — `voice` app, endpoints, `input_mode`

**Scope.** App + contracts (D11); `core/uploads.py` (moved size guard,
knowledge updated); `POST /api/v1/voice/transcriptions` (multipart,
`IsWebChannel`, conversation via `for_actor()`, WAV validation → 415,
over-cap → 413 before parsing completes, `NoProviderAvailable` → 503
`voice.no_local_provider`, gateway error → 502 `voice.stt_failed`);
`POST /api/v1/voice/speech` (JSON, same auth, ≤ 400 chars, returns
`audio/wav`); throttles (D9); `Message.input_mode` + migration +
`MessageCreateSerializer` field (forced `text` off-web) +
`MessageSerializer` exposure; error codes in the Phase 4 catalog; BFF
routes `app/api/voice/{transcriptions,speech}/route.ts`
(`rejectCrossOrigin` + `rejectNonMultipart`/`rejectNonJson`, streaming
the WAV back unmodified).
**Files.** `backend/voice/` (apps, views, serializers, services, urls,
tests), `backend/core/uploads.py`, `backend/knowledge/views.py`,
`backend/assistant/{models,serializers}.py` + migration,
`backend/pyproject.toml`, `config/settings/base.py`, `config/urls.py`,
`frontend/src/app/api/voice/`, `frontend/src/lib/i18n/tr.ts`.
**Tests.** Role × endpoint against the five Phase 4 principals (owner web
session 200; owner `cli`/`openai` PAT 403; non-owner 403; `is_agent` 403;
anonymous 401); another workspace's conversation → 404, no gateway call;
chunked upload over cap aborted with no temp file created (assert
`TemporaryFileUploadHandler` never instantiated) and no gateway call;
stereo / 44.1 kHz / MP3 / truncated header → 415; `ModelUsage.workspace`
= the conversation's; the transcript is never written to any DB row or
log by the voice app (captured log + DB scan); `input_mode` forced `text`
for a PAT turn; throttle 429 and Redis-down 503; BFF CSRF checks.
**Done when:** both endpoints pass the matrix; the knowledge upload suite
is unchanged after the move.
**Amendment additions (2026-11-20).** `X-Friday-Trace` accepted on the
voice and messages endpoints and written to each turn's `ModelUsage`
`context_report`; `voice.VoiceTurnTrace` (timings only) + migration +
30-day prune beat (A5); the ADR-015 capability cap on `input_mode="voice"`
turns (A6); `BudgetExceeded` → `429 voice.budget_exceeded`. Extra tests:
the trace row holds no text/audio keys (model field allow-list test) and is
workspace-scoped through `for_actor()`; a voice turn cannot reach any
execute path (registry scan + an end-to-end turn with a write-capable
intent yields only a draft); a malformed trace header is ignored, not
stored.

**Branch 2 — implementation notes (2026-11-20).**
- `voice` app (`backend/voice/`): `TranscriptionCreateView` /
  `SpeechCreateView` (`IsAuthenticated` + `IsWebChannel` + `IsOwner`).
  Authentication classes are injected by `config/urls.py`
  (`voice.urls.build_urlpatterns`, the deploys pattern) so a `cli` PAT gets
  403 `channel_forbidden` and an `openai` PAT 403 `token_kind_forbidden`
  without `voice` importing `accounts` (D11).
- Upload (D2): a pre-parse `Content-Length` check, then only
  `core.uploads.MaxSizeUploadHandler` + `MemoryFileUploadHandler` are
  installed — `TemporaryFileUploadHandler` is never instantiated (tested).
  Cap = `VOICE_MAX_UTTERANCE_SECONDS` × 32 000 B + 1 KiB header allowance.
  The size guard moved from `knowledge/views.py` to `core/uploads.py`; the
  knowledge suite is unchanged apart from the import.
- WAV validation reuses branch 1's `ai_core.audio.parse_stt_input`
  (`too_long` → 413, every other reason → 415); TTS output is re-checked with
  `normalize_tts_wav` (invalid → 502 `voice.tts_failed`). `audio_seconds` in
  the response is the received audio's duration.
- Error codes `voice.{unsupported_audio,upload_too_large,no_local_provider,
  stt_failed,tts_failed,budget_exceeded}` in `core/error_codes.py` + Turkish
  catalog. `BudgetExceeded` → 429 is checked before `NoProviderAvailable` →
  503; any other `GatewayError` → 502.
- **Budget session (branch-1 follow-up, resolved):** `budget_session` is
  `voice:<conversation id>`, derived server-side; a request field of that
  name is ignored (tested). Consequence: the 1 USD "per voice session" cap is
  per conversation until branch 3 introduces a server-held voice session id.
- `Message.input_mode` (`text`|`voice`, migration `assistant/0011`), on the
  user row only; `MessageCreateSerializer.input_mode`; forced `text` for any
  non-web channel; exposed by `MessageSerializer`.
- ADR-015 cap (A6): `assistant.services.apply_voice_capability_cap` runs right
  after `classify_intent` for a voice turn. `INTENT_EFFECTS` maps every
  `Message.Intent` to `read` or `proposal` (a coverage test requires every
  intent to be listed); anything else degrades to chat, and a
  `connector_read` must name a registered `ReadToolSpec`. Registry scan test:
  every proposable `ToolSpec` proposed from a voice turn yields only a `draft`
  event — `create_execution`/`dispatch` never called, no `ApprovalRequest`.
- Trace (A5): `core.tracing.request_trace_id` (UUID only, else ignored) on the
  messages, transcription and speech requests; `trace_id` + `input_mode` go
  into the `context_report` of the turn's `router.intent`,
  `router.task_draft`, `router.connector_args`, `chat`, `voice.stt` and
  `voice.tts` rows. `voice.VoiceTurnTrace` holds integer stage timings only
  (`stt_ms`, `tts_first_ms` server-side; `end_of_turn_ms`,
  `llm_first_sentence_ms`, `playback_start_ms`, `total_ms` from the speech
  request's `timings`), unique per workspace + trace id; field allow-list
  test; daily prune beat `voice.tasks.prune_voice_turn_traces_task`
  (`VOICE_TRACE_RETENTION_DAYS`, 30).
- Throttles `voice_stt` 20/min, `voice_tts` 120/min (env-overridable),
  fail-closed 503 on a cache outage (tested).
- BFF: `app/api/voice/{transcriptions,speech}/route.ts` (origin + content-type
  checks, a 2.5 MB byte cap before `formData()`, the WAV streamed back
  unmodified, a non-WAV 200 never passed on as audio); `X-Friday-Trace`
  forwarded (UUID only) by these and the messages route.
- Import-linter: `voice` may import only `core`, `workspaces`, `ai_core`,
  `assistant` (direct imports; `allow_indirect_imports`), nothing imports
  `voice`; `voice` added to the `core`, `ai_core`, `audit`, config, harness
  and MCP contracts. `audit` is not in voice's forbidden list (D11) and is
  not imported.

**Owner steps (pending) — `feat/p6-voice-api`, recorded 2026-11-20.**
1. On the Mac with the local speech server (branch-1 owner step 1): one real
   transcription and one speech request through the BFF from the trusted
   browser session (runbook §4), confirming 200s, one `ModelUsage` each with
   workspace = the conversation's and cost 0.
2. The Phase 4 R4b load check with voice STT/TTS mixed in stays branch 3's
   done-when; the two blocking endpoints it exercises exist from this branch.

**Known follow-ups (branch 2 review, LOW, unresolved at merge).**
- `voice/services.py`: `record_trace` catches only `IntegrityError`. A DB
  `OperationalError` during the trace upsert would surface as a 500 on an
  otherwise successful STT/TTS call, although the docstring says it never
  raises and a trace must not fail the voice request. Fix (optional): catch
  `DatabaseError` (or `Exception`, logging content-free) around the atomic
  block.
- `voice/services.py`: the 1 USD per-voice-session cap is keyed on
  `voice:<conversation id>`, so it is really per conversation until branch 3
  adds a server-held voice session id (a known consequence recorded above).
  No action in branch 2; branch 3 introduces the server-held session id.
- This brief, owner steps: the real-Mac STT/TTS check and the load check are
  still open. Review confirmed no code path stores the transcript, but the
  real-provider check has not been run; keep the owner steps open until the
  live check is done.

### 3. `feat/p6-push-to-talk` — the full loop, push-to-talk

**Scope.** `lib/voice/`: `capture-worklet.ts` (16 kHz mono downsampling,
RMS), `wav.ts` (encoder), `segmenter.ts` (sentences + speakable Markdown),
`playback.ts` (queue, prefetch, abort, `AnalyserNode`),
`voice-machine.ts` (state machine: idle → listening → transcribing →
thinking → speaking → idle | error); `VoiceView` mic button (hold or
toggle; `Space` hold on desktop), transcript shown and auto-sent with
`input_mode="voice"` (editable before send when "Göndermeden önce
göster" is on — Q5), draft cards rendered from `draft` events, orb states
on real levels (D10 first bullet), a mic badge on voice turns in chat
history; "Sesli" back in the grouped nav; mic-permission and
no-local-provider states explained in Turkish.
**Files.** `frontend/src/lib/voice/`, `components/voice/`,
`components/chat/MessageList.tsx` (badge), `components/shell/`,
`lib/i18n/tr.ts`.
**Tests.** Vitest with mocked `getUserMedia`/`AudioContext`/worklet port:
WAV header bytes; segmenter golden cases (abbreviations, decimals, code
fences, streaming boundaries); state machine transitions incl. abort
mid-transcription and mid-playback; draft cards appear and nothing is
auto-confirmed; the D8 scan test; playback aborts pending synth requests.
**Done when:** on the Mac with local models, push-to-talk → spoken
Turkish answer works end to end; median end-of-speech →
first-audio latency recorded in the brief (target ≤ 3 s with Whisper
`small`, `llama3.1:8b`, Piper medium — a target, not a gate: a miss up
to 6 s ships with the measurement and the dominant stage recorded in
the brief; above 6 s the branch is held and the model sizes revisited);
the Phase 4 R4b load check re-run with voice STT/TTS requests mixed in
(no SSE stream starved, no 503 from the pool); a task drafted by voice
is created only after its card is clicked.
**Amendment additions (2026-11-20).** The A7 items (finish window,
interrupt words, mute flag inside the speak function, voice routes, "a
yes is not a done", "hatırla ki" as a proposal, "bu ne kadar tuttu?");
fail-closed mute (A6); client trace id and stage timings (A5); a
`budget_exceeded` state explained in Turkish. Extra tests: a pause shorter
than `VOICE_FINISH_MS` appends instead of dispatching; an interrupt word
stops playback without a request; with mute set the speak function makes
no playback call; mute-state read failure releases the mic track; a
spoken "evet" never produces a confirmation without a receipt id; voice
routes never call the decision helpers (extends the D8 scan).

**Branch 3 — implementation notes (2026-11-20).**
- `frontend/src/lib/voice/`: `capture-worklet.ts` (self-contained
  AudioWorklet source: mono mix, 16 kHz downsampling, RMS per frame, a flush
  of the partial frame on stop; tests evaluate that exact string), `capture.ts`
  (`getUserMedia` with echo cancellation → worklet → PCM16 chunks; the mic
  track is released on every exit; the 30 s cap; mic errors mapped to
  `mic_denied` / `mic_missing` / `mic_insecure` / `mic_failed`), `wav.ts`
  (44-byte RIFF header, data size = bytes sent), `segmenter.ts` (streaming
  sentence segmentation, 20–300 chars, Turkish/English abbreviations,
  initials, decimals, never a cut at the end of a chunk; code fences → "Kod
  ekranda.", tables → "Tablo ekranda.", links → label, half-streamed fences/
  links held back), `playback.ts` (`Speaker`: one synthesis request in flight
  + one prefetched, one `AudioContext` + `AnalyserNode`, `stop()` aborts
  every pending request), `voice-machine.ts` (pure reducer: idle → listening
  → transcribing → finishing → thinking → speaking → idle | error),
  `controller.ts` (the loop, framework-free, driven with fakes in tests),
  `routes.ts`, `receipts.ts`, `mute.ts`, `trace.ts`, `api.ts`, `config.ts`.
- **Finish window (A7):** `NEXT_PUBLIC_VOICE_FINISH_MS` (900). The
  `finishing` phase holds the transcript; a press within the window appends
  the next piece to the same turn (same trace id), otherwise the buffer is
  routed/dispatched when the window elapses.
- **Interrupt words (A7):** `dur`, `durdur`, `bekle`, `sus`, `stop`, `yeter`
  (whole utterance only). They bypass the finish window and the buffer: the
  buffered utterance is discarded, playback and pending synthesis stop, and
  no message or speech request is made. Detection is on the transcript (the
  transcription request has already happened; "without a request" means no
  turn and no TTS). A mic press while Friday speaks also stops playback
  (half-duplex, D7; barge-in *by voice* stays out of scope, Q4).
- **Mute (A6/A7):** `lib/voice/mute.ts` — localStorage flag
  `friday:voice:muted`, `?mute=1` forces it, an unreadable state reads as
  muted (`readable: false`). Checked inside the capture path (before the mic
  opens and on every frame: muted/unreadable → audio discarded, track
  released) and inside `Speaker.speak` — the single speak function — plus once
  more before each sentence plays. Shell toggle `components/shell/
  VoiceMuteToggle.tsx` in the sidebar footer; `sessize al` sets it by voice.
- **Voice routes before the brain (A7):** `routeTranscript()` — interrupt,
  `sessize al`, `hatırla ki` / `unutma ki` / `hatırla:` (explicit forms
  only; "hatırla bakalım …" goes to the brain), `bu ne kadar tuttu?` (and
  variants), and every spoken yes/approve/reject/cancel word → a fixed reply
  ("Sesle onay veremem…") and nothing else.
- **"Hatırla ki" (A6):** the client posts to the existing `POST
  /api/memories` with `proposal: true`. Branch 3 added that minimal path:
  `MemoryCreateSerializer.proposal` → `create_memory(proposal=True)` births a
  `user_created` row as `pending_approval` (audit `memory.created` with
  `proposal: true`, ids/statuses only). The flag can only weaken a row; a
  pending row is never embedded or used as context until approved (tested).
  `voice` (backend) does not import `memory` (D11 unchanged).
- **"Bu ne kadar tuttu?" (A7):** new read-only `GET /api/v1/voice/receipt?
  conversation=&trace=` in the `voice` app (owner web session only, the five
  principals tested, conversation through `for_actor()`, throttle
  `voice_receipt` 30/min): sums the turn's `ModelUsage` rows by
  `context_report.trace_id` within the conversation's workspace (calls,
  failed calls, tokens, audio seconds, estimated cost, purposes, `local_only`)
  plus today's workspace total — numbers only, no model call, no write. BFF
  `app/api/voice/receipt/route.ts` forwards only the two UUIDs. The client
  asks for the previous spoken turn's trace.
- **Receipts-only confirmations (A7):** `spokenConfirmation()` returns a
  sentence only for a receipt with a non-empty id (task id, approval-request
  id, memory id, execution id + status). VoiceView calls
  `controller.announce()` from the draft cards' `onConfirmed(task)` /
  `onCreated(request)` callbacks — i.e. after the click created the row. A
  created approval request is announced as *created*, never as decided.
- **Voice-turn safety (ADR-015):** a voice turn reads (receipt) and proposes
  (memory proposal, the chat turn's draft cards) only. Drafts render as the
  same `TaskDraftCard` / `ToolRequestCard` (`canDecide` stays false);
  `lib/voice/d8-scan.test.ts` scans every non-test file in `lib/voice/` and
  `components/voice/` for task/approval endpoints, decision/confirm routes,
  memory transitions, programmatic clicks, synthetic input events and
  decision helpers, and pins that the only voice write is the memory proposal.
  The server-side cap from branch 2 still applies to every `input_mode=voice`
  turn.
- **Fail-closed (A6):** a dropped turn connection → error state
  "Bağlantı kesildi; güvenlik için mikrofon kapatıldı", any capture cancelled;
  mute read failure → capture refused/stopped and the track released.
- **Trace (A5):** one UUID per spoken turn on the transcription, message and
  every speech request; client timings (`end_of_turn_ms` = finish-window
  wait, `llm_first_sentence_ms`, `playback_start_ms` = release → first audio,
  `total_ms` = release → last sentence requested) sent with each speech
  request (the server keeps the latest). Typed turns on `/voice` carry no
  trace and are not spoken.
- UI: `VoiceView` mic button (hold ≥ 350 ms = push-to-talk, a short click =
  toggle, Space hold, keyboard activation toggles), Durdur/Escape, captions
  (heard + reply) always on, `aria-live` phase text, tab title "● Dinliyor"
  while the mic is open, "Göndermeden önce göster" (per device), Turkish
  explanations for mic permission/missing/insecure context,
  `voice.no_local_provider`, `voice.budget_exceeded`, mute; `FridayOrb`
  states `idle | armed | listening | transcribing | thinking | speaking |
  error` on real levels (mic RMS / AnalyserNode; synthetic jitter removed;
  reduced motion draws one static frame per state); mic badge (`Sesli`) on
  voice user turns in chat history; "Sesli" back in the "Çalışma" nav group.
- Not built here (by scope): the server-held voice session id for the A3
  per-session budget (still `voice:<conversation id>`; VoiceView starts one
  conversation per page visit/workspace, so the cap is per voice page
  session in practice), the ADR-015 30 s-silence session close and wake-storm
  auto-mute (branch 4, wake mode), the self-trigger rule (branch 4).

**Owner steps (pending) — `feat/p6-push-to-talk`, recorded 2026-11-20.**
1. Done-when on the Mac with local models (Whisper `small`, `llama3.1:8b`,
   Piper medium): push-to-talk → spoken Turkish answer end to end in Chromium
   and Safari; record the median end-of-speech → first-audio latency
   (`VoiceTurnTrace.playback_start_ms`, runbook §5) here with the dominant
   stage (target ≤ 3 s; ship-with-note ≤ 6 s; hold above).
2. Re-run the Phase 4 R4b load check with voice STT/TTS requests mixed into
   the SSE and polling load (no SSE stream starved, no 503 from the pool).
3. On the Mac: a task drafted by voice is created only after its card is
   clicked; "hatırla ki …" shows a pending proposal on Hafıza; "bu ne kadar
   tuttu?" reads a plausible receipt; the shell mute and a `?mute=1` tab are
   silent; echo cancellation on laptop speakers acceptable (R6).
4. Listen to the segmenter on real replies (abbreviations, numbers, code)
   and note any Turkish abbreviation missing from `segmenter.ts`.

### 3a. `feat/p6-voice-eval` — Turkish evaluation set, replay harness, latency baseline

**Scope.** A4 and A5's measurement side: `docs/voice/eval-manifest.md`
(names, hashes, reference transcripts — no audio in the repo);
`manage.py voice_eval` (WER per condition, Turkish normalisation, report
written to stdout and a JSON file outside the repo; every STT/TTS call goes
through the gateway with its own purposes `voice.eval_stt`/`voice.eval_tts`
— untiered like `voice.stt`/`voice.tts`, budgeted under A3, one
`ModelUsage` each, the owner's personal workspace); the replay harness
(golden WAVs → capture/segment/STT/TTS ports with fakes, timing capture);
the self-trigger fixture clip (the test itself lands with branch 4,
which owns the detector); the TTS listening-test sheet; the stored latency
baseline and the nightly regression check (> 20% p50 → fail).
**Files.** `backend/voice/eval/` (scorer, command, replay harness),
`backend/voice/tests/`, `frontend/src/lib/voice/` (replay entry for
vitest), `frontend/test/fixtures/voice/` (synthetic WAVs only),
`docs/voice/eval-manifest.md`, `docs/runbooks/voice-setup.md` (how to
record and run the set). **Tests.** Scorer golden cases (insertions,
deletions, substitutions, Turkish casing `İ/i`, `I/ı`, punctuation);
manifest hash check fails on a changed file; the replay harness runs in CI
with fakes only; the regression check trips on a
synthetic 25% slowdown. **Done when:** the owner's set is recorded and
measured once on the Mac (owner step); results and the TTS choice (Piper
or OpenRouter, A2) recorded in this brief; targets confirmed or re-set
with reasons.

### 4. `feat/p6-wake-word` — local "Hey Friday"

**Step 0 — wake-word spike**, recorded before code: openWakeWord model
licence and download URLs/hashes; feature-pipeline parity of the TS port
against Python openWakeWord on fixture WAVs (score delta ≤ 0.02); CPU
cost of the always-on Worker on the owner's Mac (target < 5% of one
core) in Chromium and Safari with the single-threaded build (D6 "WASM
threading"); if parity or CPU is marginal, the DSP port lands as its own
branch `feat/p6-wake-word-dsp` before the state-machine wiring
(pre-authorized split); false-accept count over a 1-hour room/TV
recording at the chosen threshold.
**Scope.** `onnxruntime-web`; `make voice-models` + model manifest;
`wakeword-worker.ts` (openWakeWord pipeline), `vad.ts` (Silero), capture
state machine additions (armed → detected → listening, no pre-roll,
no-speech timeout); shell-level `VoiceProvider` + indicator + tab-title
prefix + idle disarm; half-duplex pause during playback;
`Permissions-Policy` header; threshold in a settings panel (client-side,
per device).
**Files.** `frontend/package.json`, `frontend/src/lib/voice/`,
`app/(shell)/layout.tsx`, `components/voice/`, `lib/security-headers.ts`,
`Makefile`, `frontend/.gitignore`, `docs/runbooks/voice-setup.md`
(model download via `make voice-models`, hash check, browser support).
**Tests.** With a mocked detector: no request to `/api/voice/*` while
armed without a detection (fetch spy over a simulated hour of frames);
the uploaded buffer starts at detection frame + 1 (sample-index
assertion); no-speech timeout uploads nothing; arming requires a gesture
and is not restored after reload; disarm on 401; detection ignored while
speaking; model hash mismatch → refuses to arm; model URLs same-origin.
Nightly/manual (real models, not CI): the parity test and a Playwright
run with Chromium's fake audio capture playing a "hey friday" fixture.
**Done when:** on the Mac, "Hey Friday, yarın saat 10'da diş hekimi"
yields a spoken reply and a task draft card, with the network panel
showing no voice request before the wake word.
**Amendment additions (2026-11-20).** Self-trigger discard rule and wake
storm auto-mute (A6); VAD window aligned with `VOICE_FINISH_MS` (A7); the
spike's false-accept measurement uses the A4 negative set (target FAR ≤ 0.5
per hour, FRR ≤ 10% — in-tab only; always-on targets belong to Phase 11).
Extra tests: a detection within 1.5 s after playback with a TTS-matching
transcript is dropped; four detections in 60 s without a dispatched turn
mute and show the notice; the A6 self-trigger replay test (branch 3a's
fixture clip, mocked detector, CI) asserts no upload and no proposal.

> **Note (2026-11-20, rename Jarvis → Friday):** openWakeWord ships a
> pre-trained `hey_jarvis` model but no "hey friday" model, so branch 4
> needs a custom-trained `hey_friday` openWakeWord keyword model. Training
> it with openWakeWord's synthetic-data pipeline (generated TTS clips plus
> negative data, then the small keyword head on top of the shared
> melspectrogram/embedding graphs) is an **owner step**, done offline
> before step 0; the resulting ONNX file is pinned by sha256 like the
> others. The wake phrase/model stays configurable via the model manifest
> (`lib/voice/model-manifest.ts`), so swapping or retraining it is a
> manifest change, not a code change. The rest of the design (D6, the
> spike, the tests, the privacy invariant) is unchanged; the spike's
> false-accept measurement runs against the custom model.

### 4a. `feat/p6-wake-word-dsp` — conditional, only if the branch-4 spike is marginal

Triggered only when step 0 of branch 4 records parity delta > 0.02 or CPU
≥ 5% of one core. **Scope.** Port the openWakeWord feature pipeline
(melspectrogram → embedding → keyword head) and the Silero VAD wrapper to
TypeScript in isolation, in a Worker, with no capture state machine, UI
or header changes. **Files.** `frontend/src/lib/voice/wakeword/`,
`frontend/src/lib/voice/vad.ts`, fixture WAVs under `frontend/test/`.
**Tests.** Unit tests of each stage on fixture frames (CI, mocked ONNX
session); the Python-parity test and the CPU measurement run
nightly/manual with real models. **Done when:** the parity test passes
standalone (delta ≤ 0.02) and the CPU target holds; branch 4 then
narrows to wiring (state machine, arming, indicator, headers) on top.

### 5. `feat/p6-hud` — HUD v1 (replaces `feat/p6-visual-identity`, 2026-11-20)

Starts only after branches 3 and 4 meet "done when" on the Mac (ADR-008
decision 5 — the HUD is still the visual layer that follows working
voice). **Scope.** A8: the `/hud` route, R3F/GLSL orb (code-split,
dynamically imported) with the Canvas-2D fallback, the eleven states, the
panels (captions, tasks, approvals as deep links, live cost, health,
sessions slot), the shell mini-orb mirroring global voice state, earcons
synthesized with WebAudio oscillators, reduced motion, frame caps,
hidden-tab stop, battery rule, `aria-live`; one backend SSE endpoint
`GET /api/v1/hud/events` (web channel, owner, every queryset through
`for_actor()`; voice state is client-side, the stream carries approval,
task, cost and health deltas) and its BFF route. The endpoint lives in a
new **leaf read-only app `hud`** — not in `voice`, which may not import
`approvals` or `tasks` (D11): `hud` may import `core`, `workspaces`,
`ai_core` (ModelUsage and provider reads only, no gateway call),
`approvals` and `tasks`; it writes nothing; no app imports `hud`
(import-linter contracts in `backend/pyproject.toml`, ADR-002 kernel-list
note). The ElevenLabs UI Orb seed and the `three`/`@react-three/fiber`
pins with licence notes. **Files.** `frontend/package.json`,
`frontend/src/app/(shell)/hud/`, `frontend/src/components/hud/`,
`components/voice/` (orb fallback, state), `components/shell/`,
`app/globals.css` (HUD tokens only), `frontend/src/app/api/hud/`,
`backend/hud/` (apps, views, tests), `backend/pyproject.toml`,
`config/settings/base.py`, `config/urls.py`,
`docs/adr/ADR-002-modular-monolith.md` (note), `lib/i18n/tr.ts`.
**Tests.** State → uniform/colour mapping for all eleven states;
reduced-motion path renders no animation frame loop; WebGL unavailable →
Canvas fallback; hidden tab stops `requestAnimationFrame`; frame cap
respected (fake timers); the three.js bundle is not part of any non-HUD
route chunk (build-manifest check); every state has a text label and an
`aria-live` announcement; no decide control on the HUD (D8 scan extended);
caption token contrast (Phase 4 contrast test); the SSE endpoint's role ×
endpoint matrix (five Phase 4 principals) and another workspace's
approvals never appear; health panel renders provider-down and
budget-exhausted states. Manual Mac pass: idle GPU < 5%, frame time,
battery behaviour.
**Done when:** every state is visually and textually distinct, at phone
width too; the reduced-motion and GPU passes are recorded in this brief.

### 6. closeout

MASTER_PLAN §AA row 6 → done, §Z and §O updated (async job and temp file
superseded by ADR-011); `docs/prompts/mvp-12-voice/brief.md` closed;
ADR-007 note and ADR-008 implementation note (decision 5 landed);
`docs/integrations/README.md` voice section; Phase 4 Q4 and Phase 5 Q7
marked answered; e2e (CI, fake providers): `/voice` push-to-talk with a
stubbed capture → transcript → reply → `/api/voice/speech` called per
sentence → task draft card → confirm click → task exists.
**Amendment additions (2026-11-20).** A9 exit criteria recorded (A4/A5
measurements or re-set targets, caps enforced, A6 gates green); preflight
voice checks (A7); MASTER_PLAN §AA "Friday 3.0 phases" row 6 → done;
ADR-015 implementation note (decisions 1–3 landed); e2e adds `/hud`
showing the voice state and a pending approval deep link.

## Branch table

| # | Branch | Scope | Depends on |
|---|---|---|---|
| 1 | `feat/p6-speech-gateway` | Spikes (speech server, OpenRouter audio), ADR-011, adapter STT/TTS, WAV checks, personal-data rule for audio, OpenRouter rows (disabled), TTS language routing, gateway audio budgets (ADR-003 amendment), seeds/env, SDK guard, runbook | Phase 5 closeout on `main` |
| 2 | `feat/p6-voice-api` | `voice` app, transcription/speech endpoints, `core/uploads.py`, `Message.input_mode`, throttles, BFF routes, trace id + `VoiceTurnTrace`, ADR-015 capability cap | 1 |
| 3 | `feat/p6-push-to-talk` | Capture, WAV, segmenter, playback, state machine, drafts in voice, orb on real levels, nav, finish window, interrupt words, mute flag, voice routes, fail-closed mute | 2 |
| 3a | `feat/p6-voice-eval` | Turkish eval manifest, `voice_eval` scorer, replay harness, self-trigger fixture, latency baseline + nightly regression, TTS choice | 3 (owner recording in parallel) |
| 4a | `feat/p6-wake-word-dsp` (conditional) | TS port of the openWakeWord/Silero pipeline in isolation, parity + CPU | branch-4 spike marginal; 3 |
| 4 | `feat/p6-wake-word` | Spike, `onnxruntime-web`, openWakeWord + Silero, arming/indicator, half-duplex, headers, self-trigger rule, wake-storm mute | 3 (spike may run in parallel with 1); 4a if triggered; 3a for the FAR/FRR measurement |
| 5 | `feat/p6-hud` (replaces `feat/p6-visual-identity`) | HUD v1: `/hud`, R3F orb + Canvas fallback, eleven states, panels incl. health, `hud` leaf app + SSE, accessibility and frame/GPU budget | 3, 4 done on the Mac |
| 6 | closeout | Docs, ADR notes (ADR-011, ADR-015), exit criteria A9, preflight voice checks, e2e | 1–5 (incl. 3a) |

Minimum usable cut if the phase runs long: 1, 2, 3 (push-to-talk voice);
3a is needed for the phase exit criteria, 5 for the Friday 3.0 vertical
slice.

| # | Status |
|---|---|
| amendment (`docs/friday-3-roadmap`) | **implemented 2026-11-20** (docs only; awaiting Sonnet 5 plan review of the amendment) |
| 1 `feat/p6-speech-gateway` | **implemented 2026-11-20** (cut from the local integration branch; spike results and owner steps under "Branch 1 — spike results and implementation notes"; awaiting the Sonnet 5 branch review) |
| 2 `feat/p6-voice-api` | **implemented 2026-11-20** (cut from the local integration branch; notes and owner steps under "Branch 2 — implementation notes"; awaiting the Sonnet 5 branch review) |
| 3 `feat/p6-push-to-talk` | **implemented 2026-11-20** (cut from the local integration branch; notes and owner steps under "Branch 3 — implementation notes"; awaiting the Sonnet 5 branch review) |
| 3a–6 | not started |

## Out of scope (explicit)

Any audio provider without `personal_data_allowed` (2026-11-20: cloud
audio rows such as OpenRouter exist but are seeded disabled and
disallowed; using one is the owner's deliberate d2 toggle, never a
default) · cloud realtime / live voice (OpenAI Realtime, Gemini Live —
"later" list, gateway relay only) · a desktop overlay for the HUD
("later") · the Web Speech API · server-side or pre-roll wake detection · voice
approvals, confirmations, rejections or cancels (D8) · confirm-class for
HA or any domain via voice (Q2) · step-up re-auth (Q3) · barge-in by
voice / full duplex (Q4) · speaker identification or voice biometrics ·
persisted recordings, audio in memories or the vault · voice over
Telegram, CLI or the facade · streaming STT (partial transcripts) ·
languages beyond Turkish and English · in-app wake-word training (the
one `hey_friday` model is trained offline by the owner, branch 4 note) ·
native/mobile apps or an OS-level always-on daemon (browser tab only) ·
avatar, face or 3D character · the carried-forward kernel items
(consolidation, owner core block, runner registry, SSE log streaming).

## Test strategy

Same discipline as Phases 2–5 (CLAUDE.md rules 5, 7, 8): ruff, pytest,
vitest, import-linter (new `voice` contracts) and `makemigrations
--check` green per branch. No test touches a real speech model or LLM:
FakeAdapter for gateway paths, `httpx.MockTransport` for the adapter,
mocked `getUserMedia`/`AudioContext`/worklet/Worker and a mocked
wake-word detector in vitest; real models only in the manual Mac passes
and the nightly parity/Playwright runs. Role × endpoint for both voice
endpoints against the five Phase 4 principals; isolation via another
workspace's conversation. **Release-gate regressions** (escalate per the
CRITICAL/HIGH rule): any network request carrying audio before a wake
detection or outside push-to-talk; audio written to disk, a DB row, a log
or a `ModelUsage`; an STT/TTS call reaching a provider without
`personal_data_allowed`; a voice path that confirms a draft or decides an
approval; a voice endpoint reachable by a PAT, the facade or Telegram;
model files loaded cross-origin or with a mismatched hash.
**Amendment additions (2026-11-20):** the recorded-audio replay harness
runs in CI with fakes (A4) and with real models only nightly; the owner's
evaluation recordings never enter the repo or CI. Added release-gate
regressions: a voice-originated turn that executes a confirm-class or
gate-class action (ADR-015); a voice write route that produces an
approved row; an audio call that bypasses or exceeds a configured budget
without a `budget_exceeded` row; a disabled or disallowed cloud audio row
selected; text or audio in `VoiceTurnTrace`; the HUD exposing a decide
control or another workspace's rows; a mute-state read failure that keeps
the mic open.

## Risks

- **R1 — False wake accepts.** A TV or conversation triggers "Hey Friday"
  and ≤ 30 s of room audio goes to the local speech server. Bounded: the
  audio stays on the owner's hardware, is never stored, and the worst
  outcome is a chat turn (no side effect without a click, D8); the
  spike measures the false-accept rate and the threshold is tunable.
  Residual: that stray transcript becomes a chat message and an
  extraction candidate (Q6).
- **R2 — Always-on mic in a browser tab.** Background-tab throttling may
  starve the worklet; Safari's `AudioWorklet`/WASM performance differs.
  Spike measures both; the indicator and title prefix keep the state
  visible; push-to-talk always works.
- **R3 — Licences.** openWakeWord's pre-trained models are published
  under a non-commercial licence (to be confirmed in the branch-4 spike):
  fine for a single-owner personal system, a blocker if Friday is ever
  productized — hence downloaded, not committed, and a named item in
  ADR-011's consequences. Piper voices and Whisper weights carry their own
  licences, recorded in the runbook.
- **R4 — Speech-server drift.** The preferred server is young and has
  renamed itself before; D4's fallback sidecar keeps the adapter contract
  (two OpenAI-compatible endpoints) stable either way.
- **R5 — Latency.** Whisper + LLM + Piper on one Mac may exceed 3 s;
  sentence-chunked TTS hides most of it; model sizes are env knobs;
  branch 3's ship-with-note (≤ 6 s) / hold (> 6 s) rule decides;
  `llama3.1:8b` router JSON reliability is already a known risk (MVP 12
  brief) that voice inherits, not adds.
- **R6 — Echo and self-triggering.** Half-duplex plus
  `echoCancellation`; residual on laptop speakers at high volume.
- **R7 — Secure-context friction.** Mic needs HTTPS off `localhost`; the
  runbook points to the Phase 4 Q6 Tailscale/TLS recipe.
- **R8 — Scope creep in the HUD branch.** A8's explicit constraints and
  "done when"; no avatar work; desktop overlay stays "later".
- **R9 — Cloud audio cost and privacy (2026-11-20).** If Piper's Turkish
  fails, Turkish TTS goes to OpenRouter: reply text (not mic audio) leaves
  the device, and money is spent. Bounded by the d2 toggle, the A3 caps
  and the per-provider `personal_data_allowed` filter; STT stays local
  unless the owner also enables the STT row.
- **R10 — OpenRouter audio unverified.** Multipart stability and
  `usage.cost` presence are unconfirmed; the A1 spike decides, and the
  generation-lookup correction covers a missing cost.
- **R11 — Three.js weight and GPU.** R3F adds a large bundle and GPU load;
  code-split to `/hud`, frame caps, the Canvas fallback and the measured
  idle GPU budget bound it.
- **R12 — Eval set realism.** 200 utterances from one speaker in one room
  is small; targets are initial and re-settable (A4), and the set grows
  with real failures.

## Plan-review rounds (Sonnet 5, 2026-09-27)

Round 1 (REVISE), addressed:

| # | Finding | Resolution |
|---|---|---|
| H1 | D5's unconditional audio filter would make unflagged fake providers fail every happy-path test and invite loosening the gate | Fakes seeded `personal_data_allowed=True`; fail-closed tests use a separate disallowed stub; model default pinned False |
| M1 | Phase 4 R4b concurrency budget not revalidated for blocking voice requests | D2 notes the load; branch 3 done-when re-runs the R4b check with voice traffic |
| M2 | onnxruntime-web threading / COOP-COEP unaddressed | D6 "WASM threading": single-threaded SIMD default; threaded only via spike, then COOP/COEP + fallout check |
| M3 | WAV declared length not cross-checked | D3: declared `data` size must equal bytes read |
| L1 | Runbook missing from branch 4 files | Added |
| L2 | Latency target vs gate unclear | Branch 3: target ≤ 3 s, ship-with-note up to 6 s, hold above |
| L3 | `audit` import unused | Dropped from `voice`'s allowed imports |
| L4 | Branch 4 dense | Pre-authorized split `feat/p6-wake-word-dsp` if the spike is marginal |

Round 2: **APPROVE**. Folded in: the conditional branch 4a now has
scope/files/tests/done-when and a branch-table row (branch 5 and the
closeout depend on 4, which depends on 4a when triggered); R5
cross-references the latency rule; the D11 wording on `audit` clarified.

## Open questions (with recommended answers)

- **Q1 — Celery job or synchronous transcription?** Synchronous and
  in-memory (D2); revisit only if utterances longer than 30 s become a
  real need.
- **Q2 — Phase 5 Q7: direct HA control by voice?** No. Voice-originated
  HA calls are `tool_request` drafts like typed ones: clicked on screen,
  decided on `/approvals/[requestId]`. The friction is measured; relief
  needs its own ADR and would still require an on-screen decision.
- **Q3 — Phase 4 Q4: step-up PIN for decisions?** Still no. Voice adds no
  decision surface (D8), so the stolen-unlocked-device threat model is
  unchanged; revisit with WebAuthn.
- **Q4 — Barge-in?** Not in Phase 6: half-duplex with a stop button and
  `Escape`. Revisit after measuring echo cancellation on the owner's
  hardware.
- **Q5 — Auto-send transcripts?** Yes by default — sending a chat message
  has no side effect beyond a reply, and everything else needs a click. A
  per-device "Göndermeden önce göster" toggle shows the transcript for
  editing first.
- **Q6 — Do voice turns feed memory extraction?** Yes, like typed turns:
  conversation-derived memories stay `pending_approval` (ADR-005/ADR-008
  decision 4), so a stray wake-word transcript becomes at most a
  suggestion the owner rejects. The owner can still mark a conversation
  private.
- **Q7 — Language.** `VOICE_STT_LANGUAGE=tr` by default (auto-detect is
  unreliable on short utterances); English turns need the setting changed
  or `""` for auto-detect. Piper voice Turkish by default.
- **Q8 — One speech server for STT and TTS?** Yes if the spike confirms
  it (one host process, one `VOICE_BASE_URL`); separate `base_url`s per
  provider row remain possible without code changes.
