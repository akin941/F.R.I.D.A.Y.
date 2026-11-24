# Friday voice child — dependency manifest (Phase 11)

Governance record for `node/friday-voice/` (`docs/prompts/phase-11-live-voice/brief.md`
D7, D16; `docs/security/mcp-governance.md`). The voice child is a separate uv
project: its own `pyproject.toml` with **exact pins**, its own `uv.lock` with
the sha256 of every wheel (installed only with `uv sync --frozen`), no
install scripts beyond the wheels' own, and no Pipecat extras. It imports
nothing from `backend/` and the backend imports nothing from it. Upgrades go
in a dedicated branch (`chore/p11-pipecat-<version>` for Pipecat: bump the
pin, regenerate `friday_voice/pipecat_surface.json` with
`scripts/snapshot_pipecat.py`, fix `friday_voice/pipecat_adapter.py`, run the
replay and conformance suites), never as a side effect of another change.

## Step 0 record (Phase 11 branch 2, `feat/p11-voice-child-protocol`, 2026-11-20)

Rule: the latest release at least two weeks old on the day of the choice
(checked against PyPI upload dates on 2026-11-24, so the cut-off was
2026-11-10).

| Package | Pin | Licence (from the wheel) | Released | Newer, refused as < 2 weeks old | Used for |
|---|---|---|---|---|---|
| `pipecat-ai` | `==1.15.0` | BSD-2-Clause (`licenses/LICENSE`, © Daily) | 2026-10-29 | 1.16.0 (2026-11-12), 1.17.0 (2026-11-22) | The pipeline framework, through `pipecat_adapter.py` only (branch 3); bundles the smart-turn v3 model |
| `onnxruntime` | `==1.31.1` | MIT | 2026-10-26 | 1.32.0 (2026-11-13) | CPU inference for openWakeWord, Silero VAD and smart-turn v3 (classifiers only, D2) |
| `openwakeword` | `==0.6.0` | Apache-2.0 (code, `LICENSE`); models CC BY-NC-SA 4.0 (below) | 2024-02-11 | — (latest) | The wake-word feature pipeline (melspectrogram + embedding) and the keyword head, `inference_framework="onnx"` |
| `numpy` | `==2.5.4` | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | 2026-10-10 | 2.5.5 (2026-11-15) | PCM frames, the echo double |
| `httpx` | `==0.28.1` | BSD-3-Clause | 2024-12-06 | — (latest stable; 1.0 is still `.dev`) | The child's HTTPS client of Friday's voice endpoints (branch 3) |
| `jsonschema` | `==4.26.0` | MIT | (same pin as `backend/pyproject.toml`) | — | `friday-voice/v1` schema validation |

Dev only: `pytest==9.1.1`, `ruff==0.15.20` (the backend's versions). Build
backend: `hatchling` (wheel of the local package only).

**Install on Linux without extras: confirmed.** `uv sync --frozen` on
CPython 3.12 (Linux x86_64) installs 74 locked packages from wheels, and
`pipecat.frames.frames`, `pipecat.processors.frame_processor`,
`pipecat.pipeline.pipeline`, `pipecat.pipeline.task`,
`pipecat.audio.vad.vad_analyzer` and
`pipecat.audio.turn.smart_turn.local_smart_turn_v3` import
(`tests/test_pipecat_surface.py`).

### Two deviations, recorded

1. **`pipecat-ai` core requires the `openai` SDK** (`openai<4,>=1.74.0`,
   locked at 3.58.0) even without extras, plus `aiohttp`, `websockets`,
   `numba`, `resampy`, `soxr`, `soundfile`, `Pillow`, `protobuf`,
   `pydantic`, `loguru` and others. The brief's "no Pipecat extras that pull
   provider SDKs" holds (no extra is installed), but one provider SDK comes
   in through core. It is **never imported** by the child:
   `tests/test_guards.py` refuses `openai`, `anthropic`, `google.genai`,
   `google.generativeai`, `litellm` and every `pipecat.services.*` module
   in `friday_voice/` and `scripts/` (AST scan, `importlib.import_module`
   literals included), and the child holds no provider key (ADR-013
   decision 8). `tests/test_pipecat_surface.py` asserts that no other
   provider SDK (`anthropic`, `google.genai`, `deepgram`, `elevenlabs`,
   `cartesia`, `litellm`) is installed.
2. **`openwakeword` 0.6.0 declares `tflite-runtime` on Linux**, which ships
   no CPython 3.12 wheel. The child only runs the ONNX models, so
   `pyproject.toml` overrides it away on every platform
   (`[tool.uv] override-dependencies = ["tflite-runtime; python_version < '3.0'"]`).
   `scipy` (1.18.1), `scikit-learn` (1.9.1), `requests` and `tqdm` come in
   through openwakeword's own requirements; the child does not use them.

## Models (pinned in `node/friday-voice/models.json`)

The shared entries equal `frontend/src/lib/voice/model-manifest.json`
(`tests/test_models.py`), so the browser and the node run the same wake
model. The child refuses to arm wake mode unless every wake file matches a
non-empty pin.

| Role | File | sha256 | Licence |
|---|---|---|---|
| melspectrogram | `melspectrogram.onnx` (openWakeWord v0.5.1 release) | `ba2b0e0f…eb176f` | CC BY-NC-SA 4.0 (openWakeWord pre-trained models; non-commercial) |
| embedding | `embedding_model.onnx` (openWakeWord v0.5.1 release) | `70d16429…3075c1f` | CC BY-NC-SA 4.0 (openWakeWord's re-implementation of Google `speech_embedding`) |
| keyword | `hey_friday.onnx` (owner-trained) | _not pinned yet_ (owner step) | derived from the embedding graph: treated as CC BY-NC-SA 4.0 |
| vad | `silero_vad.onnx` (Silero VAD v5.1.2) | `2623a295…bdd788f` | MIT |
| smart_turn | `smart-turn-v3.2-cpu.onnx`, bundled in the `pipecat-ai==1.15.0` wheel (`pipecat/audio/turn/smart_turn/data/`); upstream `https://huggingface.co/pipecat-ai/smart-turn-v3` | `2bb02631…967e4f` | BSD-2-Clause (shipped under the pipecat-ai wheel's licence; model-card confirmation is an owner step: huggingface.co is not reachable from the build container) |

Pipecat 1.15.0 also bundles a Silero VAD file
(`pipecat/audio/vad/data/silero_vad.onnx`, sha256 `1a153a22…d8788e3`) that
differs from the browser's v5.1.2 pin; the child loads the pinned v5.1.2
file from its model directory, never the bundled one.

**Licence consequence (brief R7).** The openWakeWord graphs and therefore
`hey_friday` are non-commercial: fine for a single-owner, never-distributed
system, a blocker if Friday is ever productized. **Piper** (GPL-3.0) is in
neither the child nor the app bundle: it stays the speech server's separate
executable, and `backend/ai_core/tests/test_sdk_guard.py` refuses `import
piper` anywhere in the repository.

## Surface used

- Pipecat (branch 3, `feat/p11-pipecat-pipeline`, 2026-11-20):
  `pipecat_adapter.SURFACE` lists `InputAudioRawFrame`,
  `OutputAudioRawFrame`, `EndFrame`, `FrameProcessor`, `FrameDirection`,
  `Pipeline`, `PipelineWorker`, `WorkerRunner` (Pipecat 1.15 deprecates
  `PipelineTask`/`PipelineRunner` for these two), `SileroOnnxModel` (run
  over the pinned Silero v5.1.2 file, not the wheel's copy) and
  `LocalSmartTurnAnalyzerV3` (the bundled model, hash-checked; its audio log
  forced off); `pipecat_surface.json` snapshots them. Never
  `pipecat.services.*`.
- Imported directly, already locked as transitive dependencies (no new
  pin): `certifi` (httpx's CA bundle, the child's TLS trust store) and
  `loguru` (only to disable Pipecat's unscrubbed log output).
- Network (branch 3): the child calls only Friday's live-session,
  transcription, chat-turn, speech and voice-policy endpoints, outbound
  only, through `friday_voice/client.py`; an `https://` server must
  present the pinned CA in its verified chain, checked before any request
  byte is written.
- Files: reads its own install and the model directory; writes nothing
  (no audio is ever written, ADR-011 decision 3).

## Residual risk

A Pipecat, onnxruntime or openwakeword CVE, or Pipecat API churn (roadmap
§c.8). Mitigations: exact pins with hashes, the surface snapshot with
`--check` in CI, the adapter as the only door, upgrades in dedicated
branches.
