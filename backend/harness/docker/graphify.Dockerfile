# Graphify AST-only code-graph image (Phase 8B branch 5, brief D10).
#
# NOT built or deployed by any automation: the owner builds it on the
# runner host (README "Graphify image"); it needs PyPI access at BUILD time
# only. At run time `harness.runner.launch_graphify_container` starts it
# with `--network none`, `--read-only`, a size-capped noexec tmpfs `/work`,
# `/repo` read-only and `/out` as the only writable bind -- no env file and
# no credentials.
#
# Supply chain (brief R5, docs/mcp/graphify.md):
# - the base image is pinned by its multi-arch index digest, never a tag
#   alone (python:3.12-slim-bookworm, resolved 2026-11-21 from Docker Hub);
# - `graphifyy` (double y: other `graphify*` packages are not affiliated) is
#   pinned by name, version and sha256, and every transitive wheel is
#   hash-locked in graphify.lock.txt (x86_64 and aarch64 manylinux wheels,
#   so the image builds on an Intel runner and on Apple silicon);
# - wheels only (`--only-binary=:all:`): no sdist, so no install script
#   runs; `--no-deps` so nothing outside the lock is ever resolved.
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

# Graphify makes no model call and keeps no query log; no telemetry exists
# upstream ("no telemetry", README of graphifyy 0.9.103).
ENV GRAPHIFY_BACKEND=none \
    GRAPHIFY_QUERY_LOG_DISABLE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY graphify.lock.txt /opt/graphify/graphify.lock.txt
RUN pip install --require-hashes --no-deps --only-binary=:all: \
        -r /opt/graphify/graphify.lock.txt \
    && useradd --uid 10001 --no-create-home --home-dir /work/home --shell /usr/sbin/nologin graphify

COPY graphify_run.sh /opt/graphify/run.sh
RUN chmod 0555 /opt/graphify/run.sh

# The runtime `--user {host_uid}:{host_gid}` overrides this; the image
# itself never runs as root.
USER 10001
WORKDIR /work
ENTRYPOINT ["/opt/graphify/run.sh"]
