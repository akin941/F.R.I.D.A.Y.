#!/usr/bin/env bash
# Install the `friday-node` CLI that Claude Code's hooks run (Phase 9 brief
# M3): a release build copied to ~/Library/Application Support/Friday/bin/
# (0755 in a 0700 directory). Prints the hook command to put into
# ~/.claude/settings.json (docs/runbooks/friday-node.md "Switching Claude
# Code hooks to the node").
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
DEST="$HOME/Library/Application Support/Friday/bin"
(cd "$HERE" && swift build -c release --product friday-node >/dev/null)
mkdir -p "$DEST"
chmod 700 "$HOME/Library/Application Support/Friday" "$DEST"
install -m 0755 "$HERE/.build/release/friday-node" "$DEST/friday-node"
echo "installed: $DEST/friday-node"
echo "hook command: \"$DEST/friday-node\" hook"
