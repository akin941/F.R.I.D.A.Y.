"""WER scorer with Turkish text normalisation (Phase 6 brief A4, branch 3a).

A small in-repo word-level edit-distance scorer -- no speech or NLP
library. `normalize()` makes a reference and a hypothesis comparable:

1. Unicode NFC (a decomposed `I` + U+0307 becomes `İ`).
2. Case, with Turkish rules for Turkish text: `İ` -> `i` and `I` -> `ı`
   before the ordinary lower-casing (Python's `"İ".lower()` is `i` + a
   combining dot, and `"I".lower()` is `i`, both wrong for Turkish).
   English text (`language="en"`) uses the ordinary rule (`I` -> `i`).
3. Apostrophes are dropped and the suffix joined (`repo'yu` -> `repoyu`,
   `PR'ı` -> `prı`): Whisper writes the Turkish suffix apostrophe
   inconsistently, and the brief scores words, not orthography.
4. A hyphen between two letters is dropped and the parts joined
   (`e-posta` -> `eposta`, as Whisper often writes it); a dash with space
   around it is punctuation.
5. Turkish text only: digits become words (`numbers.spell_numbers`:
   integers, `10:30` -> `on buçuk`, `%15`, `₺`/`tl`, `3,5`), so `3` and
   `üç` score the same. English digits are compared as written.
6. Every other punctuation or symbol character becomes a space.

WER = (substitutions + deletions + insertions) / reference words, summed
over an utterance set (corpus WER), never averaged per utterance.
"""

import re
import unicodedata
from dataclasses import dataclass

from voice.eval.numbers import spell_numbers

# ASCII and typographic apostrophes, the modifier letter apostrophe and
# the two accent characters keyboards produce in their place.
APOSTROPHES = frozenset("'’‘ʼ`´")
_TURKISH_CASE = str.maketrans({"İ": "i", "I": "ı"})
TURKISH_LANGUAGES = frozenset({"tr", "az"})
# Hyphen-minus, hyphen and non-breaking hyphen between two letters (not
# digits: `Covid-19` stays two words, `covid on dokuz`).
_INTRA_WORD_HYPHEN = re.compile(r"(?<=[^\W\d_])[-\u2010\u2011](?=[^\W\d_])")


def fold_case(text, language="tr"):
    text = unicodedata.normalize("NFC", text)
    if language in TURKISH_LANGUAGES:
        text = text.translate(_TURKISH_CASE)
    return unicodedata.normalize("NFC", text.lower())


def normalize(text, language="tr"):
    """The comparable form of `text`: case-folded, punctuation-free words
    joined by single spaces."""
    folded = "".join(c for c in fold_case(text or "", language) if c not in APOSTROPHES)
    folded = _INTRA_WORD_HYPHEN.sub("", folded)
    if language in TURKISH_LANGUAGES:
        folded = spell_numbers(folded)
    out = []
    for char in folded:
        category = unicodedata.category(char)
        if category[0] in {"P", "S"} or char.isspace():
            out.append(" ")
        else:
            out.append(char)
    return " ".join("".join(out).split())


def words(text, language="tr"):
    return normalize(text, language).split()


@dataclass(frozen=True)
class WordErrors:
    reference_words: int
    substitutions: int
    deletions: int
    insertions: int

    @property
    def errors(self):
        return self.substitutions + self.deletions + self.insertions

    @property
    def wer(self):
        """`errors / reference words`. An empty reference scores 0.0 when the
        hypothesis is empty too and 1.0 otherwise (never a division by 0)."""
        if self.reference_words == 0:
            return 0.0 if self.insertions == 0 else 1.0
        return self.errors / self.reference_words

    def __add__(self, other):
        return WordErrors(
            self.reference_words + other.reference_words,
            self.substitutions + other.substitutions,
            self.deletions + other.deletions,
            self.insertions + other.insertions,
        )


NO_ERRORS = WordErrors(0, 0, 0, 0)


def align(reference, hypothesis):
    """Levenshtein alignment of two word lists -> `WordErrors`. On equal
    total cost the backtrace prefers a match/substitution, then a deletion,
    then an insertion, so the split between S/D/I is deterministic."""
    n, m = len(reference), len(hypothesis)
    # cost[i][j]: edits turning reference[:i] into hypothesis[:j].
    cost = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        cost[i][0] = i
    for j in range(1, m + 1):
        cost[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            diagonal = cost[i - 1][j - 1] + (reference[i - 1] != hypothesis[j - 1])
            cost[i][j] = min(diagonal, cost[i - 1][j] + 1, cost[i][j - 1] + 1)
    substitutions = deletions = insertions = 0
    i, j = n, m
    while i or j:
        if i and j and cost[i][j] == cost[i - 1][j - 1] + (reference[i - 1] != hypothesis[j - 1]):
            substitutions += reference[i - 1] != hypothesis[j - 1]
            i, j = i - 1, j - 1
        elif i and cost[i][j] == cost[i - 1][j] + 1:
            deletions += 1
            i -= 1
        else:
            insertions += 1
            j -= 1
    return WordErrors(n, substitutions, deletions, insertions)


def score(reference, hypothesis, language="tr"):
    """`WordErrors` of one utterance after `normalize()` on both sides."""
    return align(words(reference, language), words(hypothesis, language))


def corpus_errors(pairs, language="tr"):
    """Summed `WordErrors` over `(reference, hypothesis)` pairs."""
    total = NO_ERRORS
    for reference, hypothesis in pairs:
        total = total + score(reference, hypothesis, language)
    return total
