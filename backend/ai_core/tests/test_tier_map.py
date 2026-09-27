"""D11 tier map (Phase 5 branch 2): the shipped `AI_PURPOSE_TIERS` plus the
default `ModelTier` rows `seed_ai_defaults` creates route each purpose to
its tier, and every served row says so (`routing_reason="tier:<name>"`)."""

import pytest
from django.core.management import call_command

from ai_core.adapters.base import AdapterResult
from ai_core.gateway import InferenceGateway
from ai_core.models import ModelProvider, ModelTier

pytestmark = pytest.mark.django_db

CLOUD_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "NVIDIA_API_KEY", "DEEPSEEK_API_KEY")


@pytest.fixture(autouse=True)
def no_fake(settings):
    settings.AI_FAKE_PROVIDER = False
    settings.AI_RETRY_BACKOFF_SECONDS = 0


@pytest.fixture
def all_providers_seeded(monkeypatch):
    for key in (*CLOUD_KEYS, "OLLAMA_API_KEY"):
        monkeypatch.setenv(key, "test-not-a-real-key")
    call_command("seed_ai_defaults")


@pytest.fixture
def served(monkeypatch):
    """Every provider call is served in-process; records who served it."""
    calls = []

    class Recording:
        def __init__(self, provider):
            self.provider = provider

        def complete(self, messages, params, *, purpose=""):
            calls.append((self.provider.name, purpose))
            return AdapterResult(text="ok", input_tokens=1, output_tokens=1)

    monkeypatch.setattr("ai_core.gateway.get_adapter", lambda p, **kw: Recording(p))
    return calls


def _tiers(name):
    return set(ModelTier.objects.filter(provider__name=name).values_list("tier", flat=True))


def test_seed_creates_default_tier_rows(all_providers_seeded):
    assert _tiers("ollama-local") == {"cheap"}
    assert _tiers("nvidia-nim") == {"cheap"}
    assert _tiers("deepseek") == {"cheap"}
    assert _tiers("openai-primary") == {"mid"}
    assert _tiers("anthropic-primary") == {"mid", "strong"}
    # Embedding providers are never tiered (their vector dimension pins them).
    assert not ModelTier.objects.filter(
        provider__capability=ModelProvider.Capability.EMBEDDING
    ).exists()


def test_seed_skips_tiers_for_unconfigured_providers(monkeypatch):
    for key in (*CLOUD_KEYS, "OLLAMA_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    call_command("seed_ai_defaults")
    assert not ModelTier.objects.exists()


def test_seed_never_reclobbers_owner_tier_edits(all_providers_seeded):
    anthropic = ModelProvider.objects.get(name="anthropic-primary")
    ModelTier.objects.filter(provider=anthropic, tier="mid").delete()
    call_command("seed_ai_defaults")
    assert _tiers("anthropic-primary") == {"strong"}
    assert ModelTier.objects.filter(provider__name="ollama-local").count() == 1


@pytest.mark.parametrize(
    ("purpose", "expected_provider", "expected_tier"),
    [
        # ollama-local is priority 5 anyway; the tier keeps it first.
        ("router.intent", "ollama-local", "cheap"),
        ("harness.review_summary", "ollama-local", "cheap"),
        # anthropic-primary (priority 10) is in mid AND beats openai (20).
        ("chat", "anthropic-primary", "mid"),
        ("memory.extraction", "anthropic-primary", "mid"),
        ("agent.plan", "anthropic-primary", "strong"),
    ],
)
def test_shipped_map_routes_each_tier(
    all_providers_seeded, served, purpose, expected_provider, expected_tier
):
    result = InferenceGateway().complete(purpose, [{"role": "user", "content": "hi"}])
    assert served == [(expected_provider, purpose)]
    assert result.usage.provider.name == expected_provider
    assert result.usage.routing_reason == f"tier:{expected_tier}"


def test_mid_tier_beats_a_cheaper_priority(all_providers_seeded, served):
    # ollama-local (priority 5, cheap only) would win on priority alone;
    # chat's mid tier puts anthropic-primary first instead.
    result = InferenceGateway().complete("chat", [{"role": "user", "content": "hi"}])
    assert result.usage.provider.name == "anthropic-primary"
    assert served[0][0] == "anthropic-primary"


def test_tier_fallback_prefers_the_next_tier_member(all_providers_seeded, monkeypatch):
    # The primary mid provider fails; the fallback is the other mid member,
    # not the priority-order runner-up.
    from ai_core.adapters.base import AdapterError

    calls = []

    class FailAnthropic:
        def __init__(self, provider):
            self.provider = provider

        def complete(self, messages, params, *, purpose=""):
            calls.append(self.provider.name)
            if self.provider.name == "anthropic-primary":
                raise AdapterError("auth_error", retryable=False)
            return AdapterResult(text="ok", input_tokens=1, output_tokens=1)

    monkeypatch.setattr("ai_core.gateway.get_adapter", lambda p, **kw: FailAnthropic(p))
    result = InferenceGateway().complete("chat", [{"role": "user", "content": "hi"}])
    assert calls == ["anthropic-primary", "openai-primary"]
    assert result.usage.routing_reason == "fallback:1"
