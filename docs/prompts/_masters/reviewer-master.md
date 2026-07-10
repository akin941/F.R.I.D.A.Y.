# Reviewer Master Prompt

> Usage: [context header from planning-master.md] + the MVP `brief.md` + the
> diff/branch → give to a **different** model/tool than the one that wrote the code.

## Task

You did not write this code; you are its independent reviewer. Audit in this
order, and give every finding a file:line and a severity:

1. **Workspace scoping** — any queryset/endpoint without scoping? Is
   cross-workspace access possible?
2. **Gateway single door** — any LLM/provider call outside `ai_core`?
3. **Approval gate** — any write path that skips the gate?
4. **Cost logging** — any LLM call that doesn't write ModelUsage?
5. **Audit** — are write operations logged?
6. Architecture fit — app dependency violations, scope creep beyond the brief.
7. Test adequacy — are acceptance criteria covered? permission tests present?
8. Security — secret leakage, injection, path escape.
9. Regression risk — behaviour changes to existing features.

If there are no findings, say "approved" and list what you checked.
