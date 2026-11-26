"""Phase 13 D6/D9: node-action proposals on the "which PC" resolver, whole
chat turns over HTTP with two v2 nodes of the owner ("MacBook", the
`node_world` node, and "Mac mini"), the real gate and the Python reference
node for the dispatch.

- the model's `node_id` is checked, never trusted: a draft for Mac mini only
  when the owner's message names it; a model pick the owner's words
  contradict, or a pick among two unnamed online nodes, yields no draft and
  the turn answers with Friday's fixed text (no chat completion);
- an approval for MacBook dispatched while Mac mini is the active node still
  goes to MacBook only;
- the targets block names each node's name, room, role and platform (data);
- the approval card's summary names the node, its room and platform.
"""

import json

import pytest

from ai_core.models import ModelUsage
from approvals.models import ApprovalRequest
from core.channels import CHANNEL_WEB
from nodes import bus
from nodes.activity import mark_node_active
from nodes.models import NodeActionExecution, NodeInvocation
from nodes.services import relabel_node
from nodes.tests.two_nodes import add_node
from tools import node_actions
from tools.tests.test_node_action_trust import (  # noqa: F401 -- fixtures
    ACME,
    Message,
    click_card,
    conversation_in,
    drafts,
    friday,
    purposes,
    turn,
)
from workspaces import rooms

pytestmark = pytest.mark.django_db


@pytest.fixture
def mini(node_world):
    node = add_node(
        node_world.owner, name="Mac mini", platform="macos", role="workstation", protocol_version=2
    )
    yield node
    bus.clear_presence(node.pk)
    bus.clear_inbox(node.pk)


def propose_on(friday, node):  # noqa: F811 -- the fixture's state dict
    friday["router.intent"] = json.dumps({"intent": "tool_request", "tool_name": "node_action"})
    friday["router.node_action_args"] = json.dumps(
        {"node_id": str(node.pk), "capability": "repo.command.run", "args": ACME}
    )


def reply_of(conversation):
    return Message().objects.get(conversation=conversation, role="assistant").content


def no_node_call(world, *nodes):
    assert not ApprovalRequest.objects.exists()
    assert not NodeActionExecution.objects.exists()
    assert not NodeInvocation.objects.exists()
    for node in nodes:
        assert bus.drain_inbox(node.pk) == []


def test_a_draft_for_the_named_node(node_world, friday, web_client, mini):  # noqa: F811
    conversation = conversation_in(node_world, node_world.firm)
    propose_on(friday, mini)
    events = turn(
        web_client(node_world.owner), conversation, "Mac mini'de acme testlerini çalıştır"
    )
    (draft,) = drafts(events)
    assert draft["payload"]["node_id"] == str(mini.pk)


def test_an_unnamed_pick_among_two_online_nodes_asks(node_world, friday, web_client, mini):  # noqa: F811
    conversation = conversation_in(node_world, node_world.firm)
    propose_on(friday, mini)
    events = turn(web_client(node_world.owner), conversation, "acme testlerini çalıştır")
    assert drafts(events) == []
    assert reply_of(conversation).startswith("Hangi bilgisayar: Mac mini mi, MacBook mu?")
    user = Message().objects.get(conversation=conversation, role="user")
    assert user.intent == "chat" and user.intent_payload == {}
    # The router's two calls only: no chat completion for the fixed reply.
    assert purposes(friday) == ["router.intent", "router.node_action_args"]
    assert ModelUsage.objects.filter(purpose="chat").count() == 0
    no_node_call(node_world, node_world.node, mini)


def test_a_model_pick_the_owners_words_contradict_is_refused(node_world, friday, web_client, mini):  # noqa: F811
    conversation = conversation_in(node_world, node_world.firm)
    propose_on(friday, mini)
    events = turn(web_client(node_world.owner), conversation, "MacBook'ta acme testlerini çalıştır")
    assert drafts(events) == []
    assert "uyuşmuyor" in reply_of(conversation)
    no_node_call(node_world, node_world.node, mini)


def test_the_last_active_node_takes_an_unnamed_request(node_world, friday, web_client, mini):  # noqa: F811
    mark_node_active(mini.pk)
    conversation = conversation_in(node_world, node_world.firm)
    propose_on(friday, mini)
    (draft,) = drafts(turn(web_client(node_world.owner), conversation, "acme testlerini çalıştır"))
    assert draft["payload"]["node_id"] == str(mini.pk)


def test_a_named_offline_node_is_refused_naming_it(node_world, friday, web_client, mini):  # noqa: F811
    bus.clear_presence(mini.pk)
    conversation = conversation_in(node_world, node_world.firm)
    propose_on(friday, mini)
    events = turn(
        web_client(node_world.owner), conversation, "Mac mini'de acme testlerini çalıştır"
    )
    assert drafts(events) == []
    assert reply_of(conversation).startswith("Mac mini şu an çevrimdışı")


def test_an_approval_for_a_goes_to_a_while_b_is_active(
    node_world,
    friday,  # noqa: F811
    web_client,
    make_link,
    mini,
    django_capture_on_commit_callbacks,
):
    from tools.tests.conftest import script

    link = make_link(scripts=[script(("stdout", "ok\n"))])
    client = web_client(node_world.owner)
    conversation = conversation_in(node_world, node_world.firm)
    propose_on(friday, node_world.node)
    (draft,) = drafts(turn(client, conversation, "MacBook'ta acme testlerini çalıştır"))
    assert click_card(client, node_world.firm, draft).status_code == 201
    approval = ApprovalRequest.objects.get()
    assert approval.payload["node_id"] == str(node_world.node.pk)
    # The owner now works on the Mac mini: it becomes the active node.
    mark_node_active(mini.pk)
    with django_capture_on_commit_callbacks(execute=True):
        decided = client.post(f"/api/v1/approvals/requests/{approval.pk}/approve", format="json")
    assert decided.status_code == 200, decided.content
    link.pump()
    execution = NodeActionExecution.objects.get(approval=approval)
    assert execution.node_id == node_world.node.pk and execution.status == "ok"
    assert {row.node_id for row in NodeInvocation.objects.all()} == {node_world.node.pk}
    assert bus.drain_inbox(mini.pk) == []


def test_the_targets_block_names_room_role_and_platform(node_world, mini):
    study = rooms.create_room(
        actor=node_world.owner,
        workspace=node_world.personal,
        channel=CHANNEL_WEB,
        name="Çalışma odası",
    )
    relabel_node(actor=node_world.owner, node=mini, channel=CHANNEL_WEB, room_id=study.pk)
    targets = node_actions.proposal_targets(workspace=node_world.firm, actor=node_world.owner)
    by_name = {target["node_name"]: target for target in targets}
    assert set(by_name) == {"MacBook", "Mac mini"}
    assert by_name["Mac mini"]["node_room"] == "Çalışma odası"
    assert by_name["Mac mini"]["node_role"] == "workstation"
    assert by_name["Mac mini"]["node_platform"] == "macos"
    assert by_name["MacBook"]["node_room"] == ""
    assert str(node_world.other_node.pk) not in json.dumps(targets)


def test_a_killed_or_revoked_node_is_no_target(node_world, mini):
    mini.device.revoked_at = mini.created_at
    mini.device.save(update_fields=["revoked_at"])
    targets = node_actions.proposal_targets(workspace=node_world.firm, actor=node_world.owner)
    assert [target["node_name"] for target in targets] == ["MacBook"]


def test_the_approval_card_names_the_node_room_and_platform(
    node_world,
    friday,  # noqa: F811
    web_client,
    mini,
):
    study = rooms.create_room(
        actor=node_world.owner,
        workspace=node_world.personal,
        channel=CHANNEL_WEB,
        name="Çalışma odası",
    )
    relabel_node(actor=node_world.owner, node=mini, channel=CHANNEL_WEB, room_id=study.pk)
    client = web_client(node_world.owner)
    conversation = conversation_in(node_world, node_world.firm)
    propose_on(friday, mini)
    (draft,) = drafts(turn(client, conversation, "Mac mini'de acme testlerini çalıştır"))
    assert click_card(client, node_world.firm, draft).status_code == 201
    listed = client.get("/api/v1/approvals/requests", {"workspace": str(node_world.firm.pk)}).json()
    rows = listed["results"] if isinstance(listed, dict) else listed
    (summary,) = [row["display_summary"] for row in rows]
    assert summary["node_name"] == "Mac mini"
    assert summary["node_room"] == "Çalışma odası"
    assert summary["node_platform"] == "macos"


def test_validate_proposal_refusal_shape(node_world, mini):
    from tools.registry import ToolCallContext

    proposal = {"node_id": str(mini.pk), "capability": "repo.command.run", "args": ACME}
    refused = node_actions.validate_proposal(
        workspace=node_world.firm,
        actor=node_world.owner,
        proposal=proposal,
        context=ToolCallContext(source_text="MacBook'ta acme testleri"),
    )
    assert refused["refusal"] == "target_mismatch" and refused["reply"]
    cleaned = node_actions.validate_proposal(
        workspace=node_world.firm,
        actor=node_world.owner,
        proposal=proposal,
        context=ToolCallContext(source_text="acme testleri", origin_node_id=str(mini.pk)),
    )
    assert cleaned["node_id"] == str(mini.pk) and cleaned["class"] == "gate"
