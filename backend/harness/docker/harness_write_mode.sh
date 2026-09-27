#!/bin/sh
# Shared write-mode (implementation job) logic for both harness wrapper
# images (Phase 3 branch 5, re-planned 2026-09-27 -- brief "Closeout
# re-plan 2026-09-27", plan-review round 9 item 1). Sourced by
# claude_code_run.sh / codex_cli_run.sh, which each define `run_cli`
# (the vendor-specific CLI invocation, printing its transcript/result on
# stdout) and then call `harness_write_mode_main`.
#
# Trust model (brief §boundary, rounds 3-8, unchanged): the result file
# this script writes is the ONLY thing the host trusts for the job's
# outcome. The agent runs as the same uid with full write access to
# /workspace (including `.git`), so every repo read below first undoes
# whatever repo-local state the agent could have planted:
#
# 1. The CLI runs; its stdout is captured into a wrapper-owned shell
#    variable (never a file the agent could rewrite) and its exit code
#    kept.
# 2. `quiesce` kills and reaps every other process in the container's
#    PID namespace.
# 3. `restore_git_state` restores the pristine `.git/config` snapshotted
#    before the CLI ran (dropping agent-set filters, fsmonitor, sparse
#    checkout, hooksPath, ...), removes `.git/info` (exclude/attributes/
#    sparse-checkout/grafts) and `.git/hooks`, and rebuilds the index from
#    HEAD -- which drops every assume-unchanged / skip-worktree bit at
#    once, for every path spelling, instead of parsing `ls-files -v`.
# 4. The WRAPPER commits (`add -A`, hooks disabled, fixed identity,
#    wrapper-authored message): everything the agent left in the tree is
#    in the commit, except paths the committed .gitignore excludes. HEAD is
#    first re-pointed at the branch the host checked out and soft-reset to
#    the base commit, so the produced history is exactly ONE wrapper
#    commit on top of base -- any commits the agent made itself (whose
#    messages, authors or intermediate trees could carry data the final
#    diff doesn't show) are never bundled, reviewed or pushed.
# 5. A run whose produced tree equals the base tree fails closed as
#    `no_changes` -- tests are not run.
# 6. `git clean -ffdx` + `git reset --hard HEAD`: the test command runs
#    against a pristine checkout of exactly the produced commit (no
#    ignored/untracked helper file survives). A fresh clone has no
#    ignored files to preserve, so the test command must provision its
#    own dependencies (documented contract, backend/harness/docker/
#    README.md).
# 7. Tests run with the CLI API keys removed from their environment.
# 8. quiesce + restore again, then a final dirty-tree check: a test
#    command that modifies tracked files or leaves new non-ignored files
#    fails closed.
# 9. Final quiesce, result file written, and the CLI's captured stdout
#    emitted as ONE wrapper-framed line (`JARVIS_HARNESS_CLI_OUTPUT_V1
#    <base64>`) -- the host parses only the last well-formed such line for
#    cost/usage and the implementation transcript (plan-review round 9
#    item 2). Nothing else is alive to print after it.
#
# `HARNESS_WORKSPACE_DIR` is a plain shell variable (not read from the
# environment): the production wrappers never set it, so it is always
# /workspace in a real container. Tests set it after sourcing this file.

HARNESS_CLI_OUTPUT_PREFIX="JARVIS_HARNESS_CLI_OUTPUT_V1"
HARNESS_WORKSPACE_DIR="/workspace"

json_escape() {
    # Node's JSON.stringify escapes every required character (quote,
    # backslash, U+0000-U+001F) -- see the round-9 note in the original
    # wrappers. `$1` is a real argv element, never interpolated into the
    # `-e` source.
    node -e '
        const s = JSON.stringify(process.argv[1]);
        process.stdout.write(s.slice(1, -1));
    ' -- "$1"
}

nul_list_to_json() {
    # stdin: NUL-separated paths (`git diff --name-only -z`). Emits a JSON
    # array; handles spaces, quotes, newlines and non-UTF-8 bytes (decoded
    # with replacement, so the result file is always valid UTF-8 JSON).
    node -e '
        const chunks = [];
        process.stdin.on("data", (c) => chunks.push(c));
        process.stdin.on("end", () => {
            const raw = Buffer.concat(chunks).toString("utf8");
            const items = raw.split("\0").filter((p) => p.length > 0);
            process.stdout.write(JSON.stringify(items));
        });
    '
}

quiesce() {
    # Kill + reap every OTHER process in this PID namespace until none
    # remain (brief §boundary rounds 6-8). Pure shell glob + builtins, so
    # the scan itself never creates a process to chase. The `-e` guard
    # keeps an unmatched glob (no /proc) from looping forever on the
    # literal pattern.
    while :; do
        found_other=0
        for entry in /proc/[0-9]*; do
            [ -e "$entry" ] || continue
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

sgit() {
    # Every git call after the CLI ran goes through here: no system/global
    # config (HOME is agent-writable), no replace refs, no hooks, no
    # fsmonitor, no excludes file, no untracked cache, quoted paths.
    GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_NO_REPLACE_OBJECTS=1 \
        GIT_TERMINAL_PROMPT=0 GIT_OPTIONAL_LOCKS=0 \
        git -C "$repo_dir" \
        -c core.fsmonitor= -c core.hooksPath=/dev/null -c core.excludesFile=/dev/null \
        -c core.untrackedCache=false -c core.quotePath=true -c commit.gpgSign=false \
        -c core.sparseCheckout=false "$@"
}

restore_git_state() {
    # Fails (non-zero) if `.git` is no longer a real directory -- the
    # caller treats that as a failed run, never as "no changes".
    git_dir="$repo_dir/.git"
    [ -d "$git_dir" ] && [ ! -L "$git_dir" ] || return 1
    # chmod(2) checks ownership, not mode bits: restores access even if
    # the agent stripped it (same invariant as the result-dir chmod).
    chmod u+rwx "$git_dir" || return 1
    [ -d "$git_dir/objects/info" ] && chmod u+rwx "$git_dir/objects/info"
    rm -rf "$git_dir/config" "$git_dir/info" "$git_dir/hooks" \
        "$git_dir/objects/info/alternates" "$git_dir/index" || return 1
    printf '%s\n' "$pristine_git_config" > "$git_dir/config" || return 1
    # Rebuild the index from HEAD (mixed reset keeps the working tree):
    # no assume-unchanged / skip-worktree bit survives this.
    sgit reset -q >/dev/null 2>&1 || return 1
}

harness_write_mode_main() {
    repo_dir="$HARNESS_WORKSPACE_DIR/repo"
    result_path="$HARNESS_WORKSPACE_DIR/result.json"

    # Snapshotted BEFORE the agent runs: the host-created clone's own
    # config and checked-out branch are the only ones trusted afterwards.
    pristine_git_config=$(cat "$repo_dir/.git/config")
    branch_ref=$(git -C "$repo_dir" symbolic-ref -q HEAD)

    cli_exit_code=0
    cli_output=$(run_cli) || cli_exit_code=$?

    quiesce

    tests_ran=false
    tests_exit_code=1
    no_changes=false
    run_ok=true
    commit_sha=""
    changed_files_json="[]"

    if [ "$cli_exit_code" -ne 0 ]; then
        # A crashed CLI never gets its pre-existing checkout tested (branch
        # 1 round 4) -- fail closed without committing anything.
        run_ok=false
    elif ! restore_git_state; then
        run_ok=false
    elif [ -z "$base_commit" ] || [ -z "$branch_ref" ]; then
        run_ok=false
    elif ! sgit symbolic-ref HEAD "$branch_ref" >/dev/null 2>&1 \
        || ! sgit reset -q --soft "$base_commit" >/dev/null 2>&1; then
        # Agent history is discarded here: HEAD is back on the host's
        # branch, at base, with the index still holding the agent's tree.
        run_ok=false
    else
        if ! sgit add -A >/dev/null 2>&1; then
            run_ok=false
        elif ! sgit diff --cached --quiet >/dev/null 2>&1; then
            goal_line=$(printf '%s\n' "$goal" | head -n 1 | cut -c1-200)
            if ! printf 'harness: implementation\n\nGoal: %s\n' "$goal_line" \
                | GIT_AUTHOR_NAME="Jarvis Harness" GIT_AUTHOR_EMAIL="harness@jarvis.invalid" \
                    GIT_COMMITTER_NAME="Jarvis Harness" GIT_COMMITTER_EMAIL="harness@jarvis.invalid" \
                    sgit commit -q --no-verify -F - >/dev/null 2>&1; then
                run_ok=false
            fi
        fi
    fi

    if [ "$run_ok" = true ]; then
        if ! commit_sha=$(sgit rev-parse --verify HEAD 2>/dev/null); then
            run_ok=false
            commit_sha=""
        elif [ -n "$base_commit" ] && sgit diff --quiet "$base_commit" HEAD -- >/dev/null 2>&1; then
            # Produced tree == base tree: nothing to review or push.
            no_changes=true
        fi
    fi

    if [ "$run_ok" = true ] && [ "$no_changes" = false ]; then
        if sgit clean -ffdxq >/dev/null 2>&1 && sgit reset -q --hard HEAD >/dev/null 2>&1; then
            tests_ran=true
            # `if` exempts the failing test command from `set -e` (branch
            # 1 round 1). API keys are removed from its environment: the
            # test command is agent-modified repo code.
            if (cd "$repo_dir" && env -u ANTHROPIC_API_KEY -u CODEX_API_KEY -u OPENAI_API_KEY \
                sh -c "$test_command"); then
                tests_exit_code=0
            else
                tests_exit_code=$?
                [ "$tests_exit_code" -eq 0 ] && tests_exit_code=1
            fi
        else
            run_ok=false
        fi
    fi

    quiesce

    if [ "$run_ok" = true ] && [ "$no_changes" = false ]; then
        if ! restore_git_state; then
            tests_exit_code=1
        else
            # The produced commit must still be HEAD, and the test command
            # must not have left tracked modifications or new files.
            head_now=""
            if ! head_now=$(sgit rev-parse --verify HEAD 2>/dev/null) \
                || [ "$head_now" != "$commit_sha" ]; then
                tests_exit_code=1
            fi
            porcelain_output=""
            if ! porcelain_output=$(sgit status --porcelain --untracked-files=all 2>/dev/null); then
                tests_exit_code=1
            elif [ -n "$porcelain_output" ]; then
                tests_exit_code=1
            fi
        fi
        # Via a temp file, not a pipeline: POSIX sh reports only the last
        # command's status, which would mask a failing `git diff` as an
        # empty change list (Codex code-review P2, branch 5 round 3).
        # Nothing else is alive to touch the file (quiesced above).
        diff_base="${base_commit:-HEAD}"
        diff_tmp=$(mktemp)
        if sgit diff --name-only -z "$diff_base" "$commit_sha" -- >"$diff_tmp" 2>/dev/null \
            && diff_output_json=$(nul_list_to_json <"$diff_tmp"); then
            changed_files_json="$diff_output_json"
        else
            tests_exit_code=1
        fi
        rm -f "$diff_tmp"
    fi

    [ "$run_ok" = true ] || tests_exit_code=1

    quiesce

    test_passed=false
    [ "$tests_exit_code" -eq 0 ] && [ "$tests_ran" = true ] && test_passed=true

    escaped_test_command=$(json_escape "$test_command")
    result_json=$(printf '{"tests": {"ran": %s, "exit_code": %s, "passed": %s, "command": "%s"}, "commit_sha": "%s", "changed_files": %s, "no_changes": %s}\n' \
        "$tests_ran" "$tests_exit_code" "$test_passed" "$escaped_test_command" "$commit_sha" \
        "$changed_files_json" "$no_changes")

    # Last-writer-wins is safe: quiescence confirmed no other writer is
    # left (branch 1 round 3); chmod restores access to the result dir
    # whatever mode the agent set (round 8).
    chmod u+rwx "$(dirname "$result_path")"
    rm -rf "$result_path"
    printf '%s' "$result_json" > "$result_path"

    printf '\n%s %s\n' "$HARNESS_CLI_OUTPUT_PREFIX" \
        "$(printf '%s' "$cli_output" | base64 | tr -d '\n')"
}
