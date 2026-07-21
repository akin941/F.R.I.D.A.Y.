# ADR-001: Workspace Is the Data Axis

## Status
Accepted

## Context
Jarvis OS is personal-first but must host company workspaces (external first) and,
later, other tenants on the same kernel. The original external-centric draft used
`Company` as the data axis, which made the personal product a second-class citizen
and multi-tenancy expensive to retrofit.

> Note (2026-07-07): the external examples in this decision are historical — external
> split off as an external product in ADR-006. The decision itself (the
> workspace axis) remains in force.

## Decision
Every business table carries a `workspace_id`. A workspace is `personal` or
`company`; each user gets a personal workspace at signup. All querysets reaching a
view or agent tool must pass through `for_actor()` (see `core/db.py`). Company
data never leaks into personal memory scope, and vice versa.

## Consequences
Multi-tenancy is nearly free later; every feature pays a small scoping tax now.
Cross-workspace isolation gets its own test package (MVP 10) and is a release gate.
