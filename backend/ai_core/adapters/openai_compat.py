"""OpenAI-compatible adapter (OpenAI, DeepSeek, and similar endpoints)."""

import openai
from openai import OpenAI

from ai_core.adapters.base import AdapterError, AdapterResult, BaseAdapter, EmbedAdapterResult


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


class OpenAICompatibleAdapter(BaseAdapter):
    def _client(self):
        return OpenAI(
            api_key=self._api_key(),
            base_url=self.provider.base_url or None,
            timeout=self.read_timeout,
            max_retries=0,  # the gateway owns retry policy
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
