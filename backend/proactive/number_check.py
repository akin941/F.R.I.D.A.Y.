"""The digest number check (Phase 12 D6, R2).

The spoken digest is composed by a model; this module refuses the model's
text when it says a number that is not in the probe facts. The digest is
spoken Turkish, so numbers written as words count as well as digits:

1. every digit run in the text is a number;
2. the text is tokenized (NFC, Turkish İ/ı lower-casing, split on
   non-letters, an apostrophe kept inside a token) and every token is
   looked up in a closed numeral table -- Turkish and English cardinals and
   ordinals; a Turkish stem followed by exactly one suffix from a closed
   list (case, ordinal, distributive; with or without an apostrophe) is a
   numeral too ("beşte", "dokuzda", "on'da", "ikişer"); "onay" is not
   (`ay` is not a listed suffix);
3. adjacent cardinal tokens combine the Turkish (and English) way --
   additive ("yirmi beş" = 25), multiplicative before `yüz`/`bin`
   (`hundred`/`thousand`) ("iki yüz" = 200) -- before comparing.

Every resulting value must be one of the allowed numbers (`allowed_numbers`
over the facts); otherwise the check fails and the caller falls back to the
template text.

Documented rules for ambiguous tokens (table-driven tests pin each):

- `bir` and `one` are **not** numerals (the indefinite article is far more
  common; an invented "bir onay" is the accepted residual, R2). The ordinal
  `birinci` / `first` is.
- The forms of the pronoun `o` that look like `on` + suffix -- `ona, onu,
  onun, onda, ondan, onlar...` -- are an explicit exception list (residual:
  "onda" = "at ten" is not checked). With an apostrophe ("on'da") the token
  is always the numeral.
- `yüzde` ("percent", always followed by the number itself, which is
  checked) is an exception, so "yüzde 85" is checked as 85 only.
- Otherwise `on` is **always** 10, as are the homographs `yüz` (face),
  `bin` (ride), `altı` (beneath), `yedi` (ate) -- even the English
  preposition "on": the check is conservative by design, because a false
  rejection only costs the template fallback, never a wrong number.
"""

import re
import unicodedata

# --- the closed numeral table ---------------------------------------------

TR_CARDINALS = {
    "sıfır": 0,
    "iki": 2,
    "üç": 3,
    "dört": 4,
    "beş": 5,
    "altı": 6,
    "yedi": 7,
    "sekiz": 8,
    "dokuz": 9,
    "on": 10,
    "yirmi": 20,
    "otuz": 30,
    "kırk": 40,
    "elli": 50,
    "altmış": 60,
    "yetmiş": 70,
    "seksen": 80,
    "doksan": 90,
    "yüz": 100,
    "bin": 1000,
}
# Stems that take suffixes: the cardinals, `bir` (only for its ordinal
# `birinci`) and the softened `dörd-` ("dörde", "dördüncü").
_TR_STEMS = {**TR_CARDINALS, "dörd": 4}
_TR_ORDINAL_ONLY_STEMS = {"bir": 1}

EN_CARDINALS = {
    "zero": 0,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
    "hundred": 100,
    "thousand": 1000,
}
EN_ORDINALS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
    "eleventh": 11,
    "twelfth": 12,
    "thirteenth": 13,
    "fourteenth": 14,
    "fifteenth": 15,
    "sixteenth": 16,
    "seventeenth": 17,
    "eighteenth": 18,
    "nineteenth": 19,
    "twentieth": 20,
    "thirtieth": 30,
    "fortieth": 40,
    "fiftieth": 50,
    "sixtieth": 60,
    "seventieth": 70,
    "eightieth": 80,
    "ninetieth": 90,
    "hundredth": 100,
    "thousandth": 1000,
}

# Closed suffix lists (one suffix per token).
CASE_SUFFIXES = frozenset(
    {
        # dative -(y)a/-(y)e
        "a",
        "e",
        "ya",
        "ye",
        # locative -da/-de/-ta/-te
        "da",
        "de",
        "ta",
        "te",
        # ablative -dan/-den/-tan/-ten
        "dan",
        "den",
        "tan",
        "ten",
        # accusative -(y)ı/-(y)i/-(y)u/-(y)ü
        "ı",
        "i",
        "u",
        "ü",
        "yı",
        "yi",
        "yu",
        "yü",
    }
)
DISTRIBUTIVE_SUFFIXES = frozenset({"er", "ar", "şer", "şar"})
# -(ı)ncı/-(i)nci/-(u)ncu/-(ü)ncü, optionally followed by one case suffix.
ORDINAL_SUFFIXES = frozenset({"ıncı", "inci", "uncu", "üncü", "ncı", "nci", "ncu", "ncü"})

# Explicit exceptions: never numerals without an apostrophe.
NOT_NUMERALS = frozenset(
    {
        # the article / "one"
        "bir",
        "one",
        # pronoun `o` forms that look like `on` + suffix
        "ona",
        "onu",
        "onun",
        "onda",
        "ondan",
        "onlar",
        "onları",
        "onlara",
        "onlarda",
        "onlardan",
        "onların",
        "onunla",
        # the percent word; the number after it is checked
        "yüzde",
    }
)

_APOSTROPHES = ("'", "’")
_TOKEN = re.compile(r"[a-zçğıöşüâîû]+(?:['’][a-zçğıöşüâîû]+)?")
_DIGITS = re.compile(r"\d+")
_TIME = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")

CARDINAL = "cardinal"
ORDINAL = "ordinal"


def turkish_lower(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "")
    return text.replace("I", "ı").replace("İ", "i").lower()


def _ordinal_rest(rest: str) -> bool:
    """`rest` is an ordinal suffix, optionally followed by one case suffix."""
    for suffix in ORDINAL_SUFFIXES:
        if rest == suffix:
            return True
        if rest.startswith(suffix) and rest[len(suffix) :] in CASE_SUFFIXES:
            return True
    return False


def parse_token(token: str):
    """A lower-cased token -> `(value, kind, suffixed)` or None."""
    stem, apostrophe, suffix = token, False, ""
    for mark in _APOSTROPHES:
        if mark in token:
            stem, suffix = token.split(mark, 1)
            apostrophe = True
            break
    if apostrophe:
        if stem in _TR_STEMS and (suffix in CASE_SUFFIXES or suffix in DISTRIBUTIVE_SUFFIXES):
            return _TR_STEMS[stem], CARDINAL, True
        if (stem in _TR_STEMS or stem in _TR_ORDINAL_ONLY_STEMS) and _ordinal_rest(suffix):
            return {**_TR_STEMS, **_TR_ORDINAL_ONLY_STEMS}[stem], ORDINAL, True
        return None
    if token in NOT_NUMERALS:
        return None
    if token in TR_CARDINALS:
        return TR_CARDINALS[token], CARDINAL, False
    if token in EN_CARDINALS:
        return EN_CARDINALS[token], CARDINAL, False
    if token in EN_ORDINALS:
        return EN_ORDINALS[token], ORDINAL, False
    # A Turkish stem plus exactly one listed suffix (longest stem first).
    for stem in sorted({**_TR_STEMS, **_TR_ORDINAL_ONLY_STEMS}, key=len, reverse=True):
        if not token.startswith(stem) or token == stem:
            continue
        rest = token[len(stem) :]
        value = _TR_STEMS.get(stem, _TR_ORDINAL_ONLY_STEMS.get(stem))
        if stem in _TR_STEMS and (rest in CASE_SUFFIXES or rest in DISTRIBUTIVE_SUFFIXES):
            return value, CARDINAL, True
        if _ordinal_rest(rest):
            return value, ORDINAL, True
    return None


def _combine(values):
    """Adjacent cardinals, the Turkish/English way: additive, and
    multiplicative before 100 / 1000."""
    total, current = 0, 0
    for value in values:
        if value == 100:
            current = (current or 1) * 100
        elif value == 1000:
            total += (current or 1) * 1000
            current = 0
        else:
            current += value
    return total + current


_JOINER = re.compile(r"^[\s-]+$")


def word_numbers(text: str) -> list[int]:
    """The numbers the words of `text` say, adjacent cardinals combined
    (adjacent: only whitespace or a hyphen between them, "twenty-five")."""
    lowered = turkish_lower(text)
    numbers, group = [], []
    previous_end = None

    def close():
        if group:
            numbers.append(_combine(group))
            group.clear()

    for match in _TOKEN.finditer(lowered):
        parsed = parse_token(match.group(0))
        if parsed is None:
            close()
            previous_end = None
            continue
        value, kind, suffixed = parsed
        if group and not _JOINER.match(lowered[previous_end : match.start()]):
            close()
        group.append(value)
        previous_end = match.end()
        if kind == ORDINAL or suffixed:
            close()
            previous_end = None
    close()
    return numbers


def digit_numbers(text: str) -> list[int]:
    return [int(run) for run in _DIGITS.findall(text or "")]


def numbers_in(text: str) -> list[int]:
    """Every number `text` says: digit runs and numeral words."""
    return digit_numbers(text) + word_numbers(text)


def allowed_numbers(*texts: str) -> set[int]:
    """The numbers present in the facts: every digit run, plus for an
    `HH:MM` time past noon its twelve-hour hour ("saat üçte" for 15:00)."""
    allowed = set()
    for text in texts:
        allowed.update(digit_numbers(text))
        for hour, _minute in _TIME.findall(text or ""):
            if int(hour) > 12:
                allowed.add(int(hour) - 12)
    return allowed


def invented_numbers(text: str, allowed: set[int]) -> list[int]:
    """The numbers in `text` that are not allowed (empty = the check passes)."""
    return [value for value in numbers_in(text) if value not in allowed]


def passes(text: str, allowed: set[int]) -> bool:
    return not invented_numbers(text, allowed)
