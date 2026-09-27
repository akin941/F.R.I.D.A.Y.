# ADR-005: Memory Governance

## Status
Accepted

## Context
Jarvis learns continuously (semantic/episodic/preference/procedural memory), but
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

## Consequences
Memory quality stays curatable; a distillation + consolidation pipeline is needed
(MVP 2+). Vault sync is deliberately one-way per file class to avoid conflict
resolution hell (see docs/memory/obsidian-vault-strategy.md).
