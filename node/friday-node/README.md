# friday-node — the Friday Node macOS app

The Swift node of ADR-013 (Phase 9 brief "Mac session"; runbook
`docs/runbooks/mac-session-handoff.md` Part B). It runs only on the owner's
Mac, is built and installed locally, and is never distributed.

Status: **M1** (`feat/p9-node-app`) — shell, identity, session, verifier;
**M2** (`feat/p9-node-readers`) — readers, local policy and grants, the
scrubber; **M3** (`feat/p9-node-hook-relay`) — the hook relay
(`friday-node hook`, the app's Unix socket) and the bounded spool;
**Phase 8B M1** (`feat/p8-node-transcript-readers`, runbook B4) — the two
owner-turn readers and the debug-only `friday-node scrub-preview`;
**Phase 10 M1** (`feat/p10-node-confirm`, runbook B5) — protocol v2 (the
`friday-node.v2, friday-node.v1` offer), the v2 verifier (D5 checks 13-24),
grants, the confirmation window, output streaming with the D12 carry-over
scrub, and the `voice.state` relay the v2 cases need.

Until runbook B6 the app has **no executor and no command mappings**: every
confirm- and gate-class contract is advertised `unsupported` (check 21
refuses before any window opens) and the action policy is empty
(`not_configured`). B6 adds `Exec/SrtExecutor.swift`, `SandboxPolicy.swift`,
`Policy/CommandMappings.swift`, the recipes, AX and open/focus.

## Layout

| Path | What |
|---|---|
| `Package.swift` | SwiftPM: `NodeCore`, the `friday-node` CLI, `NodeCoreTests` (`swift build`, `swift test`) |
| `Sources/NodeCore/` | Protocol v1: strict JSON + canonical JSON, the schema subset, Ed25519 verification, the D5 order, `NodeSession` (the state machine), the WSS transport and `NodeRunner`, Keychain credential, TLS pin, pairing, rotation |
| `Sources/NodeCore/Generated/VendoredProtocol.swift` | Byte copies of `backend/nodes/protocol/schemas/v1`, `schemas/v2`, `capabilities/v1`, `capabilities/v2` and `capabilities/v2/recipes` (`scripts/vendor_protocol.py`; CI checks it) |
| `Sources/NodeCore/VerifierV2.swift`, `GrantVerifier.swift` | The v2 order, checks 1-22, same codes at the same checks as `nodes.protocol.verify` / `reference/verifier_v2.py`; the grant's domain-separated signature, field binding and TTL (`grants.json`, `signing_v2.json`) |
| `Sources/NodeCore/Confirmation.swift` | The window's rules (one at a time, 60 s, first click before the deadline), the text rendered from the node's own args and `confirm_template`, and `ConfirmationPanelView` (what the panel draws) |
| `Sources/NodeCore/OutputStreamer.swift` | `StreamScrubber` (D12 carry-over), the incremental UTF-8 decoder, 8 KiB pieces, `seq`, the 1 MiB cap, the 4 KiB tail (`scrub_stream.json`) |
| `Sources/NodeCore/Policy/ActionPolicy.swift` | Check 22: repo realpath equality, mapped kinds, enabled recipes, the bundle-id list (read side; editing is B6) |
| `Sources/NodeCore/Exec/` | The executor port, the throwaway-worktree mode, and (DEBUG only) `ScriptedExecutor` and `CaseSetup` that the offline suite and the Conformance hook drive |
| `App/FridayNode/ConfirmationWindow.swift` | The non-activating floating `NSPanel` ("Çalıştır" / "Reddet", 60 s countdown, "Durdur") |
| `Sources/NodeCore/ConformanceSupport.swift` | Live conformance mode, compiled only with `CONFORMANCE` |
| `App/FridayNode/` | The `LSUIElement` menu-bar app |
| `FridayNode.xcodeproj` | Debug / Release / unsigned **Conformance** configurations |
| `scripts/build-sign-local.sh` | Release build, Personal Team signing, checks, install |
| `scripts/check-release-markers.sh` | Refuses a binary carrying a conformance marker |
| `Sources/NodeCore/Scrubber.swift` | The 8A scrubber, byte-identical (`ScrubParityTests`, `scripts/scrub_differential.py`) |
| `Sources/NodeCore/Readers/`, `Policy/` | The ten readers (`ClaudeOwnerTurns.swift`, `CodexOwnerTurns.swift` and `OwnerTurns.swift` port `backend/nodes/reference/owner_turns.py`; `OwnerTurnPreview.swift` is debug-only); the local policy (keychain) and grants (Touch ID) |
| `../scripts/scrubber-corpus.sh` | Corpus recall gate over `friday-node scrub` |
| `Sources/NodeCore/HookSocket.swift`, `Spool.swift` | The hook relay socket and the event spool (M3) |
| `scripts/install-hook-cli.sh` | Installs `friday-node` for Claude Code's hooks |

## Commands

```bash
cd node/friday-node
swift build
swift test                                   # vectors (v1, signing_v2, grants, scrub_stream), vendored files, scrub parity, all 86 v1 and 99 v2 conformance cases, owner-turn vector, the args fuzz families and a CPython differential, units
../scripts/scrubber-corpus.sh                # corpus recall via friday-node scrub
xcodebuild -project FridayNode.xcodeproj -scheme FridayNode -configuration Conformance \
  -derivedDataPath build CODE_SIGNING_ALLOWED=NO build
xcodebuild -project FridayNode.xcodeproj -scheme FridayNode -configuration Release \
  -derivedDataPath build test -only-testing:NodeCoreTests/ReleaseHygieneTests
FRIDAY_NODE_TEAM_ID=<Personal Team ID> scripts/build-sign-local.sh --install
python3 scripts/vendor_protocol.py           # after any schema or contract change in backend/nodes
.build/debug/friday-node scrub-preview --owner-turns ~/.claude/projects/<one project>   # debug build only
.build/debug/friday-node scrub-preview --codex ~/.codex/sessions                        # debug build only
```

The owner-turn reads (`claude.transcripts.owner_turns`,
`codex.transcripts.owner_turns`) follow the `claude` / `codex` directory
grants: `not_granted` until the owner's Touch ID grant, and a revoke is
`not_granted` at once plus `grant.availability_changed`. They keep the
reference's own formats: RFC 3339 times with milliseconds, `cwd` in the form
of the prefix that matched, Codex turn ids hashed over byte offsets.

`scrub-preview` (compiled only with `DEBUG`: SwiftPM debug builds and the
Xcode Debug configuration; the release CLI answers with the usage line)
runs the readers' own line rules over real files and prints counts only:
per line kind (a JSON `type` word), per rule verdict, per excluded marker,
and the leading tag name of accepted turns that start with one (a candidate
harness format no marker covers). It never prints text, identifiers or file
names. Real-file checks are never committed.

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
bytes). Since M2 the Conformance build also needs `--conformance-home`, a
fixture home built by `nodes.reference.fixtures.factory.build_fixture_home`
(for example `uv run python -c "from nodes.reference.fixtures.factory import
build_fixture_home; print(build_fixture_home('/tmp/fx'))"` from `backend/`),
and every `cases/v1` case runs.

Since Phase 10 M1 run it for both protocols (the node offers v2 and v1;
the gateway picks the case's version), every case, no `--case` filter:

```bash
# backend/: unbuffered, logged; prints the certificate path
PYTHONUNBUFFERED=1 DJANGO_SETTINGS_MODULE=config.settings.test DATABASE_URL=postgres://x:x@localhost:1/x \
  uv run python -u manage.py node_conformance_server --external --port 8766 --tls-self-signed \
  --protocol 2 --hook-socket ~/.friday-conformance/hookb5.sock    # then again with --protocol 1
# repo root: the same Conformance binary and flags as above, plus
#   --conformance-home <fixture home>
```

`prepare` builds the case's scripted owner (`setup.window`, the seam through
which the server's scripted owner answers the confirmation window), the
scripted executor (`setup.executor`) and the action policy of
`fixtures/local_policy.json`; `kill_switch` engages the session's kill
switch. All of it is `#if CONFORMANCE` (the hook) or `#if DEBUG` (the
doubles), and `scripts/check-release-markers.sh` refuses a binary carrying
the hook's op and report words or the doubles' type names.

### The confirmation window (Phase 10 M1)

A non-activating floating panel (`App/FridayNode/ConfirmationWindow.swift`)
draws `ConfirmationPanelView` only: the sentence filled from the
capability's (or recipe's) `confirm_template` with the node's own validated
args, the recipe label, the repo's local name, the exact argv and network
setting, and the approval id's first 8 characters for gate class. Buttons
"Çalıştır" / "Reddet" (no default button), a 60 s countdown, "Durdur" while
the approved command runs. It closes on socket loss, the kill switch or the
timeout; one window at a time (a second invoke is `busy_confirming`). The
menu's "Acil durdur" is the node's local kill switch (later clicks are
refused `expired_after_confirm`); releasing it asks for Touch ID or the
password.

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
