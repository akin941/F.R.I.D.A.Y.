# MCP / External Tool Allowlist

Process: see `docs/security/mcp-governance.md`. Nothing outside this list gets
installed or wired into agents.

| Tool | Class | Scope | Status | Review |
|---|---|---|---|---|
| Graphify (CLI/skill) | local, read + writes `graphify-out/` | dev workflow only (not a Jarvis runtime dependency) | allowed | light review 2026-07-04: local code pass; docs/media extraction goes to configured LLM backend — disable media extraction or use Ollama for sensitive folders |
| `gh` CLI | read (write = human-approved) | dev workflow | allowed | light review 2026-07-04 |
| ripgrep / ast-grep | read-only local | dev workflow | allowed | light review 2026-07-04 |

Pending / rejected: NotebookLM MCPs (unofficial, cookie-based — public data only,
re-evaluate at MVP 9), browser/Playwright MCP (prompt-injection surface — MVP 9+,
sandboxed + domain allowlist only).
