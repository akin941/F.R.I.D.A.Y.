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
driver=""
while [ $# -gt 0 ]; do
    case "$1" in
        --driver)
            driver="$2"
            shift 2
            ;;
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

# --- Write mode (Phase 3; shared logic since branch 5) ---
#
# Everything after the CLI invocation -- commit ownership, pristine test
# checkout, quiescence, dirty-tree check, result file, cost framing -- is
# shared with the other wrapper image: see harness_write_mode.sh for the
# full sequence and its trust rationale.
# shellcheck source=harness_write_mode.sh
. "$(dirname "$0")/harness_write_mode.sh"

if [ "$driver" = "agent_sdk" ]; then
    # Driver mode (Phase 10 brief D10, branch 7): the Friday agent bridge
    # runs the pinned claude-agent-sdk and speaks friday-agent-bridge/v1
    # (one JSON object per line) over this container's stdin/stdout to the
    # host driver (backend/harness/drivers/claude_agent_sdk.py). `-I`: no
    # PYTHONPATH, no user site, no script-dir imports from the repo. Its
    # stderr is dropped so it never interleaves with protocol frames. The
    # agent's permission policy (file tools inside the repo, Bash only for
    # the exact test command, everything else relayed for the owner's
    # approval) lives in the bridge; the test command is handed to it here.
    # The bridge exits 0 when the host closes stdin after the last turn and
    # non-zero if stdin closes mid-turn; the shared write-mode sequence then
    # commits, tests and writes the result file exactly as for `claude -p`.
    harness_driver_mode=true
    run_cli() {
        /opt/friday-bridge/bin/python -I "$(dirname "$0")/claude_agent_bridge.py" \
            --test-command "$test_command" 2>/dev/null
    }
elif [ -n "$driver" ]; then
    echo "unknown driver: $driver" >&2
    exit 64
else
    # `--output-format json`: the captured stdout is Claude Code's JSON
    # result object -- the host reads total_cost_usd/usage from it (after
    # the wrapper's own base64 framing, never from raw container stdout).
    # No `2>&1` here: stderr warnings in front of the JSON would break
    # parsing.
    run_cli() {
        claude -p "$goal" --output-format json --permission-mode acceptEdits
    }
fi

harness_write_mode_main
