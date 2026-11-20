"""WAV validation helpers (Phase 6 D3): stdlib `wave` over BytesIO, declared
sizes cross-checked against the bytes actually received."""

import io
import struct
import wave

import pytest

from ai_core.audio import (
    InvalidAudio,
    normalize_tts_wav,
    parse_pcm_content_type,
    parse_stt_input,
    parse_wav,
    pcm_to_wav,
    silent_wav,
    wav_seconds_or_zero,
)


def make_wav(*, seconds=1.0, rate=16_000, channels=1, width=2):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(width)
        writer.setframerate(rate)
        writer.writeframes(b"\x01\x00" * (width // 2 or 1) * channels * int(rate * seconds))
    return buffer.getvalue()


def test_valid_utterance_reports_header_duration():
    info = parse_stt_input(make_wav(seconds=2.0), max_seconds=30)
    assert (info.sample_rate, info.channels, info.sample_width) == (16_000, 1, 2)
    assert info.frames == 32_000
    assert info.seconds == pytest.approx(2.0)


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (make_wav(channels=2), "wrong_channels"),
        (make_wav(rate=44_100), "wrong_sample_rate"),
        (make_wav(width=1), "not_pcm16"),
        (make_wav(seconds=31), "too_long"),
        (b"ID3\x04\x00\x00\x00\x00\x00\x00mp3-frame-bytes", "not_wav"),
        (b"RIFF", "not_wav"),
        (b"", "not_wav"),
    ],
)
def test_stt_input_rejections(data, reason):
    with pytest.raises(InvalidAudio) as excinfo:
        parse_stt_input(data, max_seconds=30)
    assert excinfo.value.reason == reason


def test_truncated_payload_rejected():
    data = make_wav()
    with pytest.raises(InvalidAudio) as excinfo:
        parse_wav(data[:-100])
    assert excinfo.value.reason == "size_mismatch"


def test_trailing_bytes_rejected_even_with_patched_riff_size():
    data = make_wav() + b"\x00" * 64
    patched = data[:4] + struct.pack("<I", len(data) - 8) + data[8:]
    with pytest.raises(InvalidAudio) as excinfo:
        parse_wav(patched)
    assert excinfo.value.reason == "size_mismatch"


def test_header_claiming_more_frames_than_sent_rejected():
    data = bytearray(make_wav(seconds=0.5))
    # data chunk size at offset 40 in a canonical 44-byte header
    struct.pack_into("<I", data, 40, 16_000 * 2 * 10)
    with pytest.raises(InvalidAudio):
        parse_wav(bytes(data))


def test_zero_frames_rejected():
    with pytest.raises(InvalidAudio) as excinfo:
        parse_wav(make_wav(seconds=0))
    assert excinfo.value.reason == "empty"


def test_non_pcm_format_rejected():
    data = bytearray(make_wav())
    struct.pack_into("<H", data, 20, 3)  # IEEE float format tag
    with pytest.raises(InvalidAudio):
        parse_wav(bytes(data))


def test_pcm_to_wav_and_silent_wav_round_trip():
    wav = pcm_to_wav(b"\x00\x00" * 24_000, sample_rate=24_000)
    assert parse_wav(wav).seconds == pytest.approx(1.0)
    assert parse_stt_input(silent_wav(1.5), max_seconds=30).seconds == pytest.approx(1.5)
    with pytest.raises(InvalidAudio):
        pcm_to_wav(b"\x00", sample_rate=24_000)  # half a frame


def test_wav_seconds_or_zero():
    assert wav_seconds_or_zero(make_wav(seconds=3)) == pytest.approx(3)
    assert wav_seconds_or_zero(b"junk") == 0.0


def test_tts_output_any_rate_and_cap():
    wav, info = normalize_tts_wav(make_wav(rate=22_050, seconds=1), max_seconds=60)
    assert info.sample_rate == 22_050
    with pytest.raises(InvalidAudio) as excinfo:
        normalize_tts_wav(make_wav(rate=22_050, seconds=3), max_seconds=2)
    assert excinfo.value.reason == "too_long"


def test_tts_streaming_header_is_rebuilt_from_received_pcm():
    data = bytearray(make_wav(rate=22_050, seconds=1))
    struct.pack_into("<I", data, 4, 0xFFFFFFFF)
    struct.pack_into("<I", data, 40, 0xFFFFFFFF)
    wav, info = normalize_tts_wav(bytes(data), max_seconds=60)
    assert info.frames == 22_050
    assert parse_wav(wav) == info


def test_tts_non_wav_rejected():
    with pytest.raises(InvalidAudio):
        normalize_tts_wav(b"\xff\xfb\x90mp3", max_seconds=60)


def test_upload_streaming_header_is_never_repaired():
    data = bytearray(make_wav())
    struct.pack_into("<I", data, 4, 0xFFFFFFFF)
    struct.pack_into("<I", data, 40, 0xFFFFFFFF)
    with pytest.raises(InvalidAudio):
        parse_stt_input(bytes(data), max_seconds=30)


@pytest.mark.parametrize(
    ("content_type", "expected"),
    [
        ("audio/pcm;rate=24000;channels=1", (24_000, 1)),
        ("audio/pcm; rate=44100", (44_100, 1)),
        ("audio/pcm", (24_000, 1)),
    ],
)
def test_pcm_content_type(content_type, expected):
    assert parse_pcm_content_type(content_type, default_rate=24_000) == expected


def test_pcm_content_type_rejects_absurd_shape():
    with pytest.raises(InvalidAudio):
        parse_pcm_content_type("audio/pcm;rate=5", default_rate=24_000)
