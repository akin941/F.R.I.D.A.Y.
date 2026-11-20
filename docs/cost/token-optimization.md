# Token & Cost Optimization

Five-layer defence — cost falls out of architecture, not just cheaper models:

1. **Don't call** — exact cache (same prompt version + model + input hash) and a
   semantic/retrieval cache — not yet built or scheduled in a phase. Cache keys always include
   workspace_id; sensitivity/policy changes invalidate.
2. **Send less** — Context Builder works with per-purpose budgets (system /
   conversation / memory / RAG / tool results / plan / code / safety). Every call
   records a `context_report` on ModelUsage: what was included, what was cut, why.
3. **Send summaries** — SummaryRecord pyramid per source (one_line/short eager at
   ingestion with a cheap model; section/detailed lazy). Retrieval-first ladder:
   metadata → summary → RAG/graph query → targeted chunks → (logged escalation)
   full content.
4. **Send cheap** — model cascade via the router: classification/distillation/
   short summaries on cheap tiers; RAG/extraction mid; planning/review strong;
   top tier only with a logged reason. Tier table lives in the DB (skeleton
   shipped in Phase 0; full routing map in Phase 5), revised by evals (not
   yet scheduled), never by feel.
5. **Answer outside** — external document analysis gateway (long-context Gemini
   preprocessor; optional NotebookLM for public docs only). See MCP governance.

A read-only **Token Optimizer Agent** (not yet scheduled) mines ModelUsage/context reports and
proposes routing/cache/summary changes; proposals go through the approval gate.

First habits (from MVP 1): stable system-prompt prefixes (provider prompt cache),
cheap tier pinned for distillation/classification, retrieval threshold below which
no LLM call is made.

> Note (2026-09-27): the MVP 3/6/8/17 references in this document predate
> ADR-008 and were replaced with Jarvis 2.0 phase references (MASTER_PLAN
> §AA). Only the tier-routing map is currently scheduled (Phase 5); caches,
> evals and the Token Optimizer Agent are unscheduled.

> Note (2026-09-27, Phase 5 branch 2 — D11 purpose split): the review
> pipeline's `harness.review` purpose used to cover two different costs —
> the external review CLI's own usage rows *and* Jarvis's in-process gateway
> summary of that review. From Phase 5 branch 2 on, `harness.review` means
> **external-CLI cost only** (untiered, `record_external_usage`), and the
> gateway summary is `harness.review_summary` (cheap tier, prompt
> `jarvis_harness_review_summary`). Historical rows keep `harness.review`
> for both, so a pre/post comparison must **sum `harness.review` and
> `harness.review_summary`** after the split. The full tier map
> (`AI_PURPOSE_TIERS`) and its explicit exceptions (`AI_UNTIERED_PURPOSES`:
> embeddings, external usage) live in `backend/config/settings/base.py`; a
> CI test fails on any production purpose that is in neither.
