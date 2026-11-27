"""Synthetic fixture clips for the replay harness (brief A4: "tones/silence
plus fake STT responses"). Built in code, never recorded, never a person's
voice -- so nothing binary is committed and CI never sees the owner's set.

`self_trigger_clip()` is the A6 self-trigger fixture: a stand-in for a
fake-TTS "Hey Friday, terminali aç" reply played back into the mic. Its
transcript (`SELF_TRIGGER_TEXT`) is what a fake STT returns for it; the
detector-side assertion (no upload, no proposal) belongs to the wake-word
owner (branch 4 / Phase 11 `friday-voice selftest`)."""

import math
import struct

from ai_core.audio import STT_SAMPLE_RATE, pcm_to_wav

SELF_TRIGGER_TEXT = "Hey Friday, terminali aç"


def pcm_tone(seconds, *, frequency=440.0, amplitude=0.3, rate=STT_SAMPLE_RATE):
    frames = int(round(seconds * rate))
    peak = int(32767 * amplitude)
    return b"".join(
        struct.pack("<h", int(peak * math.sin(2 * math.pi * frequency * i / rate)))
        for i in range(frames)
    )


def pcm_silence(seconds, *, rate=STT_SAMPLE_RATE):
    return b"\x00\x00" * int(round(seconds * rate))


def clip(*, lead=0.3, speech=1.0, tail=0.6, frequency=440.0, rate=STT_SAMPLE_RATE):
    """A 16 kHz mono PCM16 WAV: `lead` s silence, `speech` s tone, `tail` s
    silence. `speech=0` is an all-silent clip."""
    pcm = pcm_silence(lead, rate=rate)
    if speech:
        pcm += pcm_tone(speech, frequency=frequency, rate=rate)
    pcm += pcm_silence(tail, rate=rate)
    return pcm_to_wav(pcm or b"\x00\x00", sample_rate=rate)


def self_trigger_clip():
    return clip(lead=0.2, speech=1.4, tail=0.5, frequency=523.25)
