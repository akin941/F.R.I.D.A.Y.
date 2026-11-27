#!/usr/bin/env bash
# Install the nightly self-trigger job's files (Phase 11 M3; runbook B8):
# - selftest-nightly.sh -> ~/Library/Application Support/Friday/voice/ (0700);
# - the launchd agent, rendered from ai.friday.voice-selftest.plist.template
#   -> ~/Library/LaunchAgents/ai.friday.voice-selftest.plist (0644);
# - ~/Library/Logs/Friday/ (0700) for its log (codes only, no audio).
# It never loads the agent: the job plays audio on the speakers and opens the
# microphone every night, so `launchctl bootstrap` is the owner's step (the
# commands are printed at the end). Needs the CLI from install-hook-cli.sh.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
FRIDAY_HOME="$HOME/Library/Application Support/Friday"
CLI="$FRIDAY_HOME/bin/friday-node"
VOICE_HOME="$FRIDAY_HOME/voice"
AGENTS="$HOME/Library/LaunchAgents"
PLIST="$AGENTS/ai.friday.voice-selftest.plist"
LOGS="$HOME/Library/Logs/Friday"

[[ -x $CLI ]] || { echo "install the CLI first: $HERE/install-hook-cli.sh" >&2; exit 1; }
case $HOME in
  *'&'* | *'<'* | *'>'* | *'|'* | *'\'*) echo "HOME holds a character the plist cannot carry" >&2; exit 1 ;;
esac
mkdir -p "$VOICE_HOME" "$AGENTS" "$LOGS"
chmod 700 "$FRIDAY_HOME" "$VOICE_HOME" "$LOGS"
install -m 0700 "$HERE/selftest-nightly.sh" "$VOICE_HOME/selftest-nightly.sh"
sed "s|@HOME@|$HOME|g" "$HERE/ai.friday.voice-selftest.plist.template" > "$PLIST.tmp"
plutil -lint "$PLIST.tmp" >/dev/null
chmod 644 "$PLIST.tmp"
mv -f "$PLIST.tmp" "$PLIST"
echo "installed: $VOICE_HOME/selftest-nightly.sh"
echo "installed: $PLIST (not loaded)"
cat <<STEPS
Owner steps (the job plays audio and opens the mic every night):
  1. FridayNode.app > Ses > "Gece öz-testi" on (Touch ID).
  2. One run at the Mac:  /bin/bash "$VOICE_HOME/selftest-nightly.sh"; echo "exit \$?"
  3. Load:    launchctl bootstrap gui/\$(id -u) "$PLIST"
     (the older form: launchctl load -w "$PLIST")
  4. Unload:  launchctl bootout gui/\$(id -u)/ai.friday.voice-selftest
STEPS
