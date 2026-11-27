# Live voice evaluation — FAR/FRR, the always-on gate and the nightly self-trigger

Phase 11 brief D11 and D13 (`docs/prompts/phase-11-live-voice/brief.md`),
roadmap d3, ADR-015 decision 4. Always-on wake mode on the Mac stays **off**
until the server holds a passing measurement of the exact wake model the
child loads, at the exact threshold and refractory period it runs at. This
page is how the owner produces that measurement and keeps the self-trigger
guard green.

Nothing here runs in GitHub CI except the fake self-trigger gate
(`friday-voice selftest --ci`). Every step that needs real audio, real
models or the Mac is an owner step.

## What gets measured

| Measurement | Input (owner, outside the repo) | Budget | Gate row (`accounts.VoiceWakeEvaluation`) |
|---|---|---|---|
| False accepts per hour (FAR) | ≥ 24 h of ordinary household audio: TV, music, cooking, talk, silence — **without** anyone saying "Hey Friday" | ≤ 0.2 / h (`VOICE_LIVE_MAX_FAR_PER_HOUR`) | `household` (needs `hours ≥ 24`) |
| False rejects (FRR) | ≥ 50 recordings of the owner saying "Hey Friday", one per file, across rooms, distances and volumes | ≤ 10 % (`VOICE_LIVE_MAX_FRR`) | `positives` |
| (optional) negative corpus | any other negative set | informational | `negative_set` (shown, never judged) |

The gate judges only the **latest** `household` and `positives` row of a
model, and only when both were measured at the threshold and refractory
period the child sends (`friday_voice.config.WAKE_THRESHOLD` = 0.5,
`WAKE_REFRACTORY_MS` = 2000). Retraining `hey_friday`, changing either
number or recording a worse re-measurement re-closes the gate.

## 1. Record the audio (outside the repository)

- Record on the Mac's own microphone, in the rooms where the node listens.
  Any PCM WAV works (16 kHz mono is read as-is; other rates and stereo are
  normalised the way the child normalises audio).
- Keep the recordings **outside the repository and outside Friday's
  database** (brief "Out of scope"), e.g. `~/FridayEval/household/` and
  `~/FridayEval/positives/`. Never commit them, never upload them to
  Friday. The harness reads them and writes only numbers and hashes.
- Household: several files are fine (e.g. one per hour); the harness sums
  their lengths. The same recording twice is refused (identical sha256).
- Positives: exactly one "Hey Friday" per file, with a little silence
  before it.

## 2. Run the harness

On the Mac, in the installed child (`make friday-voice-install`) or a dev
checkout of `node/friday-voice`, with the node model directory holding the
pinned melspectrogram and embedding graphs (`make voice-models-node`):

```bash
uv run friday-voice wake-eval \
  --negative ~/FridayEval/household \
  --positives ~/FridayEval/positives \
  --model ~/FridayEval/hey_friday.onnx \
  --out ~/FridayEval/report-$(date +%Y%m%d).json
```

- `--model` is the keyword head being evaluated (it need not be pinned
  yet: measure first, pin after). The feature graphs are loaded from
  `--model-dir` (default: the node's model directory) and refused unless
  they match their pins in `node/friday-voice/models.json`.
- `--threshold` (fraction, at most 3 decimals) and `--refractory-ms`
  default to the child's own values; measure at those unless you also
  intend to change `friday_voice/config.py`.
- The harness streams every file through the real openWakeWord detector in
  the pipeline's 20 ms frames behind the child's own `WakeGate` (same
  threshold, same refractory period, in audio time). No server, no network.
- The report holds, per file, only its content sha256, its length in ms
  and its detection count — **no file name, no path, no audio, no
  transcript** — plus `hours`, `false_accepts`, `far_per_hour`,
  `positives`, `false_rejects`, `frr`, each section's `manifest_sha256`
  and a `report_sha256` over everything. The last stderr line summarises
  it.

A 24 h run takes a while on CPU; it can run in pieces (one report per
directory) and each piece is recorded separately — but only the latest
household row counts, so the 24 h must be in **one** report.

## 3. Record the report on the server

Copy the report to the server (it holds no audio) and, in the owner shell:

```bash
manage.py voice_wake_eval record --from-report report.json --kind household --model-sha256 <sha256 of hey_friday.onnx>
manage.py voice_wake_eval record --from-report report.json --kind positives --model-sha256 <sha256 of hey_friday.onnx>
manage.py voice_wake_eval list
```

`--from-report` writes exactly one row per call and refuses a report whose
hashes do not match (a number edited by hand), whose model is not the one
named, or whose numbers are inconsistent (hours not the files' total, FAR
not false accepts / hours, FRR not false rejects / positives, a duplicate
recording, any unexpected field). Every row is audited
(`accounts.voice_wake_eval.recorded`) and append-only.

`list` prints the verdict per model. When it says `permitted`:

1. pin the model's sha256 in `node/friday-voice/models.json` (`keyword`)
   and in `frontend/src/lib/voice/model-manifest.json` (a reviewed change);
2. set `VOICE_LIVE_ALWAYS_ON_ENABLED=true` on the server;
3. choose `always_on` in the node's own UI. The child re-reads the policy
   at session start and every 10 minutes; `/voice` → "Canlı ses (Mac)"
   shows the verdict and its reasons.

Re-measure after retraining, after moving the Mac to a new room layout,
and monthly (brief R2).

## 4. The self-trigger test

**CI (release gate, every push).** `uv run friday-voice selftest --ci`
plays Friday's own "Hey Friday, terminali aç" (a synthetic clip from the
fake TTS) through the reference host's echo double at AEC residual 0.0, 0.3
and 1.0 with a wake detector scripted to fire on it, and fails on any
posted echo turn, draft or spoken reply; a negative control with the
self-trigger filter off must post the turn. The backend side
(`backend/tools/tests/test_selftrigger_node_voice.py`) drives the same
transcript through the node voice channel's chat turn: at most a
`node_action` draft, nothing executed.

**Nightly on the Mac (Mac session M3, pending owner evaluation).** The
same scenario with the real pieces:

```bash
friday-voice selftest --nightly \
  --server https://friday.example.internal \
  --token-file "$HOME/Library/Application Support/Friday/voice/selftest.token" \
  --model-dir "$HOME/Library/Application Support/Friday/voice/models" \
  --loopback-cmd "/Applications/Friday Node.app/Contents/MacOS/friday-node voice-loopback" \
  --result-file "$HOME/Library/Application Support/Friday/voice/selftest-result.json"
```

- The clip is synthesised by Friday's real TTS (through the gateway, one
  `ModelUsage` row), played on the speakers and captured through the host's
  voice-processing AEC by `--loopback-cmd` (a command the Swift host
  provides: WAV on stdin → AEC-cleaned capture WAV on stdout), scored by
  the pinned `hey_friday` model behind the child's `WakeGate`. Every
  detection is transcribed and put through the self-trigger filter; the
  selftest never posts a chat turn itself.
- Exit 0 only with **no wake and no posted turn**; 1 when the gate bit; 2
  when it could not run (missing `--server`, token, model directory or
  loopback; a server, model or audio error). A run that cannot judge is
  never green.
- `--result-file` gets one JSON line, replaced atomically:
  `{"schema":"friday-voice/selftest/v1","mode":"nightly","result":"pass|fail|error","at":"…Z","wakes":0,"posted_turns":0,"model_sha256":"…","threshold":500,"refractory_ms":2000,"reason":""}`.
  Point the server's `VOICE_LIVE_SELFTEST_RESULT_PATH` at it (read by the
  `voice_live_selftest` preflight check, branch 8: pass when green and
  < 48 h old, warn when missing, fail when the last run failed).
- The token file holds a `node_voice` token (minted by the host with the
  node secret, 15-minute TTL): the launchd job asks the host to mint one
  right before the run (Mac session M3).

### The launchd job (Mac)

`~/Library/LaunchAgents/ai.friday.voice-selftest.plist`, nightly at 03:17
when on power:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>ai.friday.voice-selftest</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/sh</string><string>-c</string>
    <string>"$HOME/Library/Application Support/Friday/voice/selftest-nightly.sh"</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>3</integer><key>Minute</key><integer>17</integer></dict>
  <key>StandardErrorPath</key><string>/tmp/friday-voice-selftest.log</string>
</dict>
</plist>
```

`selftest-nightly.sh` (written in Mac session M3) skips the run on battery,
asks the host for a fresh voice token, runs the command above and leaves
the result line in place. Load it with `launchctl load -w …plist`. Seven
consecutive green nights are the go/no-go evidence (brief M3).

## What never happens

- No audio, file name or transcript is written to the report, the result
  line, the database or a log.
- The harness and the selftest make no model call of their own: detectors
  are on-device classifiers (D2); the nightly TTS/STT go through Friday's
  gateway.
- Real models never run in GitHub CI; the opt-in `-m real_models` test
  runs the real detector with the public stand-in `hey_jarvis` head
  (`FRIDAY_VOICE_MODEL_DIR`, `FRIDAY_VOICE_STANDIN_HEAD`).
