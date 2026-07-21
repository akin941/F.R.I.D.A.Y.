# Jarvis OS — development commands
COMPOSE := docker compose -f deploy/docker/compose.yml
BACKEND := cd backend &&
FRONTEND := cd frontend &&

.PHONY: up down logs test lint fmt shell migrate makemigrations \
	test-backend test-frontend test-e2e lint-backend lint-frontend \
	frontend-install frontend-dev frontend-build e2e-install

up:
	$(COMPOSE) up -d --build

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
