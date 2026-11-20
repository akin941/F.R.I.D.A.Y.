# Phase 12 — Reach and proactivity (stub)

> Status: **Stub (2026-11-20)** from the owner-approved Friday 3.0 roadmap
> (`docs/research/2026-11-roadmap-proposal.md` §B7, MASTER_PLAN §AA
> "Friday 3.0 phases", order 8). **The full brief is written at phase
> start**, in the Phase 5/6/8A format, and reviewed by an independent
> Sonnet 5 subagent before branch 1.
> Parent decisions: ADR-004/ADR-008 decision 3, ADR-010 (connector
> framework, MCP client only), ADR-012, ADR-015, `docs/security/
> mcp-governance.md`.
> Precondition: Phase 11 closeout.

## Goal

Friday starts useful conversations and uses the owner's automations:
a spoken morning digest, allowlisted n8n workflows, curated skills and
playbooks learned from harness runs — with unattended actions denied by
default.

## Scope (to be split into branches at phase start)

- n8n through the Phase 5 connector framework: narrow MCP Server Trigger
  workflows, a Friday-side workflow-ID allowlist, writes gated, async
  voice turns.
- Script-gated heartbeat/cron (cron cannot create cron; unattended =
  deny by default) and a spoken morning digest (including the 8A learning
  card).
- SKILL.md skills with staged approval and a curator (no public skill
  registries, no "allow always" wildcards, no LLM auto-approval, no host
  execution by default).
- Procedural playbooks distilled from harness runs, as proposals.
- Wake-phrase routing.

## Go/no-go checkpoint

The owner keeps the morning digest on after two weeks.
