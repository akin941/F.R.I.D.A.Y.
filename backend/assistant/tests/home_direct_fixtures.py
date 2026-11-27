# ruff: noqa: F811 -- pytest fixtures imported from the helpers are redefined as arguments
"""Shared set-up for the Phase 13b branch 1c direct-media turn tests (ADR-018):
the owner, Home Assistant bound behind the fake (pin check through the real
service), the salon TV allowlisted with every media service and flagged
direct, and one turn helper per origin -- web voice, `node_voice` (a token
minted with a paired node's secret, exactly as the reference voice host and
the Swift node do) and typed web chat.

Import by name (`from assistant.tests.home_direct_fixtures import ...  #
noqa: F401`)."""

from types import SimpleNamespace

import pytest

from accounts.tests.node_voice_helpers import bearer, mint_token, pair_node
from assistant.models import Conversation, Message
from assistant.services import create_conversation
from connectors.manifest import get_manifest
from connectors.models import HomeDirectCall
from connectors.tests.conftest import committed, enable  # noqa: F401
from connectors.tests.fake_home_assistant import allow, ha, salon_tv_state  # noqa: F401
from connectors.tests.n8n_chat_fixtures import (  # noqa: F401 -- fixtures
    calls,
    chat_turn,
    n8n_chat,
    reply_text,
    route,
)
from core.speech import phrase_key
from workspaces.models import Room, Workspace

TV = "media_player.salon_tv"
MEDIA_SERVICES = [
    "media_play",
    "media_pause",
    "media_play_pause",
    "media_stop",
    "volume_up",
    "volume_down",
    "volume_set",
    "volume_mute",
]
TV_SERVICES = ["turn_on", "turn_off", "select_source", *MEDIA_SERVICES]
PAUSE = {"domain": "media_player", "service": "media_pause", "entity_id": TV, "data": {}}
ORIGINS = ("voice", "node", "chat")


@pytest.fixture
def owner(db):
    from accounts.tests.node_voice_helpers import make_owner

    return make_owner()


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)


@pytest.fixture(autouse=True)
def direct_settings(settings):
    settings.HA_ALLOWED_DOMAINS = []
    settings.HA_DIRECT_MEDIA_KILL_SWITCH = False
    settings.HA_DIRECT_RATE_GLOBAL_PER_MINUTE = 30
    settings.HA_DIRECT_RATE_ENTITY_PER_MINUTE = 10
    settings.HA_DIRECT_VOLUME_MAX = 0.6
    return settings


@pytest.fixture(autouse=True)
def clear_rate_windows():
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


def salon(workspace):
    return Room.objects.create(workspace=workspace, name="Salon", key=phrase_key("Salon"))


@pytest.fixture
def home(ha, enable, workspace, n8n_chat):
    """HA bound; the salon TV (every media service) flagged direct; the
    salon ceiling light (not media); a scene."""
    ha.states[TV] = salon_tv_state()
    enable(get_manifest("home_assistant"), workspace)
    room = salon(workspace)
    tv = allow(workspace, TV, TV_SERVICES, label="TV", room=room, aliases=["televizyon"])
    tv.direct = True
    tv.save()
    rows = SimpleNamespace(
        salon=room,
        tv=tv,
        light=allow(
            workspace, "light.salon_tavan", ["turn_on", "turn_off"], label="lamba", room=room
        ),
        scene=allow(workspace, "scene.film_gecesi", label="film gecesi"),
    )
    ha.requests.clear()
    return rows


@pytest.fixture
def node(owner, n8n_chat):
    device, secret = pair_node(owner)
    return SimpleNamespace(device=device, client=bearer(mint_token(secret)))


@pytest.fixture
def turn(web_client, owner, workspace, node, home):
    """`turn(origin, text)` -> (events, user message): one chat turn of the
    owner over the origin's own channel."""

    def _turn(origin, text, **body):
        if origin == "node":
            conversation = create_conversation(
                workspace=workspace, actor=owner, channel="node_voice"
            )
            events = chat_turn(node.client, conversation, text, **body)
        else:
            conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
            if origin == "voice":
                body = {"input_mode": "voice", **body}
            events = chat_turn(web_client(owner), conversation, text, **body)
        user = Message.objects.get(conversation=conversation, role="user")
        return events, user

    return _turn


def draft_of(events):
    return next((e for e in events if e["type"] == "draft"), None)


def tv_posts(ha):
    return [(path, body) for path, body in ha.posts]


def no_direct_call():
    return not HomeDirectCall.objects.exists()
