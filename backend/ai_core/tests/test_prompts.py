import pytest
from django.core.management import call_command
from django.db import IntegrityError

from ai_core.models import ModelProvider, PromptTemplate, PromptVersion

pytestmark = pytest.mark.django_db


def test_seed_creates_prompt_and_fake_provider():
    call_command("seed_ai_defaults")
    template = PromptTemplate.objects.get(name="jarvis_system")
    assert template.active_version is not None
    assert template.active_version.version == 1
    assert "Jarvis OS" in template.active_version.content
    assert ModelProvider.objects.filter(
        name="fake-local", provider_type=ModelProvider.ProviderType.FAKE
    ).exists()


def test_seed_creates_fake_embedding_provider():
    call_command("seed_ai_defaults")
    provider = ModelProvider.objects.get(name="fake-embedding-local")
    assert provider.provider_type == ModelProvider.ProviderType.FAKE
    assert provider.capability == ModelProvider.Capability.EMBEDDING
    assert provider.dimension == 32


def test_seed_creates_memory_extraction_prompt():
    call_command("seed_ai_defaults")
    template = PromptTemplate.objects.get(name="jarvis_memory_extraction")
    assert template.purpose == "memory.extraction"
    assert template.active_version is not None
    assert template.active_version.version == 1
    assert "JSON" in template.active_version.content


def test_seed_creates_router_intent_prompt():
    call_command("seed_ai_defaults")
    template = PromptTemplate.objects.get(name="jarvis_router_intent")
    assert template.purpose == "router.intent"
    assert template.active_version is not None
    assert template.active_version.version == 1
    assert "JSON" in template.active_version.content


def test_seed_creates_agent_plan_prompt():
    call_command("seed_ai_defaults")
    template = PromptTemplate.objects.get(name="jarvis_agent_plan")
    assert template.purpose == "agent.plan"
    assert template.active_version is not None
    assert template.active_version.version == 1
    assert "JSON" in template.active_version.content


def test_seed_is_idempotent():
    call_command("seed_ai_defaults")
    call_command("seed_ai_defaults")
    assert PromptTemplate.objects.filter(name="jarvis_system").count() == 1
    assert PromptVersion.objects.filter(template__name="jarvis_system").count() == 1
    assert PromptTemplate.objects.filter(name="jarvis_memory_extraction").count() == 1
    assert PromptVersion.objects.filter(template__name="jarvis_memory_extraction").count() == 1
    assert PromptTemplate.objects.filter(name="jarvis_router_intent").count() == 1
    assert PromptVersion.objects.filter(template__name="jarvis_router_intent").count() == 1
    assert PromptTemplate.objects.filter(name="jarvis_agent_plan").count() == 1
    assert PromptVersion.objects.filter(template__name="jarvis_agent_plan").count() == 1
    assert ModelProvider.objects.filter(name="fake-local").count() == 1
    assert ModelProvider.objects.filter(name="fake-embedding-local").count() == 1


def test_prompt_version_unique_constraint():
    call_command("seed_ai_defaults")
    template = PromptTemplate.objects.get(name="jarvis_system")
    with pytest.raises(IntegrityError):
        PromptVersion.objects.create(template=template, version=1, content="dup")
