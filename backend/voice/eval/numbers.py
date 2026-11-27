"""Turkish digits -> words for the WER scorer (Phase 6 A4, branch 3a).

Whisper writes `3`, `10:30` or `%15` where a reference says `üç`,
`on buçuk` or `yüzde on beş`; both sides go through `spell_numbers()` so
either spelling scores the same. Deliberately small; it covers exactly:

- integers up to 999 999 999 999 (`250` -> `iki yüz elli`; `1000` ->
  `bin`, never `bir bin`; `100` -> `yüz`), with Turkish thousands dots
  (`1.000.000` -> `bir milyon`, `1.000,50` -> `bin virgül elli`); a
  digit run with a leading zero or longer than 12 digits is read digit by
  digit (`05` -> `sıfır beş`);
- a suffix written straight after the digits, a time, a percentage or a
  lira amount stays glued (`10da` -> `onda`, `10:30da` -> `on buçukda`;
  the apostrophe of `10'da` is dropped before this step). The suffix is
  not re-harmonised (`buçukda`, not `buçukta`): write such numbers as
  digits in the reference too, then both sides agree;
- decimals with a comma (`3,5` -> `üç virgül beş`);
- clock times `HH:MM` / `HH.MM` (hour <= 24, minute < 60): `10:30` -> `on
  buçuk`, `10:00` -> `on`, `09:15` -> `dokuz on beş`;
- percent before or after the number (`%15`, `15 %` -> `yüzde on beş`);
- the lira: `₺250`, `250₺`, `250 ₺` -> `iki yüz elli lira`, and the word
  `tl` -> `lira`.

Ordinals (`3.`), dates, ranges and other currencies are not covered:
write them in the reference the way Whisper is expected to (runbook §8,
manifest notes). The input is already case-folded (`scorer.fold_case`).
"""

import re

_ONES = ("", "bir", "iki", "üç", "dört", "beş", "altı", "yedi", "sekiz", "dokuz")
_TENS = ("", "on", "yirmi", "otuz", "kırk", "elli", "altmış", "yetmiş", "seksen", "doksan")
_DIGITS = ("sıfır",) + _ONES[1:]
_SCALES = ((10**9, "milyar"), (10**6, "milyon"), (10**3, "bin"))
MAX_DIGITS = 12

_NUM = r"\d+(?:,\d+)?"
_TIME = re.compile(r"(?<![\d,.])(\d{1,2})[:.](\d{2})(?![\d,]|\.\d)")
# A decimal comma may follow the last group (`1.000,50` -> `1000,50`).
_THOUSANDS = re.compile(r"(?<![\d.,])\d{1,3}(?:\.\d{3})+(?!\d|\.\d)")
_PERCENT_BEFORE = re.compile(rf"%\s*({_NUM})")
_PERCENT_AFTER = re.compile(rf"({_NUM})\s*%")
_LIRA_BEFORE = re.compile(rf"₺\s*({_NUM})")
_LIRA_AFTER = re.compile(rf"({_NUM})\s*₺")
_TL = re.compile(r"(?<!\w)tl(?!\w)")
_DECIMAL = re.compile(r"(\d+),(\d+)")
_INTEGER = re.compile(r"\d+")


def _below_thousand(n):
    hundreds, rest = divmod(n, 100)
    parts = []
    if hundreds:
        parts += ([] if hundreds == 1 else [_ONES[hundreds]]) + ["yüz"]
    tens, ones = divmod(rest, 10)
    parts += [w for w in (_TENS[tens], _ONES[ones]) if w]
    return parts


def integer_words(digits):
    """`"250"` -> `"iki yüz elli"`. Leading zeros or more than
    `MAX_DIGITS` digits: digit by digit."""
    if digits.startswith("0") and len(digits) > 1 or len(digits) > MAX_DIGITS:
        return " ".join(_DIGITS[int(d)] for d in digits)
    n = int(digits)
    if n == 0:
        return "sıfır"
    parts = []
    for scale, name in _SCALES:
        count, n = divmod(n, scale)
        if count:
            parts += ([] if count == 1 and scale == 1000 else _below_thousand(count)) + [name]
    parts += _below_thousand(n)
    return " ".join(parts)


def _time(match):
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 24 or minute > 59:
        return match.group(0)
    if minute == 0:
        return f" {hour}"
    if minute == 30:
        return f" {hour} buçuk"
    return f" {hour} {match.group(2)}"


def spell_numbers(text):
    text = _TIME.sub(_time, text)
    text = _THOUSANDS.sub(lambda m: m.group(0).replace(".", ""), text)
    text = _PERCENT_BEFORE.sub(r" yüzde \1", text)
    text = _PERCENT_AFTER.sub(r" yüzde \1", text)
    text = _LIRA_BEFORE.sub(r" \1 lira", text)
    text = _LIRA_AFTER.sub(r" \1 lira", text)
    text = _TL.sub("lira", text)
    text = _DECIMAL.sub(r"\1 virgül \2", text)
    return _INTEGER.sub(lambda m: integer_words(m.group(0)), text)
