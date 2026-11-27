#!/bin/bash
# The nightly self-trigger run (Phase 11 brief M3; runbook B8;
# docs/voice/live-eval.md §4). launchd runs it at 03:17
# (ai.friday.voice-selftest, installed by install-voice-selftest.sh; loading
# it is an owner step).
#
# 1. On battery: skip (exit 0, the last result line stays as it is).
# 2. The running FridayNode.app mints a fresh `node_voice` token with the
#    Keychain node secret (`friday-node voice-token --out`): a new 0600 file,
#    deleted on every exit path by the trap (`voice-token --end`, which also
#    gives the live voice its mic and token back).
# 3. `friday-voice selftest --nightly` plays the real TTS clip through the
#    app's voice-processing AEC (`friday-node voice-loopback`), scores the
#    capture with the pinned `hey_friday` model and writes one JSON line to
#    the result file (the server's VOICE_LIVE_SELFTEST_RESULT_PATH).
#
# Why the app does the audio and the mint: README "Nightly self-trigger"
# (the Microphone grant and the node secret are the app's; this job's own
# processes have neither).
#
# Nothing here writes audio or the token anywhere else; the log gets codes
# only. Exit: 0 pass or skipped, 1 the gate bit, 2 could not run.
set -euo pipefail
umask 077

FRIDAY_HOME="$HOME/Library/Application Support/Friday"
VOICE_HOME="$FRIDAY_HOME/voice"
NODE_CLI="${FRIDAY_NODE_CLI:-$FRIDAY_HOME/bin/friday-node}"
FRIDAY_VOICE="${FRIDAY_VOICE_BIN:-$VOICE_HOME/.venv/bin/friday-voice}"
VOICE_PYTHON="${FRIDAY_VOICE_PYTHON:-$VOICE_HOME/.venv/bin/python}"
MODEL_DIR="${FRIDAY_VOICE_MODEL_DIR:-$VOICE_HOME/models}"
RESULT_FILE="${FRIDAY_VOICE_SELFTEST_RESULT:-$VOICE_HOME/selftest-result.json}"
PMSET="${FRIDAY_SELFTEST_PMSET:-/usr/bin/pmset}"
TOKEN_FILE="$VOICE_HOME/selftest.token"

log() { printf '%s friday-voice-selftest: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }

# The token file goes on every exit path. bash runs a signal trap only after
# the foreground command returns, so `friday-voice` runs in the background
# and the script waits on it: a launchd SIGTERM reaches the trap at once,
# which stops the child and exits (the EXIT trap then deletes the token), so
# a SIGKILL after launchd's grace finds nothing left behind.
child=""
cleanup() {
  "$NODE_CLI" voice-token --end "$TOKEN_FILE" >/dev/null 2>&1 || true
  rm -f -- "$TOKEN_FILE"
}
on_signal() {
  if [[ -n $child ]]; then kill -TERM "$child" 2>/dev/null || true; fi
  exit 143
}
trap cleanup EXIT
trap on_signal INT TERM HUP
# `friday-voice` in the background, waited on (see above); its exit status.
run_voice() {
  "$FRIDAY_VOICE" "$@" &
  child=$!
  local code=0
  wait "$child" || code=$?
  child=""
  return "$code"
}
cleanup  # a file or a hold left by an earlier run that died, even on battery

power=$("$PMSET" -g batt 2>/dev/null || true)
case $power in
  *"'AC Power'"*) ;;
  *) log "skip: not on AC power"; exit 0 ;;
esac

for tool in "$NODE_CLI" "$FRIDAY_VOICE"; do
  [[ -x $tool ]] || { log "error: $(basename "$tool") is not installed"; exit 2; }
done
mkdir -p "$VOICE_HOME"
chmod 700 "$VOICE_HOME"

loopback="$(printf '%q' "$NODE_CLI") voice-loopback"
args=(selftest --nightly --model-dir "$MODEL_DIR" --loopback-cmd "$loopback" --result-file "$RESULT_FILE")
if [[ -f "$VOICE_HOME/tls.json" && -x $VOICE_PYTHON ]]; then
  # The private CA `friday-voice ca set` installed (the live child's too).
  ca_file=$("$VOICE_PYTHON" -I -c 'from friday_voice import tls; print(tls.installed_ca_file() or "")') \
    || { log "error: tls.json unusable"; ca_file=""; }
  [[ -n $ca_file ]] && args+=(--ca-file "$ca_file")
fi

if ! info=$("$NODE_CLI" voice-token --out "$TOKEN_FILE"); then
  log "error: the app minted no voice token (see the code above)"
  # No token: the selftest refuses (exit 2) and records an error line, so
  # a night that could not run never leaves an earlier green in place.
  status=0
  run_voice "${args[@]}" >/dev/null || status=$?
  exit "$status"
fi
server="" ca=""
while IFS='=' read -r key value; do
  case $key in
    server_url) server=$value ;;
    ca_sha256) ca=$value ;;
  esac
done <<<"$info"
args+=(--server "$server" --token-file "$TOKEN_FILE")
[[ -n $ca ]] && args+=(--ca-sha256 "$ca")

status=0
run_voice "${args[@]}" || status=$?
log "done: exit $status"
exit "$status"
