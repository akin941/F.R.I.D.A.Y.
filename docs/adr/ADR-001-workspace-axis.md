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

> Note (2026-09-27): since ADR-008 (single-owner system) signup is off; the
> owner's personal workspace is created by the `bootstrap_owner` management
> command (`backend/accounts/management/commands/bootstrap_owner.py`), which
> goes through the same `register_user()` service. The workspace axis itself
> is unchanged.
>
> Note (2026-09-27, Phase 4 branch 2): signup is removed entirely. The owner
> (`User.is_owner`, `bootstrap_owner` or `set_owner`) reaches the web only
> through an enrolled trusted device (`manage.py issue_pairing_code` → `/enroll`);
> firm (`company`) workspaces are created by the owner from the web
> (`POST /api/v1/workspaces`, owner + web channel only, owner membership only).
> Scoping through `for_actor()` is unchanged.

## Consequences
Multi-tenancy is nearly free later; every feature pays a small scoping tax now.
Cross-workspace isolation gets its own test package (MVP 10) and is a release gate.

> Note (2026-09-27): MVP 10 (multi-user tenancy) was cancelled by ADR-008
> decision 2. No separate isolation package exists; cross-workspace isolation
> tests live per app (each app's `tests/` covers its own `for_actor()`
> scoping) and remain part of the CI gate.
