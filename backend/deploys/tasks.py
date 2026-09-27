"""Deploy Celery tasks (brief D10). Every `deploys.tasks.*` task is routed
to `DEPLOY_CELERY_QUEUE` (`CELERY_TASK_ROUTES`) and consumed only by the
runner (`make harness-runner`, `-Q harness,deploy`), the one host holding
`DEPLOY_SSH_KEY_PATH`; the recovery scan runs on the runner's own beat
(`config.celery.build_beat_schedule(runner_mode=True)`). Logic lives in
`deploys.execution`, kept Celery-agnostic."""

import logging

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings

from deploys import execution

logger = logging.getLogger(__name__)

# One run: status + deploy + a health window + rollback + a second health
# window, each SSH step bounded by its own timeout.
_MAX_HEALTH_WINDOW_SECONDS = 900
_RUN_SOFT_TIME_LIMIT = (
    settings.DEPLOY_SSH_STATUS_TIMEOUT_SECONDS
    + 2 * settings.DEPLOY_SSH_DEPLOY_TIMEOUT_SECONDS
    + 2
    * (
        _MAX_HEALTH_WINDOW_SECONDS
        + settings.DEPLOY_HEALTH_CHECKS_PER_WINDOW
        * (settings.DEPLOY_SSH_HEALTH_TIMEOUT_SECONDS + settings.DEPLOY_HEALTH_URL_TIMEOUT_SECONDS)
    )
    + 300
)

# The lease is renewed at every step; the longest single step is the
# deploy/rollback verb (or one health interval plus its probes). The
# recovery scan must never declare a live run lost.
assert settings.DEPLOY_RUN_LEASE_SECONDS > (
    max(
        settings.DEPLOY_SSH_DEPLOY_TIMEOUT_SECONDS,
        _MAX_HEALTH_WINDOW_SECONDS / 3
        + settings.DEPLOY_SSH_HEALTH_TIMEOUT_SECONDS
        + settings.DEPLOY_HEALTH_URL_TIMEOUT_SECONDS,
    )
    + 60
), "DEPLOY_RUN_LEASE_SECONDS must exceed the longest single deploy step plus a margin."


@shared_task(soft_time_limit=_RUN_SOFT_TIME_LIMIT, time_limit=_RUN_SOFT_TIME_LIMIT + 30)
def run_deploy(run_id):
    """Claim and drive one approved deploy run. Never retried by Celery:
    a failure after the deploy verb is `unknown_outcome`, by design."""
    try:
        execution.execute_deploy_run(run_id)
    except SoftTimeLimitExceeded:
        logger.exception("Deploy run %s hit its soft time limit", run_id)


@shared_task
def refresh_deploy_target_status(target_id):
    execution.refresh_target_status(target_id)


@shared_task
def recover_stale_deploy_runs():
    """Re-dispatches `pending` runs whose publish was lost and `checking`
    runs whose runner died before the deploy verb (nothing touched); marks
    runs lost after the deploy verb `unknown_outcome` (never retried)."""
    redispatch, lost = execution.stale_run_ids()
    for run_id in lost:
        execution.mark_lost_run(run_id)
    for run_id in redispatch:
        try:
            run_deploy.delay(str(run_id))
        except Exception:
            logger.exception("Failed to re-dispatch deploy run %s", run_id)
    return {"redispatched": len(redispatch), "lost": len(lost)}
