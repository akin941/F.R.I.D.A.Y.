"""Per-sentence language routing for TTS (Phase 11 brief D8).

A small deterministic detector picks `tr` or `en` for each reply sentence
so Friday's speech endpoint can route it (`VOICE_TTS_ROUTES` on the
server picks the engine and the voice). No model, no network:

1. any Turkish-only letter (ç ğ ı ö ş ü, İ) -> `tr`;
2. else the sentence's Turkish and English stop words are counted; the
   larger count wins;
3. a tie (no stop word at all, or as many of each) -> the fallback: the
   language of the owner's transcript, else `tr`.

Vectors: `node/friday-voice/vectors/language.json` (tests/test_language.py).
"""

import re

LANGUAGES = ("tr", "en")
DEFAULT_LANGUAGE = "tr"

TURKISH_LETTERS = frozenset("çğıöşüÇĞİÖŞÜ")

TURKISH_STOP_WORDS = frozenset(
    """
    ve ile bir bu şu o da de mi mı mu mü ne için ama fakat çünkü gibi kadar daha çok
    en var yok değil ben sen biz siz onlar bana sana ona bunu şunu onu bugün yarın
    şimdi sonra önce evet hayır tamam lütfen olarak olan oldu olur ise ki hem ya veya
    nasıl neden nerede hangi kim her hiç bile sadece artık saat dakika
    """.split()
)

ENGLISH_STOP_WORDS = frozenset(
    """
    the a an and or but of to in on at for with from by is are was were be been
    it this that these those you your we our they their he she his her i me my
    what which who when where why how not no yes do does did have has had will
    would can could should there here today tomorrow now then about just please
    """.split()
)

_WORD = re.compile(r"[^\W\d_]+")


def normalise(language) -> str:
    """A language hint (e.g. `tr-TR`, `en`, `turkish`) -> `tr` | `en` | ``."""
    value = (language or "").strip().lower()
    if value.startswith("tr") or value == "turkish":
        return "tr"
    if value.startswith("en") or value == "english":
        return "en"
    return ""


def detect(sentence: str, fallback: str = "") -> str:
    """`tr` or `en` for one sentence (see the module docstring)."""
    fallback = normalise(fallback) or DEFAULT_LANGUAGE
    if any(char in TURKISH_LETTERS for char in sentence):
        return "tr"
    # Plain lower-casing for the stop-word count: with no Turkish letter in
    # the sentence an "I" is the English pronoun, not a dotless ı.
    words = _WORD.findall(sentence.lower())
    turkish = sum(word in TURKISH_STOP_WORDS for word in words)
    english = sum(word in ENGLISH_STOP_WORDS for word in words)
    if turkish > english:
        return "tr"
    if english > turkish:
        return "en"
    return fallback
