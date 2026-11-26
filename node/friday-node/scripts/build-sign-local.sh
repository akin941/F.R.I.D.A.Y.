#!/usr/bin/env bash
# Build, sign and (optionally) install FridayNode.app on the owner's Mac
# (Phase 9 brief M1; runbook Part B "scripts/build-sign-local.sh"; d9 revised
# 2026-11-25; the A1 Personal Team spike result in ADR-013).
#
#   FRIDAY_NODE_TEAM_ID=<Personal Team ID> scripts/build-sign-local.sh [--install] [--accept-requirement-change]
#
# - Release configuration only, Xcode automatic signing for the free Apple ID
#   Personal Team ("Apple Development") with hardened runtime: the path A1
#   proved, so the app keeps every entitlement Xcode adds (application
#   identifier, team identifier, keychain access groups). Never re-signed by
#   hand here; never ad-hoc; no notarization, no stapling.
# - Keychain Sharing needs an Xcode-managed provisioning profile, so the
#   build passes -allowProvisioningDeviceRegistration (A1). That profile is
#   valid for 7 days (A1): rebuild at least weekly until the 2026-12-04
#   expiry check in ADR-013 says otherwise.
# - The conformance-marker refusal runs twice: the Release-only build phase
#   (after linking, before CodeSign) and here on every Mach-O in the bundle.
# - The designated requirement is compared with the one recorded in
#   ~/.config/friday-node/designated-requirement.txt: a change means every TCC
#   grant and Keychain item is lost, so the script refuses to install unless
#   --accept-requirement-change is given.
# - The Team ID comes only from the environment, the identity only from the
#   login Keychain, the bundle id from ~/.config/friday-node/bundle-id
#   (generated once, never committed: a Personal Team App ID must be globally
#   unique). The signing key never leaves this Mac; CI never runs this.
set -euo pipefail

INSTALL=0
ACCEPT=0
for arg in "$@"; do
  case $arg in
    --install) INSTALL=1 ;;
    --accept-requirement-change) ACCEPT=1 ;;
    *) echo "usage: FRIDAY_NODE_TEAM_ID=<team> $0 [--install] [--accept-requirement-change]" >&2; exit 2 ;;
  esac
done
TEAM=${FRIDAY_NODE_TEAM_ID:?set FRIDAY_NODE_TEAM_ID (the Personal Team ID, untracked env; runbook A1.0)}

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
CONFIG="$HOME/.config/friday-node"
mkdir -p "$CONFIG" && chmod 700 "$CONFIG"

if [[ -n ${FRIDAY_NODE_BUNDLE_ID:-} ]]; then
  BUNDLE_ID=$FRIDAY_NODE_BUNDLE_ID
elif [[ -s $CONFIG/bundle-id ]]; then
  BUNDLE_ID=$(cat "$CONFIG/bundle-id")
else
  BUNDLE_ID="dev.friday.friday-node.n$(openssl rand -hex 4)"
  echo "$BUNDLE_ID" > "$CONFIG/bundle-id"
fi
[[ $BUNDLE_ID == *friday-node* && $BUNDLE_ID != *spike* ]] || { echo "refusing: unexpected bundle id" >&2; exit 1; }

DERIVED="$ROOT/build"
LOG="$CONFIG/xcodebuild-release.log"
xcodebuild -project "$ROOT/FridayNode.xcodeproj" -scheme FridayNode -configuration Release \
  -derivedDataPath "$DERIVED" -allowProvisioningUpdates -allowProvisioningDeviceRegistration \
  CODE_SIGN_STYLE=Automatic DEVELOPMENT_TEAM="$TEAM" ENABLE_HARDENED_RUNTIME=YES \
  PRODUCT_BUNDLE_IDENTIFIER="$BUNDLE_ID" build > "$LOG" 2>&1 \
  || { tail -n 40 "$LOG" >&2; echo "build failed (the Run Script phase fails it on a conformance marker): $LOG" >&2; exit 1; }

APP="$DERIVED/Build/Products/Release/FridayNode.app"

# 1. No conformance marker in any Mach-O of the bundle.
while IFS= read -r -d '' file; do
  if file -b "$file" | grep -q "Mach-O"; then
    "$HERE/check-release-markers.sh" "$file"
  fi
done < <(find "$APP" -type f -print0)

# 2. Signed by an Apple Development identity of this team, hardened runtime.
codesign --verify --strict --verbose=2 "$APP"
SIGNING=$(codesign -dv --verbose=4 "$APP" 2>&1)
grep -q "^Authority=Apple Development: " <<<"$SIGNING" || { echo "not signed with an Apple Development identity (A1)" >&2; exit 1; }
grep -qx "TeamIdentifier=$TEAM" <<<"$SIGNING" || { echo "signed by another team than FRIDAY_NODE_TEAM_ID" >&2; exit 1; }
grep -q "flags=0x10000(runtime)" <<<"$SIGNING" || { echo "hardened runtime flag missing" >&2; exit 1; }

# 3. The entitlement set M1 ships: keychain access groups yes; no sandbox,
#    no microphone (Phase 11), no Apple Events (Phase 10), no server.
ENTITLEMENTS=$(codesign -d --entitlements - --xml "$APP" 2>/dev/null | plutil -p - 2>/dev/null)
grep -q '"keychain-access-groups"' <<<"$ENTITLEMENTS" || { echo "keychain-access-groups missing: the data protection keychain needs it (A1)" >&2; exit 1; }
for forbidden in com.apple.security.app-sandbox com.apple.security.device.audio-input \
                 com.apple.security.automation.apple-events com.apple.security.network.server; do
  if grep -q "\"$forbidden\"" <<<"$ENTITLEMENTS"; then echo "unexpected entitlement $forbidden in M1" >&2; exit 1; fi
done

# 4. A stable, non-ad-hoc designated requirement.
DR=$(codesign -d -r- "$APP" 2>&1 | sed -n 's/^designated => //p')
if [[ -z $DR || $DR == *cdhash* ]]; then echo "ad-hoc or missing designated requirement: refusing" >&2; exit 1; fi
SAVED="$CONFIG/designated-requirement.txt"
if [[ -s $SAVED ]]; then
  if [[ $DR != "$(cat "$SAVED")" ]]; then
    echo "WARNING: the designated requirement changed; every TCC grant and Keychain item will be lost." >&2
    if [[ $ACCEPT -ne 1 ]]; then echo "rerun with --accept-requirement-change to install anyway" >&2; exit 1; fi
    echo "$DR" > "$SAVED"
  fi
else
  echo "$DR" > "$SAVED"
fi

if [[ -f $APP/Contents/embedded.provisionprofile ]]; then
  EXPIRY=$(security cms -D -i "$APP/Contents/embedded.provisionprofile" 2>/dev/null | plutil -extract ExpirationDate raw - 2>/dev/null || true)
  echo "embedded provisioning profile expires: ${EXPIRY:-unknown}"
fi
echo "signed: $APP"

if [[ $INSTALL -eq 1 ]]; then
  pkill -x FridayNode 2>/dev/null && sleep 1 || true
  rm -rf /Applications/FridayNode.app
  ditto "$APP" /Applications/FridayNode.app
  open /Applications/FridayNode.app
  echo "installed /Applications/FridayNode.app"
fi
