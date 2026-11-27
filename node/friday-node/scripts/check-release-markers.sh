#!/usr/bin/env bash
# Refuse a release binary that carries any conformance marker (Phase 9 brief
# M1 contract test 2; runbook Part B "The conformance build rule"): the
# strings FRIDAY_NODE_CONFORMANCE or "conformance" (any case), or the
# conformance public key's base64 from backend/nodes/conformance/keys/.
# It also refuses the debug-only owner-turn preview (Phase 8B brief M1,
# runbook B4: `scrub-preview` and its `OwnerTurnPreview` type, `#if DEBUG`),
# which reads real transcripts and must never ship.
# Run by the Release-only build phase (before CodeSign) and again by
# build-sign-local.sh on the final binary.
set -euo pipefail
BIN=${1:?usage: check-release-markers.sh <Mach-O>}
HERE=$(cd "$(dirname "$0")" && pwd)
KEYS="$HERE/../../../backend/nodes/conformance/keys/conformance_keys.json"
CONF_KEY_B64=$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["signing"]["public_key_b64"])' "$KEYS")
[[ -n $CONF_KEY_B64 ]] || { echo "cannot read the conformance public key" >&2; exit 1; }
# No `strings | grep -q` pipeline: under pipefail, grep -q exits at the first
# match, strings dies of SIGPIPE and the pipeline reads as "no match" -- the
# check would always pass. Collect first, then search.
#
# `strings -a` scans every section of the Mach-O (code, data, C strings,
# Objective-C and Swift metadata), which is where a compiled-in conformance
# type or literal lands; it is what tells the Conformance build (21 hits)
# from Release (0). It does not scan the symbol table: every Swift binary's
# symbol table names compiler-generated "associated conformance" symbols,
# which are not a marker of conformance mode.
TEXT=$(mktemp)
trap 'rm -f "$TEXT"' EXIT
strings -a "$BIN" > "$TEXT"
if grep -qiE "FRIDAY_NODE_CONFORMANCE|conformance" "$TEXT" || grep -qF -- "$CONF_KEY_B64" "$TEXT"; then
  echo "conformance marker in $(basename "$BIN"): refusing" >&2
  exit 1
fi
# Case-sensitive: the CLI word and the Swift type name as compiled in.
if grep -qE "scrub-preview|OwnerTurnPreview" "$TEXT"; then
  echo "debug-only scrub-preview marker in $(basename "$BIN"): refusing" >&2
  exit 1
fi
# Runbook B5 (Phase 10 M1): the conformance hook's own words (the
# `wait_disconnected` op and the report keys `executor_terminated` /
# `windows_shown`, which nothing outside `#if CONFORMANCE` spells), and the
# DEBUG-only scripted doubles the hook and the offline suite drive (the
# window owner's `script`, `ScriptedExecutor`, `CaseSetup`).
if grep -qE "wait_disconnected|executor_terminated|windows_shown|ScriptedExecutor|ScriptedRun|CaseSetup|CommandScript" "$TEXT"; then
  echo "conformance hook or scripted-double marker in $(basename "$BIN"): refusing" >&2
  exit 1
fi
echo "no conformance or debug-preview marker in $(basename "$BIN")"
