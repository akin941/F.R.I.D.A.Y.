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

`record_external_usage()` (module-level, not a method) is the single
exception to "every ModelUsage row comes from complete()/embed()": an
external CLI coding agent (`harness`, Phase 2, ADR-008 decision 1) runs
out of process, so there is no provider adapter for the gateway's own
retry/fallback machinery to drive. ADR-008 is explicit that this usage
"is reported through the InferenceGateway as ModelUsage," not written
around it — this function is that reporting path, and it is the *only*
place outside `complete()`/`embed()`'s own internal `_finalize` that may
construct a `ModelUsage` row. `harness` must never import `ModelUsage`
directly.
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


def record_external_usage(
    *,
    workspace,
    purpose,
    backend,
    input_tokens,
    output_tokens,
    estimated_cost,
    latency_ms,
    success,
    actor=None,
    error_type="",
    context_report=None,
):
    """Record one `ModelUsage` row for a call that ran entirely out of
    process (no `ModelProvider`, no adapter, no gateway retry/fallback
    logic — the caller already made and finished the call itself).

    Unlike `complete()`/`embed()`'s own `_finalize`, there is no
    `ModelProvider` fronting this call (`provider=None`, matching
    `ModelUsage.provider`'s nullable "no provider available" contract,
    `ai_core/models.py:130`) and no adapter-estimated cost to compute —
    `estimated_cost` is the caller's own already-known figure (e.g. an
    external CLI's own reported cost), taken as given, not derived from a
    `ModelProvider`'s per-token pricing. `request_id` is read from the
    active request the same way `_finalize` already does, so external and
    in-process rows correlate the same way; `routing_reason` is always
    empty (there is no provider to have routed to)."""
    user = actor if getattr(actor, "is_authenticated", False) else None
    return ModelUsage.objects.create(
        workspace=workspace,
        user=user,
        provider=None,
        prompt_version=None,
        purpose=purpose,
        request_id=get_request_id() or "",
        backend=backend,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        success=success,
        error_type=error_type or "",
        routing_reason="",
        estimated_cost=estimated_cost,
        context_report=dict(context_report or {}),
    )


@dataclass
class GatewayResult:
    text: str
    usage: ModelUsage
    finish_reason: str = ""


@dataclass
class EmbeddingGatewayResult:
    vectors: list
    usage: ModelUsage


@dataclass
class TranscribeGatewayResult:
    text: str
    usage: ModelUsage
    audio_seconds: float = 0.0


@dataclass
class SynthesizeGatewayResult:
    audio: bytes
    usage: ModelUsage
    content_type: str = "audio/wav"
    audio_seconds: float = 0.0


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
        personal_data=False,
    ):
        """One chat completion (or, with `stream=True`, a generator of
        chunks). Provider selection -- including the `personal_data` filter
        -- runs eagerly here, before any generator exists, so the streaming
        path can never reach a provider the synchronous path would refuse.

        `personal_data=True` (D5, ADR-010 decision 5): the messages carry
        personal-class data, so only providers with
        `personal_data_allowed=True` are eligible -- primary AND fallback.
        With none, the call fails closed (`NoProviderAvailable`, one
        `ModelUsage` row, `routing_reason="personal_data_required"`); there
        is never a silent fallback to a cloud provider."""
        started = time.monotonic()
        merged_params = dict(prompt_version.model_params) if prompt_version else {}
        merged_params.update(params or {})
        base_report = self._base_report(context_report, personal_data)

        providers = self._select_providers(
            capability=ModelProvider.Capability.CHAT,
            purpose=purpose,
            personal_data=personal_data,
        )
        if not providers:
            raise self._no_provider_error(
                purpose=purpose,
                actor=actor,
                workspace=workspace,
                prompt_version=prompt_version,
                started=started,
                report=base_report,
                personal_data=personal_data,
                on_usage=on_usage,
            )

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
        personal_data=False,
    ):
        """Embed a batch of texts. Same retry/fallback/accounting contract as
        `complete()` (ADR-003: embeddings are a gateway call like any other,
        exactly one ModelUsage row) -- including `personal_data`'s
        allowed-providers-only, fail-closed selection. No streaming
        variant."""
        started = time.monotonic()
        base_report = self._base_report(context_report, personal_data)
        base_report["item_count"] = len(texts)

        providers = self._select_providers(
            capability=ModelProvider.Capability.EMBEDDING,
            purpose=purpose,
            personal_data=personal_data,
        )
        if not providers:
            raise self._no_provider_error(
                purpose=purpose,
                actor=actor,
                workspace=workspace,
                prompt_version=prompt_version,
                started=started,
                report=base_report,
                personal_data=personal_data,
                on_usage=on_usage,
            )

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

    def transcribe(
        self,
        purpose,
        audio,
        *,
        content_type="",
        actor=None,
        workspace=None,
        prompt_version=None,
        context_report=None,
        on_usage=None,
        personal_data=False,
    ):
        """Speech-to-text (MVP 12). Same single-door/one-ModelUsage-row/
        retry/fallback/`personal_data` contract as `embed()` (ADR-003) — STT
        is a gateway call like any other. `audio` is raw bytes; returns the
        transcript. No streaming variant."""
        started = time.monotonic()
        base_report = self._base_report(context_report, personal_data)

        result, usage = self._run_audio(
            purpose=purpose,
            capability=ModelProvider.Capability.STT,
            adapter_call=lambda adapter: adapter.transcribe(
                audio, content_type=content_type, purpose=purpose
            ),
            actor=actor,
            workspace=workspace,
            prompt_version=prompt_version,
            base_report=base_report,
            started=started,
            on_usage=on_usage,
            personal_data=personal_data,
        )
        return TranscribeGatewayResult(
            text=result.text, usage=usage, audio_seconds=result.audio_seconds
        )

    def synthesize(
        self,
        purpose,
        text,
        *,
        params=None,
        actor=None,
        workspace=None,
        prompt_version=None,
        context_report=None,
        on_usage=None,
        personal_data=False,
    ):
        """Text-to-speech (MVP 12). Same contract as `transcribe()`. `text`
        in, synthesized audio bytes out. No streaming variant."""
        started = time.monotonic()
        base_report = self._base_report(context_report, personal_data)

        result, usage = self._run_audio(
            purpose=purpose,
            capability=ModelProvider.Capability.TTS,
            adapter_call=lambda adapter: adapter.synthesize(text, params=params, purpose=purpose),
            actor=actor,
            workspace=workspace,
            prompt_version=prompt_version,
            base_report=base_report,
            started=started,
            on_usage=on_usage,
            personal_data=personal_data,
        )
        return SynthesizeGatewayResult(
            audio=result.audio,
            usage=usage,
            content_type=result.content_type,
            audio_seconds=result.audio_seconds,
        )

    def _run_audio(
        self,
        *,
        purpose,
        capability,
        adapter_call,
        actor,
        workspace,
        prompt_version,
        base_report,
        started,
        on_usage,
        personal_data=False,
    ):
        """Shared STT/TTS execution: provider selection + retry/fallback +
        exactly one ModelUsage row, mirroring `embed()`'s non-streaming
        shape. `adapter_call(adapter)` performs the one provider call and
        returns a Transcribe/Synthesize AdapterResult carrying `audio_seconds`
        (recorded in context_report — the real STT/TTS cost signal, since
        this class of provider is priced by duration, not tokens)."""
        providers = self._select_providers(
            capability=capability, purpose=purpose, personal_data=personal_data
        )
        if not providers:
            raise self._no_provider_error(
                purpose=purpose,
                actor=actor,
                workspace=workspace,
                prompt_version=prompt_version,
                started=started,
                report=base_report,
                personal_data=personal_data,
                on_usage=on_usage,
            )

        attempts = []
        last_error = None
        last_provider = None
        for provider in providers:
            last_provider = provider
            adapter = self._adapter(provider)
            for attempt in range(1, self.max_retries + 2):
                try:
                    result = adapter_call(adapter)
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
                report = dict(base_report)
                report["audio_seconds"] = getattr(result, "audio_seconds", 0.0)
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
                    report=report,
                    attempts=attempts,
                    on_usage=on_usage,
                )
                return result, usage
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

    def _select_providers(
        self, *, capability=ModelProvider.Capability.CHAT, purpose="", personal_data=False
    ):
        """Active providers of the given capability, by priority; fake only
        behind the flag, real ones only when their key env var is actually
        set. Primary + one fallback.

        `personal_data=True` (D5) drops every provider without
        `personal_data_allowed` BEFORE tier ordering and the primary +
        fallback cut, so neither the primary nor the fallback can be a
        non-allowed provider; an empty result is the caller's fail-closed
        `NoProviderAvailable`. The flag is read from the rows fetched here,
        on every call -- a provider flipped off takes effect on the very
        next call, no cache.

        Tier routing skeleton (phase 0, §J): if `purpose` maps to a tier in
        `settings.AI_PURPOSE_TIERS` and that tier has eligible providers
        (`ModelTier` rows), those are ordered first; everything else keeps
        priority order behind them. With no tier configuration the result
        is byte-for-byte the old priority order. Each selected provider is
        annotated with an in-memory `_routing_reason`, persisted onto its
        `ModelUsage` row by `_record` — the single usage writer."""
        eligible = []
        for provider in ModelProvider.objects.filter(
            is_active=True, capability=capability
        ).order_by("priority", "name"):
            if provider.provider_type == ModelProvider.ProviderType.FAKE:
                if getattr(settings, "AI_FAKE_PROVIDER", False):
                    eligible.append(provider)
            elif os.environ.get(provider.api_key_env_var or "", ""):
                eligible.append(provider)
        if personal_data:
            eligible = [p for p in eligible if p.personal_data_allowed]

        tier_name = (getattr(settings, "AI_PURPOSE_TIERS", None) or {}).get(purpose, "")
        primary_reason = "priority"
        if tier_name:
            from ai_core.models import ModelTier

            tier_provider_ids = set(
                ModelTier.objects.filter(tier=tier_name).values_list("provider_id", flat=True)
            )
            in_tier = [p for p in eligible if p.id in tier_provider_ids]
            if in_tier:
                eligible = in_tier + [p for p in eligible if p.id not in tier_provider_ids]
                primary_reason = f"tier:{tier_name}"

        selected = eligible[:2]
        for position, provider in enumerate(selected):
            if len(selected) == 1:
                provider._routing_reason = "only_provider"
            elif position == 0:
                provider._routing_reason = primary_reason
            else:
                provider._routing_reason = f"fallback:{position}"
        return selected

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

    @staticmethod
    def _base_report(context_report, personal_data):
        report = dict(context_report or {})
        if personal_data:
            # The row explains why only allowed providers were eligible.
            report["personal_data"] = True
        return report

    def _no_provider_error(
        self,
        *,
        purpose,
        actor,
        workspace,
        prompt_version,
        started,
        report,
        personal_data,
        on_usage,
    ):
        """The one no-provider exit of every entry point: records exactly one
        `ModelUsage` row (`error_type="no_provider"`) and returns the
        `NoProviderAvailable` the caller raises. With `personal_data` the row
        says why the eligible set was empty:
        `routing_reason="personal_data_required"`."""
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
            report=report,
            attempts=[],
            on_usage=on_usage,
            routing_reason="personal_data_required" if personal_data else "",
        )
        return NoProviderAvailable()

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
        routing_reason="",
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
            routing_reason=(
                getattr(provider, "_routing_reason", "") if provider else routing_reason
            ),
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
