"""The self-trigger rule (brief D10): the shared vectors
(`vectors/self_trigger.json` also runs in vitest over self-trigger.ts), the
window rule and the content rule of `SelfTriggerFilter`."""

import json

import pytest
from conftest import PACKAGE_ROOT, REPO_ROOT

from friday_voice import config
from friday_voice.ports import ManualClock
from friday_voice.self_trigger import (
    REASON_CONTENT,
    REASON_WINDOW,
    SPOKEN_MEMORY_MS,
    SelfTriggerFilter,
    matches_recent_speech,
    normalize_speech,
    within_self_trigger_window,
)

VECTORS = json.loads((PACKAGE_ROOT / "vectors" / "self_trigger.json").read_text("utf-8"))


@pytest.mark.parametrize(
    ("text", "expected"), [(v["input"], v["expected"]) for v in VECTORS["normalize"]]
)
def test_normalize_vectors(text, expected):
    assert normalize_speech(text) == expected


@pytest.mark.parametrize("vector", VECTORS["match"], ids=lambda v: v["id"])
def test_match_vectors(vector):
    phrase = vector["wake_phrase"] or "Hey Friday"
    assert matches_recent_speech(vector["transcript"], vector["recent"], phrase) is vector["expected"]


@pytest.mark.parametrize("vector", VECTORS["window"], ids=lambda v: v["id"])
def test_window_vectors(vector):
    result = within_self_trigger_window(
        vector["detected_at"], vector["playback_ended_at"], vector["window_ms"]
    )
    assert result is vector["expected"]


def test_the_vectors_cover_both_outcomes_and_run_in_vitest():
    assert {v["expected"] for v in VECTORS["match"]} == {True, False}
    assert {v["expected"] for v in VECTORS["window"]} == {True, False}
    vitest = REPO_ROOT / "frontend" / "src" / "lib" / "voice" / "self-trigger.vectors.test.ts"
    assert "node/friday-voice/vectors/self_trigger.json" in vitest.read_text("utf-8")


def test_the_window_is_the_browser_value():
    browser = (REPO_ROOT / "frontend" / "src" / "lib" / "voice" / "config.ts").read_text("utf-8")
    assert f"VOICE_SELF_TRIGGER_MS = {config.SELF_TRIGGER_MS:_}" in browser
    ts = (REPO_ROOT / "frontend" / "src" / "lib" / "voice" / "self-trigger.ts").read_text("utf-8")
    assert f"SPOKEN_MEMORY_MS = {SPOKEN_MEMORY_MS:_}" in ts


def test_records_with_a_text_attribute_or_key_are_accepted():
    class Record:
        text = "Terminali aç."

    assert matches_recent_speech("terminali aç", [Record()])
    assert matches_recent_speech("terminali aç", [{"text": "Terminali aç.", "at": 0}])


# --- SelfTriggerFilter ---


def make_filter(**kwargs):
    clock = ManualClock()
    return clock, SelfTriggerFilter(clock, **kwargs)


def test_window_rule_during_playback_and_within_1_5_s_after():
    clock, rule = make_filter()
    assert rule.suspect_at(clock.monotonic_ms()) is False  # nothing ever played
    rule.playback_started()
    assert rule.suspect_at(clock.monotonic_ms()) is True  # during playback
    clock.advance(4_000)
    rule.playback_ended()
    clock.advance(config.SELF_TRIGGER_MS)
    assert rule.suspect_at(clock.monotonic_ms()) is True  # at the edge
    clock.advance(1)
    assert rule.suspect_at(clock.monotonic_ms()) is False  # outside


def test_a_suspect_echo_is_discarded_by_the_window_rule():
    clock, rule = make_filter()
    rule.spoken("Hey Friday, terminali aç.")
    assert rule.check("hey friday terminali aç", suspect=True) == REASON_WINDOW
    assert rule.discarded == {REASON_WINDOW: 1, REASON_CONTENT: 0}
    assert rule.discarded_self == 1


def test_the_content_rule_discards_whatever_the_timing():
    clock, rule = make_filter()
    rule.spoken("Takvimin boş görünüyor.")
    clock.advance(SPOKEN_MEMORY_MS - 1)
    assert rule.check("takvimin boş görünüyor", suspect=False) == REASON_CONTENT
    assert rule.discarded_self == 1


def test_the_content_rule_remembers_60_seconds():
    clock, rule = make_filter()
    rule.spoken("Takvimin boş görünüyor.")
    clock.advance(SPOKEN_MEMORY_MS + 1)
    assert rule.check("takvimin boş görünüyor") is None
    assert rule.recent() == []
    assert rule.discarded_self == 0


def test_a_new_request_passes_even_when_suspect():
    clock, rule = make_filter()
    rule.spoken("Bugün iki toplantın var.")
    assert rule.check("yarın saat onda diş hekimi", suspect=True) is None


def test_a_disabled_filter_lets_everything_through():
    clock, rule = make_filter(enabled=False)
    rule.spoken("Hey Friday, terminali aç.")
    assert rule.check("hey friday terminali aç", suspect=True) is None
    assert rule.discarded_self == 0


def test_the_memory_is_bounded():
    clock, rule = make_filter()
    for index in range(1_000):
        rule.spoken(f"cümle {index}")
    assert len(rule.recent()) <= 200
