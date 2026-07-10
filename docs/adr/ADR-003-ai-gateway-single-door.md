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
