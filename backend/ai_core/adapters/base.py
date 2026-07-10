"""Adapter contract for LLM providers.

Adapters translate between the gateway's provider-agnostic interface and each
SDK. They never write ModelUsage (the gateway owns accounting) and never let
secrets leak into exception messages.
"""

import os
from dataclasses import dataclass, field


@dataclass
class AdapterResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str = ""
    raw_usage: dict = field(default_factory=dict)
    usage_estimated: bool = False


@dataclass
class EmbedAdapterResult:
    vectors: list
    input_tokens: int = 0
    # Embeddings have no output tokens; kept so the gateway's shared
    # accounting code (`_record`) can read it uniformly across both result
    # types.
    output_tokens: int = 0
    raw_usage: dict = field(default_factory=dict)
    usage_estimated: bool = False


class AdapterError(Exception):
    """Provider failure with a secret-safe classification.

    `safe_message` must never contain API keys or raw provider payloads.
    """

    def __init__(self, error_type, *, retryable, safe_message=""):
        self.error_type = error_type
        self.retryable = retryable
        super().__init__(safe_message or error_type)


class BaseAdapter:
    def __init__(self, provider, *, connect_timeout=10, read_timeout=60):
        self.provider = provider
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout

    def _api_key(self):
        key = os.environ.get(self.provider.api_key_env_var or "", "")
        if not key:
            raise AdapterError(
                "auth_error",
                retryable=False,
                safe_message=f"env var {self.provider.api_key_env_var!r} is not set",
            )
        return key

    def complete(self, messages, params, *, purpose=""):
        """Return an AdapterResult.

        `purpose` mirrors the gateway call's `purpose` (e.g. "chat",
        "memory.extraction"). Real provider adapters ignore it — a purpose
        does not change how a request is sent to the provider; the fake
        adapter branches on it to return purpose-appropriate deterministic
        fixtures for tests.
        """
        raise NotImplementedError

    def stream(self, messages, params, *, purpose=""):
        """Generator yielding text chunks; returns the final AdapterResult."""
        raise NotImplementedError

    def embed(self, texts, *, purpose=""):
        """Return an EmbedAdapterResult. Providers with no embeddings
        endpoint (e.g. Anthropic) inherit this default."""
        raise AdapterError(
            "not_supported", retryable=False, safe_message="provider does not support embeddings"
        )

    @staticmethod
    def estimate_tokens(text):
        """Crude fallback when the provider reports no usage (~4 chars/token)."""
        return max(1, len(text) // 4)
