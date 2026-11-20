"""WAV checks for the speech gateway (Phase 6 D3, ADR-011).

One wire format: 16-bit PCM WAV. The STT input is the client-encoded
16 kHz mono utterance; the TTS output may be any PCM16 rate. Both are
validated with stdlib `wave` over `BytesIO` -- nothing touches disk -- and
the declared sizes must match the bytes actually received, so
`audio_seconds` (the cost/observability signal on `ModelUsage`) is derived
from audio that really arrived, never from a client- or server-controlled
header alone.

No speech library is imported here, or anywhere in the backend
(`ai_core/tests/test_sdk_guard.py`): speech engines run out of process.
"""

import io
import struct
import wave
from dataclasses import dataclass

PCM16_WIDTH = 2
STT_SAMPLE_RATE = 16_000
STT_CHANNELS = 1
_RIFF_HEADER = struct.Struct("<4sI4s")
# A streaming WAV writer that does not know the final length leaves the
# RIFF/data sizes as 0 or 0xFFFFFFFF (or 0x7FFFFFFF). Only TTS output from a
# speech server may carry that placeholder (repaired by `normalize_tts_wav`);
# a client upload never may.
_STREAMING_SIZES = {0, 0x7FFFFFFF, 0xFFFFFFFF}


class InvalidAudio(ValueError):  # noqa: N818 -- domain name, mirrors wave.Error
    """The bytes are not an acceptable PCM16 WAV. `reason` is a short,
    content-free code safe for logs and `ModelUsage`."""

    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class WavInfo:
    sample_rate: int
    channels: int
    sample_width: int
    frames: int

    @property
    def seconds(self):
        return self.frames / self.sample_rate if self.sample_rate else 0.0


def parse_wav(
    data,
    *,
    sample_rate=None,
    channels=None,
    max_seconds=None,
):
    """Validate `data` as a PCM16 WAV and return its `WavInfo`.

    Checks: RIFF/WAVE container, PCM (the `wave` module rejects every other
    format tag), 16-bit samples, optional exact sample rate / channel
    count, at least one frame, the RIFF size equal to the bytes received,
    the `data` chunk fully present (`frames x width x channels` bytes) and
    nothing after it beyond a pad byte, and an optional duration cap.
    Raises `InvalidAudio` otherwise."""
    if not isinstance(data, bytes | bytearray) or len(data) < _RIFF_HEADER.size:
        raise InvalidAudio("not_wav")
    riff, riff_size, wave_tag = _RIFF_HEADER.unpack_from(data)
    if riff != b"RIFF" or wave_tag != b"WAVE":
        raise InvalidAudio("not_wav")
    if riff_size + 8 != len(data):
        raise InvalidAudio("size_mismatch")
    buffer = io.BytesIO(bytes(data))
    try:
        with wave.open(buffer, "rb") as reader:
            info = WavInfo(
                sample_rate=reader.getframerate(),
                channels=reader.getnchannels(),
                sample_width=reader.getsampwidth(),
                frames=reader.getnframes(),
            )
            if info.sample_width != PCM16_WIDTH:
                raise InvalidAudio("not_pcm16")
            payload = reader.readframes(info.frames)
    except (wave.Error, EOFError, struct.error) as exc:
        raise InvalidAudio("not_pcm_wav") from exc
    if info.frames <= 0:
        raise InvalidAudio("empty")
    if len(payload) != info.frames * info.sample_width * info.channels:
        raise InvalidAudio("size_mismatch")
    if len(data) - buffer.tell() > 1:  # a single RIFF pad byte is legal
        raise InvalidAudio("size_mismatch")
    if sample_rate is not None and info.sample_rate != sample_rate:
        raise InvalidAudio("wrong_sample_rate")
    if channels is not None and info.channels != channels:
        raise InvalidAudio("wrong_channels")
    if max_seconds is not None and info.seconds > max_seconds:
        raise InvalidAudio("too_long")
    return info


def parse_stt_input(data, *, max_seconds):
    """D3's upload rule: 16 kHz mono PCM16, at most `max_seconds`."""
    return parse_wav(
        data, sample_rate=STT_SAMPLE_RATE, channels=STT_CHANNELS, max_seconds=max_seconds
    )


def wav_seconds_or_zero(data):
    """Best-effort duration for a pre-call budget estimate: 0.0 when the
    bytes are not a valid WAV (the adapter rejects them later anyway)."""
    try:
        return parse_wav(data).seconds
    except InvalidAudio:
        return 0.0


def pcm_to_wav(pcm, *, sample_rate, channels=1, sample_width=PCM16_WIDTH):
    """Wrap raw little-endian PCM in a WAV container (in memory). Used for
    providers that only return raw PCM (OpenRouter `/audio/speech`,
    `response_format="pcm"`), so the browser still only ever receives one
    checked `audio/wav` shape."""
    frame = sample_width * channels
    if not pcm or len(pcm) % frame:
        raise InvalidAudio("size_mismatch")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(sample_width)
        writer.setframerate(sample_rate)
        writer.writeframes(pcm)
    return buffer.getvalue()


def silent_wav(seconds, *, sample_rate=STT_SAMPLE_RATE):
    """A valid mono PCM16 WAV of digital silence (fake adapter, tests)."""
    frames = max(1, int(round(seconds * sample_rate)))
    return pcm_to_wav(b"\x00\x00" * frames, sample_rate=sample_rate)


def _repair_streaming_header(data):
    """A WAV whose RIFF/data sizes are streaming placeholders, rebuilt from
    the PCM bytes that actually arrived. Returns None if `data` is not that
    shape (the strict parser then decides)."""
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    offset = 12
    fmt = None
    while offset + 8 <= len(data):
        chunk_id, size = struct.unpack_from("<4sI", data, offset)
        body = offset + 8
        if chunk_id == b"fmt ":
            if size < 16 or body + 16 > len(data):
                return None
            fmt = struct.unpack_from("<HHIIHH", data, body)
        elif chunk_id == b"data":
            if fmt is None or size not in _STREAMING_SIZES:
                return None
            format_tag, channels, rate, _, _, bits = fmt
            if format_tag != 1 or bits != 16 or channels < 1:
                return None
            pcm = data[body:]
            pcm = pcm[: len(pcm) - len(pcm) % (PCM16_WIDTH * channels)]
            return pcm_to_wav(pcm, sample_rate=rate, channels=channels)
        offset = body + size + (size & 1)
    return None


def normalize_tts_wav(data, *, max_seconds):
    """D3's output rule: any-rate PCM16 WAV at most `max_seconds`. A
    streaming-style header (unknown length) is rebuilt from the received
    PCM first; everything else must pass the strict parser unchanged.
    Returns `(wav_bytes, WavInfo)`."""
    try:
        return data, parse_wav(data, max_seconds=max_seconds)
    except InvalidAudio as exc:
        if exc.reason not in {"size_mismatch", "not_pcm_wav"}:
            raise
        repaired = _repair_streaming_header(bytes(data))
        if repaired is None:
            raise
        return repaired, parse_wav(repaired, max_seconds=max_seconds)


def parse_pcm_content_type(content_type, *, default_rate):
    """`audio/pcm;rate=24000;channels=1` -> `(24000, 1)`. OpenRouter reports
    the PCM shape in the Content-Type (rate is model-dependent; spike A1)."""
    rate, channels = default_rate, 1
    for part in (content_type or "").split(";")[1:]:
        key, _, value = part.strip().partition("=")
        if key.lower() in {"rate", "sample_rate"} and value.isdigit():
            rate = int(value)
        elif key.lower() == "channels" and value.isdigit():
            channels = int(value)
    if not (8_000 <= rate <= 96_000) or channels not in {1, 2}:
        raise InvalidAudio("unsupported_pcm_shape")
    return rate, channels
