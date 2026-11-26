# friday-node — the Friday Node macOS app

The Swift node of ADR-013 (Phase 9 brief "Mac session"; runbook
`docs/runbooks/mac-session-handoff.md` Part B). It runs only on the owner's
Mac, is built and installed locally, and is never distributed.

Status: **M1** (`feat/p9-node-app`) — shell, identity, session, verifier.
Readers, grants and the scrubber are M2; the hook relay is M3.

## Layout

| Path | What |
|---|---|
| `Package.swift` | SwiftPM: `NodeCore`, the `friday-node` CLI, `NodeCoreTests` (`swift build`, `swift test`) |
| `Sources/NodeCore/` | Protocol v1: strict JSON + canonical JSON, the schema subset, Ed25519 verification, the D5 order, `NodeSession` (the state machine), the WSS transport and `NodeRunner`, Keychain credential, TLS pin, pairing, rotation |
| `Sources/NodeCore/Generated/VendoredProtocol.swift` | Byte copies of `backend/nodes/protocol/schemas/v1` and `capabilities/v1` (`scripts/vendor_protocol.py`; CI checks it) |
| `Sources/NodeCore/ConformanceSupport.swift` | Live conformance mode, compiled only with `CONFORMANCE` |
| `App/FridayNode/` | The `LSUIElement` menu-bar app |
| `FridayNode.xcodeproj` | Debug / Release / unsigned **Conformance** configurations |
| `scripts/build-sign-local.sh` | Release build, Personal Team signing, checks, install |
| `scripts/check-release-markers.sh` | Refuses a binary carrying a conformance marker |

## Commands

```bash
cd node/friday-node
swift build
swift test                                   # vectors, vendored files, 39 conformance cases, units
xcodebuild -project FridayNode.xcodeproj -scheme FridayNode -configuration Conformance \
  -derivedDataPath build CODE_SIGNING_ALLOWED=NO build
xcodebuild -project FridayNode.xcodeproj -scheme FridayNode -configuration Release \
  -derivedDataPath build test -only-testing:NodeCoreTests/ReleaseHygieneTests
FRIDAY_NODE_TEAM_ID=<Personal Team ID> scripts/build-sign-local.sh --install
python3 scripts/vendor_protocol.py           # after any schema or contract change in backend/nodes
```

### Live conformance (contract test 2)

```bash
# terminal 1 (backend/): the hostile fake gateway, M1 case set (see below)
uv run python manage.py node_conformance_server --external --port 8765 --tls-self-signed \
  --protocol 1 --hook-socket ~/.friday-conformance/hook.sock --case v1_read_ok ...
# terminal 2 (repo root): the Conformance build, pinning the printed certificate
node/friday-node/build/Build/Products/Conformance/FridayNode.app/Contents/MacOS/FridayNode \
  --conformance-hook ~/.friday-conformance/hook.sock --conformance-gateway 127.0.0.1:8765 \
  --conformance-cert <printed cert.pem> \
  --conformance-keys backend/nodes/conformance/keys/conformance_keys.json \
  --conformance-fixtures backend/nodes/reference/fixtures
```

The hook socket path must be short (macOS limits Unix socket paths to 104
bytes). The M1 case set is every `cases/v1` case except the eleven read
cases in `ConformanceTests.pendingM2`; the readers that make those pass are
M2.

## Rules this package keeps

- Outbound only: no TCP listener of any kind (`lsof -nP -iTCP -sTCP:LISTEN
  -a -p $(pgrep FridayNode)` prints nothing).
- The release build has no environment variable, user default or launch
  argument that changes the TLS pin, the pinned keys or the server URL.
- The Personal Team signing key never leaves the Mac; CI never builds or
  signs the app. No Team ID, identity name or bundle-id suffix is committed
  (`~/.config/friday-node/` holds the local ones).
- No model calls, no provider keys; logs are local (`log stream --predicate
  'subsystem == "dev.friday.friday-node"'`), carry no secret and no frame
  content.
