# ruff: noqa: F811 -- pytest fixtures imported from the helpers are redefined as arguments
"""Phase 13b branch 1c (ADR-018, D10): the ADR-015 cap's one clause for the
direct effect. A voice `home_direct` classification is kept only when it
names `ha_call_service`; naming any other tool it is plain chat (as a unit,
and through the `home.control` route and the chat view). Typed turns pass
the cap untouched, as before. The direct effect is the only executing
effect, and only `home_direct` has it."""

import dataclasses

import pytest

from assistant import wake_routes
from assistant.services import (
    EFFECT_DIRECT_HOME,
    INTENT_EFFECTS,
    VOICE_ALLOWED_EFFECTS,
    apply_voice_capability_cap,
    home_direct_classification,
)
from assistant.tests.home_direct_fixtures import (  # noqa: F401 -- fixtures
    PAUSE,
    calls,
    committed,
    direct_settings,
    draft_of,
    enable,
    ha,
    home,
    n8n_chat,
    node,
    owner,
    turn,
    workspace,
)
from assistant.wake_routes import RouteResult
from connectors.models import HomeDirectCall
from tools.home_ports import OUTCOME_DRAFT, HomeResolution

pytestmark = pytest.mark.django_db


def _classification(tool_name="ha_call_service"):
    resolution = HomeResolution(
        outcome=OUTCOME_DRAFT, reply="", code="draft", tool_name=tool_name, arguments=PAUSE
    )
    return home_direct_classification(resolution, origin="voice")


def test_the_direct_effect_is_declared_once():
    assert INTENT_EFFECTS["home_direct"] == EFFECT_DIRECT_HOME == wake_routes.EFFECT_DIRECT_HOME
    assert EFFECT_DIRECT_HOME in VOICE_ALLOWED_EFFECTS
    assert [i for i, e in INTENT_EFFECTS.items() if e == EFFECT_DIRECT_HOME] == ["home_direct"]


def test_a_voice_home_direct_naming_ha_call_service_is_kept():
    classification = _classification()
    assert apply_voice_capability_cap(classification, input_mode="voice") is classification


@pytest.mark.parametrize("tool_name", ["node_action", "vault_export", "mcp_github_merge"])
def test_a_voice_home_direct_naming_another_tool_is_chat(tool_name):
    capped = apply_voice_capability_cap(_classification(tool_name), input_mode="voice")
    assert capped == {"intent": "chat", "intent_payload": {}}


def test_a_voice_home_direct_without_a_payload_is_chat():
    capped = apply_voice_capability_cap({"intent": "home_direct"}, input_mode="voice")
    assert capped["intent"] == "chat"


def test_a_typed_turn_passes_the_cap_untouched():
    classification = _classification("node_action")
    assert apply_voice_capability_cap(classification, input_mode="text") is classification


@pytest.mark.parametrize("origin", ["voice", "node"])
def test_another_tool_through_the_home_route_is_chat_end_to_end(monkeypatch, turn, ha, origin):
    def handler(turn):
        classification = _classification("node_action")
        return RouteResult(
            intent=classification["intent"],
            intent_payload=classification["intent_payload"],
            provenance=classification["_provenance"],
        )

    (home_route,) = wake_routes.CODE_ROUTES
    monkeypatch.setattr(
        wake_routes, "CODE_ROUTES", (dataclasses.replace(home_route, handler=handler),)
    )
    events, user = turn(origin, "TV'yi duraklat")
    assert draft_of(events) is None
    assert user.intent == "chat"
    assert ha.posts == [] and not HomeDirectCall.objects.exists()
