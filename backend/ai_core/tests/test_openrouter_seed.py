"""OpenRouter provider row (Phase 5 closeout): seeded by `seed_ai_defaults`
whether or not the key is set, `openai_compatible`, chat capability,
`personal_data_allowed=False`, and disabled while `OPENROUTER_API_KEY` is
absent. No test makes a network call."""

import pytest
from django.core.management import call_command

from ai_core.adapters.base import AdapterResult
from ai_core.exceptions import NoProviderAvailable
from ai_core.gateway import InferenceGateway
from ai_core.models import ModelProvider, ModelUsage

pytestmark = pytest.mark.django_db

KEY = "OPENROUTER_API_KEY"


@pytest.fixture(autouse=True)
def no_fake(settings):
    settings.AI_FAKE_PROVIDER = False
    settings.AI_RETRY_BACKOFF_SECONDS = 0


@pytest.fixture
def no_key(monkeypatch):
    monkeypatch.delenv(KEY, raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL_ID", raising=False)


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setenv(KEY, "test-not-a-real-key")
    monkeypatch.delenv("OPENROUTER_MODEL_ID", raising=False)


def _row():
    return ModelProvider.objects.get(name="openrouter")


def test_seeded_disabled_without_key(no_key):
    call_command("seed_ai_defaults")
    row = _row()
    assert row.provider_type == ModelProvider.ProviderType.OPENAI_COMPATIBLE
    assert row.capability == ModelProvider.Capability.CHAT
    assert row.base_url == "https://openrouter.ai/api/v1"
    assert row.api_key_env_var == KEY
    assert row.model_id == "openrouter/auto"
    assert row.personal_data_allowed is False
    assert row.is_active is False


def test_seeded_enabled_with_key(with_key):
    call_command("seed_ai_defaults")
    row = _row()
    assert row.is_active is True
    assert row.personal_data_allowed is False


def test_model_id_override(with_key, monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODEL_ID", "anthropic/claude-sonnet-5")
    call_command("seed_ai_defaults")
    assert _row().model_id == "anthropic/claude-sonnet-5"


def test_empty_model_id_env_falls_back_to_default(with_key, monkeypatch):
    # Compose materializes an unset `${VAR:-}` as "".
    monkeypatch.setenv("OPENROUTER_MODEL_ID", "")
    call_command("seed_ai_defaults")
    assert _row().model_id == "openrouter/auto"


def test_rerun_without_key_disables_an_enabled_row(with_key, monkeypatch):
    call_command("seed_ai_defaults")
    assert _row().is_active is True
    monkeypatch.delenv(KEY)
    call_command("seed_ai_defaults")
    assert _row().is_active is False


def test_rerun_is_idempotent_and_never_clobbers_owner_edits(with_key):
    call_command("seed_ai_defaults")
    ModelProvider.objects.filter(name="openrouter").update(
        model_id="owner/choice", priority=7, is_active=False
    )
    call_command("seed_ai_defaults")
    assert ModelProvider.objects.filter(name="openrouter").count() == 1
    row = _row()
    # An owner-disabled row stays disabled even with the key present.
    assert (row.model_id, row.priority, row.is_active) == ("owner/choice", 7, False)


def test_rerun_keeps_an_owner_personal_data_flag(with_key):
    call_command("seed_ai_defaults")
    ModelProvider.objects.filter(name="openrouter").update(personal_data_allowed=True)
    call_command("seed_ai_defaults")
    assert _row().personal_data_allowed is True


def test_openrouter_gets_no_default_tier(with_key):
    call_command("seed_ai_defaults")
    assert not _row().tiers.exists()


def test_disabled_row_is_never_selected(no_key, monkeypatch):
    call_command("seed_ai_defaults")
    # Even if the key appears later, the inactive row is not selectable
    # until the owner enables it.
    monkeypatch.setenv(KEY, "test-not-a-real-key")
    with pytest.raises(NoProviderAvailable):
        InferenceGateway().complete("chat", [{"role": "user", "content": "hi"}])


def test_never_receives_personal_data(with_key, monkeypatch):
    call_command("seed_ai_defaults")
    monkeypatch.setattr(
        "ai_core.gateway.get_adapter",
        lambda p, **kw: pytest.fail("no adapter may be built for personal data"),
    )
    with pytest.raises(NoProviderAvailable):
        InferenceGateway().complete(
            "chat",
            [{"role": "user", "content": "hi"}],
            personal_data=True,
            only_provider="openrouter",
        )
    usage = ModelUsage.objects.get()
    assert usage.routing_reason == "personal_data_required"


def test_enabled_row_serves_through_the_gateway(with_key, monkeypatch):
    call_command("seed_ai_defaults")
    served = []

    class Recording:
        def __init__(self, provider):
            self.provider = provider

        def complete(self, messages, params, *, purpose=""):
            served.append(self.provider.name)
            return AdapterResult(text="ok", input_tokens=1, output_tokens=1)

    monkeypatch.setattr("ai_core.gateway.get_adapter", lambda p, **kw: Recording(p))
    result = InferenceGateway().complete("chat", [{"role": "user", "content": "hi"}])
    assert served == ["openrouter"]
    assert result.usage.provider.name == "openrouter"
