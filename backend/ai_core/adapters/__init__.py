from ai_core.adapters.anthropic import AnthropicAdapter
from ai_core.adapters.base import AdapterError, AdapterResult, BaseAdapter, EmbedAdapterResult
from ai_core.adapters.fake import FakeAdapter
from ai_core.adapters.openai_compat import OpenAICompatibleAdapter
from ai_core.models import ModelProvider

_ADAPTERS = {
    ModelProvider.ProviderType.ANTHROPIC: AnthropicAdapter,
    ModelProvider.ProviderType.OPENAI_COMPATIBLE: OpenAICompatibleAdapter,
    ModelProvider.ProviderType.FAKE: FakeAdapter,
}


def get_adapter(provider, *, connect_timeout=10, read_timeout=60):
    adapter_cls = _ADAPTERS[provider.provider_type]
    return adapter_cls(provider, connect_timeout=connect_timeout, read_timeout=read_timeout)


__all__ = [
    "AdapterError",
    "AdapterResult",
    "BaseAdapter",
    "EmbedAdapterResult",
    "get_adapter",
]
