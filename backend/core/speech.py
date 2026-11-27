"""Shared speech/text matching helpers (Phase 13 D16). Standard library only.

Moved from `assistant/wake_routes.py` (which re-exports `normalize_speech`
and `match_key`, so the shared vectors in `docs/voice/wake-route-vectors.json`
keep passing through both import paths) so that `nodes` and `connectors` can
use the one normalizer without importing `assistant`.

- `normalize_speech(text)`: the Python port of
  `frontend/src/lib/voice/self-trigger.ts` `normalizeSpeech` (NFC, Turkish
  lower-casing, every character that is not a letter, a digit or a space
  becomes a space, whitespace collapsed).
- `match_key(text)`: the comparison form of a normalized phrase (dotless ı
  folded to i).
- `phrase_key(text)`: `match_key(normalize_speech(text))`, the stored key
  of an owner-set label (a room's `key`, a node's `name_key`).
- `find_mentions(text, vocabulary)`: whole-word mentions of the owner's
  labels in `text`, longest match first, non-overlapping. The last word of
  a label may carry a closed set of Turkish case suffixes ("Masaüstünde",
  "salondaki", "çalışma odasındaki", "Mac'te"), never anything fuzzier.
- `match_room(text, rooms)`: the rooms mentioned in **locative** form
  ("salonda", "salondaki", "çalışma odasındaki", "Salon'daki"), as a
  room qualifier of something else in the sentence.

No model call, no I/O: pure functions of their arguments.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


def _turkish_lower(text: str) -> str:
    # `toLocaleLowerCase("tr-TR")`: dotted capital I -> i, dotless capital
    # I -> dotless ı, then the ordinary lower-casing.
    return text.replace("İ", "i").replace("I", "ı").lower()


def normalize_speech(text: str) -> str:
    """`self-trigger.ts` `normalizeSpeech`, ported: NFC, Turkish lower
    case, NFC again, non-letter/digit/space characters to spaces,
    whitespace collapsed."""
    text = unicodedata.normalize("NFC", str(text or ""))
    text = unicodedata.normalize("NFC", _turkish_lower(text))
    kept = []
    for char in text:
        if char.isspace():
            kept.append(" ")
        elif unicodedata.category(char)[0] in ("L", "N"):
            kept.append(char)
        else:
            kept.append(" ")
    return " ".join("".join(kept).split())


def match_key(text: str) -> str:
    """The comparison form of a normalized phrase: dotless ı folded to i.
    An all-caps transcript ("HEY FRIDAY", "BRIEFING") lower-cases the
    Turkish way to "frıday" / "brıefıng"; the closed phrase tables are
    small enough that folding costs no real collision, and every comparison
    (wake phrase, fillers, routes, aliases, clashes, labels) uses it."""
    return text.replace("ı", "i")


def phrase_key(text: str) -> str:
    """The stored comparison key of an owner-set label."""
    return match_key(normalize_speech(text))


# --- Turkish case suffixes (closed sets, already `match_key`-folded) ---

# Locative (-da/-de/-ta/-te), with the pronominal buffer n after a
# possessive ("odası" -> "odasında"), optionally followed by the relative
# -ki ("salondaki"). ı is folded to i by `match_key`, so "ında" etc. are
# covered by the "i" variants.
_LOCATIVE = ("da", "de", "ta", "te", "nda", "nde")
LOCATIVE_SUFFIXES = frozenset(_LOCATIVE + tuple(s + "ki" for s in _LOCATIVE))

# Every other case ending a label may carry in a sentence: dative,
# accusative, genitive, ablative, instrumental, with their buffer
# consonants. Used by `find_mentions` only ("MacBook'a", "Masaüstünü",
# "Masaüstünden").
_OTHER_CASES = (
    # dative
    "a",
    "e",
    "ya",
    "ye",
    "na",
    "ne",
    # accusative
    "i",
    "u",
    "ü",
    "yi",
    "yu",
    "yü",
    "ni",
    "nu",
    "nü",
    # genitive
    "in",
    "un",
    "ün",
    "nin",
    "nun",
    "nün",
    # ablative
    "dan",
    "den",
    "tan",
    "ten",
    "ndan",
    "nden",
    # instrumental
    "la",
    "le",
    "yla",
    "yle",
)
CASE_SUFFIXES = LOCATIVE_SUFFIXES | frozenset(_OTHER_CASES)

# Final-consonant softening before a vowel-initial suffix
# ("kitap" -> "kitabı", "ağaç" -> "ağacı", "tablet" -> "tableti" is not
# softened in practice, so both forms are accepted).
_SOFTENING = {"p": "b", "ç": "c", "t": "d", "k": "ğ"}


def _stems(word: str) -> tuple[str, ...]:
    """The forms the last word of a label may take before a suffix."""
    if word and word[-1] in _SOFTENING:
        return (word, word[:-1] + _SOFTENING[word[-1]])
    return (word,)


def _suffix_of(token: str, word: str, suffixes: frozenset) -> str | None:
    """The suffix `token` adds to `word` ("" for an exact match), or None
    when `token` is not `word` (+ one suffix of `suffixes`)."""
    if token == word:
        return ""
    for stem in _stems(word):
        if token.startswith(stem) and token[len(stem) :] in suffixes:
            return token[len(stem) :]
    return None


@dataclass(frozen=True)
class Mention:
    """One label found in a text: the label phrase as given in the
    vocabulary, its target, the token span `[start, end)` and the case
    suffix the last word carried ("" when none, or a separate token after
    an apostrophe: "Mac'te" -> tokens "mac", "te")."""

    phrase: str
    target: Any
    start: int
    end: int
    suffix: str


def _tokens(text: str) -> list[str]:
    return [match_key(token) for token in normalize_speech(text).split()]


def _match_at(tokens, index, words, suffixes):
    """Match `words` at `tokens[index]`; returns (end, suffix) or None. The
    suffix may be glued to the last word or, after an apostrophe, be the
    next token on its own."""
    if index + len(words) > len(tokens):
        return None
    for offset, word in enumerate(words[:-1]):
        if tokens[index + offset] != word:
            return None
    last = index + len(words) - 1
    suffix = _suffix_of(tokens[last], words[-1], suffixes)
    if suffix is None:
        return None
    end = last + 1
    if suffix == "" and end < len(tokens) and tokens[end] in suffixes:
        # "Mac'te" / "Salon'daki": the apostrophe split the suffix off.
        return end + 1, tokens[end]
    return end, suffix


def _find(text, vocabulary, suffixes, *, require_suffix=False):
    tokens = _tokens(text)
    entries = []
    for phrase, target in vocabulary:
        words = tuple(_tokens(phrase))
        if words:
            entries.append((words, phrase, target))
    # Longest label first, so "çalışma odası" beats "oda" and "medya
    # bilgisayarı" beats "bilgisayar".
    entries.sort(key=lambda entry: (-len(entry[0]), -sum(map(len, entry[0]))))
    taken = [False] * len(tokens)
    found = []
    for index in range(len(tokens)):
        if taken[index]:
            continue
        best = None
        for words, phrase, target in entries:
            if best is not None and len(words) < best[0]:
                break
            hit = _match_at(tokens, index, words, suffixes)
            if hit is None:
                continue
            end, suffix = hit
            if require_suffix and suffix == "":
                continue
            if any(taken[index:end]):
                continue
            if best is None:
                best = (len(words), [])
            best[1].append(Mention(phrase, target, index, end, suffix))
        if best is None:
            continue
        for mention in best[1]:
            found.append(mention)
        end = max(mention.end for mention in best[1])
        for position in range(index, end):
            taken[position] = True
    return found


def find_mentions(text: str, vocabulary: Iterable[tuple[str, Any]]) -> list[Mention]:
    """Whole-word mentions of the vocabulary's phrases in `text`, in text
    order. `vocabulary` is an iterable of `(phrase, target)` pairs (a node
    name and its node, a role word and its role, a room name and its room).
    The longest phrase wins at a position; two equally long phrases that
    match the same words are both returned (the caller sees the ambiguity).
    The last word may carry one Turkish case suffix (`CASE_SUFFIXES`)."""
    return _find(text, vocabulary, CASE_SUFFIXES)


def match_room(text: str, rooms: Iterable[tuple[str, Any]]) -> list[Mention]:
    """The rooms mentioned in locative form in `text` ("salonda",
    "salondaki", "çalışma odasındaki", "Salon'daki"), in text order. A bare
    room name ("salon") is not a locative mention. `rooms` is an iterable
    of `(name_or_alias, room)` pairs."""
    return _find(text, rooms, LOCATIVE_SUFFIXES, require_suffix=True)
