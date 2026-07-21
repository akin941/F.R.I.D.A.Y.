"""Anthropic adapter — the only place (besides tests) that imports the SDK."""

import anthropic

from ai_core.adapters.base import AdapterError, AdapterResult, BaseAdapter

DEFAULT_MAX_TOKENS = 1024


def _wrap_error(exc):
    """Map SDK exceptions to secret-safe AdapterErrors."""
    if isinstance(exc, anthropic.AuthenticationError):
        return AdapterError("auth_error", retryable=False, safe_message="authentication failed")
    if isinstance(exc, anthropic.RateLimitError):
        return AdapterError("rate_limit", retryable=True, safe_message="rate limited")
    if isinstance(exc, (anthropic.APITimeoutError, anthropic.APIConnectionError)):
        return AdapterError("timeout", retryable=True, safe_message="connection/timeout error")
    if isinstance(exc, anthropic.APIStatusError):
        retryable = exc.status_code >= 500
        return AdapterError(
            "provider_error",
            retryable=retryable,
            safe_message=f"provider returned status {exc.status_code}",
        )
    return AdapterError("unknown", retryable=False, safe_message=type(exc).__name__)


class AnthropicAdapter(BaseAdapter):
    def _client(self):
        return anthropic.Anthropic(
            api_key=self._api_key(),
            base_url=self.provider.base_url or None,
            timeout=self.read_timeout,
            max_retries=0,  # the gateway owns retry policy
        )

    def _split_messages(self, messages):
        system_parts = [m["content"] for m in messages if m.get("role") == "system"]
        rest = [m for m in messages if m.get("role") != "system"]
        return "\n\n".join(system_parts), rest

    def _request_kwargs(self, messages, params):
        system, rest = self._split_messages(messages)
        kwargs = {
            "model": self.provider.model_id,
            "messages": rest,
            "max_tokens": int(params.get("max_tokens", DEFAULT_MAX_TOKENS)),
        }
        if system:
            kwargs["system"] = system
        if "temperature" in params:
            kwargs["temperature"] = params["temperature"]
        return kwargs

    def complete(self, messages, params, *, purpose=""):
        try:
            response = self._client().messages.create(**self._request_kwargs(messages, params))
        except Exception as exc:  # noqa: BLE001 — mapped to safe errors
            raise _wrap_error(exc) from exc
        text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
        return AdapterResult(
            text=text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            finish_reason=response.stop_reason or "",
            raw_usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        )

    def stream(self, messages, params, *, purpose=""):
        text_parts = []
        try:
            with self._client().messages.stream(**self._request_kwargs(messages, params)) as stream:
                for chunk in stream.text_stream:
                    text_parts.append(chunk)
                    yield chunk
                final = stream.get_final_message()
        except AdapterError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _wrap_error(exc) from exc
        return AdapterResult(
            text="".join(text_parts),
            input_tokens=final.usage.input_tokens,
            output_tokens=final.usage.output_tokens,
            finish_reason=final.stop_reason or "",
            raw_usage={
                "input_tokens": final.usage.input_tokens,
                "output_tokens": final.usage.output_tokens,
            },
        )
