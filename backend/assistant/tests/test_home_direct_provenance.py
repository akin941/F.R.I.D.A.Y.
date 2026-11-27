# ruff: noqa: F811 -- pytest fixtures imported from the helpers are redefined as arguments
"""Phase 13b branch 1c (ADR-018, D10a): `home_direct` is a code-only
classification with server-side provenance -- three independent locks, each
tested on its own, on voice, `node_voice` and typed turns:

1. **The router cannot emit it.** `parse_intent` refuses the value; a
   router reply naming it degrades the turn to chat (no HA request, no call
   row, no card); the seeded router prompt never names it.
2. **Only `home.control` may return it**, and only with the in-process
   marker: a phrase or `tool.*` route returning it is chat with no reply;
   `validate_routes` refuses `extra_effects` elsewhere; a classification
   without the marker (a JSON round trip, a look-alike object) is chat.
3. **The executor never trusts the payload's arguments.** A hand-built
   classification *with* the marker but arguments that differ from the
   resolver's output for the stored words (another flagged entity, another
   level, another service) is refused (`provenance_mismatch`): no HA
   request, no call row, `ha.direct_provenance_refused`, and the reply is
   the matcher's own current result; stored words that now resolve to an
   ask are the ask."""

import dataclasses
import json

import pytest
from django.core.exceptions import ImproperlyConfigured

from ai_core.gateway import InferenceGateway
from assistant import services as assistant_services
from assistant import views as assistant_views
from assistant import wake_routes
from assistant.intent import CODE_ONLY_INTENTS, IntentParseError, parse_intent
from assistant.models import Message
from assistant.services import home_direct_classification, strip_provenance
from assistant.tests.home_direct_fixtures import (  # noqa: F401 -- fixtures
    ORIGINS,
    PAUSE,
    TV,
    allow,
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
    reply_text,
    route,
    turn,
    workspace,
)
from assistant.wake_routes import (
    EFFECT_PROPOSAL,
    HOME_ROUTE_NAME,
    RouteResult,
    WakeRoute,
    validate_routes,
)
from audit.models import AuditLog
from connectors.models import HomeDirectCall
from tools.home_ports import OUTCOME_DRAFT, HomeResolution

pytestmark = pytest.mark.django_db

ROUTER_SAYS_DIRECT = json.dumps({"intent": "home_direct", "tool_name": "ha_call_service"})


def _nothing_direct(ha, events, user):
    assert ha.posts == []
    assert not HomeDirectCall.objects.exists()
    assert draft_of(events) is None
    assert user.intent == "chat"


# --- lock 1: the router cannot emit it -------------------------------------------


def test_the_parser_refuses_the_code_only_intent():
    assert Message.Intent.HOME_DIRECT in CODE_ONLY_INTENTS
    with pytest.raises(IntentParseError, match="unrecognized intent value"):
        parse_intent(ROUTER_SAYS_DIRECT)


@pytest.fixture
def router_says_direct(monkeypatch):
    """The real `router.intent` call, its text replaced by a reply naming
    `home_direct` -- the real parser then runs on it."""
    original = InferenceGateway.complete

    def complete(self, purpose, messages, **kwargs):
        result = original(self, purpose, messages, **kwargs)
        if purpose == "router.intent":
            return dataclasses.replace(result, text=ROUTER_SAYS_DIRECT)
        return result

    monkeypatch.setattr(InferenceGateway, "complete", complete)


@pytest.mark.parametrize("origin", ORIGINS)
def test_a_router_reply_naming_home_direct_is_chat(router_says_direct, turn, ha, origin):
    # Words the home matcher does not resolve: the router is asked.
    events, user = turn(origin, "bugün hava nasıl")
    _nothing_direct(ha, events, user)


@pytest.mark.parametrize("origin", ORIGINS)
def test_even_a_parser_that_let_it_through_is_chat(monkeypatch, turn, ha, origin):
    """Lock 1 bypassed (a future parser change): the view's marker check
    (lock 2) still makes it chat."""
    monkeypatch.setattr(
        assistant_services,
        "parse_intent",
        lambda _text: {"intent": "home_direct", "tool_name": "ha_call_service"},
    )
    events, user = turn(origin, "bugün hava nasıl")
    _nothing_direct(ha, events, user)


def test_the_seeded_router_prompt_never_names_home_direct():
    from ai_core.management.commands import seed_ai_defaults

    source = json.dumps(
        {k: v for k, v in vars(seed_ai_defaults).items() if isinstance(v, str)},
        ensure_ascii=False,
    )
    assert "home_direct" not in source
    assert "direct_home" not in source


# --- lock 2: only home.control, only with the marker ------------------------------


def _direct_result(arguments=None):
    resolution = HomeResolution(
        outcome=OUTCOME_DRAFT,
        reply="",
        code="draft",
        tool_name="ha_call_service",
        arguments=dict(arguments or PAUSE),
    )
    classification = home_direct_classification(resolution, origin="voice")
    return RouteResult(
        intent=classification["intent"],
        intent_payload=classification["intent_payload"],
        reply_text="Tamam.",
        provenance=classification["_provenance"],
    )


@pytest.mark.parametrize(
    "route_obj",
    [
        WakeRoute(
            name="test.phrase", effect="read", default_phrases=("x",), handler=lambda t: None
        ),
        WakeRoute(
            name="tool.ha_call_service",
            effect=EFFECT_PROPOSAL,
            default_phrases=(),
            handler=lambda t: None,
        ),
    ],
)
def test_a_route_other_than_home_control_returning_home_direct_is_chat(route_obj):
    routed = wake_routes._finish(route_obj, _direct_result(), input_mode="voice", remainder="")
    assert routed.classification == {"intent": "chat", "intent_payload": {}}
    assert routed.reply_text is None


def test_home_control_returning_home_direct_keeps_it():
    (home_route,) = wake_routes.CODE_ROUTES
    routed = wake_routes._finish(home_route, _direct_result(), input_mode="voice", remainder="")
    assert routed.classification["intent"] == "home_direct"
    assert assistant_services.is_code_home_direct(routed.classification)


def test_a_phrase_route_returning_home_direct_through_the_view_is_chat(monkeypatch, turn, ha, home):
    rogue = WakeRoute(
        name="test.rogue",
        effect="read",
        default_phrases=("tv dursun hemen",),
        handler=lambda t: _direct_result(),
    )
    monkeypatch.setattr(wake_routes, "ROUTES", (*wake_routes.ROUTES, rogue))
    events, user = turn("voice", "tv dursun hemen")
    _nothing_direct(ha, events, user)
    assert reply_text(events) != "Tamam."


def test_validate_routes_refuses_extra_effects_anywhere_but_home_control():
    handler = lambda t: None  # noqa: E731
    with pytest.raises(ImproperlyConfigured, match="may not declare extra effects"):
        validate_routes(
            (
                WakeRoute(
                    name="digest.other",
                    effect="read",
                    default_phrases=("başka",),
                    handler=handler,
                    extra_effects=("direct_home",),
                ),
            )
        )
    with pytest.raises(ImproperlyConfigured, match="unknown extra effect"):
        validate_routes(
            (
                WakeRoute(
                    name=HOME_ROUTE_NAME,
                    effect=EFFECT_PROPOSAL,
                    default_phrases=(),
                    handler=handler,
                    matched_by_code=True,
                    extra_effects=("execute",),
                ),
            )
        )
    validate_routes()  # the committed table passes


@pytest.mark.parametrize(
    "forge",
    [
        # A JSON round trip: the marker cannot survive it.
        lambda c: json.loads(json.dumps(strip_provenance(c))),
        # A look-alike marker: identity, not value.
        lambda c: {**strip_provenance(c), "_provenance": object()},
        lambda c: {**strip_provenance(c), "_provenance": "code"},
    ],
)
def test_a_classification_without_the_marker_reaching_the_view_is_chat(
    monkeypatch, forge, turn, ha, home
):
    resolution = HomeResolution(
        outcome=OUTCOME_DRAFT, reply="", code="draft", tool_name="ha_call_service", arguments=PAUSE
    )
    forged = forge(home_direct_classification(resolution, origin="chat"))
    monkeypatch.setattr(assistant_views, "route_typed_home_direct", lambda *a, **k: forged)
    events, user = turn("chat", "TV'yi duraklat")
    _nothing_direct(ha, events, user)


# --- lock 3: the executor re-resolves the stored words -----------------------------


@pytest.fixture
def projector(workspace, home):
    other = allow(workspace, "media_player.projeksiyon", ["media_pause"], label="Projeksiyon")
    other.direct = True
    other.save()
    return other


def _inject(monkeypatch, origin, arguments):
    """A hand-built `home_direct` classification *with* the marker, for the
    turn's own origin: typed through the typed step, voice/node through the
    `home.control` route."""
    resolution = HomeResolution(
        outcome=OUTCOME_DRAFT,
        reply="",
        code="draft",
        tool_name="ha_call_service",
        arguments=dict(arguments),
    )
    if origin == "chat":
        monkeypatch.setattr(
            assistant_views,
            "route_typed_home_direct",
            lambda *a, **k: home_direct_classification(resolution, origin="chat"),
        )
        return

    def handler(turn):
        payload_origin = "node" if turn.channel == "node_voice" else "voice"
        classification = home_direct_classification(
            resolution, origin=payload_origin, node_id=turn.origin_node_id
        )
        return RouteResult(
            intent=classification["intent"],
            intent_payload=classification["intent_payload"],
            provenance=classification["_provenance"],
        )

    (home_route,) = wake_routes.CODE_ROUTES
    monkeypatch.setattr(
        wake_routes, "CODE_ROUTES", (dataclasses.replace(home_route, handler=handler),)
    )


@pytest.mark.parametrize("origin", ORIGINS)
@pytest.mark.parametrize(
    ("text", "forged", "expected_card"),
    [
        # Another flagged entity than the one named.
        (
            "TV'yi duraklat",
            {**PAUSE, "entity_id": "media_player.projeksiyon"},
            PAUSE,
        ),
        # Another level than the one spoken.
        (
            "sesi yüzde 40'a ayarla",
            {**PAUSE, "service": "volume_set", "data": {"volume_level": 1.0}},
            {**PAUSE, "service": "volume_set", "data": {"volume_level": 0.4}},
        ),
        # Another service than the one asked.
        ("TV'yi duraklat", {**PAUSE, "service": "media_play"}, PAUSE),
    ],
)
def test_arguments_that_differ_from_the_stored_words_are_refused(
    monkeypatch, projector, turn, ha, origin, text, forged, expected_card
):
    _inject(monkeypatch, origin, forged)
    events, user = turn(origin, text)
    assert ha.posts == []
    assert not HomeDirectCall.objects.exists()
    refused = AuditLog.objects.get(action="ha.direct_provenance_refused")
    assert refused.metadata["reason"] == "provenance_mismatch"
    assert refused.metadata["source_message_id"] == str(user.pk)
    assert refused.metadata["origin"] == origin
    # Never the refused arguments' text in the audit row.
    assert "projeksiyon" not in json.dumps(refused.metadata)
    # The answer is the matcher's own current result: its card, its reply.
    draft = draft_of(events)
    assert draft["payload"] == {"arguments": expected_card}
    assert draft["direct_reason"] == "provenance_mismatch"
    assert reply_text(events).startswith("Onay kartı hazır:")
    assert user.intent == "tool_request"


@pytest.mark.parametrize("origin", ORIGINS)
def test_stored_words_that_now_resolve_to_an_ask_are_the_ask(
    monkeypatch, workspace, home, turn, ha, origin
):
    second = allow(
        workspace,
        "media_player.projeksiyon",
        ["media_pause"],
        label="Projeksiyon",
        room=home.salon,
        aliases=["tv"],
    )
    second.direct = True
    second.save()
    _inject(monkeypatch, origin, PAUSE)
    events, user = turn(origin, "TV'yi duraklat")
    assert ha.posts == [] and not HomeDirectCall.objects.exists()
    assert draft_of(events) is None
    assert reply_text(events) == "2 cihaz eşleşti: Salon Projeksiyon, Salon TV. Hangisi?"
    assert AuditLog.objects.filter(action="ha.direct_provenance_refused").count() == 1
    assert user.intent == "chat"


def test_ambiguous_with_two_flagged_tvs_is_the_ask_and_no_call(workspace, home, turn, ha):
    second = allow(
        workspace,
        "media_player.projeksiyon",
        ["media_pause"],
        label="Projeksiyon",
        room=home.salon,
        aliases=["tv"],
    )
    second.direct = True
    second.save()
    events, _ = turn("voice", "TV'yi duraklat")
    assert reply_text(events) == "2 cihaz eşleşti: Salon Projeksiyon, Salon TV. Hangisi?"
    assert draft_of(events) is None
    assert ha.posts == [] and not HomeDirectCall.objects.exists()
