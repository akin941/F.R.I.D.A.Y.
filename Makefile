# Friday OS — development commands
COMPOSE := docker compose -f deploy/docker/compose.yml
COMPOSE_DAILY := $(COMPOSE) -f deploy/docker/compose.daily.yml
COMPOSE_LOCAL_TLS := $(COMPOSE) -f deploy/docker/compose.local-tls.yml
COMPOSE_HOME := $(COMPOSE_DAILY) -f deploy/docker/compose.home.yml
BACKEND := cd backend &&
# The WSGI/ASGI/Celery entry points default to config.settings.prod (fail
# hard on a missing env) since Phase 4 branch 1, so host-side Celery runs
# name the dev settings explicitly. manage.py keeps its dev default (every
# documented `uv run python manage.py ...` still works) and pytest takes its
# settings from backend/pyproject.toml.
DEV_SETTINGS := DJANGO_SETTINGS_MODULE=$${DJANGO_SETTINGS_MODULE:-config.settings.dev}
FRONTEND := cd frontend &&

.PHONY: up up-daily up-home up-local-tls local-tls down logs secrets test lint fmt shell migrate makemigrations \
	harness-images harness-smoke harness-runner deploy-smoke preflight \
	test-backend test-frontend test-e2e lint-backend lint-frontend \
	frontend-install frontend-dev frontend-build e2e-install voice-models \
	friday-voice-test friday-voice-e2e friday-voice-install friday-voice-ca voice-models-node \
	linux-node-test linux-node-conformance linux-node-install

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

# A Friday Node on THIS Mac only (Phase 9 M1): the dev stack plus a
# loopback-only TLS proxy on 127.0.0.1:8443 (deploy/docker/compose.local-tls.yml).
local-tls:
	@sh deploy/docker/gen-local-tls.sh

up-local-tls: secrets local-tls
	$(COMPOSE_LOCAL_TLS) up -d --build

# Home server (Phase 13, ADR-017): the daily stack plus the home override
# (deploy/docker/compose.home.yml: home_* preflight settings, the backup and
# drill status files bound read-only; still nothing on 0.0.0.0). Run the
# backup and the first restore drill before the first `up-home`
# (docs/runbooks/home-server-setup.md steps 8-9).
up-home: secrets
	$(COMPOSE_HOME) up -d --build

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

# --- Wake word (Phase 6 branch 4, D6; docs/runbooks/voice-setup.md) ---
# Downloads the pinned openWakeWord + Silero models, verifies sha256 against
# frontend/src/lib/voice/model-manifest.json and writes them (plus the
# onnxruntime-web WASM runtime) to the gitignored frontend/public/voice-models/.
# The owner-trained keyword model is copied from VOICE_WAKE_MODEL_SRC.
voice-models:
	$(FRONTEND) node scripts/voice-models.mjs

# --- Node voice child (Phase 11 branch 2, brief D16) ---
# node/friday-voice is its own uv project (pyproject.toml + uv.lock); it
# imports nothing from backend/. `friday-voice-test` is the CI job locally.
# `friday-voice-install` (on the Mac) copies the package outside the signed
# app bundle and installs the locked environment there; the Swift host
# launches $(FRIDAY_VOICE_HOME)/.venv/bin/friday-voice run and refuses to
# when the installed uv.lock sha256 differs from the one compiled into it.
# FRIDAY_VOICE_CA_FILE=<pem> (install or `friday-voice-ca`) records a CA file
# the child trusts INSTEAD of the public bundle -- for the loopback Caddy of
# compose.local-tls.yml: FRIDAY_VOICE_CA_FILE=deploy/docker/local-tls/cert.pem.
# It is copied into the install (server-ca.pem + tls.json, kept across
# reinstalls); the pin and hostname checks still run. `friday-voice-ca`
# without the variable shows the record; FRIDAY_VOICE_CA_FILE=none clears it.
FRIDAY_VOICE_DIR := node/friday-voice
FRIDAY_VOICE_HOME ?= $(HOME)/Library/Application Support/Friday/voice
FRIDAY_VOICE_BIN = "$(FRIDAY_VOICE_HOME)/.venv/bin/friday-voice"

friday-voice-test:
	cd $(FRIDAY_VOICE_DIR) && uv sync --frozen && uv run ruff check . && \
		uv run ruff format --check . && uv run friday-voice conformance --check && \
		uv run python scripts/vendor_scrubber.py --check && \
		uv run python scripts/snapshot_pipecat.py --check && \
		uv run python scripts/make_fixtures.py --check && \
		uv run friday-voice conformance && uv run friday-voice replay --check && \
		uv run friday-voice selftest --ci && uv run pytest

# Phase 11 closeout: the live-voice e2e -- the reference host and the real
# child against this checkout's Django on a loopback uvicorn (FakeAdapter,
# fake speech). Needs the dev Postgres and Redis (`make up`).
friday-voice-e2e:
	cd $(FRIDAY_VOICE_DIR) && uv sync --frozen
	cd backend && FRIDAY_VOICE_E2E=1 uv run pytest ops/tests/test_live_voice_e2e.py

friday-voice-install:
	mkdir -p "$(FRIDAY_VOICE_HOME)"
	rsync -a --delete --exclude .venv --exclude __pycache__ --exclude .pytest_cache \
		--exclude .ruff_cache --exclude tests --exclude /models/ \
		--exclude /tls.json --exclude /server-ca.pem \
		$(FRIDAY_VOICE_DIR)/ "$(FRIDAY_VOICE_HOME)/"
	@# rsync -a copies the checkout's directory mode onto the home: keep the
	@# 0700 `friday-voice ca set` gave it while a CA file is recorded.
	if [ -f "$(FRIDAY_VOICE_HOME)/tls.json" ]; then chmod 700 "$(FRIDAY_VOICE_HOME)"; fi
	cd "$(FRIDAY_VOICE_HOME)" && uv sync --frozen --no-dev
	$(if $(FRIDAY_VOICE_CA_FILE),$(MAKE) friday-voice-ca)
	shasum -a 256 "$(FRIDAY_VOICE_HOME)/uv.lock"

friday-voice-ca:
ifeq ($(FRIDAY_VOICE_CA_FILE),)
	$(FRIDAY_VOICE_BIN) ca show --home "$(FRIDAY_VOICE_HOME)"
else ifeq ($(FRIDAY_VOICE_CA_FILE),none)
	$(FRIDAY_VOICE_BIN) ca clear --home "$(FRIDAY_VOICE_HOME)"
else
	$(FRIDAY_VOICE_BIN) ca set "$(FRIDAY_VOICE_CA_FILE)" --home "$(FRIDAY_VOICE_HOME)"
endif

# Runbook A10: fill the node's model directory from models.json -- download
# every entry with a URL source, verify its sha256, copy the owner-trained
# keyword model from VOICE_WAKE_MODEL_SRC (refused while its pin is empty),
# atomic writes only. `/models/` is excluded from the install rsync above so
# a reinstall never deletes it. The target defaults to models.json `modelDir`
# (the directory the child reads); VOICE_NODE_MODEL_DIR=<dir> overrides it.
voice-models-node:
	cd $(FRIDAY_VOICE_DIR) && uv sync --frozen && \
		uv run friday-voice models fetch $(if $(VOICE_NODE_MODEL_DIR),--model-dir "$(VOICE_NODE_MODEL_DIR)")

# --- Linux node (Phase 13 branch 4, brief D10) ---
# node/friday-node-linux is its own uv project; it imports nothing from
# backend/ (the reference node is vendored, `vendor.lock` guards drift).
# `linux-node-test` is the CI `linux-node` job locally (the backend tests
# that run the packaged node need the dev Postgres and Redis: `make up`).
# `linux-node-install` runs on the Linux PC as the desktop user: the locked
# environment under ~/.local/share/friday-node-linux and the systemd user unit.
LINUX_NODE_DIR := node/friday-node-linux
LINUX_NODE_HOME ?= $(HOME)/.local/share/friday-node-linux
SYSTEMD_USER_DIR ?= $(HOME)/.config/systemd/user

linux-node-test:
	cd $(LINUX_NODE_DIR) && uv sync --frozen && uv run ruff check . && \
		uv run ruff format --check . && uv run python scripts/vendor.py --check && \
		uv run pytest && uv run python scripts/external_conformance.py
	cd backend && FRIDAY_LINUX_NODE=1 uv run pytest nodes/tests/test_linux_node_external.py \
		config/tests/test_linux_node_gateway.py

linux-node-conformance:
	cd $(LINUX_NODE_DIR) && uv sync --frozen && uv run python scripts/external_conformance.py

linux-node-install:
	mkdir -p "$(LINUX_NODE_HOME)" "$(SYSTEMD_USER_DIR)"
	rsync -a --delete --exclude .venv --exclude __pycache__ --exclude .pytest_cache \
		--exclude .ruff_cache --exclude tests $(LINUX_NODE_DIR)/ "$(LINUX_NODE_HOME)/"
	cd "$(LINUX_NODE_HOME)" && uv sync --frozen --no-dev
	install -m 0644 deploy/home/friday-node-linux.service "$(SYSTEMD_USER_DIR)/"
	@echo "next: friday-node-linux pair ..., grant, then systemctl --user enable --now friday-node-linux"

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
