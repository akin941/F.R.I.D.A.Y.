"""The reference parent double (brief D6, D10): watchdog, child exit,
unknown frames, play bookkeeping, local fail-closed rules, the echo
double and the WAV feeder."""

import wave

import numpy as np
import pytest

from friday_voice.conformance.definitions import READY, SILENCE_20MS, play, state
from friday_voice.ports import ManualClock
from friday_voice.protocol.framing import decode_frame, encode_frame, raw_frame
from friday_voice.protocol.messages import CHILD_TO_HOST
from friday_voice.reference.echo import EchoDouble
from friday_voice.reference.host import (
    BACKOFF_MAX_MS,
    WATCHDOG_MS,
    HostCore,
    feed_wav,
    wav_frames,
)

CREDENTIAL = {
    "token": "jpat_" + "x" * 32,
    "expires_at": "2099-01-01T00:00:00Z",
    "server_url": "https://friday.example",
    "ca_sha256": "0" * 64,
}


def _send(core, header, payload=b""):
    core.child_bytes(encode_frame(header, payload, direction=CHILD_TO_HOST))


def _types(core):
    return [decode_frame(frame)[0]["type"] for frame in core.take_outbound()]


@pytest.fixture
def clock():
    return ManualClock()


@pytest.fixture
def core(clock):
    host = HostCore(clock, credential=CREDENTIAL)
    host.start_child()
    assert _types(host) == ["hello", "credential"]
    _send(host, READY)
    _send(host, state("armed_ptt"))
    assert host.mic_held
    return host


def test_the_mic_is_not_held_before_the_child_reports_an_armed_state(clock):
    host = HostCore(clock, credential=CREDENTIAL)
    host.start_child()
    _send(host, READY)
    assert not host.mic_held
    _send(host, state("off"))
    assert not host.mic_held
    _send(host, state("armed_ptt"))
    assert host.mic_held


def test_the_watchdog_releases_after_ten_seconds_without_state(core, clock):
    clock.advance(WATCHDOG_MS - 1)
    core.tick()
    assert core.mic_held and core.link_open
    clock.advance(1)
    core.tick()
    assert not core.mic_held
    assert core.indicator == "off"
    assert not core.link_open
    assert core.last_failure == "watchdog"
    assert core.restart_in_ms == 1000


def test_a_state_frame_resets_the_watchdog(core, clock):
    for _ in range(5):
        clock.advance(WATCHDOG_MS - 1)
        _send(core, state("armed_ptt"))
        core.tick()
    assert core.mic_held and core.last_failure is None


def test_child_exit_releases_and_restarts_with_capped_backoff(core, clock):
    seen = []
    for _ in range(9):
        core.child_exited(1)
        assert not core.mic_held and core.indicator == "off"
        seen.append(core.restart_in_ms)
        clock.advance(core.restart_in_ms - 1)
        core.tick()
        assert not core.restart_due()
        clock.advance(1)
        assert core.restart_due()
        core.start_child()
    assert seen == [1000, 2000, 4000, 8000, 16000, 32000, 60000, 60000, 60000]
    assert max(seen) == BACKOFF_MAX_MS


def test_backoff_restarts_after_a_minute_of_health(core, clock):
    core.child_exited(1)
    clock.advance(1000)
    core.start_child()
    _send(core, READY)
    for _ in range(7):
        _send(core, state("armed_ptt"))
        clock.advance(9000)
        core.tick()
    core.child_exited(1)
    assert core.restart_in_ms == 1000


def test_an_unknown_child_frame_releases_the_mic(core):
    core.child_bytes(raw_frame({"type": "open_url", "url": "https://x.example"}))
    assert not core.mic_held and not core.link_open
    assert core.last_failure == "unknown_type"
    assert core.take_outbound() == []


def test_nothing_reaches_a_dropped_child(core):
    core.child_bytes(raw_frame({"type": "policy.set"}))
    core.set_link(False)
    core.ptt("down")
    assert core.feed_audio(SILENCE_20MS) is False
    assert core.take_outbound() == []
    _send(core, state("armed_ptt"))  # bytes after the drop are ignored
    assert not core.mic_held


def test_play_for_an_unknown_utterance_is_dropped_and_counted(core):
    core.child_bytes(play("never", 1))
    assert core.dropped_play == 1 and core.link_open and core.mic_held
    assert core.played == []
    core.child_bytes(play("u1", 0))
    core.child_bytes(play("u1", 1, last=True))
    assert [(u, s) for u, s, _ in core.played] == [("u1", 0), ("u1", 1)]
    core.child_bytes(play("u1", 0))
    assert core.dropped_play == 2


def test_stop_playback_closes_the_utterance_and_reports_it(core):
    core.child_bytes(play("u1", 0))
    core.take_outbound()
    _send(core, {"type": "stop_playback", "utterance_id": "u1"})
    headers = [decode_frame(f)[0] for f in core.take_outbound()]
    assert [(h["type"], h["state"]) for h in headers] == [("playback", "stopped")]
    core.child_bytes(play("u1", 1))
    assert core.dropped_play == 1


@pytest.mark.parametrize(
    ("event", "value", "indicator"),
    [("link", False, "off"), ("mute", True, "muted"), ("mute", "unreadable", "muted")],
)
def test_local_fail_closed_inputs_release_whatever_the_child_says(core, event, value, indicator):
    getattr(core, f"set_{event}")(value)
    assert not core.mic_held and core.indicator == indicator
    _send(core, state("listening"))
    assert not core.mic_held
    assert core.feed_audio(SILENCE_20MS) is False


def test_capture_reaches_the_child_only_while_held(core):
    core.take_outbound()
    assert core.feed_audio(SILENCE_20MS)
    assert _types(core) == ["audio"]
    _send(core, {"type": "release_mic", "reason": "storm"})
    assert not core.feed_audio(SILENCE_20MS)
    assert _types(core) == []
    assert core.releases[-1] == "child:storm"


def test_shutdown_releases_and_a_clean_exit_is_not_a_failure(core):
    core.take_outbound()
    core.shutdown()
    assert _types(core) == ["shutdown"]
    assert not core.mic_held and core.indicator == "off"
    _send(core, state("armed_ptt"))
    assert not core.mic_held  # never re-acquired while stopping
    core.child_exited(0)
    assert core.failures == [] and core.restart_at is None and not core.link_open


def test_a_failure_is_recorded_as_a_host_release(core, clock):
    clock.advance(WATCHDOG_MS)
    core.tick()
    assert core.releases[-1] == "failure:watchdog"


def test_earcons_and_logs_are_recorded(core):
    _send(core, {"type": "earcon", "earcon": "wake"})
    _send(core, {"type": "log", "code": "http.status", "detail": "429"})
    assert core.earcons == ["wake"] and core.logs == ["http.status"]


# --- echo double ---


def _tone(samples, value=1000):
    return np.full(samples, value, dtype="<i2").tobytes()


def _samples(pcm):
    return np.frombuffer(pcm, dtype="<i2")


@pytest.mark.parametrize(("residual", "expected"), [(0.0, 0), (0.3, 300), (1.0, 1000)])
def test_the_echo_double_mixes_the_residual(residual, expected):
    echo = EchoDouble(residual=residual)
    echo.played(_tone(320))
    mixed = _samples(echo.mix(b"\x00\x00" * 320))
    assert set(mixed.tolist()) == {expected}


def test_the_echo_double_delays_and_stops():
    echo = EchoDouble(residual=1.0, delay_ms=10)  # 160 samples
    echo.played(_tone(320))
    mixed = _samples(echo.mix(b"\x00\x00" * 320))
    assert mixed[:160].tolist() == [0] * 160
    assert mixed[160:].tolist() == [1000] * 160
    echo.played(_tone(320))
    echo.stopped()
    assert echo.pending_samples <= 160


def test_the_echo_double_clips():
    echo = EchoDouble(residual=1.0)
    echo.played(_tone(10, 30000))
    assert set(_samples(echo.mix(_tone(10, 30000))).tolist()) == {32767}


def test_the_echo_double_refuses_nonsense():
    with pytest.raises(ValueError):
        EchoDouble(residual=1.5)
    with pytest.raises(ValueError):
        EchoDouble(delay_ms=-1)


def test_the_host_mixes_played_audio_into_capture(clock):
    host = HostCore(clock, credential=CREDENTIAL, echo=EchoDouble(residual=1.0))
    host.start_child()
    _send(host, READY)
    _send(host, state("speaking"))
    host.child_bytes(play("u1", 0, last=True, pcm=_tone(320, 500)))
    host.take_outbound()
    host.feed_audio(b"\x00\x00" * 320)
    header, payload = decode_frame(host.take_outbound()[0])
    assert header["type"] == "audio"
    assert set(_samples(payload).tolist()) == {500}


# --- WAV fixtures ---


def _write_wav(path, pcm, rate=16000, channels=1):
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm)


def test_wav_frames_split_and_pad(tmp_path):
    path = tmp_path / "tone.wav"
    _write_wav(path, _tone(400))  # 25 ms
    frames = wav_frames(path)
    assert [len(f) for f in frames] == [640, 640]
    assert frames[1][160:] == b"\x00" * 480


def test_wav_frames_refuse_other_formats(tmp_path):
    path = tmp_path / "stereo.wav"
    _write_wav(path, _tone(400), channels=2)
    with pytest.raises(ValueError):
        wav_frames(path)


def test_feed_wav_at_an_accelerated_clock(core, clock, tmp_path):
    path = tmp_path / "tone.wav"
    _write_wav(path, _tone(1600))  # 100 ms
    start = clock.monotonic_ms()
    assert feed_wav(core, path, clock=clock) == 5
    assert clock.monotonic_ms() - start == 100
    assert _types(core)[-5:] == ["audio"] * 5
