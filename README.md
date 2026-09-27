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

Django modular monolith · PostgreSQL + pgvector · Redis + Celery ·
Next.js (MVP 1+) · Docker Compose.

## Quickstart (development)

```bash
cp .env.example .env      # fill in what you need
make up                   # start postgres/redis/web/worker (runs `make secrets` first)
make test                 # backend + frontend tests
make lint                 # backend (ruff + import-linter) + frontend (eslint + tsc)

# frontend (separate Node process, see frontend/README.md)
cp frontend/.env.example frontend/.env.local
make frontend-install
make frontend-dev         # http://localhost:3001

# daily-driver stack: gunicorn + prod settings + `next start` on :3001
make up-daily

# e2e smoke test (Playwright, real backend + FakeAdapter — requires `make up` first)
make e2e-install           # one-time: downloads the Chromium browser
make test-e2e              # boots/reuses the frontend dev server, runs the suite
```

## Stack secrets

The compose stack publishes every port on `127.0.0.1` only and commits no
secret: `make secrets` (run automatically by `make up`) writes a random
`DJANGO_SECRET_KEY`, `POSTGRES_PASSWORD` and `REDIS_PASSWORD` into the
gitignored `deploy/docker/.env` — only for keys that are missing, never
overwriting. Host-side commands (`make test`, `make harness-runner`,
`uv run python manage.py …` from `backend/`) derive their default
`DATABASE_URL`/`REDIS_URL` from that file, so nothing needs copying. See
`deploy/docker/.env.example` for the optional keys (including the e2e-run
block). A stack created before this change keeps its old Postgres password
inside the volume — `make secrets` detects that, keeps it working and prints
how to rotate it.

## First run

Jarvis is a single-owner system (ADR-008) with device-trust identity (Phase 4
branch 2): there is no signup and no password login. Create the one owner
account once, after `make migrate`, then trust your first browser with a
one-time pairing code from the host shell:

```bash
cd backend
JARVIS_OWNER_PASSWORD=... uv run python manage.py bootstrap_owner --email you@example.com [--name "..."]
uv run python manage.py issue_pairing_code            # 8-digit PIN, 5 min, LAN only
# or: issue_pairing_code --strong                     # strong code, 30 min, any network
```

Open the web app (`/enroll`), enter the code and a device name. Further
devices are paired from a trusted session (Entegrasyonlar → Cihazlar → "Yeni
cihaz eşleştir"); from outside the LAN use a strong code or the remote access
key (`manage.py rotate_remote_key`). Lost every device? Host shell +
`issue_pairing_code` always recovers; `manage.py revoke_all_devices` cuts off a
stolen one.

The password comes from `JARVIS_OWNER_PASSWORD` or an interactive prompt — never a
CLI argument — and is only a Django-admin break-glass credential. The command
refuses to run if any user already exists (`--if-absent` makes a re-run a no-op);
an existing database with several users needs `manage.py set_owner --email ...`.
It also creates the owner's personal workspace.

**Upgrading an existing installation:** every browser session from before
device trust (a JWT without a device) stops working on deploy, including your
own — re-enter through `/enroll` with a code from `manage.py issue_pairing_code`.
Behind a reverse proxy, set `TRUSTED_CLIENT_IP_HEADER` (e.g. `X-Real-IP` with
Caddy's `header_up X-Real-IP {remote_host}`) and `JARVIS_BFF_SHARED_SECRET` for
the frontend so PINs are LAN-gated; without one, production refuses PINs — pair
with `issue_pairing_code --strong`.

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
