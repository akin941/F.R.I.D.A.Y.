"""`TurnCapture` and the end-of-turn detectors (Phase 11 brief D8).

One utterance, from its first frame to its end:

- **push-to-talk** (`mode="ptt"`): every frame while the key is down; the
  key going up ends the turn. A press shorter than `config.PTT_MIN_MS` is
  a slip and uploads nothing.
- **wake / session** (`mode="vad"`): Silero VAD marks speech; after
  `config.TURN_TRAILING_SILENCE_MS` (200 ms) of trailing silence smart-turn
  v3 scores the turn: complete -> end of turn; incomplete -> keep listening
  up to `config.TURN_HARD_SILENCE_MS` (1.5 s) of silence. No speech within
  `config.WAKE_NO_SPEECH_S` (5 s) -> nothing is uploaded.

Both modes stop at `config.UTTERANCE_MAX_S` (30 s, the ADR-011 upload cap).
The utterance lives in memory only; the caller uploads it as a WAV or drops
it.
"""

import numpy as np

from . import config, models, pipecat_adapter
from .audio import to_float32

END = "end"
NO_SPEECH = "no_speech"

# End reasons, for the trace and the tests.
REASON_PTT = "ptt"
REASON_SMART_TURN = "smart_turn"
REASON_SILENCE_CAP = "silence_cap"
REASON_UTTERANCE_CAP = "utterance_cap"


class TurnCapture:
    def __init__(self, *, mode, vad=None, turn_end=None, preroll=b""):
        if mode not in ("ptt", "vad"):
            raise ValueError("mode is ptt or vad")
        if mode == "vad" and (vad is None or turn_end is None):
            raise ValueError("a vad capture needs a VAD and a turn-end detector")
        self.mode = mode
        self.vad = vad
        self.turn_end = turn_end
        self.pcm = bytearray(preroll)
        self.heard_speech = False
        self.silence_ms = 0
        self.elapsed_ms = 0
        self.asked_turn_end = False
        self.last_speech_ms = None  # capture time (ms since start) of the last speech frame
        self.reason = None
        self.done = False
        if vad is not None:
            vad.reset()

    @property
    def max_bytes(self) -> int:
        return config.ms_to_bytes(config.UTTERANCE_MAX_S * 1000)

    def feed(self, frame: bytes):
        """One capture frame. Returns None (keep going), END or NO_SPEECH."""
        if self.done:
            return None
        room = self.max_bytes - len(self.pcm)
        piece = frame[: max(room, 0)]
        self.pcm += piece
        frame_ms = config.bytes_to_ms(len(frame))
        self.elapsed_ms += frame_ms
        if self.mode == "ptt":
            if len(self.pcm) >= self.max_bytes:
                return self._end(REASON_UTTERANCE_CAP)
            return None
        speech = int(self.vad.speech_probability(piece)) >= config.VAD_SPEECH_THRESHOLD
        if speech:
            self.heard_speech = True
            self.silence_ms = 0
            self.asked_turn_end = False
            self.last_speech_ms = self.elapsed_ms
        elif self.heard_speech:
            self.silence_ms += frame_ms
        if len(self.pcm) >= self.max_bytes:
            return self._end(REASON_UTTERANCE_CAP) if self.heard_speech else self._no_speech()
        if not self.heard_speech:
            if self.elapsed_ms >= config.WAKE_NO_SPEECH_S * 1000:
                return self._no_speech()
            return None
        if self.silence_ms >= config.TURN_HARD_SILENCE_MS:
            return self._end(REASON_SILENCE_CAP)
        if self.silence_ms >= config.TURN_TRAILING_SILENCE_MS and not self.asked_turn_end:
            self.asked_turn_end = True
            if self.turn_end.is_complete(bytes(self.pcm)):
                return self._end(REASON_SMART_TURN)
        return None

    def finish(self):
        """The push-to-talk key went up."""
        if self.done:
            return None
        if config.bytes_to_ms(len(self.pcm)) < config.PTT_MIN_MS:
            return self._no_speech()
        return self._end(REASON_PTT)

    def _end(self, reason):
        self.done = True
        self.reason = reason
        return END

    def _no_speech(self):
        self.done = True
        self.reason = NO_SPEECH
        self.pcm.clear()
        return NO_SPEECH


class ScriptedVad:
    """Test double: `probabilities` is a list consumed one value per call
    (thousandths; the last value repeats), or a callable `(index, pcm)`."""

    def __init__(self, probabilities=(0,)):
        self.probabilities = probabilities
        self.calls = 0
        self.resets = 0

    def speech_probability(self, pcm: bytes) -> int:
        index = self.calls
        self.calls += 1
        if callable(self.probabilities):
            return int(self.probabilities(index, pcm))
        values = list(self.probabilities) or [0]
        return int(values[min(index, len(values) - 1)])

    def reset(self) -> None:
        self.resets += 1


class EnergyVad:
    """A level gate (RMS in thousandths of full scale): the fixtures'
    "speech" is a tone and their silence is digital zero, so the replay
    harness can run turn capture without a model."""

    def __init__(self, threshold=20):
        self.threshold = threshold

    def speech_probability(self, pcm: bytes) -> int:
        from .audio import rms

        return 1000 if rms(pcm) >= self.threshold else 0

    def reset(self) -> None:
        pass


class ScriptedTurnEnd:
    """Test double: answers are consumed one per call (the last repeats)."""

    def __init__(self, answers=(True,)):
        self.answers = list(answers) or [True]
        self.calls = []

    def is_complete(self, utterance_pcm: bytes) -> bool:
        index = len(self.calls)
        self.calls.append(len(utterance_pcm))
        return bool(self.answers[min(index, len(self.answers) - 1)])


class SileroVad:
    """Silero VAD v5.1.2 (the pinned file in the model directory) through
    Pipecat's ONNX wrapper. Frames of any length are windowed into the
    model's 512-sample windows; the probability of the last complete window
    is returned."""

    def __init__(self, model_dir, manifest=None):
        entry = models.by_role(manifest)["vad"]
        self._model = pipecat_adapter.load_silero(
            models.path_of(model_dir, "vad", manifest), entry["sha256"]
        )
        self._pending = np.zeros(0, dtype=np.float32)
        self._last = 0

    def speech_probability(self, pcm: bytes) -> int:
        self._pending = np.concatenate([self._pending, to_float32(pcm)])
        window = pipecat_adapter.SILERO_WINDOW
        while len(self._pending) >= window:
            chunk, self._pending = self._pending[:window], self._pending[window:]
            self._last = int(round(pipecat_adapter.silero_probability(self._model, chunk) * 1000))
        return self._last

    def reset(self) -> None:
        pipecat_adapter.silero_reset(self._model)
        self._pending = np.zeros(0, dtype=np.float32)
        self._last = 0


class SmartTurnV3:
    """smart-turn v3 through Pipecat's local analyzer, over the bundled
    model whose sha256 must equal the `models.json` pin."""

    def __init__(self, manifest=None):
        entry = models.by_role(manifest)["smart_turn"]
        self._analyzer = pipecat_adapter.load_smart_turn(entry["sha256"])

    def probability(self, utterance_pcm: bytes) -> float:
        return pipecat_adapter.smart_turn_probability(self._analyzer, to_float32(utterance_pcm))

    def is_complete(self, utterance_pcm: bytes) -> bool:
        return self.probability(utterance_pcm) > 0.5
