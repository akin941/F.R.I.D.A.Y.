#!/bin/sh
# Entrypoint of the Graphify AST-only image (Phase 8B branch 5, brief D10).
#
# /repo  read-only clone (bind)       /work  size-capped tmpfs (noexec)
# /out   the ONLY writable bind       network: none
#
# 1. copy /repo to /work/repo without .git (and without any committed
#    graphify-out/, so a planted symlink can never redirect the write);
# 2. run the code-only update: tree-sitter extraction, no LLM call, no
#    clustering, no query log;
# 3. copy graphify-out/graph.json to /out/graph.json -- only a regular,
#    non-symlink file of at most 20 MB. The host re-checks all of this
#    (harness/safe_read.py): /out is treated as hostile there.
set -eu

export GRAPHIFY_BACKEND=none
export GRAPHIFY_QUERY_LOG_DISABLE=1
export HOME=/work/home
export PYTHONDONTWRITEBYTECODE=1

MAX_BYTES=20971520

mkdir -p /work/home /work/repo
(cd /repo && tar --exclude=./.git --exclude=./graphify-out -cf - .) | tar -xf - -C /work/repo
rm -rf /work/repo/graphify-out

cd /work
graphify update repo --no-cluster >/dev/null

out=/work/repo/graphify-out/graph.json
if [ -L "$out" ] || [ ! -f "$out" ]; then
    echo "graphify: no regular graph.json produced" >&2
    exit 3
fi
size=$(wc -c < "$out")
if [ "$size" -gt "$MAX_BYTES" ]; then
    echo "graphify: graph.json is over 20 MB" >&2
    exit 4
fi
cp "$out" /out/graph.json
