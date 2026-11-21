# `graphify` harness job — governance manifest

Phase 8B branch 5 (`feat/p8-graphify-job`, brief
`docs/prompts/phase-8b-transcripts-graph/brief.md` D10, R5). Graphify is not
an MCP connector and has no `backend/connectors/manifests/` entry: it is a
command-line tool that runs **only inside the harness runner**, in its own
network-denied container, over a read-only clone of a registered
`HarnessRepo`. This file follows `_template.md` where its sections apply.

## Summary
- **What:** `graphify update <path> --no-cluster` — a tree-sitter AST pass
  over a repository's code, no model call, written as `graph.json`.
- **Where it runs:** the `harness` runner only (`harness.tasks.run_graphify_run`
  on the `harness` queue), image `backend/harness/docker/graphify.Dockerfile`,
  configured by `HARNESS_IMAGE_GRAPHIFY`. Never in web, worker or a sidecar,
  never imported by the backend (`graphifyy` is not a backend dependency).
- **Trigger:** `HarnessRepo.graphify_enabled` (default off; the owner turns it
  on for the Friday repo). A new default-branch head seen by the runner-beat
  git producer starts one `GraphifyRun` — at most one per head, one in flight
  per repo.
- **Class:** read-only over the clone; its only output is data imported as
  `origin=graphify`, `status=system` rows (`memory.graph.ports.
  import_code_graph`). Not owner data (ADR-012 implementation note): no
  morning card, no embedding, no retrieval; it feeds the deterministic `self`
  line `codebase`, the `memory_graph_lookup` read tool (branch 4) and the
  read-only `/memory/graph` view (branch 9).
- **Status:** allowed (harness runtime), see `docs/mcp/allowlist.md`.

## Source and pin
- Upstream: `github.com/Graphify-Labs/graphify`, Apache-2.0, Python ≥ 3.10.
- **PyPI name `graphifyy`** (double *y*). Other `graphify*` packages are not
  affiliated (the README warns about it): a typosquat risk, so the pin is by
  name **and** hash.
- **Pin: `graphifyy==0.9.103`**, uploaded 2026-11-05, wheel
  `graphifyy-0.9.103-py3-none-any.whl`, sha256
  `7948c030f8b04153d88bc1ee23a485ddaa0c2d6aaa03a748c177e3c217bcfe34`
  (re-verified 2026-11-21 by downloading the wheel). The newest release
  ≥ 14 days old on 2026-11-21; 0.9.114 (2026-11-20) is too new.
- Every transitive wheel is hash-locked in
  `backend/harness/docker/graphify.lock.txt` for CPython 3.12 on manylinux
  x86_64 **and** aarch64 (Intel runners and Apple-silicon Docker): `networkx`,
  `numpy`, `rapidfuzz`, `pyyaml`, `tree-sitter` and its grammar packages.
  Each dependency is the newest release ≥ 14 days old that graphifyy's own
  constraints accept (2026-11-21: `numpy` 2.5.4 not 2.5.5, `pyyaml` 6.0.3 not
  6.0.4, `tree-sitter-c-sharp` 0.23.5, `tree-sitter-scala` 0.26.7). The lock
  was installed with `--require-hashes --no-deps --only-binary=:all:` into a
  CPython 3.12 venv and run over the fixture package, which produced the
  committed fixture `backend/harness/tests/fixtures/graphify/graph_0.9.103.json`.
- No LLM SDK is in the base install (a test pins that `openai`, `anthropic`,
  `litellm`, `requests`, `httpx` are absent from the lock). The wheel also
  ships a `graphify-mcp` entry point; it is never invoked.
- Base image: `python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df`
  (the multi-arch index digest, resolved from Docker Hub 2026-11-21).
- **Upgrades** only in a dedicated, reviewed branch that re-captures the
  fixture with the new version (the importer's schema pins the observed
  0.9.103 shape: an unknown top-level key is a refusal).

## Tools
| Friday name | Command | Kind | Pin | Scope check | Gate | work_log |
|---|---|---|---|---|---|---|
| `graphify` harness job | `graphify update repo --no-cluster` (in `graphify_run.sh`) | read (code-only AST pass) | `graphifyy==0.9.103` + sha256, lock file | the run's `HarnessRepo` (enabled, `graphify_enabled`), its workspace only | none: it writes nothing outside Friday's own rows | no |

Not used (and why): `graphify` without `update` (the full pipeline can send
documents and media to a configured LLM backend), clustering (`--no-cluster`:
no extra dependency path), the MCP server entry point, the query log.

## Credentials
- **None.** The container gets no env file, no `--env`, no credentials; the
  image's own `ENV` carries only `GRAPHIFY_BACKEND=none` and
  `GRAPHIFY_QUERY_LOG_DISABLE=1`. The clone uses the existing host-side
  `clone_repo` (the runner's own git credentials never enter the container).

## Sandbox and network
`harness.runner.launch_graphify_container` keeps every `launch_container`
policy and adds the Graphify ones:
- **`--network none`** — never `HARNESS_DOCKER_NETWORK`; PyPI access is
  needed at image build time only;
- `--pull never`, `--cap-drop ALL`, `--security-opt no-new-privileges`,
  `--user {host_uid}:{host_gid}`, the memory/CPU/pids caps;
- `--read-only` root, a size-capped `noexec` tmpfs `/work`
  (`HARNESS_GRAPHIFY_TMPFS_SIZE`, default `1g`) and `/tmp` (64m);
- `/repo` read-only; `/out` (a fresh `mkdtemp`, mode 0700, removed after the
  run) is the **only** writable bind;
- the entrypoint copies `/repo` to `/work/repo` without `.git` and without a
  committed `graphify-out/`, and copies only a regular, non-symlink
  `graph.json` of at most 20 MB to `/out`.

## Outbound data
- None at run time (no network). Upstream states "no telemetry"; the query
  log is disabled by env.

## Prompt-injection surface and output handling
- `graph.json` is container-written, so it is **data** and the host treats
  `/out` as hostile (`harness/safe_read.py`): `O_NOFOLLOW` open relative to
  the directory descriptor, `fstat`, refuse anything that is not a regular
  file or is over 20 MB **before reading a byte**, then read at most
  20 MB + 1 from the same descriptor.
- Schema-validated against `backend/harness/graphify_schema_v1.json`
  (bounded strings and arrays; unknown top-level key = refusal); only
  `_origin == "ast"` and `EXTRACTED` items are kept; caps 20 000 entities /
  60 000 facts. A refusal fails the run with nothing replaced.
- Every label is scrubbed (identifiers can embed tokens) and fence shapes
  are neutralised (`<`/`>` replaced) before storage; it is never executed
  and never shown unfenced.
- Size headroom: on 2026-11-21 the Friday repository's own `graph.json` was
  17.5 MB (14 554 nodes, 45 708 links; 10 493 entities and 32 344 facts after
  filtering). The 20 MB cap is close: raising it is a reviewed change.

## Logging
- Audit: `memory.graph.imported` (counts and sha), `memory.graph.
  code_graph_retired` (switch off), `memory.graph.code_graph_purged`
  (gate-class `purge_trash`, `graphify_repo_ids`), `harness.graphify_retired`.
- `GraphifyRun` keeps counts and a stable `error_type` only. Container
  stdout/stderr is never read, logged or stored.

## Review
- Branch 5 implementation, 2026-11-21: pin, hash, lock and base digest
  verified as above; independent Sonnet 5 review pending (CLAUDE.md
  "Reviews"). Re-review on any pin change, a schema change or a change to
  the launch argv.
