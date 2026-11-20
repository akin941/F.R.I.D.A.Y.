# MCP / External Tool Allowlist

Process: see `docs/security/mcp-governance.md`. Nothing outside this list gets
installed or wired into agents.

| Tool | Class | Scope | Status | Review |
|---|---|---|---|---|
| Graphify (CLI/skill) | local, read + writes `graphify-out/` | dev workflow only (not a Friday runtime dependency) | allowed | light review 2026-07-04: local code pass; docs/media extraction goes to configured LLM backend — disable media extraction or use Ollama for sensitive folders |
| `gh` CLI | read (write = human-approved) | dev workflow | allowed | light review 2026-07-04 |
| ripgrep / ast-grep | read-only local | dev workflow | allowed | light review 2026-07-04 |
| Claude Code CLI (`@anthropic-ai/claude-code`) | execute-capable, credential-holding (harness runtime) | Friday `harness` workers only; containerized, image built from `backend/harness/docker/claude_code.Dockerfile`; every write behind the approval gate (ADR-008 decision 3) | allowed (harness runtime) | Phase 2 container policy + code-review; API-key auth only (`ANTHROPIC_API_KEY`), Phase 3 branch 3; recorded here 2026-09-27 |
| Codex CLI (`@openai/codex`) | execute-capable, credential-holding (harness runtime) | Friday `harness` workers only; containerized, image built from `backend/harness/docker/codex_cli.Dockerfile`; every write behind the approval gate (ADR-008 decision 3) | allowed (harness runtime) | Phase 2 container policy + code-review; vendor terms resolved GO, API-key auth only (`CODEX_API_KEY`, never ChatGPT OAuth), Phase 3 branch 3; recorded here 2026-09-27 |
| `github` connector (official `github-mcp-server` v1.12.2, digest-pinned) | read + one gated write (`create_pull_request` as the system-only `github_open_pr` gate), firm (`personal` per call for a `sensitive` repo) | personal + company workspaces; only repos registered as the workspace's `HarnessRepo`s; stdio server behind the bridge in its own sidecar, PAT in the sidecar env only; manifest `backend/connectors/manifests/github.py` | allowed | Phase 5 branch 5, 2026-09-28; see `docs/mcp/github.md` (schema pins re-verified against the live sidecar at first run) |
| `google` connector (Gmail + Google Calendar, native REST) | personal; reads ungated (web only), writes gated (`mail_send`, `calendar_create_event`) | personal workspace only; manifest `backend/connectors/manifests/google.py`; owner OAuth via `manage.py connect_google`; scopes ⊆ `gmail.readonly`, `gmail.send`, `calendar.readonly`, `calendar.events` | allowed | Phase 5 branch 8, 2026-09-28; see `docs/mcp/google.md` |
| `home_assistant` connector (Home Assistant native REST bridge) | personal; read + gated write (`ha_call_service`) | personal workspace only, web reads; code-bounded service/data allowlist (`backend/connectors/bridges/home_assistant.py`), narrowed by `HA_ALLOWED_DOMAINS`; manifest `backend/connectors/manifests/home_assistant.py` | allowed | Phase 5 branch 7, 2026-09-28 (reconciled with the native-bridge contract 2026-11-20); see `docs/mcp/home-assistant.md` |
| `external_installation` connector (external installation MCP) | read-only (planned), firm | company workspaces; manifest `backend/connectors/manifests/external_installation.py` | **blocked** -- cannot bind (O-5, ADR-006) | Phase 5 branch 4 placeholder, 2026-09-27; see `docs/mcp/external_installation.md` |

Runtime connectors (Phase 5, ADR-010) are rows named `` `<name>` connector ``
and each has a manifest in `backend/connectors/manifests/` and a twin in
`docs/mcp/<name>.md`; see `docs/security/mcp-governance.md` "Runtime
connectors".

Rejected for Friday runtime (Phase 5 closeout governance pass, 2026-11-20):

- **NotebookLM MCPs** stay rejected for runtime: every available server is
  unofficial and authenticates with a scraped Google session cookie, which
  no manifest can pin, scope or rotate, and Google offers no supported
  API behind it. Binding rule 2 of `docs/security/mcp-governance.md`
  still allows public/educational documents on a separate Google account
  as a manual dev tool outside Friday; re-open only if Google ships an
  official API or MCP server.
- **Browser/Playwright MCP** stays rejected for runtime: a page is fully
  attacker-authored input, and a browser tool turns it into actions
  (clicks, form posts, downloads) that the approval gate cannot snapshot
  as an exact payload (D6). Re-open only with a new ADR that defines a
  sandboxed, domain-allowlisted, read-only profile.

> Note (2026-09-27): the earlier MVP 9 references were replaced by Friday 2.0
> phase references (ADR-008; MCP client work is Phase 5, MASTER_PLAN §AA).
