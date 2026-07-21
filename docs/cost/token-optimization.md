# Token & Cost Optimization

Five-layer defence — cost falls out of architecture, not just cheaper models:

1. **Don't call** — exact cache (same prompt version + model + input hash) from
   MVP 3; semantic/retrieval cache from MVP 6. Cache keys always include
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
   top tier only with a logged reason. Tier table lives in the DB, revised by
   evals (MVP 17), never by feel.
5. **Answer outside** — external document analysis gateway (long-context Gemini
   preprocessor; optional NotebookLM for public docs only). See MCP governance.

A read-only **Token Optimizer Agent** (MVP 8+) mines ModelUsage/context reports and
proposes routing/cache/summary changes; proposals go through the approval gate.

First habits (from MVP 1): stable system-prompt prefixes (provider prompt cache),
cheap tier pinned for distillation/classification, retrieval threshold below which
no LLM call is made.
