import pytest

from core import legacy_env


def test_prefers_the_friday_name():
    environ = {"FRIDAY_X": "new", "JARVIS_X": "old"}
    assert legacy_env.getenv("FRIDAY_X", environ=environ) == "new"


@pytest.mark.parametrize("environ", [{"JARVIS_X": "old"}, {"FRIDAY_X": "", "JARVIS_X": "old"}])
def test_falls_back_to_the_deprecated_jarvis_name(environ):
    assert legacy_env.getenv("FRIDAY_X", environ=environ) == "old"


def test_default_when_neither_is_set():
    assert legacy_env.getenv("FRIDAY_X", "d", environ={}) == "d"


def test_only_friday_names_are_accepted():
    with pytest.raises(ValueError):
        legacy_env.getenv("OTHER_X", environ={})
