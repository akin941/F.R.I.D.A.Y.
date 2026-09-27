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
  until that verification separately clears it
  (`harness/services.py`'s `_SELECTABLE_AGENTS`).
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
  (`JARVIS_HARNESS_CLI_OUTPUT_V1 <base64>`, emitted after the final
  quiescence); a missing/malformed frame fails the job as `runner_error`.
- **`HARNESS_SELF_REPO_IDENTIFIERS` is required.** While it is empty every
  implementation job is refused (ADR-004 self-modification guard fails
  closed); set it to this installation's own remote(s).
- Containers run with `--cap-drop ALL` and
  `--security-opt no-new-privileges` in addition to the existing
  network/resource limits.
