# Friday OS — development commands
COMPOSE := docker compose -f deploy/docker/compose.yml
COMPOSE_DAILY := $(COMPOSE) -f deploy/docker/compose.daily.yml
BACKEND := cd backend &&
# The WSGI/ASGI/Celery entry points default to config.settings.prod (fail
# hard on a missing env) since Phase 4 branch 1, so host-side Celery runs
# name the dev settings explicitly. manage.py keeps its dev default (every
# documented `uv run python manage.py ...` still works) and pytest takes its
# settings from backend/pyproject.toml.
DEV_SETTINGS := DJANGO_SETTINGS_MODULE=$${DJANGO_SETTINGS_MODULE:-config.settings.dev}
FRONTEND := cd frontend &&

.PHONY: up up-daily down logs secrets test lint fmt shell migrate makemigrations \
	harness-images harness-smoke harness-runner deploy-smoke preflight \
	test-backend test-frontend test-e2e lint-backend lint-frontend \
	frontend-install frontend-dev frontend-build e2e-install

# Generates any missing secret into the gitignored deploy/docker/.env
# (never overwrites an existing key). `make up` runs it first, so a fresh
# clone needs no manual step.
secrets:
	@sh deploy/docker/gen-secrets.sh deploy/docker/.env

up: secrets
	$(COMPOSE) up -d --build

# Daily-driver stack: gunicorn + prod settings + `next start` (see
# deploy/docker/compose.daily.yml).
up-daily: secrets
	$(COMPOSE_DAILY) up -d --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100

test: test-backend test-frontend

test-backend:
	$(BACKEND) uv run pytest

test-frontend:
	$(FRONTEND) npm test

# Requires the backend running first (`make up`); Playwright starts/reuses
# the frontend dev server itself (see frontend/playwright.config.ts).
test-e2e:
	$(FRONTEND) npm run test:e2e

e2e-install:
	$(FRONTEND) npm run test:e2e:install

lint: lint-backend lint-frontend

lint-backend:
	$(BACKEND) uv run ruff check . && uv run ruff format --check . && uv run lint-imports

lint-frontend:
	$(FRONTEND) npm run lint && npm run typecheck

fmt:
	$(BACKEND) uv run ruff format . && uv run ruff check --fix .

shell:
	$(BACKEND) uv run python manage.py shell

migrate:
	$(BACKEND) uv run python manage.py migrate

makemigrations:
	$(BACKEND) uv run python manage.py makemigrations

frontend-install:
	$(FRONTEND) npm ci

frontend-dev:
	$(FRONTEND) npm run dev

frontend-build:
	$(FRONTEND) npm run build

# --- Harness runner (Phase 3 branch 8; docs/runbooks/harness-first-run.md) ---
# Builds both CLI images. Pin the real base-image digest and CLI versions in
# the Dockerfiles first (they ship with placeholders on purpose).
HARNESS_IMAGE_CLAUDE_CODE ?= friday-harness-claude-code:local
HARNESS_IMAGE_CODEX_CLI ?= friday-harness-codex-cli:local

harness-images:
	cd backend/harness/docker && docker build -f claude_code.Dockerfile -t $(HARNESS_IMAGE_CLAUDE_CODE) .
	cd backend/harness/docker && docker build -f codex_cli.Dockerfile -t $(HARNESS_IMAGE_CODEX_CLI) .

# Real-container smoke: the real Claude Code image with its CLI stubbed out
# (no paid call), then the write-mode round trip through actual Docker.
harness-smoke:
	cd backend/harness/docker/smoke && docker build --build-arg BASE_IMAGE=$(HARNESS_IMAGE_CLAUDE_CODE) -f smoke.Dockerfile -t friday-harness-smoke:local .
	$(BACKEND) HARNESS_DOCKER_SMOKE_IMAGE=friday-harness-smoke:local uv run pytest harness/tests/test_docker_smoke.py -q

# The runner worker: consumes ONLY the harness and deploy queues, on this
# host (it needs Docker and the images for harness jobs, and holds
# DEPLOY_SSH_KEY_PATH for deploys), and runs its own embedded beat that
# schedules ONLY the harness and deploy recovery scans (the compose beat
# never does). One runner at a time: a second `-B` runner would
# double-schedule the scans.
harness-runner:
	$(BACKEND) $(DEV_SETTINGS) HARNESS_RUNNER_BEAT=true uv run celery -A config worker -B -s /tmp/friday-harness-beat-schedule -Q $${HARNESS_CELERY_QUEUE:-harness},$${DEPLOY_CELERY_QUEUE:-deploy} -l info -n harness-runner@%h

# Real-SSH deploy smoke (Phase 5 branch 6; manual/nightly): an sshd container
# running the real deploy/remote/friday-deploy.sh as a forced command against
# a dummy compose app -- status, deploy, forced health failure, rollback,
# refused non-merged SHA. Needs Docker and a test database (DATABASE_URL).
deploy-smoke:
	./deploy/remote/smoke/run-smoke.sh

# Live end-to-end checks of the running stack (Phase 5 closeout): one line per
# check, then `N pass / N fail / N warn`; non-zero exit on any failure. Runs
# inside `web` (its env reaches Postgres/Redis/the providers). Pass
# PREFLIGHT_ARGS=--no-model-calls to skip the per-provider gateway calls.
preflight:
	$(COMPOSE) exec web uv run python manage.py preflight $(PREFLIGHT_ARGS)
