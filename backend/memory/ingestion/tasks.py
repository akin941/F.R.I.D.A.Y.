"""Ingestion and learning-loop Celery tasks (Phase 8A branches 1, 3, 5).
Registered by `memory.tasks` importing this module, so Celery's per-app
autodiscovery (`memory.tasks`) finds them."""

from celery import shared_task

from memory.ingestion import consolidation, extraction, reflection
from memory.ingestion.hooks import purge_idle_coding_sessions
from memory.ingestion.services import purge_expired_episodes, purge_stale_extraction_jobs

# Gateway worst case (see memory.tasks): limits sit above it and below the
# claim lease, so a hung task never straddles a takeover.
_SOFT_TIME_LIMIT = 390
_TIME_LIMIT = extraction.CLAIM_LEASE_SECONDS - 10


@shared_task(name="memory.ingestion.tasks.purge_expired_episodes_task")
def purge_expired_episodes_task():
    """Daily beat (config/celery.py, d10): hard-delete expired episodes, the
    coding-session read rows idle past the same TTL (branch 3) and
    extraction jobs older than it (branch 5: their candidates derive from
    episode text). Returns the number of episodes deleted."""
    deleted = purge_expired_episodes()
    purge_idle_coding_sessions()
    purge_stale_extraction_jobs()
    return deleted


@shared_task(
    name="memory.ingestion.tasks.run_episode_extraction_job",
    soft_time_limit=_SOFT_TIME_LIMIT,
    time_limit=_TIME_LIMIT,
)
def run_episode_extraction_job(job_id):
    extraction.run_job(job_id)


@shared_task(name="memory.ingestion.tasks.recover_stale_episode_extraction_jobs")
def recover_stale_episode_extraction_jobs():
    """Every minute (compose beat): re-dispatch lost or lease-expired jobs."""
    return extraction.recover_stale_jobs()


@shared_task(
    name="memory.ingestion.tasks.consolidate_workspace_task",
    soft_time_limit=_SOFT_TIME_LIMIT,
    time_limit=_TIME_LIMIT,
)
def consolidate_workspace_task(workspace_id):
    return consolidation.consolidate_workspace(workspace_id)


@shared_task(name="memory.ingestion.tasks.run_learning_night_task")
def run_learning_night_task():
    """Nightly beat (D9/D10): first consolidate candidates still waiting
    from an earlier night (a released claim after a budget refusal), then
    plan tonight's extraction jobs; each job's success triggers its
    workspace's consolidation once the workspace has no open job."""
    consolidated = consolidation.consolidate_waiting()
    planned = extraction.plan_nightly_extraction()
    return {"consolidated": consolidated, "jobs_planned": len(planned)}


@shared_task(
    name="memory.ingestion.tasks.run_weekly_reflection_task",
    soft_time_limit=_SOFT_TIME_LIMIT,
    time_limit=_TIME_LIMIT,
)
def run_weekly_reflection_task():
    """Sunday 03:30 Europe/Istanbul beat (Phase 8B D5): one capped,
    proposal-only reflection per workspace with enough approved items this
    week; at most one run per workspace per ISO week."""
    return reflection.run_weekly_reflection()
