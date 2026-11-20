"""Ingestion beat tasks (Phase 8A branch 1). Registered by
`memory.tasks` importing this module, so Celery's per-app autodiscovery
(`memory.tasks`) finds them."""

from celery import shared_task

from memory.ingestion.hooks import purge_idle_coding_sessions
from memory.ingestion.services import purge_expired_episodes


@shared_task(name="memory.ingestion.tasks.purge_expired_episodes_task")
def purge_expired_episodes_task():
    """Daily beat (config/celery.py, d10): hard-delete expired episodes, and
    the coding-session read rows idle past the same TTL (branch 3). Returns
    the number of episodes deleted."""
    deleted = purge_expired_episodes()
    purge_idle_coding_sessions()
    return deleted
