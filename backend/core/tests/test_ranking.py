"""Phase 8B branch 4 (brief D8): `core.ranking.rrf`, hand-computed."""

import pytest

from core.ranking import rrf


def test_hand_computed_fusion():
    # a: rank 1 in list 1, rank 2 in list 2; b: rank 2 in list 1;
    # c: rank 1 in list 2; d: rank 3 in list 2.
    fused = dict(rrf([["a", "b"], ["c", "a", "d"]], k=60))
    assert fused["a"] == pytest.approx(1 / 61 + 1 / 62)
    assert fused["b"] == pytest.approx(1 / 62)
    assert fused["c"] == pytest.approx(1 / 61)
    assert fused["d"] == pytest.approx(1 / 63)


def test_an_item_in_both_lists_outranks_one_in_a_single_list():
    order = [key for key, _ in rrf([["x", "y"], ["z", "y"]])]
    assert order[0] == "y"


def test_ties_break_on_first_appearance_and_duplicates_count_once():
    assert [k for k, _ in rrf([["p", "p", "q"], ["q2"]], k=0)] == ["p", "q2", "q"]
    assert dict(rrf([["p", "p"]], k=0))["p"] == pytest.approx(1.0)


def test_empty_and_invalid():
    assert rrf([]) == []
    assert rrf([[], []]) == []
    with pytest.raises(ValueError):
        rrf([["a"]], k=-1)
