# MCP / External Tool Allowlist

Process: see `docs/security/mcp-governance.md`. Nothing outside this list gets
installed or wired into agents.

| Tool | Class | Scope | Status | Review |
|---|---|---|---|---|
| Graphify (CLI/skill) | local, read + writes `graphify-out/` | dev workflow only (not a Jarvis runtime dependency) | allowed | light review 2026-07-04: local code pass; docs/media extraction goes to configured LLM backend — disable media extraction or use Ollama for sensitive folders |
| `gh` CLI | read (write = human-approved) | dev workflow | allowed | light review 2026-07-04 |
| ripgrep / ast-grep | read-only local | dev workflow | allowed | light review 2026-07-04 |
| Claude Code CLI (`@anthropic-ai/claude-code`) | execute-capable, credential-holding (harness runtime) | Jarvis `harness` workers only; containerized, image built from `backend/harness/docker/claude_code.Dockerfile`; every write behind the approval gate (ADR-008 decision 3) | allowed (harness runtime) | Phase 2 container policy + code-review; API-key auth only (`ANTHROPIC_API_KEY`), Phase 3 branch 3; recorded here 2026-09-27 |
| Codex CLI (`@openai/codex`) | execute-capable, credential-holding (harness runtime) | Jarvis `harness` workers only; containerized, image built from `backend/harness/docker/codex_cli.Dockerfile`; every write behind the approval gate (ADR-008 decision 3) | allowed (harness runtime) | Phase 2 container policy + code-review; vendor terms resolved GO, API-key auth only (`CODEX_API_KEY`, never ChatGPT OAuth), Phase 3 branch 3; recorded here 2026-09-27 |

Pending / rejected: NotebookLM MCPs (unofficial, cookie-based — public data only,
re-evaluate during the Phase 5 MCP review), browser/Playwright MCP (prompt-injection
surface — not before the Phase 5 MCP review; sandboxed + domain allowlist only).

> Note (2026-09-27): the earlier MVP 9 references were replaced by Jarvis 2.0
> phase references (ADR-008; MCP client work is Phase 5, MASTER_PLAN §AA).
