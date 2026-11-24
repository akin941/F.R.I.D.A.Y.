"""The child's only door to Pipecat (Phase 11 brief D7).

Pipecat's 0.x/1.x API churns (roadmap §c.8), so every Pipecat name the
child uses is listed in SURFACE and reached through this module only. A
snapshot of the installed version's signatures for those names lives in
`friday_voice/pipecat_surface.json` (`scripts/snapshot_pipecat.py`
writes it; `--check` fails when the installed Pipecat differs), and
`tests/test_pipecat_surface.py` exercises every function here against the
pinned version.

What the child takes from Pipecat (branch 3):
- the detectors: Pipecat's Silero ONNX wrapper (`SileroOnnxModel`, run over
  the Silero v5.1.2 file pinned in `models.json`, never the copy bundled in
  the wheel) and the smart-turn v3 analyzer (`LocalSmartTurnAnalyzerV3`
  over the bundled `smart-turn-v3.2-cpu.onnx`, hash-checked first);
- the frame classes and the runtime (`InputAudioRawFrame`,
  `OutputAudioRawFrame`, `EndFrame`, `FrameProcessor`, `Pipeline`,
  `PipelineWorker`, `WorkerRunner`): `PipecatVoiceProcessor` hosts the
  child's stage engine (`friday_voice.pipeline`) as one Pipecat processor
  -- capture frames in, `play` audio out -- and `run_pipeline` runs it in
  a real Pipecat pipeline (tests/test_pipecat_surface.py). The stdio child
  and the replay harness drive the same engine directly, because Pipecat's
  task runner is asyncio on a real clock and the replay harness needs a
  manual one (brief "Branch 3 implementation notes").

Pipecat logs through loguru to stderr, unscrubbed, and its smart-turn
analyzer can write captured audio to `./smart_turn_audio_log/` when
`PIPECAT_SMART_TURN_LOG_DATA` is set: `quiet()` disables Pipecat's logger
before the first Pipecat import, and `load_smart_turn` forces the audio
log off whatever the environment says (ADR-011 decision 3: no audio on
disk).

Upgrades only in a dedicated `chore/p11-pipecat-<version>` branch: bump
the pin, regenerate the snapshot, fix this adapter, run the replay and
conformance suites.

Never listed here: `pipecat.services.*` (provider STT/LLM/TTS services) --
the child's speech and language steps are its own HTTP clients of Friday
(D8), and the import guard refuses those modules anywhere in the package.
"""

import asyncio
import dataclasses
import hashlib
import importlib
import importlib.metadata
import inspect
from importlib import resources

import numpy as np

DISTRIBUTION = "pipecat-ai"
PINNED_VERSION = "1.15.0"
SAMPLE_RATE = 16_000
SILERO_WINDOW = 512  # samples per Silero v5 window at 16 kHz

# (module, attribute) pairs the child uses.
SURFACE: tuple[tuple[str, str], ...] = (
    ("pipecat.audio.turn.smart_turn.local_smart_turn_v3", "LocalSmartTurnAnalyzerV3"),
    ("pipecat.audio.vad.silero", "SileroOnnxModel"),
    ("pipecat.frames.frames", "EndFrame"),
    ("pipecat.frames.frames", "InputAudioRawFrame"),
    ("pipecat.frames.frames", "OutputAudioRawFrame"),
    ("pipecat.pipeline.pipeline", "Pipeline"),
    ("pipecat.workers.runner", "WorkerRunner"),
    ("pipecat.pipeline.worker", "PipelineWorker"),
    ("pipecat.processors.frame_processor", "FrameDirection"),
    ("pipecat.processors.frame_processor", "FrameProcessor"),
)

# Model files the pinned wheel bundles and the child may load (models.json
# pins the same sha256; the child verifies before loading, brief D8).
BUNDLED_MODELS = {
    "smart_turn_v3": ("pipecat.audio.turn.smart_turn.data", "smart-turn-v3.2-cpu.onnx"),
}


class ModelHashMismatch(RuntimeError):
    """A model file does not match its pin: it is never loaded."""


_QUIET = False


def quiet() -> None:
    """Silence Pipecat's loguru output (it is not scrubbed, D16). Called
    before every Pipecat import in this module."""
    global _QUIET
    if _QUIET:
        return
    from loguru import logger

    logger.disable("pipecat")
    _QUIET = True


def _get(module_name, attribute):
    quiet()
    return getattr(importlib.import_module(module_name), attribute)


def installed_version() -> str:
    return importlib.metadata.version(DISTRIBUTION)


def describe(module_name, attribute) -> dict:
    """A stable description of one Pipecat name: kind, signature, bases and
    (for dataclasses) field names."""
    target = _get(module_name, attribute)
    if inspect.isclass(target):
        entry = {
            "kind": "class",
            "bases": [f"{base.__module__}.{base.__qualname__}" for base in target.__mro__[1:]],
            "init": _signature(target.__init__),
        }
        if dataclasses.is_dataclass(target):
            entry["fields"] = [field.name for field in dataclasses.fields(target)]
        entry["methods"] = {
            name: _signature(member)
            for name, member in sorted(vars(target).items())
            if not name.startswith("_") and inspect.isfunction(member)
        }
        return entry
    if callable(target):
        return {"kind": "function", "signature": _signature(target)}
    return {"kind": "value", "type": type(target).__name__}


def _signature(target) -> str:
    try:
        return str(inspect.signature(target))
    except (TypeError, ValueError):
        return "<no signature>"


def bundled_model_path(name):
    package, filename = BUNDLED_MODELS[name]
    return resources.files(package).joinpath(filename)


def bundled_model_sha256(name) -> str:
    return hashlib.sha256(bundled_model_path(name).read_bytes()).hexdigest()


def snapshot(surface=SURFACE) -> dict:
    """The document `pipecat_surface.json` holds."""
    return {
        "distribution": DISTRIBUTION,
        "version": installed_version(),
        "surface": {
            f"{module}:{attribute}": describe(module, attribute) for module, attribute in surface
        },
        "bundled_models": {name: bundled_model_sha256(name) for name in sorted(BUNDLED_MODELS)},
    }


# --- detectors ---


def _sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_silero(path, sha256):
    """Pipecat's Silero ONNX wrapper over the pinned model file. Refuses a
    file whose hash differs from the pin (or an empty pin)."""
    if not sha256 or _sha256(path) != sha256:
        raise ModelHashMismatch("silero_vad")
    return _get("pipecat.audio.vad.silero", "SileroOnnxModel")(str(path))


def silero_probability(model, window: np.ndarray) -> float:
    """Speech probability of one 512-sample float32 window."""
    return float(np.asarray(model(window.astype(np.float32), SAMPLE_RATE)).reshape(-1)[0])


def silero_reset(model) -> None:
    model.reset_states()


def load_smart_turn(sha256):
    """Pipecat's smart-turn v3 analyzer over the bundled model, after the
    bundled file's hash matched the pin. Its audio log is forced off."""
    if not sha256 or bundled_model_sha256("smart_turn_v3") != sha256:
        raise ModelHashMismatch("smart_turn_v3")
    analyzer_class = _get(
        "pipecat.audio.turn.smart_turn.local_smart_turn_v3", "LocalSmartTurnAnalyzerV3"
    )
    with resources.as_file(bundled_model_path("smart_turn_v3")) as path:
        analyzer = analyzer_class(smart_turn_model_path=str(path), sample_rate=SAMPLE_RATE)
    analyzer._log_data = False  # never ./smart_turn_audio_log/ (ADR-011 decision 3)
    return analyzer


def smart_turn_probability(analyzer, audio: np.ndarray) -> float:
    """Probability (0..1) that the float32 16 kHz utterance is a finished
    turn. Synchronous: the child calls it from its own loop."""
    analyzer._log_data = False
    return float(analyzer._predict_endpoint(audio.astype(np.float32))["probability"])


# --- frames and the Pipecat runtime ---


def input_audio_frame(pcm: bytes):
    return _get("pipecat.frames.frames", "InputAudioRawFrame")(
        audio=bytes(pcm), sample_rate=SAMPLE_RATE, num_channels=1
    )


def output_audio_frame(pcm: bytes):
    return _get("pipecat.frames.frames", "OutputAudioRawFrame")(
        audio=bytes(pcm), sample_rate=SAMPLE_RATE, num_channels=1
    )


def end_frame():
    return _get("pipecat.frames.frames", "EndFrame")()


def _processor_classes():
    """Build the two FrameProcessor subclasses on first use (Pipecat is
    imported lazily, after `quiet()`)."""
    base = _get("pipecat.processors.frame_processor", "FrameProcessor")
    direction = _get("pipecat.processors.frame_processor", "FrameDirection")
    input_frame = _get("pipecat.frames.frames", "InputAudioRawFrame")

    class PipecatVoiceProcessor(base):
        """Hosts the child's stage engine in a Pipecat pipeline: every
        InputAudioRawFrame goes to `on_audio(pcm)`, which returns the PCM
        chunks to play; each becomes an OutputAudioRawFrame downstream.
        Every other frame passes through."""

        def __init__(self, on_audio, **kwargs):
            super().__init__(**kwargs)
            self._on_audio = on_audio

        async def process_frame(self, frame, frame_direction):
            await super().process_frame(frame, frame_direction)
            if isinstance(frame, input_frame) and frame_direction == direction.DOWNSTREAM:
                for pcm in self._on_audio(bytes(frame.audio)):
                    await self.push_frame(output_audio_frame(pcm), direction.DOWNSTREAM)
                return
            await self.push_frame(frame, frame_direction)

    class Collector(base):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.frames = []

        async def process_frame(self, frame, frame_direction):
            await super().process_frame(frame, frame_direction)
            self.frames.append(frame)
            await self.push_frame(frame, frame_direction)

    return PipecatVoiceProcessor, Collector


def voice_processor(on_audio):
    return _processor_classes()[0](on_audio)


async def _run(processors, frames):
    pipeline_class = _get("pipecat.pipeline.pipeline", "Pipeline")
    task_class = _get("pipecat.pipeline.worker", "PipelineWorker")
    runner_class = _get("pipecat.workers.runner", "WorkerRunner")
    collector = _processor_classes()[1]()
    task = task_class(
        pipeline_class([*processors, collector]),
        enable_rtvi=False,
        enable_turn_tracking=False,
        idle_timeout_secs=None,
        check_dangling_tasks=False,
    )
    await task.queue_frames([*frames, end_frame()])
    runner = runner_class(handle_sigint=False, check_dangling_tasks=False)
    await runner.add_workers(task)
    await runner.run()
    return collector.frames


def run_pipeline(processors, frames) -> list:
    """Run `frames` (then an EndFrame) through a real Pipecat Pipeline /
    PipelineWorker / WorkerRunner and return every frame that reached its
    end. Used by the tests to prove the engine runs inside Pipecat."""
    return asyncio.run(_run(list(processors), list(frames)))


def is_output_audio(frame) -> bool:
    return isinstance(frame, _get("pipecat.frames.frames", "OutputAudioRawFrame"))
