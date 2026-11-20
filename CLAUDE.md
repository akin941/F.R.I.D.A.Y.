# CLAUDE.md — Jarvis OS Development Rules

Jarvis OS is a **personal-first** Personal AI Operating System + a platform
core that workplaces can integrate with. Workplace products are **not**
module packs built inside Jarvis: they are independent external installations
(ADR-006). Jarvis's future contact with them is an external-client relationship
only — an MCP client consuming an external installation's MCP-compatible tools
on a user's behalf (read-only first, writes behind the approval gate) — and,
long term, a fleet layer monitoring multiple external installations (MASTER_PLAN §P). "OS"
means a shared identity/memory/permission/audit layer with pluggable modules —
not a real operating system.

## Non-negotiable architecture rules

1. **Workspace scoping is inviolable.** Every business table carries
   `workspace_id`; every queryset reaching a view or agent tool passes through
   `for_actor()` (`backend/core/db.py`). Company data never leaks into personal
   scope. (ADR-001)
2. **The AI Gateway is the single door.** All LLM calls go through `ai_core`'s
   InferenceGateway — no direct provider SDK usage anywhere else. (ADR-003)
3. **Every LLM call will be recorded as ModelUsage** (tokens, cost, latency,
   purpose, workspace, prompt version, context report). Code that skips this is a
   review-blocking defect.
4. **No write/delete/deploy/external-send without the approval gate.** Agents can
   never modify their own definitions or permissions. Coder and reviewer agents
   are never the same. (ADR-004)
   Owner deletes are reversible trash moves; permanent purge is gated. (ADR-009)
5. **LLM-calling code must be mockable.** Tests that hit a real LLM are
   nightly-only.
6. **Modular monolith dependencies are one-way:** kernel apps never import
   `apps.modules.*`; module packs may import kernel apps; `ai_core` imports no
   business module. Enforced by import-linter. (ADR-002)
7. **No feature without tests.** Permission tests (role × endpoint) are part of
   every API surface.
8. **Ruff, pytest and CI must be green** before any work is declared done.
9. **Obsidian vault is a mirror, never the primary store.** Postgres is the
   source of truth. (ADR-005)

## Commands

```bash
make up / down / logs      # Docker Compose dev stack (web on :8001)
make test                  # pytest (backend/) + vitest (frontend/)
make lint                  # ruff check + format check + import-linter
make fmt                   # autoformat
make migrate / makemigrations / shell
```

Backend tooling is `uv` (Python 3.12); run backend commands as `uv run ...` from
`backend/`.

## Conventions

- Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`, `test:`).
- Branches: `feat/mvpX-<topic>` (legacy MVP track) / `feat/pN-<topic>` (Jarvis 2.0
  phases); `main` is protected — never commit to it directly.
- Secrets only via env; update `.env.example` when adding config, never real values.
- Prompt packs live in `docs/prompts/` (masters + per-MVP / per-phase briefs); ADRs in
  `docs/adr/`; governance in `docs/security/` and `docs/mcp/`.
- Reviews: every branch and every brief is reviewed by an independent Claude Code
  subagent running **Sonnet 5** before merge (the implementing model never reviews
  its own work); findings are fixed and re-reviewed until the verdict is APPROVE.
  **Codex CLI is not used in this repo's development workflow** (since
  2026-09-27). This is about how Jarvis is built — the `harness` product's own
  cross-vendor reviewer (Codex CLI via `CODEX_API_KEY`) is a separate matter.
- MCPs/external tools require the allowlist + manifest process
  (`docs/security/mcp-governance.md`) before installation.
