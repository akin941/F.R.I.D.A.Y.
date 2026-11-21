"""Compression payload on a REAL `build_context` output (Phase 8B branch 6,
brief D11). A conversation with core blocks (incl. `self`), a memory block, a
fenced `<connector_result>` block and three history turns: the fake sidecar
receives exactly the assistant history messages, in order, and nothing else;
the system message (core blocks, `self`, memories, the connector read) and
every user message reach the adapter byte-identical. Plus the "done when"
web chat turn: compressed history with pre/post counts in its single
`ModelUsage` row. Fake provider, fake sidecar; no real LLM, no real Headroom."""

import copy

import pytest

from accounts.services import register_user
from ai_core import compression
from ai_core.adapters.fake import FakeAdapter
from ai_core.gateway import InferenceGateway
from ai_core.models import ModelProvider, ModelUsage, PromptTemplate, PromptVersion
from assistant.context import build_context, render_connector_block
from assistant.models import Conversation, Message
from core.channels import CHANNEL_WEB
from memory.services import create_memory, set_assembled_self_block, update_core_block
from tools.registry import ReadResult
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"
SELF_TEXT = "Friday OS is a personal AI OS; it runs on the owner's machine."
DEVELOPER_TEXT = "Friday'i Akın geliştiriyor."
MEMORY_TEXT = "The owner prefers filter coffee."
CONNECTOR_TEXT = "CI for PR 42: 3 checks green, 1 check red (lint)."
HISTORY = [
    ("user", "List my last coding sessions."),
    ("assistant", "Here are your sessions: " + "feat/p8 merged, tests green. " * 50),
    ("user", "And the deploy log?"),
    ("assistant", "Deploy summary: " + "step ok in 3 s, no warnings. " * 50),
    ("user", "Which branch is open?"),
    ("assistant", "Open branches: " + "feat/p8-headroom-stage, awaiting review. " * 30),
]
NEW_USER = "What failed on PR 42?"


@pytest.fixture(autouse=True)
def ai_settings(settings):
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    settings.AI_COMPRESSION_ENABLED = True
    settings.AI_COMPRESSION_PURPOSES = frozenset({"chat"})


@pytest.fixture
def owner():
    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def personal(owner):
    return Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)


@pytest.fixture
def providers(db):
    ModelProvider.objects.create(
        name="fake-local",
        provider_type=ModelProvider.ProviderType.FAKE,
        model_id="fake-1",
        priority=0,
        personal_data_allowed=True,
    )
    ModelProvider.objects.create(
        name="fake-embedding-local",
        provider_type=ModelProvider.ProviderType.FAKE,
        capability=ModelProvider.Capability.EMBEDDING,
        model_id="fake-embedding-1",
        priority=0,
        personal_data_allowed=True,
    )


def _prompt(name, purpose, content):
    template = PromptTemplate.objects.create(name=name, purpose=purpose)
    version = PromptVersion.objects.create(template=template, version=1, content=content)
    template.active_version = version
    template.save(update_fields=["active_version"])
    return version


@pytest.fixture
def system_prompt():
    return _prompt("friday_system", "chat", "You are Friday.")


@pytest.fixture
def conversation(owner, personal, system_prompt, providers):
    set_assembled_self_block(workspace=personal, content=SELF_TEXT)
    update_core_block(
        workspace=personal,
        label="developer",
        content=DEVELOPER_TEXT,
        actor=owner,
        channel=CHANNEL_WEB,
    )
    create_memory(
        workspace=personal,
        actor=owner,
        title="Coffee",
        content=MEMORY_TEXT,
        memory_type="preference",
        sensitivity="normal",
    )
    conv = Conversation.objects.create(workspace=personal, created_by=owner)
    for role, content in HISTORY:
        Message.objects.create(
            workspace=personal,
            conversation=conv,
            role=role,
            content=content,
            status=Message.Status.COMPLETE,
            created_by=owner if role == "user" else None,
        )
    return conv


class RecordingSidecar:
    def __init__(self):
        self.requests = []

    def compress(self, messages, *, model):
        self.requests.append(copy.deepcopy(messages))
        out = [{"role": "assistant", "content": m["content"][:40]} for m in messages]
        return {
            "messages": out,
            "tokens_before": sum(len(m["content"]) // 4 for m in messages),
            "tokens_after": sum(len(m["content"]) // 4 for m in out),
            "transforms_applied": ["fake"],
            "lossless": False,
        }


@pytest.fixture
def sidecar(monkeypatch):
    fake = RecordingSidecar()
    monkeypatch.setattr(compression, "get_client", lambda: fake)
    return fake


@pytest.fixture
def adapter_payloads(monkeypatch):
    payloads = []
    original = FakeAdapter._text_for

    def text_for(self, purpose, messages=None):
        payloads.append((purpose, copy.deepcopy(messages)))
        return original(self, purpose, messages)

    monkeypatch.setattr(FakeAdapter, "_text_for", text_for)
    return payloads


def _built(conversation, owner):
    block, report = render_connector_block(
        ReadResult(text=CONNECTOR_TEXT, size=len(CONNECTOR_TEXT)),
        tool="github_ci_status",
        source="github",
    )
    messages, context_report, prompt_version, *_ = build_context(
        conversation,
        actor=owner,
        new_user_content=NEW_USER,
        channel=CHANNEL_WEB,
        connector_block=block,
        connector_reads=[report],
    )
    return messages, context_report, prompt_version


def test_the_sidecar_sees_exactly_the_assistant_history_in_order(
    conversation, owner, sidecar, adapter_payloads
):
    messages, context_report, prompt_version = _built(conversation, owner)
    system = messages[0]["content"]
    # The fixture really holds every block D11 names.
    for text in (SELF_TEXT, DEVELOPER_TEXT, MEMORY_TEXT, CONNECTOR_TEXT):
        assert text in system
    assert "<connector_result" in system and "<core_blocks>" in system
    assert "<memories>" in system
    assert [m["role"] for m in messages] == ["system"] + [r for r, _ in HISTORY] + ["user"]
    original = copy.deepcopy(messages)

    InferenceGateway().complete(
        "chat",
        messages,
        actor=owner,
        workspace=conversation.workspace,
        prompt_version=prompt_version,
        context_report=context_report,
    )

    [sent] = sidecar.requests
    assert sent == [{"role": "assistant", "content": c} for r, c in HISTORY if r == "assistant"]
    sent_text = "\n".join(m["content"] for m in sent)
    for text in (SELF_TEXT, DEVELOPER_TEXT, MEMORY_TEXT, CONNECTOR_TEXT, NEW_USER, "You are Friday."):
        assert text not in sent_text
    for _role, content in HISTORY[::2]:  # no user message
        assert content not in sent_text

    [(purpose, payload)] = [p for p in adapter_payloads if p[0] == "chat"]
    assert payload[0] == original[0]  # the system message, byte-identical
    assert payload[0]["content"].encode() == original[0]["content"].encode()
    for index, message in enumerate(original):
        if message["role"] == "user":
            assert payload[index] == message
    assert payload[-1] == {"role": "user", "content": NEW_USER}
    assert [payload[i]["content"] for i in (2, 4, 6)] == [c[:40] for r, c in HISTORY if r == "assistant"]
    assert messages == original  # the caller's list is untouched


def test_a_web_chat_turn_records_compression_in_its_single_usage_row(
    conversation, owner, sidecar, adapter_payloads, web_client
):
    _prompt("friday_router_intent", "router.intent", "Classify the message.")
    response = web_client(owner).post(
        f"/api/v1/ai/conversations/{conversation.id}/messages",
        {"content": NEW_USER},
        format="json",
    )
    body = b"".join(response.streaming_content).decode()
    assert '"type": "done"' in body or '"type":"done"' in body
    [chat_usage] = ModelUsage.objects.filter(purpose="chat")
    report = chat_usage.context_report["compression"]
    assert report["applied"] is True
    assert report["pre_tokens"] > report["post_tokens"] > 0
    assert report["sent_messages"] == 3
    # The router call (bypass) is never sent to the sidecar, and only one
    # sidecar request was made for the turn.
    assert len(sidecar.requests) == 1
    router_rows = ModelUsage.objects.filter(purpose="router.intent")
    assert all("compression" not in r.context_report for r in router_rows)
    # The fake adapter's stream path reads the payload twice (text, then usage).
    payload = next(p for purpose, p in adapter_payloads if purpose == "chat")
    assert payload[0]["content"].startswith("You are Friday.")
    assert SELF_TEXT in payload[0]["content"]
    assert payload[-1] == {"role": "user", "content": NEW_USER}
