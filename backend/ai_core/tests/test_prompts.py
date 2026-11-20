import pytest
from django.core.management import call_command
from django.db import IntegrityError

from ai_core.models import ModelProvider, PromptTemplate, PromptVersion

pytestmark = pytest.mark.django_db


def test_seed_creates_prompt_and_fake_provider():
    call_command("seed_ai_defaults")
    template = PromptTemplate.objects.get(name="friday_system")
    assert template.active_version is not None
    assert template.active_version.version == 1
    assert "Friday OS" in template.active_version.content
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
    template = PromptTemplate.objects.get(name="friday_memory_extraction")
    assert template.purpose == "memory.extraction"
    assert template.active_version is not None
    assert template.active_version.version == 2
    assert "untrusted_context" in template.active_version.content
    assert "JSON" in template.active_version.content


def test_seed_creates_router_intent_prompt():
    call_command("seed_ai_defaults")
    template = PromptTemplate.objects.get(name="friday_router_intent")
    assert template.purpose == "router.intent"
    assert template.active_version is not None
    assert template.active_version.version == 1
    assert "JSON" in template.active_version.content


def test_seed_creates_router_connector_args_prompt():
    """Phase 5 branch 4 (brief D4): the argument-extraction prompt."""
    call_command("seed_ai_defaults")
    call_command("seed_ai_defaults")
    template = PromptTemplate.objects.get(name="friday_router_connector_args")
    assert template.purpose == "router.connector_args"
    assert template.active_version.version == 1
    assert "JSON" in template.active_version.content
    assert PromptVersion.objects.filter(template=template).count() == 1


def test_seed_creates_agent_plan_prompt():
    call_command("seed_ai_defaults")
    template = PromptTemplate.objects.get(name="friday_agent_plan")
    assert template.purpose == "agent.plan"
    assert template.active_version is not None
    assert template.active_version.version == 1
    assert "JSON" in template.active_version.content


def test_seed_creates_agent_read_step_prompt():
    call_command("seed_ai_defaults")
    template = PromptTemplate.objects.get(name="friday_agent_read_step")
    assert template.purpose == "agent.read_step"
    assert template.active_version is not None
    assert template.active_version.version == 1


def test_seed_creates_harness_review_summary_prompt():
    """Codex code-review P1, harness Phase 3 branch 3: without this, a
    fresh deployment has no review prompt row at all, and every
    otherwise-successful review fails closed as `review_unavailable`.
    Phase 5 D11: the summary has its own purpose and template; the Phase 3
    `friday_harness_review` row is no longer seeded."""
    call_command("seed_ai_defaults")
    template = PromptTemplate.objects.get(name="friday_harness_review_summary")
    assert template.purpose == "harness.review_summary"
    assert template.active_version is not None
    assert template.active_version.version == 1
    assert '"verdict"' in template.active_version.content
    assert not PromptTemplate.objects.filter(name="friday_harness_review").exists()


def test_seed_is_idempotent():
    call_command("seed_ai_defaults")
    call_command("seed_ai_defaults")
    assert PromptTemplate.objects.filter(name="friday_system").count() == 1
    assert PromptVersion.objects.filter(template__name="friday_system").count() == 1
    assert PromptTemplate.objects.filter(name="friday_memory_extraction").count() == 1
    assert PromptVersion.objects.filter(template__name="friday_memory_extraction").count() == 2
    assert PromptTemplate.objects.filter(name="friday_router_intent").count() == 1
    assert PromptVersion.objects.filter(template__name="friday_router_intent").count() == 1
    assert PromptTemplate.objects.filter(name="friday_agent_plan").count() == 1
    assert PromptVersion.objects.filter(template__name="friday_agent_plan").count() == 1
    assert PromptTemplate.objects.filter(name="friday_agent_read_step").count() == 1
    assert PromptVersion.objects.filter(template__name="friday_agent_read_step").count() == 1
    assert ModelProvider.objects.filter(name="fake-local").count() == 1
    assert ModelProvider.objects.filter(name="fake-embedding-local").count() == 1


def test_seed_skips_ollama_provider_when_unset(monkeypatch):
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    call_command("seed_ai_defaults")
    assert not ModelProvider.objects.filter(name="ollama-local").exists()


def test_seed_creates_ollama_provider_with_defaults(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-local")
    monkeypatch.delenv("OLLAMA_MODEL_ID", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    call_command("seed_ai_defaults")
    provider = ModelProvider.objects.get(name="ollama-local")
    assert provider.provider_type == ModelProvider.ProviderType.OPENAI_COMPATIBLE
    assert provider.model_id == "llama3.1:8b"
    assert provider.base_url == "http://host.docker.internal:11434/v1"
    assert provider.api_key_env_var == "OLLAMA_API_KEY"


def test_seed_creates_ollama_provider_with_overrides(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-local")
    monkeypatch.setenv("OLLAMA_MODEL_ID", "qwen2.5:14b")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://192.168.1.50:11434/v1")
    call_command("seed_ai_defaults")
    provider = ModelProvider.objects.get(name="ollama-local")
    assert provider.model_id == "qwen2.5:14b"
    assert provider.base_url == "http://192.168.1.50:11434/v1"


def test_seed_skips_ollama_embedding_provider_when_unset(monkeypatch):
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    call_command("seed_ai_defaults")
    assert not ModelProvider.objects.filter(name="ollama-embedding").exists()


def test_seed_creates_ollama_embedding_provider_with_defaults(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-local")
    monkeypatch.delenv("OLLAMA_EMBED_MODEL_ID", raising=False)
    monkeypatch.delenv("OLLAMA_EMBED_DIMENSION", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    call_command("seed_ai_defaults")
    provider = ModelProvider.objects.get(name="ollama-embedding")
    assert provider.provider_type == ModelProvider.ProviderType.OPENAI_COMPATIBLE
    assert provider.capability == ModelProvider.Capability.EMBEDDING
    assert provider.model_id == "nomic-embed-text"
    assert provider.dimension == 768
    assert provider.base_url == "http://host.docker.internal:11434/v1"
    assert provider.api_key_env_var == "OLLAMA_API_KEY"


def test_seed_creates_ollama_embedding_provider_with_overrides(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-local")
    monkeypatch.setenv("OLLAMA_EMBED_MODEL_ID", "mxbai-embed-large")
    monkeypatch.setenv("OLLAMA_EMBED_DIMENSION", "1024")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://192.168.1.50:11434/v1")
    call_command("seed_ai_defaults")
    provider = ModelProvider.objects.get(name="ollama-embedding")
    assert provider.model_id == "mxbai-embed-large"
    assert provider.dimension == 1024
    assert provider.base_url == "http://192.168.1.50:11434/v1"


def test_prompt_version_unique_constraint():
    call_command("seed_ai_defaults")
    template = PromptTemplate.objects.get(name="friday_system")
    with pytest.raises(IntegrityError):
        PromptVersion.objects.create(template=template, version=1, content="dup")
