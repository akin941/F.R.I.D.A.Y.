"""Phase 4 branch 6 groundwork in the assistant pipeline: the reply as
structured events (web SSE, Telegram and the facade all consume them), the
router's `allowed_intents` (chat-only channels), and the channel / extra
fields on the chat call's context report."""

import pytest

from accounts.services import register_user
from ai_core.models import ModelProvider, ModelUsage, PromptTemplate, PromptVersion
from assistant.models import Conversation, Message
from assistant.services import (
    build_gateway_stream,
    classify_intent,
    encode_sse,
    iter_assistant_reply_events,
    stream_assistant_reply,
)
from core.channels import CHANNEL_OPENAI, CHANNEL_WEB
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

FACADE_INTENTS = {
    Message.Intent.CHAT,
    Message.Intent.MEMORY_QUERY,
    Message.Intent.KNOWLEDGE_QUERY,
}


@pytest.fixture(autouse=True)
def ai_settings(settings):
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0


@pytest.fixture
def alice():
    return register_user(email="alice@test.dev", password="Str0ngDevPass!42", name="Alice")


@pytest.fixture
def conversation(alice):
    workspace = Workspace.objects.get(owner=alice)
    return Conversation.objects.create(workspace=workspace, created_by=alice)


@pytest.fixture
def fake_provider():
    return ModelProvider.objects.create(
        name="fake-local",
        provider_type=ModelProvider.ProviderType.FAKE,
        model_id="fake-1",
        priority=0,
    )


def _prompt(name, purpose, content):
    template = PromptTemplate.objects.create(name=name, purpose=purpose)
    version = PromptVersion.objects.create(template=template, version=1, content=content)
    template.active_version = version
    template.save(update_fields=["active_version"])
    return version


@pytest.fixture
def prompts():
    _prompt("jarvis_system", "chat", "You are Jarvis.")
    _prompt("jarvis_router_intent", "router.intent", "Classify the message.")
    _prompt("jarvis_router_task_draft", "router.task_draft", "Extract a task.")


def _two_chunks():
    yield "a"
    yield "b"


# --- structured events ---


def test_events_are_dicts_and_the_message_id_can_be_fixed_up_front(conversation):
    import uuid

    message_id = uuid.uuid4()
    events = list(
        iter_assistant_reply_events(
            conversation, _two_chunks(), {}, request_id="r", message_id=message_id
        )
    )
    assert events[:2] == [{"type": "chunk", "text": "a"}, {"type": "chunk", "text": "b"}]
    assert events[-1]["type"] == "done"
    assert events[-1]["message_id"] == str(message_id)
    assert Message.objects.get(role=Message.Role.ASSISTANT).id == message_id


def test_sse_encoding_is_the_framed_event_stream(conversation):
    body = b"".join(stream_assistant_reply(conversation, _two_chunks(), {}, request_id="r"))
    assert body.startswith(b'data: {"type": "chunk", "text": "a"}\n\n')
    assert b'"type": "done"' in body


def test_closing_the_encoder_closes_the_events_and_persists_partial(conversation):
    outer = encode_sse(iter_assistant_reply_events(conversation, _two_chunks(), {}, request_id="r"))
    next(outer)
    outer.close()
    message = Message.objects.get(role=Message.Role.ASSISTANT)
    assert message.status == Message.Status.PARTIAL
    assert message.content == "a"


# --- allowed_intents ---


def _force_intent(monkeypatch, intent, tool_name=None):
    import assistant.services as services_module

    monkeypatch.setattr(
        services_module, "parse_intent", lambda raw: {"intent": intent, "tool_name": tool_name}
    )


def test_task_draft_outside_allowed_intents_degrades_before_the_extraction_call(
    conversation, alice, fake_provider, prompts, monkeypatch
):
    _force_intent(monkeypatch, Message.Intent.TASK_DRAFT)
    result = classify_intent(
        conversation, actor=alice, content="remind me", allowed_intents=FACADE_INTENTS
    )
    assert result == {"intent": Message.Intent.CHAT, "intent_payload": {}}
    assert ModelUsage.objects.filter(purpose="router.intent").count() == 1
    assert not ModelUsage.objects.filter(purpose="router.task_draft").exists()


def test_tool_request_outside_allowed_intents_degrades_to_chat(
    conversation, alice, fake_provider, prompts, monkeypatch
):
    _force_intent(monkeypatch, Message.Intent.TOOL_REQUEST, "vault_export")
    result = classify_intent(
        conversation, actor=alice, content="export my vault", allowed_intents=FACADE_INTENTS
    )
    assert result == {"intent": Message.Intent.CHAT, "intent_payload": {}}


def test_allowed_intents_pass_through(conversation, alice, fake_provider, prompts, monkeypatch):
    _force_intent(monkeypatch, Message.Intent.KNOWLEDGE_QUERY)
    result = classify_intent(conversation, actor=alice, content="x", allowed_intents=FACADE_INTENTS)
    assert result == {"intent": Message.Intent.KNOWLEDGE_QUERY, "intent_payload": {}}


def test_without_allowed_intents_task_draft_still_extracts(
    conversation, alice, fake_provider, prompts, monkeypatch
):
    _force_intent(monkeypatch, Message.Intent.TASK_DRAFT)
    result = classify_intent(conversation, actor=alice, content="remind me")
    assert result["intent"] == Message.Intent.TASK_DRAFT
    assert ModelUsage.objects.filter(purpose="router.task_draft").count() == 1


def test_restricted_router_prompt_drops_the_tool_vocabulary(
    conversation, alice, prompts, monkeypatch
):
    from ai_core.adapters.base import AdapterResult
    from ai_core.gateway import InferenceGateway

    captured = {}

    def _capture(self, **kwargs):
        captured.update(kwargs)
        return AdapterResult(
            text='{"intent": "chat", "tool_name": null}',
            input_tokens=1,
            output_tokens=1,
            finish_reason="stop",
        )

    monkeypatch.setattr(InferenceGateway, "complete", _capture)
    classify_intent(
        conversation,
        actor=alice,
        content="hi",
        allowed_intents=FACADE_INTENTS,
        context_report={"channel": CHANNEL_OPENAI},
    )
    system = captured["messages"][0]["content"]
    assert "vault_export" not in system
    assert "Only these intents are available on this channel" in system
    assert "task_draft" not in system.split("available on this channel")[1]
    assert captured["context_report"] == {"channel": CHANNEL_OPENAI}


def test_router_usage_row_carries_the_context_report(conversation, alice, fake_provider, prompts):
    classify_intent(
        conversation,
        actor=alice,
        content="hi",
        allowed_intents=FACADE_INTENTS,
        context_report={"channel": CHANNEL_OPENAI, "model": "jarvis"},
    )
    usage = ModelUsage.objects.get(purpose="router.intent")
    assert usage.context_report["channel"] == CHANNEL_OPENAI
    assert usage.context_report["model"] == "jarvis"


# --- chat call context report ---


def test_chat_context_report_records_channel_and_extra(conversation, alice, fake_provider, prompts):
    stream, usage_holder, *_ = build_gateway_stream(
        conversation,
        actor=alice,
        content="hi",
        channel=CHANNEL_OPENAI,
        report_extra={"model": "jarvis"},
    )
    list(iter_assistant_reply_events(conversation, stream, usage_holder, request_id="r"))
    usage = ModelUsage.objects.get(purpose="chat")
    assert usage.context_report["channel"] == CHANNEL_OPENAI
    assert usage.context_report["model"] == "jarvis"


def test_web_chat_context_report_records_web(conversation, alice, fake_provider, prompts):
    stream, usage_holder, *_ = build_gateway_stream(
        conversation, actor=alice, content="hi", channel=CHANNEL_WEB
    )
    list(iter_assistant_reply_events(conversation, stream, usage_holder, request_id="r"))
    usage = ModelUsage.objects.get(purpose="chat")
    assert usage.context_report["channel"] == CHANNEL_WEB
    assert "model" not in usage.context_report
