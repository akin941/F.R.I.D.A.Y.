"""`WakeGate` and the wake detectors (Phase 11 brief D8, D11).

Pre-wake audio exists only in the child's 2 s ring buffer. While wake mode
is armed, every capture frame is scored; a score at or above
`config.WAKE_THRESHOLD` (outside the refractory period) is a detection and
the utterance starts at the **first frame after** the detection frame --
no pre-roll (ADR-011 decision 2). Whether wake mode may arm at all is the
arming policy's call (D11, branch 5); this branch wires the gate and the
policy refuses it.

Detectors are on-device classifiers that emit only scores (D2): no text,
no tokens, no model call in the CLAUDE.md sense.
"""

from . import config, models


class WakeGate:
    def __init__(self, detector, clock, *, threshold=None, refractory_ms=None):
        self.detector = detector
        self.clock = clock
        self.threshold = config.WAKE_THRESHOLD if threshold is None else int(threshold)
        self.refractory_ms = (
            config.WAKE_REFRACTORY_MS if refractory_ms is None else int(refractory_ms)
        )
        self.last_detection_ms = None
        self.detections = 0

    def feed(self, pcm: bytes) -> bool:
        """Score one frame. True when it is a detection: the caller starts
        the utterance with the next frame."""
        score = int(self.detector.score(pcm))
        if score < self.threshold:
            return False
        now = self.clock.monotonic_ms()
        if self.last_detection_ms is not None and now - self.last_detection_ms < self.refractory_ms:
            return False
        self.last_detection_ms = now
        self.detections += 1
        return True

    def reset(self) -> None:
        self.detector.reset()


class ScriptedWakeDetector:
    """Test double: `scores` maps a frame index (0-based, counted over
    every `score` call) to a score in thousandths; other frames score 0.
    A callable `scores(index, pcm)` is accepted too."""

    def __init__(self, scores=None, *, model_sha256="0" * 64):
        self.scores = scores or {}
        self.model_sha256 = model_sha256
        self.calls = 0
        self.resets = 0

    def score(self, pcm: bytes) -> int:
        index = self.calls
        self.calls += 1
        if callable(self.scores):
            return int(self.scores(index, pcm))
        return int(self.scores.get(index, 0))

    def reset(self) -> None:
        self.resets += 1


class OpenWakeWordDetector:
    """openWakeWord over the pinned ONNX graphs (melspectrogram, embedding,
    `hey_friday`). Refuses to load unless every wake file in the model
    directory matches a non-empty pin (`models.wake_armable`)."""

    def __init__(self, model_dir, manifest=None):
        if not models.wake_armable(model_dir, manifest):
            raise models.ModelNotArmable("wake models missing, unpinned or modified")
        entries = models.by_role(manifest)
        from openwakeword.model import Model  # lazy: heavy, and never needed by the shell

        self.model_sha256 = entries["keyword"]["sha256"]
        self._keyword = str(entries["keyword"]["file"]).rsplit(".", 1)[0]
        self._model = Model(
            wakeword_models=[str(models.path_of(model_dir, "keyword", manifest))],
            melspec_model_path=str(models.path_of(model_dir, "melspectrogram", manifest)),
            embedding_model_path=str(models.path_of(model_dir, "embedding", manifest)),
            inference_framework="onnx",
        )

    def score(self, pcm: bytes) -> int:
        import numpy as np

        scores = self._model.predict(np.frombuffer(pcm, dtype="<i2"))
        value = max(scores.values()) if scores else 0.0
        return int(round(float(value) * 1000))

    def reset(self) -> None:
        self._model.reset()


class OpenWakeWordEvalDetector(OpenWakeWordDetector):
    """The FAR/FRR harness's detector (`friday-voice wake-eval`, D13): the
    pinned melspectrogram and embedding graphs of `model_dir` (refused
    unless both match their pins) and the keyword head under evaluation at
    `keyword_path` -- not necessarily pinned yet: the owner measures a newly
    trained `hey_friday` before pinning it. `model_sha256` is the head's
    content hash, the key the always-on gate is judged by (D11). Never used
    by `friday-voice run`, which loads only the pinned head."""

    def __init__(self, model_dir, keyword_path, manifest=None):
        from pathlib import Path

        model_dir = models.default_model_dir(manifest) if model_dir is None else model_dir
        states = models.verify(model_dir, ("melspectrogram", "embedding"), manifest)
        if any(state != "ok" for state in states.values()):
            raise models.ModelNotArmable("feature graphs missing, unpinned or modified")
        keyword_path = Path(keyword_path)
        if not keyword_path.is_file():
            raise models.ModelNotArmable("the keyword model file does not exist")
        from openwakeword.model import Model  # lazy: heavy

        self.model_sha256 = models.sha256_file(keyword_path)
        self._keyword = keyword_path.stem
        self._model = Model(
            wakeword_models=[str(keyword_path)],
            melspec_model_path=str(models.path_of(model_dir, "melspectrogram", manifest)),
            embedding_model_path=str(models.path_of(model_dir, "embedding", manifest)),
            inference_framework="onnx",
        )
