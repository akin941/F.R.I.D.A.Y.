#!/bin/sh
# Regenerates a harness driver's protocol snapshot from the INSTALLED binary
# of the pinned version (Phase 10 brief D10 "Pins and contract tests").
# Owner-run on a machine with Docker and the built image -- never in CI, and
# never by the app. Method names and payloads in the snapshot come from the
# binary, never from memory.
#
#   generate_driver_schemas.sh codex <image>
#
# writes backend/harness/drivers/codex_schema/<version>/protocol.json, where
# <version> is the image's CODEX_CLI_VERSION ARG (read from
# codex_cli.Dockerfile). Afterwards:
#   1. set HARNESS_CODEX_APP_SERVER_VERSION=<version> (and the Dockerfile
#      ARG, if it changed);
#   2. run `uv run pytest -q harness/tests/test_driver_contract.py` -- every
#      fixture frame must still validate and every method the driver uses
#      must exist in the new snapshot;
#   3. commit the snapshot on a dedicated upgrade branch (brief R4).
#
#   generate_driver_schemas.sh claude <image>
#
# (branch 7) writes
# backend/harness/drivers/claude_sdk_surface/<version>/sdk_surface.json from
# the image's installed claude-agent-sdk (claude_sdk_surface.py run by the
# image's bridge interpreter), where <version> is the Dockerfile's
# CLAUDE_AGENT_SDK_VERSION ARG; the installed version must match it. Then
# set HARNESS_CLAUDE_AGENT_SDK_VERSION=<version> and run
# `uv run pytest -q harness/tests/test_claude_driver_contract.py` (every name
# the bridge uses must exist in the new surface).
set -eu

here=$(cd "$(dirname "$0")" && pwd)
drivers_dir="$here/../drivers"

usage() {
    echo "usage: $0 codex|claude <image>" >&2
    exit 64
}

[ $# -eq 2 ] || usage
target="$1"
image="$2"

case "$target" in
    codex)
        version=$(sed -n 's/^ARG CODEX_CLI_VERSION=\(.*\)$/\1/p' "$here/codex_cli.Dockerfile")
        [ -n "$version" ] || { echo "no ARG CODEX_CLI_VERSION in codex_cli.Dockerfile" >&2; exit 1; }
        work=$(mktemp -d)
        trap 'rm -rf "$work"' EXIT
        # --network none: schema generation needs nothing from outside.
        docker run --rm --pull never --network none --entrypoint codex \
            --user "$(id -u):$(id -g)" \
            --mount "type=bind,source=$work,target=/out" \
            "$image" app-server generate-json-schema --out /out
        installed=$(docker run --rm --pull never --network none --entrypoint codex "$image" --version)
        case "$installed" in
            *"$version"*) ;;
            *) echo "image reports '$installed', Dockerfile pins $version" >&2; exit 1 ;;
        esac
        python3 -I "$here/codex_schema_subset.py" "$work" "$version" \
            "$drivers_dir/codex_schema/$version/protocol.json"
        echo "wrote $drivers_dir/codex_schema/$version/protocol.json"
        ;;
    claude)
        version=$(sed -n 's/^ARG CLAUDE_AGENT_SDK_VERSION=\(.*\)$/\1/p' "$here/claude_code.Dockerfile")
        [ -n "$version" ] || { echo "no ARG CLAUDE_AGENT_SDK_VERSION in claude_code.Dockerfile" >&2; exit 1; }
        work=$(mktemp -d)
        trap 'rm -rf "$work"' EXIT
        # --network none: introspection needs nothing from outside. The
        # script is mounted read-only and run by the bridge's own interpreter.
        docker run --rm --pull never --network none \
            --user "$(id -u):$(id -g)" \
            --mount "type=bind,source=$here/claude_sdk_surface.py,target=/tmp/claude_sdk_surface.py,readonly" \
            --entrypoint /opt/friday-bridge/bin/python \
            "$image" -I /tmp/claude_sdk_surface.py > "$work/sdk_surface.json"
        installed=$(python3 -I -c 'import json, sys; print(json.load(open(sys.argv[1]))["version"])' \
            "$work/sdk_surface.json")
        if [ "$installed" != "$version" ]; then
            echo "image has claude-agent-sdk $installed, Dockerfile pins $version" >&2
            exit 1
        fi
        mkdir -p "$drivers_dir/claude_sdk_surface/$version"
        cp "$work/sdk_surface.json" "$drivers_dir/claude_sdk_surface/$version/sdk_surface.json"
        echo "wrote $drivers_dir/claude_sdk_surface/$version/sdk_surface.json"
        ;;
    *)
        usage
        ;;
esac
