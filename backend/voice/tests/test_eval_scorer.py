"""Branch 3a scorer golden cases (brief "3a" Tests): insertions, deletions,
substitutions, Turkish casing `İ/i` and `I/ı`, punctuation."""

import pytest

from voice.eval.scorer import align, corpus_errors, normalize, score


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("İSTANBUL", "istanbul"),
        ("IŞIK", "ışık"),
        ("Iğdır ve İzmir", "ığdır ve izmir"),
        # Decomposed I + combining dot above is the same letter as İ.
        ("İstanbul", "istanbul"),
        ("Merhaba, Friday! Nasılsın?", "merhaba friday nasılsın"),
        ("Repo'yu aç.", "repoyu aç"),
        ("PR’ı merge et…", "prı merge et"),
        ("Saat 10'da — “toplantı”", "saat 10da toplantı"),
        ("  çok   boşluk  ", "çok boşluk"),
        ("", ""),
    ],
)
def test_turkish_normalisation(text, expected):
    assert normalize(text) == expected


def test_english_uses_the_ordinary_casing_rule():
    assert normalize("I THINK it is", "en") == "i think it is"
    assert normalize("I THINK it is", "tr") == "ı thınk it is"


def test_identical_after_normalisation_is_zero_wer():
    errors = score("İzmir'e yarın gidiyorum.", "izmire yarın gidiyorum")
    assert (errors.errors, errors.wer) == (0, 0.0)


def test_substitution():
    errors = align("a b c d".split(), "a x c d".split())
    assert (errors.substitutions, errors.deletions, errors.insertions) == (1, 0, 0)
    assert errors.wer == 0.25


def test_deletion():
    errors = align("a b c d".split(), "a c d".split())
    assert (errors.substitutions, errors.deletions, errors.insertions) == (0, 1, 0)


def test_insertion():
    errors = align("a b".split(), "a b c d".split())
    assert (errors.substitutions, errors.deletions, errors.insertions) == (0, 0, 2)
    assert errors.wer == 1.0


def test_mixed_edits_and_minimal_distance():
    errors = score("yarın saat onda diş hekimi", "yarın saatte on da diş hekimi var")
    assert errors.reference_words == 5
    assert errors.errors == 4  # saat->saatte, onda->on, +da, +var
    assert errors.substitutions == 2 and errors.insertions == 2


def test_empty_reference_and_hypothesis():
    assert score("", "").wer == 0.0
    assert score("", "bir şey").wer == 1.0
    assert score("iki kelime", "").deletions == 2


def test_corpus_wer_sums_errors_not_per_utterance_rates():
    total = corpus_errors([("a b c d e f g h i j", "a b c d e f g h i x"), ("k", "")])
    # 2 errors over 11 words, not the mean of 10% and 100%.
    assert total.errors == 2 and total.reference_words == 11
    assert round(total.wer, 4) == round(2 / 11, 4)
