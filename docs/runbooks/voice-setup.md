# Runbook — local voice (speech server, models, env, verification)

Phase 6 branch 1 (`feat/p6-speech-gateway`, ADR-011). Speech-to-text
(faster-whisper) and text-to-speech (Piper) run **on the owner's Mac, next
to Ollama**, behind one OpenAI-compatible HTTP server. The backend imports
no speech library; it talks to the server through the gateway's
`openai_compatible` adapter (`/v1/audio/transcriptions`,
`/v1/audio/speech`). Audio is never written to disk by Friday.

Later branches extend this runbook: wake-word models (`make voice-models`,
branch 4) and recording/running the Turkish evaluation set (branch 3a).

## 1. Install and run the speech server (host, not Docker)

Preferred server: **speaches** (formerly `faster-whisper-server`), which
serves faster-whisper for STT and Piper (and Kokoro) for TTS. Confirm the
current release, licence and whether Piper voices are still served before
installing (brief, step 0 spike — owner step).

```bash
# On the Mac (not in the Django image). Follow the speaches README for the
# current install command; it is a Python app run with uvicorn.
# Listen on :8000 so the default VOICE_BASE_URL works from Docker Desktop.
```

Pull the models through the server (speaches downloads from Hugging Face on
first use or via its model API):

- STT: a faster-whisper model, e.g. `Systran/faster-whisper-small`
  (`medium` for better Turkish WER at higher latency).
- TTS: a Turkish Piper voice, e.g. `tr_TR-dfki-medium`, and an English one,
  e.g. `en_US-lessac-medium`.

Exact model ids as the server names them (e.g. whether the TTS model is
`speaches-ai/piper-tr_TR-dfki-medium` with voice `tr_TR-dfki-medium`) are a
spike result — record them in the brief and set the env below to match.

**Fallback** if speaches no longer serves Piper or misbehaves: the
`deploy/voice/` FastAPI sidecar (≈150 lines wrapping `faster-whisper` and
`piper-tts`, same two endpoints) — built only if the spike says so.

### Licences

- faster-whisper / CTranslate2: MIT. Whisper weights: MIT (OpenAI).
- Piper: MIT (engine); each voice has its own licence on its model card
  (`tr_TR-dfki-medium` is derived from the DFKI dataset — check the card).
- openWakeWord pre-trained models (branch 4): non-commercial — downloaded,
  never committed.

## 2. Environment (`.env`)

```bash
VOICE_API_KEY=voice-local          # placeholder opt-in, not a secret
VOICE_BASE_URL=http://host.docker.internal:8000/v1
VOICE_STT_MODEL_ID=Systran/faster-whisper-small
VOICE_TTS_MODEL_ID=<the server's Piper model id>
VOICE_STT_LANGUAGE=tr              # "" = auto-detect (unreliable on short clips)
VOICE_TTS_ROUTE_TR_VOICE=tr_TR-dfki-medium
VOICE_TTS_ROUTE_EN_VOICE=en_US-lessac-medium
```

Then reseed (idempotent): `make up` runs `seed_ai_defaults`, or
`uv run python manage.py seed_ai_defaults` from `backend/`. With
`VOICE_API_KEY` set it creates `voice-stt-local` and `voice-tts-local`
(`personal_data_allowed=True`, cost 0). Existing rows are never overwritten:
to change a model id later, edit the row in the admin.

### Language routing (A2)

`settings.VOICE_TTS_ROUTES` maps a language to a provider row and voice
(`tr` and `en` → `voice-tts-local` by default). The routed provider is used
only if it is active, keyed and `personal_data_allowed`; otherwise the
gateway falls back to priority order among eligible rows. The client never
chooses the provider.

### Cloud audio (OpenRouter, decision d2) — off by default

`openrouter-stt` and `openrouter-tts` are always seeded **disabled** and
**not personal-data-allowed**. To use one (e.g. Turkish TTS after the Piper
listening test scores below 4/5):

```bash
# .env: OPENROUTER_API_KEY=<owner key>; optionally VOICE_TTS_ROUTE_TR_PROVIDER=openrouter-tts
uv run python manage.py set_provider_personal_data openrouter-tts --allow --activate
# and to turn it off again:
uv run python manage.py set_provider_personal_data openrouter-tts --deny --deactivate
```

Both flips are audited (`ai_core.provider_personal_data_set`). OpenRouter's
`/audio/speech` returns raw PCM (or MP3), never WAV; the adapter wraps PCM
into WAV using the rate in the `Content-Type`. It returns no cost, so the
usage row carries the configured price and a Celery task
(`ai_core.tasks.correct_usage_cost`) replaces it from OpenRouter's
generation lookup. Reply **text** (not mic audio) leaves the device when TTS
is routed there; STT stays local unless the STT row is also enabled.

### Budgets (A3)

The `audio` `AIBudget` row (admin → Ai core → AI budgets) caps
`voice.stt`/`voice.tts` spend: monthly 20 USD and 1 USD per voice session by
default (`AI_BUDGET_AUDIO_*` seed values). Local providers cost 0 and never
trip it. A refused call is a `ModelUsage` row with
`error_type="budget_exceeded"`.

## 3. Verify through the gateway (`manage.py shell`)

```python
import io, wave
from ai_core.gateway import InferenceGateway
from workspaces.models import Workspace

ws = Workspace.objects.filter(type="personal").first()  # the owner's workspace
gw = InferenceGateway()

speech = gw.synthesize("voice.tts", "Merhaba, ben Friday.", params={"language": "tr"}, workspace=ws)
print(speech.content_type, speech.audio_seconds, speech.usage.provider)

# STT needs 16 kHz mono PCM16; resample the TTS output first if needed, or
# record one with: sox -d -r 16000 -c 1 -b 16 utterance.wav trim 0 4
wav = open("utterance.wav", "rb").read()
result = gw.transcribe("voice.stt", wav, params={"language": "tr"}, workspace=ws)
print(result.text, result.audio_seconds, result.usage.routing_reason)
```

Done when (branch 1, owner step): a Turkish sentence round-trips with one
`ModelUsage` row per call (`ModelUsage.objects.filter(purpose__startswith="voice.")`),
`routing_reason` names the local provider and `estimated_cost` is 0.

Troubleshooting: `NoProviderAvailable` with `routing_reason=
"personal_data_required"` means no active, keyed, personal-data-allowed
STT/TTS row exists (is `VOICE_API_KEY` set in the backend's environment and
was the seed rerun?). `invalid_audio` in the usage row's attempts means the
input was not 16 kHz mono PCM16 WAV, or the server's output was not a valid
PCM16 WAV within `VOICE_TTS_MAX_SECONDS`.

## 4. The voice API (Phase 6 branch 2)

Two web-only endpoints (owner, trusted device session; a PAT gets 403):

- `POST /api/v1/voice/transcriptions` — multipart `audio` (16 kHz mono PCM16
  WAV, at most `VOICE_MAX_UTTERANCE_SECONDS`) + `conversation` →
  `{text, audio_seconds, language}`. The upload is held in request memory only
  (no temp-file handler is installed). Errors: 413 `voice.upload_too_large`,
  415 `voice.unsupported_audio`, 503 `voice.no_local_provider`, 502
  `voice.stt_failed`, 429 `voice.budget_exceeded` (or `throttled`).
- `POST /api/v1/voice/speech` — JSON `{conversation, text (≤ VOICE_TTS_MAX_CHARS),
  language?, timings?}` → `audio/wav`. Same error codes with `voice.tts_failed`.

The browser goes through the BFF (`/api/voice/transcriptions`,
`/api/voice/speech`). The transcript is sent on as an ordinary chat message
with `input_mode: "voice"`; a voice turn can read and propose only (ADR-015).
An optional `X-Friday-Trace: <uuid>` on all three requests ties the turn's
`ModelUsage` rows together and fills one `voice.VoiceTurnTrace` row (timings
only, pruned after `VOICE_TRACE_RETENTION_DAYS`). The per-session audio budget
is keyed on the conversation (`voice:<conversation id>`), never on a request
field.

Quick check from the Mac once the stack runs (replace the ids; the access
token is the web session's — normally the browser does this):

```bash
curl -s -X POST http://localhost:3001/api/voice/transcriptions \
  -H "Origin: http://localhost:3001" -b cookies.txt \
  -F conversation=<conversation-uuid> -F audio=@utterance.wav
```

## 5. Push-to-talk on `/voice` (Phase 6 branch 3)

Open **Sesli** in the sidebar (needs HTTPS or `localhost` for the mic; on the
LAN use the Phase 4 Tailscale/TLS recipe). Then:

- **Hold** the mic button (or hold **Space** outside a text field) while you
  speak and let go; or **click** once to start and once to stop. `Escape` /
  **Durdur** stops listening, the reply stream's audio and every pending
  synthesis request.
- After you let go Friday waits `NEXT_PUBLIC_VOICE_FINISH_MS` (900 ms): press
  again within it and the next piece is **appended** to the same turn.
- **Göndermeden önce göster** (per device) shows the transcript for editing
  before it is sent.
- Deterministic voice routes run before the brain: `dur` / `bekle` / `stop`
  (stop audio, send nothing), `sessize al` (mute), `hatırla ki …` (a memory
  **proposal** — `pending_approval`, approve it on **Hafıza**), `bu ne kadar
  tuttu?` (the previous spoken turn's `ModelUsage` receipt via
  `GET /api/v1/voice/receipt`, plus today's workspace total). A spoken
  `evet` / `onayla` confirms nothing: decisions are made on screen.
- Task and tool-request drafts from a voice turn are the same cards as chat;
  only a click creates the task / approval request, and only then does Friday
  say so (a confirmation needs the created row's id).
- The shell's **Sesi kapat** toggle (or opening a tab with `?mute=1`) is read
  inside the capture path and the speak function: nothing is captured or
  played. If the setting cannot be read (blocked storage) the mic is closed.

Measuring latency (owner step): open the browser devtools, speak one Turkish
sentence, and read the turn's `voice.VoiceTurnTrace` row (`playback_start_ms`
= end of speech → first audio) in the Django shell:

```python
from voice.models import VoiceTurnTrace
VoiceTurnTrace.objects.order_by("-created_at").values()[:5]
```


## 6. Wake word "Hey Friday" (Phase 6 branch 4)

Push-to-talk stays the default (owner decision d3). Wake mode is an in-tab
option: the wake word is detected **in the browser** (openWakeWord ONNX
graphs + Silero VAD on `onnxruntime-web`, single-threaded WASM, in a Worker);
audio before the wake word never leaves the tab, and the uploaded utterance
starts at the first sample *after* the detection frame (no pre-roll).

### 6.1 Models (`make voice-models`)

The manifest `frontend/src/lib/voice/model-manifest.json` is the single
source of truth: file names, pinned release URLs, sha256 and licences.

| Model | Source | Licence |
|---|---|---|
| `melspectrogram.onnx` | openWakeWord v0.5.1 release | CC BY-NC-SA 4.0 (openWakeWord pre-trained models; non-commercial, R3) |
| `embedding_model.onnx` | openWakeWord v0.5.1 release | same (re-implementation of Google's Apache-2.0 speech_embedding) |
| `hey_friday.onnx` | **owner-trained** (6.2) | the owner's own model; trained on openWakeWord's pipeline |
| `silero_vad.onnx` | silero-vad v5.1.2 | MIT |

```bash
# Downloads, verifies sha256, writes frontend/public/voice-models/ (gitignored)
# and copies the onnxruntime-web WASM runtime to public/voice-models/ort/.
VOICE_WAKE_MODEL_SRC=/path/to/hey_friday.onnx make voice-models
```

The browser refuses to arm wake mode if any file is missing, unpinned (empty
sha256) or its hash differs; every model URL is same-origin (no CDN). Until
`hey_friday.onnx` is trained and pinned, the toggle explains that and
push-to-talk keeps working.

### 6.2 Training `hey_friday` (owner step, offline)

openWakeWord ships `hey_jarvis` but no "hey friday". Train a keyword head with
openWakeWord's synthetic-data pipeline (its `automatic_model_training`
notebook / `train.py`: generated TTS clips of "hey friday" in Turkish and
English accents, plus negative data), export ONNX, then pin it:

```bash
sha256sum hey_friday.onnx      # put the hash into model-manifest.json ("keyword")
VOICE_WAKE_MODEL_SRC=$PWD/hey_friday.onnx make voice-models
```

Swapping or retraining the phrase is a manifest change (`wakePhrase`, the
keyword `file` and `sha256`), not a code change.

### 6.3 Using it

On **Sesli**, the "Uyandırma sözcüğü" panel arms wake mode with a click (a
user gesture; never restored after a reload). While armed: a persistent
indicator in the bottom-right corner, the tab title prefix "● Dinliyor", and
the mic open in this tab only. Navigating between pages keeps it armed (the
shell's `VoiceProvider`); a detection on another page opens `/voice`.

- After "Hey Friday", speak; the turn ends after `NEXT_PUBLIC_VOICE_FINISH_MS`
  of silence (VAD), at 30 s, or — if you say nothing within 5 s — nothing is
  uploaded and wake mode re-arms.
- While Friday speaks, the detector receives no audio (half-duplex). A
  detection within 1.5 s after playback whose transcript repeats Friday's
  own words is discarded (self-trigger rule).
- More than 3 activations in 60 s without a dispatched turn mutes voice and
  disarms (wake storm). Unmute with the shell's toggle.
- Disarmed by: the toggle or the indicator, logout or a hard 401 (revoked
  device), the shell mute, `NEXT_PUBLIC_VOICE_WAKE_IDLE_MINUTES` (default 60,
  editable per device) without a detection, the mic track ending.
- The detection threshold (default 0.5) is a per-device slider; raise it if
  the TV wakes Friday, lower it if "Hey Friday" is missed.

Browser support: Chromium and Safari (16.4+) with `AudioWorklet`, module
Workers and WASM SIMD; HTTPS or `localhost` (Permissions-Policy
`microphone=(self)` is sent in production). Background-tab throttling can
starve the worklet (R2); keep the tab visible or use push-to-talk.

### 6.4 Parity and CPU (manual / nightly, real models)

```bash
D=$(mktemp -d); cp frontend/public/voice-models/*.onnx "$D"/
# Python reference (throwaway venv, see the script's docstring)
/tmp/oww/bin/python -I frontend/scripts/oww-parity.py "$D" hey_friday.onnx
cd frontend && VOICE_PARITY_DIR="$D" npx vitest run \
  src/lib/voice/wakeword/parity.nightly.test.ts --reporter=verbose
```

It prints the max embedding/score delta (target ≤ 0.02) and ms per 80 ms
chunk in Node. The browser figure (Chromium and Safari, the always-on Worker
in Activity Monitor; target < 5% of one core) and the false-accept count over
the A4 negative set (target FAR ≤ 0.5/h, FRR ≤ 10%) are measured on the Mac
and recorded in the Phase 6 brief.
