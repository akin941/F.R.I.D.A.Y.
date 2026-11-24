"""Shared fixtures for the Phase 12 branch 4 n8n chat tests: the chat setup
(prompts, a personal-data-allowed FakeAdapter provider), a gateway spy, a
router stub, a `node_voice` client with its live conversation, and a web
conversation. Import by name (`from connectors.tests.n8n_chat_fixtures
import ...  # noqa: F401`)."""

import json

import pytest

from accounts.tests.node_voice_helpers import CHAT_URL, bearer, mint_token, pair_node, sse_events
from ai_core.gateway import InferenceGateway
from ai_core.models import ModelProvider, PromptTemplate, PromptVersion
from assistant import services as assistant_services
from assistant.models import Conversation
from assistant.services import create_conversation


def _prompt(name, purpose, content):
    template, _ = PromptTemplate.objects.get_or_create(name=name, defaults={"purpose": purpose})
    if template.active_version_id is None:
        version = PromptVersion.objects.create(template=template, version=1, content=content)
        template.active_version = version
        template.save(update_fields=["active_version"])


@pytest.fixture
def n8n_chat(db, settings):
    """Prompts and the one provider (local: personal data allowed)."""
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    _prompt("friday_system", "chat", "You are Friday.")
    _prompt("friday_router_intent", "router.intent", "Classify the message.")
    _prompt("friday_router_connector_args", "router.connector_args", "Extract the arguments.")
    _prompt("friday_chat_followup", "chat.followup", "Tell the owner the result.")
    return ModelProvider.objects.create(
        name="fake-local",
        provider_type=ModelProvider.ProviderType.FAKE,
        model_id="fake-1",
        priority=0,
        personal_data_allowed=True,
    )


@pytest.fixture
def calls(monkeypatch):
    """Every `InferenceGateway.complete` call: purpose, personal_data and the
    messages sent."""
    captured = []
    original = InferenceGateway.complete

    def spy(self, purpose, messages, **kwargs):
        captured.append(
            {
                "purpose": purpose,
                "personal_data": kwargs.get("personal_data", False),
                "messages": json.dumps(messages, ensure_ascii=False),
                "count": len(messages),
            }
        )
        return original(self, purpose, messages, **kwargs)

    monkeypatch.setattr(InferenceGateway, "complete", spy)
    return captured


@pytest.fixture
def route(monkeypatch):
    """Make the router pick `intent` for `tool` and extract `args`."""

    def _route(tool=None, args=None, intent="connector_read"):
        parsed = {"intent": intent, "tool_name": tool} if tool else {"intent": "chat"}
        parsed.setdefault("tool_name", None)
        monkeypatch.setattr(assistant_services, "parse_intent", lambda _text: dict(parsed))
        monkeypatch.setattr(
            assistant_services, "parse_connector_args", lambda _text: dict(args or {})
        )

    return _route


@pytest.fixture
def voice(owner, n8n_chat):
    _, secret = pair_node(owner)
    return bearer(mint_token(secret))


@pytest.fixture
def live(owner, workspace):
    return create_conversation(workspace=workspace, actor=owner, channel="node_voice")


@pytest.fixture
def conversation(workspace, owner):
    return Conversation.objects.create(workspace=workspace, created_by=owner)


def chat_turn(client, conversation, content, **body):
    response = client.post(
        CHAT_URL.format(conversation.pk), {"content": content, **body}, format="json"
    )
    assert response.status_code == 200, response.content
    return sse_events(response)


def reply_text(events):
    return "".join(e["text"] for e in events if e["type"] == "chunk")
