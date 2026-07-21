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


@dataclass
class TranscribeAdapterResult:
    """Speech-to-text result (MVP 12). `text` is the transcript.

    STT is priced by audio duration, not tokens, so `audio_seconds` is the
    real cost signal — the gateway records it in `context_report`. The
    token fields exist only so the gateway's shared `_record()` accounting
    reads uniformly across every result type; for STT `input_tokens` stays 0
    (audio in, not text) and `output_tokens` is a best-effort estimate of
    the transcript, never a billed quantity.
    """

    text: str
    audio_seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    raw_usage: dict = field(default_factory=dict)
    usage_estimated: bool = False


@dataclass
class SynthesizeAdapterResult:
    """Text-to-speech result (MVP 12). `audio` is the synthesized bytes and
    `content_type` its MIME type (e.g. "audio/wav").

    TTS is priced by input characters/duration, not output tokens. As with
    STT, the token fields are only for `_record()` uniformity: `input_tokens`
    is a best-effort estimate of the source text, `output_tokens` stays 0
    (audio out, not text).
    """

    audio: bytes
    content_type: str = "audio/wav"
    audio_seconds: float = 0.0
    input_tokens: int = 0
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

    def transcribe(self, audio, *, content_type="", purpose=""):
        """Return a TranscribeAdapterResult (speech-to-text). `audio` is raw
        bytes. Providers with no STT endpoint inherit this default (MVP 12)."""
        raise AdapterError(
            "not_supported", retryable=False, safe_message="provider does not support transcription"
        )

    def synthesize(self, text, *, params=None, purpose=""):
        """Return a SynthesizeAdapterResult (text-to-speech). Providers with
        no TTS endpoint inherit this default (MVP 12)."""
        raise AdapterError(
            "not_supported", retryable=False, safe_message="provider does not support synthesis"
        )

    @staticmethod
    def estimate_tokens(text):
        """Crude fallback when the provider reports no usage (~4 chars/token)."""
        return max(1, len(text) // 4)
