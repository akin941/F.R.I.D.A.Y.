"""Router proposals of node actions (Phase 10 brief D13, branch 4), as a
unit: `router.node_action_args`, its strict parser, the proposal ports and
the degradations to plain chat. The ports are faked here (`assistant` tests
may not reach `nodes`); the real ports, the reference node and whole turns
are in `tools/tests/test_node_action_trust.py`."""

import json

import pytest

from accounts.services import register_user
from ai_core.adapters.base import AdapterError
from ai_core.adapters.fake import FakeAdapter
from ai_core.models import ModelProvider, ModelUsage, PromptTemplate, PromptVersion
from assistant.models import Conversation, Message
from assistant.services import (
    NODE_ACTION_TOOL,
    NodeActionArgsParseError,
    _tool_vocabulary_line,
    classify_intent,
    parse_node_action_args,
)
from core.channels import CHANNEL_WEB
from tools import node_action_ports
from tools.registry import list_tool_names
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"
NODE_ID = "11111111-1111-4111-8111-111111111111"
ARGS = {"repo_path": "~/work/auth-service", "command_kind": "test"}
TARGETS = [
    {
        "node_id": NODE_ID,
        "node_name": "MacBook",
        "capabilities": [{"capability": "repo.command.run", "class": "gate"}],
        "repo_prefixes": ["~/work"],
    }
]
NODE_INTENT = json.dumps({"intent": "tool_request", "tool_name": NODE_ACTION_TOOL})


def _proposal(**overrides):
    body = {"node_id": NODE_ID, "capability": "repo.command.run", "args": dict(ARGS)}
    body.update(overrides)
    return json.dumps(body)


@pytest.fixture(autouse=True)
def ai_settings(settings):
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    return settings


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def conversation(owner):
    workspace = Workspace.objects.get(owner=owner)
    return Conversation.objects.create(workspace=workspace, created_by=owner)


def _prompt(name, purpose, content):
    template = PromptTemplate.objects.create(name=name, purpose=purpose)
    version = PromptVersion.objects.create(template=template, version=1, content=content)
    template.active_version = version
    template.save(update_fields=["active_version"])
    return version


@pytest.fixture
def router(monkeypatch):
    ModelProvider.objects.create(
        name="fake-local", provider_type=ModelProvider.ProviderType.FAKE, model_id="f", priority=0
    )
    _prompt("friday_router_intent", "router.intent", "Classify.")
    _prompt("friday_router_node_action_args", "router.node_action_args", "Extract args.")
    state = {"intent": NODE_INTENT, "args": _proposal(), "calls": []}
    original = FakeAdapter._text_for

    def text_for(self, purpose, messages=None):
        if purpose in ("router.intent", "router.node_action_args"):
            state["calls"].append((purpose, messages))
            answer = state["intent" if purpose == "router.intent" else "args"]
            if isinstance(answer, Exception):
                raise answer
            return answer
        return original(self, purpose, messages)

    monkeypatch.setattr(FakeAdapter, "_text_for", text_for)
    return state


@pytest.fixture
def ports():
    """Fake proposal ports: the source lists `TARGETS`; the validator accepts
    only the exact `ARGS` on `NODE_ID` and records what it saw."""
    seen = {"source": [], "validator": []}

    def source(*, workspace, actor):
        seen["source"].append((workspace.pk, actor.pk))
        return seen.get("targets", TARGETS)

    def validator(*, workspace, actor, proposal, context=None):
        seen.setdefault("contexts", []).append(context)
        seen["validator"].append(proposal)
        if (
            proposal.get("node_id") == NODE_ID
            and proposal.get("capability") == "repo.command.run"
            and proposal.get("args") == ARGS
        ):
            return {**proposal, "class": "gate"}
        return None

    state = node_action_ports.snapshot_ports()
    node_action_ports.register_proposal_source(source)
    node_action_ports.register_proposal_validator(validator)
    yield seen
    node_action_ports.restore_ports(state)


def classify(conversation, content="auth-service'te testleri çalıştır", channel=CHANNEL_WEB):
    return classify_intent(
        conversation, actor=conversation.created_by, content=content, channel=channel
    )


# --- the vocabulary -----------------------------------------------------


def test_the_vocabulary_has_node_action_and_no_agent_gate():
    names = list_tool_names()
    assert NODE_ACTION_TOOL in names
    assert "harness_agent_action" not in names
    line = _tool_vocabulary_line()
    assert "node_action (run an action on the owner's paired Mac" in line


# --- the argument step --------------------------------------------------


def test_a_typed_request_yields_a_node_action_draft_payload(conversation, router, ports):
    result = classify(conversation)
    assert result["intent"] == Message.Intent.TOOL_REQUEST
    assert result["intent_payload"] == {
        "tool_name": NODE_ACTION_TOOL,
        "payload": {
            "node_id": NODE_ID,
            "capability": "repo.command.run",
            "args": ARGS,
            "source_message_id": "",
        },
        "action_class": "gate",
    }
    purposes = [purpose for purpose, _ in router["calls"]]
    assert purposes == ["router.intent", "router.node_action_args"]
    usage = ModelUsage.objects.get(purpose="router.node_action_args")
    assert usage.context_report["node_action_targets"] == 1
    assert ModelUsage.objects.count() == 2


def test_the_argument_step_sees_the_owner_message_and_the_targets_only(conversation, router, ports):
    classify(conversation)
    _, messages = router["calls"][1]
    assert [m["role"] for m in messages] == ["system", "user"]
    assert messages[1]["content"] == "auth-service'te testleri çalıştır"
    assert messages[0]["content"].startswith("Extract args.")
    assert json.dumps(TARGETS, ensure_ascii=False, sort_keys=True) in messages[0]["content"]


@pytest.mark.parametrize(
    "answer",
    [
        _proposal(args={"repo_path": "~/work/auth-service", "command_kind": "rm -rf"}),
        _proposal(node_id="22222222-2222-4222-8222-222222222222"),
        _proposal(capability="shell.exec"),
        '{"node_id": null, "capability": null, "args": null}',
        "not json",
        '{"node_id": "x", "capability": "y"}',
        '{"node_id": "x", "capability": "y", "args": [], "extra": 1}',
        '{"node_id": 1, "capability": "y", "args": {}}',
        '{"node_id": "x", "capability": "y", "args": "rm"}',
    ],
)
def test_an_answer_outside_the_contract_is_plain_chat(conversation, router, ports, answer):
    router["args"] = answer
    result = classify(conversation)
    assert result == {"intent": Message.Intent.CHAT, "intent_payload": {}}


def test_an_argument_step_provider_error_is_plain_chat(conversation, router, ports):
    router["args"] = AdapterError("provider_error", retryable=False)
    assert classify(conversation)["intent"] == Message.Intent.CHAT


def test_no_target_means_no_argument_call(conversation, router, ports):
    ports["targets"] = []
    assert classify(conversation)["intent"] == Message.Intent.CHAT
    assert [purpose for purpose, _ in router["calls"]] == ["router.intent"]


@pytest.mark.parametrize("channel", ["cli", "telegram", "openai", "", None])
def test_off_the_web_channel_nothing_is_proposed(conversation, router, ports, channel):
    assert classify(conversation, channel=channel)["intent"] == Message.Intent.CHAT
    assert [purpose for purpose, _ in router["calls"]] == ["router.intent"]
    assert ports["source"] == []


def test_unregistered_ports_propose_nothing(conversation, router):
    state = node_action_ports.snapshot_ports()
    node_action_ports.restore_ports(state[:2] + (None, None))
    try:
        assert classify(conversation)["intent"] == Message.Intent.CHAT
    finally:
        node_action_ports.restore_ports(state)


def test_the_validator_sees_the_parsed_untrusted_answer(conversation, router, ports):
    classify(conversation)
    assert ports["validator"] == [
        {"node_id": NODE_ID, "capability": "repo.command.run", "args": ARGS}
    ]


# --- the parser ---------------------------------------------------------


def test_parse_node_action_args_is_strict():
    assert parse_node_action_args('{"node_id": null, "capability": null, "args": null}') is None
    parsed = parse_node_action_args(_proposal())
    assert parsed["capability"] == "repo.command.run"
    for bad in ("", "[]", '{"node_id": "a", "capability": null, "args": null}'):
        with pytest.raises(NodeActionArgsParseError):
            parse_node_action_args(bad)
