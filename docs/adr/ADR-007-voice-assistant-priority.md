# ADR-007: Voice Assistant Moves Up the Roadmap

## Status
Accepted (2026-07-16)

## Context
MASTER_PLAN §L has always closed the roadmap with a single unscoped clause —
"Orchestration/voice/productization last" — and no other document in the
repo names voice, speech, STT, or TTS anywhere except one deferred-Telegram
bullet in the MVP 9 brief. Voice was never designed, only postponed. Two
things changed: the personal kernel (shell/chat through skills/integrations)
is now fully done as of MVP 9 (§Y, 2026-07-14) — the exact trigger condition
§P already named for revisiting deferred roadmap items — and the owner now
has local-LLM infrastructure available for dogfooding (Ollama running on a
personal Mac, more machines to follow), making a locally-hosted, personal
voice assistant newly practical rather than purely aspirational.

## Decision
Voice assistant work is promoted out of the unscoped "last" bucket into a
named slot: **MVP 12** (the first of the MVP 12–15 slots reserved by
ADR-006), with a vision section at MASTER_PLAN §Z mirroring §P's format.
Architecture stays bound by the existing rules, not exempted from them: any
STT/TTS provider call goes through `InferenceGateway` (ADR-003's single door
stays in force — no direct SDK calls from a new `voice` app), every such
call is still recorded as `ModelUsage` (rule 3), and a local Ollama endpoint
is wired as an opt-in, personal-only chat provider exactly the way NVIDIA
NIM was (`a78b6de`) — never wired into the external product, same
personal/company infra separation ADR-006 already draws. Which STT/TTS
provider(s) to use (local Whisper/Piper on the user's own machine vs. a
cloud API) and how raw audio gets stored (the dormant MinIO settings vs. a
bounded temp-file pattern — no binary-blob storage exists anywhere in this
codebase today) are explicitly **not** decided by this ADR; they are open
decisions tracked in MASTER_PLAN §O and must be resolved before MVP 12's
real implementation branches (STT/TTS capability, async transcription jobs)
can start.

> Note (2026-07-17): amended by ADR-008 decision 5 — the shipped voice
> foundation stands, but the remaining voice work is re-sequenced to Phase 6
> of the Jarvis 2.0 roadmap (MASTER_PLAN §AA), with wake-word detection fixed
> as local (audio never leaves the device) and STT/TTS kept local.

## Consequences
MASTER_PLAN §L's closing sentence is rewritten to point at MVP 12/§Z instead
of naming voice as unscoped-last (orchestration/productization remain
unscoped and later — only voice is promoted here). A new §Z vision section
is added, and §O gains two open-decision entries (STT/TTS provider choice,
binary-audio storage mechanism). A local Ollama `ModelProvider` ships now,
independent of the rest of MVP 12, as low-risk foundation work. The full
STT/TTS pipeline itself is a separate implementation pass
(`docs/prompts/mvp-12-voice/brief.md`) gated on the two open decisions above.
