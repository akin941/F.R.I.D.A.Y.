"""Branch 3a review follow-up: Turkish digits -> words (both sides of the
WER comparison) and hyphenated words."""

import pytest

from voice.eval.numbers import integer_words, spell_numbers
from voice.eval.scorer import normalize, score


@pytest.mark.parametrize(
    ("digits", "words"),
    [
        ("0", "sıfır"),
        ("7", "yedi"),
        ("10", "on"),
        ("15", "on beş"),
        ("100", "yüz"),
        ("101", "yüz bir"),
        ("250", "iki yüz elli"),
        ("1000", "bin"),
        ("1100", "bin yüz"),
        ("2026", "iki bin yirmi altı"),
        ("100000", "yüz bin"),
        ("1000000", "bir milyon"),
        ("3000000000", "üç milyar"),
        ("05", "sıfır beş"),
        ("1234567890123", "bir iki üç dört beş altı yedi sekiz dokuz sıfır bir iki üç"),
    ],
)
def test_integer_words(digits, words):
    assert integer_words(digits) == words


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("3 elma", "üç elma"),
        ("10'da", "onda"),
        ("saat 10:30", "saat on buçuk"),
        ("saat 10.30", "saat on buçuk"),
        ("10:00", "on"),
        ("09:15", "dokuz on beş"),
        ("25:00", "yirmi beş sıfır sıfır"),  # not a time: digits only
        ("%15 indirim", "yüzde on beş indirim"),
        ("15% indirim", "yüzde on beş indirim"),
        ("%3,5", "yüzde üç virgül beş"),
        ("₺250", "iki yüz elli lira"),
        ("250₺", "iki yüz elli lira"),
        ("250 TL", "iki yüz elli lira"),
        ("1.000.000 kişi", "bir milyon kişi"),
        ("1.000", "bin"),
        ("1.000,50 TL", "bin virgül elli lira"),
        ("12.500,75", "on iki bin beş yüz virgül yetmiş beş"),
        ("₺1.000,50", "bin virgül elli lira"),
        ("3,5 kilo", "üç virgül beş kilo"),
    ],
)
def test_turkish_numbers_normalise(text, expected):
    assert normalize(text) == expected


def test_digits_and_words_score_the_same():
    assert score("üç elma ve on beş armut", "3 elma ve 15 armut").errors == 0
    assert score("toplantı on buçukta", "Toplantı 10:30'da").errors == 1  # buçukda
    assert score("toplantı 10:30'da", "Toplantı 10:30'da").errors == 0
    assert score("yüzde on beş", "%15").errors == 0


def test_english_digits_are_compared_as_written():
    assert normalize("I have 3 cats", "en") == "i have 3 cats"


def test_hyphens():
    assert normalize("e-posta gönder") == "eposta gönder"
    assert score("e-posta gönder", "eposta gönder").errors == 0
    assert normalize("Covid-19 aşısı") == "covid on dokuz aşısı"
    assert normalize("Ali - Veli — Ayşe") == "ali veli ayşe"


def test_spell_numbers_leaves_text_without_digits_alone():
    assert spell_numbers("merhaba dünya") == "merhaba dünya"
