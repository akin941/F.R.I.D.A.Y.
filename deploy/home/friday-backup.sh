#!/usr/bin/env bash
# Friday home server -- nightly encrypted backup (Phase 13 D14, ADR-017
# decision 6; docs/runbooks/home-server-setup.md step 8).
#
#   friday-backup                 # normally run by friday-backup.timer
#
# 1. a custom-format `pg_dump` of the live database, piped straight into
#    `age` (the plaintext dump never touches the disk);
# 2. the stack's secrets file (`deploy/docker/.env`), encrypted the same way
#    (a restore needs NODE_SIGNING_KEY and the rest);
# 3. `restic backup` of the backup directory to the off-site repository,
#    then retention (`restic forget --prune`) and local pruning;
# 4. the status file preflight reads (`home_backup_fresh`), in its own
#    status/ subdirectory (the only part of the backup directory the web
#    container sees: a directory bind mount, so the atomic replace below is
#    visible there -- a single-file mount would pin the old inode):
#      status/status.json {v:1, finished_at, dump, bytes, sha256,
#                          restic_snapshot, repository}
#    -- no secret, no path beyond the dump's file name and the repository
#    identifier (Phase 13b D2: RESTIC_REPOSITORY as configured, e.g.
#    s3:https://<account-id>.r2.cloudflarestorage.com/<bucket>; the run
#    FAILS, before anything is dumped, when it carries userinfo (`@`) or a
#    query (`?`), so no credential ever reaches the status file).
#
# A host operation the owner configures: no Friday agent, tool, schedule or
# model starts, changes or reads it. Only the age PUBLIC key is on the
# server, so the server cannot read its own old backups.
#
# Every location is an environment variable with the runbook's default, so
# the same script is tested against a scratch Postgres and a local restic
# repository (backend/ops/tests/test_home_backup_restore.py):
#   FRIDAY_REPO_DIR         /srv/friday/friday
#   FRIDAY_BACKUP_DIR       /var/backups/friday
#   FRIDAY_STATUS_DIR       $FRIDAY_BACKUP_DIR/status (status.json, drill.json;
#                           never a dump -- bound read-only into web)
#   FRIDAY_AGE_RECIPIENTS   /etc/friday-backup/age.pub   (age public key(s))
#   FRIDAY_RESTIC_ENV       /etc/friday-backup/restic.env (RESTIC_REPOSITORY,
#                           RESTIC_PASSWORD_FILE, the bucket's credentials)
#   FRIDAY_ENV_FILE         $FRIDAY_REPO_DIR/deploy/docker/.env
#   FRIDAY_PG_DUMP_MODE     compose (pg_dump inside the stack's postgres
#                           container) | direct (host pg_dump with the libpq
#                           PG* environment; tests and non-Docker installs)
#   FRIDAY_KEEP_LOCAL_DAYS  3      (local *.age files older than this go)
#   FRIDAY_RESTIC_KEEP      "--keep-daily 14 --keep-weekly 8 --keep-monthly 12"
#   FRIDAY_PATH             unset  (replaces PATH when set: launchd starts
#                           jobs with /usr/bin:/bin:/usr/sbin:/sbin, without
#                           Homebrew's age/restic or the Docker CLI)
#
# Portable (Phase 13b D1): Linux and macOS. It needs bash >= 4.4 (macOS
# ships 3.2: run it with Homebrew's /opt/homebrew/bin/bash) and uses only
# tools both systems have -- `flock` when present, else a lock directory
# with a stale-PID check; `sha256sum` or `shasum -a 256`; `wc -c` for the
# size (no GNU `stat -c`). On the Mac it is scheduled by the LaunchAgent
# deploy/home/macos/com.friday.backup.plist instead of the systemd timer.
set -euo pipefail
if [[ -n ${FRIDAY_PATH:-} ]]; then
	PATH=$FRIDAY_PATH
	export PATH
fi
# Before anything bash-4.4-only (`inherit_errexit`): a clear refusal.
_major=${BASH_VERSION%%.*}
_minor=${BASH_VERSION#*.}
_minor=${_minor%%[!0-9]*}
if ((_major < 4 || (_major == 4 && ${_minor:-0} < 4))); then
	echo "friday-backup: needs bash >= 4.4, this is bash $BASH_VERSION" \
		"(on macOS: brew install bash; run it with /opt/homebrew/bin/bash)" >&2
	exit 1
fi
shopt -s inherit_errexit
umask 077

REPO_DIR=${FRIDAY_REPO_DIR:-/srv/friday/friday}
BACKUP_DIR=${FRIDAY_BACKUP_DIR:-/var/backups/friday}
STATUS_DIR=${FRIDAY_STATUS_DIR:-$BACKUP_DIR/status}
AGE_RECIPIENTS=${FRIDAY_AGE_RECIPIENTS:-/etc/friday-backup/age.pub}
RESTIC_ENV=${FRIDAY_RESTIC_ENV:-/etc/friday-backup/restic.env}
ENV_FILE=${FRIDAY_ENV_FILE:-$REPO_DIR/deploy/docker/.env}
DUMP_MODE=${FRIDAY_PG_DUMP_MODE:-compose}
KEEP_LOCAL_DAYS=${FRIDAY_KEEP_LOCAL_DAYS:-3}
RESTIC_KEEP=${FRIDAY_RESTIC_KEEP:---keep-daily 14 --keep-weekly 8 --keep-monthly 12}

die() {
	echo "friday-backup: $*" >&2
	exit 1
}

[[ -d $BACKUP_DIR ]] || die "backup directory missing: $BACKUP_DIR"
[[ -s $AGE_RECIPIENTS ]] || die "age recipients file missing or empty: $AGE_RECIPIENTS"
[[ -r $RESTIC_ENV ]] || die "restic environment file missing: $RESTIC_ENV"
[[ -r $ENV_FILE ]] || die "secrets file missing: $ENV_FILE"
if grep -q 'AGE-SECRET-KEY-' "$AGE_RECIPIENTS"; then
	die "$AGE_RECIPIENTS holds an age PRIVATE key; only the public key belongs on the server"
fi
command -v age >/dev/null || die "age is not installed"
command -v restic >/dev/null || die "restic is not installed"

# The repository this run backs up to, recorded in status.json (D2). Read
# from the owner's restic env file in a subshell (its credentials never
# reach this shell) and refused -- never echoed -- when it could carry a
# credential (userinfo `user:pass@`, a `?query`) or a character the JSON
# status line cannot hold verbatim.
REPOSITORY=$(
	set -a
	# shellcheck source=/dev/null
	. "$RESTIC_ENV"
	set +a
	printf '%s' "${RESTIC_REPOSITORY:-}"
)
[[ -n $REPOSITORY ]] || die "RESTIC_REPOSITORY is not set in $RESTIC_ENV"
case $REPOSITORY in
*@*) die "RESTIC_REPOSITORY carries userinfo (@); refusing to record it (put credentials in the restic env variables, not the URL)" ;;
*\?*) die "RESTIC_REPOSITORY carries a query (?); refusing to record it" ;;
esac
[[ $REPOSITORY =~ ^[A-Za-z0-9._~:/%+=,-]{1,512}$ ]] ||
	die "RESTIC_REPOSITORY is not a plain repository identifier; refusing to record it"

sha256_of() {
	if command -v sha256sum >/dev/null; then
		sha256sum "$1" | cut -d' ' -f1
	elif command -v shasum >/dev/null; then
		shasum -a 256 "$1" | cut -d' ' -f1
	else
		die "neither sha256sum nor shasum is installed"
	fi
}

# One run at a time (a manual start while the timer fires). `flock` on
# Linux; macOS has none, so a lock directory (mkdir is atomic) holding the
# owner's PID, taken over only when that process is gone (a crashed run).
LOCK_FILE="$BACKUP_DIR/.friday-backup.lock"
LOCK_DIR="$BACKUP_DIR/.friday-backup.lock.d"
HOLD_LOCK_DIR=0
if command -v flock >/dev/null; then
	exec 9>"$LOCK_FILE"
	flock -n 9 || die "another backup is running"
else
	if ! mkdir "$LOCK_DIR" 2>/dev/null; then
		holder=$(cat "$LOCK_DIR/pid" 2>/dev/null || true)
		if [[ $holder =~ ^[0-9]+$ ]] && ps -p "$holder" >/dev/null 2>&1; then
			die "another backup is running (pid $holder)"
		fi
		echo "friday-backup: removing a stale lock (pid ${holder:-unknown} is gone)" >&2
		rm -rf "$LOCK_DIR"
		mkdir "$LOCK_DIR" 2>/dev/null || die "another backup is running"
	fi
	HOLD_LOCK_DIR=1
	echo "$$" >"$LOCK_DIR/pid"
fi

# The status directory holds only the two status files (no dump, no
# secret); it is world-readable so the web container's bind mount can read it.
mkdir -p "$STATUS_DIR"
chmod 0755 "$STATUS_DIR"

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
DUMP_NAME="friday-$STAMP.dump.age"
OUT="$BACKUP_DIR/$DUMP_NAME"
ENV_OUT="$BACKUP_DIR/env-$STAMP.age"

cleanup() {
	rm -f "$OUT.part" "$ENV_OUT.part" "$STATUS_DIR/status.json.part"
	if [[ $HOLD_LOCK_DIR == 1 ]]; then
		rm -rf "$LOCK_DIR"
	fi
}
trap cleanup EXIT

pg_dump_stream() {
	case $DUMP_MODE in
	compose)
		# The user and database are the postgres container's own env.
		# shellcheck disable=SC2016
		(cd "$REPO_DIR" && docker compose \
			-f deploy/docker/compose.yml -f deploy/docker/compose.daily.yml \
			exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc')
		;;
	direct)
		pg_dump -Fc
		;;
	*)
		die "FRIDAY_PG_DUMP_MODE must be compose or direct, not '$DUMP_MODE'"
		;;
	esac
}

# 1. Consistent custom-format dump, encrypted before it touches the disk.
pg_dump_stream | age -R "$AGE_RECIPIENTS" -o "$OUT.part"
mv "$OUT.part" "$OUT"

# 2. The secrets file, encrypted the same way (needed for a restore).
age -R "$AGE_RECIPIENTS" -o "$ENV_OUT.part" "$ENV_FILE"
mv "$ENV_OUT.part" "$ENV_OUT"

# 3. Off-site push and retention. The restic env file is the owner's; it is
#    sourced in a subshell so its credentials never reach later steps.
read -r -a KEEP_ARGS <<<"$RESTIC_KEEP"
SNAPSHOT=$(
	set -a
	# shellcheck source=/dev/null
	. "$RESTIC_ENV"
	set +a
	restic backup --quiet --tag friday --exclude '*.part' --exclude '.friday-backup.lock' \
		--exclude '.friday-backup.lock.d' "$BACKUP_DIR" >/dev/null
	restic forget --quiet --tag friday "${KEEP_ARGS[@]}" --prune >/dev/null
	restic snapshots --tag friday --latest 1 --json |
		grep -o '"short_id":"[0-9a-f]*"' | tail -n 1 | cut -d'"' -f4
)
[[ -n $SNAPSHOT ]] || die "restic reported no snapshot"
find "$BACKUP_DIR" -maxdepth 1 -name '*.age' -mtime +"$KEEP_LOCAL_DAYS" -delete

# 4. Status for preflight (`home_backup_fresh`).
BYTES=$(wc -c <"$OUT" | tr -d ' ')
SHA=$(sha256_of "$OUT")
[[ $BYTES =~ ^[0-9]+$ && $SHA =~ ^[0-9a-f]{64}$ ]] || die "could not measure $DUMP_NAME"
FINISHED=$(date -u +%Y-%m-%dT%H:%M:%SZ)
printf '{"v":1,"finished_at":"%s","dump":"%s","bytes":%s,"sha256":"%s","restic_snapshot":"%s","repository":"%s"}\n' \
	"$FINISHED" "$DUMP_NAME" "$BYTES" "$SHA" "$SNAPSHOT" "$REPOSITORY" >"$STATUS_DIR/status.json.part"
# Readable by the web container's bind mount (it holds no secret). The
# rename replaces the directory entry; the directory mount sees the new file.
chmod 0644 "$STATUS_DIR/status.json.part"
mv "$STATUS_DIR/status.json.part" "$STATUS_DIR/status.json"
echo "friday-backup: $DUMP_NAME ($BYTES bytes), restic snapshot $SNAPSHOT"
