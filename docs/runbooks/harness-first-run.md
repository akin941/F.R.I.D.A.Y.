# Runbook — first real harness run (owner)

Phase 3 shipped the whole harness pipeline (discovery → Gate A →
implementation → cross-vendor review → Gate B → push) and tested it
against mocks, real git and a stubbed CLI inside a real container. A run
against a paid model has never happened: it needs the owner's own API keys
and images built from pinned, owner-verified digests. Follow these steps
once; after that the harness is a normal daily tool.

## 0. Prerequisites

- Docker Desktop running on the runner host (first runner: this Mac).
- The dev stack up: `make up` (Postgres :5433, Redis :6380, web :8001).
- API keys **for the harness only** — API-key auth is the only supported
  mode (Phase 3 brief, "Codex CLI vendor terms"): `ANTHROPIC_API_KEY`
  (Claude Code implements), `CODEX_API_KEY` (Codex CLI reviews).
- A deploy key (SSH) with **write access only to the repos you register**
  and **no access to the Jarvis repo itself**: `HARNESS_GIT_SSH_KEY_PATH`.
  Push remotes must be SSH (`git@host:owner/repo.git`).

## 1. Pin and build the images

1. In `backend/harness/docker/claude_code.Dockerfile` and
   `codex_cli.Dockerfile` replace the placeholder `FROM node@sha256:00…`
   with a real digest:
   `docker pull node:20-slim && docker inspect --format='{{index .RepoDigests 0}}' node:20-slim`
   (record the tag next to the digest in a comment).
2. Set `ARG CLAUDE_CODE_VERSION` / `ARG CODEX_CLI_VERSION` to the current
   releases (npm: `@anthropic-ai/claude-code`, `@openai/codex`).
3. Confirm the two write-mode permission flags against current vendor docs
   (Claude Code `--permission-mode acceptEdits`, Codex `--sandbox
   workspace-write`). The wrapper commits on the agent's behalf, so the
   agent only needs edit permission, not shell `git commit`.
4. `make harness-images` (tags `jarvis-harness-claude-code:local` and
   `jarvis-harness-codex-cli:local`; override with
   `HARNESS_IMAGE_CLAUDE_CODE=…` / `HARNESS_IMAGE_CODEX_CLI=…`).
5. `make harness-smoke` — builds the smoke image (real image, stubbed CLI)
   and runs the real-container round trip. It must pass before any paid
   run.

## 2. Network

Create the restricted-egress network the containers join. Minimal version
(internal network, no internet at all — enough for discovery against an
already-cloned checkout, **not** for implementation, whose CLI must reach
the model API and whose test command usually installs dependencies):

```
docker network create --internal jarvis-harness-egress
```

For implementation, use a bridge network plus an egress allowlist (the
model API hosts and your package registries), e.g. through a forward proxy
the containers are forced through. Record what you allowed.

## 3. Environment (repo-root `.env`, never committed)

```
ANTHROPIC_API_KEY=…
CODEX_API_KEY=…
HARNESS_GIT_SSH_KEY_PATH=/Users/<you>/.ssh/jarvis_harness_deploy
HARNESS_SELF_REPO_IDENTIFIERS=git@github.com:akin941/jarvis-os.git
HARNESS_IMAGE_CLAUDE_CODE=jarvis-harness-claude-code:local
HARNESS_IMAGE_CODEX_CLI=jarvis-harness-codex-cli:local
HARNESS_DOCKER_NETWORK=jarvis-harness-egress
HARNESS_MAX_JOB_COST=1.00
```

`HARNESS_SELF_REPO_IDENTIFIERS` is **required**: while it is empty every
implementation job is refused (the ADR-004 self-modification guard fails
closed). Keep `HARNESS_MAX_JOB_COST` low for the first runs.

## 4. Start the runner

The compose worker never takes harness tasks (it has no Docker). On the
runner host, in its own terminal:

```
make harness-runner
```

It consumes only the `harness` queue, including the recovery scans. With
no runner running, harness jobs wait in `pending`.

## 5. First runs

1. Register a small, disposable test repo (Harness → repos API; set
   `test_command` to something that provisions its own dependencies, e.g.
   `npm ci && npm test` — tests run on a pristine checkout of the produced
   commit).
2. Run a **discovery** job first and read the briefing.
3. From the briefing, "Uygulama iste". Approve Gate A in Onaylar.
4. Watch the job: implementing → pending_review (Codex review) →
   pending_gate_b. Read the diff summary, test result, review verdict and
   both transcripts before approving Gate B.
5. Approve Gate B → the branch `harness/p3-<job-id>` is pushed (never
   `main`, never force). Merge it yourself in GitHub.

## 6. If something goes wrong

- Cancel from the job detail — containers stop, worktrees are removed.
- Failure reasons are shown in Turkish in the UI; `secret_leak` means the
  produced change contained a literal API key and was never pushed —
  rotate that key anyway.
- Recovery scans (every few minutes on the runner) fail stuck jobs closed
  and remove leaked containers/worktrees.
