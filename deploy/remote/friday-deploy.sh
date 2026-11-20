#!/usr/bin/env bash
# friday-deploy.sh -- the forced command behind Friday's deploy key
# (Phase 5 branch 6, ADR-010 decision 7, brief D10/Q12).
#
# Installed root-owned (0755) at /opt/friday-deploy/friday-deploy.sh and
# bound to Friday's public key in the dedicated deploy user's
# ~/.ssh/authorized_keys:
#
#   command="/opt/friday-deploy/friday-deploy.sh",restrict ssh-ed25519 AAAA... friday-deploy
#
# sshd runs this script for every connection made with that key and puts the
# client's requested command in SSH_ORIGINAL_COMMAND. Exactly four commands
# are accepted, the grammar Friday itself enforces on its side
# (backend/deploys/validation.py):
#
#   status   <app>          print app/sha/branch/remote/previous (read-only)
#   health   <app>          run the app's local health command
#   deploy   <app> <sha>    fetch; <sha> must be an ancestor of
#                           origin/<TRACKED_BRANCH>; record the live SHA as
#                           "previous"; check out; build; restart
#   rollback <app> <sha>    <sha> must equal the recorded "previous"; check
#                           out; build; restart; forget "previous"
#
# <app> matches [a-z0-9][a-z0-9-]{0,62}; <sha> is 40 lowercase hex. What an
# app's build/restart/health commands are is NOT Friday's to choose: they
# live in root-owned per-app config, /etc/friday-deploy/apps/<app>.conf
# (see apps/example.conf). A compromised runner gets four verbs, not a shell.
#
# Exit codes (the runner maps them, backend/deploys/execution.py):
#   0   ok
#   1   restart failed after the new checkout (live state changed)
#   64  usage: not one of the four commands
#   65  refused, nothing changed (unknown app, bad config, not an ancestor,
#       already live, rollback SHA not the recorded previous, dirty tree)
#   66  build failed; the previous checkout was restored, nothing restarted
#   69  unavailable, nothing changed (app dir missing, fetch failed)
#   75  busy: another deploy/rollback of this app holds its lock

set -euo pipefail
umask 027

# Constants (the tests rewrite these lines to point at a temp tree).
readonly CONFIG_DIR=/etc/friday-deploy/apps
readonly STATE_DIR=/var/lib/friday-deploy
readonly REQUIRE_ROOT_OWNED_CONFIG=1
readonly SAFE_PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

PATH="$SAFE_PATH"
export PATH
export LC_ALL=C
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES

readonly EX_FAILED=1
readonly EX_USAGE=64
readonly EX_REFUSED=65
readonly EX_BUILD_FAILED=66
readonly EX_UNAVAILABLE=69
readonly EX_BUSY=75

die() {
    local code=$1
    shift
    printf 'friday-deploy: %s\n' "$*" >&2
    exit "$code"
}

# --- parse the one command -------------------------------------------------

cmd="${SSH_ORIGINAL_COMMAND-}"
[ -n "$cmd" ] || die "$EX_USAGE" "no command given; this key runs only status|health|deploy|rollback"
[ "${#cmd}" -le 128 ] || die "$EX_USAGE" "command too long"

re_plain='^(status|health) ([a-z0-9][a-z0-9-]{0,62})$'
re_sha='^(deploy|rollback) ([a-z0-9][a-z0-9-]{0,62}) ([0-9a-f]{40})$'
if [[ $cmd =~ $re_plain ]]; then
    verb="${BASH_REMATCH[1]}"
    app="${BASH_REMATCH[2]}"
    sha=""
elif [[ $cmd =~ $re_sha ]]; then
    verb="${BASH_REMATCH[1]}"
    app="${BASH_REMATCH[2]}"
    sha="${BASH_REMATCH[3]}"
else
    die "$EX_USAGE" "refused: not an allowed command"
fi

# --- load the root-owned per-app config -----------------------------------

check_trusted() {
    # Not group/other-writable, and root-owned unless the test harness
    # rewrote REQUIRE_ROOT_OWNED_CONFIG. `ls -ldn` is portable (GNU/BSD).
    local path=$1 listing perms uid
    listing=$(ls -ldn -- "$path") || die "$EX_REFUSED" "refused: cannot stat $path"
    # shellcheck disable=SC2086
    set -- $listing
    perms=$1
    uid=$3
    if [ "${perms:5:1}" = "w" ] || [ "${perms:8:1}" = "w" ]; then
        die "$EX_REFUSED" "refused: $path is group- or world-writable"
    fi
    if [ "$REQUIRE_ROOT_OWNED_CONFIG" = "1" ] && [ "$uid" != "0" ]; then
        die "$EX_REFUSED" "refused: $path is not owned by root"
    fi
}

conf="$CONFIG_DIR/$app.conf"
if [ ! -f "$conf" ] || [ -L "$conf" ]; then
    die "$EX_REFUSED" "refused: unknown app '$app'"
fi
check_trusted "$CONFIG_DIR"
check_trusted "$conf"

APP_DIR=""
TRACKED_BRANCH=""
BUILD_CMD=""
RESTART_CMD=""
HEALTH_CMD=""
# shellcheck source=/dev/null
. "$conf"
[ -n "$APP_DIR" ] && [ -n "$TRACKED_BRANCH" ] && [ -n "$BUILD_CMD" ] \
    && [ -n "$RESTART_CMD" ] && [ -n "$HEALTH_CMD" ] \
    || die "$EX_REFUSED" "refused: $conf must set APP_DIR, TRACKED_BRANCH, BUILD_CMD, RESTART_CMD, HEALTH_CMD"
re_branch='^[A-Za-z0-9._/-]{1,200}$'
if ! [[ $TRACKED_BRANCH =~ $re_branch ]] || [[ $TRACKED_BRANCH == -* ]] || [[ $TRACKED_BRANCH == *..* ]]; then
    die "$EX_REFUSED" "refused: invalid TRACKED_BRANCH in $conf"
fi
[ -d "$APP_DIR/.git" ] || die "$EX_UNAVAILABLE" "app directory $APP_DIR is not a git checkout"
[ -d "$STATE_DIR" ] || die "$EX_UNAVAILABLE" "state directory $STATE_DIR is missing"

previous_file="$STATE_DIR/$app.previous"
lock_dir="$STATE_DIR/$app.lock"

git_app() {
    git -C "$APP_DIR" "$@"
}

run_in_app() {
    (cd "$APP_DIR" && bash -c "$1")
}

acquire_lock() {
    # mkdir is atomic on every filesystem; a lock whose holder died is
    # reclaimed (same deploy user, so `kill -0` sees its processes).
    if ! mkdir "$lock_dir" 2>/dev/null; then
        local holder
        holder=$(cat "$lock_dir/pid" 2>/dev/null || true)
        if [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null; then
            die "$EX_BUSY" "busy: another deploy of '$app' is running"
        fi
        rm -rf "$lock_dir"
        mkdir "$lock_dir" 2>/dev/null || die "$EX_BUSY" "busy: another deploy of '$app' is running"
    fi
    printf '%s\n' "$$" > "$lock_dir/pid"
    trap 'rm -rf "$lock_dir"' EXIT
}

live_sha() {
    git_app rev-parse --verify --quiet HEAD || die "$EX_UNAVAILABLE" "cannot resolve HEAD in $APP_DIR"
}

require_clean_tree() {
    if [ -n "$(git_app status --porcelain --untracked-files=no)" ]; then
        die "$EX_REFUSED" "refused: $APP_DIR has local modifications"
    fi
}

# Checks out $1, builds, restarts. A failed build restores $2 (nothing was
# restarted, so nothing live changed); a failed restart is reported as such.
switch_to() {
    local new=$1 old=$2
    git_app checkout --quiet --detach "$new" || die "$EX_REFUSED" "refused: checkout of $new failed"
    if ! run_in_app "$BUILD_CMD"; then
        git_app checkout --quiet --detach "$old" || true
        die "$EX_BUILD_FAILED" "build failed; checkout restored to $old, nothing restarted"
    fi
    run_in_app "$RESTART_CMD" || die "$EX_FAILED" "restart failed after switching to $new"
}

case "$verb" in
status)
    head=$(live_sha)
    # The configured URL verbatim (not insteadOf-expanded): the runner
    # compares it with the target's repo_git_url canonically.
    remote=$(git_app config --get remote.origin.url 2>/dev/null || true)
    previous=$(cat "$previous_file" 2>/dev/null || true)
    printf 'app=%s\nsha=%s\nbranch=%s\nremote=%s\nprevious=%s\n' \
        "$app" "$head" "$TRACKED_BRANCH" "$remote" "$previous"
    ;;
health)
    if run_in_app "$HEALTH_CMD"; then
        echo "healthy"
    else
        die "$EX_FAILED" "health check failed"
    fi
    ;;
deploy)
    acquire_lock
    git_app fetch --quiet --prune origin "+refs/heads/$TRACKED_BRANCH:refs/remotes/origin/$TRACKED_BRANCH" \
        || die "$EX_UNAVAILABLE" "git fetch failed"
    git_app cat-file -e "$sha^{commit}" 2>/dev/null || die "$EX_REFUSED" "refused: unknown commit $sha"
    # Only merged commits deploy (brief D10): Friday never merges.
    git_app merge-base --is-ancestor "$sha" "refs/remotes/origin/$TRACKED_BRANCH" \
        || die "$EX_REFUSED" "refused: $sha is not on origin/$TRACKED_BRANCH"
    current=$(live_sha)
    [ "$current" != "$sha" ] || die "$EX_REFUSED" "refused: $sha is already live"
    require_clean_tree
    printf '%s\n' "$current" > "$previous_file.tmp"
    mv -f "$previous_file.tmp" "$previous_file"
    switch_to "$sha" "$current"
    echo "deployed $sha (previous $current)"
    ;;
rollback)
    acquire_lock
    previous=$(cat "$previous_file" 2>/dev/null || true)
    re_full='^[0-9a-f]{40}$'
    if ! [[ $previous =~ $re_full ]] || [ "$previous" != "$sha" ]; then
        die "$EX_REFUSED" "refused: $sha is not the recorded previous deploy"
    fi
    current=$(live_sha)
    [ "$current" != "$sha" ] || die "$EX_REFUSED" "refused: $sha is already live"
    require_clean_tree
    switch_to "$sha" "$current"
    # One rollback per recorded deploy: a further one needs a new deploy.
    rm -f "$previous_file"
    echo "rolled back to $sha (from $current)"
    ;;
esac
