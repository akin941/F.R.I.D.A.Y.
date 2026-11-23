# `compressor` sidecar image (Phase 8B branch 6, brief D11,
# docs/mcp/headroom.md).
#
# Headroom (`headroom-ai`) behind a stdlib HTTP server with one endpoint,
# `POST /compress`. Friday code never imports Headroom: its base install
# requires `litellm`, which ADR-003 rules out. This image installs Headroom
# and exactly the wheels `compress()` imports, WITHOUT `litellm`.
#
# Built by the owner (`docker compose --profile compression build
# compressor`); it needs PyPI and openaipublic.blob.core.windows.net (the
# tiktoken encodings) at BUILD time only. At run time it sits on the
# `compression` network (`internal: true`, shared only with web and worker):
# no egress, no provider key, read-only root, every capability dropped.
#
# Supply chain:
# - the base image is pinned by digest (python:3.12-slim-bookworm, the same
#   digest as backend/harness/docker/graphify.Dockerfile, 2026-11-21);
# - every wheel is pinned by version and sha256 in requirements.lock.txt and
#   installed `--require-hashes --no-deps --only-binary=:all:` (no sdist, no
#   install script, nothing resolved outside the lock);
# - the build FAILS unless test_no_litellm.py (import blocker for litellm,
#   openai, anthropic) and test_server.py pass.
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

ENV HEADROOM_OFFLINE=1 \
    HEADROOM_TELEMETRY=off \
    HEADROOM_BEACON=off \
    DO_NOT_TRACK=1 \
    HF_HUB_OFFLINE=1 \
    TIKTOKEN_CACHE_DIR=/opt/compressor/tiktoken \
    COMPRESSOR_PORT=8080 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.lock.txt /opt/compressor/requirements.lock.txt
RUN pip install --require-hashes --no-deps --only-binary=:all: \
        -r /opt/compressor/requirements.lock.txt \
    && useradd --uid 10002 --no-create-home --shell /usr/sbin/nologin compressor

# Bake the tiktoken encodings so the running container never downloads one.
RUN mkdir -p "$TIKTOKEN_CACHE_DIR" \
    && python -c "import tiktoken; [tiktoken.get_encoding(n) for n in ('o200k_base', 'cl100k_base')]" \
    && chmod -R a+rX "$TIKTOKEN_CACHE_DIR"

COPY server.py test_no_litellm.py test_server.py /opt/compressor/
WORKDIR /opt/compressor
RUN python test_no_litellm.py && python test_server.py \
    && rm -f test_no_litellm.py test_server.py \
    && chmod 0444 server.py

USER 10002
EXPOSE 8080
ENTRYPOINT ["python", "/opt/compressor/server.py"]
