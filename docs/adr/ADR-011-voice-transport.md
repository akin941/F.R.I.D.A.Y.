# ADR-011: Voice Transport

## Status
Accepted 2026-11-20 — Phase 6 branch 1 (`feat/p6-speech-gateway`), as
specified by the approved Phase 6 brief
(`docs/prompts/phase-6-voice-identity/brief.md`, D1–D11 and "Amendment
2026-11-20"). Like every branch, it merges only after the independent
Sonnet 5 review (CLAUDE.md). Amends ADR-007 and MASTER_PLAN §O (the MVP 12
async transcription job and its bounded temp file are superseded, point 3).
Cross-references ADR-015 (Accepted 2026-11-20) for the voice-turn
capability cap and the microphone rules; it does not restate them.

## Context
Phase 6 makes Friday spoken: "Hey Friday", speak, hear the answer, entirely
on the owner's own hardware by default. A voice recording is biometric and
can carry third parties' voices; a recognized utterance can come from a TV,
a guest, or Friday's own speaker. The MVP 12 gateway spine already had
`InferenceGateway.transcribe()`/`.synthesize()` and the STT/TTS provider
capabilities, but no real provider could serve them and nothing stopped an
audio call from reaching a cloud provider.

## Decision
1. **Voice is a transport, not a pipeline.** Audio → transcript before the
   turn and reply text → audio after it, around the unchanged conversation
   pipeline (`POST /api/v1/conversations/{id}/messages`): same intent
   router, context builder, drafts and approval gate as typed text. Web
   channel only (a trusted device's web session, owner); PATs, the facade
   and Telegram are refused.
2. **Wake word in the browser; nothing leaves the device before it fires.**
   Detection runs in the tab (openWakeWord ONNX models via
   `onnxruntime-web`, a custom `hey_friday` head). No pre-roll: the
   uploaded utterance starts at the first frame after the detection frame.
   No cloud speech API for detection or capture — including the browser's
   Web Speech API, which streams audio to the browser vendor.
3. **Raw audio is never persisted and never written to disk by Friday.**
   Uploads are capped below Django's in-memory threshold (30 s of 16 kHz
   mono PCM16 = 960 KB), validated with stdlib `wave` over `BytesIO`
   (`ai_core/audio.py`), passed to the gateway as bytes and dropped when the
   request ends. No audit row per utterance: the `ModelUsage` row is the
   record, and it never holds audio or text. This tightens §O's "bounded
   temp file, deleted on completion" — nothing to delete, nothing a crash
   can leave behind.
4. **STT/TTS calls require `personal_data_allowed` providers, enforced in
   the gateway.** `InferenceGateway._run_audio` keeps only providers with
   `personal_data_allowed=True` for every transcribe and synthesize call,
   unconditionally — the audio entry points take no `personal_data`
   argument, so no caller can relax it — and fails closed with
   `NoProviderAvailable` and a `ModelUsage` row
   `routing_reason="personal_data_required"`. A cloud audio row (e.g.
   `openrouter-stt`/`openrouter-tts`, seeded disabled and disallowed)
   qualifies only after the owner's deliberate, audited toggle
   (`manage.py set_provider_personal_data <name> --allow --activate`,
   decision d2 of the Friday 3.0 roadmap). TTS is included because it
   speaks reply text that may come from a personal connector read.
5. **A spoken utterance is never the explicit decision of ADR-008
   decision 3 nor a confirm-class confirmation.** Voice cannot approve,
   reject, confirm or cancel; drafts from a voice turn are the same cards,
   clicked on screen. ADR-015 decision 1 makes this a server-side cap on
   every `input_mode="voice"` turn.
6. **The `voice` kernel app and its contracts.** `voice` (Phase 6 branch 2)
   may import `core`, `workspaces`, `ai_core` and `assistant` (conversation
   lookup and `Message.InputMode` only); it must not import `approvals`,
   `tasks`, `tools`, `agents`, `skills`, `memory`, `knowledge`,
   `integrations`, `harness`, `connectors`, `deploys`; no app imports
   `voice` (a leaf, like `harness`). It is added to the `ai_core`/`audit`
   forbidden lists. Speech engines run out of process behind an
   OpenAI-compatible HTTP server (speaches, or the `deploy/voice/` sidecar
   fallback); `ai_core/tests/test_sdk_guard.py` forbids importing
   `faster_whisper`, `ctranslate2`, `piper`, `onnxruntime` or `whisper`
   anywhere in `backend/`.

It moves nothing between gate-class and confirm-class.

## Consequences
- The only real speech path is the existing `openai_compatible` adapter
  (`transcribe` via multipart `/audio/transcriptions`, `synthesize` via
  `/audio/speech`); no new provider SDK, no new `ProviderType`, no second
  door (ADR-003; the A3 budgets of its 2026-11-20 amendment apply to audio).
- Every audio row's cost is real: 0 for local providers, the
  provider-reported cost or the configured per-second / per-character
  price for a priced row, corrected later from the provider's generation
  lookup when the response carried none.
- **Licences (R3).** openWakeWord's pre-trained models carry a
  non-commercial licence: fine for a single-owner personal system, a
  blocker if Friday is ever productized — hence downloaded by
  `make voice-models`, never committed. Piper voices and Whisper weights
  carry their own licences, recorded in `docs/runbooks/voice-setup.md`.
- Voice turns feed memory extraction like typed turns; conversation-derived
  memories stay `pending_approval` (ADR-005), so a stray transcript is at
  most a suggestion the owner rejects.
- ADR-002's kernel-app list gains `voice` (note there).
