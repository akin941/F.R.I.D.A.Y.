# ruff: noqa: F811 -- pytest fixtures imported from the helpers are redefined as arguments
"""Phase 13b branch 1c (ADR-018, D10): the typed pre-router step.

A typed web turn whose words the deterministic home matcher resolves to one
draft ADR-018 admits as direct is answered with **no** `router.intent` call
(a typed direct turn makes fewer model calls, never more). Every other
typed text -- a non-direct home command ("salondaki TV'yi aç"), an
ambiguous one, a non-home question, a member's turn, a CLI PAT turn -- goes
to the router exactly as in Phase 13."""

import pytest

from accounts.tests.node_voice_helpers import CHAT_URL, make_pat, sse_events
from ai_core.models import ModelUsage
from assistant.models import Conversation, Message
from assistant.tests.home_direct_fixtures import (  # noqa: F401 -- fixtures
    TV,
    allow,
    calls,
    chat_turn,
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
from connectors.models import HomeDirectCall
from workspaces.models import WorkspaceMember

pytestmark = pytest.mark.django_db


def test_typed_tv_yi_duraklat_is_direct_without_the_router(turn, ha, calls):
    events, user = turn("chat", "TV'yi duraklat")
    assert calls == []  # no router.intent, no completion
    assert not ModelUsage.objects.exists()
    assert draft_of(events) is None
    assert reply_text(events) == "Salon TV duraklatıldı."
    assert ha.posts == [("/api/services/media_player/media_pause", {"entity_id": TV})]
    call = HomeDirectCall.objects.get()
    assert (call.origin, call.node) == ("chat", None)
    assert (user.input_mode, user.intent, user.route) == ("text", "home_direct", "home.control")


def test_a_typed_non_direct_home_command_goes_to_the_router(turn, ha, calls, route):
    route(None)
    events, user = turn("chat", "salondaki TV'yi aç")
    assert calls and calls[0]["purpose"] == "router.intent"
    assert user.route is None and user.intent == "chat"
    assert ha.posts == [] and not HomeDirectCall.objects.exists()


def test_the_phase_13_router_card_path_is_unchanged_for_typed_turns(turn, ha, calls, route):
    turn_on = {"domain": "media_player", "service": "turn_on", "entity_id": TV, "data": {}}
    route("ha_call_service", turn_on, intent="tool_request")
    events, user = turn("chat", "salondaki TV'yi aç")
    draft = draft_of(events)
    assert draft["payload"] == {"arguments": turn_on}
    assert "direct_reason" not in draft
    assert user.intent == "tool_request"
    assert ha.posts == []


def test_a_typed_ambiguous_command_goes_to_the_router(turn, workspace, home, ha, calls, route):
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
    route(None)
    _, user = turn("chat", "TV'yi duraklat")
    assert calls and calls[0]["purpose"] == "router.intent"
    assert ha.posts == [] and not HomeDirectCall.objects.exists()
    assert user.intent == "chat"


def test_a_non_home_question_goes_to_the_router(turn, calls, route, ha):
    route(None)
    turn("chat", "bugün hava nasıl")
    assert calls[0]["purpose"] == "router.intent"
    assert ha.requests == []


def test_a_members_typed_turn_is_never_direct(home, web_client, workspace, calls, route, ha):
    from accounts.tests.node_voice_helpers import other_user

    member, _ = other_user()
    WorkspaceMember.objects.get_or_create(
        workspace=workspace, user=member, defaults={"role": WorkspaceMember.Role.MEMBER}
    )
    conversation = Conversation.objects.create(workspace=workspace, created_by=member)
    route(None)
    chat_turn(web_client(member), conversation, "TV'yi duraklat")
    assert calls and calls[0]["purpose"] == "router.intent"
    assert ha.posts == [] and not HomeDirectCall.objects.exists()


def test_a_cli_pat_turn_is_never_direct(home, owner, workspace, calls, route, ha):
    raw, _ = make_pat(owner, workspace, "cli")
    from rest_framework.test import APIClient

    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    route(None)
    response = client.post(
        CHAT_URL.format(conversation.pk), {"content": "TV'yi duraklat"}, format="json"
    )
    assert response.status_code == 200, response.content
    sse_events(response)
    assert calls and calls[0]["purpose"] == "router.intent"
    assert ha.posts == [] and not HomeDirectCall.objects.exists()
    user = Message.objects.get(conversation=conversation, role="user")
    assert user.channel == "cli" and user.intent != "home_direct"
