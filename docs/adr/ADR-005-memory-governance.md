# ADR-005: Memory Governance

## Status
Accepted

## Context
Friday learns continuously (semantic/episodic/preference/procedural memory), but
uncontrolled remembering pollutes context, leaks company data into personal scope,
and destroys trust.

## Decision
PostgreSQL is the source of truth for memory; the Obsidian vault is a
human-readable mirror (export) plus a human-authored ingestion source — never the
primary store. AI-suggested memories are created as `pending_approval`. Memory is
scoped (`personal` / `workspace` / `agent`); company workspace data can never be
written into personal scope. `private` records never reach cloud models. Users can
edit, forget and export everything; every memory access is auditable.

> Note (2026-07-17): amended by ADR-008 decision 4 — a deliberately narrow
> `work_log` class of system-generated activity summaries is auto-approved
> (audited and deletable); every other AI-suggested memory still starts
> `pending_approval`, and the `private` negative contract is unchanged.

> Note (2026-11-20): extended by ADR-012 — continuous-learning ingestion
> runs behind a privacy pipeline (source registry, workspace mapping,
> scrubber, raw-episode TTL, audit view, forget-source); extraction sees
> only owner-authored text; learned proposals are capped and
> owner-approved. Nothing in this ADR is relaxed.

## Consequences
Memory quality stays curatable; a distillation + consolidation pipeline is needed
(MVP 2+). Vault sync is deliberately one-way per file class to avoid conflict
resolution hell (see docs/memory/obsidian-vault-strategy.md).
