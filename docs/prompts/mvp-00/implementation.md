# MVP 0 — Implementation Prompt (executed 2026-07-04)

This is the prompt that produced the MVP 0 foundation, kept for the record.

Scope: repo init, directory tree, backend Django scaffold (config + core only),
Docker Compose dev stack (web/worker/postgres[pgvector]/redis/minio with
healthchecks), Dockerfile.dev, Makefile, CI (ruff, format, import-linter,
makemigrations --check, pytest), pre-commit, `.gitignore`, `.env.example`,
ADR-001..005, governance stubs (obsidian-vault-strategy, token-optimization,
mcp-governance, mcp allowlist), prompt pack masters, CLAUDE.md, CODEX.md,
frontend/cli README placeholders. **No feature code** — auth, chat, memory, RAG,
agents and UI all start at MVP 1.

Deviations recorded during execution:
- Host ports shifted (web 8001, postgres 5433, redis 6380) to avoid clashing
  with other local stacks that commonly occupy the defaults.
- Tooling: `uv` manages Python 3.12 both locally and in the dev image
  (UV_PROJECT_ENVIRONMENT=/opt/venv so the bind mount doesn't shadow the venv).
- MVP 0 tests intentionally require no database; sqlite fallback exists only so
  the scaffold runs without services (DATABASE_URL becomes mandatory in MVP 1).
