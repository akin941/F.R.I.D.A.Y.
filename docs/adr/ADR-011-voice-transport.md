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

## Implementation note (2026-11-20, Phase 6 closeout)

All six decisions are implemented (brief branches 1–5). Closeout additions:
`manage.py preflight` checks every speech row with one pinned gateway call
(`only_provider` now also on `transcribe()`/`synthesize()`; the
personal-data rule of decision 4 still applies to a pinned row, so a
disallowed cloud row is reported, never called), checks the TTS routes and
the audio budget, and runs a real browser to prove a `?mute=1` voice tab is
silent (`frontend/e2e/voice-mute.e2e.ts`). The CI e2e
(`frontend/e2e/voice.e2e.ts`) drives push-to-talk with a stubbed
microphone through fake STT/TTS to a task draft confirmed by click
(decision 5), and the HUD showing the voice state and a pending approval
deep link.

## Amendment 2026-11 — the node voice channel

Phase 11 branch 1 (`feat/p11-node-voice-channel`,
`docs/prompts/phase-11-live-voice/brief.md` D3–D5).

- **Decision 1** "Web channel only (a trusted device's web session,
  owner)" becomes "**the owner's web session or the node voice
  credential**". The node voice credential is a `node_voice`
  `PersonalAccessToken`, minted only by a Friday Node's secret
  (`POST /api/v1/auth/nodes/voice-token`), bound to the owner's personal
  workspace and to the minting node device, hard-expiring after
  `VOICE_LIVE_TOKEN_TTL_SECONDS` (default 900, refused at boot above
  3 600), re-checked against the node device on every request (revoke,
  kill switch, `revoke_all_devices` refuse it at once). It authenticates
  as `CHANNEL_NODE_VOICE` (low-trust) and reaches exactly: the three voice
  endpoints, `POST /api/v1/voice/live/sessions`, the voice-policy read and
  the chat turn of a conversation opened via `node_voice`
  (`Conversation.opened_via`; any other conversation is a 404). PATs of
  every other kind, the facade and Telegram stay refused.
- On the node voice channel `input_mode` is **forced to `voice`** whatever
  the request body says, so ADR-015's capability cap always applies.
- Decisions 2–6 are unchanged: the node's voice child holds no provider
  key and makes no model call (ADR-013 decision 8); STT and TTS stay
  gateway calls on `personal_data_allowed` providers with one `ModelUsage`
  each; no audio is persisted; every draft is still a card clicked on
  screen.

## Implementation note — Phase 11 closeout (`chore/p11-closeout`, 2026-11-20)

The amendment's surface list is now checked at runtime, not only in tests:
the `voice_live_channel` preflight row walks the live URL table and fails
when the set of DRF views whose authenticators accept `node_voice` differs
from the six D4 views, when the kind becomes mintable through
`/auth/tokens` or maps to another channel, or when
`VOICE_LIVE_TOKEN_TTL_SECONDS` leaves 1..3 600. The closeout e2e
(`backend/ops/tests/test_live_voice_e2e.py`) runs the transport end to end
over real HTTP — the reference host, a real `friday-voice run` child and
this repository's Django on a loopback uvicorn with FakeAdapter and the
fake speech provider: a push-to-talk turn is one `voice.stt` and one
`voice.tts` `ModelUsage` per sentence, the user message is
`channel=node_voice`, `input_mode=voice`, every trace row `source=node`,
and no audio is stored. Decisions 1–6 (as amended) are unchanged. Runbook:
`docs/runbooks/live-voice.md`.
