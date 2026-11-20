# ADR-002: Django Modular Monolith

## Status
Accepted

## Context
One developer, long roadmap, mostly CRUD + workflow + async pipelines. Django
gives ORM/admin/auth/migrations for free; microservices would multiply
operational cost without solving any current problem.

## Decision
Single Django project; each module is a Django app. Kernel apps (accounts,
workspaces, audit, ai_core, assistant, memory, files, knowledge, tasks, tools,
approvals, agents, skills, integrations) never import `apps.modules.*`; module
packs (e.g. a document-intelligence pack) may import kernel apps; `ai_core` imports no business
module. Contracts enforced with import-linter (see `backend/pyproject.toml`).

> Note (2026-07-07): the company-workspace examples in this decision are
> historical (see ADR-006: workplace products run as external installations). The decision itself (the
> modular monolith) remains in force; the first concrete module pack is TBD.

> Note (2026-09-27): the kernel app list above is out of date — there never
> was a `files` app, and it omits `core` and `harness` (the latter added by
> ADR-008). The actual kernel apps, per `INSTALLED_APPS` in
> `backend/config/settings/base.py`, are: `core`, `accounts`, `workspaces`,
> `audit`, `ai_core`, `assistant`, `memory`, `knowledge`, `tasks`,
> `approvals`, `tools`, `skills`, `agents`, `integrations`, `harness`. No
> `apps.modules.*` pack exists yet.

## Consequences
A module can be extracted into a service later because boundaries stay clean.
The first extraction candidate, if ever needed, is the AI gateway.
