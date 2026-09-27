# `github` connector — GitHub (official `github-mcp-server`)

## Summary
- **Manifest:** `backend/connectors/manifests/github.py`, version 1
- **Kind:** `mcp` — stdio server behind the Jarvis stdio↔HTTP bridge, in its
  own sidecar (brief D1/D7, Step-0 spike)
- **Status:** active
- **Data class:** `firm`; a call naming a `sensitive` `HarnessRepo` is
  `personal` for that call (web only, personal-data providers only, marks
  the conversation `private` + `personal_data`) — `connectors.github.classify`
- **Workspace types:** personal and company (the scope check limits each
  workspace to its own registered repositories)
- **Read channels:** web, cli (never telegram or openai; `personal` calls web only)

## Source and maintenance
- Upstream: `github/github-mcp-server` (official, GitHub; MIT). Actively
  maintained; tool names/schemas change across minor releases (e.g. the
  workflow-run tools were consolidated into `actions_list`/`actions_get`,
  `perPage` added in v1.12.0) — hence the pins.
- Image: `ghcr.io/github/github-mcp-server:v1.12.2@sha256:508a0857ec762b1ab1cece29193345b501fab1dd9d1228a7b617062954cecac6`
  (`MANIFEST.sidecar_image`). The binary is copied into the bridge image
  (`backend/connectors/docker/Dockerfile.github`); bumping the digest is a
  reviewed commit that re-records every schema pin below.
- The server's HTTP mode takes the PAT from each request's `Authorization`
  header — Jarvis would hold the token, which D1 forbids — so it runs in
  **stdio mode**; GitHub's hosted endpoint is not used (its tool limits are
  client-sent headers).
- MCP protocol version: recorded per binding in `last_health` on each pin check.

## Tools
| Jarvis name | Server tool | Kind | Schema pin (sha256) | Scope check | Gate / user-invocable | work_log |
|---|---|---|---|---|---|---|
| `github_pr_status` | `pull_request_read` (`method` ∈ `get`, `get_status`, `get_check_runs`) | read | `3ae9d2e56991de3df6adef7492f2c64088f871df10e3a3a2ef741c42e65974d5` | owner/repo → workspace `HarnessRepo` | — | — |
| `github_ci_status` | `actions_list` (`method` = `list_workflow_runs`) | read | `ad9af0e02784afe169f4b9f507dd365a4a72a1238f2e7123396ec7d47a15b441` | same | — | — |
| `github_branch_head` | `get_commit` (`include_diff` forced false) | read | `fd744e8ba4cd547352955d0f8c58ea1f136d4cd765516ad19a5c899440c11091` | same | — | — |
| `github_open_pr` | `create_pull_request` | write | `b40cba45097ff0ed7171b16adfff2053e530a3652fbd8cd1664bd8f2c031ba6f` | same | `github_open_pr` / **no** (system-created by the harness from a `succeeded` job) | yes |

**Pin provenance.** The pinned `inputSchema`s (`SERVER_SCHEMAS` in the
manifest) were written for v1.12.2 in branch 5 without a live server (no
real GitHub in tests). The first pin check against the real sidecar is
authoritative: any tool whose live schema differs is disabled
(`mismatch`, `connector.pin_mismatch` audit) until a reviewed commit
records the live schema — fail closed, never silently accepted. First-run
step: enable the binding, read the binding's pin report, and if needed
update `SERVER_SCHEMAS` from the sidecar's `tools/list`.

`github_open_pr` specifics: `reviewers` is always `[]` and
`maintainer_can_modify` always `false` (the serializer refuses anything
else; both shown in the dialog as fixed rows). The payload snapshots repo,
`head = branch_name`, `base = repo_default_branch`, an owner-editable title,
a Jarvis-authored body (goal, the diff summary **labelled model-written**,
test result, review verdict) and, as `context` (never sent),
`head_sha = produced_commit_sha` plus the opaque
`origin_ref = harness_implementation_job:<id>`. After the claim and before
the one send the task re-reads the head of `head` (`get_commit`); unless it
equals `head_sha` the execution ends `failed`/`head_moved` and nothing is
sent.

Tools the server offers that are deliberately NOT listed (and why):
`merge_pull_request`, `update_pull_request*`, `create_or_update_file`,
`push_files`, `delete_file`, `create_branch`, `create_repository`, fork,
issue and comment mutations, `run_workflow`/`rerun_*`/`cancel_*` workflow
triggers — every mutation except opening a PR (R6: the PAT could merge via
REST; the tool is unreachable). `actions_get`, `list_branches`,
`list_commits`, `list_pull_requests` (named in the Step-0 spike) are not
listed in v1 — no Jarvis surface needs them yet; adding one is a new
`ToolPolicy`, pin and twin row. Diffs, files and comments (`pull_request_read`
methods `get_diff`, `get_files`, `get_*comments`, `get_reviews`) are refused
by the argument serializer: attacker-authored text with no Jarvis use.

Server-side ceiling: the sidecar starts
`github-mcp-server stdio --tools=pull_request_read,actions_list,get_commit,create_pull_request`
and the bridge's `BRIDGE_ALLOWED_TOOLS` repeats the list, so unlisted tools
are not even registered (not `--read-only`, which would drop
`create_pull_request`). A server that lists them anyway is still ignored by
the manifest (tested).

## Credentials
- Sidecar only: `GITHUB_PERSONAL_ACCESS_TOKEN` (from `deploy/docker/.env`,
  passed to the child via `BRIDGE_ENV_PASSTHROUGH`). Jarvis-side: only the
  URL `GITHUB_MCP_URL` (e.g. `http://github-mcp:8080/mcp`).
- Fine-grained PAT, **only** the `HarnessRepo`-registered repositories:
  `contents: read`, `pull_requests: write`, `actions: read`,
  `metadata: read`. 90-day expiry; rotate by replacing the env value and
  restarting the sidecar. Branch protection on the default branch is
  recommended (R6).
- Never in Postgres, a payload, an audit row or a log line.

## Sandbox and network
- Inherits `x-connector-sidecar` (read-only rootfs, `cap_drop: ALL`,
  `no-new-privileges`, no published ports, tmpfs `/tmp`); joins
  `connectors` (internal) and `connectors-egress` (it must reach
  `api.github.com`).
- stdio bridge: `BRIDGE_ALLOWED_TOOLS` as above; `BRIDGE_ENV_PASSTHROUGH=GITHUB_PERSONAL_ACCESS_TOKEN`.

## Outbound data
- Jarvis sends only the validated arguments: owner/repo (always a
  registered repo), PR number, branch name or SHA, and for the one write
  the PR title/body (the job's goal, model-written diff summary, test and
  review lines). Goes to GitHub under the owner's PAT.
- A `sensitive` repo's reads are `personal`: never sent to a provider that
  is not `personal_data_allowed`.

## Prompt-injection surface
- PR titles, commit messages, workflow names are attacker-reachable text
  (anyone who can push or open a PR). Chat reads are fenced
  (`<connector_result source="github">`), stripped and bounded (D4).
  Jarvis-internal reads (harness PR panel, deploy snapshot) parse JSON into
  bounded fields and never reach a prompt; the deploy snapshot's commit
  title is shown labelled as repo text.

## Logging
- `connector.read` for every read, including the harness panel's CI read
  and the deploy snapshot's commit/CI reads (actor, channel, args sha256,
  size, outcome, `data_class` when the call is classified).
- `connector.write.{succeeded,failed,unknown_outcome}` for `github_open_pr`
  (`error_type` `head_moved` when the head moved), `connector.pin_mismatch`,
  `harness.pull_request.requested`, and the approval's own audit trail. The
  `get_commit` head check `github_open_pr` makes before its send is a write
  precondition, not a read: it has no `connector.read` row of its own and
  is recorded only through the write's terminal `connector.write.*` row
  (its `error_type`).

## Review
- Branch 5 implementation, 2026-09-28; independent Sonnet 5 review pending
  (merge gate). Re-review triggers: image digest bump, any pin change, a new
  tool, PAT scope change.
