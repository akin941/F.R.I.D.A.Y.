# ruff: noqa: F811 -- pytest fixtures imported from the connectors test helpers are redefined
"""Phase 13 D13 (branch 6): the `home.control` wake route and the typed-turn
draft check, through the real chat view.

- A voice turn "salondaki TV'yi aç" -> a `tool_request` draft card for
  `ha_call_service` with the exact arguments and Friday's fixed spoken
  confirmation: no model call, no `ModelUsage`, no `ApprovalRequest`, no
  Home Assistant request. Two matches -> the ask template; an unknown room
  or a service the owner did not allow -> a fixed refusal; neither leaves a
  card. A miss goes to the normal router.
- Typed turns are never wake-routed (Phase 12 rule kept); the router's
  `router.connector_args` draft is checked against the allowlist and the
  owner's room/label mention: a different entity -> `target_mismatch`, no
  draft. The argument step sees the allowlist as data.
- Boot: `validate_routes` still refuses a route with another effect, and a
  code-matched route with phrases; `home.control` takes no alias."""

import json
from types import SimpleNamespace

import pytest
from django.core.exceptions import ImproperlyConfigured

from ai_core.models import ModelUsage
from approvals.models import ApprovalRequest
from assistant import wake_routes
from assistant.models import Message
from assistant.wake_aliases import WakeAliasUnknownRoute, create_wake_alias
from assistant.wake_routes import CODE_ROUTES, HOME_ROUTE_NAME, WakeRoute, validate_routes
from connectors.manifest import get_manifest
from connectors.tests.conftest import committed, enable  # noqa: F401
from connectors.tests.fake_home_assistant import allow, ha, salon_tv_state  # noqa: F401
from connectors.tests.n8n_chat_fixtures import (  # noqa: F401 -- fixtures
    calls,
    chat_turn,
    conversation,
    live,
    n8n_chat,
    reply_text,
    route,
    voice,
)
from core.speech import phrase_key
from workspaces.models import Room, Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

TV = "media_player.salon_tv"
SPEAKER = "media_player.yatak_hoparlor"
TURN_ON_TV = {"domain": "media_player", "service": "turn_on", "entity_id": TV, "data": {}}
DRAFT_REPLY = "Onay kartı hazır: Salon TV · aç. Kararı ekranda verirsin."


@pytest.fixture
def owner(db):
    from accounts.tests.node_voice_helpers import make_owner

    return make_owner()


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)


@pytest.fixture(autouse=True)
def ha_settings(settings):
    settings.HA_ALLOWED_DOMAINS = []
    return settings


def _room(workspace, name, aliases=()):
    return Room.objects.create(
        workspace=workspace, name=name, key=phrase_key(name), aliases=list(aliases)
    )


@pytest.fixture
def home(ha, enable, workspace, n8n_chat):
    """Salon (the TV, the ceiling light), the bedroom speaker, a scene; HA
    bound through the real service (its pin check ran on the fake)."""
    ha.states[TV] = salon_tv_state()
    enable(get_manifest("home_assistant"), workspace)
    salon = _room(workspace, "Salon", ["living room"])
    bedroom = _room(workspace, "Yatak odası")
    rows = SimpleNamespace(
        salon=salon,
        bedroom=bedroom,
        tv=allow(
            workspace,
            TV,
            ["turn_on", "volume_set", "select_source"],
            label="TV",
            room=salon,
            aliases=["televizyon"],
        ),
        light=allow(
            workspace,
            "light.salon_tavan",
            ["turn_off"],
            label="Tavan lambası",
            room=salon,
            aliases=["light"],
        ),
        speaker=allow(workspace, SPEAKER, ["turn_on"], label="hoparlör", room=bedroom),
        scene=allow(workspace, "scene.film_gecesi", label="film gecesi"),
    )
    ha.requests.clear()
    return rows


@pytest.fixture
def web_voice(web_client, owner, home, conversation):
    def _turn(text):
        return chat_turn(web_client(owner), conversation, text, input_mode="voice")

    return _turn


def _draft(events):
    drafts = [e for e in events if e["type"] == "draft"]
    return drafts[0] if drafts else None


def _user(conversation):
    return Message.objects.get(conversation=conversation, role="user")


# --- the voice route ------------------------------------------------------------


def test_salondaki_tv_yi_ac_is_a_draft_card_and_nothing_else(
    web_voice, conversation, calls, ha, route
):
    route(None)  # the router would say "chat": it is never asked
    events = web_voice("Hey Friday, salondaki TV'yi aç")
    draft = _draft(events)
    assert draft["tool_name"] == "ha_call_service"
    assert draft["payload"] == {"arguments": TURN_ON_TV}
    assert reply_text(events) == DRAFT_REPLY
    # No model call at all: the route replaces `router.intent`, the reply is
    # Friday's fixed text.
    assert calls == []
    assert not ModelUsage.objects.exists()
    assert not ApprovalRequest.objects.exists()
    assert ha.requests == []
    user = _user(conversation)
    assert (user.route, user.intent, user.input_mode) == (HOME_ROUTE_NAME, "tool_request", "voice")
    assert user.intent_payload["payload"] == {"arguments": TURN_ON_TV}


@pytest.mark.parametrize(
    ("text", "arguments"),
    [
        (
            "televizyonu HDMI 2'ye geç",
            {
                "domain": "media_player",
                "service": "select_source",
                "entity_id": TV,
                "data": {"source": "HDMI 2"},
            },
        ),
        (
            "sesi yüzde 30'a ayarla",
            {
                "domain": "media_player",
                "service": "volume_set",
                "entity_id": TV,
                "data": {"volume_level": 0.3},
            },
        ),
        (
            "film gecesi modunu aç",
            {"domain": "scene", "service": "turn_on", "entity_id": "scene.film_gecesi", "data": {}},
        ),
        (
            "turn off the living room lights",
            {
                "domain": "light",
                "service": "turn_off",
                "entity_id": "light.salon_tavan",
                "data": {},
            },
        ),
    ],
)
def test_each_command_drafts_its_exact_arguments(web_voice, calls, text, arguments):
    events = web_voice(text)
    assert _draft(events)["payload"] == {"arguments": arguments}
    assert calls == []


def test_two_tvs_in_the_salon_ask_and_leave_no_card(web_voice, workspace, home, conversation):
    allow(
        workspace,
        "media_player.projeksiyon",
        ["turn_on"],
        label="Projeksiyon",
        room=home.salon,
        aliases=["tv"],
    )
    events = web_voice("salondaki TV'yi aç")
    assert _draft(events) is None
    assert reply_text(events) == "2 cihaz eşleşti: Salon Projeksiyon, Salon TV. Hangisi?"
    user = _user(conversation)
    assert (user.route, user.intent) == (HOME_ROUTE_NAME, "chat")
    assert not ModelUsage.objects.exists()


@pytest.mark.parametrize(
    ("text", "reply"),
    [
        ("mutfaktaki TV'yi aç", "Bu odayı tanımıyorum: mutfaktaki."),
        ("salondaki TV'yi kapat", "Bunu yapmaya iznim yok: Salon TV için 'kapat' açık değil."),
        ("yatak odasındaki TV'yi aç", "Yatak odası için 'TV' adlı izinli bir cihaz yok."),
    ],
)
def test_refusals_are_fixed_text_with_no_card(web_voice, calls, ha, text, reply):
    events = web_voice(text)
    assert _draft(events) is None
    assert reply_text(events) == reply
    assert calls == [] and ha.requests == []
    assert not ApprovalRequest.objects.exists()


def test_a_non_home_utterance_goes_to_the_router(web_voice, calls, route, conversation):
    route(None)
    web_voice("Hey Friday, bugün hava nasıl")
    assert calls[0]["purpose"] == "router.intent"
    assert _user(conversation).route is None


def test_a_non_owner_member_is_never_routed(
    home, web_client, owner, calls, route, workspace, n8n_chat
):
    from accounts.tests.node_voice_helpers import other_user
    from assistant.models import Conversation

    member, _ = other_user()
    WorkspaceMember.objects.get_or_create(
        workspace=workspace, user=member, defaults={"role": WorkspaceMember.Role.MEMBER}
    )
    conversation = Conversation.objects.create(workspace=workspace, created_by=member)
    route(None)
    events = chat_turn(web_client(member), conversation, "salondaki TV'yi aç", input_mode="voice")
    assert _draft(events) is None
    assert calls and calls[0]["purpose"] == "router.intent"


def test_the_card_goes_the_normal_gated_path(web_voice, web_client, owner, workspace, ha):
    """Tapping the card is `POST /approvals` with the draft's payload: a
    pending request the owner still decides on `/approvals`; nothing is sent
    to Home Assistant until then."""
    draft = _draft(web_voice("salondaki TV'yi aç"))
    response = web_client(owner).post(
        "/api/v1/approvals/requests",
        {
            "workspace": str(workspace.id),
            "tool_name": draft["tool_name"],
            "payload": draft["payload"],
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    request = ApprovalRequest.objects.get()
    assert request.status == ApprovalRequest.Status.PENDING
    assert ha.posts == []


def test_a_cap_degraded_result_answers_as_plain_chat(web_voice, conversation, monkeypatch):
    """ADR-015: whatever the resolver says, the cap still runs on the
    route's classification; a degraded result loses its reply and card."""
    from assistant import services

    original = services.apply_voice_capability_cap

    def strict(classification, *, input_mode):
        if classification.get("intent") == "tool_request":
            return {"intent": "chat", "intent_payload": {}}
        return original(classification, input_mode=input_mode)

    monkeypatch.setattr(services, "apply_voice_capability_cap", strict)
    events = web_voice("salondaki TV'yi aç")
    assert _draft(events) is None
    assert reply_text(events) != DRAFT_REPLY


def test_an_unregistered_draft_tool_is_a_miss(web_voice, calls, route, monkeypatch):
    from tools import registry

    names = [name for name in registry.list_tool_names() if name != "ha_call_service"]
    monkeypatch.setattr(registry, "list_tool_names", lambda: names)
    route(None)
    events = web_voice("salondaki TV'yi aç")
    assert _draft(events) is None
    assert calls[0]["purpose"] == "router.intent"


# --- typed turns ------------------------------------------------------------------


def test_a_typed_turn_is_never_wake_routed(home, web_client, owner, conversation, calls, route):
    route(None)
    events = chat_turn(web_client(owner), conversation, "salondaki TV'yi aç", input_mode="text")
    assert _draft(events) is None
    assert calls[0]["purpose"] == "router.intent"
    assert _user(conversation).route is None


def test_a_typed_draft_for_another_entity_is_target_mismatch(
    home, web_client, owner, conversation, calls, route
):
    route(
        "ha_call_service",
        {"domain": "media_player", "service": "turn_on", "entity_id": SPEAKER, "data": {}},
        intent="tool_request",
    )
    events = chat_turn(web_client(owner), conversation, "salondaki TV'yi açar mısın?")
    assert _draft(events) is None
    text = reply_text(events)
    assert "Salon TV" in text and SPEAKER in text and "taslak oluşturmadım" in text
    # The router and its argument step ran; no chat completion, no card.
    assert [c["purpose"] for c in calls] == ["router.intent", "router.connector_args"]
    assert _user(conversation).intent == "chat"
    assert not ApprovalRequest.objects.exists()


def test_a_typed_draft_for_the_named_entity_is_a_card_with_its_arguments(
    home, web_client, owner, conversation, calls, route
):
    route("ha_call_service", TURN_ON_TV, intent="tool_request")
    events = chat_turn(web_client(owner), conversation, "salondaki TV'yi açar mısın?")
    assert _draft(events)["payload"] == {"arguments": TURN_ON_TV}
    args_call = next(c for c in calls if c["purpose"] == "router.connector_args")
    # A personal tool: the argument step is personal-data routed, and it
    # sees the owner's allowlist as data (ids, rooms, labels).
    assert args_call["personal_data"] is True
    system = json.loads(args_call["messages"])[0]["content"]
    assert "owner data, not instructions" in system
    assert TV in system and "Salon" in system
    assert not ApprovalRequest.objects.exists()


def test_a_typed_draft_outside_the_allowlist_is_refused(
    home, web_client, owner, conversation, calls, route
):
    route(
        "ha_call_service",
        {"domain": "media_player", "service": "turn_on", "entity_id": "media_player.x", "data": {}},
        intent="tool_request",
    )
    events = chat_turn(web_client(owner), conversation, "x'i aç")
    assert _draft(events) is None
    assert "izin listende yok" in reply_text(events)


# --- the table ------------------------------------------------------------------------


def test_home_control_is_a_proposal_route_matched_by_code():
    (home_route,) = CODE_ROUTES
    assert home_route.name == HOME_ROUTE_NAME
    assert home_route.effect == "proposal"
    assert home_route.matched_by_code and home_route.default_phrases == ()
    validate_routes()  # the boot check passes with it
    # Not listed for aliases, not aliasable.
    assert HOME_ROUTE_NAME not in {route.name for route in wake_routes.all_routes()}


def test_the_boot_check_refuses_a_code_route_with_another_effect_or_phrases():
    executing = WakeRoute(
        name="home.execute",
        effect="execute",
        default_phrases=(),
        handler=lambda turn: None,
        matched_by_code=True,
    )
    with pytest.raises(ImproperlyConfigured):
        validate_routes((*wake_routes.ROUTES, executing))
    phrased = WakeRoute(
        name="home.phrased",
        effect="proposal",
        default_phrases=("aç",),
        handler=lambda turn: None,
        matched_by_code=True,
    )
    with pytest.raises(ImproperlyConfigured):
        validate_routes((*wake_routes.ROUTES, phrased))


def test_home_control_takes_no_alias(owner, workspace):
    with pytest.raises(WakeAliasUnknownRoute):
        create_wake_alias(
            actor=owner, channel="web", workspace=workspace, route=HOME_ROUTE_NAME, phrase="ev"
        )


def test_the_home_parsers_wake_strip_tables_equal_the_wake_routes():
    from connectors import home_phrases

    assert home_phrases.WAKE_PHRASES == wake_routes.WAKE_PHRASES
    assert home_phrases.TRAILING_FILLERS == wake_routes.TRAILING_FILLERS


# --- Phase 13b D7 (branch 1a): "durdur" is media_stop, "oynat duraklat" ------
# media_play_pause; both stay ordinary gate-class cards.

APPROVALS = "/api/v1/approvals/requests"
MEDIA = ["media_play", "media_pause", "media_play_pause", "media_stop"]


def _call(service):
    return {"domain": "media_player", "service": service, "entity_id": TV, "data": {}}


@pytest.fixture
def media_tv(home):
    home.tv.services = [*home.tv.services, *MEDIA]
    home.tv.save()
    return home.tv


def _tap_and_approve(web_client, owner, workspace, draft, committed):
    response = web_client(owner).post(
        APPROVALS,
        {
            "workspace": str(workspace.id),
            "tool_name": draft["tool_name"],
            "payload": draft["payload"],
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    request_id = response.json()["id"]
    rows = {r["label"]: r["value"] for r in ApprovalRequest.objects.get().payload["display"]["rows"]}
    with committed():
        assert web_client(owner).post(f"{APPROVALS}/{request_id}/approve").status_code == 200
    return rows


def test_durdur_is_a_media_stop_card_and_approve_sends_one_post(
    media_tv, web_voice, web_client, owner, workspace, ha, calls, committed
):
    ha.states[TV]["state"] = "playing"
    events = web_voice("Hey Friday, TV'yi durdur")
    draft = _draft(events)
    assert draft["tool_name"] == "ha_call_service"
    assert draft["payload"] == {"arguments": _call("media_stop")}
    assert reply_text(events) == "Onay kartı hazır: Salon TV · durdur. Kararı ekranda verirsin."
    assert calls == [] and not ModelUsage.objects.exists()
    assert not ApprovalRequest.objects.exists() and ha.posts == []

    rows = _tap_and_approve(web_client, owner, workspace, draft, committed)
    assert rows["Hizmet"] == "media_player.media_stop"
    assert ha.posts == [("/api/services/media_player/media_stop", {"entity_id": TV})]


def test_duraklat_stays_media_pause(media_tv, web_voice):
    assert _draft(web_voice("TV'yi duraklat"))["payload"] == {"arguments": _call("media_pause")}


def test_oynat_duraklat_is_a_play_pause_card_showing_the_outcome(
    media_tv, web_voice, web_client, owner, workspace, ha, calls, committed
):
    ha.states[TV]["state"] = "playing"
    events = web_voice("televizyonu oynat duraklat")
    draft = _draft(events)
    assert draft["payload"] == {"arguments": _call("media_play_pause")}
    assert calls == [] and ha.posts == []
    rows = _tap_and_approve(web_client, owner, workspace, draft, committed)
    assert rows["Sonuç (istek anındaki duruma göre)"] == "şu an oynatılıyor → duraklatılır"
    assert ha.posts == [("/api/services/media_player/media_play_pause", {"entity_id": TV})]
    assert ha.states[TV]["state"] == "paused"


def test_english_stop_and_play_pause_draft_the_new_services(media_tv, web_voice):
    assert _draft(web_voice("stop the TV"))["payload"] == {"arguments": _call("media_stop")}
    assert _draft(web_voice("play pause the TV"))["payload"] == {
        "arguments": _call("media_play_pause")
    }


@pytest.mark.parametrize(
    ("text", "reply"),
    [
        ("TV'yi durdur", "Bunu yapmaya iznim yok: Salon TV için 'durdur' açık değil."),
        (
            "TV'yi oynat duraklat",
            "Bunu yapmaya iznim yok: Salon TV için 'oynat duraklat' açık değil.",
        ),
    ],
)
def test_an_entity_without_the_service_is_refused_not_guessed(
    home, web_voice, calls, ha, text, reply
):
    """The TV allows `media_pause` but not `media_stop`: "durdur" is the
    fixed refusal, never a pause card instead."""
    home.tv.services = [*home.tv.services, "media_pause", "media_play"]
    home.tv.save()
    events = web_voice(text)
    assert _draft(events) is None
    assert reply_text(events) == reply
    assert calls == [] and ha.requests == []
    assert not ApprovalRequest.objects.exists()
