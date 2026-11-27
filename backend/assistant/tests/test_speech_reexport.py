"""Phase 13 D16: `assistant.wake_routes` re-exports the normalizer that
moved to `core.speech`, so every caller (and the shared vectors) uses one
implementation."""

from assistant import wake_aliases, wake_routes
from core import speech


def test_wake_routes_re_exports_the_core_speech_functions():
    assert wake_routes.normalize_speech is speech.normalize_speech
    assert wake_routes.match_key is speech.match_key


def test_wake_aliases_still_reach_the_same_match_key():
    assert wake_aliases.wake_routes.match_key is speech.match_key
