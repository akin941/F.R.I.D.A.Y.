"""Gateway background jobs. Autodiscovered by `config.celery`.

`correct_usage_cost` (Phase 6 A1/A3, ADR-003 amendment 2026-11-20): an
audio call whose response carried no cost (OpenRouter `/audio/speech`
returns raw bytes) was recorded with the provider row's configured-price
estimate and its `generation_id`. This task asks the provider's generation
lookup for the real figure and rewrites that same row's `estimated_cost`
-- it never creates a second `ModelUsage` row.
"""

import logging
from decimal import Decimal

from celery import shared_task
from django.db import transaction

from ai_core.adapters import AdapterError, get_adapter
from ai_core.models import ModelUsage

logger = logging.getLogger(__name__)

# OpenRouter's generation stats can lag the response by a few seconds.
COST_LOOKUP_MAX_RETRIES = 5
COST_LOOKUP_RETRY_SECONDS = 30


class CostNotYetAvailable(Exception):  # noqa: N818 -- a state, not an error
    pass


def apply_cost_correction(usage_id):
    """One lookup attempt. Returns `"corrected"`, `"skipped"` (row gone,
    already corrected or not correctable) or raises `CostNotYetAvailable`
    / `AdapterError` for the Celery wrapper to retry. Idempotent: only a
    row still marked `cost_source="configured_price"` is touched."""
    usage = ModelUsage.objects.select_related("provider").filter(pk=usage_id).first()
    if usage is None or usage.provider is None:
        return "skipped"
    report = usage.context_report or {}
    generation_id = report.get("generation_id")
    if report.get("cost_source") != "configured_price" or not generation_id:
        return "skipped"
    cost = get_adapter(usage.provider).generation_cost(generation_id)
    if cost is None:
        raise CostNotYetAvailable
    with transaction.atomic():
        locked = ModelUsage.objects.select_for_update().get(pk=usage_id)
        report = dict(locked.context_report or {})
        if report.get("cost_source") != "configured_price":
            return "skipped"
        report["estimated_cost_before_correction"] = str(locked.estimated_cost)
        report["cost_source"] = "provider_lookup"
        locked.context_report = report
        locked.estimated_cost = Decimal(cost).quantize(Decimal("0.000001"))
        locked.save(update_fields=["context_report", "estimated_cost", "updated_at"])
    return "corrected"


@shared_task(bind=True, max_retries=COST_LOOKUP_MAX_RETRIES)
def correct_usage_cost(self, usage_id):
    try:
        return apply_cost_correction(usage_id)
    except CostNotYetAvailable as exc:
        raise self.retry(countdown=COST_LOOKUP_RETRY_SECONDS, exc=exc) from exc
    except AdapterError as exc:
        if exc.retryable:
            raise self.retry(countdown=COST_LOOKUP_RETRY_SECONDS, exc=exc) from exc
        logger.warning("ai_core: cost lookup for usage %s failed: %s", usage_id, exc.error_type)
        return "failed"
