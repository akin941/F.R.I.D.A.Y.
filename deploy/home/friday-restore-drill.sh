#!/usr/bin/env bash
# Friday home server -- restore drill (Phase 13 D14, ADR-017 decision 6;
# docs/runbooks/home-server-setup.md step 9). At least monthly.
#
#   friday-restore-drill /run/user/1000/friday-backup.agekey [DUMP]
#
# Restores the latest encrypted dump (or DUMP, e.g. one restored from the
# off-site copy) into a SCRATCH Postgres -- never the live one -- and proves
# it: the schema matches the code (`migrate --check`) and the node audit
# chain is intact (`verify_node_audit_chain`). Then it writes the status
# file preflight reads (`home_restore_drill_age`):
#   drill.json {v:1, drilled_at, dump, result}     result: pass | fail
# Any failure (a dump whose sha256 differs from status.json, an age MAC
# error on a tampered file, a pg_restore error, a schema or chain check)
# writes `result: fail` and exits non-zero.
#
# The age PRIVATE key is brought in only for the drill (tmpfs, USB stick)
# and removed afterwards; it is never stored on the server.
#
# Environment (runbook defaults):
#   FRIDAY_REPO_DIR            /srv/friday/friday
#   FRIDAY_BACKUP_DIR          /var/backups/friday
#   FRIDAY_DRILL_MODE          docker (a throwaway pgvector container on the
#                              stack's network, no published port, and a
#                              one-off `web` container for the checks) |
#                              direct (FRIDAY_DRILL_DATABASE_URL, an EMPTY
#                              scratch database, and the checks run from the
#                              checkout's backend/ with uv; tests use this)
#   FRIDAY_DRILL_DATABASE_URL  direct mode only
#   FRIDAY_DRILL_IMAGE         pgvector/pgvector:pg16
#   FRIDAY_DRILL_NETWORK       friday_default
#   FRIDAY_DRILL_UV            uv
set -euo pipefail
shopt -s inherit_errexit
umask 077

usage() {
	echo "usage: friday-restore-drill AGE_PRIVATE_KEY_FILE [DUMP_FILE]" >&2
	exit 2
}

KEY=${1:-}
DUMP=${2:-}
[[ -n $KEY ]] || usage
[[ -r $KEY ]] || {
	echo "friday-restore-drill: cannot read the key file" >&2
	exit 2
}

REPO_DIR=${FRIDAY_REPO_DIR:-/srv/friday/friday}
BACKUP_DIR=${FRIDAY_BACKUP_DIR:-/var/backups/friday}
MODE=${FRIDAY_DRILL_MODE:-docker}
IMAGE=${FRIDAY_DRILL_IMAGE:-pgvector/pgvector:pg16}
NETWORK=${FRIDAY_DRILL_NETWORK:-friday_default}
UV=${FRIDAY_DRILL_UV:-uv}
CONTAINER=friday-drill-pg
COMPOSE=(docker compose -f deploy/docker/compose.yml -f deploy/docker/compose.daily.yml)

case $MODE in
docker | direct) ;;
*)
	echo "friday-restore-drill: FRIDAY_DRILL_MODE must be docker or direct" >&2
	exit 2
	;;
esac
if [[ $MODE == direct && -z ${FRIDAY_DRILL_DATABASE_URL:-} ]]; then
	echo "friday-restore-drill: direct mode needs FRIDAY_DRILL_DATABASE_URL" >&2
	exit 2
fi
[[ -d $BACKUP_DIR ]] || {
	echo "friday-restore-drill: backup directory missing: $BACKUP_DIR" >&2
	exit 2
}

# Absolute paths: the docker mode changes into the checkout.
BACKUP_DIR=$(cd "$BACKUP_DIR" && pwd)
KEY=$(realpath "$KEY")
[[ -z $DUMP ]] || DUMP=$(realpath -m "$DUMP")

RESULT=fail
DUMP_NAME=""
STARTED_CONTAINER=0

finish() {
	local code=$?
	if [[ $STARTED_CONTAINER == 1 ]]; then
		docker stop "$CONTAINER" >/dev/null 2>&1 || true
	fi
	if [[ $RESULT != pass ]]; then
		RESULT=fail
		[[ $code == 0 ]] && code=1
	fi
	printf '{"v":1,"drilled_at":"%s","dump":"%s","result":"%s"}\n' \
		"$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$DUMP_NAME" "$RESULT" >"$BACKUP_DIR/drill.json.part"
	chmod 0644 "$BACKUP_DIR/drill.json.part"
	mv "$BACKUP_DIR/drill.json.part" "$BACKUP_DIR/drill.json"
	echo "friday-restore-drill: ${DUMP_NAME:-no dump}: $RESULT" >&2
	exit "$code"
}
trap finish EXIT

fail() {
	echo "friday-restore-drill: $*" >&2
	exit 1
}

# 1. Which dump: the newest (the names sort by their UTC stamp) or DUMP.
if [[ -z $DUMP ]]; then
	shopt -s nullglob
	dumps=("$BACKUP_DIR"/friday-*.dump.age)
	shopt -u nullglob
	((${#dumps[@]})) || fail "no friday-*.dump.age in $BACKUP_DIR"
	DUMP=${dumps[-1]}
fi
[[ -r $DUMP ]] || fail "cannot read the dump"
DUMP_NAME=$(basename "$DUMP")

# 2. The dump is the one the backup recorded (when status.json names it).
STATUS="$BACKUP_DIR/status.json"
if [[ -r $STATUS ]] && grep -q "\"dump\":\"$DUMP_NAME\"" "$STATUS"; then
	want=$(grep -o '"sha256":"[0-9a-f]*"' "$STATUS" | cut -d'"' -f4)
	have=$(sha256sum "$DUMP" | cut -d' ' -f1)
	[[ $want == "$have" ]] || fail "sha256 of $DUMP_NAME differs from status.json"
fi

# 3. Restore into the scratch database, streaming: no plaintext on disk.
case $MODE in
docker)
	cd "$REPO_DIR"
	PASSWORD=$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')
	docker run -d --rm --name "$CONTAINER" --network "$NETWORK" \
		-e POSTGRES_USER=jarvis -e POSTGRES_PASSWORD="$PASSWORD" -e POSTGRES_DB=jarvis \
		"$IMAGE" >/dev/null
	STARTED_CONTAINER=1
	for _ in $(seq 1 60); do
		docker exec "$CONTAINER" pg_isready -U jarvis -d jarvis >/dev/null 2>&1 && break
		sleep 1
	done
	docker exec "$CONTAINER" pg_isready -U jarvis -d jarvis >/dev/null 2>&1 ||
		fail "the scratch database did not start"
	age -d -i "$KEY" "$DUMP" |
		docker exec -i "$CONTAINER" pg_restore -U jarvis -d jarvis \
			--no-owner --no-privileges --exit-on-error
	SCRATCH_URL="postgres://jarvis:$PASSWORD@$CONTAINER:5432/jarvis"
	# 4. Schema matches the code, and the node audit chain is intact.
	"${COMPOSE[@]}" run --rm --no-deps -e DATABASE_URL="$SCRATCH_URL" web \
		sh -c "uv run python manage.py migrate --check && uv run python manage.py verify_node_audit_chain"
	;;
direct)
	SCRATCH_URL=$FRIDAY_DRILL_DATABASE_URL
	tables=$(psql -X -A -t -d "$SCRATCH_URL" \
		-c "select count(*) from pg_tables where schemaname = 'public'")
	[[ $tables == 0 ]] || fail "the scratch database is not empty; refusing to restore into it"
	age -d -i "$KEY" "$DUMP" |
		pg_restore -d "$SCRATCH_URL" --no-owner --no-privileges --exit-on-error
	# 4. Schema matches the code, and the node audit chain is intact.
	(
		cd "$REPO_DIR/backend"
		export DATABASE_URL=$SCRATCH_URL
		"$UV" run python manage.py migrate --check
		"$UV" run python manage.py verify_node_audit_chain
	)
	;;
esac

RESULT=pass
