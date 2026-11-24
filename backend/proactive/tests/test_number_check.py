"""Phase 12 D6 / R2: the digest number check, table-driven -- digits,
Turkish and English numeral words, suffixed and ordinal forms, the
combining rule and the documented ambiguous-token rules."""

import pytest

from proactive import number_check as nc

# (text, the numbers it says) -- one row per rule.
VECTORS = [
    # digits
    ("5 onay", [5]),
    ("saat 10:00'da", [10, 0]),
    ("%85", [85]),
    # Turkish cardinals (every table entry)
    *[(word, [value]) for word, value in nc.TR_CARDINALS.items()],
    # English cardinals (every table entry)
    *[(word, [value]) for word, value in nc.EN_CARDINALS.items()],
    # English ordinals (every table entry)
    *[(word, [value]) for word, value in nc.EN_ORDINALS.items()],
    # Turkish ordinals
    ("birinci", [1]),
    ("ikinci", [2]),
    ("üçüncü", [3]),
    ("dördüncü", [4]),
    ("beşinci", [5]),
    ("altıncı", [6]),
    ("yedinci", [7]),
    ("sekizinci", [8]),
    ("dokuzuncu", [9]),
    ("onuncu", [10]),
    ("yirminci", [20]),
    ("dokuzuncuya", [9]),
    # case suffixes
    ("saat dokuzda", [9]),
    ("saat beşte", [5]),
    ("ikiye", [2]),
    ("üçten", [3]),
    ("dörde", [4]),
    ("altıyı", [6]),
    # apostrophe forms
    ("on'da", [10]),
    ("beş'te", [5]),
    ("dokuz’da", [9]),
    # distributive
    ("ikişer", [2]),
    ("beşer", [5]),
    # combining: additive, multiplicative
    ("yirmi beş", [25]),
    ("iki yüz", [200]),
    ("iki bin üç yüz", [2300]),
    ("twenty-five", [25]),
    ("two hundred", [200]),
    ("yirmi, beş", [20, 5]),
    # case folding and NFC
    ("İKİ ONAY", [2]),
    ("DOKUZ", [9]),
    ("ALTI", [6]),
    ("dördüncü", [4]),
    # not numerals
    ("onay", []),
    ("onaylar", []),
    ("onu", []),
    ("ona", []),
    ("onun", []),
    ("onda", []),
    ("ondan", []),
    ("onları", []),
    ("bir toplantı", []),
    ("one meeting", []),
    ("yüzde 85", [85]),
    ("birkaç şey", []),
    ("ikisi", []),
    ("altında", []),
    # conservative homographs: always numerals
    ("on onay", [10]),
    ("turned on", [10]),
    ("yüzü", [100]),
]


@pytest.mark.parametrize(("text", "numbers"), VECTORS)
def test_numbers_in(text, numbers):
    assert nc.numbers_in(text) == numbers


@pytest.mark.parametrize(
    ("text", "facts", "passes"),
    [
        # the brief's named cases
        ("5 onay seni bekliyor.", "onay: 2", False),
        ("beş onay seni bekliyor.", "onay: 2", False),
        ("five approvals are waiting.", "onay: 2", False),
        ("yirmi beş görev var.", "a: 20\nb: 5", False),
        ("iki onay seni bekliyor.", "onay: 2", True),
        ("saat dokuzda toplantın var.", "09:00 Sprint", True),
        ("saat beşte toplantın var.", "09:00 Sprint", False),
        ("Bugün on onay var.", "onay: 2", False),
        ("Bugün on onay var.", "onay: 10", True),
        ("The heater is turned on.", "onay: 2", False),
        ("Bugün bir toplantı var.", "takvim: 1", True),
        ("Bugün bir toplantı var.", "takvim: 2", True),
        ("Bugün onay bekleyen bir iş var, onu Onaylar sayfasında bulursun.", "onay: 2", True),
        # twelve-hour reading of an afternoon time
        ("saat üçte toplantın var.", "15:00 Sprint", True),
        ("saat dörtte toplantın var.", "15:00 Sprint", False),
        # the date is a fact
        ("24 Kasım, 2 onay.", "tarih: 2026-11-24\nonay: 2", True),
    ],
)
def test_passes(text, facts, passes):
    assert nc.passes(text, nc.allowed_numbers(facts)) is passes


def test_invented_numbers_lists_only_the_unknown_ones():
    assert nc.invented_numbers("2 onay, 7 görev, beş oturum", {2}) == [7, 5]


def test_allowed_numbers_reads_every_digit_run_and_afternoon_hours():
    allowed = nc.allowed_numbers("tarih: 2026-11-24\n- Görev: 3\n  - 15:30 Plan")
    assert {2026, 11, 24, 3, 15, 30}.issubset(allowed)
    assert 24 in allowed and 2 not in allowed


def test_the_numeral_table_is_closed_and_bir_is_excluded():
    assert "bir" not in nc.TR_CARDINALS
    assert "one" not in nc.EN_CARDINALS
    assert {"bir", "one", "ona", "onu", "onda", "ondan", "yüzde"} <= nc.NOT_NUMERALS
    assert nc.parse_token("bir") is None
    assert nc.parse_token("one") is None
