#!/usr/bin/env bash
# `make deploy-smoke` (manual/nightly; Phase 5 branch 6, brief D10): builds
# the smoke "VPS" image, starts it with a freshly generated runner key bound
# to the real forced-command script, captures its host key out of band
# (`docker exec`, never trust-on-first-use), and runs
# backend/deploys/tests/test_deploy_smoke.py against it through the real
# system ssh client: status, a deploy, a forced health failure with its
# automatic rollback, and a refused non-merged SHA. Needs Docker and a test
# database (DATABASE_URL, as for `make test`). Leaves nothing running.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../../.." && pwd)
image=${DEPLOY_SMOKE_IMAGE:-friday-deploy-smoke:local}
work=$(mktemp -d "${TMPDIR:-/tmp}/friday-deploy-smoke.XXXXXX")
cid=""

cleanup() {
    if [ -n "$cid" ]; then
        docker rm -f "$cid" >/dev/null 2>&1 || true
    fi
    rm -rf "$work"
}
trap cleanup EXIT

docker build -q -t "$image" -f "$here/Dockerfile" "$root/deploy/remote" >/dev/null
ssh-keygen -q -t ed25519 -N '' -C friday-deploy-smoke -f "$work/runner_key"
cid=$(docker run -d --rm \
    -e AUTHORIZED_KEY="$(cat "$work/runner_key.pub")" \
    -p 127.0.0.1::22 -p 127.0.0.1::8080 \
    "$image")

for _ in $(seq 1 30); do
    if docker exec "$cid" pgrep -x sshd >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
sleep 1

port_of() { docker port "$cid" "$1" | head -n 1 | sed 's/.*://'; }
ssh_port=$(port_of 22/tcp)
http_port=$(port_of 8080/tcp)
host_key=$(docker exec "$cid" cut -d' ' -f1,2 /etc/ssh/ssh_host_ed25519_key.pub)
shas=$(docker exec "$cid" cat /srv/smoke-shas.env)

cd "$root/backend"
env $shas \
    DEPLOY_SMOKE_SSH_PORT="$ssh_port" \
    DEPLOY_SMOKE_HTTP_PORT="$http_port" \
    DEPLOY_SMOKE_HOST_KEY="$host_key" \
    DEPLOY_SMOKE_KEY_PATH="$work/runner_key" \
    uv run pytest deploys/tests/test_deploy_smoke.py -q -p no:cacheprovider "$@"
