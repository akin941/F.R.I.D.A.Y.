# Harness runner image for the Claude Code agent (Phase 2 branch 2, brief
# "Container policy"). NOT built or deployed automatically by this branch --
# building/pushing this image, and keeping its pins current, is the
# "reviewed bump/rebuild cadence" the brief itself calls for (Codex
# code-review round-4 HIGH), owner-run infra work, not application code.
#
# Base image is pinned by immutable digest, never a mutable tag and never
# `latest` (Codex code-review P2, branch 2 round 3: an earlier draft
# pinned the `node:20.11.1-slim` TAG, which Docker Hub could still
# repoint to a different underlying image without any reviewed change
# here -- exactly what the container policy's "pinned to a specific,
# recorded base-image digest" requirement exists to prevent). The digest
# below is a PLACEHOLDER -- resolve the real current digest with
# `docker pull node:20.11.1-slim && docker inspect --format='{{index .RepoDigests 0}}' node:20.11.1-slim`
# before the first real build, and record the tag alongside the digest in
# a comment so a future reader knows what it was pinned from. The CLI
# version ARG below is a separate placeholder, verified against
# https://www.npmjs.com/package/@anthropic-ai/claude-code at build time --
# neither pin here is re-verified as part of any automated test in this
# repo and must not be treated as a confirmed-current value.
FROM node@sha256:0000000000000000000000000000000000000000000000000000000000000000
# ^ TODO(owner, before first real build): replace with the real digest for
# node:20.11.1-slim (or whatever base the reviewed bump cadence selects).

# TODO(owner, before first real build): verify this against the npm
# registry's current release and bump on the reviewed cadence above.
ARG CLAUDE_CODE_VERSION=1.0.0

# --disable-update-notifier / no auto-update: the container is disposable
# per job (brief: "disable the CLI's runtime auto-update/update-check
# behavior") -- there is nothing for an in-container update to persist to
# anyway, but installing a version-pinned package explicitly (rather than
# `@latest`) is the actual control, not a flag.
# git (Codex code-review P1, branch 1 round 9): write mode's wrapper
# script now shells out to `git rev-parse/status/diff` to read committed
# repo state after quiescence -- a node:*-slim base does not include it,
# so those calls would exit 127 and fail every implementation run closed
# even when the agent and tests both succeeded. Installed here rather
# than assumed present; the apt lists are removed in the same layer so
# this doesn't bloat the disposable per-job image with package metadata
# it never needs again.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN npm install --global "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    && useradd --create-home --uid 10001 harness \
    # World-writable + sticky bit (Codex code-review P2, branch 2 round 6):
    # harness.runner.launch_container now runs this image with `--user
    # <host-uid>:<host-gid>` (whatever uid cloned the repo on the host),
    # not this image's own baked-in UID 10001 -- so /home/harness must be
    # writable by an arbitrary runtime uid, not just its build-time owner.
    # Sticky bit (the `t`) keeps one job's files from being deletable by a
    # differently-uid'd process, in case a runner ever ran two jobs
    # concurrently under different host uids.
    && chmod 1777 /home/harness

# No USER directive: the runtime `--user` flag (harness.runner.
# launch_container) decides who this container actually runs as, matching
# whichever host uid owns the read-only-mounted clone.
ENV HOME=/home/harness
WORKDIR /repo

COPY claude_code_run.sh /home/harness/run.sh
RUN chmod +x /home/harness/run.sh

ENTRYPOINT ["/home/harness/run.sh"]
