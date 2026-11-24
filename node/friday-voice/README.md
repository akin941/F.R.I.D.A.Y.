# friday-voice — the live voice child (Phase 11)

The Python child process the Swift `friday-node` host spawns for live voice
(`docs/prompts/phase-11-live-voice/brief.md` D1, D6–D8, D16; ADR-013
decision 1). It talks to the host over **stdin/stdout only**
(`friday-voice/v1`), opens no listening socket, holds no provider key and
never the node secret, and imports nothing from `backend/`.

Branch 2 (`feat/p11-voice-child-protocol`) ships the package, the pins, the
protocol, the reference host, the protocol shell of the child and the
conformance suite. Branch 3 (`feat/p11-pipecat-pipeline`) plugs the voice
pipeline in behind the same shell: wake gate (wired; always-on is refused
until branch 5), turn capture, Friday's STT / chat / TTS clients, the
segmenter port, per-sentence language routing, `play` frames, and the
replay harness.

## Layout

| Path | What |
|---|---|
| `friday_voice/protocol/` | `framing.py` (length-prefixed frames), `messages.py` (frozen sets, refusal codes), `schemas/v1/{host_to_child,child_to_host}/`, `vectors/frames.json` |
| `friday_voice/child.py` | `ChildCore` (the fail-closed protocol shell) and `run_stdio` |
| `friday_voice/reference/` | `host.py` (the reference parent double: watchdog, backoff, play bookkeeping, local fail-closed rules, `ProcessHost`), `echo.py` (`EchoDouble`) |
| `friday_voice/conformance/` | `definitions.py` (cases + vectors, the source), `build.py` (render / `--check`), `runner.py`, `hostile.py` (`conformance-child`), `cases/{host,child}/*.json` |
| `friday_voice/ports.py` | detector / client / clock ports, `ManualClock` |
| `friday_voice/pipeline.py` | `VoicePipeline`: the D8 stages, the error rules, the turn timer |
| `friday_voice/{wake,turn}.py` | `WakeGate` + wake detectors; `TurnCapture` + VAD / smart-turn detectors and their scripted doubles |
| `friday_voice/client.py`, `fakes.py` | `HttpFridayClient` (the five endpoints, CA pin) and `FakeFriday` |
| `friday_voice/workers.py` | `ThreadWorker` (stdio) and `SimWorker` (manual clock) for the network jobs |
| `friday_voice/{segmenter,language,audio,config}.py` | sentence segmenter (port of `segmenter.ts`), tr/en routing, PCM/WAV helpers, every child tunable |
| `friday_voice/replay.py`, `fixtures/`, `baselines/replay.json` | the replay harness, its synthetic fixtures and the overhead baseline |
| `vectors/` | shared segmenter vectors (also run by vitest) and language vectors |
| `friday_voice/pipecat_adapter.py` | the only door to Pipecat; `pipecat_surface.json` is its snapshot |
| `friday_voice/log.py`, `_scrubber_vendored.py` | stderr log, scrubbed with the vendored ADR-012 scrubber |
| `models.json`, `friday_voice/models.py` | pinned detector models (shared entries equal the browser's manifest) |
| `scripts/` | `vendor_scrubber.py`, `snapshot_pipecat.py` (both with `--check`) |

## Commands

```bash
uv sync --frozen
uv run friday-voice run                       # serve one host on stdio (the host passes everything)
uv run friday-voice conformance               # both families vs the reference implementations
uv run friday-voice conformance --live        # the same, as real processes
uv run friday-voice conformance --check       # fail if cases/vectors drift from definitions.py
uv run friday-voice conformance --build       # after changing definitions.py
uv run friday-voice conformance-child --case <id>   # the hostile child a host conformance build launches
uv run python scripts/vendor_scrubber.py [--check]
uv run python scripts/snapshot_pipecat.py [--check]
uv run python scripts/make_fixtures.py [--check]  # the synthetic replay fixtures
uv run friday-voice replay [--check | --write-baseline]
uv run friday-voice replay --server http://127.0.0.1:8001 --token-file <file>  # a running Friday
uv run pytest                                 # default run; `-m real_models` is opt-in
```

`make friday-voice-test` runs the CI job locally; `make friday-voice-install`
installs the locked environment on the Mac under
`~/Library/Application Support/Friday/voice/` (outside the signed bundle).

## `friday-voice/v1` in one screen

```
frame  = u32 big-endian length (1 .. 1 MiB) || header JSON (canonical, UTF-8) || "\n" || payload
payload: only `audio` (host->child) and `play` (child->host): 16 kHz mono PCM16 LE,
         `pcm_bytes` long, <= 64 KiB, even
host -> child: hello credential audio playback mode mute ptt link shutdown
child -> host: ready state play stop_playback earcon release_mic log
```

The child sends nothing before `hello` + a valid `credential`; it releases
the mic on `link: down`, `mute: true|unreadable`, `mode: off`, credential
expiry, a protocol error, stdin EOF and `shutdown`. The host releases on any
child protocol error, a missing `state` for 10 s and child exit, and
restarts the child with backoff 1 s → 60 s.
