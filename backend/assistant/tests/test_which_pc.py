"""Phase 13 D6, the chat-turn plumbing of "which PC" (`assistant` tests may
not reach `nodes`, so the node side is faked here; the real resolver, the
reference nodes and whole turns are in `tools/tests/test_node_targeting_*`
and `test_one_node_invariant.py`):

- a read tool that accepts the `ToolCallContext` gets the owner's own
  message as `source_text`, `origin_node_id` only on a `node_voice` turn
  (the token's node device) and the turn's `input_mode`;
- a read refused with a Friday-authored `reply` (the ask template) ends the
  turn with that text: no chat completion, only the router's calls;
- the node-action proposal validator gets the same context, and its
  `{"refusal", "reply"}` answer is no draft and that reply;
- a read tool that does not accept the context is called exactly as before.
"""

import json

import pytest
from rest_framework import serializers

from accounts.tests.node_voice_helpers import CHAT_URL, bearer, make_owner, mint_token, pair_node
from accounts.tests.node_voice_helpers import sse_events as events_of
from ai_core.adapters.fake import FakeAdapter
from ai_core.models import ModelProvider, ModelUsage, PromptTemplate, PromptVersion
from assistant.models import Conversation, Message
from assistant.services import NODE_ACTION_TOOL, create_conversation
from core.channels import CHANNEL_NODE_VOICE, CHANNEL_WEB
from tools import node_action_ports
from tools.registry import (
    DATA_CLASS_FIRM,
    ReadResult,
    ReadToolError,
    ReadToolSpec,
    register_read_tool,
    unregister_read_tool,
)
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db


class NoArgs(serializers.Serializer):
    pass


ASK = "Hangi bilgisayar: MacBook mu, Masaüstü mü?"
NODE_ID = "11111111-1111-4111-8111-111111111111"


def _prompt(name, purpose, content):
    template = PromptTemplate.objects.create(name=name, purpose=purpose)
    version = PromptVersion.objects.create(template=template, version=1, content=content)
    template.active_version = version
    template.save(update_fields=["active_version"])


@pytest.fixture
def router(settings, monkeypatch):
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    settings.AI_PURPOSE_TIERS = {}
    ModelProvider.objects.create(
        name="fake-chat",
        provider_type=ModelProvider.ProviderType.FAKE,
        model_id="fake-1",
        priority=0,
        personal_data_allowed=True,
    )
    _prompt("friday_system", "chat", "You are Friday.")
    _prompt("friday_router_intent", "router.intent", "Classify.")
    _prompt("friday_router_connector_args", "router.connector_args", "Args.")
    _prompt("friday_router_node_action_args", "router.node_action_args", "Node args.")
    state = {
        "router.intent": json.dumps({"intent": "connector_read", "tool_name": "fake_pc_read"}),
        "router.connector_args": "{}",
        "router.node_action_args": json.dumps(
            {"node_id": NODE_ID, "capability": "repo.command.run", "args": {"x": 1}}
        ),
        "chat": "Tamam.",
    }
    original = FakeAdapter._text_for

    def text_for(self, purpose, messages=None):
        if purpose in state:
            return state[purpose]
        return original(self, purpose, messages)

    monkeypatch.setattr(FakeAdapter, "_text_for", text_for)
    return state


@pytest.fixture
def read_tool():
    seen = {"contexts": [], "reply": ASK}

    def execute(*, workspace, actor, args, channel, context=None):
        seen["contexts"].append(context)
        if seen["reply"]:
            raise ReadToolError("node_ambiguous", reply=seen["reply"])
        return ReadResult(text="rows", size=4)

    register_read_tool(
        ReadToolSpec(
            name="fake_pc_read",
            description="A fake read of the owner's PC.",
            argument_serializer=NoArgs,
            data_class=DATA_CLASS_FIRM,
            channels=frozenset({CHANNEL_WEB, CHANNEL_NODE_VOICE}),
            execute=execute,
            is_available=lambda workspace, context=None: True,
            source="friday-node",
            accepts_context=True,
        )
    )
    yield seen
    unregister_read_tool("fake_pc_read")


@pytest.fixture
def owner():
    return make_owner()


@pytest.fixture
def personal(owner):
    return Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)


def turn(client, conversation, content):
    response = client.post(CHAT_URL.format(conversation.pk), {"content": content}, format="json")
    assert response.status_code == 200, response.content
    return events_of(response)


def reply_of(conversation):
    return Message.objects.get(conversation=conversation, role=Message.Role.ASSISTANT).content


def purposes():
    return list(ModelUsage.objects.order_by("created_at").values_list("purpose", flat=True))


def test_a_web_read_gets_the_owners_words_and_no_origin(
    router, read_tool, owner, personal, web_client
):
    conversation = Conversation.objects.create(workspace=personal, created_by=owner)
    turn(web_client(owner), conversation, "Claude oturumlarım ne durumda?")
    (context,) = read_tool["contexts"]
    assert context.source_text == "Claude oturumlarım ne durumda?"
    assert context.origin_node_id is None
    assert context.input_mode == "text"


def test_an_ask_reply_ends_the_turn_without_a_chat_completion(
    router, read_tool, owner, personal, web_client
):
    conversation = Conversation.objects.create(workspace=personal, created_by=owner)
    events = turn(web_client(owner), conversation, "oturumlarım")
    assert reply_of(conversation) == ASK
    assert events[-1]["type"] == "done"
    assert purposes() == ["router.intent", "router.connector_args"]
    user = Message.objects.get(conversation=conversation, role=Message.Role.USER)
    assert user.intent == Message.Intent.CONNECTOR_READ
    assert user.intent_payload["outcome"] == "node_ambiguous"


def test_a_resolved_read_still_gets_its_chat_completion(
    router, read_tool, owner, personal, web_client
):
    read_tool["reply"] = ""
    conversation = Conversation.objects.create(workspace=personal, created_by=owner)
    turn(web_client(owner), conversation, "MacBook'ta oturumlarım")
    assert "chat" in purposes()


def test_a_node_voice_turn_carries_the_speaking_node(router, read_tool, owner, personal):
    device, secret = pair_node(owner)
    live = create_conversation(workspace=personal, actor=owner, channel="node_voice")
    turn(bearer(mint_token(secret)), live, "oturumlarım ne durumda")
    (context,) = read_tool["contexts"]
    assert context.origin_node_id == str(device.pk)
    assert context.input_mode == "voice"
    assert reply_of(live) == ASK


@pytest.fixture
def ports():
    seen = {"contexts": [], "answer": {"refusal": "node_ambiguous", "reply": ASK}}

    def source(*, workspace, actor):
        return [{"node_id": NODE_ID, "node_name": "MacBook", "capabilities": []}]

    def validator(*, workspace, actor, proposal, context=None):
        seen["contexts"].append(context)
        return seen["answer"]

    state = node_action_ports.snapshot_ports()
    node_action_ports.register_proposal_source(source)
    node_action_ports.register_proposal_validator(validator)
    yield seen
    node_action_ports.restore_ports(state)


def test_a_node_action_refusal_is_no_draft_and_the_fixed_reply(
    router, ports, owner, personal, web_client
):
    router["router.intent"] = json.dumps({"intent": "tool_request", "tool_name": NODE_ACTION_TOOL})
    conversation = Conversation.objects.create(workspace=personal, created_by=owner)
    events = turn(web_client(owner), conversation, "testleri çalıştır")
    assert [e for e in events if e["type"] == "draft"] == []
    assert reply_of(conversation) == ASK
    assert purposes() == ["router.intent", "router.node_action_args"]
    (context,) = ports["contexts"]
    assert context.source_text == "testleri çalıştır" and context.origin_node_id is None
    user = Message.objects.get(conversation=conversation, role=Message.Role.USER)
    assert (user.intent, user.intent_payload) == (Message.Intent.CHAT, {})


def test_a_node_voice_proposal_gets_the_speaking_node(router, ports, owner, personal):
    router["router.intent"] = json.dumps({"intent": "tool_request", "tool_name": NODE_ACTION_TOOL})
    device, secret = pair_node(owner)
    live = create_conversation(workspace=personal, actor=owner, channel="node_voice")
    turn(bearer(mint_token(secret)), live, "testleri çalıştır")
    (context,) = ports["contexts"]
    assert context.origin_node_id == str(device.pk)
    assert context.input_mode == "voice"


def test_a_read_tool_without_context_is_called_as_before(router, owner, personal, web_client):
    calls = []

    def execute(*, workspace, actor, args, channel):  # no `context` keyword
        calls.append(channel)
        return ReadResult(text="ok", size=2)

    register_read_tool(
        ReadToolSpec(
            name="fake_plain_read",
            description="A plain read.",
            argument_serializer=NoArgs,
            data_class=DATA_CLASS_FIRM,
            channels=frozenset({CHANNEL_WEB}),
            execute=execute,
            is_available=lambda workspace: True,
            source="fake",
        )
    )
    try:
        router["router.intent"] = json.dumps(
            {"intent": "connector_read", "tool_name": "fake_plain_read"}
        )
        conversation = Conversation.objects.create(workspace=personal, created_by=owner)
        turn(web_client(owner), conversation, "oku")
        assert calls == [CHANNEL_WEB]
    finally:
        unregister_read_tool("fake_plain_read")
