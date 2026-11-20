# ADR-012: Continuous-Learning Ingestion Behind a Privacy Pipeline

## Status
Accepted 2026-11-20 — owner approval of the Friday 3.0 roadmap
(`docs/research/2026-11-roadmap-proposal.md` §B4, §g decisions d5, d6 and d10).
Implemented by Phase 8A (`docs/prompts/phase-8a-learning-memory/brief.md`)
and extended by Phase 8B. Extends ADR-005 (memory governance). Amends
nothing else. ADR-008 decision 4 (`work_log`) and ADR-009 (reversible owner
deletes, gated purge) apply unchanged.

Numbering note: the proposal called this "ADR-011". ADR-011 is reserved by
the approved Phase 6 brief for "voice transport", so the proposal's ADRs are
numbered from ADR-012.

## Context
Friday 3.0 wants Friday to learn from the owner's work without being told:
commits, harness outcomes, Claude Code session events, calendar items and,
later (Phase 8B), the owner's own turns in Claude Code / Codex transcripts.
Today memory comes only from chat (Phase 1 extraction, every candidate
`pending_approval`) and from `record_work_log()` (auto-approved
`system_generated` rows). Continuous ingestion changes three things:

- **Volume and provenance.** Raw activity is mostly not owner-authored:
  tool output, assistant text, web pages, third-party commit messages. Any
  of it can carry prompt-injection text, and none of it is a statement of
  the owner's preferences.
- **Secrets and personal data.** Session events, shell history and
  transcripts routinely contain API keys, tokens, `.env` fragments, e-mail
  addresses, phone numbers, Turkish ID numbers (TCKN) and IBANs.
- **Workspace boundaries.** A repo path or calendar belongs to one
  workspace. Firm work must never land in personal memory (ADR-001).

## Decision
1. **Source registry.** Every ingestion source is a registered row with an
   owner, a kind, a workspace-mapping rule, a daily token budget and an
   enabled/paused state. Nothing is ingested from an unregistered or paused
   source. Default sources (d5): on in 8A — git commits of registered repos,
   harness outcomes, Claude Code hook events (metadata only), calendar
   metadata; on in 8B, after the per-directory node grant — Claude/Codex
   transcripts (owner turns only); opt-in — shell history, mail bodies, the
   Obsidian `Inbox/`.
2. **Workspace mapping before storage.** Each item is mapped to exactly one
   workspace through explicit rules (registered repo → its workspace, local
   path prefix → workspace, calendar/connector binding → its workspace).
   An item no rule maps is **dropped and counted**, never defaulted to the
   personal workspace. Firm items land only in the firm workspace.
3. **Scrubber before storage.** Every item passes a scrubber (secret rules
   in the gitleaks style plus entropy, PII patterns for e-mail, phone,
   TCKN, IBAN and card numbers, and a per-workspace term list such as
   customer names) before any row is written. Hits become typed
   placeholders (`[SECRET:aws_key]`, `[PII:tckn]`). It runs where the data
   is produced (server in 8A, node and server in 8B). A synthetic corpus is
   part of the test suite; CI requires 100% recall on its secrets class.
4. **Retention (d10).** Raw episodes expire 30 days after ingestion in a
   personal workspace and 14 days in a firm workspace, then are deleted by a
   daily task. The retention window is part of the owner's decision, like
   ADR-009's trash retention. Derived memories keep only a provenance quote
   (bounded length) and the episode id.
5. **Ingestion audit view.** Per source: counts, last N items, scrubber
   hits by type, dropped/unmapped counts, tokens spent against budget, and
   proposal acceptance rate. Owner-only, web channel.
6. **Forget source X.** One owner action moves the source's episodes and
   everything derived from them (facts in 8B, memories, the Obsidian mirror
   on its next export) to the trash and pauses the source. It is a
   reversible confirm-class move under ADR-009; permanent purge is the
   gate-class `purge_trash` path. Available from the first branch that
   ingests anything.
7. **Owner-authored extraction only.** The extraction prompt sees only
   text the owner wrote (commit messages authored by a configured owner
   identity, owner turns in transcripts, owner-written notes). Tool output,
   assistant text, web content and third-party text may be passed only
   inside an untrusted-data fence and can never be the provenance of a
   proposal.
8. **Provenance is checked in code.** Every proposal carries a quote that
   must match owner-authored episode text verbatim (after normalisation of
   whitespace). A proposal without a match is dropped and counted.
9. **Caps and approvals (d6).** At most 10 learned proposals per night per
   owner and at most 3 core-block diffs per week. Nothing learned is
   auto-approved except the existing `work_log` class (ADR-008 decision 4);
   bulk approve is allowed only for `work_log`-class items; anything that
   touches the `self`, `developer` or `preferences` core blocks is approved
   per item. Content from external tools or MCP is never auto-promoted.
10. **Never ingested:** screen content, screenshots, accessibility trees,
    keystrokes, browser history. An on-demand screenshot (Phase 10) is a
    gate-class invocation used for one turn and never stored as an episode.
11. **Gateway and cost.** Every learning model call goes through the
    gateway with its own purpose (ADR-003) and a tier; per-source daily
    budgets are checked before the call and the gateway's purpose budget
    (ADR-003 amendment 2026-11-20) is the hard stop. Personal-class sources
    (calendar, mail, transcripts) and `private`/sensitive rows are sent with
    `personal_data=True`, so only `personal_data_allowed` (local) providers
    serve them.
12. **Obsidian stays a mirror (ADR-005).** `Inbox/` notes, when that
    opt-in source is enabled, are imported once as episodes and moved to
    `Inbox/_imported/`; the vault never changes Postgres.

## Consequences
- Learning becomes possible without weakening ADR-001/003/005: every raw
  row is scoped, scrubbed and short-lived; every derived row is a proposal.
- The owner carries a review load (a capped nightly card). The Phase 8A
  go/no-go checks the acceptance rate (target ≥ 60% over two weeks) before
  more sources are added in 8B.
- Recall of the scrubber is a release gate: a corpus regression blocks the
  branch. Precision misses (over-scrubbing) are tolerated and tuned.
- Some learning value is lost on purpose: third-party text, tool output and
  screen content never become memories.
- New tables (sources, mapping rules, episodes, scrub terms) are
  `WorkspaceScopedModel`s; the ingestion code is a `memory.ingestion`
  package with import-linter contracts (no `harness`, `connectors` or node
  imports; producers in those apps call a memory-owned port instead).
