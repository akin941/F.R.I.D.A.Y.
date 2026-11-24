"""PCM helpers of the voice child (Phase 11 brief D8).

Everything here works on bytes in memory: the child never writes audio
anywhere (ADR-011 decision 3). Capture is 16 kHz mono PCM16 little-endian;
an utterance leaves the child only as an in-memory WAV posted to Friday's
transcription endpoint, and a synthesized reply comes back as WAV and is
normalised to the same format before it becomes `play` frames.
"""

import io
import wave

import numpy as np

from . import config

MAX_PLAY_PAYLOAD = 64 * 1024


class RingBuffer:
    """The pre-wake buffer: the newest `max_bytes` of capture, in memory."""

    def __init__(self, max_bytes):
        self.max_bytes = int(max_bytes)
        self._data = bytearray()

    def append(self, pcm: bytes) -> None:
        self._data += pcm
        if len(self._data) > self.max_bytes:
            del self._data[: len(self._data) - self.max_bytes]

    def tail(self, size: int) -> bytes:
        size = max(0, min(int(size), len(self._data)))
        return bytes(self._data[len(self._data) - size :]) if size else b""

    def clear(self) -> None:
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)


def encode_wav(pcm: bytes, *, sample_rate=config.SAMPLE_RATE) -> bytes:
    """16 kHz mono PCM16 -> a WAV file, in memory."""
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return out.getvalue()


def _to_float(frames: bytes, width: int) -> np.ndarray:
    if width == 2:
        return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if width == 1:
        return (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    if width == 4:
        return np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0
    raise ValueError("unsupported sample width")


def resample(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    """Linear-interpolation resampling (speech for playback; the server's
    TTS normally already returns 16 kHz)."""
    if source_rate == target_rate or not len(samples):
        return samples
    count = max(1, round(len(samples) * target_rate / source_rate))
    positions = np.arange(count, dtype=np.float64) * (source_rate / target_rate)
    return np.interp(positions, np.arange(len(samples)), samples).astype(np.float32)


def decode_wav(data: bytes) -> bytes:
    """A PCM WAV (any rate, 1-2 channels, 8/16/32-bit) -> 16 kHz mono PCM16.
    Raises ValueError for anything else (the child plays nothing it cannot
    read)."""
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            channels, width, rate = wav.getnchannels(), wav.getsampwidth(), wav.getframerate()
            frames = wav.readframes(wav.getnframes())
    except (wave.Error, EOFError) as exc:
        raise ValueError("not a PCM WAV") from exc
    if channels not in (1, 2) or rate <= 0:
        raise ValueError("unsupported WAV layout")
    if (channels, width, rate) == (1, 2, config.SAMPLE_RATE):
        return frames[: len(frames) - len(frames) % 2]  # already the playback format
    samples = _to_float(frames, width)
    if channels == 2:
        samples = samples[: len(samples) - len(samples) % 2].reshape(-1, 2).mean(axis=1)
    samples = resample(samples, rate, config.SAMPLE_RATE)
    return to_pcm16(samples)


def to_pcm16(samples: np.ndarray) -> bytes:
    clipped = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2").tobytes()


def to_float32(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0


def play_chunks(pcm: bytes, *, chunk_ms=config.PLAY_CHUNK_MS) -> list:
    """Split a reply sentence into `play` payloads (even length, <= 64 KiB).
    An empty sentence still yields one empty chunk so the utterance opens
    and closes."""
    size = min(config.ms_to_bytes(chunk_ms), MAX_PLAY_PAYLOAD)
    size -= size % 2
    pcm = pcm[: len(pcm) - len(pcm) % 2]
    chunks = [pcm[start : start + size] for start in range(0, len(pcm), size)]
    return chunks or [b""]


def rms(pcm: bytes) -> int:
    """Root-mean-square level of a frame in thousandths of full scale."""
    if not pcm:
        return 0
    samples = to_float32(pcm)
    return int(round(float(np.sqrt(np.mean(samples * samples))) * 1000))
