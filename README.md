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

## First run

Jarvis is a single-owner system (ADR-008): signup is off (`SIGNUP_ENABLED=false`
everywhere). Create the one owner account once, after `make migrate`:

```bash
cd backend
JARVIS_OWNER_PASSWORD=... uv run python manage.py bootstrap_owner --email you@example.com [--name "..."]
```

The password comes from `JARVIS_OWNER_PASSWORD` or an interactive prompt — never a
CLI argument. The command refuses to run if any user already exists. It also
creates the owner's personal workspace.

The agent harness launches its CLI workers (Claude Code, Codex CLI) from locally
built Docker images; see `backend/harness/docker/README.md` for how to build them
(the pinned versions/digests there are still placeholders).

## Roadmap

The current roadmap is Jarvis 2.0 (ADR-008): `docs/VISION_JARVIS_2.md` (approved
vision) and `docs/MASTER_PLAN.md` §AA (per-phase status). MVP numbering elsewhere in
the docs belongs to the earlier track (MVP 1–9 done; later MVP slots were cancelled or
re-sequenced into phases by ADR-008).

## Repository layout

- `backend/` — Django project (`config`) + kernel apps: `core`, `accounts`, `workspaces`,
  `audit`, `ai_core`, `assistant`, `memory`, `knowledge`, `tasks`, `approvals`, `tools`,
  `skills`, `agents`, `integrations`, `harness`
- `frontend/` — Next.js web shell (MVP 1)
- `cli/` — Jarvis CLI client (MVP 9)
- `deploy/docker/` — Compose stack + dev Dockerfile
- `docs/` — master plan, ADRs, governance, prompt packs

## Non-negotiable architecture rules

See [CLAUDE.md](CLAUDE.md) and `docs/adr/`. Highlights: Workspace is the data axis;
Postgres is the source of truth; all LLM calls go through the `ai_core` gateway and
are logged as ModelUsage; no write/delete/deploy/external-send without the approval
gate; agents can never change their own permissions.

Harness (coding-agent runner): images, runner worker and the first paid run are covered in
[`docs/runbooks/harness-first-run.md`](docs/runbooks/harness-first-run.md).
