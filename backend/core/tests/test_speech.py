"""Phase 13 D16: `core.speech` -- the normalizer moved from
`assistant.wake_routes` (the shared wake-route vectors pass through both
import paths), `find_mentions` and `match_room` over Turkish case forms,
and the module stays standard-library only."""

import ast
import json
from pathlib import Path

import pytest
from django.conf import settings as django_settings

from core import speech

VECTORS = json.loads(
    (
        Path(django_settings.BASE_DIR).resolve().parent
        / "docs"
        / "voice"
        / "wake-route-vectors.json"
    ).read_text(encoding="utf-8")
)


@pytest.mark.parametrize("row", VECTORS["normalize"], ids=lambda r: r["input"])
def test_the_wake_route_vectors_pass_through_core_speech(row):
    assert speech.normalize_speech(row["input"]) == row["speech"]


def test_phrase_key_folds_case_punctuation_and_dotless_i():
    assert speech.phrase_key("  Çalışma  Odası! ") == "çalişma odasi"
    assert speech.phrase_key("MASAÜSTÜ") == speech.phrase_key("masaüstü")
    assert speech.phrase_key("MacBook") == "macbook"
    assert speech.phrase_key("!!!") == ""


NODES = [("MacBook", "mac"), ("Masaüstü", "desk"), ("medya bilgisayarı", "media")]


def _targets(mentions):
    return [mention.target for mention in mentions]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Masaüstünde Claude Code oturumlarım ne yapıyor?", ["desk"]),
        ("MacBook'ta testleri çalıştır", ["mac"]),
        ("macbookta testleri çalıştır", ["mac"]),
        ("Masaüstünü kontrol et", ["desk"]),
        ("Masaüstünden oku", ["desk"]),
        ("MacBook'a geç", ["mac"]),
        ("medya bilgisayarında film aç", ["media"]),
        ("MASAÜSTÜNDE NE VAR", ["desk"]),
        ("Masaüstü ve MacBook", ["desk", "mac"]),
        ("Claude Code oturumlarım ne yapıyor?", []),
        # Whole words only: no prefix or substring hits.
        ("Masaüstücüler toplantısı", []),
        ("MacBookPro", []),
        ("bilgisayarım yavaş", []),
    ],
)
def test_find_mentions_whole_words_with_turkish_cases(text, expected):
    assert _targets(speech.find_mentions(text, NODES)) == expected


def test_the_longest_phrase_wins():
    vocabulary = [("oda", "short"), ("çalışma odası", "long")]
    (mention,) = speech.find_mentions("çalışma odasındaki Mac", vocabulary)
    assert mention.target == "long" and mention.suffix == "ndaki"
    assert (mention.start, mention.end) == (0, 2)


def test_two_targets_with_the_same_phrase_are_both_returned():
    mentions = speech.find_mentions("salondaki bilgisayar", [("salon", "a"), ("Salon", "b")])
    assert _targets(mentions) == ["a", "b"]


def test_an_apostrophe_suffix_is_a_separate_token():
    (mention,) = speech.find_mentions("Mac'te çalıştır", [("Mac", "mac")])
    assert mention.suffix == "te" and (mention.start, mention.end) == (0, 2)


def test_final_consonant_softening():
    assert _targets(speech.find_mentions("kitabı oku", [("kitap", "book")])) == ["book"]


ROOMS = [("Salon", "salon"), ("Çalışma odası", "study"), ("yatak odası", "bed")]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("salondaki TV'yi aç", ["salon"]),
        ("Salonda ışığı kapat", ["salon"]),
        ("Salon'daki lamba", ["salon"]),
        ("çalışma odasındaki Mac", ["study"]),
        ("ÇALIŞMA ODASINDA", ["study"]),
        ("yatak odasındaki lambayı aç", ["bed"]),
        # A bare room name, or another case, is not a locative mention.
        ("salon", []),
        ("salona git", []),
        ("salonun ışığı", []),
        ("çalışma odası", []),
    ],
)
def test_match_room_locative_forms_only(text, expected):
    assert _targets(speech.match_room(text, ROOMS)) == expected


def test_empty_inputs():
    assert speech.find_mentions("", NODES) == []
    assert speech.find_mentions("Masaüstünde", []) == []
    assert speech.find_mentions("Masaüstünde", [("", "x"), ("!!", "y")]) == []
    assert speech.match_room(None, ROOMS) == []


def test_core_speech_is_standard_library_only():
    tree = ast.parse(Path(speech.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"__future__", "unicodedata", "collections", "dataclasses", "typing"}
