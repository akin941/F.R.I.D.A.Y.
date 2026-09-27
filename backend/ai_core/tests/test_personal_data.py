"""D5 (Phase 5 branch 2, ADR-010 decision 5): personal-class data never
reaches a provider without `personal_data_allowed`.

Every test routes provider calls through a counting `FakeAdapter` (no
network, no real SDK) patched in at `ai_core.gateway.get_adapter`, so
"never reached" is asserted as a call count of zero and "reached only the
allowed provider" as the captured provider/kwargs of every call."""

from decimal import Decimal

import pytest
from django.contrib.admin.sites import site as admin_site
from django.core.management import call_command
from django.core.management.base import CommandError

from accounts.services import register_user
from ai_core.adapters.base import AdapterError
from ai_core.adapters.fake import FAKE_RESPONSE, FakeAdapter
from ai_core.exceptions import GatewayError, NoProviderAvailable
from ai_core.gateway import InferenceGateway
from ai_core.models import ModelProvider, ModelTier, ModelUsage
from audit.models import AuditLog

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"
MESSAGES = [{"role": "user", "content": "Summarize my inbox"}]


@pytest.fixture(autouse=True)
def ai_settings(settings):
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    settings.AI_PURPOSE_TIERS = {}
    return settings


@pytest.fixture
def user():
    return register_user(email="alice@test.dev", password=PASSWORD, name="Alice")


@pytest.fixture
def workspace(user):
    return user.owned_workspaces.first()


class CountingFakeAdapter(FakeAdapter):
    """The real FakeAdapter, recording every provider call it serves."""

    calls = []
    fail_providers = set()

    def _log(self, method, **kwargs):
        CountingFakeAdapter.calls.append(
            {"provider": self.provider.name, "method": method, **kwargs}
        )
        if self.provider.name in CountingFakeAdapter.fail_providers:
            raise AdapterError("provider_error", retryable=False)

    def complete(self, messages, params, *, purpose=""):
        self._log("complete", messages=messages, purpose=purpose)
        return super().complete(messages, params, purpose=purpose)

    def stream(self, messages, params, *, purpose=""):
        self._log("stream", messages=messages, purpose=purpose)
        return super().stream(messages, params, purpose=purpose)

    def embed(self, texts, *, purpose=""):
        self._log("embed", texts=texts, purpose=purpose)
        return super().embed(texts, purpose=purpose)

    def transcribe(self, audio, *, content_type="", purpose=""):
        self._log("transcribe", purpose=purpose)
        return super().transcribe(audio, content_type=content_type, purpose=purpose)

    def synthesize(self, text, *, params=None, purpose=""):
        self._log("synthesize", purpose=purpose)
        return super().synthesize(text, params=params, purpose=purpose)


@pytest.fixture(autouse=True)
def counting_adapter(monkeypatch):
    CountingFakeAdapter.calls = []
    CountingFakeAdapter.fail_providers = set()
    monkeypatch.setattr(
        "ai_core.gateway.get_adapter",
        lambda provider, **kwargs: CountingFakeAdapter(provider, **kwargs),
    )
    return CountingFakeAdapter


def make_provider(name, *, priority, allowed=False, capability=ModelProvider.Capability.CHAT):
    """A provider typed as a real cloud SDK (its key env var is always set);
    every call is served by the patched counting FakeAdapter."""
    return ModelProvider.objects.create(
        name=name,
        provider_type=ModelProvider.ProviderType.ANTHROPIC,
        capability=capability,
        model_id=f"{name}-model",
        api_key_env_var="PATH",
        priority=priority,
        input_price_per_mtok=Decimal("3.0"),
        output_price_per_mtok=Decimal("15.0"),
        personal_data_allowed=allowed,
    )


def complete(**kwargs):
    kwargs.setdefault("purpose", "chat")
    return InferenceGateway().complete(messages=MESSAGES, **kwargs)


# --- the flag itself ---


def test_personal_data_allowed_defaults_false():
    provider = ModelProvider.objects.create(
        name="new-cloud", provider_type=ModelProvider.ProviderType.ANTHROPIC, model_id="m"
    )
    assert provider.personal_data_allowed is False


# --- fail closed: only cloud providers ---


def test_only_cloud_providers_fail_closed_with_one_usage_row(user, workspace, counting_adapter):
    make_provider("cloud-a", priority=10)
    make_provider("cloud-b", priority=20)

    with pytest.raises(NoProviderAvailable):
        complete(actor=user, workspace=workspace, personal_data=True)

    assert counting_adapter.calls == []  # no provider was ever called
    usage = ModelUsage.objects.get()  # exactly one row
    assert usage.success is False
    assert usage.provider is None
    assert usage.error_type == "no_provider"
    assert usage.routing_reason == "personal_data_required"
    assert usage.context_report["personal_data"] is True
    assert usage.context_report["attempts"] == []


def test_stream_fails_closed_eagerly_before_any_generator(user, workspace, counting_adapter):
    make_provider("cloud-a", priority=10)

    with pytest.raises(NoProviderAvailable):
        complete(actor=user, workspace=workspace, stream=True, personal_data=True)

    assert counting_adapter.calls == []
    usage = ModelUsage.objects.get()
    assert usage.error_type == "no_provider"
    assert usage.routing_reason == "personal_data_required"


def test_on_usage_called_once_on_personal_data_refusal(user, workspace):
    make_provider("cloud-a", priority=10)
    seen = []
    with pytest.raises(NoProviderAvailable):
        complete(actor=user, workspace=workspace, personal_data=True, on_usage=seen.append)
    assert len(seen) == 1
    assert seen[0].routing_reason == "personal_data_required"


def test_fake_provider_without_the_flag_is_refused_too(user, workspace, counting_adapter):
    # The rule is the flag, not the provider type: an unflagged fake
    # provider is as ineligible as a cloud one.
    ModelProvider.objects.create(
        name="fake-unflagged",
        provider_type=ModelProvider.ProviderType.FAKE,
        model_id="fake-1",
        priority=0,
    )
    with pytest.raises(NoProviderAvailable):
        complete(actor=user, workspace=workspace, personal_data=True)
    assert counting_adapter.calls == []


def test_no_provider_without_personal_data_keeps_empty_reason(user, workspace):
    with pytest.raises(NoProviderAvailable):
        complete(actor=user, workspace=workspace)
    usage = ModelUsage.objects.get()
    assert usage.routing_reason == ""
    assert "personal_data" not in usage.context_report


# --- allowed providers only: primary and fallback ---


def test_allowed_provider_serves_even_at_worse_priority(user, workspace, counting_adapter):
    make_provider("cloud-a", priority=1)
    local = make_provider("local-a", priority=90, allowed=True)

    result = complete(actor=user, workspace=workspace, personal_data=True)

    assert result.text == FAKE_RESPONSE
    assert [c["provider"] for c in counting_adapter.calls] == ["local-a"]
    assert counting_adapter.calls[0]["messages"] == MESSAGES
    usage = ModelUsage.objects.get()
    assert usage.provider == local
    assert usage.success is True
    assert usage.routing_reason == "only_provider"
    assert usage.context_report["personal_data"] is True


def test_failing_allowed_provider_never_falls_back_to_cloud(user, workspace, counting_adapter):
    make_provider("cloud-a", priority=1)
    make_provider("local-a", priority=90, allowed=True)
    counting_adapter.fail_providers = {"local-a"}

    with pytest.raises(GatewayError):
        complete(actor=user, workspace=workspace, personal_data=True)

    assert {c["provider"] for c in counting_adapter.calls} == {"local-a"}
    usage = ModelUsage.objects.get()
    assert [a["provider"] for a in usage.context_report["attempts"]] == ["local-a"]


def test_fallback_stays_inside_the_allowed_set(user, workspace, counting_adapter):
    make_provider("cloud-a", priority=1)
    make_provider("local-a", priority=10, allowed=True)
    make_provider("local-b", priority=20, allowed=True)
    counting_adapter.fail_providers = {"local-a"}

    result = complete(actor=user, workspace=workspace, personal_data=True)

    assert result.usage.provider.name == "local-b"
    assert result.usage.routing_reason == "fallback:1"
    assert [c["provider"] for c in counting_adapter.calls] == ["local-a", "local-b"]


def test_streaming_uses_only_allowed_providers(user, workspace, counting_adapter):
    make_provider("cloud-a", priority=1)
    make_provider("local-a", priority=90, allowed=True)

    chunks = list(complete(actor=user, workspace=workspace, stream=True, personal_data=True))

    assert "".join(chunks) == FAKE_RESPONSE
    assert [(c["provider"], c["method"]) for c in counting_adapter.calls] == [("local-a", "stream")]


def test_tier_preference_cannot_pull_in_a_cloud_provider(
    user, workspace, counting_adapter, settings
):
    cloud = make_provider("cloud-cheap", priority=1)
    ModelTier.objects.create(tier=ModelTier.Tier.CHEAP, provider=cloud)
    settings.AI_PURPOSE_TIERS = {"chat": "cheap"}
    local = make_provider("local-a", priority=90, allowed=True)

    result = complete(actor=user, workspace=workspace, personal_data=True)

    assert result.usage.provider == local
    assert [c["provider"] for c in counting_adapter.calls] == ["local-a"]


def test_tier_ordering_still_applies_within_the_allowed_set(
    user, workspace, counting_adapter, settings
):
    make_provider("local-a", priority=10, allowed=True)
    local_cheap = make_provider("local-cheap", priority=50, allowed=True)
    ModelTier.objects.create(tier=ModelTier.Tier.CHEAP, provider=local_cheap)
    settings.AI_PURPOSE_TIERS = {"chat": "cheap"}

    result = complete(actor=user, workspace=workspace, personal_data=True)

    assert result.usage.provider == local_cheap
    assert result.usage.routing_reason == "tier:cheap"


def test_without_personal_data_cloud_priority_is_unchanged(user, workspace, counting_adapter):
    cloud = make_provider("cloud-a", priority=1)
    make_provider("local-a", priority=90, allowed=True)

    result = complete(actor=user, workspace=workspace)

    assert result.usage.provider == cloud
    assert "personal_data" not in result.usage.context_report


def test_flag_is_read_fresh_on_every_call(user, workspace, counting_adapter):
    local = make_provider("local-a", priority=10, allowed=True)
    gateway = InferenceGateway()
    gateway.complete("chat", MESSAGES, actor=user, workspace=workspace, personal_data=True)

    ModelProvider.objects.filter(pk=local.pk).update(personal_data_allowed=False)
    with pytest.raises(NoProviderAvailable):
        gateway.complete("chat", MESSAGES, actor=user, workspace=workspace, personal_data=True)

    assert [c["provider"] for c in counting_adapter.calls] == ["local-a"]  # 2nd call: none


# --- every other entry point applies the same filter ---


def test_embed_personal_data_fails_closed(user, workspace, counting_adapter):
    make_provider("cloud-embed", priority=1, capability=ModelProvider.Capability.EMBEDDING)
    with pytest.raises(NoProviderAvailable):
        InferenceGateway().embed(
            purpose="memory.retrieval",
            texts=["my calendar"],
            actor=user,
            workspace=workspace,
            personal_data=True,
        )
    assert counting_adapter.calls == []
    usage = ModelUsage.objects.get()
    assert usage.routing_reason == "personal_data_required"


def test_embed_personal_data_uses_allowed_provider(user, workspace, counting_adapter):
    make_provider("cloud-embed", priority=1, capability=ModelProvider.Capability.EMBEDDING)
    make_provider(
        "local-embed", priority=90, allowed=True, capability=ModelProvider.Capability.EMBEDDING
    )
    InferenceGateway().embed(
        purpose="memory.retrieval",
        texts=["my calendar"],
        actor=user,
        workspace=workspace,
        personal_data=True,
    )
    assert [c["provider"] for c in counting_adapter.calls] == ["local-embed"]


@pytest.mark.parametrize(
    ("method", "capability", "kwargs"),
    [
        ("transcribe", ModelProvider.Capability.STT, {"audio": b"x"}),
        ("synthesize", ModelProvider.Capability.TTS, {"text": "hi"}),
    ],
)
def test_audio_entry_points_fail_closed(
    user, workspace, counting_adapter, method, capability, kwargs
):
    make_provider("cloud-audio", priority=1, capability=capability)
    with pytest.raises(NoProviderAvailable):
        getattr(InferenceGateway(), method)(
            purpose="voice.x", actor=user, workspace=workspace, personal_data=True, **kwargs
        )
    assert counting_adapter.calls == []
    assert ModelUsage.objects.get().routing_reason == "personal_data_required"


# --- set_provider_personal_data (audited) ---


def test_command_allows_and_audits():
    provider = make_provider("ollama-local", priority=5)
    call_command("set_provider_personal_data", "ollama-local", "--allow")
    provider.refresh_from_db()
    assert provider.personal_data_allowed is True
    entry = AuditLog.objects.get(action="ai_core.provider_personal_data_set")
    assert entry.resource_id == str(provider.pk)
    assert entry.metadata["previous"] is False
    assert entry.metadata["personal_data_allowed"] is True
    assert entry.metadata["changed"] is True
    assert entry.metadata["via"] == "management_command"


def test_command_denies_and_audits():
    provider = make_provider("ollama-local", priority=5, allowed=True)
    call_command("set_provider_personal_data", "ollama-local", "--deny")
    provider.refresh_from_db()
    assert provider.personal_data_allowed is False
    entry = AuditLog.objects.get(action="ai_core.provider_personal_data_set")
    assert entry.metadata["previous"] is True
    assert entry.metadata["personal_data_allowed"] is False


def test_command_noop_is_still_audited():
    make_provider("ollama-local", priority=5, allowed=True)
    call_command("set_provider_personal_data", "ollama-local", "--allow")
    entry = AuditLog.objects.get(action="ai_core.provider_personal_data_set")
    assert entry.metadata["changed"] is False


def test_command_unknown_provider_errors_without_audit():
    with pytest.raises(CommandError):
        call_command("set_provider_personal_data", "nope", "--allow")
    assert not AuditLog.objects.filter(action="ai_core.provider_personal_data_set").exists()


def test_command_requires_exactly_one_of_allow_or_deny():
    make_provider("ollama-local", priority=5)
    with pytest.raises(CommandError):
        call_command("set_provider_personal_data", "ollama-local")
    with pytest.raises(CommandError):
        call_command("set_provider_personal_data", "ollama-local", "--allow", "--deny")


def test_admin_shows_the_flag_read_only():
    assert "personal_data_allowed" in admin_site._registry[ModelProvider].readonly_fields


# --- seeding ---


def test_seed_flags_only_local_providers(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-local")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-real-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-a-real-key")
    monkeypatch.setenv("NVIDIA_API_KEY", "test-not-a-real-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-not-a-real-key")
    call_command("seed_ai_defaults")
    allowed = set(
        ModelProvider.objects.filter(personal_data_allowed=True).values_list("name", flat=True)
    )
    assert allowed == {"ollama-local", "ollama-embedding", "fake-local", "fake-embedding-local"}


def test_seed_never_flips_an_existing_row(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-local")
    call_command("seed_ai_defaults")
    call_command("set_provider_personal_data", "ollama-local", "--deny")
    call_command("seed_ai_defaults")
    assert ModelProvider.objects.get(name="ollama-local").personal_data_allowed is False


# --- pre-flight (Phase 5 branch 4, D5) ---


def test_preflight_mirrors_selection_without_a_call_or_a_usage_row():
    gateway = InferenceGateway()
    assert gateway.has_eligible_provider(purpose="chat", personal_data=True) is False
    ModelProvider.objects.create(
        name="cloud", provider_type=ModelProvider.ProviderType.FAKE, model_id="f", priority=0
    )
    assert gateway.has_eligible_provider(purpose="chat") is True
    assert gateway.has_eligible_provider(purpose="chat", personal_data=True) is False
    ModelProvider.objects.create(
        name="local",
        provider_type=ModelProvider.ProviderType.FAKE,
        model_id="f",
        priority=1,
        personal_data_allowed=True,
    )
    assert gateway.has_eligible_provider(purpose="chat", personal_data=True) is True
    assert not ModelUsage.objects.exists()
