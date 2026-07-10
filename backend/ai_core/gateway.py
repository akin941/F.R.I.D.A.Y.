"""InferenceGateway — the single door for every LLM call (ADR-003).

Guarantees:
- Exactly ONE ModelUsage row per gateway call — success, failure, timeout,
  retry-then-success, fallback, streaming completion and client disconnect
  included. Attempt details go into context_report["attempts"].
- Real API keys never touch the database, logs or error messages.
- Fully mockable: tests patch `ai_core.gateway.get_adapter`.

`complete(..., on_usage=callback)` invokes `callback(usage)` exactly once, right
after the (single) ModelUsage row is persisted, on every accounted path. Callers
must not raise from the callback and must never create a second ModelUsage row.
"""

import os
import time
from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings

from ai_core.adapters import AdapterError, get_adapter
from ai_core.exceptions import GatewayError, NoProviderAvailable
from ai_core.models import ModelProvider, ModelUsage
from core.context import get_request_id

_MTOK = Decimal(1_000_000)
_COST_PRECISION = Decimal("0.000001")


@dataclass
class GatewayResult:
    text: str
    usage: ModelUsage
    finish_reason: str = ""


@dataclass
class EmbeddingGatewayResult:
    vectors: list
    usage: ModelUsage


class InferenceGateway:
    backend = "in_process"

    def __init__(self):
        self.max_retries = getattr(settings, "AI_MAX_RETRIES", 2)
        self.backoff_seconds = getattr(settings, "AI_RETRY_BACKOFF_SECONDS", 0.5)
        self.connect_timeout = getattr(settings, "AI_TIMEOUT_CONNECT", 10)
        self.read_timeout = getattr(settings, "AI_TIMEOUT_READ", 60)

    # --- public API ---

    def complete(
        self,
        purpose,
        messages,
        *,
        actor=None,
        workspace=None,
        prompt_version=None,
        stream=False,
        params=None,
        context_report=None,
        on_usage=None,
    ):
        started = time.monotonic()
        merged_params = dict(prompt_version.model_params) if prompt_version else {}
        merged_params.update(params or {})
        base_report = dict(context_report or {})

        providers = self._select_providers(capability=ModelProvider.Capability.CHAT)
        if not providers:
            self._record(
                purpose=purpose,
                actor=actor,
                workspace=workspace,
                prompt_version=prompt_version,
                provider=None,
                result=None,
                success=False,
                error_type="no_provider",
                started=started,
                report=base_report,
                attempts=[],
                on_usage=on_usage,
            )
            raise NoProviderAvailable()

        if stream:
            return self._stream_generator(
                purpose,
                messages,
                providers,
                merged_params,
                base_report,
                actor,
                workspace,
                prompt_version,
                started,
                on_usage,
            )
        return self._complete_sync(
            purpose,
            messages,
            providers,
            merged_params,
            base_report,
            actor,
            workspace,
            prompt_version,
            started,
            on_usage,
        )

    def embed(
        self,
        purpose,
        texts,
        *,
        actor=None,
        workspace=None,
        prompt_version=None,
        context_report=None,
        on_usage=None,
    ):
        """Embed a batch of texts. Same retry/fallback/accounting contract as
        `complete()` (ADR-003: embeddings are a gateway call like any other,
        exactly one ModelUsage row). No streaming variant."""
        started = time.monotonic()
        base_report = dict(context_report or {})
        base_report["item_count"] = len(texts)

        providers = self._select_providers(capability=ModelProvider.Capability.EMBEDDING)
        if not providers:
            self._record(
                purpose=purpose,
                actor=actor,
                workspace=workspace,
                prompt_version=prompt_version,
                provider=None,
                result=None,
                success=False,
                error_type="no_provider",
                started=started,
                report=base_report,
                attempts=[],
                on_usage=on_usage,
            )
            raise NoProviderAvailable()

        attempts = []
        last_error = None
        last_provider = None
        for provider in providers:
            last_provider = provider
            adapter = self._adapter(provider)
            for attempt in range(1, self.max_retries + 2):
                try:
                    result = adapter.embed(texts, purpose=purpose)
                except AdapterError as exc:
                    attempts.append(self._attempt_entry(provider, attempt, exc))
                    last_error = exc
                    if not exc.retryable:
                        break
                    time.sleep(self.backoff_seconds * attempt)
                    continue
                except Exception:
                    exc = AdapterError("unknown", retryable=False)
                    attempts.append(self._attempt_entry(provider, attempt, exc))
                    last_error = exc
                    break
                attempts.append(self._attempt_entry(provider, attempt, None))
                usage = self._record(
                    purpose=purpose,
                    actor=actor,
                    workspace=workspace,
                    prompt_version=prompt_version,
                    provider=provider,
                    result=result,
                    success=True,
                    error_type="",
                    started=started,
                    report=base_report,
                    attempts=attempts,
                    on_usage=on_usage,
                )
                return EmbeddingGatewayResult(vectors=result.vectors, usage=usage)
        self._record(
            purpose=purpose,
            actor=actor,
            workspace=workspace,
            prompt_version=prompt_version,
            provider=last_provider,
            result=None,
            success=False,
            error_type=last_error.error_type if last_error else "unknown",
            started=started,
            report=base_report,
            attempts=attempts,
            on_usage=on_usage,
        )
        raise GatewayError(
            last_error.error_type if last_error else "unknown",
            retryable=last_error.retryable if last_error else False,
        )

    # --- provider selection ---

    def _select_providers(self, *, capability=ModelProvider.Capability.CHAT):
        """Active providers of the given capability, by priority; fake only
        behind the flag, real ones only when their key env var is actually
        set. Primary + one fallback."""
        selected = []
        for provider in ModelProvider.objects.filter(
            is_active=True, capability=capability
        ).order_by("priority", "name"):
            if provider.provider_type == ModelProvider.ProviderType.FAKE:
                if getattr(settings, "AI_FAKE_PROVIDER", False):
                    selected.append(provider)
            elif os.environ.get(provider.api_key_env_var or "", ""):
                selected.append(provider)
        return selected[:2]

    # --- non-streaming path ---

    def _complete_sync(
        self,
        purpose,
        messages,
        providers,
        params,
        base_report,
        actor,
        workspace,
        prompt_version,
        started,
        on_usage=None,
    ):
        attempts = []
        last_error = None
        last_provider = None
        for provider in providers:
            last_provider = provider
            adapter = self._adapter(provider)
            for attempt in range(1, self.max_retries + 2):
                try:
                    result = adapter.complete(messages, params, purpose=purpose)
                except AdapterError as exc:
                    attempts.append(self._attempt_entry(provider, attempt, exc))
                    last_error = exc
                    if not exc.retryable:
                        break
                    time.sleep(self.backoff_seconds * attempt)
                    continue
                except Exception:
                    exc = AdapterError("unknown", retryable=False)
                    attempts.append(self._attempt_entry(provider, attempt, exc))
                    last_error = exc
                    break
                attempts.append(self._attempt_entry(provider, attempt, None))
                usage = self._record(
                    purpose=purpose,
                    actor=actor,
                    workspace=workspace,
                    prompt_version=prompt_version,
                    provider=provider,
                    result=result,
                    success=True,
                    error_type="",
                    started=started,
                    report=base_report,
                    attempts=attempts,
                    on_usage=on_usage,
                )
                return GatewayResult(
                    text=result.text, usage=usage, finish_reason=result.finish_reason
                )
        self._record(
            purpose=purpose,
            actor=actor,
            workspace=workspace,
            prompt_version=prompt_version,
            provider=last_provider,
            result=None,
            success=False,
            error_type=last_error.error_type if last_error else "unknown",
            started=started,
            report=base_report,
            attempts=attempts,
            on_usage=on_usage,
        )
        raise GatewayError(
            last_error.error_type if last_error else "unknown",
            retryable=last_error.retryable if last_error else False,
        )

    # --- streaming path ---

    def _stream_generator(
        self,
        purpose,
        messages,
        providers,
        params,
        base_report,
        actor,
        workspace,
        prompt_version,
        started,
        on_usage=None,
    ):
        attempts = []
        provider_used = None
        gen = None
        first_chunk = None
        empty_result = None
        last_error = None
        last_provider = None

        # Acquire the first chunk with retry/fallback; once streaming has
        # begun, errors are terminal (no mid-stream retry).
        for provider in providers:
            last_provider = provider
            adapter = self._adapter(provider)
            for attempt in range(1, self.max_retries + 2):
                try:
                    candidate = adapter.stream(messages, params, purpose=purpose)
                    first_chunk = next(candidate)
                except StopIteration as stop:  # empty but successful stream
                    empty_result = stop.value
                    attempts.append(self._attempt_entry(provider, attempt, None))
                    provider_used = provider
                    break
                except AdapterError as exc:
                    attempts.append(self._attempt_entry(provider, attempt, exc))
                    last_error = exc
                    if not exc.retryable:
                        break
                    time.sleep(self.backoff_seconds * attempt)
                    continue
                except Exception:
                    exc = AdapterError("unknown", retryable=False)
                    attempts.append(self._attempt_entry(provider, attempt, exc))
                    last_error = exc
                    break
                attempts.append(self._attempt_entry(provider, attempt, None))
                provider_used = provider
                gen = candidate
                break
            if provider_used:
                break

        if provider_used is None:
            self._record(
                purpose=purpose,
                actor=actor,
                workspace=workspace,
                prompt_version=prompt_version,
                provider=last_provider,
                result=None,
                success=False,
                error_type=last_error.error_type if last_error else "unknown",
                started=started,
                report=base_report,
                attempts=attempts,
                on_usage=on_usage,
            )
            raise GatewayError(
                last_error.error_type if last_error else "unknown",
                retryable=last_error.retryable if last_error else False,
            )

        if gen is None:  # empty stream — still a success, still accounted
            self._record(
                purpose=purpose,
                actor=actor,
                workspace=workspace,
                prompt_version=prompt_version,
                provider=provider_used,
                result=empty_result,
                success=True,
                error_type="",
                started=started,
                report=base_report,
                attempts=attempts,
                on_usage=on_usage,
            )
            return

        streamed_chars = len(first_chunk or "")
        error_type = None
        result = None
        try:
            yield first_chunk
            while True:
                try:
                    chunk = next(gen)
                except StopIteration as stop:
                    result = stop.value
                    break
                except AdapterError as exc:
                    error_type = exc.error_type
                    # Once streaming has begun there is no mid-stream retry
                    # (the docstring's own rule), but the adapter's
                    # classification still matters to a caller deciding
                    # whether to retry the WHOLE call later (e.g. a
                    # background extraction job).
                    raise GatewayError(exc.error_type, retryable=exc.retryable) from exc
                streamed_chars += len(chunk)
                yield chunk
        except GeneratorExit:
            error_type = "client_disconnect"
            raise
        finally:
            report = dict(base_report)
            if result is None:
                # Interrupted stream: estimate what we managed to send.
                report["usage_estimated"] = True
            self._record(
                purpose=purpose,
                actor=actor,
                workspace=workspace,
                prompt_version=prompt_version,
                provider=provider_used,
                result=result,
                success=error_type is None and result is not None,
                error_type=error_type or ("" if result is not None else "unknown"),
                started=started,
                report=report,
                attempts=attempts,
                fallback_output_chars=streamed_chars,
                on_usage=on_usage,
            )

    # --- accounting ---

    def _adapter(self, provider):
        return get_adapter(
            provider,
            connect_timeout=self.connect_timeout,
            read_timeout=self.read_timeout,
        )

    @staticmethod
    def _attempt_entry(provider, attempt, exc):
        return {
            "provider": provider.name,
            "attempt": attempt,
            "error_type": exc.error_type if exc else None,
        }

    def _record(
        self,
        *,
        purpose,
        actor,
        workspace,
        prompt_version,
        provider,
        result,
        success,
        error_type,
        started,
        report,
        attempts,
        fallback_output_chars=0,
        on_usage=None,
    ):
        latency_ms = int((time.monotonic() - started) * 1000)
        input_tokens = result.input_tokens if result else 0
        output_tokens = result.output_tokens if result else 0
        if result is None and fallback_output_chars:
            output_tokens = max(1, fallback_output_chars // 4)
        context_report = dict(report or {})
        context_report["attempts"] = attempts
        if result is not None and result.usage_estimated:
            context_report["usage_estimated"] = True

        user = actor if getattr(actor, "is_authenticated", False) else None
        usage = ModelUsage.objects.create(
            workspace=workspace,
            user=user,
            provider=provider,
            prompt_version=prompt_version,
            purpose=purpose,
            request_id=get_request_id() or "",
            backend=self.backend,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            success=success,
            error_type=error_type or "",
            estimated_cost=self._estimate_cost(provider, input_tokens, output_tokens),
            context_report=context_report,
        )
        if on_usage is not None:
            on_usage(usage)
        return usage

    @staticmethod
    def _estimate_cost(provider, input_tokens, output_tokens):
        if provider is None:
            return Decimal("0")
        cost = (
            Decimal(input_tokens) * provider.input_price_per_mtok
            + Decimal(output_tokens) * provider.output_price_per_mtok
        ) / _MTOK
        return cost.quantize(_COST_PRECISION)
