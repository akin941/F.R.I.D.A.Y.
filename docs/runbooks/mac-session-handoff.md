# Mac session handoff — everything that needs the owner's MacBook

> Branch `docs/mac-session-handoff` — **implemented 2026-11-20** (docs
> only). This file collects every "Mac session (deferred)" section and
> every "Owner steps (pending)" list of the phase briefs 5, 6, 8A, 9, 8B,
> 10, 11 and 12 into **one ordered checklist**. A future Claude Code
> session on the owner's MacBook starts here.
>
> **Revised 2026-11-25 (d9):** A1 is now the Personal Team signing spike;
> the paid Apple Developer Program, Developer ID and notarization are not
> required (fallback / optional later: A1-F). `scripts/build-sign-notarize.sh`
> is replaced by `scripts/build-sign-local.sh`.

## How to use this file

You are a Claude Code session on the owner's MacBook. The server half of
Friday 3.0 (phases 5 → 6 → 8A → 9 → 8B → 10 → 11 → 12) is implemented
and tested against Python reference doubles. What is left needs the Mac:
a signed Swift app, the Keychain, Touch ID, TCC grants, AppleScript and
Accessibility, the real microphone and speakers, and the owner's own data
and accounts.

Work top to bottom:

1. **Part A — prerequisites.** Accounts, tools, the running stack. Do
   these first. Some of them (24 h of audio, `hey_friday`
   training) take days, so start them early. A1, the Personal Team
   signing spike (d9 revised 2026-11-25), comes first and takes hours.
2. **Part B — Swift `friday-node` tasks.** One branch per task, in the
   order given. Each one names the server-side conformance and contract
   tests it must pass, with the exact commands.
3. **Part C — measurements and go/no-go evaluations** on real hardware
   and real data. Each one says where its result is recorded.
4. **Part D — owner-only steps that are not Swift work** (credentials,
   reviews, merges, decisions). Most of them do not need the Mac. They are
   listed so nothing is lost.

Each item has a checkbox. The **brief** named in each item is the full
specification; this file is the order, the commands and the acceptance
tests. When the two disagree, the brief wins: fix this file in the same
branch.

### Rules that bind the Mac session (CLAUDE.md, unchanged)

- Branches: `feat/pN-<topic>` (the names below). Never commit to `main` or
  to the integration branch directly. Conventional Commits.
- Every branch is reviewed by an independent Sonnet 5 subagent until
  APPROVE (the implementing model never reviews its own work). Codex CLI is
  not used in this repo's development workflow.
- `ruff`, `pytest` and CI must be green before work is declared done. For
  Swift branches, `swift test` must also be green on the Mac (CI has no
  macOS runner unless the owner decides otherwise, step D9).
- **The Personal Team ("Apple Development") signing key never leaves the
  Mac. CI never holds it.** (d9 revised 2026-11-25: no paid Developer
  Program, no Developer ID, no notarization unless A1 falls back.)
  No `NODE_SIGNING_KEY`, node secret (`jnd_…`), PAT or provider key in any
  file in the repo.
- **Recordings never enter the repo or the database.** Household audio,
  "Hey Friday" positives, real transcripts and private eval cases live
  under `~/FridayEval/` and `~/friday-evals/` (mode 0700). Only numbers and
  hashes are recorded.
- Third-party code goes through the governance manifest
  (`docs/security/mcp-governance.md`, `docs/mcp/dependencies/`), with exact
  pins and `Package.resolved` committed.
- Results go into the brief's "Go/no-go record" on a `docs/…` branch,
  reviewed like any brief change.

### The source briefs

| Phase | Brief | Mac sections | Owner-step lists |
|---|---|---|---|
| 5 | `docs/prompts/phase-5-deploy-mcp/brief.md` | — (no Swift) | §7, §9, §10 "Owner steps (pending, recorded 2026-11-20)" |
| 6 | `docs/prompts/phase-6-voice-identity/brief.md` | manual Mac passes in the branch lists | "Owner steps (pending) — recorded 2026-11-20", the five branch lists, "closeout"; "A9 — exit criteria record" |
| 8A | `docs/prompts/phase-8a-learning-memory/brief.md` | — (no Swift) | phase list, branch 2, the five branch lists, `closeout`; "Go/no-go record" |
| 9 | `docs/prompts/phase-9-friday-node/brief.md` | "Mac session (deferred)" M1–M4 | phase list + nine branch lists; "Go/no-go record" |
| 8B | `docs/prompts/phase-8b-transcripts-graph/brief.md` | "Mac session (deferred)" M1–M2 | phase list + eleven branch lists; "Go/no-go record" |
| 10 | `docs/prompts/phase-10-control/brief.md` | "Mac session (deferred)" M1–M4 | phase list + nine branch lists; "Go/no-go record" |
| 11 | `docs/prompts/phase-11-live-voice/brief.md` | "Mac session (deferred)" M1–M4 | phase list + nine branch lists; "Go/no-go record" |
| 12 | `docs/prompts/phase-12-reach/brief.md` | "Mac session (deferred)" M1 (verification only) | phase list + nine branch lists; "Go/no-go record" |

ADRs these tasks implement: **ADR-013** (Friday Node protocol: identity,
pinning, no listener, owner-away rules), **ADR-014** (node action
classes; the M3 evidence run), **ADR-015** (voice-originated turns
only propose), **ADR-012** (ingestion privacy pipeline: scrubber parity),
**ADR-011** (voice transport) and **ADR-016** (proactive, Proposed).

Runbooks the Mac session uses: `friday-node.md`, `claude-code-hooks.md`,
`live-voice.md`, `voice-setup.md`, `memory-eval.md`, `learning-memory.md`,
`harness-drivers.md`, `proactive.md`, `n8n-connector.md`, `skills.md`, and
`docs/voice/live-eval.md`.

---

## What is implemented server-side (as of 2026-11-20)

All of this is on the integration branch `claude/inspiring-franklin-pw7avo`
and green in CI against reference doubles (`MASTER_PLAN.md` §AA).

| Phase | Server side (done) | Left for the Mac session / owner |
|---|---|---|
| 5 Deploy + MCP | deploy gate, SSH runner, MCP client framework, GitHub, Google, Home Assistant, connectors UI, `manage.py preflight`, seeded `openrouter` row | real-stack preflight, real-credential smokes (GitHub, Google, HA, OpenRouter), merge to `main` (C1, D1–D4) |
| 6 Voice + HUD | speech gateway (ADR-011), voice API, push-to-talk, in-tab wake word, HUD v1, caps, preflight voice checks, e2e | branch 3a (eval harness) is **not** on the integration branch; `hey_friday` training; A4/A5/A8 measurements; one manual budget refusal (A9, C2) |
| 8A Learning memory | ingestion pipeline, scrubber + corpus at 100% recall, core blocks, hook relay, producers, learning loop, morning card, sessions panel, preflight | real sources and mapping rules, `friday-hook` with the Keychain token, two weeks of morning cards (≥ 60% acceptance) (C3) |
| 9 Friday Node v1 | `kind=node` device trust, `nodes` app, protocol v1, conformance suite, WSS `node-gateway`, read tools, node-relayed hook events, `/devices`, HUD node line, preflight | the Swift app M1–M3 (B1–B3), M4 measurements (C4) |
| 8B Transcripts + graph | temporal facts, fact learning, reflection, eval harness, hybrid retrieval (kept `legacy`), Graphify job, Headroom (off), Obsidian mirror v2, transcript ingestion via the reference node, `/memory/graph`, preflight | the `nodes`-backed transcript port (server follow-up, A12), Swift owner-turn readers M1 (B4), private eval + compares + transcript week M2 (C5) |
| 10 Control | protocol v2 + invocation grants, capability contracts v2, reference executor + `srt` policy generator, 32-case escape suite (fake executor), `node_action` gate, trust check, HUD terminal, Codex/Agent SDK drivers (off), preflight | Swift confirmation window + executors M1–M2 (B5–B6), macOS `srt` evidence M3 and measurements M4 (C6), driver pins on the Linux runner (D6) |
| 11 Local live voice | `node_voice` credential and channel, `friday-voice/v1` + reference host, Pipecat pipeline, barge-in, always-on gate (closed), storm backstop, CI self-trigger gate, FAR/FRR harness, `voice.state`, preflight | Swift voice host M1 (B7), loopback + nightly M3 (B8, C7), MLX speech server M2 (C7), FAR/FRR/latency/barge-in/CPU M4 (C7) |
| 12 Reach | `proactive` app, digest, `/today`, wake routes, n8n connector (relay, allowlist, gated writes), SKILL.md skills + curator, playbooks, preflight | spoken digest and wake routes on the Mac M1 (C8), live n8n, two-week digest checkpoint (C9, D7) |

---

## Part A — Prerequisites (do these first, in this order)

### A1. Personal Team signing spike (Phase 9 d9, revised 2026-11-25) — blocks B1

> **Owner decision 2026-11-25 (d9 revised).** `friday-node` runs only on
> this Mac. It is built and installed locally from Xcode and never
> distributed, so the paid Apple Developer Program (99 USD/year), the
> Developer ID identity and notarization are **not required**. The app is
> signed with the free Apple ID **Personal Team** "Apple Development"
> identity, with hardened runtime. That identity is stable, so TCC grants
> and Keychain items should survive rebuilds. **Never** use ad-hoc signing
> ("Sign to Run Locally", `codesign -s -`) for a build that holds TCC
> grants: an ad-hoc designated requirement is the code hash, so every
> rebuild loses its grants. The signing key never leaves this Mac; CI never
> holds it. This spike proves the decision on the real Mac **before** any
> Swift branch; it takes hours, not days. Needs only Xcode (install it
> first: A2's first bullet). Sources: MASTER_PLAN §O d9; ADR-013
> "Amendment 2026-11-25 (d9 revised)".

The spike app is throwaway and lives **outside the repo**, in
`~/FridaySpike/PTSpike/` (an Xcode macOS App project, Swift, no tests).
It must be a real `.app` bundle launched with `open`: TCC attributes a
bare command-line binary started from Terminal to Terminal, which would
make the test meaningless. Use a spike bundle id
(`<your reverse domain>.friday-node-spike`), never the real app's, so the
spike's grants and Keychain items do not leak into `friday-node`.

**A1.0 — Identity.**

- [ ] Xcode → Settings → Accounts → "+" → Apple ID: sign in with the
      owner's free Apple ID. The team shows as "<Name> (Personal Team)".
      Select it → Manage Certificates → "+" → **Apple Development** if no
      certificate is listed yet.
- [ ] Check that the identity is in the login Keychain and note its Team ID
      (the `OU` of the subject):

  ```bash
  security find-identity -v -p codesigning | grep "Apple Development"
  security find-certificate -c "Apple Development" -p \
    | openssl x509 -noout -subject -startdate -enddate
  ```

  Never copy the certificate, its private key or a `.p12` off this Mac,
  and never put the identity name or Team ID in a committed file (it names
  the owner); the scripts read it from the Keychain or from the untracked
  env var `FRIDAY_NODE_SIGN_IDENTITY`.

**A1.1 — (a) Signing works with hardened runtime and the needed entitlements.**

- [ ] Create the spike project (Xcode → New → Project → macOS → App,
      product `PTSpike`). Signing & Capabilities: Team = the Personal
      Team, "Automatically manage signing" on, **Hardened Runtime** on
      with *Audio Input* and *Apple Events* ticked, **no App Sandbox**
      (remove the capability if Xcode added it). `PTSpike.entitlements`
      must hold exactly:

  ```xml
  <key>com.apple.security.device.audio-input</key><true/>
  <key>com.apple.security.automation.apple-events</key><true/>
  ```

  Info.plist: `NSMicrophoneUsageDescription` and
  `NSAppleEventsUsageDescription` strings.
- [ ] On launch the app writes one line per check to
      `~/FridaySpike/status-<CFBundleVersion>.txt` and shows the same in a
      window (no prompts are triggered from a background loop; each
      request is a button):
  - Microphone: `AVCaptureDevice.authorizationStatus(for: .audio)`
    (button: `requestAccess(for: .audio)`);
  - Accessibility: `AXIsProcessTrusted()` (button:
    `AXIsProcessTrustedWithOptions` with the prompt option);
  - Screen Recording: `CGPreflightScreenCaptureAccess()` (button:
    `CGRequestScreenCaptureAccess()`);
  - Full Disk Access: whether `FileManager` can list
    `~/Library/Safari` (or another FDA-protected directory) — added by
    hand in System Settings;
  - Automation: `AEDeterminePermissionToAutomateTarget` for
    `com.apple.systemevents` with `askUserIfNeeded` false (button: the
    same with true, or `NSAppleScript` `tell application "System Events"
    to get name of first process`);
  - Keychain: see A1.4.
- [ ] Build Release and re-sign explicitly with the Apple Development
      identity and hardened runtime, the way `scripts/build-sign-local.sh`
      will (B1):

  ```bash
  cd ~/FridaySpike/PTSpike
  xcodebuild -project PTSpike.xcodeproj -scheme PTSpike -configuration Release \
    -derivedDataPath build build
  APP=build/Build/Products/Release/PTSpike.app
  IDENTITY=$(security find-identity -v -p codesigning | grep -m1 "Apple Development" | awk -F'"' '{print $2}')
  codesign --force --options runtime --entitlements PTSpike/PTSpike.entitlements \
    --sign "$IDENTITY" "$APP"
  codesign --verify --strict --verbose=2 "$APP"                 # "valid on disk", "satisfies its Designated Requirement"
  codesign -dv --verbose=4 "$APP" 2>&1 | grep -E "Identifier|Authority|TeamIdentifier|flags|Runtime"
  codesign -d --entitlements - "$APP"                           # exactly the two entitlements above (plus any Xcode adds — record them)
  codesign -d -r- "$APP"                                        # the designated requirement
  ```

  Expect `flags=0x10000(runtime)`, `Authority=Apple Development: …`,
  `Authority=Apple Worldwide Developer Relations Certification Authority`,
  `Authority=Apple Root CA`, a `TeamIdentifier` (not `not set`), and a
  designated requirement of the form `identifier "…" and anchor apple
  generic and certificate leaf[subject.CN] = "Apple Development: …" and
  certificate 1[field.1.2.840.113635.100.6.2.1] /* exists */`. A
  requirement containing `cdhash` means the build is ad-hoc: stop and fix
  the signing. Save the requirement to the untracked file
  `~/FridaySpike/designated-requirement.txt`.
- [ ] Gatekeeper, for the record only:

  ```bash
  spctl --assess --type execute -vv "$APP"     # expected: rejected / not notarized — accepted, the app is never downloaded
  xattr -p com.apple.quarantine "$APP"         # expected: no such xattr (local build)
  ```

  A local build carries no quarantine attribute and launches without a
  Gatekeeper prompt. Never clear quarantine on anything downloaded and
  never disable Gatekeeper (`spctl --master-disable`) for this.
- [ ] Check whether Xcode embedded a provisioning profile
      (`ls "$APP/Contents/embedded.provisionprofile"`). If it did, record
      its expiry:
      `security cms -D -i "$APP/Contents/embedded.provisionprofile" | plutil -p - | grep -E 'Name|ExpirationDate|TeamIdentifier'`.
      The two hardened-runtime entitlements above should not need one; if
      a profile is required, record why.

**A1.2 — (b) Certificate validity, expiry and renewal.**

- [ ] Record the certificate's `notBefore` / `notAfter` (A1.0) and, if
      any, the profile's `ExpirationDate` (A1.1). Free-account
      certificates and profiles are short-lived compared with paid ones;
      record the actual dates, do not assume them.
- [ ] Renewal: in Xcode → Settings → Accounts → Manage Certificates,
      create a new Apple Development certificate (only with the owner's
      OK; the old one can be left to expire), rebuild with it and compare
      `codesign -d -r-` against `~/FridaySpike/designated-requirement.txt`.
      The requirement pins the leaf's **common name**, not its serial, so
      it is expected to stay equal; record whether it did and whether the
      A1.3 grants and A1.4 item survived. If the owner prefers not to
      renew now, record "renewal untested" and put a reminder two weeks
      before `notAfter` to run this check then.
- [ ] What happens at expiry. Expected, to be confirmed and recorded:
      an already-installed local build without an embedded profile keeps
      launching (a local, non-quarantined app is not assessed for
      notarization), new builds need the renewed identity, and an app
      whose embedded profile has expired may refuse to launch. If a
      profile was embedded (A1.1), its expiry is the binding date: record
      how often the app must be rebuilt, and that the rebuild keeps the
      grants (A1.3).

**A1.3 — (c) TCC grants persist across two rebuilds.**

- [ ] Install build N: `ditto "$APP" /Applications/PTSpike.app && open /Applications/PTSpike.app`.
      Grant all five: Microphone and Automation (System Events) through
      the prompts; Accessibility, Screen Recording and Full Disk Access in
      System Settings → Privacy & Security (add `PTSpike` with "+" where
      needed). Relaunch; `status-<N>.txt` must show all five granted.
- [ ] Build N+1: bump `CFBundleVersion` (and change a visible string so
      the binary really differs), build and sign exactly as in A1.1,
      `codesign -d -r-` must equal the saved requirement, quit the app,
      `ditto` over `/Applications/PTSpike.app`, `open` it. **No new
      prompt**, and `status-<N+1>.txt` shows all five still granted.
- [ ] Build N+2: the same again. Two consecutive rebuilds must keep every
      grant.
- [ ] Inspect the grants in System Settings → Privacy & Security (each
      pane lists `PTSpike` switched on). Do not read `TCC.db` directly.
      Use `tccutil reset <Service> <bundle id>` (services `Microphone`,
      `AppleEvents`, `Accessibility`, `ScreenCapture`,
      `SystemPolicyAllFiles`) **only** to clean up after the spike or to
      retry a confused state, always with the spike's bundle id, never
      without one.

**A1.4 — (d) Keychain items written by build N are readable by build N+1.**

- [ ] Build N writes a `kSecClassGenericPassword` item with service
      `friday-node-spike`, account `spike`, and the same attributes
      `Credential.swift` will use (B1:
      `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`, no iCloud sync).
      Try it twice: with the default (file-based) keychain, and with
      `kSecUseDataProtectionKeychain: true`. Record the `OSStatus` of each
      (`-34018` `errSecMissingEntitlement` on the second means the data
      protection keychain is not available to this Personal Team build —
      `Credential.swift` then uses the file-based keychain; record that in
      the B1 notes).
- [ ] Builds N+1 and N+2 read the item back **without** a Keychain access
      prompt ("PTSpike wants to use your confidential information…"). A
      prompt means the item's access control no longer matches the
      designated requirement: a failure.
- [ ] Clean up: delete the spike items from the app (or Keychain Access),
      `tccutil reset All <spike bundle id>`, remove
      `/Applications/PTSpike.app`. Keep `~/FridaySpike/` notes until the
      result is recorded.

**A1.5 — Result and fallback.**

- [ ] **Pass** = (a) signs with hardened runtime and both entitlements, no
      sandbox; (b) dates recorded (renewal tested or reminded); (c) all
      five grants survive two rebuilds; (d) the item is read without a
      prompt by N+1 and N+2. Record the result (macOS and Xcode versions,
      date, the requirement's *form* without the owner's name, cert and
      profile expiry, keychain variant used, per-check pass/fail) as a
      dated implementation note in ADR-013 ("Implementation notes —
      Personal Team spike") on a `docs/p9-personal-team-spike` branch,
      reviewed like any brief change, and tick this item. Then B1 may
      start.
- [ ] **Fail** (a needed entitlement is unavailable to a Personal Team, a
      grant or the Keychain item does not survive a rebuild, or a
      profile's expiry would break the installed app): record which check
      failed and why in the same note and in MASTER_PLAN §O d9, and switch
      to the **fallback**, the original paid path (A1-F below). B1 waits
      for A1-F.

### A1-F. Fallback / optional later: the paid Apple Developer Program

Not required by the 2026-11-25 decision. Do this **only** if A1.5 fails
(fallback), or later if the owner wants to distribute `friday-node` to
other Macs or use Sparkle-style auto-update (optional). It is the original
d9 path:

- [ ] Enrol in the Apple Developer Program (99 USD/year). This takes days.
- [ ] Create a **Developer ID Application** certificate in Xcode
      (Settings → Accounts → Manage Certificates → "+"). Check:

  ```bash
  security find-identity -v -p codesigning | grep "Developer ID Application"
  ```

- [ ] Store the notarisation credentials as a Keychain profile (an
      app-specific password from appleid.apple.com, or an App Store Connect
      API key):

  ```bash
  xcrun notarytool store-credentials friday-notary \
    --apple-id <owner apple id> --team-id <TEAMID> --password <app-specific password>
  xcrun notarytool history --keychain-profile friday-notary   # must list without error
  ```

- [ ] `scripts/build-sign-local.sh` then gains a `--notarize` mode
      (`codesign --options runtime --timestamp` with the Developer ID,
      `notarytool submit --wait --keychain-profile friday-notary`,
      `stapler staple`), keeping the same conformance-marker refusal.
      Switching identity changes the designated requirement, so every TCC
      grant and Keychain item has to be granted / re-paired once more.

The key never leaves this Mac and CI never holds it, on either path.

### A2. Tools on the Mac

- [ ] Xcode (current stable) and the command-line tools:
      `xcode-select -p && swift --version`.
- [ ] `uv` (`curl -LsSf https://astral.sh/uv/install.sh | sh`), Node 24
      (`frontend/package.json` engines `>=24 <25`), Docker Desktop, and
      `git` ≥ 2.27 (`git --version`). `rsync` and `shasum` ship with macOS
      (needed by `make friday-voice-install`).
- [ ] `sox` for recordings (`brew install sox`), optional.
- [ ] Clone and check out the integration branch (or `main` once D4 has
      merged it):

  ```bash
  git clone <repo url> ~/code/friday && cd ~/code/friday
  git checkout claude/inspiring-franklin-pw7avo
  (cd backend && uv sync --frozen)
  (cd frontend && npm ci)
  (cd node/friday-voice && uv sync --frozen)
  ```

### A3. The running stack (the Mac or the server host)

- [ ] Bring the stack up, migrate and seed (Phase 5, 8A, 8B, 12 owner
      steps):

  ```bash
  make up                        # runs `make secrets` first: NODE_SIGNING_KEY etc. into deploy/docker/.env
  make migrate
  docker compose -f deploy/docker/compose.yml exec web uv run python manage.py seed_ai_defaults
  docker compose -f deploy/docker/compose.yml exec web uv run python manage.py seed_ingestion_sources
  docker compose -f deploy/docker/compose.yml exec web uv run python manage.py build_vector_indexes --check
  curl -s http://127.0.0.1:8002/healthz   # "redis": true and a 16-character signing_key_id
  ```

- [ ] `NODE_SIGNING_KEY` lives only in the host's `deploy/docker/.env`
      (Phase 9 owner step 5). Rolling it later means re-pairing every node.
- [ ] Expose **only** `/ws/node/v1` of `node-gateway` through the overlay
      or TLS reverse proxy (`docs/runbooks/friday-node.md` "Expose
      `/ws/node/v1`", Caddy example). Never publish 8002 on 0.0.0.0.
      Optionally compute the SPKI pin and set `NODE_TLS_PIN_SHA256` and
      `PREFLIGHT_NODE_TLS_URL` (runbook "Pinning the served certificate").
- [ ] A local `personal_data_allowed` chat provider is active (Ollama).
      Personal node reads, calendar/private episodes, the digest and the
      curator fail closed without one.
- [ ] Baseline preflight, with the reference node paired so the node rows
      have a node to report (dev stack, `DEBUG` on):

  ```bash
  CODE=$(docker compose -f deploy/docker/compose.yml exec -T web \
    uv run python manage.py issue_pairing_code --node --strong --plain)
  docker compose -f deploy/docker/compose.yml exec web \
    uv run python manage.py run_reference_node --code "$CODE"   # Ctrl-C to stop
  make preflight                                                # in another shell
  ```

  Expect no `fail`. Expected `warn`s until this session is done:
  `node_tls_pin` (no pin), `graphify`, `transcripts`, `eval_baseline`,
  `node_escape_suite`, `voice_live_selftest`, `proactive_schedules`,
  `proactive_last_run` and the three n8n rows. Record the summary line in
  the Phase 5 brief §10. Revoke the reference node on `/devices` after.

### A4. Host-side backend for the conformance servers

The conformance servers run from the repo checkout on the Mac, against the
stack's Postgres and Redis (compose publishes them on loopback):

```bash
cd backend
export DATABASE_URL=postgres://jarvis:jarvis@127.0.0.1:5433/jarvis \
       REDIS_URL=redis://127.0.0.1:6380/0
uv run python manage.py build_node_conformance --check   # case and vector files are current
```

(Use the user and password from `deploy/docker/.env` if they differ.)

### A5. Decisions to take before the Swift work

- [ ] **macOS CI (Phase 9 owner step 8).** Decide whether a macOS CI runner
      may run `swift test` for `node/friday-node`, unsigned and with no
      signing secrets. If not, run `swift test` locally before each merge.
- [ ] **Cloud audio rows and the audio cap (Phase 6 d2).** Decide which
      cloud audio rows may be enabled; confirm the 20 USD/month audio cap
      in the admin.
- [ ] **Third-party terms (Phase 10 d4).** Check the Claude, Codex and
      Antigravity terms for automation. Record the date in
      `HARNESS_DRIVER_TERMS_CHECKED_ON` and the note in
      `docs/runbooks/harness-drivers.md` before any driver is enabled.

### A6. `srt` on the Mac (Phase 10 owner step 4) — blocks B6

- [ ] Install Anthropic's sandbox-runtime `srt` at an **exact** version and
      record it in `docs/mcp/dependencies/node-gateway.md`.
- [ ] Confirm the CLI matches `SRT_ARGV_TEMPLATE`
      (`srt --settings <file> -- <argv...>`) and the settings keys of
      `backend/nodes/conformance/vectors/sandbox.json`
      (`network.allowedDomains`, `deniedDomains`, `allowUnixSockets`,
      `allowLocalBinding`, `filesystem.denyRead`, `allowWrite`,
      `denyWrite`). If the pinned version differs, change the generator
      (`backend/nodes/reference/sandbox.py`), regenerate the vectors with
      `uv run python manage.py build_node_conformance` in a dedicated
      branch, and merge it **before** B6.

### A7. Local speech server (Phase 6 branch 1 step 0) — blocks C2 and B7

- [ ] Install speaches on the Mac host (not in Docker), with Piper served,
      on `:8000` (`docs/runbooks/voice-setup.md` §1). Record the exact
      STT/TTS model ids, voice ids, the WAV response shape and cold/warm
      latency in the Phase 6 brief; set `VOICE_*` in `.env`.
- [ ] Done-when: `InferenceGateway().transcribe/synthesize` round-trips a
      Turkish sentence with one `ModelUsage` each (`voice-setup.md` §3, in
      `manage.py shell`).
- [ ] One real transcription and one speech request through the BFF from a
      trusted browser (`voice-setup.md` §4): 200s, one `ModelUsage` each,
      workspace = the conversation's, cost 0.

### A8. Train and pin `hey_friday.onnx` (Phase 6 owner step 4, Phase 11 owner step 3) — blocks B7 and C7

- [ ] Train `hey_friday.onnx` offline with openWakeWord's synthetic-data
      pipeline (`voice-setup.md` §6.2). Keep it at `~/FridayEval/hey_friday.onnx`
      until measured.
- [ ] Browser: `VOICE_WAKE_MODEL_SRC=~/FridayEval/hey_friday.onnx make voice-models`
      (writes `frontend/public/voice-models/`).
- [ ] **Pin only after C7's measurement says `permitted`**: the sha256 goes
      into `node/friday-voice/models.json` (`keyword`) and
      `frontend/src/lib/voice/model-manifest.json`, in one reviewed change
      (`node/friday-voice/tests/test_models.py` checks they are equal).

### A9. Start the long recordings early (Phase 11 owner steps 1 and 4)

- [ ] Record **24 h of household audio** into `~/FridayEval/household/`
      (one file per hour is fine) on the Mac's own microphone in the rooms
      where the node listens. Outside the repo, never uploaded.
- [ ] Record **≥ 50 "Hey Friday" positives** into `~/FridayEval/positives/`,
      one per file, across rooms and distances.

### A10. Node voice model directory — a server-side gap to close first (blocks B7)

The Phase 11 brief and `docs/runbooks/live-voice.md` §1 say
`make voice-models --node` fills the node's model directory
(`~/Library/Application Support/Friday/voice/models/`, from
`node/friday-voice/models.json`). **As of 2026-11-20 no such mode exists**:
`frontend/scripts/voice-models.mjs` has no `--node` option, and GNU make
rejects `--node` as an unknown make option. Before B7:

- [ ] Add a small tooling branch (`feat/p11-node-voice-models`) that adds
      the node mode (for example `make voice-models-node`: download every
      `models.json` entry with a `source`, verify its sha256, copy the
      owner's `hey_friday.onnx` from `VOICE_WAKE_MODEL_SRC`, write into
      `modelDir`), with a test, and fix the two docs that name
      `make voice-models --node`. This can be done in a cloud session too.
- [ ] Then: `make friday-voice-install` and the node model fill; check with

  ```bash
  cd node/friday-voice
  uv run friday-voice conformance --check
  uv run friday-voice conformance
  uv run friday-voice selftest --ci
  ```

### A11. Phase 6 branch 3a — a server-side gap (blocks C2's A4 measurements)

`feat/p6-voice-eval` (the Turkish eval set's `voice_eval` scorer, replay
harness and manifest) is **not on the integration branch** and no local
branch of it exists (Phase 6 brief A9).

- [ ] Implement or restore it per the Phase 6 brief ("Amendment
      2026-11-20", branch 3a) before C2's WER and TTS measurements. This is
      not Swift work and can be done in a cloud session.

### A12. The `nodes`-backed transcript port — a server-side gap (blocks B4's live half and C5 item 4)

8B closeout owner step 2. Until it lands, preflight `transcripts` warns and
the transcript pull is a no-op. It needs no Mac:

- [ ] Branch `feat/p8-node-transcript-port`: move the two owner-turn
      contracts from `backend/integrations/node_reference/contracts/v1/`
      into `backend/nodes/capabilities/v1/` (ten contracts; today eight),
      dispatch them in `backend/nodes/reference/readers.py`, fold
      `owner_turns_cases.json` into `backend/nodes/conformance/cases/v1/`,
      regenerate `scrub.json` with `transcripts.jsonl`
      (`manage.py build_node_conformance`), set `SYSTEM_READ_CAPABILITIES`,
      add a `NodeReadPort` over
      `nodes.services.invoke_read(channel=CHANNEL_SYSTEM, actor=owner)`,
      install it in `tools.transcript_ingest`, and re-run
      `tools/tests/test_transcript_ingest.py` and the 8B e2e against the
      in-process gateway transport.

---

## Part B — Swift `friday-node` tasks (in this order)

All Swift code lives in `node/friday-node/` (Swift package: app target
`FridayNode`, CLI target `friday-node` with `hook`, `scrub` and later
`voice-loopback`, test target `NodeCoreTests`). Apple frameworks only,
unless a dependency goes through the governance manifest
(`Package.resolved` committed, exact versions).

Common commands for every B branch:

```bash
cd node/friday-node
swift build
swift test                                            # NodeCoreTests, Debug
xcodebuild -scheme FridayNode -configuration Conformance build   # unsigned, CONFORMANCE condition
xcodebuild -scheme FridayNode -configuration Release test \
  -only-testing:NodeCoreTests/ReleaseHygieneTests      # the conformance pin type is not compiled in
scripts/build-sign-local.sh                            # Release only; refuses conformance markers; Personal Team signing + hardened runtime
```

and on the server side (Part A4 environment, from `backend/`):

```bash
uv run ruff check . && uv run ruff format --check . && uv run lint-imports
uv run python manage.py build_node_conformance --check
```

**The conformance build rule (ADR-013 decisions 2 and 4).** Everything
that pins the fake server's self-signed SPKI or the conformance key sits
inside `#if CONFORMANCE … #endif` in the separate, unsigned `Conformance`
configuration. The release build has **no** environment variable, user
default or launch argument that changes the TLS pin, the pinned signing
keys or the server URL. The conformance build is never signed with the
Personal Team identity (or any Developer ID), never notarised and never a
login item. `scripts/build-sign-local.sh` fails before `codesign` if
`strings` on the release Mach-O finds `FRIDAY_NODE_CONFORMANCE`,
`conformance` or the conformance public key's base64 (from
`backend/nodes/conformance/keys/`).

**`scripts/build-sign-local.sh` (d9 revised 2026-11-25; replaces
`build-sign-notarize.sh`).** It builds the `Release` configuration only,
runs the conformance-marker refusal above, then signs with the Personal
Team identity and hardened runtime and verifies:

```bash
IDENTITY=${FRIDAY_NODE_SIGN_IDENTITY:-$(security find-identity -v -p codesigning   | grep -m1 "Apple Development" | awk -F'"' '{print $2}')}
[ -n "$IDENTITY" ] && [ "$IDENTITY" != "-" ] || { echo "no Apple Development identity (A1)"; exit 1; }
codesign --force --options runtime --entitlements FridayNode/FridayNode.entitlements   --sign "$IDENTITY" "$APP"
codesign --verify --strict --verbose=2 "$APP"
codesign -d -r- "$APP" 2>&1 | grep -q cdhash && { echo "ad-hoc requirement: refusing"; exit 1; }
```

It refuses ad-hoc signing (`-`, or a requirement with `cdhash`), warns if
the designated requirement differs from the one recorded in the untracked
`~/.config/friday-node/designated-requirement.txt` (a change means every
TCC grant and Keychain item is lost), and installs with `ditto` to
`/Applications/FridayNode.app`. No notarization and no stapling. Nested
code (helpers, frameworks) is signed inside-out before the app; never
`--deep`. The identity name and Team ID are never committed.

### B1. Phase 9 M1 — `feat/p9-node-app`: shell, identity, session, verifier

Needs: A1, A2, A3, A4. Spec: Phase 9 brief "M1", D5, D6, D13 items 1–4
and 9; ADR-013.

- [ ] Create `Package.swift`; `Sources/FridayNode/` (menu bar
      `NSStatusItem`, `LSUIElement`); `Sources/NodeCore/` with
      `Canonical.swift` (JCS subset), `Envelope.swift`, `Verifier.swift`,
      `NonceCache.swift`, `Session.swift` (`URLSessionWebSocketTask`,
      pinning `URLSessionDelegate`, subprotocol offer
      `friday-node.v2, friday-node.v1` as the v2 work lands, **no
      `Origin`**), `Backoff.swift`, `Credential.swift` (Keychain
      `kSecClassGenericPassword`, service `friday-node`,
      `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`, no iCloud sync;
      the secret, the TLS SPKI pin and the pinned server keys),
      `Pairing.swift` (`POST /api/v1/nodes/pair`), `Rotation.swift`
      (`POST /api/v1/auth/nodes/credential/rotate`; persist the new secret
      **before** finishing the call — ADR-013 residual);
      `scripts/build-sign-local.sh` (Release only, conformance-marker
      refusal, `codesign --force --options runtime` with the Personal Team
      "Apple Development" identity, `codesign --verify --strict`, no
      ad-hoc, no notarization — see Part B intro); entitlements: hardened
      runtime and `com.apple.security.network.client` only.
- [ ] Gateway behaviour (Phase 9 branch 4 owner step 6): `Authorization:
      Bearer <jnd_…>`, `hello` within 10 s, heartbeat every 15 s (45 s of
      silence closes 4408), ≤ 20 frames/s and ≤ 256 KiB per frame, one
      `result` per `invoke` on the same socket, `bye{revoked|kill_switch|
      credential_expired}` = do not reconnect, close 1013 = retry with
      backoff, 4401 = stop until re-paired or the kill switch is released.
- [ ] The Swift test target loads `backend/nodes/protocol/vectors/canonical.json`
      and `signing.json` from the repo path and reproduces them byte for
      byte; decodes `backend/nodes/protocol/schemas/v1/` strictly (unknown
      keys refused, full-match patterns, integers only); pins
      `server_signing_keys` from the pair response.

**Server-side contract tests it must pass:**

1. Offline conformance: `Tests/NodeCoreTests/ConformanceTests.swift` runs
   every file in `backend/nodes/conformance/cases/v1/*.json` with the
   reference node's verdicts and codes, **none skipped**.
2. Live conformance, with the `Conformance` build connected:

   ```bash
   cd backend && uv run python manage.py node_conformance_server --port 8765 --tls-self-signed --protocol 1
   ```

   prints all-pass.
3. Real stack: pair with a code from `/devices` → "Mac düğümleri" (or
   `manage.py issue_pairing_code --node --strong`); `hello.ack` arrives;
   `/devices` shows `Bağlı`; `GET /api/v1/nodes` shows it `connected`; the
   kill switch (`/devices`, or
   `docker compose -f deploy/docker/compose.yml exec web uv run python manage.py node_kill_switch on`)
   closes the socket within 2 s; the app does not reconnect after
   `bye{kill_switch}` and resumes after release (`node_kill_switch off`,
   then the owner action in the menu); revoking
   (`/api/v1/nodes/<id>/revoke`) closes the socket within 2 s and requires
   re-pairing.
4. No listener:

   ```bash
   lsof -nP -iTCP -sTCP:LISTEN -a -p $(pgrep FridayNode)   # prints nothing
   nettop -p $(pgrep FridayNode) -L 1                       # no listening socket
   ```

5. `make preflight`: `node_link:<the Mac's name>` passes and
   `node_signing_key` confirms the node pinned the current key.

### B2. Phase 9 M2 — `feat/p9-node-readers`: readers, grants, local policy, scrubber

Needs: B1. Spec: Phase 9 brief "M2", D7; `backend/nodes/reference/readers.py`
module docstring (normative reader rules).

- [ ] Readers in `Sources/NodeCore/Readers/`: `ClaudeSessions`
      (`~/.claude/projects/*/*.jsonl`), `CodexThreads`
      (`~/.codex/sessions/**/rollout-*.jsonl`) — streaming, bounded,
      `user`/`assistant` text only, tool calls counted never returned, each
      text ≤ 2000 chars; `CCUsage` (a pinned `ccusage` binary, path in local
      policy, hash checked, fixed argv `--json --offline`, governance
      manifest entry; missing binary → `unsupported`); `GitStatus`
      (`/usr/bin/git -C <repo> status --porcelain=v2 --branch` and
      `log -1 --format=%H%x00%cI`, repos in the local list only, counts
      never file names); `Frontmost`, `Windows` (`NSWorkspace`,
      `CGWindowListCopyWindowInfo`; titles only with the local toggle on
      and Screen Recording granted).
- [ ] `Policy/{LocalPolicy,Grants}.swift`: grants off by default, changed
      only after `LAContext.evaluatePolicy(.deviceOwnerAuthentication)`;
      a revoked grant gives `not_granted` at once plus
      `grant.availability_changed` with items exactly
      `{name, version, availability}`.
- [ ] Honour `cwd_prefixes` exactly as the reference node; report `cwd` in
      `~/…` form under the home directory.
- [ ] `Scrubber.swift` (port of `backend/memory/ingestion/scrubber.py`) and
      `friday-node scrub --stdin`.

**Server-side contract tests it must pass:**

1. Scrub parity: `Tests/NodeCoreTests/ScrubParityTests.swift` reproduces
   every vector in `backend/nodes/conformance/vectors/scrub.json` byte for
   byte (recall alone is not enough).
2. Corpus recall 100%: `node/scripts/scrubber-corpus.sh` (written in this
   branch) runs `friday-node scrub` over every item of
   `backend/memory/tests/scrubber_corpus/` and compares with the labels.
3. Offline conformance: the `not_granted` and args-schema families and the
   13 `backend/nodes/conformance/cases/v1/v1_read_*.json` cases, the
   `Conformance` build reading the fixture home of
   `backend/nodes/reference/fixtures/factory.py` (templates under
   `fixtures/transcripts/` with `{{HOME}}` = the fixture home and the
   `<<>>` split marker removed, repos `~/work/acme` and `~/code/friday`,
   `fixtures/desktop.json` in place of `NSWorkspace`); `report` includes
   `reader_calls` (0 on every refusal case).
4. Live: `uv run python manage.py node_conformance_server --port 8765 --tls-self-signed --protocol 1`
   all-pass again.
5. Real stack (manual): on `/devices` an ungranted directory reads "Mac'te
   izin verilmedi"; grant `~/.claude/projects` with Touch ID → it turns
   "Kullanılabilir". In a web chat with a local model, "Claude Code
   oturumlarım ne yapıyor?" is answered from local detail; rows of
   unmapped directories never appear (the hooks path rules must use the
   `~/…` form). Server pins: `tools/tests/test_node_reads.py`,
   `config/tests/test_node_read_turns.py`.

### B3. Phase 9 M3 — `feat/p9-node-hook-relay`: Unix socket, spool, switch-over

Needs: B1 (B2 for the scrubber). Spec: Phase 9 brief "M3"; ADR-013
decision 7; reference `backend/nodes/reference/{hook_relay,spool}.py`.

- [ ] `Sources/friday-node/Hook.swift`: stdin ≤ 256 KiB, the same
      allow-listed event as `backend/integrations/cli/friday_hook.py`
      `build_event`, written to
      `~/Library/Application Support/Friday/node.sock`, exit 0 within 3 s
      on any error.
- [ ] `HookSocket.swift`: directory 0700, socket 0600, `getpeereid` uid ==
      own uid.
- [ ] `Spool.swift`: ≤ 10 485 760 bytes (oldest batch dropped first),
      ≤ 24 h, file 0600 `O_EXCL` in a 0700 directory, batches of 1–50,
      `event{spooled:true}` after `hello.ack`, one batch in flight, delete
      only on the `event.ack` naming that frame id; an unacked live event is
      spooled when the socket drops.
- [ ] Re-express `backend/nodes/tests/test_reference_spool.py` and
      `test_reference_hook_relay.py` in Swift.
- [ ] Switch-over: change the hook entries in `~/.claude/settings.json`
      from `friday-hook` to `friday-node hook` as
      `docs/runbooks/friday-node.md` "Switching Claude Code hooks to the
      node" says; update `docs/runbooks/claude-code-hooks.md`; keep
      `friday-hook` installed as the fallback.

**Server-side contract tests it must pass** (they must stay green with the
Swift relay's traffic):

```bash
cd backend
uv run pytest -q config/tests/test_hooks_via_node.py nodes/tests/test_node_events.py
```

Manual pass: stop the stack, run a Claude Code session, restart; the
spooled events arrive **once** in `/sessions`.

**After B1–B3: build, sign (Personal Team identity) and install release
build N** with `scripts/build-sign-local.sh`, then do C4.

### B4. Phase 8B M1 — `feat/p8-node-transcript-readers`: owner-turn readers

Needs: B2, A12 (for the live half). Spec: Phase 8B brief "Mac session
(deferred)" M1, D13 (owner-turn rules), D17.

- [ ] `Sources/NodeCore/Readers/ClaudeOwnerTurns.swift` and
      `CodexOwnerTurns.swift` (streaming JSONL, D13 rules); catalogue
      entries `claude.transcripts.owner_turns` and
      `codex.transcripts.owner_turns` with schemas loaded from, or checked
      byte-identical to, `backend/nodes/capabilities/v1/` (before A12 they
      are in `backend/integrations/node_reference/contracts/v1/`; use
      whichever the merged tree has).
- [ ] `Tests/NodeCoreTests/OwnerTurnReaderTests.swift` over the reference
      fixtures (`backend/nodes/reference/fixtures/{claude,codex}/`, or
      `backend/integrations/node_reference/fixtures/` before A12), output
      compared to `expected_owner_turns.json`.
- [ ] Availability `not_granted` until the Touch ID grant; revoke gives
      `not_granted` at once plus `grant.availability_changed`.
- [ ] Real-file verification, never committed: the debug-only
      `friday-node scrub-preview --owner-turns ~/.claude/projects/<one project>`
      (and the Codex equivalent) prints counts per line kind and per
      excluded marker, never text. A line kind not covered by D13 → stop,
      update D13 + fixtures + reference readers + cases in one server
      branch, re-run. The owner skims the first 20 accepted turns and
      confirms they are text the owner typed.

**Server-side contract tests it must pass:** every case in
`backend/nodes/conformance/cases/v1/` (with the owner-turn cases) offline
and live (`node_conformance_server --port 8765 --tls-self-signed --protocol 1`);
scrub parity with `scrub.json` including `transcripts.jsonl`; the pull-job
tests re-run by hand against the live Mac node (episodes only in mapped
workspaces, no assistant text in any stored row — a DB scan for known
assistant phrases of the session); the 8B closeout e2e with the live node in
place of the reference node.

### B5. Phase 10 M1 — `feat/p10-node-confirm`: v2 verifier, grants, confirmation window

Needs: B1–B3. Spec: Phase 10 brief "Mac session (deferred)" M1, D5 checks
13–24, D12; ADR-013 decision 6; ADR-014.

- [ ] `GrantVerifier.swift` (domain-separated Ed25519 over the canonical
      grant, field binding, TTL ≤ 120 s); `VerifierV2.swift` (D5 checks
      13–24 in order, same codes); `OutputStreamer.swift` (8 KiB chunks,
      seq, D12 carry-over scrub so a split secret is still removed, 1 MiB
      cap, tail ≤ 4 KiB); v2 subprotocol offer in `Session.swift`.
- [ ] `Sources/FridayNode/ConfirmationWindow.swift`: non-activating
      floating `NSPanel`, rendered from the node's **own** validated args
      and the capability's `confirm_template` (never server text); shows
      the approval id's first 8 characters for gate-class, the repo's local
      name and the exact argv and network setting for `repo.command.run`;
      buttons "Çalıştır" / "Reddet"; 60 s countdown; closes on socket loss,
      kill switch or timeout; one window at a time; "Durdur" while running.
- [ ] Validate `text`, `title` and `repo_path` with the **identical**
      pattern strings from the v2 contract files (no `CharacterSet`
      approximation); replay the hostile-insert families of
      `backend/nodes/tests/test_capabilities_v2.py`; mirror
      `x-friday-max-bytes`.
- [ ] The `Conformance` build implements the external JSON-lines hook of
      `backend/nodes/conformance/gateway.py` (ops `prepare`, `advance`,
      `tick`, `kill_switch`, `report`, `wait_disconnected`), compiled only
      under `#if CONFORMANCE`.

**Server-side contract tests it must pass:**

1. Offline: every file in `backend/nodes/conformance/cases/v1/` and
   `cases/v2/` in `ConformanceTests.swift`, none skipped; the test target
   loads `backend/nodes/protocol/schemas/v2/`,
   `backend/nodes/protocol/vectors/{signing_v2,grants}.json` (every
   `grants.json` refusal gives the same code at the same check) and
   reproduces `backend/nodes/conformance/vectors/scrub_stream.json` piece
   for piece.
2. Live, both protocols:

   ```bash
   cd backend
   uv run python manage.py node_conformance_server --external --port 8765 --tls-self-signed --protocol 2 --hook-socket /tmp/friday-conf.sock
   uv run python manage.py node_conformance_server --external --port 8765 --tls-self-signed --protocol 1 --hook-socket /tmp/friday-conf.sock
   ```

   all-pass, including the window cases driven by the server's scripted
   owner.
3. Real stack: a typed "testleri çalıştır" in a repo mapped locally,
   approved on `/approvals`, shows the window with the same approval id
   prefix; deny → `denied` on `/approvals` and the HUD; allow → output in
   the HUD terminal panel (a long `uv run pytest` streams within a second
   per line); the kill switch closes an open window within 2 s. The Swift
   node must produce the same frames, statuses and chain rows as the
   reference node in `backend/ops/tests/test_control_e2e.py`,
   `backend/tools/tests/test_node_actions.py` and
   `test_node_action_endpoints.py`
   (`confirmation{shown}` then `{approved|denied|timed_out}`, one `result`
   with `confirm_state`, `exit_code`, scrubbed `tail`, nothing after a
   socket loss). Afterwards:

   ```bash
   docker compose -f deploy/docker/compose.yml exec web uv run python manage.py verify_node_audit_chain
   ```

### B6. Phase 10 M2 — `feat/p10-node-executors`: srt, recipes, AX, open/focus

Needs: B5, A6. Spec: Phase 10 brief M2, D4; ADR-014.

- [ ] `Exec/SrtExecutor.swift` (spawns `srt` with the settings document
      generated exactly as `backend/nodes/reference/sandbox.py`; argv only;
      stripped env; SIGTERM/SIGKILL on loss), `Exec/SandboxPolicy.swift`;
      `Policy/CommandMappings.swift` (repo root, local name,
      `command_kind` → argv, network domains, runtime; edited only behind
      `LAContext.evaluatePolicy(.deviceOwnerAuthentication)`);
      `Recipes/` (one file per v1 recipe, fixed AppleScript, params only as
      escaped AppleScript string literals, with escaping tests);
      `AX/{Press,TypeText}.swift` (frontmost window of `bundle_id` only,
      exact title match, refuses when not frontmost or a secure text field
      has focus); `Apps/OpenFocus.swift` (`NSWorkspace`, local bundle
      list); availability in `hello` (`granted` only with the TCC grant).
- [ ] Owner, in the node's policy UI with Touch ID (Phase 10 owner step 5):
      map the repos and commands, enable the wanted recipes and the
      bundle-id list.

**Server-side contract tests it must pass:**

1. Sandbox parity: `SandboxPolicyTests.swift` reproduces every vector in
   `backend/nodes/conformance/vectors/sandbox.json` byte for byte
   (`settings_canonical`).
2. The fuzz families `backend/nodes/conformance/cases/v2/args_*` are
   refused by the Swift schema validator; AppleScript params never reach
   interpolation (asserted with a spy).
3. Escape-policy equivalence: the test target exports its documents in the
   `--export-policy` format, then

   ```bash
   cd backend
   uv run python manage.py node_escape_suite --export-policy /tmp/reference-policy.json   # the reference format
   uv run python manage.py node_escape_suite --check-policy <file exported by the Swift tests>   # 32/32 covered
   ```

### B7. Phase 11 M1 — `feat/p11-node-voice-host`: the Swift voice host

Needs: B1–B3, B5 merged; A7, A8, A10. Spec: Phase 11 brief "Mac session
(deferred)" M1, D6, D9, D12, D15; ADR-013 decisions 1, 5, 6; ADR-015.

- [ ] `make friday-voice-install` (prints the installed `uv.lock` sha256;
      compile it into the host) and the node model fill (A10).
- [ ] `Voice/VoiceChildHost.swift` (spawns
      `~/Library/Application Support/Friday/voice/.venv/bin/friday-voice run`,
      stdin/stdout pipes, clean env `PATH`/`HOME`/`LANG` only, lock-hash
      check, restart backoff 1 s → 60 s, watchdog 10 s, kill on quit, kill
      switch or WS loss); `VoiceFraming.swift` and `VoiceMessages.swift`
      (validated against `node/friday-voice/friday_voice/protocol/schemas/v1/`);
      `AudioEngine.swift` (`AVAudioEngine`,
      `inputNode.setVoiceProcessingEnabled(true)`, 16 kHz mono PCM16, 20 ms
      frames, `play` through the same engine as the AEC reference,
      `stop_playback` ≤ 100 ms, paced playback exactly as
      `HostCore(paced_playback=True)`); `MicIndicator.swift` (states `off`,
      `armed_ptt`, `armed_wake`, `listening`, `thinking`, `speaking`,
      `muted`, `storm_muted`, `error`); `Hotkey.swift` (global
      push-to-talk); `VoiceSettings.swift` (off / push-to-talk / always-on;
      always-on needs LocalAuthentication; mute read failure →
      `mute: unreadable`); `VoiceCredential.swift` (mints `node_voice`
      with the Keychain node secret, refresh ≤ 60 s before expiry, never
      written anywhere, a 401 releases the mic and backs off).
- [ ] `voice.state` relay exactly as `backend/nodes/reference/node.py`
      `relay_voice_state` (coalesced ≤ 1/s, v2 only, after `hello.ack`,
      validated against `$defs.voice_state_item`, re-sent with every
      heartbeat and after reconnect, never spooled); the `Conformance`
      build's `{"op": "voice_state", …}` hook op and the
      `voice_states_dropped` report key.
- [ ] `release_mic(storm)` releases the mic and shows `storm_muted` until a
      mute on → off cycle.
- [ ] Microphone entitlement (`com.apple.security.device.audio-input`,
      checked in A1) and TCC usage string; a new build signed with the same
      Personal Team identity (`scripts/build-sign-local.sh`).

**Server-side contract tests it must pass:**

0. Credential: same exchange as `backend/accounts/tests/node_voice_helpers.py`
   (`POST /api/v1/auth/nodes/voice-token`, `201 {token, token_id,
   expires_at, previous_valid_until}`, `Cache-Control: no-store`);
   `backend/accounts/tests/test_node_voice_token.py` and
   `backend/config/tests/test_node_voice_surfaces.py` stay green.
1. Offline: every host case in
   `node/friday-voice/friday_voice/conformance/cases/` in
   `VoiceConformanceTests.swift` (step vocabulary:
   `friday_voice/conformance/definitions.py` docstring), none skipped.
2. Live, one process per host case, launched by the host's conformance
   build:

   ```bash
   cd node/friday-voice
   uv run friday-voice conformance-child --case <id> [--cases <dir>]   # writes "verdict": "pass" per case
   ```

3. Node conformance with the nine voice-state cases:
   `cd backend && uv run python manage.py node_conformance_server --external --protocol 2 --tls-self-signed --port 8765 --hook-socket /tmp/friday-conf.sock`.
4. Real stack: hotkey push-to-talk "bugün ne var" → spoken reply; a voice
   "testleri çalıştır" → a card on `/chat/<id>` and nothing else; kill
   switch mid-session → icon `off` within 2 s and the next request
   refused; the HUD node line and ring follow the menu-bar state within
   ~1 s, read `unknown` 30 s after quitting, `off` after the kill switch;
   the `/voice` storm banner's release re-enables the Mac within one
   request. The Swift host produces the same frames, states and server
   rows as the reference host in `backend/ops/tests/test_live_voice_e2e.py`.
5. Barge-in and playback: pinned by `node/friday-voice/tests/test_barge_in.py`,
   `tests/test_session.py` and `fixtures/barge_in.json`; re-run the
   scripts' shape by hand with the real AEC (feeds C7 item 4).
6. On the Mac, the real models: `cd node/friday-voice && FRIDAY_VOICE_MODEL_DIR="$HOME/Library/Application Support/Friday/voice/models" uv run pytest -m real_models`;
   check the CA pin against the real server chain (a Caddy internal CA must
   be in the child's trust store or passed as the install's CA file).

### B8. Phase 11 M3 (code half) — `feat/p11-node-voice-loopback`: loopback and the nightly job

Needs: B7. Spec: Phase 11 brief M3, `node/friday-voice/friday_voice/selftest.py`,
`docs/voice/live-eval.md` §4.

- [ ] `friday-node voice-loopback`: one PCM WAV on stdin → played through
      the same voice-processing graph → AEC-cleaned capture (clip + 1 s)
      as 16 kHz mono PCM16 WAV on stdout; exit 0 (non-zero on any audio or
      TCC failure); never writes audio to disk; releases the mic.
- [ ] Token hand-over: the host mints a fresh `node_voice` token into a
      0600 file for `--token-file`, deleted after the run.
- [ ] `selftest-nightly.sh` (skips on battery) and
      `~/Library/LaunchAgents/ai.friday.voice-selftest.plist` (03:17
      nightly) from `docs/voice/live-eval.md`;
      `launchctl load -w ~/Library/LaunchAgents/ai.friday.voice-selftest.plist`.

**Contract test:** the `CommandLoopback` tests (loopback section of
`node/friday-voice/tests/test_selftest.py`) with the real binary in place of
the Python echo script. A manual run:

```bash
cd node/friday-voice
uv run friday-voice selftest --nightly --server <https url> --token-file <0600 file> \
  --model-dir "$HOME/Library/Application Support/Friday/voice/models" \
  --loopback-cmd "<path>/friday-node voice-loopback"
```

---

## Part C — Measurements and go/no-go evaluations (real hardware, real data)

Record every result in the named brief's "Go/no-go record" (or "A9 — exit
criteria record" for Phase 6) on a `docs/…` branch, reviewed like any
brief change. A missed target is either met later or **re-set consciously**
in the record, with the dominant stage named.

### C1. Phase 5 — real stack and real credentials

- [ ] `make up && make frontend-build && make preflight` with
      `PREFLIGHT_BFF_URL` (the running frontend) and
      `PREFLIGHT_FRONTEND_BUILD_ID_FILE` (its `.next/BUILD_ID`): 0 fail;
      record the summary line in the Phase 5 brief §10.
- [ ] `OPENROUTER_API_KEY` in `deploy/docker/.env`, re-run
      `seed_ai_defaults` (or enable the `openrouter` row), `make preflight`:
      `provider:openrouter` passes and one `ops.preflight` `ModelUsage` row
      shows its cost.
- [ ] `make deploy-smoke` against the local sshd target; then by hand: a
      real harness job → "PR aç" → "Deploy" on staging → `work_log` rows on
      Hafıza. Force one attention state (failing health check) and confirm
      the `rolled_back` / `health_failed` banner.
- [ ] Home Assistant (§7): a dedicated **non-admin** HA user and a
      long-lived token in the host env (`HA_BASE_URL` LAN/Tailscale only,
      `HA_TOKEN`); optionally narrow `HA_ALLOWED_DOMAINS`, set
      `HA_WOL_ALLOWED_MACS`; enable the `home_assistant` binding; live
      smoke "salonun ışığını %40 yap" (dialog shows service, exact entity
      id, `data.brightness_pct`; approve; read-back state) and "salonun
      durumu ne?" (answered by a local model, conversation private).
- [ ] Connectors UI (§9): phone-width check (390 px) of `/deploys`, the
      deploy dialog, `/deploys/runs/<id>` and the PR panel; enable/disable a
      binding on Ayarlar → Bağlantılar (pins "Henüz kontrol edilmedi" →
      "Doğrulandı").
- [ ] The open branch 5–8 steps: real GitHub sidecar pins, Google OAuth
      consent (`docs/runbooks/google-connector.md`).

### C2. Phase 6 — voice and HUD on the Mac (A9 exit criteria)

Needs: A7, A8, A11.

- [ ] **A1 spike (paid):** one real OpenRouter STT call (30 s WAV) and one
      TTS call; confirm `usage.cost`, the PCM `Content-Type` rate,
      `/generation?id=` `total_cost`, and which voices speak Turkish;
      correct seeded prices/model ids if needed.
- [ ] **A4 (after A11):** record the Turkish evaluation set and the
      negative audio on the Mac; WER per condition (≤ 12% quiet, ≤ 18%
      noise, ≤ 15% code-switched), TTS rating ≥ 4/5, wake FRR ≤ 10% /
      FAR ≤ 0.5/h at the chosen threshold; set the default threshold from it.
- [ ] **A5:** push-to-talk end to end in Chromium and Safari with local
      models (Whisper `small`, `llama3.1:8b`, Piper medium); p50/p95 end of
      speech → first audio from `VoiceTurnTrace.playback_start_ms`
      (`voice-setup.md` §5): p50 ≤ 1.5 s, p95 ≤ 2.5 s.
- [ ] Re-run the Phase 4 R4b load check with STT/TTS mixed into SSE and
      polling (no stream starved, no 503 from the pool).
- [ ] Voice behaviour: a task drafted by voice is created only after its
      card is clicked; "hatırla ki …" shows a pending proposal on Hafıza;
      "bu ne kadar tuttu?" reads a plausible receipt; shell mute and a
      `?mute=1` tab are silent; echo cancellation on laptop speakers is
      acceptable; listen to the segmenter on real replies and note missing
      Turkish abbreviations in `segmenter.ts`.
- [ ] Wake word: parity of the real keyword head on a recorded clip
      (`voice-setup.md` §6.4, score delta ≤ 0.02); always-on Worker CPU in
      Chromium and Safari < 5% of one core; "Hey Friday, yarın saat 10'da
      diş hekimi" → spoken reply + task draft card, with no voice request in
      the network panel before the wake word; note Safari background-tab
      behaviour.
- [ ] **A8 HUD:** idle GPU < 5% (`/hud` idle 5 min, Activity Monitor → GPU
      history), frame time ≤ 2 ms at the 30 fps cap while speaking, the
      battery rule; macOS "Reduce motion" → static orb, states still
      distinct, VoiceOver announces state changes; iPhone Safari at phone
      width walks the eleven states; Safari WebGL2 orb (else canvas) and
      event-stream reconnect after sleep.
- [ ] **A3 manual refusal:** set a tiny session cap on an enabled priced
      row, trigger `429 voice.budget_exceeded` and see the
      `budget_exceeded` usage row.
- [ ] Preflight on the real stack from the host checkout with
      `VOICE_API_KEY` and `PREFLIGHT_VOICE_MUTE_CMD` (`voice-setup.md` §7);
      revoke the "e2e voice-mute browser" device it enrolls.
- [ ] Nightly (CI side, once the model exists): Playwright with Chromium
      `--use-file-for-fake-audio-capture` playing a "hey friday" fixture.

### C3. Phase 8A — two weeks of real learning (go/no-go)

- [ ] Prerequisites in `docs/runbooks/learning-memory.md` §1:
      `MEMORY_OWNER_GIT_EMAILS` in the server env, mapping rules (repo ids,
      `~/…` path prefixes, the calendar binding), scrub terms; enable the
      four sources (`git_commits`, harness outcomes, `claude_code_hooks`,
      calendar).
- [ ] Mint the `hook` PAT from the web UI, store it in the Keychain
      (service `friday-hook`), install `friday_hook.py` +
      `_scrubber_vendored.py` and the four hook entries in
      `~/.claude/settings.json` (`docs/runbooks/claude-code-hooks.md`
      §2–5). Manual pass: a session shows in `GET /memory/sessions` through
      start → permission prompt → stop → end; a pasted fake key never
      reaches the DB (§6).
- [ ] Runner: restart `make harness-runner` (beat
      `memory-ingest-git-commits`); clone credentials reach every enabled
      repo; after a day check the audit view (counts per source, commit
      `authorship` mix, unmapped keys); one real Google read stores
      owner-organised titles only (DB check).
- [ ] Write the `developer` block (`/memory/core-blocks`); ask "Sen kimsin,
      seni kim geliştiriyor?" with a real model.
- [ ] Full morning review on `/memory/learning` (approve, edit, reject one
      each), forget and restore one source, repeat at 375 px.
- [ ] `make preflight` (once with model calls) on the stack and the runner
      host: every `learning_*` line passes after the first night.
- [ ] Two weeks of use → record the acceptance rate (target ≥ 60%) in the
      8A "Go/no-go record". Below 60%: tune 8A before enabling 8B's
      transcript sources.

### C4. Phase 9 M4 — node measurements (after B1–B3)

- [ ] **TCC across rebuilds:** install build N signed with the Personal
      Team identity, grant Screen Recording; install build N+1 signed by
      the same Personal Team identity (same designated requirement as
      recorded in A1); the grant stays and titles are still returned.
- [ ] **Keychain half of spike S9.0:** the secret survives a reboot;
      rotation updates the Keychain item in place; deleting the item forces
      re-pair; `grep -r jnd_ ~/Library` finds nothing.
- [ ] **Kill-switch latency** measured (target ≤ 2 s).
- [ ] **Heartbeat and backoff** behave as specified over sleep/wake and a
      network change.
- [ ] **No TCP listener** (B1 test 4).
- [ ] `make preflight` with the Mac node paired: `node_gateway`,
      `node_signing_key`, `node_link:<Mac>`, `node_audit_chain`,
      `node_conformance` pass (`node_tls_pin` too once pinned).

### C5. Phase 8B M2 — private eval and the transcript week

Commands: `docs/runbooks/memory-eval.md` §4–§6.

- [ ] Write about 100 private cases under `~/friday-evals/` (0700); flag
      every case built from firm or personal data `sensitivity: private`.
- [ ] Eval database and baseline:

  ```bash
  createdb -h 127.0.0.1 -p 5433 -U jarvis friday_eval
  cd backend && export DJANGO_SETTINGS_MODULE=config.settings.eval
  uv run python manage.py migrate && uv run python manage.py seed_ai_defaults
  uv run python manage.py memory_eval baseline --set private
  uv run python manage.py memory_eval compare --set private --retrieval hybrid
  uv run python manage.py memory_eval compare --set private --compression chat   # only with the compressor up
  unset DJANGO_SETTINGS_MODULE
  uv run python manage.py memory_eval record ~/friday-evals/results/<run id>.json --owner <owner e-mail>
  ```

  Record each arm (`<id>-a.json`, `<id>-b.json`). Flip
  `MEMORY_RETRIEVAL_MODE=hybrid` only on a `pass`. Enable
  `AI_COMPRESSION_ENABLED=true` / `AI_COMPRESSION_PURPOSES=chat` only on a
  `pass` (≤ 1 point loss, no extra leak); for the compare, first
  `docker compose -f deploy/docker/compose.yml --profile compression build compressor`
  and `… --profile compression up -d compressor`, and make sure the eval
  DB's `learning_eval` budget covers `chat`.
- [ ] Transcripts (after B4 and A12): grant `~/.claude/projects` and
      `~/.codex/sessions` with Touch ID, enable both transcript sources, run
      one week; record episodes per day, `dropped_not_owner` (expect 0),
      scrubber hits by type, share of fact proposals accepted.
- [ ] Graphify (runner host, needs Docker): build the image
      (`cd backend/harness/docker && docker build -f graphify.Dockerfile -t friday-harness-graphify:0.9.103 .`),
      run `cd backend && HARNESS_GRAPHIFY_IMAGE_TEST=1 uv run pytest harness/tests/test_graphify.py -k real_image`
      (it checks `NetworkMode=none` and the fixture counts),
      enable `graphify_enabled` for the Friday repo
      (`PATCH /api/v1/harness/repos/<id>`), merge one branch to `main`,
      record the run's counts and duration; watch the 20 MB graph cap.
- [ ] Obsidian: move hand-edited mirror notes away before the first v2
      run; opt in to `obsidian_inbox` with `vault_prefix` rules
      (`00_Inbox/<folder>`); approve one `vault_export` against the real
      vault, edit one mirrored note and confirm the copy lands in
      `_conflicts/<date>/` next run.
- [ ] `/memory/graph`, `/memory/evals` and the sources page on a real phone
      and with VoiceOver.

### C6. Phase 10 M3 and M4 — ADR-014 evidence and control measurements (after B5–B6)

- [ ] **M3 evidence run:** run the 32 cases of
      `backend/nodes/conformance/escape/cases.json` through the Swift
      `SrtExecutor` in `worktree` mode:

  ```bash
  friday-node escape-suite --cases backend/nodes/conformance/escape/
  ```

  Record per case pass/fail, `srt` version, macOS version, date as an
  implementation note in ADR-014, and set `NODE_ESCAPE_EVIDENCE_RECORDED_ON`.
  Every case must fail to escape. Moving `worktree`-mode test/lint to
  confirm-class is a **separate** ADR-014 amendment the owner commissions;
  this session does not write it.
- [ ] **M4:** confirmation window latency invoke → shown ≤ 1 s (p95 over
      20 runs); kill switch and socket loss close an open window ≤ 2 s and
      stop a running command ≤ 6 s; Accessibility and Automation grants
      survive a rebuild signed by the same Personal Team identity; a real `pytest` of
      a registered repo under `srt` with network denied completes and
      streams, and a test that opens a socket fails.
- [ ] `make preflight` on the dev stack with the Mac node: every control
      row passes (`node_escape_suite` after M3).
- [ ] Trust check: sample `node_action` verdicts on real traffic for false
      blocks / false `ok`s; cut `friday_control_trust_check` v2 if needed
      (a new `PromptVersion`, never an edit).
- [ ] Optional Linux evidence (not ADR-014 evidence):
      `uv run pytest -m srt nodes/tests` and
      `manage.py node_escape_suite --executor srt` on a Linux box with
      `srt` and bubblewrap.

### C7. Phase 11 M2–M4 — speech server, nightly, wake and latency (after B7–B8, A8, A9)

- [ ] **M2 MLX Whisper:** install an OpenAI-compatible server serving MLX
      Whisper (record project, exact version and licence), add the speech
      row `voice-stt-mlx` (`personal_data_allowed=True`, cost 0), route
      `voice.stt` to it; Turkish WER spot check on ten recordings; p50/p95
      STT latency for ≤ 8 s utterances; fallback order MLX → speaches
      faster-whisper → whisper.cpp sidecar; `manage.py preflight`
      `voice_config` passes. Name the server in `live-voice.md` §2.
- [ ] **M3:** seven consecutive green nights of the launchd self-trigger
      (B8); set `VOICE_LIVE_SELFTEST_RESULT_PATH` on the server so the
      `voice_live_selftest` preflight row reads the line.
- [ ] **M4 FAR/FRR** (`docs/voice/live-eval.md` §2–§3):

  ```bash
  cd node/friday-voice
  uv run friday-voice wake-eval --negative ~/FridayEval/household --positives ~/FridayEval/positives \
    --model ~/FridayEval/hey_friday.onnx --out ~/FridayEval/report-$(date +%Y%m%d).json
  # on the server (the report holds no audio):
  manage.py voice_wake_eval record --from-report report.json --kind household --model-sha256 <sha256>
  manage.py voice_wake_eval record --from-report report.json --kind positives --model-sha256 <sha256>
  manage.py voice_wake_eval list
  ```

  Targets: FAR ≤ 0.2/h on the 24 h household set (in **one** report), FRR
  ≤ 10% on ≥ 50 positives. Only when `list` says `permitted`: pin the
  model (A8), set `VOICE_LIVE_ALWAYS_ON_ENABLED=true`, choose `always_on`
  in the node UI.
- [ ] **Latency:** end of owner speech → first audio byte over ≥ 50
      `VoiceTurnTrace` rows with `source=node`: p50 ≤ 1.5 s, p95 ≤ 2.5 s
      (`live-voice.md` §8 for the shell query).
- [ ] **Barge-in:** onset → playback stopped ≤ 200 ms p95 over 20 trials;
      no self barge-in across 20 replies at normal volume. Check the real
      `BARGE_IN_VAD` / guard values against the voice-processing AEC (a
      change is a code change in `friday_voice/config.py`).
- [ ] **CPU:** the child with always-on wake < 10% of one core at rest.
- [ ] **TCC:** the Microphone grant survives a rebuild signed by the same
      Personal Team identity.
- [ ] `manage.py preflight`: record the live-voice rows.

### C8. Phase 12 M1 — spoken digest and wake routes on the Mac (after B7)

Preconditions: a `morning_digest` schedule enabled on `/today` and at least
one digest produced; aliases `ev yedeği` and `sunucu durumu` on
`/settings/wake-routes` if n8n is live. Results note outside the repo; no
recordings in the repo or the database.

- [ ] "Hey Friday, günaydın" and "Friday, brifing" ten times each in a
      quiet room and five times with the TV on: route matched
      (`Message.route = digest.today`), first-audio p95 ≤ 2.5 s, whole
      digest spoken; ≥ 90% route match after STT (misses → owner aliases,
      not fuzzy matching).
- [ ] With one active SKILL.md skill, five ordinary voice questions: the
      added first-audio latency against the Phase 11 targets (the matcher
      is bounded by 600 ms).
- [ ] Interrupt a digest with "dur" three times: stops within the
      barge-in budget; the next utterance is handled normally.
- [ ] "Hey Friday, bildirimler": titles read; nothing marked read or
      decided.
- [ ] (n8n live) "Hey Friday, ev yedeğini başlat" → a draft card only;
      approve on screen; the completion notice is read back by
      "bildirimler". "Hey Friday, sunucu durumu" → the async template reply,
      then the follow-up in the live conversation.
- [ ] Contract unchanged: `backend/assistant/tests/test_wake_routes_node_voice.py`,
      `backend/ops/tests/test_reach_e2e.py`,
      `node/friday-voice/conformance.lock` and
      `manage.py build_node_conformance --check`.

### C9. Phase 12 — the two-week digest checkpoint

- [ ] Enable `morning_digest` on `/today` (time, days, optionally "HUD
      açılınca bir kez sesli oku"); the two weeks start that day.
- [ ] After two weeks:
      `docker compose -f deploy/docker/compose.yml exec web uv run python manage.py proactive_checkpoint`
      and record the go/no-go rows (the owner keeps the digest on).

---

## Part D — Owner-only steps that are not Swift work

These need the owner's accounts, money or judgement. Most do not need the
Mac. They are collected here from the same briefs.

- [ ] **D1 Reviews.** Every brief's pending "independent Sonnet 5 review"
      steps (plan reviews of the 8A, 9, 8B, 10, 11, 12 briefs; branch
      reviews whose verdict was not recorded at merge). The swarm recorded
      APPROVE for merged branches; anything still open is listed in each
      brief's branch lists.
- [ ] **D2 Confirmations asked of the owner.** The "Confirm the decisions
      above" steps of the 8B, 9, 10 and 11 branch lists (for example the
      pending-edit semantics, the retrieval-compare verdict rule, the
      `for_actor()` change, the Graphify mapping, D4's widening of personal
      reads to `node_voice`, D11's always-on gate, the smart-turn v3 licence
      and bundled sha256, the recorded `friday-voice` dependency
      deviations, the Phase 12 defaults).
- [ ] **D3 ADR-016.** Review it and move it from Proposed to Accepted; then
      regenerate the `self` snapshot:
      `cd backend && uv run python manage.py build_self_sources`.
- [ ] **D4 Merge to `main`.** After the integration branch's review and a
      green CI, merge `claude/inspiring-franklin-pw7avo` to `main`; close
      the former repository's PR #29 with a pointer to the Phase 5 brief
      §10. Then the history rewrite and the move described in
      `.swarm/README.md` and `docs/runbooks/rename-jarvis-to-friday.md`.
- [ ] **D5 n8n (Phase 12 owner steps 3–4).** Run n8n where `n8n-relay`
      reaches it, import `deploy/n8n/friday-tools.workflow.json`, create the
      trigger's bearer credential and put it only in `deploy/docker/.env`
      as `RELAY_BEARER_TOKEN`, set `RELAY_UPSTREAM_URL`,
      `N8N_RELAY_ALLOWED_TOOLS` (print it with `manage.py n8n_relay_env`;
      replace any old `BRIDGE_ALLOWED_TOOLS=` line) and `N8N_MCP_URL`;
      re-pin the tools against the live trigger in a reviewed commit with a
      manifest bump; enable the `n8n` connector; `n8n_allowlist`,
      `n8n_relay`, `n8n_pins` must pass; record n8n's licence terms in
      `docs/mcp/n8n.md`.
- [ ] **D6 Harness drivers (Linux runner, not the Mac).** After A5's d4
      terms check: pin the real Codex CLI and `claude-agent-sdk` versions,
      build the images, run
      `backend/harness/docker/generate_driver_schemas.sh codex <image>` and
      `… claude <image>`, then
      `uv run pytest -q harness/tests/test_driver_contract.py harness/tests/test_claude_driver_contract.py`
      on a dedicated upgrade branch; smoke-test sandbox/approval behaviour on
      a disposable repo; only then enable the `codex_cli` slot /
      `HARNESS_DRIVER_CLAUDE=agent_sdk`. Decide whether Codex runs need a
      priced cost estimate. Check that Claude Code in `harness-claude-code`
      loads `~/.claude/skills/<name>/SKILL.md` from the read-only mount.
- [ ] **D7 Skills and playbooks.** Write or upload the first SKILL.md
      skills on `/skills`; decide curator and playbook proposals as they
      arrive (after three real approved jobs on one repo).
- [ ] **D8 Optional connector opt-in.** Add `node_voice` to the Google
      manifest's `read_channels` through MCP governance only if spoken
      calendar reads are wanted (mail reads become speakable too —
      ADR-015 decision 7's accepted risk).
- [ ] **D9 Optional follow-ups recorded in the briefs.**
      `friday_router_intent` v2 naming `node_action`; move the self-trigger
      regression onto the replay clip when Phase 6 branch 3a merges.

---

## Closing the Mac session

- [ ] Every B branch merged after its review reached APPROVE, with
      `swift test` green on the Mac and the server suites green
      (`cd backend && uv run pytest -q`).
- [ ] Every C result recorded in its brief's go/no-go record.
- [ ] `docs/MASTER_PLAN.md` §AA: each Friday 3.0 row's status moves from
      "server half done … pending owner evaluation" to done, or to a
      recorded re-scope; regenerate the `self` snapshot
      (`uv run python manage.py build_self_sources`) in the same branch.
- [ ] This file: tick the boxes in a final docs branch, or replace the open
      items with a dated "remaining" list.

## Owner steps (pending) — `docs/mac-session-handoff`, recorded 2026-11-20

1. Run the independent Sonnet 5 review of this branch (CLAUDE.md
   "Reviews") and fix findings until APPROVE.
2. Start the Mac session with this file as its task list (Parts A → B →
   C), starting with A1, the Personal Team signing spike (d9 revised
   2026-11-25; no paid enrolment unless the spike falls back to A1-F).
3. Close the three server-side gaps found while writing this handoff
   (A10 `make voice-models --node` has no implementation, A11 Phase 6
   branch 3a, A12 the `nodes`-backed transcript port). None needs the Mac;
   each is its own branch.

Nothing in this branch needs the Mac, real credentials, paid calls or real
hardware: it is documentation only.
