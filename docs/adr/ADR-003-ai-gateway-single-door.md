# ADR-003: AI Gateway Is the Single Door

## Status
Accepted

## Context
Cost tracking, model routing, fallback, PII/sensitivity policy, caching, evals and
provider portability all depend on one interception point for LLM traffic.

## Decision
Every LLM call — chat, extraction, embeddings, agent steps, skills, external CLI
backends' usage reports — goes through `ai_core`'s InferenceGateway and writes a
ModelUsage row (tokens, cost, latency, purpose, workspace, prompt version,
routing reason, context report). No exceptions; code calling a provider SDK
directly is a review-blocking defect. All gateway-calling code must be mockable.

## Consequences
Provider switching, budgets, semantic caching and the Token Optimizer Agent become
configuration/analysis problems instead of refactors. The gateway is in-process
now; it may become a separate service later without changing callers.

## Amendment 2026-11-20 — audio purposes and gateway-enforced budgets

Accepted 2026-11-20 with the Friday 3.0 roadmap
(`docs/research/2026-11-roadmap-proposal.md` §B5, §c.2, decision d2);
implemented in Phase 6 branch 1 (`docs/prompts/phase-6-voice-identity/brief.md`,
"Amendment 2026-11-20").

- **Audio is metered like text.** `voice.stt` and `voice.tts` calls write
  one `ModelUsage` row each, as today. A provider that prices audio (for
  example an `openrouter` row on the `openai_compatible` adapter) records a
  real cost: the provider-reported cost when the response carries it,
  otherwise a configured per-audio-second / per-character price, corrected
  later from the provider's generation lookup when that is the only source.
- **Budgets are enforced in the gateway, before the call.** A per-purpose
  daily budget, a per-session cap and a monthly cap apply to every purpose
  that has a budget configured; every audio purpose with a non-zero-cost
  provider (`voice.stt`, `voice.tts`, later `voice.realtime`) must have one.
  Default monthly audio cap: 20 USD, owner-editable. A call that would
  exceed a cap is refused and the refusal is itself a `ModelUsage` row
  (`error_type="budget_exceeded"`), so caps are auditable. The same
  primitive later carries the learning caps of ADR-012 (default 20 USD per
  month).
- **Fallback attempts.** Tier entries may become ordered target lists;
  each attempt is its own `ModelUsage` row recording the served provider
  and model. If every cloud target fails, the tier may fall back to the
  local model (the HUD shows *offline-local*).
- **No second door.** No LiteLLM proxy or external router. OmniRoute was
  evaluated on 2026-11-20 and rejected as a dependency; its routing-trace
  and circuit-breaker ideas may be copied into `ai_core` later.
- A **second amendment** for a cloud realtime session as a metered unit
  (gateway WebSocket relay, per-session and monthly caps that close the
  relay) is written only when cloud live mode leaves the "later" list.
