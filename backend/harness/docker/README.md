# Harness runner images

Two Dockerfiles, one per supported CLI agent (`claude_code.Dockerfile`,
`codex_cli.Dockerfile`), per `docs/prompts/phase-2-harness-v1/brief.md`'s
container policy. **Not built, pushed, or deployed by any automation in
this branch** — that is deliberate scoping, agreed with the repo owner
when this branch started, not an oversight:

- The Python code that launches a container by image name/tag
  (`harness.runner.launch_container`, configured via
  `HARNESS_IMAGE_CLAUDE_CODE`/`HARNESS_IMAGE_CODEX_CLI`) is complete and
  fully tested via mocks — no test in this repo depends on either image
  actually existing.
- Both the CLI version (`ARG CLAUDE_CODE_VERSION` / `ARG CODEX_CLI_VERSION`)
  and the base image (`FROM node@sha256:00...00`, Codex code-review P2,
  branch 2 round 3 -- a mutable tag alone doesn't satisfy the container
  policy's "pinned to a specific, recorded base-image digest" requirement)
  are **placeholders**, not values verified against a real current
  release. Building either image for real use requires:
  1. Checking the current CLI release at
     `https://www.npmjs.com/package/@anthropic-ai/claude-code` /
     `https://www.npmjs.com/package/@openai/codex` and updating the `ARG`
     default to a real, current version.
  1b. Resolving the real base-image digest --
      `docker pull node:20.11.1-slim && docker inspect --format='{{index .RepoDigests 0}}' node:20.11.1-slim`
      (or whatever tag the reviewed bump cadence selects) -- and replacing
      the placeholder `FROM node@sha256:...` line with it.
  2. `docker build -f claude_code.Dockerfile -t <image:tag> .`, run from
     **inside this directory** (`backend/harness/docker/`) -- the build
     context must be this directory, not the repo/backend root: the
     Dockerfile's `COPY claude_code_run.sh ...` (and, since Phase 3
     branch 5, `COPY harness_write_mode.sh ...`) expects both scripts at
     the context root (Codex code-review, branch 2 round 1: an
     earlier version of this README documented a repo-root build context,
     which would have made the `COPY` fail before the image could ever be
     used). Same command for `codex_cli.Dockerfile`.
  3. A manual smoke run against a real, disposable test repo — this is
     the "reviewed bump/rebuild cadence" the brief itself requires, not
     an automated CI step (Codex code-review, branch 2 round 4).
  4. Setting `HARNESS_IMAGE_CLAUDE_CODE`/`HARNESS_IMAGE_CODEX_CLI` (env)
     to the built tag.
- Codex CLI's image exists so the vendor-terms/cost-reporting
  verification this branch's O-2/R6 items require has something real to
  test against — it is **not** wired as a selectable `HarnessJob.agent`
  until that verification separately clears it (the per-job-kind
  `selectable` lists of `HARNESS_AGENT_POLICY`, `config/settings/base.py`;
  Codex CLI is selectable only as the cross-vendor reviewer).
- The restricted-egress Docker network (`HARNESS_DOCKER_NETWORK`) referenced
  by `harness.runner.launch_container` is runner setup, not built by this
  branch either — provisioning it (a bridge network with an egress
  allowlist, or firewall rules scoped to the container) is the same kind
  of owner-run infra work as building the images above.

## Write mode (Phase 3 branch 1)

Both wrapper scripts (`claude_code_run.sh`, `codex_cli_run.sh`) now branch
on whether `--test-command` is present in their argv:

- **Absent** (Phase 2, unchanged): the existing read-only discovery/review
  invocation, `exec`'d directly — behavior identical to before this branch.
- **Present** (Phase 3, `HarnessImplementationJob` only): the CLI agent
  runs with real write access, then the wrapper runs the configured test
  command itself, forcibly quiesces every other process in the container
  (`docs/prompts/phase-3-harness-v2/brief.md` §boundary, rounds 6-8), and
  writes a trusted result file to `/workspace/result.json` — the *only*
  thing the host ever reads for this job's outcome, never container
  stdout/stderr.

**Two more placeholder values need real vendor verification before write
mode ships for real**, same discipline as the CLI version/base-image
digest above: Claude Code's `--permission-mode acceptEdits` and Codex
CLI's `--sandbox workspace-write` are this branch's best-grounded guesses
for "unattended, non-interactive write access", not yet confirmed against
either vendor's current documentation. Confirm both (or correct them)
before building either image for real write-mode use.

## Write mode since Phase 3 branch 5 (2026-09-27)

The write-mode sequence now lives in one shared, tested library,
`harness_write_mode.sh` (sourced by both wrappers; real-git tests in
`backend/harness/tests/test_write_mode_wrapper.py`). Its header comment is
the authoritative description; the operator-facing consequences are:

- **The wrapper owns the commit.** The agent does not need (and is not
  trusted) to `git commit`; after the CLI exits and the container is
  quiesced, the wrapper restores the clone's pristine `.git/config`,
  drops `.git/info`/hooks, rebuilds the index and commits everything the
  agent left in the tree (committed `.gitignore` rules still apply).
- **A run that changes nothing fails as `no_changes`.** Tests don't run.
- **`test_command` runs against a pristine checkout of the produced
  commit** (`git clean -ffdx` + `git reset --hard HEAD` first). A fresh
  clone has no ignored files, so **the test command must provision its
  own dependencies** — e.g. `npm ci && npm test` or
  `uv sync --frozen && uv run pytest`, not a bare `pytest` that relies on
  something the agent installed during its session. The container's
  egress network must allow the package registries the test command
  needs.
- **The test command never sees the CLI API keys** (`ANTHROPIC_API_KEY`,
  `CODEX_API_KEY`, `OPENAI_API_KEY` are unset for it), and the host scans
  the produced diff for the literal key values before review — a match
  fails the job as `secret_leak`.
- **Cost/usage come from one wrapper-framed line only**
  (`FRIDAY_HARNESS_CLI_OUTPUT_V1 <base64>`, emitted after the final
  quiescence); a missing/malformed frame fails the job as `runner_error`.
- **`HARNESS_SELF_REPO_IDENTIFIERS` is required.** While it is empty every
  implementation job is refused (ADR-004 self-modification guard fails
  closed); set it to this installation's own remote(s).
- Containers run with `--cap-drop ALL` and
  `--security-opt no-new-privileges` in addition to the existing
  network/resource limits.

## Driver mode (Phase 10 branch 6, 2026-11-20)

`codex_cli_run.sh --driver app_server` (launched by
`harness.runner.launch_interactive_container`, `docker run -i`, never
detached) runs `codex app-server` with the container's stdin/stdout as the
JSON-RPC transport to the host driver (`backend/harness/drivers/`); its
stderr is dropped. `harness_write_mode.sh` does not capture the agent's
stdout in driver mode (`harness_driver_mode=true`); when the host closes
stdin the app-server exits and the unchanged write-mode sequence runs
(quiesce, wrapper commit, pristine test run, result file, an empty final
frame). Off unless `HARNESS_DRIVERS_ENABLED=true`,
`HARNESS_DRIVER_CODEX=app_server` and the owner's terms check (d4) is
recorded in `HARNESS_DRIVER_TERMS_CHECKED_ON`.

Pins: `ARG CODEX_CLI_VERSION` is also the app-server pin. It must equal
`HARNESS_CODEX_APP_SERVER_VERSION` and the version of
`backend/harness/drivers/codex_schema/<version>/protocol.json` (system
check `harness.E004`). The committed snapshot for the placeholder `0.1.0`
is a hand-written **provisional** subset; after building the image for a
real version, run `./generate_driver_schemas.sh codex <image>` from this
directory (it runs `codex app-server generate-json-schema` inside the
image with `--network none` and converts the output with
`codex_schema_subset.py`), then the contract test
`harness/tests/test_driver_contract.py`.

## Graphify image (Phase 8B branch 5, 2026-11-21)

`graphify.Dockerfile` builds the Graphify AST-only code-graph image used by
`harness.runner.launch_graphify_container` (governance:
`docs/mcp/graphify.md`). Unlike the two agent images above, its pins are
**real** values, verified 2026-11-21:

- base `python:3.12-slim-bookworm` by its multi-arch index digest;
- `graphifyy==0.9.103` by name, version and sha256, with every transitive
  wheel hash-locked in `graphify.lock.txt` for CPython 3.12 on manylinux
  x86_64 and aarch64; installed with `--require-hashes --no-deps
  --only-binary=:all:` (wheels only, no install scripts).

Build it on the runner host, from inside this directory (PyPI access is
needed at build time only):

```bash
docker build -f graphify.Dockerfile -t friday-harness-graphify:0.9.103 .
```

and point `HARNESS_IMAGE_GRAPHIFY` at the tag if it differs. At run time the
container gets `--network none`, `--read-only`, a size-capped noexec tmpfs
`/work` (`HARNESS_GRAPHIFY_TMPFS_SIZE`), `/repo` read-only and `/out` as the
only writable bind; no env file, no credentials. `graphify_run.sh` copies
the clone without `.git`, runs `graphify update repo --no-cluster` and copies
a regular `graph.json` of at most 20 MB to `/out`; the host re-checks it
(`harness/safe_read.py`). An opt-in test builds and runs the real image over
the fixture package:

```bash
HARNESS_GRAPHIFY_IMAGE_TEST=1 uv run pytest harness/tests/test_graphify.py -k real_image
```

Re-lock (a dedicated, reviewed upgrade branch only): pick the newest
releases that are at least 14 days old, regenerate `graphify.lock.txt` with
every wheel hash for both architectures, re-capture
`harness/tests/fixtures/graphify/graph_<version>.json` with the new version
over `fixtures/graphify/package_v1`, and update `docs/mcp/graphify.md`.
