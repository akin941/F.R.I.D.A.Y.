import io
import secrets
import wave

import pytest
from rest_framework.test import APIClient

from accounts.authentication import TOKEN_PREFIX, hash_token
from accounts.models import PersonalAccessToken, User
from accounts.services import register_user
from ai_core.models import ModelProvider, PromptTemplate, PromptVersion
from assistant.models import Conversation
from workspaces.models import Workspace

PASSWORD = "Str0ngDevPass!42"
TRANSCRIPTIONS = "/api/v1/voice/transcriptions"
SPEECH = "/api/v1/voice/speech"


def make_wav(*, seconds=1.0, rate=16_000, channels=1):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b"\x01\x00" * channels * int(rate * seconds))
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def ai_settings(settings):
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    settings.AI_PURPOSE_TIERS = {}
    settings.VOICE_TTS_ROUTES = {"tr": {}, "en": {}}
    settings.VOICE_TTS_PROVIDER_PARAMS = {}
    settings.VOICE_STT_LANGUAGE = "tr"
    return settings


@pytest.fixture
def owner(db):
    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


@pytest.fixture
def conversation(owner, workspace):
    return Conversation.objects.create(workspace=workspace, created_by=owner)


def _provider(name, capability, *, allowed=True, priority=0, active=True):
    return ModelProvider.objects.create(
        name=name,
        provider_type=ModelProvider.ProviderType.FAKE,
        capability=capability,
        model_id=f"{name}-model",
        priority=priority,
        personal_data_allowed=allowed,
        is_active=active,
    )


@pytest.fixture
def speech_providers(db):
    """The seeded `fake-stt-local`/`fake-tts-local` shape: personal-data
    allowed (D5), so happy paths reach them."""
    return (
        _provider("fake-stt-local", ModelProvider.Capability.STT),
        _provider("fake-tts-local", ModelProvider.Capability.TTS),
    )


@pytest.fixture
def make_provider(db):
    return _provider


def _prompt(name, purpose, content):
    template = PromptTemplate.objects.create(name=name, purpose=purpose)
    version = PromptVersion.objects.create(template=template, version=1, content=content)
    template.active_version = version
    template.save(update_fields=["active_version"])
    return version


@pytest.fixture
def chat_setup(db):
    """A fake chat provider plus the prompts a message turn needs."""
    ModelProvider.objects.create(
        name="fake-chat",
        provider_type=ModelProvider.ProviderType.FAKE,
        model_id="fake-1",
        priority=0,
    )
    _prompt("friday_system", "chat", "You are Friday.")
    _prompt("friday_router_intent", "router.intent", "Classify the message.")
    _prompt("friday_router_task_draft", "router.task_draft", "Extract the task.")
    # Phase 12 D12: a gated tool with draft arguments (the n8n write) runs
    # the argument step before its draft.
    _prompt("friday_router_connector_args", "router.connector_args", "Extract the arguments.")


def pat_client(owner, workspace, kind):
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    PersonalAccessToken.objects.create(
        workspace=workspace, user=owner, name=kind, kind=kind, token_hash=hash_token(raw)
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return client


@pytest.fixture
def principal_client(owner, workspace, web_client):
    """The five Phase 4 principals (plus anonymous)."""

    def make(principal):
        if principal == "owner-web":
            return web_client(owner)
        if principal == "owner-pat-cli":
            return pat_client(owner, workspace, PersonalAccessToken.Kind.CLI)
        if principal == "owner-pat-openai":
            return pat_client(owner, workspace, PersonalAccessToken.Kind.OPENAI)
        if principal == "non-owner":
            return web_client(register_user(email="member@test.dev", password=PASSWORD))
        if principal == "agent":
            return web_client(User.objects.create_user(email="agent@test.dev", is_agent=True))
        return APIClient()

    return make
