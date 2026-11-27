"""Every function of `pipecat_adapter.py` against the pinned Pipecat
(brief D7, branch 3 Tests): frames, the voice processor inside a real
Pipecat Pipeline / PipelineWorker / WorkerRunner, the detector loaders
(hash-checked, audio log forced off), and, opt-in (`-m real_models`), the
real Silero and smart-turn v3 scoring a fixture."""

import hashlib
import os

import numpy as np
import pytest
from conftest import PACKAGE_ROOT

from friday_voice import models, pipecat_adapter
from friday_voice.reference.host import wav_frames
from friday_voice.turn import SileroVad, SmartTurnV3

SMART_TURN_SHA = models.by_role()["smart_turn"]["sha256"]


def test_frames_carry_16k_mono_pcm():
    frame = pipecat_adapter.input_audio_frame(b"\x01\x00" * 320)
    assert (frame.sample_rate, frame.num_channels, frame.num_frames) == (16000, 1, 320)
    out = pipecat_adapter.output_audio_frame(b"\x00\x00" * 10)
    assert pipecat_adapter.is_output_audio(out) and not pipecat_adapter.is_output_audio(frame)
    assert type(pipecat_adapter.end_frame()).__name__ == "EndFrame"


def test_the_engine_runs_inside_a_real_pipecat_pipeline():
    seen = []

    def on_audio(pcm):
        seen.append(len(pcm))
        return [pcm[:64], pcm[64:128]]

    frames = [pipecat_adapter.input_audio_frame(bytes([i]) * 640) for i in range(3)]
    out = pipecat_adapter.run_pipeline([pipecat_adapter.voice_processor(on_audio)], frames)
    audio = [frame for frame in out if pipecat_adapter.is_output_audio(frame)]
    assert seen == [640, 640, 640]
    assert [frame.audio for frame in audio] == [bytes([i]) * 64 for i in range(3) for _ in (0, 1)]
    assert type(out[-1]).__name__ == "EndFrame"


def test_quiet_disables_pipecat_logging():
    from loguru import logger

    pipecat_adapter.quiet()
    records = []
    sink = logger.add(records.append, level="DEBUG")
    try:
        code = "from loguru import logger; logger.info('noise')"
        exec(code, {"__name__": "pipecat.probe"})  # as if logged by a pipecat module
        exec(code, {"__name__": "friday_probe"})
    finally:
        logger.remove(sink)
    assert len(records) == 1 and "friday_probe" in records[0].record["name"]


def test_smart_turn_refuses_a_wrong_or_empty_pin():
    for pin in ("", "0" * 64):
        with pytest.raises(pipecat_adapter.ModelHashMismatch):
            pipecat_adapter.load_smart_turn(pin)


def test_silero_refuses_a_file_that_does_not_match(tmp_path):
    path = tmp_path / "silero_vad.onnx"
    path.write_bytes(b"not the model")
    with pytest.raises(pipecat_adapter.ModelHashMismatch):
        pipecat_adapter.load_silero(path, "0" * 64)
    with pytest.raises(pipecat_adapter.ModelHashMismatch):
        pipecat_adapter.load_silero(path, "")


def test_the_bundled_smart_turn_matches_the_pin():
    assert pipecat_adapter.bundled_model_sha256("smart_turn_v3") == SMART_TURN_SHA


def test_the_smart_turn_audio_log_is_forced_off(monkeypatch, tmp_path):
    monkeypatch.setenv("PIPECAT_SMART_TURN_LOG_DATA", "1")
    monkeypatch.chdir(tmp_path)
    analyzer = pipecat_adapter.load_smart_turn(SMART_TURN_SHA)
    assert analyzer._log_data is False
    probability = pipecat_adapter.smart_turn_probability(analyzer, np.zeros(8000, np.float32))
    assert 0.0 <= probability <= 1.0
    assert list(tmp_path.iterdir()) == []  # no ./smart_turn_audio_log/


def test_silero_wrapper_scores_through_the_adapter(tmp_path):
    """Pipecat's bundled Silero copy stands in for the pinned v5.1.2 file
    (not downloadable here): the adapter path is the same."""
    from importlib import resources

    data = resources.files("pipecat.audio.vad.data").joinpath("silero_vad.onnx").read_bytes()
    path = tmp_path / "silero_vad.onnx"
    path.write_bytes(data)
    model = pipecat_adapter.load_silero(path, hashlib.sha256(data).hexdigest())
    value = pipecat_adapter.silero_probability(model, np.zeros(512, np.float32))
    assert 0.0 <= value <= 1.0
    pipecat_adapter.silero_reset(model)


# --- real models (opt-in: -m real_models; Linux CPU) ---

MODEL_DIR = os.environ.get("FRIDAY_VOICE_MODEL_DIR")


@pytest.mark.real_models
def test_real_smart_turn_scores_a_fixture():
    pcm = b"".join(wav_frames(PACKAGE_ROOT / "fixtures" / "ptt_question.wav"))
    detector = SmartTurnV3()
    assert 0.0 <= detector.probability(pcm) <= 1.0
    assert isinstance(detector.is_complete(pcm), bool)


@pytest.mark.real_models
@pytest.mark.skipif(not MODEL_DIR, reason="FRIDAY_VOICE_MODEL_DIR (make voice-models-node)")
def test_real_silero_loads_from_the_pinned_hash_and_scores_a_fixture():
    vad = SileroVad(MODEL_DIR)
    silence = vad.speech_probability(b"\x00" * 640 * 50)
    vad.reset()
    tone = vad.speech_probability(
        b"".join(wav_frames(PACKAGE_ROOT / "fixtures" / "ptt_question.wav"))
    )
    assert 0 <= silence <= 1000 and 0 <= tone <= 1000
