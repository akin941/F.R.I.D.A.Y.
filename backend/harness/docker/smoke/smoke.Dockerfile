# Real-container smoke image (Phase 3 branch 8): the REAL harness image with
# only its CLI binaries replaced by a stub, so `make harness-smoke` can run
# the full write-mode wrapper inside Docker without a paid model call.
# Build context: backend/harness/docker/smoke/.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
COPY stub_cli.sh /usr/local/bin/claude
COPY stub_cli.sh /usr/local/bin/codex
RUN chmod 755 /usr/local/bin/claude /usr/local/bin/codex
