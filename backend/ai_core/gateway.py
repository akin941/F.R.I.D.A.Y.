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

import logging
import os
import time
from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings
from django.db import transaction

from ai_core import budgets, compression
from ai_core.adapters import AdapterError, get_adapter
from ai_core.audio import wav_seconds_or_zero
from ai_core.exceptions import BudgetExceeded, GatewayError, NoProviderAvailable
from ai_core.models import ModelProvider, ModelUsage
from core.context import get_request_id

logger = logging.getLogger(__name__)

_MTOK = Decimal(1_000_000)
_COST_PRECISION = Decimal("0.000001")
_AUDIO_CAPABILITIES = {ModelProvider.Capability.STT, ModelProvider.Capability.TTS}
# TTS params every call starts from; the provider defaults and the language
# route (A2) override them per provider (`_tts_params`).
_TTS_PARAM_KEYS = ("voice", "response_format", "pcm_sample_rate")


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
    routing_reason="",
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
    in-process rows correlate the same way. There is no provider to have
    routed to, so `routing_reason` (Phase 5 D11) is the caller's own reason
    for picking the external agent -- e.g. the harness job's
    `agent_routing_reason` ("policy:default" | "owner_selected" |
    "policy:cross_vendor") -- so external rows explain themselves the way
    in-process rows do; empty when the caller has none."""
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
        routing_reason=routing_reason or "",
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
    language: str = ""


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
        budget_session="",
        only_provider=None,
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
        is never a silent fallback to a cloud provider.

        Budgets (A3): a configured `AIBudget` covering `purpose` is checked
        first, also eagerly; a refusal raises `BudgetExceeded` with one
        `budget_exceeded` row. Token cost cannot be known before the call,
        so a chat call is refused only once its window is already spent.

        `only_provider` (a `ModelProvider.name`; Phase 5 closeout, used by
        `manage.py preflight`) restricts selection to that one row -- still
        only if it is active, keyed and allowed for `personal_data`; there
        is no fallback. Same single-door accounting: one `ModelUsage` row."""
        started = time.monotonic()
        merged_params = dict(prompt_version.model_params) if prompt_version else {}
        merged_params.update(params or {})
        base_report = self._base_report(context_report, personal_data, budget_session)

        self._enforce_budget(
            purpose=purpose,
            capability=ModelProvider.Capability.CHAT,
            personal_data=personal_data,
            units=0,
            session=budget_session,
            actor=actor,
            workspace=workspace,
            prompt_version=prompt_version,
            started=started,
            report=base_report,
            on_usage=on_usage,
        )
        providers = self._select_providers(
            capability=ModelProvider.Capability.CHAT,
            purpose=purpose,
            personal_data=personal_data,
            only_provider=only_provider,
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

        # Phase 8B D11: the pre-send compression stage, after budget and
        # provider selection, before any adapter call (sync and stream alike).
        # Off by default; with it off the messages are passed through as-is.
        messages, compression_report = compression.apply(
            purpose, messages, model=providers[0].model_id, personal_data=personal_data
        )
        if compression_report is not None:
            base_report["compression"] = compression_report

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
        budget_session="",
        only_provider=None,
    ):
        """Embed a batch of texts. Same retry/fallback/accounting contract as
        `complete()` (ADR-003: embeddings are a gateway call like any other,
        exactly one ModelUsage row) -- including `personal_data`'s
        allowed-providers-only, fail-closed selection and the budget check.
        No streaming variant. `only_provider`: as for `complete()`."""
        started = time.monotonic()
        base_report = self._base_report(context_report, personal_data, budget_session)
        base_report["item_count"] = len(texts)

        self._enforce_budget(
            purpose=purpose,
            capability=ModelProvider.Capability.EMBEDDING,
            personal_data=personal_data,
            units=0,
            session=budget_session,
            actor=actor,
            workspace=workspace,
            prompt_version=prompt_version,
            started=started,
            report=base_report,
            on_usage=on_usage,
        )
        providers = self._select_providers(
            capability=ModelProvider.Capability.EMBEDDING,
            purpose=purpose,
            personal_data=personal_data,
            only_provider=only_provider,
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
        params=None,
        actor=None,
        workspace=None,
        prompt_version=None,
        context_report=None,
        on_usage=None,
        budget_session="",
        only_provider=None,
    ):
        """Speech-to-text (MVP 12, Phase 6 D4/D5). Same single-door/one-
        ModelUsage-row/retry/fallback contract as `embed()` (ADR-003). `audio`
        is D3's 16 kHz mono PCM16 WAV as bytes -- held in memory only, never
        logged or stored; `params` may carry `language` (ISO 639-1, empty =
        auto-detect). Always personal data (D5): only
        `personal_data_allowed` providers, no kwarg relaxes it. No
        streaming variant. `only_provider` pins the call to one row, as in
        `complete()` (Phase 6 closeout: the preflight voice checks) -- the
        personal-data filter still applies to the pinned row."""
        params = dict(params or {})
        started = time.monotonic()
        base_report = self._base_report(context_report, True, budget_session)
        base_report["input_bytes"] = len(audio or b"")
        base_report["language"] = str(params.get("language") or "")
        seconds = wav_seconds_or_zero(audio)

        result, usage = self._run_audio(
            purpose=purpose,
            capability=ModelProvider.Capability.STT,
            adapter_call=lambda adapter: adapter.transcribe(
                audio, content_type=content_type, params=params, purpose=purpose
            ),
            units=seconds,
            actor=actor,
            workspace=workspace,
            prompt_version=prompt_version,
            base_report=base_report,
            started=started,
            on_usage=on_usage,
            budget_session=budget_session,
            only_provider=only_provider,
        )
        return TranscribeGatewayResult(
            text=result.text,
            usage=usage,
            audio_seconds=result.audio_seconds,
            language=getattr(result, "language", "") or base_report["language"],
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
        budget_session="",
        only_provider=None,
    ):
        """Text-to-speech (MVP 12, Phase 6 D4/D5/A2). Same contract as
        `transcribe()`, personal data included. `params["language"]` selects
        the provider through `settings.VOICE_TTS_ROUTES` -- still only among
        eligible (active, keyed, `personal_data_allowed`) rows, never chosen
        by the client; voice and response format come from the route / the
        provider defaults (`settings.VOICE_TTS_PROVIDER_PARAMS`). No
        streaming variant. `only_provider` pins the call to one eligible row
        (no route, no fallback); the route's voice still applies when the
        pinned row is the routed one."""
        params = dict(params or {})
        started = time.monotonic()
        language = str(params.get("language") or "")
        base_report = self._base_report(context_report, True, budget_session)
        base_report["input_bytes"] = len((text or "").encode("utf-8"))
        base_report["language"] = language
        route = (getattr(settings, "VOICE_TTS_ROUTES", None) or {}).get(language) or {}
        if route.get("provider"):
            base_report["tts_route"] = route["provider"]

        result, usage = self._run_audio(
            purpose=purpose,
            capability=ModelProvider.Capability.TTS,
            adapter_call=lambda adapter: adapter.synthesize(
                text, params=self._tts_params(adapter.provider, params, route), purpose=purpose
            ),
            units=len(text or ""),
            actor=actor,
            workspace=workspace,
            prompt_version=prompt_version,
            base_report=base_report,
            started=started,
            on_usage=on_usage,
            budget_session=budget_session,
            preferred=route.get("provider") or "",
            preferred_reason=f"route:{language}"[:50] if language else "",
            only_provider=only_provider,
        )
        return SynthesizeGatewayResult(
            audio=result.audio,
            usage=usage,
            content_type=result.content_type,
            audio_seconds=result.audio_seconds,
        )

    @staticmethod
    def _tts_params(provider, params, route):
        """Effective TTS params for one provider: the global default voice
        < the caller's params < the provider's own defaults
        (`VOICE_TTS_PROVIDER_PARAMS[name]`) < the language route, when this
        provider is the routed one. A voice id belongs to one provider, so a
        fallback provider never receives the route's voice."""
        effective = {
            "voice": getattr(settings, "VOICE_TTS_VOICE", ""),
            "response_format": "wav",
        }
        effective.update(params)
        provider_defaults = (getattr(settings, "VOICE_TTS_PROVIDER_PARAMS", None) or {}).get(
            provider.name, {}
        )
        effective.update({k: v for k, v in provider_defaults.items() if v})
        if route.get("provider") == provider.name:
            effective.update({k: route[k] for k in _TTS_PARAM_KEYS if route.get(k)})
        return effective

    def _run_audio(
        self,
        *,
        purpose,
        capability,
        adapter_call,
        units,
        actor,
        workspace,
        prompt_version,
        base_report,
        started,
        on_usage,
        budget_session="",
        preferred="",
        preferred_reason="",
        only_provider=None,
    ):
        """Shared STT/TTS execution: budget check, then provider selection
        (personal-data providers only, D5 -- unconditionally), retry/fallback
        and exactly one ModelUsage row, mirroring `embed()`'s non-streaming
        shape. `adapter_call(adapter)` performs the one provider call and
        returns a Transcribe/Synthesize AdapterResult carrying
        `audio_seconds` (recorded in context_report). `units` is the call's
        billable quantity known before the call -- input seconds (STT) or
        characters (TTS) -- used for the pre-call budget estimate and the
        configured-price cost."""
        self._enforce_budget(
            purpose=purpose,
            capability=capability,
            personal_data=True,
            units=units,
            session=budget_session,
            actor=actor,
            workspace=workspace,
            prompt_version=prompt_version,
            started=started,
            report=base_report,
            on_usage=on_usage,
        )
        providers = self._select_providers(
            capability=capability,
            purpose=purpose,
            personal_data=True,
            preferred=preferred,
            preferred_reason=preferred_reason,
            only_provider=only_provider,
        )
        if not providers:
            raise self._no_provider_error(
                purpose=purpose,
                actor=actor,
                workspace=workspace,
                prompt_version=prompt_version,
                started=started,
                report=base_report,
                personal_data=True,
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
                cost, cost_source = self._audio_cost(provider, capability, result, units)
                report["cost_source"] = cost_source
                generation_id = getattr(result, "generation_id", "") or ""
                if generation_id:
                    report["generation_id"] = generation_id
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
                    cost=cost,
                )
                if cost_source == "configured_price" and generation_id:
                    self._schedule_cost_correction(usage.pk)
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

    @classmethod
    def _audio_cost(cls, provider, capability, result, units):
        """`(cost, cost_source)` for one successful audio call (A3): the
        provider-reported cost when the response carried one
        (`"provider"`), else the row's configured per-second (STT) /
        per-million-characters (TTS) price plus any token price
        (`"configured_price"`), `"free"` when that is 0."""
        reported = getattr(result, "reported_cost", None)
        if reported is not None:
            return Decimal(reported).quantize(_COST_PRECISION), "provider"
        cost = cls._estimate_cost(provider, result.input_tokens, result.output_tokens)
        cost += cls._audio_unit_cost(provider, capability, units, result)
        cost = cost.quantize(_COST_PRECISION)
        return cost, ("configured_price" if cost > 0 else "free")

    @staticmethod
    def _audio_unit_cost(provider, capability, units, result=None):
        if capability == ModelProvider.Capability.STT:
            seconds = getattr(result, "audio_seconds", 0.0) if result is not None else units
            return Decimal(str(seconds or units or 0)) * provider.price_per_audio_second
        if capability == ModelProvider.Capability.TTS:
            return Decimal(int(units or 0)) * provider.price_per_mchar / _MTOK
        return Decimal("0")

    @staticmethod
    def _schedule_cost_correction(usage_id):
        """A1/A3: the response carried no cost, so the row holds the
        configured-price estimate; a Celery task replaces it with the
        provider's generation-lookup figure once the transaction commits.
        Best effort -- a broker outage leaves the estimate in place."""

        def enqueue():
            try:
                from ai_core.tasks import correct_usage_cost

                correct_usage_cost.delay(usage_id)
            except Exception:  # noqa: BLE001 -- accounting already holds an estimate
                logger.warning("ai_core: could not enqueue cost correction for usage %s", usage_id)

        transaction.on_commit(enqueue)

    def has_eligible_provider(
        self, *, purpose, personal_data=False, capability=ModelProvider.Capability.CHAT
    ):
        """Pre-flight (D5, Phase 5 branch 4): would a call with these
        arguments find a provider? The same selection `complete()` runs --
        including the `personal_data` filter -- but no LLM call is made and
        no `ModelUsage` row is written (nothing was attempted). A connector
        read that must not happen without an allowed provider asks this
        first. Audio capabilities are always personal data (Phase 6 D5)."""
        return bool(
            self._select_providers(
                capability=capability,
                purpose=purpose,
                personal_data=personal_data or capability in _AUDIO_CAPABILITIES,
            )
        )

    # --- provider selection ---

    @staticmethod
    def _eligible_providers(*, capability, personal_data):
        """Active providers of `capability` that could serve a call right
        now, by priority: fake only behind `AI_FAKE_PROVIDER`, real ones
        only when their key env var is set, and -- with `personal_data` --
        only `personal_data_allowed` rows. Inactive (`is_active=False`)
        rows, e.g. the seeded-disabled OpenRouter audio rows, never
        appear."""
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
        return eligible

    def _enforce_budget(
        self,
        *,
        purpose,
        capability,
        personal_data,
        units,
        session,
        actor,
        workspace,
        prompt_version,
        started,
        report,
        on_usage,
    ):
        """A3: refuse BEFORE provider selection when a covering `AIBudget`
        cap is spent or this call's worst-case estimate would break it.
        The estimate is the dearest eligible candidate's price for `units`
        (audio); a call whose every candidate is unpriced (local) is never
        refused. A refusal writes one `budget_exceeded` row (provider None,
        cost 0) and raises `BudgetExceeded`."""
        if not budgets.budgets_for(purpose):
            return
        candidates = self._eligible_providers(capability=capability, personal_data=personal_data)
        priced = any(p.is_priced for p in candidates)
        estimate = Decimal("0")
        if capability in _AUDIO_CAPABILITIES and units:
            estimate = max(
                (self._audio_unit_cost(p, capability, units) for p in candidates),
                default=Decimal("0"),
            ).quantize(_COST_PRECISION)
        refusal = budgets.find_refusal(purpose, estimate=estimate, priced=priced, session=session)
        if refusal is None:
            return
        refused_report = dict(report)
        refused_report["budget"] = refusal.report()
        self._record(
            purpose=purpose,
            actor=actor,
            workspace=workspace,
            prompt_version=prompt_version,
            provider=None,
            result=None,
            success=False,
            error_type="budget_exceeded",
            started=started,
            report=refused_report,
            attempts=[],
            on_usage=on_usage,
            routing_reason=f"budget:{refusal.budget}:{refusal.window}"[:50],
        )
        raise BudgetExceeded(refusal.budget, refusal.window)

    def _select_providers(
        self,
        *,
        capability=ModelProvider.Capability.CHAT,
        purpose="",
        personal_data=False,
        preferred="",
        preferred_reason="",
        only_provider=None,
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
        `ModelUsage` row by `_record` — the single usage writer.

        `preferred` (Phase 6 A2, TTS language routes): a provider name moved
        to the front IF it is already eligible (reason `preferred_reason`,
        e.g. "route:tr"); an ineligible routed provider -- inactive,
        keyless or not personal-data-allowed -- is simply skipped, so a
        route can never widen the eligible set.

        `only_provider` (Phase 5 closeout): selection is pinned to that one
        eligible row, with no fallback (reason "pinned")."""
        eligible = self._eligible_providers(capability=capability, personal_data=personal_data)
        if only_provider is not None:
            # Phase 5 closeout pin (`manage.py preflight`): that one row or
            # nothing -- no route, tier or fallback can widen it.
            eligible = [p for p in eligible if p.name == only_provider]
            for provider in eligible:
                provider._routing_reason = "pinned"
            return eligible[:1]

        if preferred:
            routed = [p for p in eligible if p.name == preferred]
            if routed:
                eligible = routed + [p for p in eligible if p.name != preferred]
                selected = eligible[:2]
                for position, provider in enumerate(selected):
                    provider._routing_reason = (
                        (preferred_reason or "route") if position == 0 else f"fallback:{position}"
                    )
                return selected

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
    def _base_report(context_report, personal_data, budget_session=""):
        report = dict(context_report or {})
        if personal_data:
            # The row explains why only allowed providers were eligible.
            report["personal_data"] = True
        if budget_session:
            # A3 session window: budgets.spent() sums rows by this key.
            report[budgets.SESSION_KEY] = str(budget_session)[:64]
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
        cost=None,
    ):
        """The single in-process `ModelUsage` writer. `cost` overrides the
        token-priced estimate (audio rows, A3)."""
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
            estimated_cost=(
                cost
                if cost is not None
                else self._estimate_cost(provider, input_tokens, output_tokens)
            ),
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
