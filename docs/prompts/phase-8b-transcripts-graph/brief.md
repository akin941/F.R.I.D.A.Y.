# Phase 8B — Transcripts and knowledge graph (stub)

> Status: **Stub (2026-11-20)** from the owner-approved Friday 3.0 roadmap
> (`docs/research/2026-11-roadmap-proposal.md` §B4, MASTER_PLAN §AA
> "Friday 3.0 phases", order 5). **The full brief is written at phase
> start**, in the Phase 5/6/8A format, and reviewed by an independent
> Sonnet 5 subagent before branch 1.
> Parent decisions: ADR-001, ADR-002, ADR-003 (+ 2026-11-20 amendment),
> ADR-005, ADR-012 (ingestion), ADR-013 (node protocol, the transcript
> reader), ADR-009.
> Precondition: Phase 9 closeout (the node's per-directory transcript
> grant and Unix-socket relay) and the Phase 8A go/no-go passed.

## Goal

Friday knows the details of the owner's work across tools: what was
decided in which Claude Code / Codex session, which entities (repos,
services, firms, people by role) relate to which facts and since when —
still only from owner-authored text, behind the ADR-012 pipeline.

## Scope (to be split into branches at phase start)

- Claude Code / Codex transcript ingestion through the node (d5: on after
  the per-directory Touch ID grant), **owner turns only** (`user`
  messages typed by the human; `tool_result`, assistant text and web
  content excluded or fenced); scrubbed on the node and again on the
  server.
- Temporal facts in Postgres: `MemoryEntity` / `MemoryFact` with validity
  windows, `superseded_by`, source episode and confidence; recursive CTEs,
  no graph database.
- Retrieval: pgvector HNSW + tsvector + reciprocal-rank fusion, workspace
  filter first, 1–2 hop expansion, recency decay.
- Self-knowledge: a Graphify AST-only job in a network-denied harness
  container on merges to `main` (exact pin + hash, governance manifest);
  its output is imported as entities/facts with `source=graphify` and
  feeds the `self` template — it never writes the block.
- Headroom as a pre-send gateway stage behind a flag (off by default),
  bypassed for approval, tool-call, trust-check and core-block prompts;
  enabled per purpose only after the private eval set shows ≤ 1 point
  accuracy loss; pre/post token counts in the `ModelUsage` context report.
- Obsidian: mirror format; the opt-in `Inbox/` source imported once and
  moved to `Inbox/_imported/`; owner edits of mirrored notes saved to
  `_conflicts/<date>/` with a notice (ADR-005 unchanged).
- Weekly reflection (purpose `memory.reflect`, proposal-only, capped).
- A private eval set and its baseline.

## Go/no-go checkpoint

Eval baseline recorded; Headroom stays off unless ≤ 1 point loss; scrubber
corpus extended with transcript fragments at 100% secrets recall.
