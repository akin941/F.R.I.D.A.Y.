"""The self-trigger rule on the echo-cancelled signal (Phase 11 brief D10,
ADR-015 decision 4).

In Phase 11 the wake detector and the barge-in VAD are fed the AEC-cleaned
signal while Friday speaks, instead of being suppressed (half-duplex). Two
filters stand behind them, both applied to the transcript before any chat
turn is posted:

1. **Window rule.** A wake detection while playback is active, or within
   `config.SELF_TRIGGER_MS` (1.5 s, the browser's `VOICE_SELF_TRIGGER_MS`)
   after playback ended, marks its utterance *suspect* (a barge-in always
   starts during playback, so it is suspect too). A suspect utterance whose
   transcript matches recent TTS text is discarded.
2. **Content rule.** Every transcript (wake-initiated, push-to-talk or
   barge-in) that matches the sentences Friday spoke in the last
   `SPOKEN_MEMORY_MS` (60 s) is discarded, whatever the timing. Stricter
   than the browser and free: Friday never needs to hear its own words.

Discarded means: no chat turn, nothing spoken, the `discarded_self`
counter (and the day's `wake_discards_today`) incremented; the session
stays open.

**One implementation of "matches".** `normalize_speech`,
`matches_recent_speech` and `within_self_trigger_window` are a port of
`frontend/src/lib/voice/self-trigger.ts`; the shared vectors
`node/friday-voice/vectors/self_trigger.json` run in pytest and in vitest
(`self-trigger.vectors.test.ts`), so the browser and the Mac can never
disagree on what counts as an echo.

Spoken text is held in memory only, for 60 s, and is never logged.
"""

import unicodedata
from collections import deque
from dataclasses import dataclass

from . import config

# How long spoken sentences are remembered (self-trigger.ts SPOKEN_MEMORY_MS).
SPOKEN_MEMORY_MS = 60_000
DEFAULT_WAKE_PHRASE = "Hey Friday"
# Enough for a long reply spoken within a minute; older entries fall out first.
SPOKEN_MEMORY_MAX = 200
# Token overlap that makes a transcript "mostly a repeat" (self-trigger.ts).
OVERLAP_NUMERATOR = 3
OVERLAP_DENOMINATOR = 5  # 0.6

REASON_WINDOW = "window"
REASON_CONTENT = "content"


def _turkish_lower(text: str) -> str:
    """`toLocaleLowerCase("tr-TR")`: dotted and dotless I keep their dot."""
    text = text.replace("İ", "i").replace("İ", "i").replace("I", "ı")
    return text.lower()


def normalize_speech(text: str) -> str:
    """Turkish lower-casing, NFC, everything but letters, digits and
    whitespace to a space, whitespace collapsed (`normalizeSpeech`)."""
    text = unicodedata.normalize("NFC", _turkish_lower(text))
    kept = []
    for char in text:
        category = unicodedata.category(char)
        if category[0] in ("L", "N") or char.isspace():
            kept.append(char)
        else:
            kept.append(" ")
    return " ".join("".join(kept).split())


def _strip_wake_phrase(text: str, phrase: str) -> str:
    normalized = normalize_speech(phrase)
    if normalized and text.startswith(normalized):
        return text[len(normalized) :].strip()
    return text


def matches_recent_speech(transcript, recent, wake_phrase=DEFAULT_WAKE_PHRASE) -> bool:
    """True when the transcript is (mostly) a repeat of something Friday
    said recently (`matchesRecentSpeech`): containment either way, or at
    least 60 % of the heard tokens (two or more) among the said ones.
    `recent` holds strings or objects with a `text` attribute/key."""
    heard = _strip_wake_phrase(normalize_speech(transcript), wake_phrase)
    if not heard:
        return False
    heard_tokens = heard.split(" ")
    for record in recent:
        text = record if isinstance(record, str) else _text_of(record)
        said = _strip_wake_phrase(normalize_speech(text), wake_phrase)
        if not said:
            continue
        if heard in said or said in heard:
            return True
        said_tokens = set(said.split(" "))
        overlap = sum(1 for token in heard_tokens if token in said_tokens)
        if (
            len(heard_tokens) >= 2
            and overlap * OVERLAP_DENOMINATOR >= len(heard_tokens) * OVERLAP_NUMERATOR
        ):
            return True
    return False


def _text_of(record) -> str:
    if isinstance(record, dict):
        return str(record.get("text") or "")
    return str(getattr(record, "text", "") or "")


def within_self_trigger_window(detected_at, playback_ended_at, window_ms=None) -> bool:
    """The detection came within the window after playback ended
    (`withinSelfTriggerWindow`; a detection before the end is not "after")."""
    if playback_ended_at is None:
        return False
    window = config.SELF_TRIGGER_MS if window_ms is None else int(window_ms)
    delta = detected_at - playback_ended_at
    return 0 <= delta <= window


@dataclass(frozen=True)
class Spoken:
    text: str
    at: int  # monotonic ms


class SelfTriggerFilter:
    """The node's two rules over one memory of what Friday spoke.

    The pipeline tells it what is spoken (`spoken`) and when playback runs
    (`playback_started` / `playback_ended`); it asks `suspect_at(now)` when
    a wake fires or a barge-in starts, and `check(transcript, suspect)`
    before posting a turn. Pure and clock-injected."""

    def __init__(self, clock, *, wake_phrase=DEFAULT_WAKE_PHRASE, enabled=True):
        self.clock = clock
        self.wake_phrase = wake_phrase
        self.enabled = bool(enabled)
        self._spoken = deque(maxlen=SPOKEN_MEMORY_MAX)
        self.playing = False
        self.playback_ended_at = None
        self.discarded = {REASON_WINDOW: 0, REASON_CONTENT: 0}

    @property
    def discarded_self(self) -> int:
        return self.discarded[REASON_WINDOW] + self.discarded[REASON_CONTENT]

    # --- what the pipeline reports ---

    def spoken(self, text) -> None:
        self._prune()
        if text:
            self._spoken.append(Spoken(str(text), self.clock.monotonic_ms()))

    def playback_started(self) -> None:
        self.playing = True

    def playback_ended(self) -> None:
        self.playing = False
        self.playback_ended_at = self.clock.monotonic_ms()

    # --- the rules ---

    def _prune(self):
        horizon = self.clock.monotonic_ms() - SPOKEN_MEMORY_MS
        while self._spoken and self._spoken[0].at < horizon:
            self._spoken.popleft()

    def recent(self) -> list:
        self._prune()
        return [entry.text for entry in self._spoken]

    def suspect_at(self, at_ms) -> bool:
        """Window rule: a detection while playback is active or within
        `SELF_TRIGGER_MS` after it ended."""
        return self.playing or within_self_trigger_window(at_ms, self.playback_ended_at)

    def check(self, transcript, *, suspect=False):
        """The reason to discard the transcript (`window` or `content`), or
        None to let the turn through. Counts every discard."""
        if not self.enabled:
            return None
        if not matches_recent_speech(transcript, self.recent(), self.wake_phrase):
            return None
        reason = REASON_WINDOW if suspect else REASON_CONTENT
        self.discarded[reason] += 1
        return reason
