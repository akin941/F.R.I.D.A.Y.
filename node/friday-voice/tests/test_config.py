"""The child's tunables (brief D8): one module, the brief's values, integers
only (scores and probabilities in thousandths)."""

import ast

from conftest import SOURCE_DIR

from friday_voice import config

BRIEF = {
    "WAKE_THRESHOLD": 500,
    "WAKE_REFRACTORY_MS": 2_000,
    "WAKE_NO_SPEECH_S": 5,
    "BARGE_IN_VAD": 700,
    "BARGE_IN_MIN_MS": 250,
    "BARGE_IN_AEC_GUARD_MS": 300,
    "BARGE_IN_PREROLL_MS": 300,
    "SELF_TRIGGER_MS": 1_500,
    "SILENCE_CLOSE_S": 30,
    "SESSION_MAX_MINUTES": 15,
    "WAKE_STORM_MAX": 3,
    "WAKE_STORM_WINDOW_S": 60,
    "TURN_TRAILING_SILENCE_MS": 200,
    "TURN_HARD_SILENCE_MS": 1_500,
    "UTTERANCE_MAX_S": 30,
    "REPLAY_REGRESSION_PCT": 20,
}


def test_the_brief_values():
    for name, value in BRIEF.items():
        assert getattr(config, name) == value, name


def test_every_tunable_is_an_integer_or_a_string():
    for name in dir(config):
        if name.isupper():
            assert isinstance(getattr(config, name), int | str), name


def test_no_other_module_hard_codes_the_wake_numbers():
    """The threshold and refractory period are read from `config` only
    (D8: the policy request must carry the values the detector runs at)."""
    for path in SOURCE_DIR.rglob("*.py"):
        if path.name == "config.py":
            continue
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                assert not {"WAKE_THRESHOLD", "WAKE_REFRACTORY_MS"} & set(names), path
