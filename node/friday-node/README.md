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
scrub, and the `voice.state` relay the v2 cases need;
**Phase 10 M2** (`feat/p10-node-executors`, runbook B6) — the real
executors: commands under `srt`, the AppleScript recipes, AX press/type,
open/focus, the owner's command mappings (keychain, edited in "İzinler"
behind Touch ID), and TCC-gated availability;
**Phase 11 M1** (`feat/p11-node-voice-host`, runbook B7) — the voice host:
the `friday-voice run` child over `friday-voice/v1`, AVAudioEngine with
voice processing, the menu-bar mic indicator, the push-to-talk hotkey, the
"Ses" settings, the `node_voice` credential and the `voice.state` relay
(see "The voice host (Phase 11 M1)" below).

The app advertises every action contract with its real availability: see
"The executors (Phase 10 M2)" below. Nothing runs before the owner maps it
(check 22 `not_configured`), grants the TCC permission it needs (check 21
`not_granted`), and clicks the confirmation window.

## Layout

| Path | What |
|---|---|
| `Package.swift` | SwiftPM: `NodeCore`, the `friday-node` CLI, `NodeCoreTests` (`swift build`, `swift test`) |
| `Sources/NodeCore/` | Protocol v1: strict JSON + canonical JSON, the schema subset, Ed25519 verification, the D5 order, `NodeSession` (the state machine), the WSS transport and `NodeRunner`, Keychain credential, TLS pin, pairing, rotation |
| `Sources/NodeCore/Generated/VendoredProtocol.swift` | Byte copies of `backend/nodes/protocol/schemas/v1`, `schemas/v2`, `capabilities/v1`, `capabilities/v2` and `capabilities/v2/recipes` (`scripts/vendor_protocol.py`; CI checks it) |
| `Sources/NodeCore/VerifierV2.swift`, `GrantVerifier.swift` | The v2 order, checks 1-22, same codes at the same checks as `nodes.protocol.verify` / `reference/verifier_v2.py`; the grant's domain-separated signature, field binding and TTL (`grants.json`, `signing_v2.json`) |
| `Sources/NodeCore/Confirmation.swift` | The window's rules (one at a time, 60 s, first click before the deadline), the text rendered from the node's own args and `confirm_template`, and `ConfirmationPanelView` (what the panel draws) |
| `Sources/NodeCore/OutputStreamer.swift` | `StreamScrubber` (D12 carry-over), the incremental UTF-8 decoder, 8 KiB pieces, `seq`, the 1 MiB cap, the 4 KiB tail (`scrub_stream.json`) |
| `Sources/NodeCore/Policy/ActionPolicy.swift` | Check 22: repo realpath equality, mapped kinds, enabled recipes, the bundle-id list (read side) |
| `Sources/NodeCore/Policy/CommandMappings.swift` | The owner's command mappings, recipes, bundle ids and srt location: the keychain item (account `command-mappings`) and `CommandMappingsEditor` (LocalAuthentication first) |
| `Sources/NodeCore/Exec/` | The executor port; `SandboxPolicy` (the `srt` settings document, byte-identical to `sandbox.json`); `SrtExecutor` and `SandboxRun`; `SrtPin` (the pinned npm packages, verified offline); `ProcessTree` and `TreeMonitor` (the `max_processes` / `cpu_seconds` monitor); `ChildProcess` (every spawn: argv, CLOEXEC default, explicit stdio; `BoundedRun`, `ChildRegistry`); `MacActionExecutor` (the app's executor, TCC-gated availability); the throwaway-worktree mode; and (DEBUG only) `ScriptedExecutor` and `CaseSetup` that the offline suite and the Conformance hook drive |
| `Sources/NodeCore/Recipes/` | One file per recipe of `capabilities/v2/recipes/`: the fixed AppleScript, values only as escaped string literals, run by `osascript` |
| `Sources/NodeCore/AX/`, `Sources/NodeCore/Apps/` | `ax.press` / `ax.type_text` (frontmost window only, exact title, never a secure field) and `apps.open` / `apps.focus` (NSWorkspace, the local list) |
| `App/FridayNode/CommandMappingsMenu.swift` | The "İzinler" section that edits the command mappings, recipes, bundle ids and srt location, and asks for the TCC grants |
| `App/FridayNode/ConfirmationWindow.swift` | The non-activating floating `NSPanel` ("Çalıştır" / "Reddet", 60 s countdown, "Durdur") |
| `Sources/NodeCore/ConformanceSupport.swift` | Live conformance mode, compiled only with `CONFORMANCE` |
| `Sources/NodeCore/Voice/` | The voice host (Phase 11 M1): `VoiceMessages` / `VoiceFraming` (`friday-voice/v1`, the Python codes), `VoiceHostCore` (a port of the reference `HostCore`, paced playback), `VoiceChildHost` (the child process: clean env, lock hash, backoff, watchdog, kill ladder), `AudioEngine` (`VoiceAudioIO`, `AVAudioVoiceEngine`), `MicIndicator`, `VoiceSettings` (keychain item `voice-settings`, mute, wake models), `VoiceCredential`, `VoiceService` (the wiring); `VoiceConformanceLive` (`CONFORMANCE` only) |
| `Sources/NodeCore/Generated/VendoredVoiceProtocol.swift` | Byte copies of `node/friday-voice/friday_voice/protocol/schemas/v1` and the sha256 of `node/friday-voice/uv.lock` (`scripts/vendor_protocol.py`) |
| `App/FridayNode/Voice/` | `Hotkey.swift` (Carbon `RegisterEventHotKey`, press/release) and `VoiceMenu.swift` (the "Ses" menu) |
| `fixtures/voice_host_traces.json`, `scripts/voice_host_traces.py` | The reference host's barge-in traces (`--check` in CI) that `VoiceHostTraceTests` replays |
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

### The executors (Phase 10 M2, runbook B6)

| Capability | Runs with | Availability in `hello` |
|---|---|---|
| `repo.command.run` | `srt` 0.0.83 by absolute paths: `<node> <prefix>/node_modules/@anthropic-ai/sandbox-runtime/dist/cli.js --settings <file> -- <argv>`; the settings document of `SandboxPolicy` (byte-identical to `backend/nodes/conformance/vectors/sandbox.json`), the stripped environment plus `CLAUDE_CODE_TMPDIR` (so `TMPDIR` is the run's private dir), its own process group | `granted` when the configured srt location is usable (pinned version, Node ≥ 22.12), else `unsupported` |
| `applescript.recipe.run` | `osascript -e <fixed source>`, values only as escaped string literals | `granted` when every enabled recipe's target app has the Automation grant (`AEDeterminePermissionToAutomateTarget`, never prompting), else `not_granted` |
| `ax.press`, `ax.type_text` | Accessibility API on the frontmost window of the bundle id | `granted` with the Accessibility grant (`AXIsProcessTrusted`), else `not_granted` |
| `apps.open`, `apps.focus` | `NSWorkspace` | `granted` (no TCC grant involved) |

The runner re-reads the availability every 15 s and whenever the menu
opens, so a TCC grant given in System Settings reaches the server as
`grant.availability_changed`.

- **srt location:** "İzinler" → "srt konumu… (Touch ID ile onaylanır)"
  (default `/usr/local/bin/node`, `~/Library/Application Support/Friday/srt`;
  see `docs/mcp/dependencies/node-gateway.md`). Setting it, or "srt
  dosyalarını yeniden onayla (Touch ID)", stores the sha256 of the Node
  binary, the whole `node_modules` tree and `package-lock.json` in the
  keychain item; any later change of those files makes `repo.command.run`
  `unsupported` until the owner re-confirms (an upgrade of srt or Node needs
  that step; the prompt and the menu name the digests that changed).
  Confirming is refused unless the prefix is exactly the pinned npm packages
  (`Exec/SrtPin.swift`: the lockfile's five packages at their versions and
  integrities, sandbox-runtime 0.0.83 with the A6 sha512; each cached
  tarball in `~/.npm/_cacache` hashes to its integrity; the installed files
  equal the tarball's, byte for byte). A missing cache entry refuses: run
  `npm cache add <name>@<version>` first. srt's Node runs with
  `--no-global-search-paths`; the tree hash refuses a symlink out of the
  prefix; a mapping may not list a `NODE_*` variable. **Check-to-exec
  window:** the digests are verified right before `node` is spawned, so a
  process of the owner's user that rewrites the prefix in that gap
  (milliseconds) is not caught; only a later run notices. The prefix is
  writable by any process of the user, as the whole home is.
- **Mappings:** "İzinler" → "Komut deposu ekle…", then per repo "Komut ekle /
  değiştir…": kind, argv (one argument per line, no shell), PATH (must
  contain `/bin`: srt runs the argv through `bash` found on it), environment
  names, network domains (empty: network off), runtime, mode. Recipes and
  the bundle-id list in the same menu. Every change asks for Touch ID or the
  password and is refused before saving when it does not validate.
- **Limits on macOS:** `max_processes` and `cpu_seconds` are enforced by a
  process-tree monitor (never `RLIMIT_NPROC`, which macOS counts per user).
  The root is spawned suspended; its identity and kqueue fork/exit watch
  exist before it runs (if they cannot be set up, the suspended root is
  killed and the run is an `error`; a failed unique-id self-test makes
  `repo.command.run` `unsupported`). Members are followed by **unique-id
  lineage** (`p_puniqueid`, the forking parent's unique id; exited members'
  ids are kept). The kernel keeps `p_puniqueid` across re-parenting to
  launchd only sometimes: measured, it was kept for a `setsid` child and for
  a child whose parent lived about 0.5 s after forking, and reset to 1 for a
  non-setsid child whose parent exits at once and for `( cmd & )` (and
  occasionally even for a setsid child). A child whose id was reset is found
  only if it was learnt while its parent lived; the orphan sweep (which
  requires a recorded parent newer than the root) skips it. In the review's
  probe such non-setsid daemons were still contained 0 of 60 times through
  fork events, the process group and scans. Members are learnt from fork and exit events, which
  trigger an immediate full sweep, and from scans every 5 ms for the first
  2 s, then 50 ms, 200 ms once forks stop. A last-resort **orphan sweep**
  attributes to the run (so it dies with the run) a launchd-parented process
  of this user created after the root whose recorded parent is gone and
  whose responsible process (macOS responsibility) is this app; it is on
  only when the app is its own responsible process (the installed app),
  never under a shell or test runner. **The rule against other runs:** a
  process-wide registry holds every live monitor's member and lineage unique
  ids (exited ones included) and the node's helpers (git, osascript); the
  sweep skips a candidate that another monitor knows, or whose recorded
  parent or any live ancestor it knows, or that descends from a helper; and
  it does not run at all while another run has a live root or members, or
  while a helper started after the run's root runs or ran in the last 10 s
  (helpers before the root, such as the worktree's git calls, do not
  count: the sweep only considers processes newer than the root), since an orphan nobody knows
  the parent of cannot be attributed to one run. So with two concurrent
  runs, only lineage protects each run's tree. After the root exits the
  monitor keeps killing leftovers for at most 10 s; members that survive
  that are logged (os_log, `tree-monitor`) and the monitor stops, freeing
  its kqueue and threads. Every kill goes to one pid after re-checking its (unique id,
  start time); the monitor never uses `killpg`. Output past
  1 MiB is counted, not kept; "Durdur", the kill switch and socket loss
  send SIGTERM to the (unreaped) group and SIGKILL to the verified tree
  after 5 s, without blocking the session; the worktree and temp dirs are
  removed once the tree is gone.
- **Residual limits (carried into M3):**
  - **A daemonising command can still evade the monitor by retrying when
    the orphan sweep is off.** An intermediate process that forks a
    `setsid` child and exits before its fork event is handled leaves a
    child whose recorded parent is unknown. Measured with the review's
    probe (15 such children per run, 40 runs, 600 children; 2026-11-27):
    lineage and events only, 2-4 of 600 survived (before hardening: 3 of
    150); with the orphan sweep on (forced in the test, restricted to the
    probe's perl and sleep binaries), 0 of 600, 0 of 593 and 1 of 596 in
    three measurements, and about 4 in 1080 when the whole test suite ran
    (a launchd-parented `sleep` answering to the same responsible process
    that the sweep missed; the cause is not found). **So even with the sweep
    on, a daemonising command that retries hundreds of times can expect an
    occasional survivor.** The installed app has the sweep on only if
    macOS names it its own responsible process, which is expected for an
    app launched by launchd but **not yet verified on the installed app**
    (M3). A descendant that gets itself started under another responsible
    process (for example through launchd or LaunchServices) is out of
    reach; whether a sandboxed command can do that is not tested yet (M3).
  - A fork burst can overshoot the count for one sample (5-200 ms;
    `kern.maxprocperuid` is the backstop); CPU time of a process that lived
    less than a sample is not counted; a few microseconds remain between the
    identity check and `kill(2)` (macOS has no pidfd).
  - (A per-run environment marker was tried and dropped: macOS does not show
    other processes' environments.)
- **apps.open / apps.focus** launch or activate only a bundle whose realpath
  is under `/Applications`, `/System/Applications` or
  `/System/Library/CoreServices`, and `apps.open` only with a valid code
  signature; `ax.type_text`'s keystroke fallback re-checks frontmost, the
  focused element and the secure-field rule right before posting.
- **The window** shows the mapping's sandbox mode (`in_place` with a
  warning: the command writes into the repo itself).
- **Git for the worktree mode:** 60 s timeout, `HOME=/var/empty`,
  `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`, hooks and
  fsmonitor off, every filter driver of the repo's config blanked; the kill
  switch kills a running git from the menu thread.
- **Tests never drive the owner's apps:** AX, workspace, AppleScript and TCC
  are spies or fakes; the real-`srt` test writes only into a temp dir and
  skips when srt is not at the configured location.

Escape-policy export (contract test 3):

```bash
# backend/
uv run python manage.py node_escape_suite --export-policy /private/tmp/<x>/reference-policy.json
# node/friday-node/ (the Swift documents over the reference's context must equal its documents)
FRIDAY_ESCAPE_REFERENCE=/private/tmp/<x>/reference-policy.json FRIDAY_ESCAPE_EXPORT=/private/tmp/<x>/swift-policy.json \
  swift test --filter SandboxPolicyTests
# backend/
uv run python manage.py node_escape_suite --check-policy /private/tmp/<x>/swift-policy.json   # 32/32 covered
```

## The voice host (Phase 11 M1, runbook B7)

The app runs `~/Library/Application Support/Friday/voice/.venv/bin/friday-voice
run` (`make friday-voice-install`) while voice is wanted: the "Ses" mode is
not "Kapalı", the node WebSocket is up, the kill switch is released and the
node is paired. Before each spawn the installed `uv.lock` must hash to the
value compiled in from this checkout's `node/friday-voice/uv.lock` (rerun
`python3 scripts/vendor_protocol.py` after a lock change, then reinstall).
The child gets `PATH`, `HOME` and `LANG` only; a failed child is released,
shown `off` and restarted 1 s → 60 s; quitting, WS loss and mode off send
`shutdown` and kill what is left; the kill switch kills the tree at once.
The mic (AVAudioEngine, voice processing on) runs only while the host core
holds it. Always-on asks for Touch ID; it arms only with every wake model
pinned (`hey_friday.onnx` is not yet: push-to-talk only). The voice token
is minted with the node secret over the pairing pin, kept in memory, and
re-minted 75 s before expiry; a 401 releases the mic (icon `error`).

```bash
cd node/friday-node
swift test --filter Voice                    # vectors, the 28 host cases offline, barge-in traces, credential, service, settings
FRIDAY_VOICE_INSTALLED_CHILD=1 swift test --filter VoiceInstalledChildTests   # the installed child: lock hash + handshake
# contract test 2: the Conformance build against the hostile child, one process per (re)started child
node/friday-node/build/Build/Products/Conformance/FridayNode.app/Contents/MacOS/FridayNode \
  --conformance-voice-cases "$PWD/node/friday-voice/friday_voice/conformance/cases" \
  --conformance-voice-project "$PWD/node/friday-voice" \
  --conformance-voice-exec "$PWD/node/friday-voice/.venv/bin/friday-voice"     # from the repo root
cd node/friday-voice && uv run python ../friday-node/scripts/voice_host_traces.py [--check]
```

`--conformance-voice-uv "$(which uv)"` runs the hostile child as `uv run
--frozen friday-voice …` instead; under uv `host.child_stdout_short_read`
cannot pass (uv keeps the child's stdout open while it waits). The three
manual-clock cases run with real processes on a stepped host clock.

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
