# MVP 12 — Voice Assistant (foundation + vision)

> **Re-sequenced (2026-07-17, ADR-008):** the remaining branches of this
> brief now execute as **Phase 6** of the Friday 2.0 roadmap
> (MASTER_PLAN §AA) — after memory v2, the agent harness, the UI renewal
> and deploy/MCP. Everything shipped so far stands. Phase 6 adds two fixed
> design constraints on top of this brief: a **local wake word**
> ("Hey Friday", openWakeWord/Porcupine-class, audio never leaves the
> device) for always-listening operation, and the animated visual-identity
> layer coming only **after** the voice pipeline works.
>
> Status: **closed (2026-11-20)** — superseded and completed by Phase 6
> (`docs/prompts/phase-6-voice-identity/brief.md`, closeout 2026-11-20):
> this brief's branches 2b, 3 and 4 were replaced by Phase 6 branches 1–5
> (ADR-011 voice transport, ADR-015 voice-originated turns). Its async
> transcription job and temp file were superseded by ADR-011's synchronous,
> in-memory design. Nothing in this brief is pending.
>
> Earlier status: **dormant (2026-09-27)** — re-sequenced to Phase 6 by ADR-008
> decision 5; the foundation already shipped (below) stays in place. No
> branch of this brief is active until Phase 6 opens.
>
> Earlier status: **in progress (2026-07-17)** — roadmap slot created by ADR-007,
> promoting voice off MASTER_PLAN §L's unscoped "last" bucket. The two
> MASTER_PLAN §O open decisions are now **resolved**: STT/TTS runs **fully
> local** (Whisper-class STT + Piper-class TTS on the user's own machine),
> and raw audio uses a **bounded temp-file, deleted on completion** — never
> persisted (see §O). Progress against the branch plan below: branch 1
> (Ollama provider + Friday orb) and the Gateway STT/TTS capability
> foundation (part of branch 2's backend spine) have shipped; real mic
> capture, the local STT/TTS adapters, and audio playback remain. The
> parts that need real microphone/model hardware are built and verified on
> the user's Mac, not in the cloud sandbox (which has no mic and no local
> model).

## Goal and user value

Every reply Friday has ever given has been typed and read. The owner wants a
real spoken assistant — say something, hear it think, hear it answer — running
against a **locally-hosted model on their own hardware** (Ollama on a
MacBook Pro M5 today, a Mac Pro later) rather than a cloud API for the
"thinking" step, for both cost and privacy reasons consistent with why
personal-Friday and external workplace installations already keep separate infra
(ADR-006). Alongside the audio itself, the owner wants a visible on-screen
presence — an animated indicator that reacts to the assistant listening,
thinking, and speaking — because a voice assistant with no visual feedback
while it's silently working feels broken.

## Relationship to existing systems — the boundary this MVP draws

- **`ai_core` / `InferenceGateway`** (`backend/ai_core/gateway.py`): unchanged
  by §1. A local Ollama endpoint is added as a new `ModelProvider` row of the
  *existing* `provider_type=openai_compatible`, exactly like NVIDIA NIM
  (`a78b6de`) — zero adapter code, zero Gateway code. §2-§4 (real STT/TTS)
  would add a new `ModelProvider.Capability.STT`/`.TTS` and new
  `InferenceGateway.transcribe()`/`.synthesize()` methods mirroring
  `embed()`'s shape (own `_select_providers(capability=...)` call, own
  `ModelUsage`-recording path) — **this part shipped (2026-07-17,
  `4f34da2`)**: `Capability.STT`/`.TTS`, `transcribe()`/`synthesize()`,
  exactly-one-ModelUsage-per-call preserved. The cost-accounting question
  this paragraph originally left open was answered in that same pass:
  audio is duration-priced, recorded as `audio_seconds` in the usage row's
  `context_report` — no new DB columns, `_estimate_cost`'s token shape
  untouched.
- **`assistant`** (`backend/assistant/services.py`, `views.py`): completely
  unchanged. §1's `VoiceView` frontend component calls the *same*
  `POST /conversations/{id}/messages` SSE endpoint the existing `ChatView`
  already calls — voice is a new *input/output transport* onto an unchanged
  conversation pipeline, not a new intent or a new endpoint.
- **`memory`** (`backend/memory/jobs.py`, `tasks.py`): not touched by §1.
  §2-§4's async transcription job (upload audio → Celery job → transcript)
  would follow the exact lease-fenced `pending → running → succeeded/failed`
  pattern `memory.jobs`/`memory.tasks` already establish (claim token, lease
  expiry, `transaction.on_commit` dispatch, periodic recovery scan) — reuse
  the pattern, not the code (`ai_core` boundary rules mean no cross-app
  import of the job machinery itself; a new `voice` app would re-implement
  the same shape, as `knowledge`/`tools`/`agents`/`integrations` already do
  independently).
- **No binary-audio storage exists anywhere in this codebase.**
  `knowledge`'s file upload path (`knowledge/services.py`) decodes every
  upload to UTF-8 text — there is no blob-storage precedent to reuse. §2-§4
  needs a real decision: wire the dormant `MINIO_*` / `django-storages`
  settings (`config/settings/base.py`), or a bounded temp-file-then-delete
  pattern that never persists raw audio past the transcription job. Open
  (MASTER_PLAN §O). **Resolved (2026-09-27 note):** MASTER_PLAN §O settled
  on a bounded temp file, deleted on completion — raw audio is never
  persisted.
- **No realtime client→server transport exists.** SSE (`StreamingHttpResponse`
  in `assistant/views.py`) only streams server→client text. Live mic-audio
  ingestion (as opposed to upload-then-transcribe) would need a new
  transport — WebSocket via Django Channels (ASGI is configured but bare
  today) or chunked HTTP POST. §1 sidesteps this entirely: no mic capture
  ships in this pass.

## Scope

### 1. Local LLM foundation + Friday orb UI (ships with this brief)

- `backend/ai_core/management/commands/seed_ai_defaults.py`: an
  `ollama-local` `ModelProvider` (`openai_compatible`, gated on
  `OLLAMA_API_KEY` being set to any non-empty placeholder — Ollama itself
  doesn't check auth, but `BaseAdapter._api_key()` requires a non-empty env
  var to select the provider at request time), `base_url` defaulting to
  `http://host.docker.internal:11434/v1` (Docker Desktop → host machine),
  `model_id` defaulting to `llama3.1:8b`, both overridable. Priority 5 —
  preferred over cloud providers when configured, since local/free/private
  is the whole point.
- `.env.example` / `deploy/docker/compose.yml`: `OLLAMA_API_KEY`,
  `OLLAMA_MODEL_ID`, `OLLAMA_BASE_URL`, mirroring the NVIDIA block's
  placement and comment style. Empty by default — `make test-e2e`/CI
  unaffected, identical guarantee to every other opt-in provider here.
- `frontend/src/components/voice/FridayOrb.tsx`: dependency-free Canvas-2D
  component (no framer-motion/three.js — none exist in this frontend today
  and none are added), `state: "idle" | "thinking" | "speaking"`, styled off
  the existing OKLCH tokens in `globals.css` (`--sidebar-primary` as the one
  chromatic accent already defined, currently unused elsewhere).
- `frontend/src/components/voice/VoiceView.tsx` +
  `frontend/src/app/(shell)/voice/page.tsx` +
  `SidebarContent.tsx`'s `NAV_ITEMS`: a real (not mocked) integration — a
  text box sends a message through the same SSE plumbing `ChatView.tsx`
  already uses (`frontend/src/lib/sse.ts`'s `SSEStreamParser`), and the orb
  transitions idle → thinking (waiting for first chunk) → speaking
  (chunks arriving) → idle (`done`/`error`), driven by the same event types
  `ChatView.tsx` already handles. Mic button is present but disabled
  ("yakında" / "coming soon") — real capture is §2.

### 2. Gateway STT/TTS foundation (backend spine — **shipped**)

The single-door plumbing every voice call routes through (ADR-003), built
and unit-tested against the `FakeAdapter` with no real model or audio:
- `ModelProvider.Capability.STT` + `.TTS` (migration `0003`).
- `BaseAdapter.transcribe(audio, content_type, purpose)` /
  `.synthesize(text, params, purpose)` + `TranscribeAdapterResult` /
  `SynthesizeAdapterResult` dataclasses carrying `audio_seconds` (the real
  STT/TTS cost signal — this provider class is duration-priced, not
  token-priced). Default base implementations raise `not_supported`, so a
  provider only gains voice when its adapter overrides them.
- `InferenceGateway.transcribe()` / `.synthesize()` mirroring `embed()`'s
  non-streaming shape (own `_select_providers(capability=...)`, own
  retry/fallback, exactly one `ModelUsage` row per call — rule 3 — with
  `audio_seconds` recorded in `context_report`), factored through a shared
  `_run_audio()` helper.
- `FakeAdapter` STT/TTS fixtures (rule 5: mockable, tests never hit a real
  model).

### 2b. Speech-to-text capture + local adapter (remaining — Mac work)

Push-to-talk mic capture (`MediaRecorder`/`getUserMedia`) in `VoiceView`,
uploaded to a new `voice` kernel app, written to a **bounded temp file**,
transcribed asynchronously (Celery, lease-fenced per `memory.jobs`'s
pattern) through the Gateway's `transcribe()` against a **local**
Whisper-class provider, temp file deleted on completion, transcript handed
to the existing `assistant` chat-send flow as if typed. Needs a real mic +
local model — built/verified on the user's Mac, not the sandbox.

### 3. Text-to-speech local adapter + playback (remaining — Mac work)

The assistant's existing streamed text reply synthesized to audio through
the Gateway's `synthesize()` against a **local** Piper-class provider and
played back in `VoiceView`, driving the orb's "speaking" state off real
audio amplitude (Web Audio API `AnalyserNode`) instead of §1's synthetic
jitter. Needs a local model — Mac work.

### 4. Full loop polish (design only — not built this pass)

Push-to-talk → transcribe → existing chat pipeline → synthesize → playback,
end to end, with the orb's four states all driven by real signals. Includes
whatever UX affordances make a push-to-talk loop feel natural (recording
indicator, cancel-mid-record, error recovery when STT/TTS/Gateway fails).

## Out of scope (explicit)

- ~~Wake-word detection ("Hey Friday") — push-to-talk only, for any version of
  this MVP. A wake-word pipeline is continuous audio processing, a much
  larger privacy/always-listening decision the owner hasn't made.~~
  **Superseded (2026-09-27 note):** ADR-008 decision 5 made the decision — a
  **local** wake word (openWakeWord/Porcupine-class, audio never leaves the
  device) is in scope for Phase 6.
- Multi-turn barge-in / interrupting the assistant mid-reply — the existing
  chat pipeline has no notion of a client-initiated stream cancellation
  beyond the web UI's "Durdur" stop button; voice doesn't get a new one here.
- Non-Turkish/English locales.
- Ollama-hosted STT/TTS specifically (separate from Ollama-as-chat-LLM,
  which §1 does ship) — Ollama is a chat/completion server; whether any
  STT/TTS model ends up served through it is part of the open provider
  decision, not assumed.
- Telegram/CLI voice messages (already explicitly deferred in the MVP 9
  brief) — this MVP is web-shell only.

## Branch plan

| # | Branch | Contents | Status |
|---|--------|----------|--------|
| 1 | `feat/mvp12-ollama-orb-foundation` | §1: `ollama-local` provider, env/compose wiring, `FridayOrb` + `VoiceView` + nav entry, wired to the existing real chat SSE endpoint. | **done** (on the working branch) |
| 2 | `feat/mvp12-gateway-stt-tts` | §2: `Capability.STT/.TTS`, adapter contract + result dataclasses, `InferenceGateway.transcribe()/synthesize()`, `FakeAdapter` fixtures — the single-door spine, no real model. | **done** (backend, sandbox-verified) |
| 2b | `feat/mvp12-stt-capture` (Mac) | Mic capture, `voice` app, bounded temp-file, lease-fenced transcription job against a local Whisper provider. | needs mic + local model |
| 3 | `feat/mvp12-tts-playback` (Mac) | Local Piper provider adapter, audio playback, real-amplitude orb state. | needs local model |
| 4 | `feat/mvp12-e2e-docs` | Full push-to-talk loop polish, Playwright e2e, brief closeout. | after 2b/3 |

## Test strategy

- Branch 1: `seed_ai_defaults` idempotency (re-run creates nothing new) with
  `OLLAMA_API_KEY` set vs. unset (provider absent when unset, matching
  every other opt-in provider's existing test coverage pattern in
  `ai_core/tests/`); frontend component test for `FridayOrb`'s state prop
  driving the correct visual class/animation branch; `VoiceView` reuses
  `ChatView`'s already-tested SSE parsing, so no duplicate SSE-parsing test
  is needed — only the state-machine mapping (chunk→speaking, done→idle,
  error→idle) is new and needs covering.
- Branches 2-4: to be defined once scoped for real (STT/TTS mocking strategy
  for the Gateway's `FakeAdapter`, lease-fencing tests mirroring
  `memory/tests/test_jobs.py`'s pattern, e2e coverage of the full loop).

## Risks / notes

- `host.docker.internal` as the Ollama `base_url` default only works when
  the Django backend runs inside Docker Desktop on macOS/Windows reaching a
  natively-running Ollama on the host — this is exactly the owner's current
  setup (Docker Compose dev stack + native Ollama on the same Mac) but will
  need reconsidering once they move to running things on a Mac Pro or any
  Linux host, where `host.docker.internal` isn't automatically resolvable.
- `OLLAMA_MODEL_ID` default (`llama3.1:8b`) is a guess, not a confirmed
  locally-pulled tag — the owner must verify/override it against whatever
  they've actually pulled (`ollama list`).
- Local model quality/latency for `router.intent`/`router.task_draft`'s
  strict-JSON-output requirement is unverified — NVIDIA NIM's dogfooding
  week (`docs/dogfooding/friction-log.md`) found real per-model differences
  here (the 70b NIM model was unreliable, the 8b model wasn't); the same
  kind of live evaluation will likely be needed for whichever Ollama model
  tag actually gets used, once this session can reach the owner's machine
  (it currently cannot — this is a cloud sandbox with no network path to a
  personal Mac's `localhost:11434`).
- Section 1's "speaking" orb state is driven by the real SSE chunk stream
  (text arriving), not real audio — it will look identical whether the
  provider behind it is the fake provider, Ollama, or a cloud model. This is
  intentional: it lets the UI be verified in this sandbox without any
  dependency on the owner's local Ollama being reachable.
