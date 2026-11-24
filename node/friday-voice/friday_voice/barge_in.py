"""`BargeInMonitor` (Phase 11 brief D9): the owner talks over Friday.

While Friday is *speaking*, every consumed capture frame (the host's
AEC-cleaned signal) is scored by the VAD. Barge-in fires when

- the speech probability is at least `config.BARGE_IN_VAD` (0.7) on
  consecutive frames for at least `config.BARGE_IN_MIN_MS` (250 ms), and
- every one of those frames came at least `config.BARGE_IN_AEC_GUARD_MS`
  (300 ms) after playback started (the AEC convergence guard: speech in
  the first 300 ms of playback never counts).

The monitor also keeps the audio of the current reply phase in a small
in-memory ring, so the new utterance can start with a
`config.BARGE_IN_PREROLL_MS` (300 ms) pre-roll before the speech onset.
The ring is cleared whenever a reply phase begins: it only ever holds
audio from inside the open session, never anything from before it
(ADR-011 decision 2 governs pre-wake audio; it is not touched here).

Pure and clock-injected; no audio is written anywhere.
"""

from dataclasses import dataclass

from . import config
from .audio import RingBuffer


@dataclass(frozen=True)
class BargeIn:
    onset_ms: int  # monotonic time of the first frame of the speech run
    fired_ms: int  # monotonic time of the frame that met the persistence rule
    preroll: bytes  # pre-roll + the speech run, oldest first


class BargeInMonitor:
    def __init__(self, vad, clock):
        self.vad = vad
        self.clock = clock
        # Pre-roll + the speech run + one frame of slack.
        size = config.ms_to_bytes(config.BARGE_IN_PREROLL_MS + config.BARGE_IN_MIN_MS + 40)
        self._history = RingBuffer(size)
        self.playing_since = None
        self._run_start = None
        self._run_bytes = 0
        self.fired = 0

    @property
    def active(self) -> bool:
        return self.playing_since is not None

    def begin_reply(self) -> None:
        """A reply phase starts: forget every earlier frame."""
        self._history.clear()
        self._reset_run()
        self.playing_since = None

    def playback_started(self) -> None:
        """Playback went from silent to playing: the guard restarts."""
        if self.playing_since is None:
            self.playing_since = self.clock.monotonic_ms()
            self._reset_run()
            self.vad.reset()

    def playback_stopped(self) -> None:
        self.playing_since = None
        self._reset_run()

    def _reset_run(self):
        self._run_start = None
        self._run_bytes = 0

    def remember(self, pcm: bytes) -> None:
        """A frame of the reply phase that is not scored (nothing playing)."""
        self._history.append(pcm)

    def feed(self, pcm: bytes):
        """One frame while playing. Returns a `BargeIn` or None."""
        self._history.append(pcm)
        if self.playing_since is None:
            return None
        now = self.clock.monotonic_ms()
        if now - self.playing_since < config.BARGE_IN_AEC_GUARD_MS:
            self._reset_run()
            return None
        if int(self.vad.speech_probability(pcm)) < config.BARGE_IN_VAD:
            self._reset_run()
            return None
        if self._run_start is None:
            self._run_start = now
        self._run_bytes += len(pcm)
        if config.bytes_to_ms(self._run_bytes) < config.BARGE_IN_MIN_MS:
            return None
        wanted = self._run_bytes + config.ms_to_bytes(config.BARGE_IN_PREROLL_MS)
        preroll = self._history.tail(wanted)
        event = BargeIn(onset_ms=self._run_start, fired_ms=now, preroll=preroll)
        self.fired += 1
        self._history.clear()
        self.playback_stopped()
        return event
