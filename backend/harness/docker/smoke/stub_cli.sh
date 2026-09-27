#!/bin/sh
# Stub CLI for the real-container smoke test (Phase 3 branch 8). Installed
# over the real `claude`/`codex` binary in a throwaway image
# (smoke.Dockerfile) so the smoke exercises the real image, mounts, flags,
# wrapper, quiescence and result contract WITHOUT a paid model call. It
# behaves like a well-mannered agent: edits a file in the checkout, spawns
# a stray background process (which quiescence must kill), and prints a
# Claude-Code-shaped JSON result on stdout.
set -eu
printf 'smoke change\n' >> SMOKE.md
( sleep 300 & ) >/dev/null 2>&1
printf '{"type":"result","result":"smoke ok","total_cost_usd":0,"usage":{"input_tokens":1,"output_tokens":1}}\n'
