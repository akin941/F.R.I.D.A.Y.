"""Speech through the gateway (Phase 6 branch 1): D5's unconditional
personal-data rule for audio, A1's disabled OpenRouter rows, A2's TTS
language routing, A3's budgets and audio cost, the seeds, and the cost
correction task. No real speech model, OpenRouter or LLM: FakeAdapter for
gateway paths, `httpx.MockTransport` where the real adapter runs."""

import io
import json
import wave
from datetime import timedelta
from decimal import Decimal
from unittest import mock

import httpx
import pytest
from django.core.management import call_command
from django.utils import timezone

from accounts.services import register_user
from ai_core import budgets
from ai_core.adapters import get_adapter as real_get_adapter
from ai_core.adapters.fake import FAKE_TRANSCRIPT
from ai_core.adapters.openai_compat import OpenAICompatibleAdapter
from ai_core.exceptions import BudgetExceeded, NoProviderAvailable
from ai_core.gateway import InferenceGateway
from ai_core.models import AIBudget, ModelProvider, ModelUsage
from ai_core.tasks import CostNotYetAvailable, apply_cost_correction
from audit.models import AuditLog

pytestmark = pytest.mark.django_db

STT = ModelProvider.Capability.STT
TTS = ModelProvider.Capability.TTS
KEY_ENV = "FRIDAY_TEST_SPEECH_KEY"
_AUDIO_FREE_KEYS = {"text", "audio", "transcript", "input", "messages", "content"}


def make_wav(*, seconds=1.0, rate=16_000):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b"\x00\x00" * int(rate * seconds))
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def ai_settings(settings, monkeypatch):
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    settings.AI_PURPOSE_TIERS = {}
    settings.VOICE_TTS_VOICE = "default-voice"
    settings.VOICE_TTS_ROUTES = {}
    settings.VOICE_TTS_PROVIDER_PARAMS = {}
    monkeypatch.setenv(KEY_ENV, "placeholder")
    for name in ("OPENROUTER_API_KEY", "VOICE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return settings


@pytest.fixture
def user():
    return register_user(email="voice@test.dev", password="Str0ngDevPass!42", name="V")


@pytest.fixture
def workspace(user):
    return user.owned_workspaces.first()


@pytest.fixture
def calls():
    """Records every adapter the gateway builds; delegates to the real
    factory (FakeAdapter for fake rows -- no network)."""
    seen = []

    def factory(provider, **kwargs):
        adapter = real_get_adapter(provider, **kwargs)
        for method in ("transcribe", "synthesize"):
            original = getattr(adapter, method)

            def recorder(*args, _original=original, _method=method, **kw):
                seen.append({"provider": provider.name, "method": _method, **kw})
                return _original(*args, **kw)

            setattr(adapter, method, recorder)
        return adapter

    with mock.patch("ai_core.gateway.get_adapter", side_effect=factory):
        yield seen


def provider(name, capability, *, priority=10, allowed=False, active=True, **extra):
    return ModelProvider.objects.create(
        name=name,
        provider_type=extra.pop("provider_type", ModelProvider.ProviderType.FAKE),
        capability=capability,
        model_id=f"{name}-model",
        priority=priority,
        personal_data_allowed=allowed,
        is_active=active,
        **extra,
    )


def stt(**kwargs):
    kwargs.setdefault("audio", make_wav(seconds=2))
    return InferenceGateway().transcribe("voice.stt", **kwargs)


def tts(text="Merhaba dünya.", **kwargs):
    return InferenceGateway().synthesize("voice.tts", text, **kwargs)


# --- D5: audio is always personal data ---


@pytest.mark.parametrize(("capability", "call"), [(STT, stt), (TTS, tts)])
def test_disallowed_only_fails_closed_without_reaching_the_adapter(
    user, workspace, calls, capability, call
):
    provider("cloud-audio", capability, priority=0, allowed=False)
    with pytest.raises(NoProviderAvailable):
        call(actor=user, workspace=workspace)
    assert calls == []
    usage = ModelUsage.objects.get()
    assert usage.error_type == "no_provider"
    assert usage.routing_reason == "personal_data_required"
    assert usage.context_report["personal_data"] is True


@pytest.mark.parametrize(("capability", "call"), [(STT, stt), (TTS, tts)])
def test_allowed_provider_wins_over_better_priority_disallowed(
    user, workspace, calls, capability, call
):
    provider("cloud-audio", capability, priority=1, allowed=False)
    local = provider("local-audio", capability, priority=90, allowed=True)
    call(actor=user, workspace=workspace)
    assert [c["provider"] for c in calls] == ["local-audio"]
    usage = ModelUsage.objects.get()
    assert usage.provider == local
    assert usage.routing_reason == "only_provider"


def test_personal_data_kwarg_cannot_relax_audio_rule(user, workspace):
    provider("cloud-audio", STT, allowed=False)
    with pytest.raises(TypeError):
        stt(actor=user, workspace=workspace, personal_data=False)
    with pytest.raises(TypeError):
        tts(actor=user, workspace=workspace, personal_data=False)


def test_audio_preflight_is_always_personal_data():
    provider("cloud-audio", TTS, allowed=False)
    gateway = InferenceGateway()
    assert gateway.has_eligible_provider(purpose="voice.tts", capability=TTS) is False
    provider("local-audio", TTS, allowed=True)
    assert gateway.has_eligible_provider(purpose="voice.tts", capability=TTS) is True


def test_stt_language_param_reaches_adapter_and_report(user, workspace, calls):
    provider("local-stt", STT, allowed=True)
    result = stt(
        actor=user,
        workspace=workspace,
        params={"language": "tr"},
        context_report={"input_mode": "voice"},
    )
    assert result.text == FAKE_TRANSCRIPT
    assert result.language == "tr"
    assert result.audio_seconds == pytest.approx(2.0)  # from the WAV header
    assert calls[0]["params"] == {"language": "tr"}
    report = ModelUsage.objects.get().context_report
    assert report["language"] == "tr"
    assert report["input_mode"] == "voice"
    assert report["input_bytes"] == len(make_wav(seconds=2))
    assert report["audio_seconds"] == pytest.approx(2.0)


@pytest.mark.parametrize(("capability", "call"), [(STT, stt), (TTS, tts)])
def test_context_report_never_holds_text_or_audio(user, workspace, capability, call):
    provider("local", capability, allowed=True)
    call(actor=user, workspace=workspace, params={"language": "tr"})
    usage = ModelUsage.objects.get()
    assert not _AUDIO_FREE_KEYS & set(usage.context_report)
    serialized = json.dumps(usage.context_report, ensure_ascii=False)
    assert FAKE_TRANSCRIPT not in serialized
    assert "Merhaba" not in serialized
    assert "RIFF" not in serialized


def test_fake_tts_output_is_a_valid_wav(user, workspace):
    provider("local-tts", TTS, allowed=True)
    result = tts(actor=user, workspace=workspace)
    assert result.audio[:4] == b"RIFF"
    assert result.content_type == "audio/wav"


# --- A1: OpenRouter rows are seeded disabled and disallowed ---


def _openrouter_transport(monkeypatch, hits):
    def handler(request):
        hits.append(request.url.path)
        if request.url.path.endswith("/audio/transcriptions"):
            return httpx.Response(
                200, json={"text": "uzak", "usage": {"seconds": 2, "cost": 0.0002}}
            )
        return httpx.Response(
            200,
            headers={"content-type": "audio/pcm;rate=24000;channels=1", "X-Generation-Id": "g-1"},
            content=b"\x00\x00" * 24_000,
        )

    monkeypatch.setattr(OpenAICompatibleAdapter, "http_transport", httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("active", "allowed"),
    [(False, False), (True, False), (False, True)],
)
@pytest.mark.parametrize(("capability", "call"), [(STT, stt), (TTS, tts)])
def test_openrouter_rows_never_selected_unless_active_and_allowed(
    settings, monkeypatch, user, workspace, active, allowed, capability, call
):
    settings.AI_FAKE_PROVIDER = False
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-not-a-real-key")
    call_command("seed_ai_defaults")
    name = "openrouter-stt" if capability == STT else "openrouter-tts"
    ModelProvider.objects.filter(name=name).update(is_active=active, personal_data_allowed=allowed)
    hits = []
    _openrouter_transport(monkeypatch, hits)
    with pytest.raises(NoProviderAvailable):
        call(actor=user, workspace=workspace)
    assert hits == []


def test_openrouter_tts_after_owner_toggle_costs_configured_price_and_schedules_lookup(
    settings, monkeypatch, user, workspace, django_capture_on_commit_callbacks
):
    settings.AI_FAKE_PROVIDER = False
    settings.VOICE_TTS_PROVIDER_PARAMS = {
        "openrouter-tts": {"voice": "alloy", "response_format": "pcm"}
    }
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-not-a-real-key")
    call_command("seed_ai_defaults")
    call_command("set_provider_personal_data", "openrouter-tts", "--allow", "--activate")
    hits = []
    _openrouter_transport(monkeypatch, hits)
    text = "x" * 100
    with mock.patch("ai_core.tasks.correct_usage_cost.delay") as delay:
        with django_capture_on_commit_callbacks(execute=True):
            result = tts(text, actor=user, workspace=workspace)
    assert hits == ["/api/v1/audio/speech"]
    assert result.audio[:4] == b"RIFF"
    usage = ModelUsage.objects.get()
    # 100 chars x 15 USD / 1M chars, plus no token price
    assert usage.estimated_cost == Decimal("0.001500")
    assert usage.context_report["cost_source"] == "configured_price"
    assert usage.context_report["generation_id"] == "g-1"
    delay.assert_called_once_with(usage.pk)


def test_openrouter_stt_cost_taken_from_response(settings, monkeypatch, user, workspace):
    settings.AI_FAKE_PROVIDER = False
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-not-a-real-key")
    call_command("seed_ai_defaults")
    call_command("set_provider_personal_data", "openrouter-stt", "--allow", "--activate")
    _openrouter_transport(monkeypatch, [])
    result = stt(actor=user, workspace=workspace)
    assert result.text == "uzak"
    usage = ModelUsage.objects.get()
    assert usage.estimated_cost == Decimal("0.000200")  # not 2 s x 0.0001 configured
    assert usage.context_report["cost_source"] == "provider"


def test_set_provider_personal_data_activate_is_audited():
    call_command("seed_ai_defaults")
    call_command("set_provider_personal_data", "openrouter-tts", "--allow", "--activate")
    row = ModelProvider.objects.get(name="openrouter-tts")
    assert (row.is_active, row.personal_data_allowed) == (True, True)
    entry = AuditLog.objects.get(action="ai_core.provider_personal_data_set")
    assert entry.metadata["previous_is_active"] is False
    assert entry.metadata["is_active"] is True
    assert entry.metadata["active_changed"] is True
    call_command("set_provider_personal_data", "openrouter-tts", "--deny", "--deactivate")
    row.refresh_from_db()
    assert (row.is_active, row.personal_data_allowed) == (False, False)


# --- A2: TTS routed by language, only among allowed rows ---


def test_tts_route_picks_routed_provider_with_its_voice(settings, user, workspace, calls):
    provider("tts-a", TTS, priority=1, allowed=True)
    provider("tts-b", TTS, priority=50, allowed=True)
    settings.VOICE_TTS_ROUTES = {"en": {"provider": "tts-b", "voice": "en-voice"}}
    tts(actor=user, workspace=workspace, params={"language": "en"})
    assert calls[0]["provider"] == "tts-b"
    assert calls[0]["params"]["voice"] == "en-voice"
    usage = ModelUsage.objects.get()
    assert usage.routing_reason == "route:en"
    assert usage.context_report["tts_route"] == "tts-b"


def test_tts_route_to_disallowed_provider_is_ignored(settings, user, workspace, calls):
    provider("tts-cloud", TTS, priority=1, allowed=False)
    provider("tts-local", TTS, priority=50, allowed=True)
    settings.VOICE_TTS_ROUTES = {"tr": {"provider": "tts-cloud", "voice": "cloud-voice"}}
    tts(actor=user, workspace=workspace, params={"language": "tr"})
    assert [c["provider"] for c in calls] == ["tts-local"]
    # the route's voice belongs to the routed provider, never to a fallback
    assert calls[0]["params"]["voice"] == "default-voice"
    assert ModelUsage.objects.get().routing_reason == "only_provider"


def test_tts_route_to_disabled_provider_is_ignored(settings, user, workspace, calls):
    provider("tts-off", TTS, priority=1, allowed=True, active=False)
    provider("tts-local", TTS, priority=50, allowed=True)
    settings.VOICE_TTS_ROUTES = {"tr": {"provider": "tts-off", "voice": "v"}}
    tts(actor=user, workspace=workspace, params={"language": "tr"})
    assert [c["provider"] for c in calls] == ["tts-local"]


def test_tts_unrouted_language_uses_priority(settings, user, workspace, calls):
    provider("tts-a", TTS, priority=1, allowed=True)
    provider("tts-b", TTS, priority=50, allowed=True)
    settings.VOICE_TTS_ROUTES = {"en": {"provider": "tts-b", "voice": "v"}}
    tts(actor=user, workspace=workspace, params={"language": "de"})
    assert calls[0]["provider"] == "tts-a"
    assert ModelUsage.objects.get().routing_reason == "priority"


# --- A3: budgets, checked before provider selection ---


def _budget(**limits):
    return AIBudget.objects.create(name="audio", purposes=["voice.stt", "voice.tts"], **limits)


def _spend(amount, *, purpose="voice.tts", when=None, session=""):
    usage = ModelUsage.objects.create(
        purpose=purpose,
        success=True,
        estimated_cost=Decimal(amount),
        context_report={"budget_session": session} if session else {},
    )
    if when is not None:
        ModelUsage.objects.filter(pk=usage.pk).update(created_at=when)
    return usage


def _priced_tts(**extra):
    return provider("paid-tts", TTS, allowed=True, price_per_mchar=Decimal("15"), **extra)


def _assert_refused(window, calls):
    assert calls == []
    refused = ModelUsage.objects.get(error_type="budget_exceeded")
    assert refused.provider is None
    assert refused.estimated_cost == 0
    assert refused.success is False
    assert refused.routing_reason == f"budget:audio:{window}"
    assert refused.context_report["budget"]["window"] == window
    return refused


@pytest.mark.parametrize(
    ("limits", "window", "session"),
    [
        ({"monthly_limit_usd": Decimal("20")}, "monthly", ""),
        ({"daily_limit_usd": Decimal("20")}, "daily", ""),
        ({"session_limit_usd": Decimal("20")}, "session", "s-1"),
    ],
)
def test_exhausted_window_refuses_before_selection(user, workspace, calls, limits, window, session):
    _budget(**limits)
    _priced_tts()
    _spend("20", session=session)
    with pytest.raises(BudgetExceeded) as excinfo:
        tts(actor=user, workspace=workspace, budget_session=session)
    assert excinfo.value.error_type == "budget_exceeded"
    assert excinfo.value.retryable is False
    assert excinfo.value.window == window
    _assert_refused(window, calls)


def test_estimate_that_would_cross_the_cap_is_refused(user, workspace, calls):
    _budget(monthly_limit_usd=Decimal("1"))
    _priced_tts()
    _spend("0.999")
    with pytest.raises(BudgetExceeded):
        tts("x" * 400, actor=user, workspace=workspace)  # 400 chars = 0.006 USD
    _assert_refused("monthly", calls)


def test_refusal_happens_before_provider_selection(user, workspace, calls):
    _budget(monthly_limit_usd=Decimal("1"))
    _priced_tts()
    _spend("5")
    with mock.patch.object(
        InferenceGateway, "_select_providers", side_effect=AssertionError("selected")
    ):
        with pytest.raises(BudgetExceeded):
            tts(actor=user, workspace=workspace)
    assert ModelUsage.objects.filter(error_type="no_provider").count() == 0


def test_no_priced_candidate_means_no_budget_refusal(user, workspace):
    # Nothing could cost anything, so an exhausted cap is not the reason
    # the call fails: it is an ordinary no-provider failure.
    _budget(monthly_limit_usd=Decimal("1"))
    _spend("5", purpose="voice.stt")
    with pytest.raises(NoProviderAvailable):
        stt(actor=user, workspace=workspace)


def test_under_cap_call_proceeds_and_is_priced(user, workspace, calls):
    _budget(monthly_limit_usd=Decimal("1"), session_limit_usd=Decimal("1"))
    _priced_tts()
    tts("x" * 1000, actor=user, workspace=workspace, budget_session="s-1")
    usage = ModelUsage.objects.get()
    assert usage.success is True
    assert usage.estimated_cost == Decimal("0.015000")
    assert usage.context_report["budget_session"] == "s-1"
    assert usage.context_report["cost_source"] == "configured_price"
    audio = AIBudget.objects.get(name="audio")
    assert budgets.spent(audio, "session", session="s-1") == Decimal("0.015")


def test_stt_cost_from_configured_per_second_price(user, workspace):
    provider("paid-stt", STT, allowed=True, price_per_audio_second=Decimal("0.0001"))
    stt(actor=user, workspace=workspace, audio=make_wav(seconds=10))
    assert ModelUsage.objects.get().estimated_cost == Decimal("0.001000")


def test_local_cost_zero_calls_never_trip_an_exhausted_cap(user, workspace, calls):
    _budget(monthly_limit_usd=Decimal("1"), daily_limit_usd=Decimal("1"))
    provider("local-tts", TTS, allowed=True)  # unpriced
    _spend("50")
    tts(actor=user, workspace=workspace)
    usage = ModelUsage.objects.get(purpose="voice.tts", provider__isnull=False)
    assert usage.success is True
    assert usage.estimated_cost == 0
    assert usage.context_report["cost_source"] == "free"


def test_disabled_priced_row_does_not_count_as_a_candidate(user, workspace, calls):
    # The seeded-disabled OpenRouter row must not make local calls refusable.
    _budget(monthly_limit_usd=Decimal("1"))
    _priced_tts(active=False)
    provider("local-tts", TTS, allowed=True)
    _spend("50")
    tts(actor=user, workspace=workspace)
    assert ModelUsage.objects.filter(error_type="budget_exceeded").count() == 0


def test_previous_month_and_day_spend_do_not_count(user, workspace, calls):
    _budget(monthly_limit_usd=Decimal("1"), daily_limit_usd=Decimal("1"))
    _priced_tts()
    _spend("5", when=timezone.now() - timedelta(days=40))
    tts(actor=user, workspace=workspace)
    assert ModelUsage.objects.filter(error_type="budget_exceeded").count() == 0


def test_other_session_and_other_purposes_do_not_count(user, workspace, calls):
    _budget(session_limit_usd=Decimal("1"))
    _priced_tts()
    _spend("5", session="other")
    _spend("5", purpose="chat", session="s-1")
    tts(actor=user, workspace=workspace, budget_session="s-1")
    assert ModelUsage.objects.filter(error_type="budget_exceeded").count() == 0


def test_inactive_budget_is_ignored(user, workspace, calls):
    AIBudget.objects.create(
        name="audio", purposes=["voice.tts"], monthly_limit_usd=Decimal("0"), is_active=False
    )
    _priced_tts()
    tts(actor=user, workspace=workspace)
    assert ModelUsage.objects.get().success is True


def test_chat_budget_refuses_once_spent_and_only_for_priced_candidates(user, workspace):
    AIBudget.objects.create(name="chat", purposes=["chat"], daily_limit_usd=Decimal("1"))
    ModelUsage.objects.create(purpose="chat", success=True, estimated_cost=Decimal("1"))
    free = provider("fake-chat", ModelProvider.Capability.CHAT, allowed=True)
    messages = [{"role": "user", "content": "hi"}]
    InferenceGateway().complete("chat", messages, actor=user, workspace=workspace)
    free.input_price_per_mtok = Decimal("1")
    free.save()
    with pytest.raises(BudgetExceeded):
        InferenceGateway().complete("chat", messages, actor=user, workspace=workspace)
    with pytest.raises(BudgetExceeded):
        InferenceGateway().complete("chat", messages, actor=user, workspace=workspace, stream=True)
    assert ModelUsage.objects.filter(error_type="budget_exceeded").count() == 2


def test_every_audio_purpose_with_a_priced_provider_has_a_budget(monkeypatch):
    """A3 coverage: after seeding with every key present, each audio
    purpose whose capability has a non-zero-cost provider row (active or
    not) is covered by an active budget with at least one limit."""
    for name in ("OPENROUTER_API_KEY", "VOICE_API_KEY", "OLLAMA_API_KEY"):
        monkeypatch.setenv(name, "test-not-a-real-key")
    call_command("seed_ai_defaults")
    from django.conf import settings

    capability_of = {
        "voice.stt": STT,
        "voice.tts": TTS,
        # Phase 6 branch 3a: the evaluation set's own calls.
        "voice.eval_stt": STT,
        "voice.eval_tts": TTS,
    }
    assert set(settings.AI_AUDIO_PURPOSES) == set(capability_of)
    for purpose, capability in capability_of.items():
        priced = [p for p in ModelProvider.objects.filter(capability=capability) if p.is_priced]
        if not priced:
            continue
        covering = budgets.budgets_for(purpose)
        assert covering, purpose
        assert any(
            b.monthly_limit_usd is not None
            or b.daily_limit_usd is not None
            or b.session_limit_usd is not None
            for b in covering
        ), purpose


# --- seeds (D4) ---


def test_seed_fake_speech_rows_and_audio_budget():
    call_command("seed_ai_defaults")
    for name, capability in (("fake-stt-local", STT), ("fake-tts-local", TTS)):
        row = ModelProvider.objects.get(name=name)
        assert row.capability == capability
        assert row.provider_type == ModelProvider.ProviderType.FAKE
        assert (row.priority, row.is_active, row.personal_data_allowed) == (0, True, True)
    budget = AIBudget.objects.get(name="audio")
    assert set(budget.purposes) == {"voice.stt", "voice.tts", "voice.eval_stt", "voice.eval_tts"}
    assert budget.monthly_limit_usd == Decimal("20")
    assert budget.session_limit_usd == Decimal("1")
    assert budget.daily_limit_usd is None
    for name in ("openrouter-stt", "openrouter-tts"):
        row = ModelProvider.objects.get(name=name)
        assert (row.is_active, row.personal_data_allowed) == (False, False)
        assert row.api_key_env_var == "OPENROUTER_API_KEY"
        assert row.base_url == "https://openrouter.ai/api/v1"
        assert row.is_priced
    assert not ModelProvider.objects.filter(name__startswith="voice-").exists()


def test_seed_local_speech_rows_with_voice_key_and_idempotent(monkeypatch):
    monkeypatch.setenv("VOICE_API_KEY", "voice-local")
    monkeypatch.setenv("VOICE_STT_MODEL_ID", "Systran/faster-whisper-medium")
    call_command("seed_ai_defaults")
    call_command("seed_ai_defaults")
    stt_row = ModelProvider.objects.get(name="voice-stt-local")
    tts_row = ModelProvider.objects.get(name="voice-tts-local")
    tts_en_row = ModelProvider.objects.get(name="voice-tts-local-en")
    for row in (stt_row, tts_row, tts_en_row):
        assert row.provider_type == ModelProvider.ProviderType.OPENAI_COMPATIBLE
        assert row.base_url == "http://host.docker.internal:8000/v1"
        assert row.api_key_env_var == "VOICE_API_KEY"
        assert row.personal_data_allowed is True
        assert not row.is_priced
    assert stt_row.model_id == "Systran/faster-whisper-medium"
    # speaches selects the voice by model id, so English has its own row,
    # ranked after the Turkish one (the fallback for an unrouted language).
    assert tts_row.model_id == "speaches-ai/piper-tr_TR-dfki-medium"
    assert tts_en_row.model_id == "speaches-ai/piper-en_US-lessac-medium"
    assert (tts_row.priority, tts_en_row.priority) == (5, 6)
    assert (
        ModelProvider.objects.filter(
            name__in=["voice-stt-local", "voice-tts-local", "voice-tts-local-en"]
        ).count()
        == 3
    )
    # audio + the three Phase 8A and two Phase 8B learning budgets + the
    # Phase 8B eval cap + the Phase 10 `control` cap (migration 0008).
    assert set(AIBudget.objects.values_list("name", flat=True)) == {
        "audio",
        "learning",
        "learning_extraction_daily",
        "learning_consolidation_daily",
        "learning_reconciliation_daily",
        "learning_reflection_weekly",
        "learning_eval",
        "control",
        "proactive",
        "skills",
        "skills_curate_weekly",
        # Phase 12 D17 (ai_core 0013): the playbook distiller's daily cap.
        "skills_playbook_daily",
    }


def test_reseed_keeps_owner_budget_and_toggle_edits():
    call_command("seed_ai_defaults")
    AIBudget.objects.filter(name="audio").update(monthly_limit_usd=Decimal("5"))
    call_command("set_provider_personal_data", "openrouter-tts", "--allow", "--activate")
    call_command("seed_ai_defaults")
    assert AIBudget.objects.get(name="audio").monthly_limit_usd == Decimal("5")
    row = ModelProvider.objects.get(name="openrouter-tts")
    assert (row.is_active, row.personal_data_allowed) == (True, True)


def test_model_default_personal_data_stays_false():
    assert ModelProvider._meta.get_field("personal_data_allowed").default is False


# --- cost correction task (A1/A3) ---


def _correctable_usage(**report):
    row = provider(
        "or-tts",
        TTS,
        allowed=True,
        provider_type=ModelProvider.ProviderType.OPENAI_COMPATIBLE,
        api_key_env_var=KEY_ENV,
        base_url="https://openrouter.test/api/v1",
        price_per_mchar=Decimal("15"),
    )
    return ModelUsage.objects.create(
        purpose="voice.tts",
        provider=row,
        success=True,
        estimated_cost=Decimal("0.0015"),
        context_report={"cost_source": "configured_price", "generation_id": "g-1", **report},
    )


def _lookup(monkeypatch, response):
    hits = []

    def handler(request):
        hits.append(request.url.params.get("id"))
        return response

    monkeypatch.setattr(OpenAICompatibleAdapter, "http_transport", httpx.MockTransport(handler))
    return hits


def test_cost_correction_rewrites_the_same_row(monkeypatch):
    usage = _correctable_usage()
    hits = _lookup(monkeypatch, httpx.Response(200, json={"data": {"total_cost": 0.0021}}))
    assert apply_cost_correction(usage.pk) == "corrected"
    usage.refresh_from_db()
    assert usage.estimated_cost == Decimal("0.002100")
    assert usage.context_report["cost_source"] == "provider_lookup"
    assert usage.context_report["estimated_cost_before_correction"] == "0.001500"
    assert hits == ["g-1"]
    assert ModelUsage.objects.count() == 1
    # idempotent: a corrected row is never looked up again
    assert apply_cost_correction(usage.pk) == "skipped"
    assert hits == ["g-1"]


def test_cost_correction_waits_while_not_available(monkeypatch):
    usage = _correctable_usage()
    _lookup(monkeypatch, httpx.Response(404))
    with pytest.raises(CostNotYetAvailable):
        apply_cost_correction(usage.pk)
    usage.refresh_from_db()
    assert usage.context_report["cost_source"] == "configured_price"


def test_cost_correction_skips_rows_with_provider_cost(monkeypatch):
    usage = _correctable_usage(cost_source="provider")
    hits = _lookup(monkeypatch, httpx.Response(200, json={"data": {"total_cost": 1}}))
    assert apply_cost_correction(usage.pk) == "skipped"
    assert hits == []


def test_default_routes_send_english_to_its_own_local_row():
    # speaches selects the Piper voice by model id (the request's `voice` is
    # ignored), so `en` must route to the English row, not the Turkish one.
    # Read the shipped default (test settings and fixtures override the live one).
    from config.settings import base

    routes = base.VOICE_TTS_ROUTES
    assert routes["tr"]["provider"] == "voice-tts-local"
    assert routes["en"]["provider"] == "voice-tts-local-en"


def test_reseed_adds_the_eval_purposes_to_an_existing_audio_budget():
    """Phase 6 branch 3a: an installation seeded before the eval purposes
    existed gains them on reseed; the owner's limits stay untouched."""
    AIBudget.objects.create(
        name="audio", purposes=["voice.stt", "voice.tts"], monthly_limit_usd=Decimal("7")
    )
    call_command("seed_ai_defaults")
    budget = AIBudget.objects.get(name="audio")
    assert budget.purposes == ["voice.stt", "voice.tts", "voice.eval_stt", "voice.eval_tts"]
    assert budget.monthly_limit_usd == Decimal("7") and budget.session_limit_usd is None
