"""OpenAI-compatible adapter (OpenAI, DeepSeek, Ollama, OpenRouter, and the
local speech server -- speaches or the `deploy/voice/` sidecar, Phase 6 D4).
"""

import json
from decimal import Decimal, InvalidOperation

import httpx
import openai
from django.conf import settings
from openai import OpenAI

from ai_core.adapters.base import (
    AdapterError,
    AdapterResult,
    BaseAdapter,
    EmbedAdapterResult,
    SynthesizeAdapterResult,
    TranscribeAdapterResult,
)
from ai_core.audio import (
    InvalidAudio,
    normalize_tts_wav,
    parse_pcm_content_type,
    parse_stt_input,
    pcm_to_wav,
)

# TTS response formats this adapter accepts from a provider. `wav` is the
# local speech server's (Piper via speaches/sidecar); `pcm` is OpenRouter's
# default and, with `mp3`, its only option (spike A1) -- wrapped into WAV
# here so callers only ever see one checked `audio/wav` shape (D3).
_TTS_FORMATS = {"wav", "pcm"}
# OpenRouter reports the PCM rate in the Content-Type
# (`audio/pcm;rate=24000;channels=1`); a bare `audio/pcm` falls back to
# 24 kHz mono, the common default (or the route's `pcm_sample_rate`).
_DEFAULT_PCM_RATE = 24_000


def _wrap_error(exc):
    if isinstance(exc, openai.AuthenticationError):
        return AdapterError("auth_error", retryable=False, safe_message="authentication failed")
    if isinstance(exc, openai.RateLimitError):
        return AdapterError("rate_limit", retryable=True, safe_message="rate limited")
    if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError)):
        return AdapterError("timeout", retryable=True, safe_message="connection/timeout error")
    if isinstance(exc, openai.APIStatusError):
        retryable = exc.status_code >= 500
        return AdapterError(
            "provider_error",
            retryable=retryable,
            safe_message=f"provider returned status {exc.status_code}",
        )
    return AdapterError("unknown", retryable=False, safe_message=type(exc).__name__)


def _decimal_or_none(value):
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        cost = Decimal(str(value))
    except InvalidOperation:
        return None
    return cost if cost.is_finite() and cost >= 0 else None


def _int_or_none(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _invalid_audio(exc):
    return AdapterError(
        "invalid_audio", retryable=False, safe_message=f"invalid audio: {exc.reason}"
    )


class OpenAICompatibleAdapter(BaseAdapter):
    # Test seam (Phase 6 branch 1): an `httpx` transport -- tests set an
    # `httpx.MockTransport` here so the real `openai` client builds real
    # requests that never leave the process. None in production.
    http_transport = None

    def _client(self):
        kwargs = {}
        if self.http_transport is not None:
            kwargs["http_client"] = httpx.Client(transport=self.http_transport)
        return OpenAI(
            api_key=self._api_key(),
            base_url=self.provider.base_url or None,
            timeout=self.read_timeout,
            max_retries=0,  # the gateway owns retry policy
            **kwargs,
        )

    def _request_kwargs(self, messages, params):
        kwargs = {"model": self.provider.model_id, "messages": messages}
        for key in ("max_tokens", "temperature"):
            if key in params:
                kwargs[key] = params[key]
        return kwargs

    def complete(self, messages, params, *, purpose=""):
        try:
            response = self._client().chat.completions.create(
                **self._request_kwargs(messages, params)
            )
        except Exception as exc:  # noqa: BLE001 — mapped to safe errors
            raise _wrap_error(exc) from exc
        choice = response.choices[0]
        usage = response.usage
        return AdapterResult(
            text=choice.message.content or "",
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            finish_reason=choice.finish_reason or "",
            raw_usage={
                "prompt_tokens": getattr(usage, "prompt_tokens", 0),
                "completion_tokens": getattr(usage, "completion_tokens", 0),
            },
        )

    def stream(self, messages, params, *, purpose=""):
        text_parts = []
        usage = None
        finish_reason = ""
        try:
            response = self._client().chat.completions.create(
                stream=True,
                stream_options={"include_usage": True},
                **self._request_kwargs(messages, params),
            )
            for chunk in response:
                if chunk.usage is not None:
                    usage = chunk.usage
                if chunk.choices:
                    if chunk.choices[0].finish_reason:
                        finish_reason = chunk.choices[0].finish_reason
                    delta = chunk.choices[0].delta.content
                    if delta:
                        text_parts.append(delta)
                        yield delta
        except AdapterError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _wrap_error(exc) from exc
        text = "".join(text_parts)
        if usage is not None:
            return AdapterResult(
                text=text,
                input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                output_tokens=getattr(usage, "completion_tokens", 0) or 0,
                finish_reason=finish_reason,
                raw_usage={"prompt_tokens": usage.prompt_tokens},
            )
        # Endpoint did not report usage — estimate, but never skip accounting.
        return AdapterResult(
            text=text,
            input_tokens=sum(self.estimate_tokens(str(m.get("content", ""))) for m in messages),
            output_tokens=self.estimate_tokens(text),
            finish_reason=finish_reason,
            usage_estimated=True,
        )

    def embed(self, texts, *, purpose=""):
        try:
            response = self._client().embeddings.create(model=self.provider.model_id, input=texts)
        except Exception as exc:  # noqa: BLE001 — mapped to safe errors
            raise _wrap_error(exc) from exc
        vectors = [item.embedding for item in response.data]
        usage = response.usage
        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        if not input_tokens:
            input_tokens = sum(self.estimate_tokens(t) for t in texts)
            return EmbedAdapterResult(
                vectors=vectors, input_tokens=input_tokens, usage_estimated=True
            )
        return EmbedAdapterResult(
            vectors=vectors,
            input_tokens=input_tokens,
            raw_usage={"prompt_tokens": input_tokens},
        )

    # --- speech (Phase 6 D3/D4, ADR-011) ---

    def transcribe(self, audio, *, content_type="", params=None, purpose=""):
        """`POST {base_url}/audio/transcriptions` (multipart). The input must
        be D3's 16 kHz mono PCM16 WAV within `VOICE_MAX_UTTERANCE_SECONDS`;
        `audio_seconds` comes from that validated header, never from the
        provider's response. A provider-reported `usage.cost` (OpenRouter)
        is passed through as `reported_cost`."""
        params = params or {}
        try:
            info = parse_stt_input(audio, max_seconds=settings.VOICE_MAX_UTTERANCE_SECONDS)
        except InvalidAudio as exc:
            raise _invalid_audio(exc) from exc
        kwargs = {
            "model": self.provider.model_id,
            "file": ("utterance.wav", bytes(audio), "audio/wav"),
            "response_format": "json",
        }
        if params.get("language"):
            kwargs["language"] = params["language"]
        try:
            raw = self._client().audio.transcriptions.with_raw_response.create(**kwargs)
            body = raw.http_response.json()
        except AdapterError:
            raise
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AdapterError(
                "provider_error", retryable=False, safe_message="transcription response not JSON"
            ) from exc
        except Exception as exc:  # noqa: BLE001 — mapped to safe errors
            raise _wrap_error(exc) from exc
        text = body.get("text") if isinstance(body, dict) else None
        if not isinstance(text, str):
            raise AdapterError(
                "provider_error", retryable=False, safe_message="transcription has no text"
            )
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        output_tokens = _int_or_none(usage.get("output_tokens"))
        return TranscribeAdapterResult(
            text=text,
            audio_seconds=info.seconds,
            input_tokens=_int_or_none(usage.get("input_tokens")) or 0,
            output_tokens=(
                output_tokens if output_tokens is not None else self.estimate_tokens(text)
            ),
            # Numbers only: never the transcript or any provider string.
            raw_usage={
                k: v
                for k, v in usage.items()
                if isinstance(v, int | float) and not isinstance(v, bool)
            },
            usage_estimated=output_tokens is None,
            reported_cost=_decimal_or_none(usage.get("cost")),
            generation_id=str(raw.headers.get("x-generation-id") or body.get("id") or "")[:100],
            language=str(body.get("language") or params.get("language") or "")[:16],
        )

    def synthesize(self, text, *, params=None, purpose=""):
        """`POST {base_url}/audio/speech`. `params["voice"]` is required;
        `params["response_format"]` is `wav` (default, local server) or
        `pcm` (OpenRouter). The bytes returned are always a checked PCM16
        WAV within `VOICE_TTS_MAX_SECONDS` -- non-WAV, oversized or
        malformed output is `invalid_audio`, never passed through."""
        params = params or {}
        voice = params.get("voice") or ""
        response_format = params.get("response_format") or "wav"
        if not voice or response_format not in _TTS_FORMATS:
            raise AdapterError(
                "invalid_request", retryable=False, safe_message="TTS voice/format not configured"
            )
        try:
            raw = self._client().audio.speech.with_raw_response.create(
                model=self.provider.model_id,
                voice=voice,
                input=text,
                response_format=response_format,
            )
            content = raw.http_response.content
        except AdapterError:
            raise
        except Exception as exc:  # noqa: BLE001 — mapped to safe errors
            raise _wrap_error(exc) from exc
        content_type = (raw.headers.get("content-type") or "").lower()
        try:
            if response_format == "pcm" or content_type.startswith(("audio/pcm", "audio/l16")):
                rate, channels = parse_pcm_content_type(
                    content_type,
                    default_rate=params.get("pcm_sample_rate") or _DEFAULT_PCM_RATE,
                )
                content = pcm_to_wav(content, sample_rate=rate, channels=channels)
            wav, info = normalize_tts_wav(content, max_seconds=settings.VOICE_TTS_MAX_SECONDS)
        except InvalidAudio as exc:
            raise _invalid_audio(exc) from exc
        return SynthesizeAdapterResult(
            audio=wav,
            content_type="audio/wav",
            audio_seconds=info.seconds,
            input_tokens=self.estimate_tokens(text),
            usage_estimated=True,
            generation_id=str(raw.headers.get("x-generation-id") or "")[:100],
        )

    def generation_cost(self, generation_id):
        """OpenRouter's generation lookup, `GET {base_url}/generation?id=`:
        the final `total_cost` (USD) of an earlier call, or None while it is
        not yet available (404) or absent. Used only by
        `ai_core.tasks.correct_usage_cost` when a response carried no cost
        (spike A1: `/audio/speech` returns raw bytes, no usage)."""
        url = f"{(self.provider.base_url or '').rstrip('/')}/generation"
        api_key = self._api_key()
        try:
            with httpx.Client(
                transport=self.http_transport,
                timeout=httpx.Timeout(self.read_timeout, connect=self.connect_timeout),
            ) as client:
                response = client.get(
                    url,
                    params={"id": generation_id},
                    headers={"Authorization": f"Bearer {api_key}"},
                )
        except httpx.HTTPError as exc:
            raise AdapterError(
                "timeout", retryable=True, safe_message="connection/timeout error"
            ) from exc
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise AdapterError(
                "provider_error",
                retryable=response.status_code >= 500 or response.status_code == 429,
                safe_message=f"provider returned status {response.status_code}",
            )
        try:
            data = response.json().get("data") or {}
        except (ValueError, AttributeError):
            return None
        return _decimal_or_none(data.get("total_cost")) if isinstance(data, dict) else None
