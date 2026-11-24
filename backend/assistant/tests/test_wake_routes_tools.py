# ruff: noqa: F811 -- pytest fixtures imported from the connectors test helpers are redefined
"""Phase 12 D8 (branch 4): `tool.<name>` wake routes. An owner alias for a
read tool yields exactly the router's `connector_read` turn (arguments from
the words after the alias, through `router.connector_args`; the async read
path included); an alias for a write tool yields exactly the router's
`tool_request` draft -- a card, never an approval. No `router.intent` call
on a match; an unknown phrase and every typed turn go to the router."""

import pytest

from approvals.models import ApprovalRequest
from assistant import wake_routes
from assistant.models import Message, WakeRouteAlias
from assistant.wake_aliases import WakeAliasConflict, create_wake_alias
from connectors.manifest import get_manifest
from connectors.tests.conftest import committed, enable, n8n, n8n_state  # noqa: F401
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

pytestmark = pytest.mark.django_db

READ_ROUTE = "tool.n8n_server_status"
WRITE_ROUTE = "tool.mcp_n8n_home_backup"


@pytest.fixture
def owner(db):
    from accounts.tests.node_voice_helpers import make_owner

    return make_owner()


@pytest.fixture
def workspace(owner):
    from workspaces.models import Workspace

    return Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)


@pytest.fixture
def aliases(owner, workspace):
    for route_name, phrase in ((READ_ROUTE, "sunucu durumu"), (WRITE_ROUTE, "ev yedeği")):
        create_wake_alias(
            actor=owner, channel="web", workspace=workspace, route=route_name, phrase=phrase
        )


@pytest.fixture
def ready(owner, workspace, n8n, enable, aliases):
    enable(get_manifest("n8n"), workspace)


def _purposes(calls):
    return [c["purpose"] for c in calls]


def test_tool_routes_exist_for_registered_tools_only():
    names = {route.name for route in wake_routes.tool_routes()}
    assert {READ_ROUTE, WRITE_ROUTE} <= names
    assert "tool.node_action" not in names
    by_name = {route.name: route for route in wake_routes.tool_routes()}
    assert by_name[READ_ROUTE].effect == "read"
    assert by_name[WRITE_ROUTE].effect == "proposal"
    assert all(route.effect in ("read", "proposal") for route in by_name.values())
    assert wake_routes.get_route("tool.no_such_tool") is None
    assert wake_routes.tool_route_label(READ_ROUTE) == "Sunucu durumu"
    assert wake_routes.tool_route_label(WRITE_ROUTE) == "Ev yedeği"


def test_match_turn_takes_the_alias_as_leading_words():
    aliases = {READ_ROUTE: ["sunucu durumu"], WRITE_ROUTE: ["ev yedeği", "ev yedeği bulut"]}
    route, rest = wake_routes.match_turn("Hey Friday, sunucu durumu tam lütfen", aliases=aliases)
    assert (route.name, rest) == (READ_ROUTE, "tam")
    route, rest = wake_routes.match_turn("EV YEDEĞİ bulut hemen", aliases=aliases)
    assert (route.name, rest) == (WRITE_ROUTE, "hemen")  # longest alias first
    route, rest = wake_routes.match_turn("sunucu durumu", aliases=aliases)
    assert (route.name, rest) == (READ_ROUTE, "")
    assert wake_routes.match_turn("sunucular durumu", aliases=aliases) == (None, "")
    # Zero-argument routes keep their whole-utterance rule and win first.
    assert wake_routes.match_turn("günaydın", aliases=aliases)[0].name == "digest.today"
    # An alias whose tool is gone routes nowhere.
    assert wake_routes.match_turn("eski araç", aliases={"tool.gone": ["eski araç"]}) == (None, "")


def test_a_read_alias_routes_a_node_voice_turn_to_the_async_read(
    ready, n8n, voice, live, calls, route, committed
):
    route(None, {"scope": "full"})  # the router would say "chat": it is never asked
    with committed():
        events = chat_turn(voice, live, "Hey Friday, sunucu durumu tam")
    assert reply_text(events).startswith("Başlattım: Sunucu durumu.")
    # No `router.intent` call: the argument step, then the follow-up's one.
    assert _purposes(calls) == ["router.connector_args", "chat.followup"]
    # The arguments are extracted from the words after the alias.
    assert '"content": "tam"' in calls[0]["messages"]
    assert n8n.calls and n8n.calls[0][1] == {"scope": "full"}
    user = Message.objects.get(conversation=live, role="user")
    assert user.route == READ_ROUTE
    assert user.intent == "connector_read"
    assert Message.objects.filter(conversation=live, followup_of=user).count() == 1


def test_a_write_alias_yields_a_draft_card_only(ready, n8n, voice, live, calls, route):
    route(None, {"target": "nas"})
    events = chat_turn(voice, live, "Hey Friday, ev yedeği nas")
    draft = next(e for e in events if e["type"] == "draft")
    assert draft["tool_name"] == "mcp_n8n_home_backup"
    assert draft["payload"] == {"arguments": {"target": "nas"}}
    assert "router.intent" not in _purposes(calls)
    assert _purposes(calls) == ["router.connector_args", "chat"]
    assert not ApprovalRequest.objects.exists()
    assert n8n.calls == []
    user = Message.objects.get(conversation=live, role="user")
    assert (user.route, user.intent) == (WRITE_ROUTE, "tool_request")


def test_an_unknown_phrase_goes_to_the_router(ready, n8n, voice, live, calls, route):
    route(None)
    chat_turn(voice, live, "Hey Friday, bugün hava nasıl")
    assert _purposes(calls)[0] == "router.intent"
    assert Message.objects.get(conversation=live, role="user").route is None


def test_a_typed_turn_is_never_routed(
    ready, owner, n8n, n8n_chat, web_client, conversation, calls, route
):
    route(None)
    chat_turn(web_client(owner), conversation, "sunucu durumu tam", input_mode="text")
    assert _purposes(calls)[0] == "router.intent"
    assert n8n.calls == []


def test_a_tool_alias_cannot_shadow_a_zero_argument_phrase(owner, workspace):
    with pytest.raises(WakeAliasConflict):
        create_wake_alias(
            actor=owner, channel="web", workspace=workspace, route=READ_ROUTE, phrase="brifing ver"
        )
    assert not WakeRouteAlias.objects.exists()
