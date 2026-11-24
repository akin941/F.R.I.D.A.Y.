"""`EchoDouble` (Phase 11 brief D10): the reference host's stand-in for a
loudspeaker leaking into the microphone after acoustic echo cancellation.

The child's own `play` PCM is mixed back into the `audio` frames the host
captures, after `delay_ms`, scaled by `residual`: 0.0 is a perfect
canceller, 1.0 is no canceller at all. Self-trigger and barge-in tests run
at 0.0, 0.3 and 1.0 so both a good and a failing AEC are covered.

Host-side only: `residual` is a test knob, never a protocol field.
"""

import numpy as np

from ..protocol.messages import SAMPLE_RATE


class EchoDouble:
    def __init__(self, *, residual=0.0, delay_ms=0):
        if not 0.0 <= residual <= 1.0:
            raise ValueError("residual is between 0.0 (perfect AEC) and 1.0 (no AEC)")
        if delay_ms < 0:
            raise ValueError("delay_ms is not negative")
        self.residual = float(residual)
        self.delay_samples = SAMPLE_RATE * int(delay_ms) // 1000
        # Echo samples still to reach the microphone, in capture order.
        self._pending = np.zeros(0, dtype=np.int32)

    def played(self, pcm: bytes) -> None:
        """The host played `pcm` (the AEC reference). Playback that starts
        after silence reaches the microphone `delay_ms` later."""
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.int32)
        if not len(self._pending):
            samples = np.concatenate([np.zeros(self.delay_samples, dtype=np.int32), samples])
        self._pending = np.concatenate([self._pending, samples])

    def stopped(self) -> None:
        """Playback stopped: only what is already in the air (the delay) is
        still heard."""
        self._pending = self._pending[: self.delay_samples]

    def mix(self, capture: bytes) -> bytes:
        """Return `capture` with the residual echo of what was played."""
        mic = np.frombuffer(capture, dtype="<i2").astype(np.int32)
        count = min(len(mic), len(self._pending))
        if count:
            echo = (self._pending[:count] * self.residual).astype(np.int32)
            mic[:count] += echo
            self._pending = self._pending[count:]
        return np.clip(mic, -32768, 32767).astype("<i2").tobytes()

    @property
    def pending_samples(self) -> int:
        return int(len(self._pending))
