# ruff: noqa: F811 -- pytest fixtures imported from the helpers are redefined as arguments
"""Phase 13b branch 1c (ADR-018, D10): the direct media class on the node
voice channel -- the server-side contract the Mac session (M2) must pass
with the existing `friday-node` app (no protocol change).

A `node_voice` token minted with a paired node's secret (exactly as the
Swift host and the Phase 11 reference voice host do), on a live session
conversation: "Hey Friday, TV'yi duraklat" pauses the flagged TV with one
Home Assistant call, no card and no model call; the call and its audit row
name origin `node` and the **speaking node's id** (the voice token's
device). The body cannot change the origin; a non-flagged TV is still the
card; the node voice token still cannot reach an approval."""

import pytest
from django.apps import apps

from ai_core.models import ModelUsage
from approvals.models import ApprovalRequest
from assistant.models import Message
from assistant.services import create_conversation
from assistant.tests.home_direct_fixtures import (  # noqa: F401 -- fixtures
    TV,
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
    workspace,
)
from audit.models import AuditLog
from connectors.models import HomeDirectCall

pytestmark = pytest.mark.django_db


@pytest.fixture
def live(owner, workspace):
    return create_conversation(workspace=workspace, actor=owner, channel="node_voice")


def _row_counts():
    return {
        model._meta.label: model.objects.count()
        for model in apps.get_models()
        if not model._meta.proxy and model._meta.managed
    }


def test_a_node_voice_pause_is_one_direct_call_naming_the_node(home, node, live, calls, ha):
    before = _row_counts()
    events = chat_turn(node.client, live, "Hey Friday, TV'yi duraklat")
    after = _row_counts()
    assert draft_of(events) is None
    assert reply_text(events) == "Salon TV duraklatıldı."
    assert events[-1]["usage"] == {"input_tokens": 0, "output_tokens": 0, "estimated_cost": "0"}
    assert ha.posts == [("/api/services/media_player/media_pause", {"entity_id": TV})]
    assert calls == [] and not ModelUsage.objects.exists()
    changed = {k: after[k] - before[k] for k in after if after[k] != before[k]}
    # The turn's two messages, the call row and its one audit row -- no
    # approval, no write execution, no model usage.
    assert changed == {"assistant.Message": 2, "connectors.HomeDirectCall": 1, "audit.AuditLog": 1}
    call = HomeDirectCall.objects.get()
    assert (call.origin, str(call.node)) == ("node", str(node.device.pk))
    audit = AuditLog.objects.get(action="ha.direct_call")
    assert audit.metadata["origin"] == "node"
    assert audit.metadata["node"] == str(node.device.pk)
    user = Message.objects.get(conversation=live, role="user")
    assert (user.channel, user.input_mode, user.intent) == ("node_voice", "voice", "home_direct")
    assert user.intent_payload["node_id"] == str(node.device.pk)


@pytest.mark.parametrize("asserted", [None, "text"])
def test_the_body_cannot_change_the_origin(home, node, live, ha, asserted):
    body = {"input_mode": asserted} if asserted else {}
    events = chat_turn(node.client, live, "TV'yi duraklat", **body)
    assert draft_of(events) is None
    assert HomeDirectCall.objects.get().origin == "node"


def test_a_non_flagged_tv_is_still_the_card_on_node_voice(home, node, live, ha):
    home.tv.direct = False
    home.tv.save()
    events = chat_turn(node.client, live, "TV'yi duraklat")
    draft = draft_of(events)
    assert draft["payload"]["arguments"]["service"] == "media_pause"
    assert draft["direct_reason"] == "not_flagged"
    assert ha.posts == [] and not HomeDirectCall.objects.exists()


def test_the_node_voice_token_still_cannot_reach_an_approval(home, node, live, workspace, ha):
    home.tv.direct = False
    home.tv.save()
    draft = draft_of(chat_turn(node.client, live, "TV'yi duraklat"))
    created = node.client.post(
        "/api/v1/approvals/requests",
        {
            "workspace": str(workspace.id),
            "tool_name": draft["tool_name"],
            "payload": draft["payload"],
        },
        format="json",
    )
    assert created.status_code in (401, 403), created.content
    assert not ApprovalRequest.objects.exists()
    assert ha.posts == []


def test_a_second_node_is_recorded_as_itself(home, owner, live, ha, n8n_chat):
    from accounts.tests.node_voice_helpers import bearer, mint_token, pair_node

    device, secret = pair_node(owner, name="Ev PC")
    events = chat_turn(bearer(mint_token(secret)), live, "TV'yi duraklat")
    assert draft_of(events) is None
    assert str(HomeDirectCall.objects.get().node) == str(device.pk)
