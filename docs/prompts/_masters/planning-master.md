# Planning Master Prompt

> Usage: [context header below] + the MVP's `brief.md` → give to a strong model.
> Output is a plan, not code.

## Context header (include verbatim in every prompt)

Project: Jarvis OS. Read `CLAUDE.md`, the relevant `docs/MASTER_PLAN.md` section
and `docs/adr/`. Non-negotiable rules: (1) workspace scoping — every business
table has workspace_id, every queryset goes through `for_actor()`; (2) all LLM
calls go through the `ai_core` gateway and write ModelUsage; (3) write actions go
through the approval gate; (4) audit mixin on every writing model; (5) one-way app
dependencies (modules → kernel); (6) every PR passes tests + ruff; (7) LLM-calling
code must be mockable.

## Task

You are planning this MVP, not implementing it. Produce:

1. Goal and user value (3 sentences max).
2. Scope list and an explicit **out-of-scope** list (extend the brief; draw the line).
3. Inventory: files/apps/migrations/endpoints to create or change.
4. Work order — each step sized to a single PR.
5. Test strategy: test files, critical scenarios, what gets mocked.
6. Verification commands for each acceptance criterion.
7. Risks and simplification opportunities.

If the brief conflicts with the architecture rules, stop and ask before planning
around it.
