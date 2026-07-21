from decimal import Decimal
from unittest import mock

import pytest

from accounts.services import register_user
from ai_core.adapters.base import (
    AdapterError,
    AdapterResult,
    EmbedAdapterResult,
    TranscribeAdapterResult,
)
from ai_core.adapters.fake import FAKE_RESPONSE
from ai_core.exceptions import GatewayError, NoProviderAvailable
from ai_core.gateway import InferenceGateway
from ai_core.models import ModelProvider, ModelTier, ModelUsage

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture(autouse=True)
def ai_settings(settings):
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    return settings


@pytest.fixture
def user():
    return register_user(email="alice@test.dev", password=PASSWORD, name="Alice")


@pytest.fixture
def workspace(user):
    return user.owned_workspaces.first()


@pytest.fixture
def fake_provider():
    return ModelProvider.objects.create(
        name="fake-local",
        provider_type=ModelProvider.ProviderType.FAKE,
        model_id="fake-1",
        priority=0,
        input_price_per_mtok=Decimal("2.0"),
        output_price_per_mtok=Decimal("10.0"),
    )


@pytest.fixture
def fake_embedding_provider():
    return ModelProvider.objects.create(
        name="fake-embedding-local",
        provider_type=ModelProvider.ProviderType.FAKE,
        capability=ModelProvider.Capability.EMBEDDING,
        model_id="fake-embedding-1",
        dimension=32,
        priority=0,
        input_price_per_mtok=Decimal("1.0"),
    )


def make_real_provider(name="real-a", priority=10):
    """A provider whose type would hit a real SDK — only used behind mocks."""
    return ModelProvider.objects.create(
        name=name,
        provider_type=ModelProvider.ProviderType.ANTHROPIC,
        model_id="test-model",
        api_key_env_var="PATH",  # always present in env; no network happens (mocked)
        priority=priority,
        input_price_per_mtok=Decimal("3.0"),
        output_price_per_mtok=Decimal("15.0"),
    )


def make_real_embedding_provider(name="real-embedding-a", priority=10):
    """A capability="embedding" provider whose type would hit a real SDK —
    only used behind mocks."""
    return ModelProvider.objects.create(
        name=name,
        provider_type=ModelProvider.ProviderType.OPENAI_COMPATIBLE,
        capability=ModelProvider.Capability.EMBEDDING,
        model_id="test-embedding-model",
        api_key_env_var="PATH",  # always present in env; no network happens (mocked)
        dimension=8,
        priority=priority,
        input_price_per_mtok=Decimal("0.1"),
    )


def call(gateway, **kwargs):
    return gateway.complete(
        purpose="chat",
        messages=[{"role": "user", "content": "Merhaba"}],
        **kwargs,
    )


# --- happy paths with the fake adapter ---


def test_fake_complete_records_usage(user, workspace, fake_provider):
    result = call(
        InferenceGateway(),
        actor=user,
        workspace=workspace,
        context_report={"budget_profile": "test"},
    )
    assert result.text == FAKE_RESPONSE
    usage = ModelUsage.objects.get()
    assert usage.success is True
    assert usage.user == user
    assert usage.workspace == workspace
    assert usage.purpose == "chat"
    assert usage.provider == fake_provider
    assert usage.input_tokens > 0 and usage.output_tokens > 0
    assert usage.estimated_cost > 0
    assert usage.context_report["budget_profile"] == "test"
    assert usage.context_report["attempts"][0]["error_type"] is None


def test_fake_stream_records_usage(user, workspace, fake_provider):
    chunks = list(call(InferenceGateway(), actor=user, workspace=workspace, stream=True))
    assert "".join(chunks) == FAKE_RESPONSE
    assert len(chunks) > 1
    usage = ModelUsage.objects.get()
    assert usage.success is True
    assert usage.output_tokens > 0


def test_cost_is_exact_decimal(user, workspace, fake_provider):
    call(InferenceGateway(), actor=user, workspace=workspace)
    usage = ModelUsage.objects.get()
    expected = (
        Decimal(usage.input_tokens) * Decimal("2.0")
        + Decimal(usage.output_tokens) * Decimal("10.0")
    ) / Decimal(1_000_000)
    assert usage.estimated_cost == expected.quantize(Decimal("0.000001"))


# --- retry / fallback / failure (mock adapters, no SDK, no network) ---


def ok_result():
    return AdapterResult(text="ok", input_tokens=10, output_tokens=5, finish_reason="stop")


def test_retryable_error_then_success_single_usage(user, workspace):
    make_real_provider()
    adapter = mock.Mock()
    adapter.complete.side_effect = [
        AdapterError("rate_limit", retryable=True),
        ok_result(),
    ]
    with mock.patch("ai_core.gateway.get_adapter", return_value=adapter):
        result = call(InferenceGateway(), actor=user, workspace=workspace)
    assert result.text == "ok"
    usage = ModelUsage.objects.get()  # exactly one row
    assert usage.success is True
    assert [a["error_type"] for a in usage.context_report["attempts"]] == ["rate_limit", None]


def test_permanent_failure_single_usage(user, workspace):
    make_real_provider()
    adapter = mock.Mock()
    adapter.complete.side_effect = AdapterError("provider_error", retryable=True)
    with mock.patch("ai_core.gateway.get_adapter", return_value=adapter):
        with pytest.raises(GatewayError) as excinfo:
            call(InferenceGateway(), actor=user, workspace=workspace)
    assert excinfo.value.error_type == "provider_error"
    usage = ModelUsage.objects.get()
    assert usage.success is False
    assert usage.error_type == "provider_error"


def test_fallback_to_second_provider(user, workspace):
    first = make_real_provider(name="real-a", priority=10)
    second = make_real_provider(name="real-b", priority=20)

    failing = mock.Mock()
    failing.complete.side_effect = AdapterError("auth_error", retryable=False)
    working = mock.Mock()
    working.complete.return_value = ok_result()

    def adapter_for(provider, **kwargs):
        return failing if provider == first else working

    with mock.patch("ai_core.gateway.get_adapter", side_effect=adapter_for):
        result = call(InferenceGateway(), actor=user, workspace=workspace)
    assert result.text == "ok"
    usage = ModelUsage.objects.get()
    assert usage.success is True
    assert usage.provider == second
    providers_tried = [a["provider"] for a in usage.context_report["attempts"]]
    assert providers_tried == ["real-a", "real-b"]


def test_unexpected_exception_records_usage_without_leaking_message(user, workspace):
    make_real_provider()
    adapter = mock.Mock()
    adapter.complete.side_effect = ValueError("boom secret-like raw error")
    with mock.patch("ai_core.gateway.get_adapter", return_value=adapter):
        with pytest.raises(GatewayError) as excinfo:
            call(InferenceGateway(), actor=user, workspace=workspace)
    assert excinfo.value.error_type == "unknown"
    assert ModelUsage.objects.count() == 1
    usage = ModelUsage.objects.get()
    assert usage.success is False
    assert usage.error_type == "unknown"
    assert "boom secret-like raw error" not in usage.error_type
    assert "boom secret-like raw error" not in str(usage.context_report)


def test_no_provider_records_usage_and_raises(user, workspace):
    # No providers at all → nullable provider FK carries the accounting.
    with pytest.raises(NoProviderAvailable) as excinfo:
        call(InferenceGateway(), actor=user, workspace=workspace)
    usage = ModelUsage.objects.get()
    assert usage.success is False
    assert usage.provider is None
    assert usage.error_type == "no_provider"
    # A caller (e.g. a background extraction job) may retry the whole call —
    # a missing/inactive provider is a transient operational state.
    assert excinfo.value.retryable is True


# --- GatewayError.retryable classification ---


def test_retryable_flag_propagates_from_adapter_on_permanent_failure(user, workspace):
    """provider_error/rate_limit/timeout are adapter-classified retryable —
    the caller (e.g. MemoryExtractionJob) reads this to decide whether to
    return the job to pending or fail it terminally."""
    make_real_provider()
    adapter = mock.Mock()
    adapter.complete.side_effect = AdapterError("rate_limit", retryable=True)
    with mock.patch("ai_core.gateway.get_adapter", return_value=adapter):
        with pytest.raises(GatewayError) as excinfo:
            call(InferenceGateway(), actor=user, workspace=workspace)
    assert excinfo.value.retryable is True


def test_non_retryable_flag_propagates_from_adapter(user, workspace):
    make_real_provider()
    adapter = mock.Mock()
    adapter.complete.side_effect = AdapterError("auth_error", retryable=False)
    with mock.patch("ai_core.gateway.get_adapter", return_value=adapter):
        with pytest.raises(GatewayError) as excinfo:
            call(InferenceGateway(), actor=user, workspace=workspace)
    assert excinfo.value.retryable is False


def test_unknown_error_defaults_to_non_retryable(user, workspace):
    """An unexpected (non-AdapterError) exception must not be assumed
    transient."""
    make_real_provider()
    adapter = mock.Mock()
    adapter.complete.side_effect = ValueError("boom")
    with mock.patch("ai_core.gateway.get_adapter", return_value=adapter):
        with pytest.raises(GatewayError) as excinfo:
            call(InferenceGateway(), actor=user, workspace=workspace)
    assert excinfo.value.retryable is False


def test_stream_midway_retryable_flag_propagates(user, workspace):
    make_real_provider()

    def broken_stream(messages, params):
        yield "partial "
        raise AdapterError("timeout", retryable=True)

    adapter = mock.Mock()
    adapter.stream.side_effect = lambda m, p, **kw: broken_stream(m, p)
    with mock.patch("ai_core.gateway.get_adapter", return_value=adapter):
        gen = call(InferenceGateway(), actor=user, workspace=workspace, stream=True)
        with pytest.raises(GatewayError) as excinfo:
            list(gen)
    assert excinfo.value.retryable is True


def test_fake_provider_not_selectable_without_flag(user, workspace, fake_provider, settings):
    settings.AI_FAKE_PROVIDER = False
    with pytest.raises(NoProviderAvailable):
        call(InferenceGateway(), actor=user, workspace=workspace)


# --- streaming failure modes ---


def test_stream_midway_exception_records_failure(user, workspace):
    make_real_provider()

    def broken_stream(messages, params):
        yield "partial "
        raise AdapterError("provider_error", retryable=True)

    adapter = mock.Mock()
    adapter.stream.side_effect = lambda m, p, **kw: broken_stream(m, p)
    with mock.patch("ai_core.gateway.get_adapter", return_value=adapter):
        gen = call(InferenceGateway(), actor=user, workspace=workspace, stream=True)
        with pytest.raises(GatewayError):
            list(gen)
    usage = ModelUsage.objects.get()
    assert usage.success is False
    assert usage.error_type == "provider_error"


def test_client_disconnect_records_usage(user, workspace, fake_provider):
    gen = call(InferenceGateway(), actor=user, workspace=workspace, stream=True)
    next(gen)
    gen.close()  # simulate the client going away mid-stream
    usage = ModelUsage.objects.get()
    assert usage.success is False
    assert usage.error_type == "client_disconnect"
    assert usage.output_tokens >= 1  # estimated from what was sent


def test_stream_first_chunk_unexpected_exception_records_usage(user, workspace):
    make_real_provider()

    def broken_stream(messages, params):
        raise ValueError("boom secret-like raw error")
        yield  # pragma: no cover — makes this a generator

    adapter = mock.Mock()
    adapter.stream.side_effect = lambda m, p, **kw: broken_stream(m, p)
    with mock.patch("ai_core.gateway.get_adapter", return_value=adapter):
        with pytest.raises(GatewayError) as excinfo:
            list(call(InferenceGateway(), actor=user, workspace=workspace, stream=True))
    assert excinfo.value.error_type == "unknown"
    assert ModelUsage.objects.count() == 1
    usage = ModelUsage.objects.get()
    assert usage.success is False
    assert usage.error_type == "unknown"
    assert "boom secret-like raw error" not in usage.error_type
    assert "boom secret-like raw error" not in str(usage.context_report)


def test_stream_start_failure_falls_back(user, workspace):
    first = make_real_provider(name="real-a", priority=10)
    make_real_provider(name="real-b", priority=20)

    def failing_stream(messages, params):
        raise AdapterError("timeout", retryable=True)
        yield  # pragma: no cover — makes this a generator

    def working_stream(messages, params):
        yield "ok"
        return ok_result()

    failing = mock.Mock()
    failing.stream.side_effect = lambda m, p, **kw: failing_stream(m, p)
    working = mock.Mock()
    working.stream.side_effect = lambda m, p, **kw: working_stream(m, p)

    def adapter_for(provider, **kwargs):
        return failing if provider == first else working

    with mock.patch("ai_core.gateway.get_adapter", side_effect=adapter_for):
        chunks = list(call(InferenceGateway(), actor=user, workspace=workspace, stream=True))
    assert chunks == ["ok"]
    usage = ModelUsage.objects.get()
    assert usage.success is True
    assert usage.provider.name == "real-b"


# --- on_usage callback (assistant chat branch) ---


def test_on_usage_optional_and_backward_compatible(user, workspace, fake_provider):
    result = call(InferenceGateway(), actor=user, workspace=workspace)
    assert result.text == FAKE_RESPONSE
    assert ModelUsage.objects.count() == 1


def test_on_usage_called_once_on_sync_success(user, workspace, fake_provider):
    received = []
    call(InferenceGateway(), actor=user, workspace=workspace, on_usage=received.append)
    assert len(received) == 1
    assert ModelUsage.objects.count() == 1
    assert received[0] == ModelUsage.objects.get()


def test_on_usage_called_once_on_sync_failure(user, workspace):
    make_real_provider()
    adapter = mock.Mock()
    adapter.complete.side_effect = AdapterError("provider_error", retryable=False)
    received = []
    with mock.patch("ai_core.gateway.get_adapter", return_value=adapter):
        with pytest.raises(GatewayError):
            call(InferenceGateway(), actor=user, workspace=workspace, on_usage=received.append)
    assert len(received) == 1
    assert ModelUsage.objects.count() == 1
    assert received[0] == ModelUsage.objects.get()


def test_on_usage_called_once_on_no_provider(user, workspace):
    received = []
    with pytest.raises(NoProviderAvailable):
        call(InferenceGateway(), actor=user, workspace=workspace, on_usage=received.append)
    assert len(received) == 1
    assert ModelUsage.objects.count() == 1
    assert received[0] == ModelUsage.objects.get()


def test_on_usage_called_once_on_stream_success(user, workspace, fake_provider):
    received = []
    chunks = list(
        call(
            InferenceGateway(),
            actor=user,
            workspace=workspace,
            stream=True,
            on_usage=received.append,
        )
    )
    assert "".join(chunks) == FAKE_RESPONSE
    assert len(received) == 1
    assert ModelUsage.objects.count() == 1
    assert received[0] == ModelUsage.objects.get()


def test_on_usage_called_once_on_stream_midway_failure(user, workspace):
    make_real_provider()

    def broken_stream(messages, params):
        yield "partial "
        raise AdapterError("provider_error", retryable=True)

    adapter = mock.Mock()
    adapter.stream.side_effect = lambda m, p, **kw: broken_stream(m, p)
    received = []
    with mock.patch("ai_core.gateway.get_adapter", return_value=adapter):
        gen = call(
            InferenceGateway(),
            actor=user,
            workspace=workspace,
            stream=True,
            on_usage=received.append,
        )
        with pytest.raises(GatewayError):
            list(gen)
    assert len(received) == 1
    assert ModelUsage.objects.count() == 1
    assert received[0] == ModelUsage.objects.get()


def test_on_usage_called_once_on_client_disconnect(user, workspace, fake_provider):
    received = []
    gen = call(
        InferenceGateway(),
        actor=user,
        workspace=workspace,
        stream=True,
        on_usage=received.append,
    )
    next(gen)
    gen.close()
    assert len(received) == 1
    assert ModelUsage.objects.count() == 1
    assert received[0] == ModelUsage.objects.get()
    assert received[0].error_type == "client_disconnect"


# --- embed() (MVP 3 §1: embeddings are a gateway call like any other) ---


def test_fake_embed_records_usage(user, workspace, fake_embedding_provider):
    result = InferenceGateway().embed(
        purpose="knowledge.embed",
        texts=["hello world", "another text"],
        actor=user,
        workspace=workspace,
    )
    assert len(result.vectors) == 2
    usage = ModelUsage.objects.get()
    assert usage.success is True
    assert usage.provider == fake_embedding_provider
    assert usage.purpose == "knowledge.embed"
    assert usage.output_tokens == 0
    assert usage.input_tokens > 0
    assert usage.context_report["item_count"] == 2


def test_embed_capability_filtering_never_selects_chat_provider(user, workspace, fake_provider):
    """A chat-capability provider must never be selected for an embed() call,
    even though it is the only active provider."""
    with pytest.raises(NoProviderAvailable):
        InferenceGateway().embed(
            purpose="knowledge.embed", texts=["hi"], actor=user, workspace=workspace
        )


def test_complete_capability_filtering_never_selects_embedding_provider(
    user, workspace, fake_embedding_provider
):
    """An embedding-capability provider must never be selected for a
    complete() call, even though it is the only active provider."""
    with pytest.raises(NoProviderAvailable):
        call(InferenceGateway(), actor=user, workspace=workspace)


def test_embed_no_provider_records_usage_and_raises(user, workspace):
    with pytest.raises(NoProviderAvailable) as excinfo:
        InferenceGateway().embed(
            purpose="knowledge.embed", texts=["hi"], actor=user, workspace=workspace
        )
    usage = ModelUsage.objects.get()
    assert usage.success is False
    assert usage.provider is None
    assert usage.error_type == "no_provider"
    assert excinfo.value.retryable is True


def ok_embed_result():
    return EmbedAdapterResult(vectors=[[0.1, 0.2]], input_tokens=5)


def test_embed_retryable_error_then_success_single_usage(user, workspace):
    make_real_embedding_provider()
    adapter = mock.Mock()
    adapter.embed.side_effect = [
        AdapterError("rate_limit", retryable=True),
        ok_embed_result(),
    ]
    with mock.patch("ai_core.gateway.get_adapter", return_value=adapter):
        result = InferenceGateway().embed(
            purpose="knowledge.embed", texts=["hi"], actor=user, workspace=workspace
        )
    assert result.vectors == [[0.1, 0.2]]
    usage = ModelUsage.objects.get()  # exactly one row
    assert usage.success is True
    assert [a["error_type"] for a in usage.context_report["attempts"]] == ["rate_limit", None]


def test_embed_permanent_failure_single_usage(user, workspace):
    make_real_embedding_provider()
    adapter = mock.Mock()
    adapter.embed.side_effect = AdapterError("auth_error", retryable=False)
    with mock.patch("ai_core.gateway.get_adapter", return_value=adapter):
        with pytest.raises(GatewayError) as excinfo:
            InferenceGateway().embed(
                purpose="knowledge.embed", texts=["hi"], actor=user, workspace=workspace
            )
    assert excinfo.value.error_type == "auth_error"
    usage = ModelUsage.objects.get()
    assert usage.success is False
    assert usage.error_type == "auth_error"


def test_embed_fallback_to_second_provider(user, workspace):
    first = make_real_embedding_provider(name="real-embedding-a", priority=10)
    second = make_real_embedding_provider(name="real-embedding-b", priority=20)

    failing = mock.Mock()
    failing.embed.side_effect = AdapterError("auth_error", retryable=False)
    working = mock.Mock()
    working.embed.return_value = ok_embed_result()

    def adapter_for(provider, **kwargs):
        return failing if provider == first else working

    with mock.patch("ai_core.gateway.get_adapter", side_effect=adapter_for):
        result = InferenceGateway().embed(
            purpose="knowledge.embed", texts=["hi"], actor=user, workspace=workspace
        )
    assert result.vectors == [[0.1, 0.2]]
    usage = ModelUsage.objects.get()
    assert usage.success is True
    assert usage.provider == second


def test_embed_on_usage_called_once(user, workspace, fake_embedding_provider):
    received = []
    InferenceGateway().embed(
        purpose="knowledge.embed",
        texts=["hi"],
        actor=user,
        workspace=workspace,
        on_usage=received.append,
    )
    assert len(received) == 1
    assert ModelUsage.objects.count() == 1
    assert received[0] == ModelUsage.objects.get()


# --- capability backfill: existing chat behavior is unchanged (MVP 3 §1 risk) ---


def test_legacy_provider_defaults_to_chat_capability_and_is_still_selected(user, workspace):
    """A provider created without specifying `capability` (as every MVP 1/2
    call site and fixture does) must default to "chat" and remain selectable
    by complete() exactly as before the capability field was added."""
    provider = ModelProvider.objects.create(
        name="fake-legacy",
        provider_type=ModelProvider.ProviderType.FAKE,
        model_id="fake-1",
        priority=0,
    )
    assert provider.capability == ModelProvider.Capability.CHAT
    result = call(InferenceGateway(), actor=user, workspace=workspace)
    assert result.text == FAKE_RESPONSE
    usage = ModelUsage.objects.get()
    assert usage.provider == provider


# --- transcribe()/synthesize() (MVP 12: STT/TTS are gateway calls too) ---


@pytest.fixture
def fake_stt_provider():
    return ModelProvider.objects.create(
        name="fake-stt-local",
        provider_type=ModelProvider.ProviderType.FAKE,
        capability=ModelProvider.Capability.STT,
        model_id="fake-stt-1",
        priority=0,
    )


@pytest.fixture
def fake_tts_provider():
    return ModelProvider.objects.create(
        name="fake-tts-local",
        provider_type=ModelProvider.ProviderType.FAKE,
        capability=ModelProvider.Capability.TTS,
        model_id="fake-tts-1",
        priority=0,
    )


def test_fake_transcribe_records_usage(user, workspace, fake_stt_provider):
    result = InferenceGateway().transcribe(
        purpose="voice.stt",
        audio=b"raw-audio-bytes",
        content_type="audio/wav",
        actor=user,
        workspace=workspace,
    )
    assert result.text
    assert result.audio_seconds > 0
    usage = ModelUsage.objects.get()
    assert usage.success is True
    assert usage.provider == fake_stt_provider
    assert usage.purpose == "voice.stt"
    assert usage.context_report["audio_seconds"] == result.audio_seconds


def test_fake_synthesize_records_usage(user, workspace, fake_tts_provider):
    result = InferenceGateway().synthesize(
        purpose="voice.tts",
        text="Merhaba dünya",
        actor=user,
        workspace=workspace,
    )
    assert isinstance(result.audio, bytes) and result.audio
    assert result.content_type == "audio/wav"
    usage = ModelUsage.objects.get()
    assert usage.success is True
    assert usage.provider == fake_tts_provider
    assert usage.purpose == "voice.tts"
    assert usage.input_tokens > 0  # source text estimate
    assert usage.context_report["audio_seconds"] == result.audio_seconds


def test_transcribe_capability_filtering_never_selects_chat_provider(
    user, workspace, fake_provider
):
    """A chat provider must never be selected for a transcribe() call."""
    with pytest.raises(NoProviderAvailable):
        InferenceGateway().transcribe(
            purpose="voice.stt", audio=b"x", actor=user, workspace=workspace
        )


def test_synthesize_capability_filtering_never_selects_stt_provider(
    user, workspace, fake_stt_provider
):
    """An STT provider must never be selected for a synthesize() call."""
    with pytest.raises(NoProviderAvailable):
        InferenceGateway().synthesize(
            purpose="voice.tts", text="hi", actor=user, workspace=workspace
        )


def test_transcribe_no_provider_records_usage_and_raises(user, workspace):
    with pytest.raises(NoProviderAvailable) as excinfo:
        InferenceGateway().transcribe(
            purpose="voice.stt", audio=b"x", actor=user, workspace=workspace
        )
    usage = ModelUsage.objects.get()
    assert usage.success is False
    assert usage.provider is None
    assert usage.error_type == "no_provider"
    assert excinfo.value.retryable is True


def test_transcribe_retryable_error_then_success_single_usage(user, workspace):
    ModelProvider.objects.create(
        name="real-stt",
        provider_type=ModelProvider.ProviderType.OPENAI_COMPATIBLE,
        capability=ModelProvider.Capability.STT,
        model_id="whisper-x",
        api_key_env_var="PATH",  # always present; no network happens (mocked)
        priority=10,
    )
    adapter = mock.Mock()
    adapter.transcribe.side_effect = [
        AdapterError("timeout", retryable=True),
        TranscribeAdapterResult(text="ok", audio_seconds=2.0, output_tokens=1),
    ]
    with mock.patch("ai_core.gateway.get_adapter", return_value=adapter):
        result = InferenceGateway().transcribe(
            purpose="voice.stt", audio=b"x", actor=user, workspace=workspace
        )
    assert result.text == "ok"
    usage = ModelUsage.objects.get()  # exactly one row
    assert usage.success is True
    assert [a["error_type"] for a in usage.context_report["attempts"]] == ["timeout", None]


def test_synthesize_permanent_failure_single_usage(user, workspace):
    ModelProvider.objects.create(
        name="real-tts",
        provider_type=ModelProvider.ProviderType.OPENAI_COMPATIBLE,
        capability=ModelProvider.Capability.TTS,
        model_id="piper-x",
        api_key_env_var="PATH",
        priority=10,
    )
    adapter = mock.Mock()
    adapter.synthesize.side_effect = AdapterError("auth_error", retryable=False)
    with mock.patch("ai_core.gateway.get_adapter", return_value=adapter):
        with pytest.raises(GatewayError) as excinfo:
            InferenceGateway().synthesize(
                purpose="voice.tts", text="hi", actor=user, workspace=workspace
            )
    usage = ModelUsage.objects.get()  # exactly one row, even on failure
    assert usage.success is False
    assert usage.error_type == "auth_error"
    assert excinfo.value.retryable is False


# --- tier routing skeleton (phase 0, MASTER_PLAN §J) ---


class TestTierRouting:
    def test_single_provider_records_only_provider(self, user, workspace, fake_provider):
        result = call(InferenceGateway(), actor=user, workspace=workspace)
        assert result.usage.routing_reason == "only_provider"

    def test_priority_order_and_reason_without_tier_config(self, user, workspace, fake_provider):
        # A second eligible (fake) provider at a worse priority: selection
        # must be byte-for-byte the old priority order, reason "priority".
        ModelProvider.objects.create(
            name="fake-slow",
            provider_type=ModelProvider.ProviderType.FAKE,
            model_id="fake-2",
            priority=50,
        )
        result = call(InferenceGateway(), actor=user, workspace=workspace)
        assert result.usage.provider == fake_provider
        assert result.usage.routing_reason == "priority"

    def test_tier_map_reorders_selection_and_records_tier_reason(
        self, user, workspace, fake_provider, settings
    ):
        # The tier-mapped provider loses on priority (50 vs 0) but the
        # purpose's tier puts it first -- the actual point of §J routing.
        cheap = ModelProvider.objects.create(
            name="fake-cheap",
            provider_type=ModelProvider.ProviderType.FAKE,
            model_id="fake-cheap-1",
            priority=50,
        )
        ModelTier.objects.create(tier=ModelTier.Tier.CHEAP, provider=cheap)
        settings.AI_PURPOSE_TIERS = {"chat": "cheap"}
        result = call(InferenceGateway(), actor=user, workspace=workspace)
        assert result.usage.provider == cheap
        assert result.usage.routing_reason == "tier:cheap"

    def test_unmapped_purpose_ignores_tier_rows(self, user, workspace, fake_provider, settings):
        cheap = ModelProvider.objects.create(
            name="fake-cheap",
            provider_type=ModelProvider.ProviderType.FAKE,
            model_id="fake-cheap-1",
            priority=50,
        )
        ModelTier.objects.create(tier=ModelTier.Tier.CHEAP, provider=cheap)
        settings.AI_PURPOSE_TIERS = {"some.other.purpose": "cheap"}
        result = call(InferenceGateway(), actor=user, workspace=workspace)
        assert result.usage.provider == fake_provider
        assert result.usage.routing_reason == "priority"

    def test_fallback_position_is_recorded(self, user, workspace, monkeypatch):
        # First provider hard-fails, second serves: the surviving usage row
        # must say the serving provider was the fallback, not the primary.
        primary = make_real_provider(name="real-a", priority=10)
        make_real_provider(name="real-b", priority=20)
        real_calls = {"n": 0}

        class FlakyAdapter:
            def __init__(self, provider):
                self.provider = provider

            def complete(self, messages, params, purpose=""):
                real_calls["n"] += 1
                if self.provider == primary:
                    raise AdapterError("auth_error", retryable=False)
                return AdapterResult(text="ok", input_tokens=1, output_tokens=1)

        monkeypatch.setattr("ai_core.gateway.get_adapter", lambda p, **kw: FlakyAdapter(p))
        result = call(InferenceGateway(), actor=user, workspace=workspace)
        assert result.usage.provider.name == "real-b"
        assert result.usage.routing_reason == "fallback:1"
        assert real_calls["n"] == 2


class TestRecordExternalUsage:
    """`record_external_usage()` (Phase 2 branch 2, brief §"ModelUsage
    stays ai_core-owned"): the one sanctioned way to write a ModelUsage
    row for a call that ran out of process, entirely outside
    complete()/embed()'s own provider-adapter path."""

    def test_creates_provider_null_row(self, user, workspace):
        from ai_core.gateway import record_external_usage

        usage = record_external_usage(
            workspace=workspace,
            purpose="harness.discovery",
            backend="harness_cli:claude_code",
            input_tokens=100,
            output_tokens=50,
            estimated_cost=Decimal("0.0123"),
            latency_ms=4200,
            success=True,
            actor=user,
        )
        usage.refresh_from_db()
        assert usage.provider is None
        assert usage.prompt_version is None
        assert usage.backend == "harness_cli:claude_code"
        assert usage.purpose == "harness.discovery"
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50
        assert usage.estimated_cost == Decimal("0.012300")
        assert usage.latency_ms == 4200
        assert usage.success is True
        assert usage.error_type == ""
        assert usage.routing_reason == ""
        assert usage.user == user
        assert usage.workspace == workspace

    def test_estimated_cost_is_stored_as_given_not_recomputed(self, workspace):
        from ai_core.gateway import record_external_usage

        usage = record_external_usage(
            workspace=workspace,
            purpose="harness.discovery",
            backend="harness_cli:claude_code",
            input_tokens=0,
            output_tokens=0,
            estimated_cost=Decimal("1.500000"),
            latency_ms=1,
            success=True,
        )
        assert usage.estimated_cost == Decimal("1.500000")

    def test_failure_path_records_error_type(self, workspace):
        from ai_core.gateway import record_external_usage

        usage = record_external_usage(
            workspace=workspace,
            purpose="harness.discovery",
            backend="harness_cli:claude_code",
            input_tokens=0,
            output_tokens=0,
            estimated_cost=Decimal("0"),
            latency_ms=500,
            success=False,
            error_type="cancelled",
        )
        assert usage.success is False
        assert usage.error_type == "cancelled"

    def test_context_report_round_trips(self, workspace):
        from ai_core.gateway import record_external_usage

        usage = record_external_usage(
            workspace=workspace,
            purpose="harness.discovery",
            backend="harness_cli:claude_code",
            input_tokens=0,
            output_tokens=0,
            estimated_cost=Decimal("0"),
            latency_ms=1,
            success=True,
            context_report={"usage_source": "cli_reported"},
        )
        assert usage.context_report == {"usage_source": "cli_reported"}

    def test_request_id_comes_from_active_request_context(self, workspace, monkeypatch):
        from ai_core import gateway as gateway_module

        monkeypatch.setattr(gateway_module, "get_request_id", lambda: "req-123")
        usage = gateway_module.record_external_usage(
            workspace=workspace,
            purpose="harness.discovery",
            backend="harness_cli:claude_code",
            input_tokens=0,
            output_tokens=0,
            estimated_cost=Decimal("0"),
            latency_ms=1,
            success=True,
        )
        assert usage.request_id == "req-123"

    def test_unauthenticated_actor_stores_no_user(self, workspace):
        from django.contrib.auth.models import AnonymousUser

        from ai_core.gateway import record_external_usage

        usage = record_external_usage(
            workspace=workspace,
            purpose="harness.discovery",
            backend="harness_cli:claude_code",
            input_tokens=0,
            output_tokens=0,
            estimated_cost=Decimal("0"),
            latency_ms=1,
            success=True,
            actor=AnonymousUser(),
        )
        assert usage.user is None
