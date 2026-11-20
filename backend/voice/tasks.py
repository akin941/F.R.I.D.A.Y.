"""Voice beat tasks (Phase 6 A5)."""

from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from voice.models import VoiceTurnTrace


def prune_voice_turn_traces(*, now=None):
    """Delete trace rows older than `VOICE_TRACE_RETENTION_DAYS` (30).
    System code, not a view: no actor, no `for_actor()` (the
    `purge_expired_trashed_*` precedent). Returns the number deleted."""
    cutoff = (now or timezone.now()) - timedelta(days=settings.VOICE_TRACE_RETENTION_DAYS)
    deleted, _ = VoiceTurnTrace.objects.filter(created_at__lt=cutoff).delete()
    return deleted


@shared_task
def prune_voice_turn_traces_task():
    return prune_voice_turn_traces()
