#!/bin/sh
# Entrypoint for the Codex CLI harness image (Phase 2 branch 2; write
# mode added Phase 3 branch 1). NOT wired as a selectable
# HarnessJob.agent/HarnessImplementationJob.agent until branch 3's own
# vendor-terms and cost-reporting verification clears it for each
# capability separately (discovery already cleared, implementation/review
# each need their own go/no-go per brief §boundary's capability-matrix
# split) -- this image exists so that verification has something real to
# test against.
#
# Read-only discovery/review (no `--test-command`): `codex exec --sandbox
# read-only` is this repo's own already-proven non-interactive invocation
# (the orchestration protocol this session itself follows).
#
# `2>&1` (Codex code-review P1, branch 3 round 11, confirmed by directly
# running `codex exec` and inspecting its two streams): the CLI writes its
# actual transcript -- session banner, echoed prompt, reasoning/tool
# output -- to STDERR, with only the bare final-answer line on STDOUT.
# `runner.capture_logs()` (`docker logs`) only ever returns the
# container's stdout, so without this redirect, a review's `raw_output`
# would be just that one bare line -- not the real transcript the review
# pipeline's gateway call is supposed to summarize a verdict from, and not
# obviously empty either (so it wouldn't even fail closed). Combining the
# streams here, before either ever leaves the container, is the narrowest
# fix: it doesn't touch `capture_logs()` itself (shared by every job
# type) or Claude Code's own wrapper (which uses `--output-format json`
# and isn't affected).
#
# Write mode (`--test-command` present, Phase 3): [TODO: verify the
# correct Codex CLI sandbox mode for unattended write access against
# current vendor docs before this ships for real -- `--sandbox
# workspace-write` is this script's best-grounded guess, not yet verified,
# the same vendor-verification discipline the read-only `--sandbox
# read-only` value already went through in branch 2]. This script does
# not `exec` the CLI invocation in write mode, since the wrapper must keep
# running afterward to run the test command, quiesce the container, and
# write the trusted result file -- see below. Result contract and
# process-quiescence discipline: docs/prompts/phase-3-harness-v2/brief.md
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
    # Read-only discovery/review path (Phase 2, unchanged except for the
    # `2>&1` above).
    exec codex exec --sandbox read-only "$goal" 2>&1
fi

# --- Write mode (Phase 3 branch 1) ---

repo_dir="/workspace/repo"
result_path="/workspace/result.json"

# JSON string escaping (Codex code-review P2, branch 1 rounds 1 and 4;
# awk rewrite round 8; delegated to Node's JSON.stringify round 9 to close
# the whole class of control-character gaps at once) -- see
# claude_code_run.sh for the full rationale; identical here.
json_escape() {
    node -e '
        const s = JSON.stringify(process.argv[1]);
        process.stdout.write(s.slice(1, -1));
    ' -- "$1"
}

# Codex code-review P2, branch 1 round 4: the CLI's own exit code gates
# whether the test command even runs -- see claude_code_run.sh for the
# full rationale; identical fix here.
cli_exit_code=0
codex exec --sandbox workspace-write "$goal" || cli_exit_code=$?

# Codex code-review P1, branch 1 round 1: wrapped as an `if` condition so
# `set -e` does not abort the script on a failing test command -- see
# claude_code_run.sh for the full rationale; identical fix here.
tests_ran=false
tests_exit_code=1
if [ "$cli_exit_code" -eq 0 ]; then
    tests_ran=true
    if ( cd "$repo_dir" && sh -c "$test_command" ); then
        tests_exit_code=0
    else
        tests_exit_code=$?
    fi
fi

# Process quiescence (brief §boundary, rounds 6-8) -- see claude_code_run.sh
# for the full rationale and the round-1 code-review fix (pure shell glob
# + builtins, no `ls`/`grep` subprocess, which would otherwise keep
# `remaining` non-empty forever). Codex code-review P1, branch 1 round 2:
# this must run BEFORE reading repo state below -- a daemonized child
# could still be committing/modifying files after tests pass, so reading
# commit_sha/changed_files first could describe a state the worktree left
# for bundling no longer matches by the time anything reads it.
#
# Extracted into a function (Codex code-review P1, branch 1 round 6) --
# see claude_code_run.sh for the full rationale: the git reads below
# themselves consult agent-writable repo config (e.g. `core.fsmonitor`
# can make `git status` spawn a background helper), so a second call
# right before the final write is needed to catch anything they spawn.
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

# Repo state is read only now, after quiescence.
#
# Codex code-review P1, branch 1 round 7: fail closed on the git read's
# OWN failure, not just on empty/non-empty output -- see claude_code_run.sh
# for the full rationale; identical fix here.
commit_sha=""
if ! commit_sha=$(git -C "$repo_dir" -c core.fsmonitor= rev-parse HEAD 2>/dev/null); then
    tests_exit_code=1
    commit_sha=""
fi

# Codex code-review P1, branch 1 round 10: clear agent-set skip-worktree
# bits and override status.showUntrackedFiles -- see claude_code_run.sh
# for the full rationale; identical fix here.
git -C "$repo_dir" -c core.fsmonitor= ls-files -v 2>/dev/null \
    | awk '$1 == "S" {print $2}' \
    | while IFS= read -r skip_worktree_path; do
        git -C "$repo_dir" update-index --no-skip-worktree -- "$skip_worktree_path" 2>/dev/null || true
    done

# Codex code-review P1, branch 1 round 4: a dirty (uncommitted) worktree
# means the test command ran against state that `commit_sha` above
# doesn't fully capture, and bundling/pushing only ever carries commits --
# see claude_code_run.sh for the full rationale; identical fix here.
# Round 7: the command's own failure is now also fail-closed.
porcelain_output=""
if ! porcelain_output=$(git -C "$repo_dir" -c core.fsmonitor= -c status.showUntrackedFiles=all status --porcelain 2>/dev/null); then
    tests_exit_code=1
elif [ -n "$porcelain_output" ]; then
    tests_exit_code=1
fi

# Codex code-review P2, branch 1 round 4: `base_commit` is the host-
# resolved, immutable pre-launch base commit SHA -- not a mutable ref the
# agent's own writes inside `/workspace/repo/.git` could rewrite -- see
# claude_code_run.sh for the full rationale; identical fix here.
if [ -n "$base_commit" ]; then
    diff_base="$base_commit"
else
    diff_base="HEAD"
fi
# Round 7: fail closed on diff failure -- see claude_code_run.sh for the
# full rationale; identical fix here.
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

# Second quiescence pass (Codex code-review P1, branch 1 round 6) -- see
# claude_code_run.sh for the full rationale: closes whatever the git
# reads above might have spawned, before the trusted write below.
quiesce

test_passed=false
[ "$tests_exit_code" -eq 0 ] && test_passed=true

escaped_test_command=$(json_escape "$test_command")
result_json=$(printf '{"tests": {"ran": %s, "exit_code": %s, "passed": %s, "command": "%s"}, "commit_sha": "%s", "changed_files": %s}\n' \
    "$tests_ran" "$tests_exit_code" "$test_passed" "$escaped_test_command" "$commit_sha" "$changed_files_json")

# Codex code-review P1, branch 1 round 3 -- see claude_code_run.sh for
# the full rationale: quiescence above already guarantees the wrapper is
# the sole remaining actor, so an unconditional remove-then-write is what
# actually provides the guarantee here, not noclobber (which would fail
# against an agent-planted decoy instead of replacing it).
#
# Codex code-review P1, branch 1 round 8: restore owner rwx on the
# containing directory first -- a filesystem-permission attack (the agent
# planting a decoy then stripping /workspace's own write/execute bit) is
# not a process quiescence handles, and would otherwise abort this script
# via `set -e` before the real write, leaving the decoy as the only
# content -- see claude_code_run.sh for the full rationale; identical fix
# here.
chmod u+rwx "$(dirname "$result_path")"
rm -f "$result_path"
printf '%s' "$result_json" > "$result_path"
