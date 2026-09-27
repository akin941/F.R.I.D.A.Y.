# Harness runner image for the Codex CLI agent (Phase 2 branch 2, brief
# "Container policy"). NOT built or deployed automatically by this branch,
# and NOT selectable as a HarnessJob.agent until this branch's own O-2
# vendor-terms + cost-reporting verification clears it (brief R6) -- this
# Dockerfile exists so that verification work has a real image to test
# against, not to enable Codex CLI as a harness agent by itself.
#
# Base image pinned by immutable digest, never a mutable tag and never
# `latest` (Codex code-review P2, branch 2 round 3 -- see
# claude_code.Dockerfile's identical comment for why a tag alone isn't
# enough). The digest below is a PLACEHOLDER -- resolve the real current
# one with
# `docker pull node:20.11.1-slim && docker inspect --format='{{index .RepoDigests 0}}' node:20.11.1-slim`
# before the first real build. The CLI version ARG below is a separate
# placeholder verified against https://www.npmjs.com/package/@openai/codex
# at build time -- neither pin here is re-verified by any automated test
# in this repo.
FROM node@sha256:0000000000000000000000000000000000000000000000000000000000000000
# ^ TODO(owner, before first real build): replace with the real digest for
# node:20.11.1-slim (or whatever base the reviewed bump cadence selects).

# TODO(owner, before first real build): verify this against the npm
# registry's current release and bump on the reviewed cadence the brief
# calls for.
ARG CODEX_CLI_VERSION=0.1.0

# git (Codex code-review P1, branch 1 round 9) -- see
# claude_code.Dockerfile's identical comment: write mode's wrapper now
# depends on git being present to read committed repo state after
# quiescence.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN npm install --global "@openai/codex@${CODEX_CLI_VERSION}" \
    && useradd --create-home --uid 10001 harness \
    # World-writable + sticky bit (Codex code-review P2, branch 2 round 6):
    # harness.runner.launch_container now runs this image with `--user
    # <host-uid>:<host-gid>`, not this image's own baked-in UID 10001 -- see
    # claude_code.Dockerfile's identical comment.
    && chmod 1777 /home/harness

# No USER directive: the runtime `--user` flag (harness.runner.
# launch_container) decides who this container actually runs as, matching
# whichever host uid owns the read-only-mounted clone.
ENV HOME=/home/harness
WORKDIR /repo

COPY codex_cli_run.sh /home/harness/run.sh
# Shared write-mode logic (Phase 3 branch 5), sourced by run.sh from its
# own directory.
COPY harness_write_mode.sh /home/harness/harness_write_mode.sh
RUN chmod +x /home/harness/run.sh

ENTRYPOINT ["/home/harness/run.sh"]
