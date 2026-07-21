#!/bin/sh
# Entrypoint for the Claude Code harness image (Phase 2 branch 2; write
# mode added Phase 3 branch 1). Translates this repo's own `--goal <text>`
# launch convention (harness.runner.launch_container /
# launch_implementation_container) into the real, documented Claude Code
# non-interactive invocation.
#
# Read-only discovery (no `--test-command`): `-p` (headless/print mode),
# `--output-format json` (so harness.execution._parse_claude_code_output
# can read total_cost_usd/usage back out), `--permission-mode plan`
# (read-only -- no write tool is available to the model in this mode, the
# actual containment mechanism for "read-only", not merely a documented
# convention this script trusts).
#
# Write mode (`--test-command` present, Phase 3): the model runs with real
# write access to /workspace/repo -- [TODO: verify `acceptEdits` is
# actually the correct non-interactive Claude Code permission mode for
# unattended write access against current vendor docs before this ships
# for real, the same vendor-verification discipline the read-only
# `--permission-mode plan` value already went through; this script does
# not `exec` the CLI invocation here, since the wrapper must keep running
# afterward to run the test command, quiesce the container, and write the
# trusted result file -- see below]. Result contract and process-
# quiescence discipline: docs/prompts/phase-3-harness-v2/brief.md
# §boundary (rounds 3-8) -- the result file is the ONLY thing the host
# ever trusts for this job's outcome, never container stdout/stderr,
# which the CLI agent or the test command could freely pollute with a
# spoofed-looking result.
set -eu

goal=""
test_command=""
base_commit=""
while [ $# -gt 0 ]; do
    case "$1" in
        --goal)
            goal="$2"
            shift 2
            ;;
        --test-command)
            test_command="$2"
            shift 2
            ;;
        --base-commit)
            base_commit="$2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

if [ -z "$test_command" ]; then
    # Read-only discovery path (Phase 2, unchanged).
    exec claude -p "$goal" --output-format json --permission-mode plan
fi

# --- Write mode (Phase 3 branch 1) ---

repo_dir="/workspace/repo"
result_path="/workspace/result.json"

# JSON string escaping (Codex code-review P2, branch 1 rounds 1 and 4;
# rewritten in awk round 8 to fix a single-line empty-output bug; rewritten
# again round 9): hand-rolled sed/awk escaping kept finding new gaps --
# round 1/4 fixed backslash/quote/newline, round 8 fixed the single-line
# case, and round 9 found that any OTHER JSON control character (a tab
# from a pasted Makefile recipe, a CR from CRLF text) still went into the
# result raw, which `read_implementation_result`'s `json.loads` rejects
# outright as malformed even though the run completed. This base image is
# already `node:*-slim` (the CLI itself needs node) -- delegating to
# Node's own `JSON.stringify`, which the language spec guarantees escapes
# every required character (quote, backslash, and all of U+0000-U+001F),
# closes the whole class instead of chasing individual characters one
# round at a time. `$1` is passed as a real argv element (not interpolated
# into the `-e` source), so this carries no injection risk. `.slice(1,
# -1)` strips the surrounding quotes `JSON.stringify` always adds, since
# the caller already wraps this value in its own `"%s"` below.
json_escape() {
    node -e '
        const s = JSON.stringify(process.argv[1]);
        process.stdout.write(s.slice(1, -1));
    ' -- "$1"
}

# Codex code-review P2, branch 1 round 4: an earlier version discarded
# Claude's own exit code with a blanket `|| true` -- if the CLI itself
# crashed (bad credentials, a CLI bug) but the pre-existing checkout
# already happened to pass the configured test command, the job could be
# reported a success despite the CLI never actually doing anything. The
# CLI's exit code is captured and gates whether the test command even
# runs: a failed CLI invocation skips testing entirely and reports no
# valid test result, the same fail-closed shape as an unconfigured
# `test_command`.
cli_exit_code=0
claude -p "$goal" --output-format json --permission-mode acceptEdits || cli_exit_code=$?

tests_ran=false
tests_exit_code=1
if [ "$cli_exit_code" -eq 0 ]; then
    # Codex code-review P1, branch 1 round 1: an earlier version ran the
    # test command as a bare subshell statement under `set -e` -- a
    # FAILING test (the exact case this whole contract exists to report)
    # made `set -e` abort the entire script right here, before
    # `tests_exit_code` was ever assigned and before the result file was
    # ever written, turning "tests failed" into "no result at all".
    # Wrapping it as an `if` condition is what actually exempts it from
    # `set -e` (POSIX: a command's exit status is not fatal to the script
    # when it appears as an `if`/`while` test).
    tests_ran=true
    if ( cd "$repo_dir" && sh -c "$test_command" ); then
        tests_exit_code=0
    else
        tests_exit_code=$?
    fi
fi

# Process quiescence (brief §boundary, rounds 6-8): forcibly terminate and
# reap every OTHER process still alive in this container's PID namespace
# BEFORE reading any repo state below (Codex code-review P1, branch 1
# round 2: an earlier version captured commit_sha/changed_files first --
# a daemonized child of the agent or the test command could still be
# committing/modifying files at that point, so the result file could
# describe a state the worktree left for bundling no longer matches by
# the time anything reads it). A single signal is not enough -- a killed
# process briefly remains a zombie until reaped, so this loops kill +
# reap + rescan until nothing but this script's own PID remains.
# Effective against a forked/daemonized (`setsid`) grandchild specifically
# because a container's PID namespace has nowhere else for it to hide.
#
# Codex code-review P1, branch 1 round 1: an earlier version scanned via
# `ls /proc | grep ...`, but `ls`/`grep` are themselves external processes
# -- each iteration's own scan momentarily creates new PIDs visible under
# /proc, so "remaining" was never empty and the loop never terminated,
# even for a fully successful job. This version scans via a pure shell
# glob (`/proc/[0-9]*`, expanded by the shell itself -- no subprocess) and
# uses `kill`/`wait`, both POSIX shell builtins in `sh` -- nothing in the
# scan-kill-reap cycle itself creates an observable process to chase.
#
# Extracted into a function (Codex code-review P1, branch 1 round 6): the
# git reads directly below (rev-parse/status/diff) themselves consult
# repo config the agent has full write access to (`.git/config`), and a
# setting like `core.fsmonitor` can make an ordinary `git status` spawn a
# background helper -- demonstrated concretely by Codex against this exact
# line. That helper is a NEW process, created AFTER this first quiescence
# pass already ran, so it would otherwise survive undetected into the
# result-file write below and could race mutations against it or the
# worktree. `-c core.fsmonitor=` on those specific invocations neutralizes
# the one named vector as defense in depth, but the structural fix is
# calling quiesce a second time, immediately before the trusted write,
# after every post-quiescence git read has already run -- that catches
# anything any of them might have spawned, regardless of which repo-config
# knob triggered it.
quiesce() {
    while :; do
        found_other=0
        for entry in /proc/[0-9]*; do
            pid=${entry#/proc/}
            [ "$pid" = "$$" ] && continue
            found_other=1
            kill -9 "$pid" 2>/dev/null || true
        done
        wait 2>/dev/null || true
        [ "$found_other" -eq 0 ] && break
        sleep 0.1
    done
}
quiesce

# Repo state is read only now, after quiescence -- nothing else in the
# container can still be mutating the worktree at this point.
#
# Codex code-review P1, branch 1 round 7: an earlier version silenced
# this call's own failure (`2>/dev/null || echo ""`) -- if the agent or
# the test command corrupted/removed `.git` after tests passed, this
# converted a git failure into an empty string and let the wrapper still
# report `"passed": true` with an invalid commit. Wrapped in `if`/`!` (the
# same `set -e`-exemption idiom used for the test command above) so a
# failure here is caught and forces the result closed instead of aborting
# the script or being swallowed.
commit_sha=""
if ! commit_sha=$(git -C "$repo_dir" -c core.fsmonitor= rev-parse HEAD 2>/dev/null); then
    tests_exit_code=1
    commit_sha=""
fi

# Codex code-review P1, branch 1 round 10: the agent has full write
# access to `.git`, so it can make `git status --porcelain` report clean
# despite real uncommitted changes -- either by setting the skip-worktree
# bit on a modified tracked file (`git update-index --skip-worktree`,
# which tells git to assume the working-tree copy matches the index and
# stop diffing it) or by setting `status.showUntrackedFiles=no` in repo
# config to hide new untracked files entirely. Both are demonstrated,
# repo-local ways to make this check lie. Cleared/overridden right
# before reading status: every skip-worktree path found via `ls-files -v`
# (marked with a literal capital `S`) has the bit cleared so a real
# modification underneath it becomes visible again, and `-c
# status.showUntrackedFiles=all` overrides whatever the repo's own config
# says for this one invocation.
git -C "$repo_dir" -c core.fsmonitor= ls-files -v 2>/dev/null \
    | awk '$1 == "S" {print $2}' \
    | while IFS= read -r skip_worktree_path; do
        git -C "$repo_dir" update-index --no-skip-worktree -- "$skip_worktree_path" 2>/dev/null || true
    done

# Codex code-review P1, branch 1 round 4: if the agent (or the test
# command) left uncommitted changes, the test command above ran against
# that dirty working tree, but `commit_sha` above only reflects the last
# COMMIT -- and `create_bundle`/`push_bundled_commit` only ever bundle
# commits, never uncommitted working-tree state. A "passing" result in
# that case would report a commit that doesn't actually contain what was
# tested, and pushing it would ship something different from what passed.
# Fails closed rather than silently trusting a commit that doesn't match
# reality. Round 7: the command's OWN failure (not just non-empty output)
# is now also fail-closed, for the same reason as the rev-parse fix above.
porcelain_output=""
if ! porcelain_output=$(git -C "$repo_dir" -c core.fsmonitor= -c status.showUntrackedFiles=all status --porcelain 2>/dev/null); then
    tests_exit_code=1
elif [ -n "$porcelain_output" ]; then
    tests_exit_code=1
fi

# Codex code-review P2, branch 1 round 4: `base_commit` (Codex code-review
# P2, branch 1 round 2 originally passed a *ref* here, `origin/
# <default_branch>` -- but that ref lives inside `/workspace/repo/.git`,
# which the agent has full write access to; rewriting `refs/remotes/
# origin/<default_branch>` or `packed-refs` to point at HEAD would make
# this diff wrongly report zero changed files despite real committed
# changes). `base_commit` is now the pre-launch base commit's own SHA,
# resolved and passed by the HOST before the container ever started --
# an immutable value the agent's own writes inside the container cannot
# retroactively change, unlike a mutable ref name.
if [ -n "$base_commit" ]; then
    diff_base="$base_commit"
else
    diff_base="HEAD"
fi
# Round 7: same fail-closed treatment as rev-parse/status above -- a
# failing diff (e.g. `.git` corrupted after tests passed) must not be
# silently treated as "no changed files" in an otherwise-passing result.
diff_output=""
if ! diff_output=$(git -C "$repo_dir" -c core.fsmonitor= diff --name-only "$diff_base"..HEAD 2>/dev/null); then
    tests_exit_code=1
    diff_output=""
fi
if [ -n "$diff_output" ]; then
    changed_files_json=$(
        printf '%s\n' "$diff_output" \
            | awk 'BEGIN{printf "["} {if(NR>1)printf ","; gsub(/\\/,"\\\\"); gsub(/"/,"\\\""); printf "\"%s\"", $0} END{printf "]"}'
    )
else
    changed_files_json="[]"
fi

# Second quiescence pass (Codex code-review P1, branch 1 round 6): the
# git reads above are themselves capable of spawning a process via
# repo-configured helpers (see the comment above the first `quiesce`
# call), and that would happen AFTER the first pass already ran. Calling
# quiesce again here, immediately before the trusted write below, closes
# that gap regardless of which specific git invocation or config knob
# might have triggered it.
quiesce

test_passed=false
[ "$tests_exit_code" -eq 0 ] && test_passed=true

escaped_test_command=$(json_escape "$test_command")
result_json=$(printf '{"tests": {"ran": %s, "exit_code": %s, "passed": %s, "command": "%s"}, "commit_sha": "%s", "changed_files": %s}\n' \
    "$tests_ran" "$tests_exit_code" "$test_passed" "$escaped_test_command" "$commit_sha" "$changed_files_json")

# Codex code-review P1, branch 1 round 3: `/workspace` is ONE read-write
# bind mount shared with the agent/test-command process (same uid, no
# privilege separation -- rounds 5-6 already ruled that out) -- nothing
# actually stops the agent from planting its own `result.json` at ANY
# point during its own run, well before this point. An earlier version
# used noclobber here, refusing to overwrite an existing file -- but that
# gets the direction backwards: if the agent planted a decoy EARLIER,
# noclobber would make the wrapper's own legitimate write FAIL, leaving
# the decoy as the only content at this path for the host to trust. The
# actual guarantee comes from ordering, not from refusing to overwrite:
# quiescence (above) has already confirmed nothing else is alive in this
# container, so the wrapper is the sole remaining actor and can safely
# remove whatever is there -- stale decoy, symlink, or nothing -- and
# write the real result. Last-writer-wins is safe exactly because there
# is no other writer left to race it.
#
# Codex code-review P1, branch 1 round 8: an earlier version went
# straight to `rm -f` -- but the agent can plant a believable decoy
# result.json and then strip write (or execute/search) permission from
# `/workspace` itself before quiescence, a purely filesystem-state attack
# that no amount of killing processes touches. Under `set -e`, that made
# `rm -f` fail and abort the script right here, leaving the decoy as the
# only content ever written -- and nothing on the host side checks this
# container's own exit code, only `result.json`'s content (see
# `read_implementation_result`), so an aborted script here is silently
# indistinguishable from a legitimate one. The fix relies on a real
# invariant: this wrapper runs as the SAME uid that owns every path under
# `/workspace` (the `--user {host_uid}:{host_gid}` convention), and
# chmod(2)'s permission check is based on ownership, never on a path's
# current mode bits -- so this chmod succeeds regardless of what mode the
# agent set, restoring exactly what `rm`/the write below need.
chmod u+rwx "$(dirname "$result_path")"
rm -f "$result_path"
printf '%s' "$result_json" > "$result_path"
