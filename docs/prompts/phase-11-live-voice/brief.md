# Phase 11 — Local live voice (stub)

> Status: **Stub (2026-11-20)** from the owner-approved Friday 3.0 roadmap
> (`docs/research/2026-11-roadmap-proposal.md` §B2, §c.1, §c.3,
> MASTER_PLAN §AA "Friday 3.0 phases", order 7). **The full brief is
> written at phase start**, in the Phase 5/6/8A format, and reviewed by an
> independent Sonnet 5 subagent before branch 1.
> Parent decisions: ADR-003 (+ 2026-11-20 amendment), ADR-011 (voice
> transport), ADR-013 (the voice process is a child of the node),
> **ADR-015** in full (decisions 4–6 completed before always-on ships),
> d3 (always-on only after the false-activation budget is met).
> Precondition: Phase 10 closeout.

## Goal

Natural, interruptible voice anywhere on the Mac: always-on "Hey Friday",
barge-in, echo cancellation — still local by default, still "voice
proposes, screen approves".

## Scope (to be split into branches at phase start)

- A Pipecat child process of `friday-node` (exact pin, generated adapter
  tests, upgrades only in a dedicated branch): openWakeWord with the
  custom `hey_friday` model → Silero VAD → smart-turn v3 → MLX Whisper
  (whisper.cpp fallback) → an LLM step that calls Friday's streaming chat
  endpoint (never a provider) → language-routed TTS.
- macOS voice-processing AEC and barge-in.
- Always-on wake word with the menu-bar indicator, fail-closed mute and
  the global hotkey (ADR-015 decisions 2–3).
- ADR-015 decisions 4–6: self-trigger rule on the AEC-cleaned signal,
  nightly self-trigger test, wake-storm auto-mute, 30 s silence close.
- Piper stays a separate executable over stdio/HTTP (GPL-3.0, never
  imported or linked, not shipped in the signed bundle; a test checks
  there is no `import piper`).
- openWakeWord model licence confirmed from the LICENSE/model cards and
  recorded (personal, non-distributed use).

## Go/no-go checkpoint

FAR ≤ 0.2 per hour on 24 h of household audio; self-trigger test green;
latency targets (local p50 ≤ 1.5 s, p95 ≤ 2.5 s) met.

## Owner steps (pending) — recorded 2026-11-20

1. Record 24 h of household audio (kept outside the repo) for the
   false-accept measurement.
