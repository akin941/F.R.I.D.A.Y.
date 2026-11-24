"""Phase 11 branch 1 (brief D4): the Phase 10 D13 node-action voice
regressions re-run over the node voice channel. "Testleri çalıştır" said
to the Mac yields the same `node_action` draft as on the web; it is a
draft only (nothing requested, nothing invoked), and once the owner clicks
the card on screen the approval carries voice evidence and is at best
`warn`.

The voice token is minted over HTTP with the reference node's own secret
(`node_world.node_secret`). `tools` tests may not import `assistant`, so
the live conversation is created through the app registry with
`opened_via="node_voice"`, which is what `POST /voice/live/sessions`
writes."""

import pytest
from rest_framework.test import APIClient

from approvals.models import ApprovalRequest
from tools.tests.test_node_action_trust import (  # noqa: F401 -- fixtures
    Conversation,
    Message,
    click_card,
    drafts,
    friday,
    nothing_executed,
    propose,
    purposes,
    turn,
)

CODE = {"repo_path": "~/code/friday", "command_kind": "test"}


def _voice_client(world):
    node = APIClient()
    node.credentials(HTTP_AUTHORIZATION=f"Bearer {world.node_secret}")
    response = node.post("/api/v1/auth/nodes/voice-token")
    assert response.status_code == 201, response.content
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.json()['token']}")
    return client


def _live(world):
    return Conversation().objects.create(
        workspace=world.personal, created_by=world.owner, opened_via="node_voice"
    )


@pytest.mark.django_db
def test_a_node_voice_turn_naming_a_node_action_yields_a_draft_and_nothing_else(
    node_world,
    friday,  # noqa: F811 -- the imported fixture
    make_link,
):
    link = make_link()
    conversation = _live(node_world)
    propose(friday, node_world.node, "repo.command.run", CODE)
    events = turn(_voice_client(node_world), conversation, "friday'de testleri çalıştır")
    (draft,) = drafts(events)
    assert draft["tool_name"] == "node_action" and draft["where_to_act"]
    assert draft["payload"]["args"] == CODE
    user = Message().objects.get(conversation=conversation, role="user")
    assert user.channel == "node_voice" and user.input_mode == "voice"
    assert "router.node_action_args" in purposes(friday)
    assert "control.trust_check" not in purposes(friday)
    link.pump()
    nothing_executed(node_world)
    assert [f for f in link.frames if f["type"] != "heartbeat"] == []


@pytest.mark.django_db
def test_the_voice_token_cannot_click_the_card(node_world, friday, web_client):  # noqa: F811
    conversation = _live(node_world)
    propose(friday, node_world.node, "repo.command.run", CODE)
    voice = _voice_client(node_world)
    (draft,) = drafts(turn(voice, conversation, "friday'de testleri çalıştır"))
    assert click_card(voice, node_world.personal, draft).status_code == 403
    assert not ApprovalRequest.objects.exists()
    # The owner clicks on screen: voice evidence, at best `warn`.
    assert click_card(web_client(node_world.owner), node_world.personal, draft).status_code == 201
    approval = ApprovalRequest.objects.get()
    assert approval.payload["trust"]["verdict"] == "warn"
    assert approval.payload["trust"]["reason_code"] == "voice_evidence"
    assert approval.payload["source_input_mode"] == "voice"
    assert approval.status == "pending"
