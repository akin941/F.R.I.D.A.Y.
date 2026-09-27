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

# --- Write mode (Phase 3; shared logic since branch 5) ---
#
# Everything after the CLI invocation -- commit ownership, pristine test
# checkout, quiescence, dirty-tree check, result file, cost framing -- is
# shared with the other wrapper image: see harness_write_mode.sh for the
# full sequence and its trust rationale.
# shellcheck source=harness_write_mode.sh
. "$(dirname "$0")/harness_write_mode.sh"

# `2>&1`: `codex exec` writes its real transcript to stderr (branch 3
# round 11) -- the captured value is the implementation transcript.
run_cli() {
    codex exec --sandbox workspace-write "$goal" 2>&1
}

harness_write_mode_main
