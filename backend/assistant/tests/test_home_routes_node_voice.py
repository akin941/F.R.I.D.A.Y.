# ruff: noqa: F811 -- pytest fixtures imported from the helpers are redefined as arguments
"""Phase 13 D13 (branch 6): `home.control` on the node voice channel -- the
server-side contract the Mac session (M2) must pass.

A `node_voice` token minted with a paired node's secret (exactly as the
Swift host and the reference voice host do) and a live session
conversation:

- "Hey Friday, salondaki TV'yi aç" -> the `ha_call_service` draft card and
  the fixed spoken confirmation; the only rows written are the turn's two
  messages (no approval, execution, audit, `ModelUsage`); no Home Assistant
  request;
- the body cannot opt out of routing (node voice is always `voice`);
- **no voice path reaches an approval**: the node voice token can neither
  create the request from the card nor approve one -- the execution needs
  the owner's on-screen decision on `/approvals`;
- ask / refusal are fixed text with no card.

The real-child run (reference voice host + `friday-voice run`) is
`ops/tests/test_home_voice_e2e.py`."""

import pytest
from django.apps import apps

from accounts.tests.node_voice_helpers import bearer, mint_token, pair_node
from ai_core.models import ModelUsage
from approvals.models import ApprovalRequest
from assistant.models import Message
from assistant.services import create_conversation
from assistant.tests.test_home_routes import (  # noqa: F401
    DRAFT_REPLY,
    TURN_ON_TV,
    ha_settings,
    home,
)
from connectors.tests.conftest import committed, enable  # noqa: F401
from connectors.tests.fake_home_assistant import allow, ha  # noqa: F401
from connectors.tests.n8n_chat_fixtures import (  # noqa: F401 -- fixtures
    calls,
    chat_turn,
    conversation,
    n8n_chat,
    reply_text,
)
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

APPROVALS_URL = "/api/v1/approvals/requests"


@pytest.fixture
def owner(db):
    from accounts.tests.node_voice_helpers import make_owner

    return make_owner()


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)


@pytest.fixture
def voice(owner, n8n_chat):
    _, secret = pair_node(owner)
    return bearer(mint_token(secret))


@pytest.fixture
def live(owner, workspace):
    return create_conversation(workspace=workspace, actor=owner, channel="node_voice")


def _row_counts():
    return {
        model._meta.label: model.objects.count()
        for model in apps.get_models()
        if not model._meta.proxy and model._meta.managed
    }


def _draft(events):
    return next((e for e in events if e["type"] == "draft"), None)


def test_a_node_voice_home_command_is_a_draft_card_only(home, voice, live, calls, ha):
    before = _row_counts()
    events = chat_turn(voice, live, "Hey Friday, salondaki TV'yi aç")
    after = _row_counts()
    draft = _draft(events)
    assert draft["tool_name"] == "ha_call_service"
    assert draft["payload"] == {"arguments": TURN_ON_TV}
    assert reply_text(events) == DRAFT_REPLY
    assert events[-1]["usage"] == {"input_tokens": 0, "output_tokens": 0, "estimated_cost": "0"}
    changed = {k: after[k] - before[k] for k in after if after[k] != before[k]}
    assert changed == {"assistant.Message": 2}
    assert calls == [] and not ModelUsage.objects.exists()
    assert ha.requests == []
    user, reply = Message.objects.filter(conversation=live).order_by("created_at")
    assert user.channel == reply.channel == Message.Channel.NODE_VOICE
    assert (user.input_mode, user.route, user.intent) == ("voice", "home.control", "tool_request")
    assert reply.content == DRAFT_REPLY and reply.model_usage is None


@pytest.mark.parametrize("asserted", [None, "text"])
def test_the_body_cannot_opt_out_of_routing(home, voice, live, asserted):
    body = {"input_mode": asserted} if asserted else {}
    events = chat_turn(voice, live, "salondaki TV'yi aç", **body)
    assert _draft(events)["payload"] == {"arguments": TURN_ON_TV}


def test_no_voice_path_reaches_an_approval(home, voice, live, workspace, ha):
    draft = _draft(chat_turn(voice, live, "salondaki TV'yi aç"))
    created = voice.post(
        APPROVALS_URL,
        {
            "workspace": str(workspace.id),
            "tool_name": draft["tool_name"],
            "payload": draft["payload"],
        },
        format="json",
    )
    assert created.status_code in (401, 403), created.content
    assert not ApprovalRequest.objects.exists()


def test_a_voice_token_cannot_approve_a_pending_home_call(
    home, voice, live, workspace, web_client, owner, ha
):
    draft = _draft(chat_turn(voice, live, "salondaki TV'yi aç"))
    # The owner taps the card on screen: a pending request.
    created = web_client(owner).post(
        APPROVALS_URL,
        {
            "workspace": str(workspace.id),
            "tool_name": draft["tool_name"],
            "payload": draft["payload"],
        },
        format="json",
    )
    assert created.status_code == 201, created.content
    request = ApprovalRequest.objects.get()
    response = voice.post(f"{APPROVALS_URL}/{request.pk}/approve")
    assert response.status_code in (401, 403, 404), response.content
    request.refresh_from_db()
    assert request.status == ApprovalRequest.Status.PENDING
    assert ha.posts == []


def test_ask_and_refusal_on_node_voice_leave_no_card(home, voice, live, workspace):
    allow(
        workspace,
        "media_player.projeksiyon",
        ["turn_on"],
        label="Projeksiyon",
        room=home.salon,
        aliases=["tv"],
    )
    ask = chat_turn(voice, live, "salondaki TV'yi aç")
    assert _draft(ask) is None and reply_text(ask).endswith("Hangisi?")
    refusal = chat_turn(voice, live, "mutfaktaki TV'yi aç")
    assert _draft(refusal) is None
    assert reply_text(refusal) == "Bu odayı tanımıyorum: mutfaktaki."
    assert not ApprovalRequest.objects.exists()
    assert not ModelUsage.objects.exists()
