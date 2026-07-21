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
packs (e.g. external module) may import kernel apps; `ai_core` imports no business
module. Contracts enforced with import-linter (see `backend/pyproject.toml`).

> Note (2026-07-07): the external examples in this decision are historical — external
> split off as an external product in ADR-006. The decision itself (the
> modular monolith) remains in force; the first concrete module pack is TBD.

## Consequences
A module can be extracted into a service later because boundaries stay clean.
The first extraction candidate, if ever needed, is the AI gateway.
