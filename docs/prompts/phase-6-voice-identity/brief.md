# Phase 6 — Voice (ex-MVP 12) and the visual identity layer

> Status: **Approved (2026-09-27) — Sonnet 5 plan-review round 2
> APPROVE after one REVISE round (see "Plan-review rounds"; Codex CLI is
> retired from the development workflow)**. Implementation waits for the
> precondition below.
> Parent decisions: ADR-001 (workspace axis), ADR-002 (import contracts),
> ADR-003 (single gateway door, one `ModelUsage` per call), ADR-004 +
> ADR-008 decision 3 (explicit, separate owner decision; gate-class vs
> confirm-class), ADR-005 (`private` contract; conversation-derived
> memories stay `pending_approval`), ADR-007 (voice promoted, local
> Ollama), ADR-008 decision 5 (local wake word, audio never leaves the
> device before it; local faster-whisper + Piper; visual identity after
> voice works), ADR-009 (Phase 4: device-trust web channel, no
> confirm-class growth by precedent), ADR-010 (Phase 5: personal-data
> provider rule), `docs/VISION_JARVIS_2.md` §6 rows 6 and 6+,
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

Every Jarvis exchange so far is typed and read. Phase 6 makes it spoken —
say "Hey Jarvis", speak, hear the answer — **entirely on the owner's own
hardware**: the wake word runs in the browser, speech-to-text
(faster-whisper) and text-to-speech (Piper) run on the owner's Mac next
to Ollama, and nothing about the turn itself changes: the transcript goes
through the same intent router, context builder, drafts and approval gate
as a typed message. Only after that loop works does the orb become the
animated "Jarvis" presence (ADR-008 decision 5: "a visual shell before
the pipeline works would be an empty costume"). Usable outcome
(MASTER_PLAN §AA): "The real Jarvis experience."

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
- **Frontend.** `components/voice/JarvisOrb.tsx` (Canvas 2D, states
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
  frontend runtime dependency, WASM backend). openWakeWord ships a
  pre-trained "hey jarvis" model; its pipeline (melspectrogram →
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
wake detection and capture pause while Jarvis speaks (echo would
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
  the card deep-links. Jarvis may *say* "onay isteği ekranda" but never
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

- **With the loop (branch 3):** `JarvisOrbState` grows to `idle | armed |
  listening | transcribing | thinking | speaking | error`; `listening` is
  driven by mic RMS from the worklet, `speaking` by the TTS `AnalyserNode`
  — the synthetic jitter goes. Still the existing Canvas-2D component.
- **Identity layer (branch 5), only after branches 3–4 are "done" on the
  Mac:** an in-house WebGL2 fragment-shader orb (no three.js/framer-motion,
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
audio is never persisted and never written to disk by Jarvis (tightens
§O); (4) STT/TTS calls require `personal_data_allowed` providers,
enforced in the gateway; (5) a spoken utterance is never the explicit
decision of ADR-008 decision 3 nor a confirm-class confirmation — voice
cannot approve, reject, confirm or cancel; (6) the `voice` app and its
contracts. It moves nothing between gate-class and confirm-class and
amends ADR-007/§O (async-job plan and temp file superseded). ADR-002's
kernel-list note gains `voice`.

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

### 4. `feat/p6-wake-word` — local "Hey Jarvis"

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
run with Chromium's fake audio capture playing a "hey jarvis" fixture.
**Done when:** on the Mac, "Hey Jarvis, yarın saat 10'da diş hekimi"
yields a spoken reply and a task draft card, with the network panel
showing no voice request before the wake word.

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

### 5. `feat/p6-visual-identity` — the animated Jarvis layer

Starts only after branches 3 and 4 meet "done when" on the Mac (ADR-008
decision 5). **Scope.** D10 second bullet: WebGL2 orb + Canvas fallback,
shell mini-orb, full-screen voice mode with captions and inline cards,
earcons, reduced motion, hidden-tab pause, `aria-live`.
**Files.** `frontend/src/components/voice/` (`orb/` renderer, shaders as
TS strings), `components/shell/`, `app/(shell)/voice/page.tsx`,
`app/globals.css` (voice tokens only).
**Tests.** State → uniform mapping; reduced-motion path renders no
animation frame loop; WebGL unavailable → Canvas fallback; hidden tab
stops `requestAnimationFrame`; token contrast for captions (Phase 4
contrast test extended); a frame-time check in the manual Mac pass.
**Done when:** every voice state is visually distinct, at phone width
too; the reduced-motion pass is recorded.

### 6. closeout

MASTER_PLAN §AA row 6 → done, §Z and §O updated (async job and temp file
superseded by ADR-011); `docs/prompts/mvp-12-voice/brief.md` closed;
ADR-007 note and ADR-008 implementation note (decision 5 landed);
`docs/integrations/README.md` voice section; Phase 4 Q4 and Phase 5 Q7
marked answered; e2e (CI, fake providers): `/voice` push-to-talk with a
stubbed capture → transcript → reply → `/api/voice/speech` called per
sentence → task draft card → confirm click → task exists.

## Branch table

| # | Branch | Scope | Depends on |
|---|---|---|---|
| 1 | `feat/p6-speech-gateway` | Spike, ADR-011, adapter STT/TTS, WAV checks, personal-data rule for audio, seeds/env, SDK guard, runbook | Phase 5 closeout on `main` |
| 2 | `feat/p6-voice-api` | `voice` app, transcription/speech endpoints, `core/uploads.py`, `Message.input_mode`, throttles, BFF routes | 1 |
| 3 | `feat/p6-push-to-talk` | Capture, WAV, segmenter, playback, state machine, drafts in voice, orb on real levels, nav | 2 |
| 4a | `feat/p6-wake-word-dsp` (conditional) | TS port of the openWakeWord/Silero pipeline in isolation, parity + CPU | branch-4 spike marginal; 3 |
| 4 | `feat/p6-wake-word` | Spike, `onnxruntime-web`, openWakeWord + Silero, arming/indicator, half-duplex, headers | 3 (spike may run in parallel with 1); 4a if triggered |
| 5 | `feat/p6-visual-identity` | WebGL orb, shell presence, voice mode, captions, earcons | 3, 4 done on the Mac |
| 6 | closeout | Docs, ADR notes, e2e | 1–5 |

Minimum usable cut if the phase runs long: 1, 2, 3 (push-to-talk voice).

## Out of scope (explicit)

Cloud STT/TTS or any non-`personal_data_allowed` audio provider · the
Web Speech API · server-side or pre-roll wake detection · voice
approvals, confirmations, rejections or cancels (D8) · confirm-class for
HA or any domain via voice (Q2) · step-up re-auth (Q3) · barge-in by
voice / full duplex (Q4) · speaker identification or voice biometrics ·
persisted recordings, audio in memories or the vault · voice over
Telegram, CLI or the facade · streaming STT (partial transcripts) ·
languages beyond Turkish and English · custom wake-word training ·
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

## Risks

- **R1 — False wake accepts.** A TV or conversation triggers "Hey Jarvis"
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
  fine for a single-owner personal system, a blocker if Jarvis is ever
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
- **R8 — Scope creep in the identity branch.** D10's explicit
  constraints and "done when"; no avatar work.

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
