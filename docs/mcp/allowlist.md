# MCP / External Tool Allowlist

Process: see `docs/security/mcp-governance.md`. Nothing outside this list gets
installed or wired into agents.

| Tool | Class | Scope | Status | Review |
|---|---|---|---|---|
| Graphify (CLI/skill) | local, read + writes `graphify-out/` | dev workflow only (not a Friday runtime dependency) | allowed | light review 2026-07-04: local code pass; docs/media extraction goes to configured LLM backend — disable media extraction or use Ollama for sensitive folders |
| Graphify (`graphify` harness job, `graphifyy==0.9.103`, hash-locked) | local, read-only AST pass; output imported as `system` code-graph rows | Friday `harness` runner only; image `backend/harness/docker/graphify.Dockerfile`, `--network none`, read-only root, `/out` the only writable bind, no credentials; per-repo `graphify_enabled` switch (default off) | allowed (harness runtime) | Phase 8B branch 5, 2026-11-21; see `docs/mcp/graphify.md` |
| Headroom (`compressor` sidecar, `headroom-ai==0.43.1`, hash-locked, without `litellm`) | local, model-free transform of prior assistant messages; no egress, no credentials | Friday `compressor` compose service only (profile `compression`, image `deploy/compressor/Dockerfile`, internal network shared with web and worker, read-only, `cap_drop: ALL`, telemetry off); never imported by the backend; gateway stage off by default, per purpose, with a hard bypass set | allowed (sidecar only) | Phase 8B branch 6, 2026-11-21; see `docs/mcp/headroom.md` |
| Friday voice child (`node/friday-voice`: `pipecat-ai==1.15.0`, `onnxruntime==1.31.1`, `openwakeword==0.6.0`, `numpy==2.5.4`, `httpx==0.28.1`, `jsonschema==4.26.0`, hash-locked in its own `uv.lock`) | local, on-device classifiers only (wake word, VAD, end of turn); no model call, no provider key, no listening socket | the owner's Mac, as the `friday-node` host's stdio child (`friday-voice/v1`); outbound HTTPS to Friday's voice endpoints only (branch 3); never imported by the backend; Piper never in it | allowed (node child only) | Phase 11 branch 2, 2026-11-20; see `docs/mcp/dependencies/friday-voice.md` (step 0 pins, licences, the `openai`-via-Pipecat-core and `tflite-runtime` deviations) |
| Friday Node for Linux (`node/friday-node-linux`: `cryptography==50.0.1`, `jsonschema==4.26.0`, `wsproto==1.3.2`, the backend's pins, hash-locked in its own `uv.lock`; the reference node vendored with `vendor.lock`) | local, read-only node daemon; no model call, no MCP, no provider key; outbound TLS-pinned HTTPS (pairing) + WSS (`/ws/node/v1`) to the home server only; one 0600 Unix hook socket | a Linux PC of the owner, as a systemd user unit (`deploy/home/friday-node-linux.service`); v1 read capabilities only, directory grants off by default; never imported by the backend | allowed (Linux node only) | Phase 13 branch 4, 2026-11-20; see `docs/mcp/dependencies/friday-node-linux.md` |
| `gh` CLI | read (write = human-approved) | dev workflow | allowed | light review 2026-07-04 |
| ripgrep / ast-grep | read-only local | dev workflow | allowed | light review 2026-07-04 |
| Claude Code CLI (`@anthropic-ai/claude-code`) | execute-capable, credential-holding (harness runtime) | Friday `harness` workers only; containerized, image built from `backend/harness/docker/claude_code.Dockerfile`; every write behind the approval gate (ADR-008 decision 3) | allowed (harness runtime) | Phase 2 container policy + code-review; API-key auth only (`ANTHROPIC_API_KEY`), Phase 3 branch 3; recorded here 2026-09-27 |
| Codex CLI (`@openai/codex`) | execute-capable, credential-holding (harness runtime) | Friday `harness` workers only; containerized, image built from `backend/harness/docker/codex_cli.Dockerfile`; every write behind the approval gate (ADR-008 decision 3) | allowed (harness runtime) | Phase 2 container policy + code-review; vendor terms resolved GO, API-key auth only (`CODEX_API_KEY`, never ChatGPT OAuth), Phase 3 branch 3; recorded here 2026-09-27 |
| `github` connector (official `github-mcp-server` v1.12.2, digest-pinned) | read + one gated write (`create_pull_request` as the system-only `github_open_pr` gate), firm (`personal` per call for a `sensitive` repo) | personal + company workspaces; only repos registered as the workspace's `HarnessRepo`s; stdio server behind the bridge in its own sidecar, PAT in the sidecar env only; manifest `backend/connectors/manifests/github.py` | allowed | Phase 5 branch 5, 2026-09-28; see `docs/mcp/github.md` (schema pins re-verified against the live sidecar at first run) |
| `google` connector (Gmail + Google Calendar, native REST) | personal; reads ungated (web only), writes gated (`mail_send`, `calendar_create_event`) | personal workspace only; manifest `backend/connectors/manifests/google.py`; owner OAuth via `manage.py connect_google`; scopes ⊆ `gmail.readonly`, `gmail.send`, `calendar.readonly`, `calendar.events` | allowed | Phase 5 branch 8, 2026-09-28; see `docs/mcp/google.md` |
| `home_assistant` connector (Home Assistant native REST bridge) | personal; read + gated write (`ha_call_service`) | personal workspace only, web reads; code-bounded service/data allowlist (`backend/connectors/bridges/home_assistant.py`), narrowed by `HA_ALLOWED_DOMAINS`; since manifest v2 every entity call also needs the owner's per-entity allowlist (`connectors.HomeEntity`, owner-web only; empty = nothing callable); manifest `backend/connectors/manifests/home_assistant.py` (v3) | allowed | Phase 5 branch 7, 2026-09-28 (reconciled with the native-bridge contract 2026-11-20); v2 Phase 13 branch 5, 2026-11-20 (entity allowlist, media/TV services, new pins); v3 Phase 13b branch 1a, 2026-11-20 (`media_play_pause` with its outcome row and precondition, `media_stop`, both gate-class; new `call_service` pin); see `docs/mcp/home-assistant.md` |
| `n8n` connector (the owner's n8n "Friday tools" MCP Server Trigger behind the `n8n-relay` sidecar) | personal; one async read (`n8n_server_status`), one gated write (`mcp_n8n_home_backup`) | personal workspace only, reads over web + node_voice; Friday-side workflow-ID allowlist (`WORKFLOWS`, `wf_<id>` tools only), relay ceiling `BRIDGE_ALLOWED_TOOLS`, pinned schemas; bearer only in the sidecar env (`RELAY_BEARER_TOKEN`); never n8n's instance-level MCP; manifest `backend/connectors/manifests/n8n.py` | allowed | Phase 12 branch 4, 2026-11-20; see `docs/mcp/n8n.md` (live pins and the n8n licence confirmation are owner steps) |
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
