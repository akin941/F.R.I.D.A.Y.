# Runbook — live voice on the Mac (Phase 11)

Natural, interruptible voice through the Friday Node: push-to-talk on a
global hotkey now, always-on "Hey Friday" once the false-accept gate opens.
Voice stays a transport (ADR-011): every spoken turn is an ordinary chat
turn on the node voice channel, capped by ADR-015 (reads and proposals
only); every draft is a card clicked on screen.

Sources: `docs/prompts/phase-11-live-voice/brief.md` (D1–D16, "Mac session
(deferred)", "Go/no-go record"), ADR-011 (amendment 2026-11), ADR-013 (the
voice child, `voice.state`), ADR-015 (decisions 4–6, d3),
`docs/voice/live-eval.md` (measurements), `docs/runbooks/voice-setup.md`
(the speech server), `docs/runbooks/friday-node.md` (pairing, kill switch).

## What runs where

| Piece | Where | Holds |
|---|---|---|
| Swift voice host (`friday-node`, M1) | the Mac, signed app | the microphone (TCC), AVAudioEngine voice processing (AEC), playback, the menu-bar mic icon, hotkey, mute, the node secret (Keychain) |
| `friday-voice run` (the child) | the Mac, `~/Library/Application Support/Friday/voice/` | a 15-minute `node_voice` token in memory; no provider key, no listening socket, no audio on disk |
| Speech server (MLX Whisper / speaches + Piper, M2) | the Mac, host process | STT/TTS models; reached only through Friday's gateway |
| Friday (Django) | the server | STT/TTS through the gateway (`ModelUsage` per call), the chat turn, the always-on gate, the storm backstop, traces, the HUD |

The child talks to the host over stdio only (`friday-voice/v1`) and to
Friday over HTTPS only: the live-session, transcription, chat-turn, speech
and voice-policy endpoints, nothing else.

## 1. Install the child (Mac)

```bash
make friday-voice-install          # uv sync --frozen into ~/Library/Application Support/Friday/voice
make voice-models-node            # the pinned openWakeWord graphs, Silero VAD, hey_friday.onnx
                                  # (VOICE_WAKE_MODEL_SRC=/path/to/hey_friday.onnx once trained and pinned)
```

`make friday-voice-install` prints the installed `uv.lock` sha256; the host
refuses to launch a child whose lock hash differs from the one compiled
into it. The child is not inside the signed bundle; Piper is never in
either (GPL-3.0) — it stays the speech server's own executable.

### 1a. A local server behind a self-signed certificate (the install's CA file)

The child verifies an `https://` Friday against the public CA bundle and
then checks the credential's `ca_sha256` pin (the DER sha256 of the top
certificate of the chain the host saw on its pinned connection) before any
request byte is sent. A server on this Mac behind the loopback Caddy of
`deploy/docker/compose.local-tls.yml` uses the self-signed certificate
`deploy/docker/gen-local-tls.sh` writes to `deploy/docker/local-tls/cert.pem`,
which no public bundle holds, so the handshake is refused until the install
records that certificate as its CA file:

```bash
make friday-voice-install FRIDAY_VOICE_CA_FILE=deploy/docker/local-tls/cert.pem
# or, on an existing install, without reinstalling:
make friday-voice-ca FRIDAY_VOICE_CA_FILE=deploy/docker/local-tls/cert.pem
make friday-voice-ca                              # show what is recorded
make friday-voice-ca FRIDAY_VOICE_CA_FILE=none    # back to the public bundle
```

`friday-voice ca set` copies the certificate into the install
(`server-ca.pem`, so the child never reads the checkout, which may sit in a
TCC-protected folder such as `~/Desktop`), records it in `tls.json` and
prints each certificate's DER sha256 — for the gen-local-tls certificate
that is the value the host must send as `ca_sha256`. Both files survive a
reinstall. A new key (`gen-local-tls.sh --force`) means re-pairing and
running `make friday-voice-ca` again.

The CA file **replaces** the public bundle for this install; it relaxes
nothing. Certificate and hostname verification stay on, the pin is still
checked on every connection (a TLS connection without a pin is refused),
and there is no option that turns either off. Only the install sets it —
no protocol frame, environment variable or server reply can. A `tls.json`
that cannot be read, or a recorded CA file that is missing, unreadable or
holds no certificate, fails closed: the child logs
`tls.config_invalid` / `tls.ca_file_unreadable` at start-up and every
request fails with `tls_ca_file_unreadable` (error earcon, mic released);
it never falls back to the public bundle.

`Caddyfile.local` forwards the node voice flow and nothing else: the host's
`POST /api/v1/auth/nodes/voice-token` and the six views a `node_voice` token
reaches (D4, the set preflight `voice_live_channel` checks) —
`/api/v1/auth/nodes/voice-policy`, `/api/v1/voice/live/sessions`,
`/api/v1/voice/transcriptions`, `/api/v1/voice/speech`,
`/api/v1/voice/receipt` and `/api/v1/ai/conversations/<uuid>/messages` —
beside the node's pairing, rotation and `/ws/node/v1` paths. Every other
path is 404 (`backend/ops/tests/test_local_tls_caddyfile.py`). A running
proxy picks the list up only when the `local-tls` container is recreated
(`make up-local-tls`).

Check the install with the reference implementations before the host:

```bash
cd node/friday-voice
uv run friday-voice conformance --check   # case/vector/schema files and conformance.lock current
uv run friday-voice conformance           # both families pass against the reference host/child
uv run friday-voice selftest --ci         # the self-trigger release gate (fakes)
```

## 2. The speech server with MLX Whisper (M2)

Follow `voice-setup.md` §1 for the OpenAI-compatible speech server; on the
Mac the STT engine is MLX Whisper (fallback order: MLX → speaches
faster-whisper → whisper.cpp sidecar). Add it as a speech row
(`voice-stt-mlx`, `personal_data_allowed=True`, cost 0) and route
`voice.stt` to it; `manage.py preflight` → `voice_config` must pass (the
pinned STT/TTS round trip). The chosen server, its version and licence are
recorded in the brief's go/no-go record (pending owner evaluation).

## 3. Pairing, the voice credential and push-to-talk

1. Pair the node (`friday-node.md`, "Pairing a node").
2. The host mints the voice token with the node secret
   (`POST /api/v1/auth/nodes/voice-token`, `Authorization: Bearer jnd_…`):
   `201 {token, token_id, expires_at, previous_valid_until}`, TTL
   `VOICE_LIVE_TOKEN_TTL_SECONDS` (900; refused at boot above 3 600),
   re-minted ≤ 60 s before expiry; the superseded token keeps 30 s of grace.
   A `node_voice` token is never mintable through `/auth/tokens`.
3. Set the mic mode in the node UI: **off**, **push-to-talk** (hotkey) or
   **always-on**. The server can only narrow it, never widen it.
4. Hold the hotkey, speak, release: the child opens a live session
   (`opened_via=node_voice`, reused for 30 minutes), uploads the utterance,
   posts the transcript as a voice turn and speaks the reply sentence by
   sentence. A draft adds "Kart ekranda." — the card is on `/chat/<id>`.
   Pressing the hotkey while Friday speaks cuts the reply (barge-in).

The token reaches exactly six surfaces (D4): the chat turn of a live
conversation, transcriptions, speech, the receipt, live sessions and the
voice-policy read. Approvals, tasks, memory writes, nodes, harness,
deploys, connector writes and the facade refuse it (`voice_live_channel`
preflight row; `config/tests/test_node_voice_surfaces.py`).

## 4. The always-on gate (D11, roadmap d3)

Always-on arms only when **all** hold:
- the node UI chose always-on (LocalAuthentication on the Mac);
- every wake model file matches its pin (`hey_friday.onnx` included);
- `VOICE_LIVE_ALWAYS_ON_ENABLED=true` on the server;
- for the loaded model's sha256, the **latest** `household` evaluation
  (≥ 24 h, FAR ≤ `VOICE_LIVE_MAX_FAR_PER_HOUR` = 0.2/h) and the latest
  `positives` evaluation (FRR ≤ `VOICE_LIVE_MAX_FRR` = 10 %) pass at the
  threshold and refractory period the child runs at.

Record the evaluations as `docs/voice/live-eval.md` describes
(`friday-voice wake-eval` → `manage.py voice_wake_eval record
--from-report`). Then, and only when `/voice` → "Canlı ses (Mac)" shows
`permitted`, set `VOICE_LIVE_ALWAYS_ON_ENABLED=true` and restart `web`.

The server's backstop: a transcription or live session with
`X-Friday-Voice-Trigger: wake` is refused 403
`voice.always_on_not_permitted` (before any model call) while no model's
latest pair passes.

## 5. Storms and the release button (D12)

- **Child side.** More than `WAKE_STORM_MAX` (3) wakes in `WAKE_STORM_WINDOW_S` (60 s) →
  the child mutes itself (`storm_muted`), releases the mic and shows it on
  the menu-bar icon until a mute on → off cycle.
- **Server side.** More than `VOICE_LIVE_STORM_MAX_STT_PER_MINUTE` (8)
  transcriptions from one node device in 60 s engages
  `accounts.VoiceLiveMute` (audited): every node voice route answers 423
  `voice.live_muted` — the chat turn included.
- **Release** only on screen: `/voice` → "Canlı ses (Mac)" → the release button on the storm banner
  (`DELETE /api/v1/auth/nodes/{device_id}/voice-mute`, web session, owner).
  Then toggle mute on → off on the Mac; the next turn works.
- `manage.py preflight` shows an engaged mute as `voice_live_storm:<node>`
  (`warn`, with its age).

## 6. Fail closed

The mic is released (icon `off` / `muted` / `error`) on: node WebSocket
loss, mute on or unreadable, mode off, the child's exit or watchdog, a
refused credential (401 → `release_mic(credential_refused)`, the host
re-mints), a storm, and the **kill switch** (`friday-node.md`, "Kill
switch"): every `node_voice` token of the owner is revoked at once, the
next request is refused, the gateway closes the socket, and the HUD's mic
state reads `off`.

## 7. The nightly self-trigger (M3)

`friday-voice selftest --nightly` with the real model, speakers and AEC,
from the launchd agent in `docs/voice/live-eval.md` §4. Point the server's
`VOICE_LIVE_SELFTEST_RESULT_PATH` at the result line (synced to the server
host or the same host). Seven consecutive green nights are the go/no-go
evidence.

## 8. Reading traces

Every live turn writes a `voice.VoiceTurnTrace` row with `source=node`
(set by the server from the channel): `stt_ms`, `tts_first_ms` (server),
`end_of_turn_ms`, `llm_first_sentence_ms`, `playback_start_ms`, `total_ms`,
`wake_to_listen_ms` and `interrupted_at_ms` (client-reported with the
speech requests). No text column. Latency p50/p95 over ≥ 50 turns:

```python
# manage.py shell
from voice.models import VoiceTurnTrace
rows = VoiceTurnTrace.objects.filter(source="node").exclude(playback_start_ms=None)
values = sorted(r.playback_start_ms for r in rows)
print(len(values), values[len(values) // 2], values[int(len(values) * 0.95)])
```

Targets (M4): local p50 ≤ 1.5 s, p95 ≤ 2.5 s end of speech → first audio.

## 9. Preflight live-voice checks

`manage.py preflight` (`ops/preflight.py` `check_live_voice`; read-only,
no model call):

| Row | Pass | Warn | Fail |
|---|---|---|---|
| `voice_live_channel` | `node_voice` reaches exactly the D4 views; TTL ≤ 3 600 s | — | a view outside D4 accepts it, a D4 view lost it, the kind is mintable or mapped to another channel, TTL out of range |
| `voice_live_policy` | a model's latest household + positives pair passes | no pair passes and always-on is off (pending owner evaluation) | always-on enabled and no pair passes |
| `voice_live_storm` | no mute engaged | `voice_live_storm:<node>`: a mute engaged (age shown) | — |
| `voice_live_selftest` | a green nightly line < 48 h old | not configured, no line yet, stale, or not a nightly run (Mac pending) | the last run failed or errored; an unreadable line |
| `friday_voice_conformance` | the case, vector and schema files match `node/friday-voice/conformance.lock` | no `node/friday-voice` here (the backend image) | drift, a missing or malformed lock |
| `node_voice_state` | `node_voice_state:<node>`: the node reports `voice.state` | no node paired, or none reports (Mac pending) | — |

On the fake dev stack the expected result is: everything green apart from
`voice_live_policy`, `voice_live_selftest` and `node_voice_state`, which
warn until the Mac session (M1–M4) and the owner's evaluations exist. The
HUD's health panel lists failing check names only.

## 10. The e2e (CI)

`make friday-voice-e2e` (CI: the last step of the `friday-voice` job) runs
`backend/ops/tests/test_live_voice_e2e.py`: this checkout's Django on a
loopback uvicorn (FakeAdapter, the fake speech provider), the reference
host and a real `friday-voice run` child over real HTTP — push-to-talk
spoken back sentence by sentence, a node action as a card only, barge-in,
the self-trigger clip at AEC residual 1.0, the kill switch and a storm.
The Swift host must produce the same frames, states and server rows (M1
contract test 4).

## Troubleshooting

| Symptom | Look at |
|---|---|
| Icon `error` after every turn | the host's local log (codes only): `friday.error`, `chat.error`, `turn.failed`; `voice_config` preflight |
| Always-on stays push-to-talk | `/voice` → "Canlı ses (Mac)" reasons (`disabled`, `no_household_eval`, `threshold_mismatch`, `model_mismatch`, `storm_muted`); a model file not matching its pin (`wake.models_not_armable`) |
| 423 on every request | an engaged storm mute: release on `/voice`, then mute on → off on the Mac |
| 401 then `off` | the token expired or was revoked (kill switch, node revoked): release the kill switch / re-pair; the host re-mints |
| HUD mic state `unknown` | no `voice.state` for two heartbeats (node offline, a v1 session, or the child not running) |
| `friday_voice_conformance` fails | run `uv run friday-voice conformance --check` in `node/friday-voice`; rebuild with `--build` only for an intended definition change |
