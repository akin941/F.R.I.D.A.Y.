"""`only_provider` (Phase 5 closeout, used by `manage.py preflight`): a
gateway call pinned to one provider row -- no fallback, still only active,
keyed and (for personal data) allowed rows, and still exactly one
`ModelUsage` row per call."""

import pytest

from ai_core.adapters.base import AdapterError, AdapterResult, EmbedAdapterResult
from ai_core.exceptions import GatewayError, NoProviderAvailable
from ai_core.gateway import InferenceGateway
from ai_core.models import ModelProvider, ModelUsage

pytestmark = pytest.mark.django_db

MESSAGES = [{"role": "user", "content": "ping"}]


@pytest.fixture(autouse=True)
def setup(settings, monkeypatch):
    settings.AI_FAKE_PROVIDER = False
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    monkeypatch.setenv("PIN_KEY_A", "x" * 20)
    monkeypatch.setenv("PIN_KEY_B", "y" * 20)
    for name, priority, key in (("alpha", 1, "PIN_KEY_A"), ("beta", 2, "PIN_KEY_B")):
        ModelProvider.objects.create(
            name=name,
            provider_type=ModelProvider.ProviderType.OPENAI_COMPATIBLE,
            model_id=f"{name}-model",
            api_key_env_var=key,
            priority=priority,
        )
    ModelProvider.objects.create(
        name="beta-embed",
        provider_type=ModelProvider.ProviderType.OPENAI_COMPATIBLE,
        capability=ModelProvider.Capability.EMBEDDING,
        model_id="beta-embed-model",
        api_key_env_var="PIN_KEY_B",
        dimension=3,
    )


@pytest.fixture
def served(monkeypatch):
    calls = []

    class Recording:
        def __init__(self, provider):
            self.provider = provider

        def complete(self, messages, params, *, purpose=""):
            calls.append(self.provider.name)
            return AdapterResult(text="ok", input_tokens=1, output_tokens=1)

        def embed(self, texts, *, purpose=""):
            calls.append(self.provider.name)
            return EmbedAdapterResult(vectors=[[0.0, 0.0, 1.0]], input_tokens=1)

    monkeypatch.setattr("ai_core.gateway.get_adapter", lambda p, **kw: Recording(p))
    return calls


def test_pins_to_the_named_provider_even_if_not_first(served):
    result = InferenceGateway().complete("ops.preflight", MESSAGES, only_provider="beta")
    assert served == ["beta"]
    assert result.usage.provider.name == "beta"
    assert result.usage.routing_reason == "pinned"
    assert result.usage.purpose == "ops.preflight"
    assert ModelUsage.objects.count() == 1


def test_no_fallback_when_the_pinned_provider_fails(monkeypatch):
    calls = []

    class Failing:
        def __init__(self, provider):
            self.provider = provider

        def complete(self, messages, params, *, purpose=""):
            calls.append(self.provider.name)
            raise AdapterError("provider_error", retryable=False)

    monkeypatch.setattr("ai_core.gateway.get_adapter", lambda p, **kw: Failing(p))
    with pytest.raises(GatewayError):
        InferenceGateway().complete("ops.preflight", MESSAGES, only_provider="beta")
    assert calls == ["beta"]  # alpha (a healthy fallback) is never tried
    usage = ModelUsage.objects.get()
    assert usage.success is False
    assert usage.provider.name == "beta"


def test_inactive_pinned_provider_is_not_selectable(served):
    ModelProvider.objects.filter(name="beta").update(is_active=False)
    with pytest.raises(NoProviderAvailable):
        InferenceGateway().complete("ops.preflight", MESSAGES, only_provider="beta")
    assert served == []
    assert ModelUsage.objects.get().error_type == "no_provider"


def test_unkeyed_pinned_provider_is_not_selectable(served, monkeypatch):
    monkeypatch.delenv("PIN_KEY_B")
    with pytest.raises(NoProviderAvailable):
        InferenceGateway().complete("ops.preflight", MESSAGES, only_provider="beta")
    assert served == []


def test_unknown_pinned_provider_fails_closed(served):
    with pytest.raises(NoProviderAvailable):
        InferenceGateway().complete("ops.preflight", MESSAGES, only_provider="nope")
    assert served == []


def test_pinning_keeps_the_personal_data_filter(served):
    with pytest.raises(NoProviderAvailable):
        InferenceGateway().complete(
            "ops.preflight", MESSAGES, only_provider="alpha", personal_data=True
        )
    assert served == []
    assert ModelUsage.objects.get().routing_reason == "personal_data_required"


def test_embed_pins_by_capability(served):
    result = InferenceGateway().embed("ops.preflight", ["x"], only_provider="beta-embed")
    assert served == ["beta-embed"]
    assert result.usage.provider.name == "beta-embed"
    # A chat row's name never satisfies an embedding call.
    with pytest.raises(NoProviderAvailable):
        InferenceGateway().embed("ops.preflight", ["x"], only_provider="beta")


def test_unpinned_selection_unchanged(served):
    result = InferenceGateway().complete("ops.preflight", MESSAGES)
    assert served == ["alpha"]
    assert result.usage.routing_reason == "priority"
