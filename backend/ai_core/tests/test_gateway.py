from decimal import Decimal
from unittest import mock

import pytest

from accounts.services import register_user
from ai_core.adapters.base import AdapterError, AdapterResult, EmbedAdapterResult
from ai_core.adapters.fake import FAKE_RESPONSE
from ai_core.exceptions import GatewayError, NoProviderAvailable
from ai_core.gateway import InferenceGateway
from ai_core.models import ModelProvider, ModelUsage

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
