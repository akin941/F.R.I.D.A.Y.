"""Turn capture (brief D8): scripted VAD / smart-turn sequences end the
turn at the right frame; the hard caps; the PCM helpers."""

import io
import wave

import numpy as np
import pytest

from friday_voice import audio, config
from friday_voice.fakes import tone_wav
from friday_voice.turn import (
    END,
    NO_SPEECH,
    EnergyVad,
    ScriptedTurnEnd,
    ScriptedVad,
    TurnCapture,
)

FRAME = b"\x01\x00" * 320  # 20 ms


def run(capture, frames):
    """Feed frames until the capture ends; returns (result, frame index)."""
    for index in range(frames):
        result = capture.feed(FRAME)
        if result is not None:
            return result, index
    return None, frames


def vad_capture(probabilities, answers=(True,)):
    turn_end = ScriptedTurnEnd(answers)
    return TurnCapture(mode="vad", vad=ScriptedVad(probabilities), turn_end=turn_end), turn_end


def test_smart_turn_is_asked_after_200_ms_of_trailing_silence():
    capture, turn_end = vad_capture([900] * 10 + [0])
    result, index = run(capture, 100)
    assert result == END and capture.reason == "smart_turn"
    assert index == 10 + config.TURN_TRAILING_SILENCE_MS // 20 - 1  # the 10th silent frame
    assert turn_end.calls == [len(capture.pcm)]


def test_an_incomplete_turn_keeps_listening_up_to_the_hard_silence_cap():
    capture, turn_end = vad_capture([900] * 10 + [0], answers=(False,))
    result, index = run(capture, 200)
    assert result == END and capture.reason == "silence_cap"
    assert index == 10 + config.TURN_HARD_SILENCE_MS // 20 - 1
    assert len(turn_end.calls) == 1  # asked once per silence stretch


def test_speech_after_an_incomplete_verdict_asks_again():
    probabilities = [900] * 5 + [0] * 12 + [900] * 5 + [0]
    capture, turn_end = vad_capture(probabilities, answers=(False, True))
    result, _index = run(capture, 200)
    assert result == END and capture.reason == "smart_turn"
    assert len(turn_end.calls) == 2


def test_no_speech_within_five_seconds_uploads_nothing():
    capture, turn_end = vad_capture([0])
    result, index = run(capture, 1000)
    assert result == NO_SPEECH
    assert index == config.WAKE_NO_SPEECH_S * 50 - 1
    assert capture.pcm == bytearray() and turn_end.calls == []


def test_the_utterance_cap_ends_a_turn_that_never_pauses():
    capture, _ = vad_capture([900])
    result, index = run(capture, 2000)
    assert result == END and capture.reason == "utterance_cap"
    assert index == config.UTTERANCE_MAX_S * 50 - 1
    assert len(capture.pcm) == config.ms_to_bytes(config.UTTERANCE_MAX_S * 1000)


def test_push_to_talk_ends_on_the_key_and_ignores_vad():
    capture = TurnCapture(mode="ptt")
    assert run(capture, 30) == (None, 30)
    assert capture.finish() == END and capture.reason == "ptt"
    assert capture.finish() is None  # done is done


def test_a_short_press_is_a_slip():
    capture = TurnCapture(mode="ptt")
    run(capture, config.PTT_MIN_MS // 20 - 1)
    assert capture.finish() == NO_SPEECH and capture.pcm == bytearray()


def test_a_vad_capture_needs_its_detectors():
    with pytest.raises(ValueError):
        TurnCapture(mode="vad")
    with pytest.raises(ValueError):
        TurnCapture(mode="wake")


def test_energy_vad_hears_tones_not_silence():
    vad = EnergyVad()
    assert vad.speech_probability(b"\x00" * 640) == 0
    loud = (np.ones(320) * 3000).astype("<i2").tobytes()
    assert vad.speech_probability(loud) == 1000


# --- audio helpers ---


def test_wav_round_trip_and_normalisation_to_16k_mono():
    pcm = (np.sin(np.arange(1600) / 5) * 8000).astype("<i2").tobytes()
    assert audio.decode_wav(audio.encode_wav(pcm)) == pcm
    resampled = audio.decode_wav(tone_wav("Bir cümle."))  # 22.05 kHz
    assert len(resampled) == pytest.approx(16000 * 2 * 0.4, abs=4)
    stereo = io.BytesIO()
    with wave.open(stereo, "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x10\x00\x10\x00" * 160)
    assert len(audio.decode_wav(stereo.getvalue())) == 320


@pytest.mark.parametrize("data", [b"", b"RIFF\x00\x00", b"not a wav at all" * 4])
def test_an_unreadable_wav_is_refused(data):
    with pytest.raises(ValueError):
        audio.decode_wav(data)


def test_play_chunks_are_even_and_bounded():
    chunks = audio.play_chunks(b"\x01" * 100_001)
    assert all(len(c) % 2 == 0 and len(c) <= audio.MAX_PLAY_PAYLOAD for c in chunks)
    assert sum(map(len, chunks)) == 100_000
    assert audio.play_chunks(b"") == [b""]


def test_the_ring_buffer_keeps_only_the_newest_bytes():
    ring = audio.RingBuffer(10)
    ring.append(b"0123456789abc")
    assert len(ring) == 10 and ring.tail(3) == b"abc" and ring.tail(99) == b"3456789abc"
    ring.clear()
    assert ring.tail(3) == b""
