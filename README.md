# Jarvis OS

Personal AI Operating System + Workspace Platform.

Jarvis OS is a **personal-first**, web-based AI operating layer: one entry point for
projects, documents, tasks, memory, knowledge, and AI agents. Workplace systems are
not built inside Jarvis: they run as independent external installations (external is the
first — see `docs/adr/ADR-006-external-workplace-products.md`), and Jarvis connects to them
as an external client, with a long-term fleet-management layer over many
installations.

> "OS" means a shared identity/memory/permission/audit layer with pluggable
> modules — not a real operating system kernel.

## Stack

Django modular monolith · PostgreSQL + pgvector · Redis + Celery · MinIO (S3) ·
Next.js (MVP 1+) · Docker Compose.

## Quickstart (development)

```bash
cp .env.example .env      # fill in what you need
make up                   # start postgres/redis/minio/web/worker
make test                 # backend + frontend tests
make lint                 # backend (ruff + import-linter) + frontend (eslint + tsc)

# frontend (separate Node process, see frontend/README.md)
cp frontend/.env.example frontend/.env.local
make frontend-install
make frontend-dev         # http://localhost:3001

# e2e smoke test (Playwright, real backend + FakeAdapter — requires `make up` first)
make e2e-install           # one-time: downloads the Chromium browser
make test-e2e              # boots/reuses the frontend dev server, runs the suite
```

## Repository layout

- `backend/` — Django project (`config`) + apps (`core` for now; kernel apps arrive in MVP 1)
- `frontend/` — Next.js web shell (MVP 1)
- `cli/` — Jarvis CLI client (MVP 9)
- `deploy/docker/` — Compose stack + dev Dockerfile
- `docs/` — master plan, ADRs, governance, prompt packs

## Non-negotiable architecture rules

See [CLAUDE.md](CLAUDE.md) and `docs/adr/`. Highlights: Workspace is the data axis;
Postgres is the source of truth; all LLM calls go through the `ai_core` gateway and
are logged as ModelUsage; no write/delete/deploy/external-send without the approval
gate; agents can never change their own permissions.
