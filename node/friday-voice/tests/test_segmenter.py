"""The segmenter port and the language router (brief D8): the shared
vectors (`vectors/segmenter.json` also runs in vitest over segmenter.ts)."""

import json

import pytest
from conftest import PACKAGE_ROOT, REPO_ROOT

from friday_voice import language
from friday_voice.segmenter import (
    CODE_PLACEHOLDER,
    MAX_SENTENCE_CHARS,
    SentenceSegmenter,
    speakable_line,
    to_speakable,
)

SEGMENTER = json.loads((PACKAGE_ROOT / "vectors" / "segmenter.json").read_text("utf-8"))
LANGUAGE = json.loads((PACKAGE_ROOT / "vectors" / "language.json").read_text("utf-8"))


@pytest.mark.parametrize(
    ("line", "expected"), [(v["input"], v["expected"]) for v in SEGMENTER["speakable_line"]]
)
def test_speakable_line_vectors(line, expected):
    assert speakable_line(line) == expected


@pytest.mark.parametrize("vector", SEGMENTER["segment"], ids=lambda v: v["id"])
def test_segment_vectors(vector):
    segmenter = SentenceSegmenter()
    assert [segmenter.push(chunk) for chunk in vector["chunks"]] == vector["live"]
    assert segmenter.flush() == vector["flush"]


def test_the_vectors_cover_the_rules():
    ids = {vector["id"] for vector in SEGMENTER["segment"]}
    assert {"abbreviation_and_initial", "decimal", "code_fence", "table", "streamed_link"} <= ids


def test_the_frontend_runs_the_same_vectors():
    spec = REPO_ROOT / "frontend" / "src" / "lib" / "voice" / "segmenter.vectors.test.ts"
    assert "node/friday-voice/vectors/segmenter.json" in spec.read_text("utf-8")


def test_sentences_stay_within_bounds_and_never_read_code():
    text = "Komut:\n```sh\nrm -rf /\n```\n" + "uzun kelime " * 80 + "son."
    segmenter = SentenceSegmenter()
    out = [*segmenter.push(text), *segmenter.flush()]
    joined = to_speakable(text)
    assert "rm -rf" not in joined and CODE_PLACEHOLDER in joined
    assert all(len(sentence) <= MAX_SENTENCE_CHARS for sentence in out)


@pytest.mark.parametrize(
    "case", LANGUAGE["cases"], ids=lambda c: f"{c['sentence'][:20]}|{c['fallback']}"
)
def test_language_vectors(case):
    assert language.detect(case["sentence"], case["fallback"]) == case["expected"]


@pytest.mark.parametrize(
    ("hint", "expected"),
    [("tr", "tr"), ("tr-TR", "tr"), ("EN", "en"), ("en-GB", "en"), ("de", ""), (None, "")],
)
def test_language_hints_are_normalised(hint, expected):
    assert language.normalise(hint) == expected
