"""Ports of the voice child (Phase 11 brief D8).

Every detector, the Friday HTTP client and the clock sit behind a small
protocol so the pipeline is testable with fakes on Linux, with no real
microphone, model, server or wall clock. Branch 2 defines the ports and
the clocks; the real adapters (`OpenWakeWordDetector`, `SileroVad`,
`SmartTurnV3`, `HttpFridayClient`) and the fakes (`ScriptedWakeDetector`,
`ScriptedVad`, `ScriptedTurnEnd`, `FakeFriday`) land with the pipeline in
branch 3.

A detector is an on-device classifier that emits only scores (brief D2,
the ADR-013 implementation note): anything that emits text or tokens is a
model call and goes through Friday's gateway, never through a port here.
"""

import time
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now_ms(self) -> int:
        """Milliseconds since the Unix epoch."""

    def monotonic_ms(self) -> int:
        """Milliseconds on a monotonic clock (timeouts, watchdogs)."""


class RealClock:
    def now_ms(self) -> int:
        return time.time_ns() // 1_000_000

    def monotonic_ms(self) -> int:
        return time.monotonic_ns() // 1_000_000


class ManualClock:
    """A clock that moves only when told to (tests, conformance, replay)."""

    DEFAULT_EPOCH_MS = 1_800_000_000_000  # 2027-01-15T08:00:00Z

    def __init__(self, start_ms=DEFAULT_EPOCH_MS):
        self._now = int(start_ms)
        self._mono = 0

    def now_ms(self) -> int:
        return self._now

    def monotonic_ms(self) -> int:
        return self._mono

    def advance(self, ms) -> None:
        if ms < 0:
            raise ValueError("a clock never goes back")
        self._now += int(ms)
        self._mono += int(ms)


@runtime_checkable
class WakeDetector(Protocol):
    model_sha256: str

    def score(self, pcm: bytes) -> int:
        """Wake score of one 16 kHz PCM16 frame, in thousandths (0..1000)."""

    def reset(self) -> None: ...


@runtime_checkable
class VoiceActivity(Protocol):
    def speech_probability(self, pcm: bytes) -> int:
        """Speech probability of one frame, in thousandths (0..1000)."""

    def reset(self) -> None: ...


@runtime_checkable
class TurnEnd(Protocol):
    def is_complete(self, utterance_pcm: bytes) -> bool:
        """Whether the utterance so far reads as a finished turn."""


@runtime_checkable
class FridayClient(Protocol):
    """The child's only network surface: Friday's transcription, chat-turn,
    speech, live-session and voice-policy endpoints (brief D4, D8). No
    provider, no other host."""

    def set_credential(self, token: str, server_url: str, ca_sha256: str | None) -> None: ...
