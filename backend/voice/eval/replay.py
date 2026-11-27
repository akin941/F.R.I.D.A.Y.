"""The recorded-audio replay harness (Phase 6 brief A4/A5, branch 3a).

Golden WAVs go through the same stages as a spoken turn, each behind a
port so CI runs it with fakes and the owner's Mac with real models:

    capture  -> 20 ms PCM16 frames (what the browser worklet hands on)
    segment  -> an energy VAD: is there speech, where does it end, and when
                would end-of-turn fire (`VOICE_FINISH_MS` after it)
    stt      -> the transcript (gateway `voice.eval_stt`, one ModelUsage)
    reply    -> the sentence to speak; by default the transcript's first
                sentence (an echo: the eval measures speech, not the LLM)
    tts      -> the first sentence's audio (gateway `voice.eval_tts`)

Timings per clip: `stt_ms` and `tts_first_ms` are the gateway's own
latencies (the same numbers `VoiceTurnTrace` stores for live turns),
`turn_ms` the wall clock from STT start to the first sentence's audio,
`end_of_turn_ms` the finish window in audio time. The LLM stage of A5's
table is not replayed here; the live end-to-end figure comes from
`VoiceTurnTrace.playback_start_ms` (`voice_eval --traces`).

The latency baseline is the p50 of each stage; a later run whose p50 is
more than 20% above it fails (`REGRESSION_LIMIT`).
"""

import io
import json
import math
import os
import re
import struct
import time
import wave
from dataclasses import dataclass, field

from ai_core.audio import STT_SAMPLE_RATE, InvalidAudio, normalize_tts_wav, parse_stt_input
from ai_core.exceptions import BudgetExceeded, GatewayError, NoProviderAvailable
from ai_core.gateway import InferenceGateway
from voice.eval.scorer import score

PURPOSE_EVAL_STT = "voice.eval_stt"
PURPOSE_EVAL_TTS = "voice.eval_tts"
FRAME_MS = 20
# The browser's default end-of-speech finish window (NEXT_PUBLIC_VOICE_FINISH_MS).
FINISH_MS = 900
# RMS of a 20 ms PCM16 frame above which it counts as speech (about -40 dBFS).
SPEECH_RMS = 330
LATENCY_STAGES = ("stt_ms", "tts_first_ms", "turn_ms")
REGRESSION_LIMIT = 0.20
BASELINE_VERSION = 1
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


class ReplayAborted(Exception):
    """A run-wide failure (no eligible provider, budget exhausted): every
    further clip would fail the same way."""


# --- capture / segment -------------------------------------------------------


def capture_frames(wav):
    """D3 input (16 kHz mono PCM16) -> 20 ms PCM frames. Raises InvalidAudio."""
    parse_stt_input(wav, max_seconds=60)
    pcm = _pcm_payload(wav)
    size = STT_SAMPLE_RATE * FRAME_MS // 1000 * 2
    return [pcm[i : i + size] for i in range(0, len(pcm) - size + 1, size)]


def _pcm_payload(wav):
    with wave.open(io.BytesIO(wav), "rb") as reader:
        return reader.readframes(reader.getnframes())


def frame_rms(frame):
    count = len(frame) // 2
    if not count:
        return 0.0
    samples = struct.unpack(f"<{count}h", frame[: count * 2])
    return math.sqrt(sum(s * s for s in samples) / count)


@dataclass(frozen=True)
class Segment:
    has_speech: bool
    speech_start_ms: int = 0
    speech_end_ms: int = 0
    end_of_turn_ms: int = 0  # audio time at which end-of-turn fires

    @property
    def speech_ms(self):
        return self.speech_end_ms - self.speech_start_ms


def segment(frames, *, threshold=SPEECH_RMS, finish_ms=FINISH_MS):
    speech = [i for i, f in enumerate(frames) if frame_rms(f) >= threshold]
    if not speech:
        return Segment(False)
    start, end = speech[0] * FRAME_MS, (speech[-1] + 1) * FRAME_MS
    return Segment(True, start, end, end + finish_ms)


def first_sentence(text, max_chars=400):
    text = " ".join((text or "").split())
    if not text:
        return ""
    return _SENTENCE_END.split(text, maxsplit=1)[0][:max_chars]


# --- ports -------------------------------------------------------------------


class GatewaySTT:
    """`voice.eval_stt` through the InferenceGateway: one ModelUsage row per
    clip, in the owner's personal workspace, `context_report` carrying the
    run id and condition only (never audio, never text)."""

    def __init__(self, *, actor, workspace, run_id, language, provider=None):
        self.actor, self.workspace, self.run_id = actor, workspace, run_id
        self.language, self.provider = language, provider
        self.providers = set()

    def __call__(self, wav, *, condition):
        result = InferenceGateway().transcribe(
            PURPOSE_EVAL_STT,
            wav,
            content_type="audio/wav",
            params={"language": self.language},
            actor=self.actor,
            workspace=self.workspace,
            context_report={"voice_eval_run": self.run_id, "condition": condition},
            budget_session=f"voice_eval:{self.run_id}",
            only_provider=self.provider,
        )
        if result.usage.provider_id:
            self.providers.add(result.usage.provider.name)
        return result.text, result.usage.latency_ms


class GatewayTTS:
    """`voice.eval_tts` through the InferenceGateway (A2 language route
    unless `provider` pins one row); the audio must pass D3's output rule."""

    def __init__(self, *, actor, workspace, run_id, provider=None, max_seconds=60):
        self.actor, self.workspace, self.run_id = actor, workspace, run_id
        self.provider, self.max_seconds = provider, max_seconds
        self.providers = set()

    def __call__(self, text, *, language, condition=""):
        result = InferenceGateway().synthesize(
            PURPOSE_EVAL_TTS,
            text,
            params={"language": language},
            actor=self.actor,
            workspace=self.workspace,
            context_report={"voice_eval_run": self.run_id, "condition": condition},
            budget_session=f"voice_eval:{self.run_id}",
            only_provider=self.provider,
        )
        if result.usage.provider_id:
            self.providers.add(result.usage.provider.name)
        wav, _info = normalize_tts_wav(result.audio, max_seconds=self.max_seconds)
        return wav, result.usage.latency_ms


def echo_reply(transcript):
    return first_sentence(transcript)


# --- the harness -------------------------------------------------------------


@dataclass
class ClipResult:
    file: str
    condition: str
    language: str
    reference_words: int = 0
    substitutions: int = 0
    deletions: int = 0
    insertions: int = 0
    hypothesis: str = ""
    has_speech: bool = True
    speech_ms: int = 0
    end_of_turn_ms: int | None = None
    stt_ms: int | None = None
    tts_first_ms: int | None = None
    turn_ms: int | None = None
    error: str = ""

    @property
    def errors(self):
        return self.substitutions + self.deletions + self.insertions

    def as_dict(self):
        return {
            "file": self.file,
            "condition": self.condition,
            "reference_words": self.reference_words,
            "substitutions": self.substitutions,
            "deletions": self.deletions,
            "insertions": self.insertions,
            "wer": round(self.errors / self.reference_words, 4) if self.reference_words else 0.0,
            "hypothesis": self.hypothesis,
            "has_speech": self.has_speech,
            "speech_ms": self.speech_ms,
            "end_of_turn_ms": self.end_of_turn_ms,
            "stt_ms": self.stt_ms,
            "tts_first_ms": self.tts_first_ms,
            "turn_ms": self.turn_ms,
            "error": self.error,
        }


@dataclass
class Pipeline:
    stt: object
    tts: object = None  # None: STT only
    reply: object = echo_reply
    clock: object = field(default=time.monotonic)
    finish_ms: int = FINISH_MS


def replay_clip(pipeline, entry, wav):
    """One golden clip through capture/segment/STT/(reply/TTS). Per-clip
    failures are recorded on the result (the clip then scores as an empty
    hypothesis: every reference word a deletion); run-wide ones raise
    `ReplayAborted`."""
    result = ClipResult(entry.file, entry.condition, entry.language)
    try:
        seg = segment(capture_frames(wav), finish_ms=pipeline.finish_ms)
    except InvalidAudio as exc:
        result.error = f"invalid_audio:{exc.reason}"
        seg = None
    hypothesis = ""
    if seg is not None:
        result.has_speech, result.speech_ms = seg.has_speech, seg.speech_ms
        result.end_of_turn_ms = pipeline.finish_ms if seg.has_speech else None
        started = pipeline.clock()
        try:
            hypothesis, result.stt_ms = pipeline.stt(wav, condition=entry.condition)
            sentence = pipeline.reply(hypothesis) if pipeline.tts else ""
            if sentence:
                _audio, result.tts_first_ms = pipeline.tts(
                    sentence, language=entry.language, condition=entry.condition
                )
                result.turn_ms = int(round((pipeline.clock() - started) * 1000))
        except (NoProviderAvailable, BudgetExceeded) as exc:
            raise ReplayAborted(exc.error_type) from exc
        except GatewayError as exc:
            result.error = exc.error_type
        except InvalidAudio as exc:
            result.error = f"invalid_tts_audio:{exc.reason}"
    errors = score(entry.reference, hypothesis, entry.language)
    result.reference_words = errors.reference_words
    result.substitutions = errors.substitutions
    result.deletions = errors.deletions
    result.insertions = errors.insertions
    result.hypothesis = hypothesis
    return result


def replay(pipeline, clips):
    """`[(Entry, wav bytes)]` -> `[ClipResult]`, in order."""
    return [replay_clip(pipeline, entry, wav) for entry, wav in clips]


# --- latency statistics and the baseline ---------------------------------------


def percentile(values, fraction):
    """Linear-interpolated percentile (`fraction` in [0, 1]); None if empty."""
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    position = (len(values) - 1) * fraction
    low = math.floor(position)
    high = min(low + 1, len(values) - 1)
    value = values[low] + (values[high] - values[low]) * (position - low)
    return int(round(value))


def latency_stats(rows, stages):
    """`{stage: {"n", "p50", "p95"}}` over dict rows (or objects)."""
    stats = {}
    for stage in stages:
        values = [
            row.get(stage) if isinstance(row, dict) else getattr(row, stage, None) for row in rows
        ]
        values = [v for v in values if v is not None]
        stats[stage] = {
            "n": len(values),
            "p50": percentile(values, 0.5),
            "p95": percentile(values, 0.95),
        }
    return stats


def make_baseline(stats, *, run_id, created_at):
    return {
        "version": BASELINE_VERSION,
        "run_id": run_id,
        "created_at": created_at,
        "p50": {stage: s["p50"] for stage, s in stats.items() if s["p50"] is not None},
    }


def compare_to_baseline(stats, baseline, limit=REGRESSION_LIMIT):
    """`[(stage, baseline p50, current p50)]` for every stage whose p50 is
    more than `limit` above the stored one (A5's nightly rule)."""
    regressions = []
    for stage, stored in (baseline.get("p50") or {}).items():
        current = (stats.get(stage) or {}).get("p50")
        if current is None or not isinstance(stored, int | float) or stored <= 0:
            continue
        if current > stored * (1 + limit):
            regressions.append((stage, stored, current))
    return regressions


def read_baseline(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as handle:
        raw = handle.read(64 * 1024)
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError(f"{path}: not a valid latency baseline") from None
    if not isinstance(data, dict) or data.get("version") != BASELINE_VERSION:
        raise ValueError(f"{path}: not a version-{BASELINE_VERSION} latency baseline")
    return data
