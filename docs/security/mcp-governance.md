# MCP / External Tool Governance

Default answer is **no**: an MCP or external tool that is not on the allowlist
(`docs/mcp/allowlist.md`) is not installed. MCPs can hold file/mail/browser/shell/
GitHub powers; treat every installation as a security decision.

## Permission manifest (required before install, one file per tool in docs/mcp/)

- Source & maintenance: official/community, activity, version pinned?
- Capability class: read-only / write-capable / execute-capable
- Access surface: which paths/services; can it be path-jailed?
- Outbound data: does it send data anywhere? telemetry?
- Credentials: token/cookie/OAuth? minimal scopes? storage?
- Sandbox: can it run isolated?
- Logging: can calls be tied into Jarvis audit?
- Scope: which workspaces, which agents, sensitive data allowed? (default: no)
- Review: who reviewed, when.

## Binding rules

1. Write/execute-capable or credential-holding tools require a security review
   before install; read-only local tools get a light review.
2. Unofficial browser-automation / cookie-based MCPs (e.g. NotebookLM MCPs) are
   **never** enabled for sensitive or company workspaces; public/educational
   documents only, on a separate Google account.
3. MCP write actions cannot bypass the Jarvis approval gate — MCPs are wrapped by
   the tool registry, never wired raw into production agents.
4. Every MCP call is logged; unmaintained tools get removed at the periodic
   re-review (first pass scheduled with the Phase 4 security baseline).

> Note (2026-09-27): there is no `ToolCall` model in the codebase. MCP calls
> will be logged through the `audit` app, plus `ModelUsage` where an LLM call
> is involved; the concrete logging design belongs to the Phase 5 brief (MCP
> client work, MASTER_PLAN §AA). The earlier MVP numbering in this document
> was replaced by Jarvis 2.0 phase references (ADR-008).
