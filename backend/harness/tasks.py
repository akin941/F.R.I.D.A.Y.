"""The `harness` job-execution Celery task (Phase 2 branch 2, brief §2).
Autodiscovered by `config.celery.app.autodiscover_tasks()`. All the actual
logic (claim, brakes, Docker orchestration) lives in
`harness.execution.run_harness_job`, kept Celery-agnostic so it can be
unit-tested without a broker/worker -- mirrors `agents.tasks`'s identical
split against `agents.execution`."""

import logging

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings

from harness import (
    execution,
    graphify_execution,
    implementation_execution,
    ingestion,
    review_execution,
)

logger = logging.getLogger(__name__)

# The recovery scan only reclaims a `running` HarnessJob once it's older
# than HARNESS_MAX_JOB_SECONDS + HARNESS_RECOVERY_GRACE_SECONDS -- this
# buffer must stay comfortably above the Celery task's own hard time_limit
# below (HARNESS_MAX_JOB_SECONDS + HARNESS_CELERY_FINALIZATION_BUFFER_SECONDS
# + 10), or the scan could reclaim a job still legitimately within its own
# timeout window (mirrors agents/tasks.py's identical assertion). Codex
# code-review P2, branch 2 round 6: an earlier draft's assertion (`> 10`)
# was written before HARNESS_CELERY_FINALIZATION_BUFFER_SECONDS existed
# (round 5) and was never updated for it -- it could pass even when the
# recovery threshold actually fell BEFORE the hard limit, letting beat
# reclaim (and tear down) a job its own worker was still allowed to
# finalize.
assert (
    settings.HARNESS_RECOVERY_GRACE_SECONDS
    > settings.HARNESS_CELERY_FINALIZATION_BUFFER_SECONDS + 10
), (
    "HARNESS_RECOVERY_GRACE_SECONDS must exceed HARNESS_CELERY_FINALIZATION_BUFFER_SECONDS + "
    "the Celery hard time_limit buffer (10s), or the recovery scan could reclaim a job that "
    "is still legitimately in flight."
)


@shared_task(
    soft_time_limit=settings.HARNESS_MAX_JOB_SECONDS
    + settings.HARNESS_CELERY_FINALIZATION_BUFFER_SECONDS,
    time_limit=settings.HARNESS_MAX_JOB_SECONDS
    + settings.HARNESS_CELERY_FINALIZATION_BUFFER_SECONDS
    + 10,
)
def run_harness_job(job_id):
    """Thin wrapper -- see `harness.execution.run_harness_job` for the
    actual clone/launch/wait/finalize logic. `soft_time_limit` is
    `HARNESS_MAX_JOB_SECONDS` PLUS `HARNESS_CELERY_FINALIZATION_BUFFER_SECONDS`
    (Codex code-review P2, branch 2 round 5: an earlier draft set it equal
    to `HARNESS_MAX_JOB_SECONDS` exactly, racing the wait loop's own
    internal timeout branch -- if Celery's limit fired mid-teardown/
    capture/finalize, the interrupted work was abandoned and the outer
    safety net could duplicate or lose the recorded outcome). The hard
    `time_limit` is a further backstop on top of that; the wait loop
    already tears the container down on its own once it detects the
    internal `HARNESS_MAX_JOB_SECONDS` brake, so neither Celery limit
    should normally fire.

    Both exception branches resolve the job immediately via
    `resolve_running_job_after_unexpected_error` (Codex code-review P2,
    branch 2 round 1: an earlier draft only logged and swallowed, leaving
    a `running` row stalled until the recovery scan's full grace window
    instead of resolving it right away) -- `SoftTimeLimitExceeded` is
    handled explicitly (not merely caught by the blanket branch below) so
    its log message is distinguishable from a genuine bug, mirroring
    `agents.tasks.advance_agent_step`'s identical split."""
    try:
        execution.run_harness_job(job_id)
    except SoftTimeLimitExceeded:
        logger.exception("Harness job %s hit its soft time limit", job_id)
        execution.resolve_running_job_after_unexpected_error(job_id)
    except Exception:
        logger.exception("Unexpected failure running harness job %s", job_id)
        execution.resolve_running_job_after_unexpected_error(job_id)


@shared_task
def recover_stale_harness_jobs():
    """Periodic scan (Celery beat, embedded via the worker's `-B` flag --
    see deploy/docker/compose.yml) for HarnessJobs whose execution got
    stuck with no live worker left to unstick them. Mirrors
    `agents.tasks.recover_stale_agents`/`tools.tasks.
    recover_stale_vault_export_runs` exactly."""
    return execution.recover_stale_harness_jobs()


@shared_task(
    soft_time_limit=settings.HARNESS_MAX_JOB_SECONDS
    + settings.HARNESS_CELERY_FINALIZATION_BUFFER_SECONDS,
    time_limit=settings.HARNESS_MAX_JOB_SECONDS
    + settings.HARNESS_CELERY_FINALIZATION_BUFFER_SECONDS
    + 10,
)
def run_harness_implementation_job(job_id):
    """Thin wrapper -- see `harness.implementation_execution.
    run_harness_implementation_job` for the actual writable-clone/launch/
    wait/test-result logic (Phase 3 branch 2). Same soft/hard time-limit
    shape as `run_harness_job` above, for the same reason.

    Unlike `run_harness_job`, an unexpected interruption here does NOT
    call any "resolve after unexpected error" helper -- there is no
    synchronous one for this job type; a job interrupted here stays
    `implementing` until `recover_stale_harness_implementation_jobs`
    (Phase 3 branch 4, registered in `config/celery.py`'s beat schedule)
    reclaims it, the same grace-window behavior `HarnessJob` itself had
    before its own recovery scan existed."""
    try:
        implementation_execution.run_harness_implementation_job(job_id)
    except SoftTimeLimitExceeded:
        logger.exception("Harness implementation job %s hit its soft time limit", job_id)
    except Exception:
        logger.exception("Unexpected failure running harness implementation job %s", job_id)


@shared_task(
    soft_time_limit=settings.HARNESS_MAX_JOB_SECONDS
    + settings.HARNESS_CELERY_FINALIZATION_BUFFER_SECONDS,
    time_limit=settings.HARNESS_MAX_JOB_SECONDS
    + settings.HARNESS_CELERY_FINALIZATION_BUFFER_SECONDS
    + 10,
)
def run_harness_push_job(job_id):
    """Thin wrapper -- see `harness.implementation_execution.
    run_harness_push_job` for the actual bundle/clean-clone/push logic.
    Same recovery-scan-is-the-backstop shape as `run_harness_
    implementation_job` above, one phase later --
    `recover_stale_harness_implementation_jobs` covers `pushing` too."""
    try:
        implementation_execution.run_harness_push_job(job_id)
    except SoftTimeLimitExceeded:
        logger.exception("Harness push job %s hit its soft time limit", job_id)
    except Exception:
        logger.exception("Unexpected failure running harness push job %s", job_id)


@shared_task
def recover_stale_harness_implementation_jobs():
    """Periodic scan (Celery beat, embedded via the worker's `-B` flag --
    see deploy/docker/compose.yml), Phase 3 branch 4. Covers
    `HarnessImplementationJob`'s `implementing`/`pushing` phases (both a
    lost-gate-approval-dispatch case and a worker-crashed-mid-run case
    each) plus the worktree-retention backstop sweep -- see
    `harness.implementation_execution.
    recover_stale_harness_implementation_jobs`'s own docstring for the
    full case list."""
    return implementation_execution.recover_stale_harness_implementation_jobs()


@shared_task(
    soft_time_limit=settings.HARNESS_MAX_JOB_SECONDS
    + settings.HARNESS_CELERY_FINALIZATION_BUFFER_SECONDS,
    time_limit=settings.HARNESS_MAX_JOB_SECONDS
    + settings.HARNESS_CELERY_FINALIZATION_BUFFER_SECONDS
    + 10,
)
def run_harness_review_job(review_id):
    """Thin wrapper -- see `harness.review_execution.run_harness_review_job`
    for the actual read-only review container/gateway-summarization
    logic (Phase 3 branch 3). Same soft/hard time-limit shape as the
    other harness tasks, for the same reason.

    Both exception branches resolve the review immediately via
    `resolve_running_review_after_unexpected_error` (Codex code-review
    P2, branch 3 round 2: an earlier version only logged and swallowed,
    leaving a `running` review -- and its implementation job stuck
    `pending_review` -- with no periodic recovery scan yet to reclaim it;
    mirrors `run_harness_job`'s identical two-branch split above)."""
    try:
        review_execution.run_harness_review_job(review_id)
    except SoftTimeLimitExceeded:
        logger.exception("Harness review %s hit its soft time limit", review_id)
        review_execution.resolve_running_review_after_unexpected_error(review_id)
    except Exception:
        logger.exception("Unexpected failure running harness review %s", review_id)
        review_execution.resolve_running_review_after_unexpected_error(review_id)


@shared_task
def recover_stale_harness_reviews():
    """Periodic scan (Celery beat, embedded via the worker's `-B` flag --
    see deploy/docker/compose.yml), Phase 3 branch 4 -- the backstop for
    the cases branch 3's synchronous rescues can't reach (a message
    silently dropped after successful publish, a hard kill with nothing
    left to run an exception handler) plus the leaked-review-container
    sweep round 4 explicitly deferred here. See `harness.review_
    execution.recover_stale_harness_reviews`'s own docstring for the
    full case list."""
    return review_execution.recover_stale_harness_reviews()


@shared_task
def ingest_repo_commits():
    """Phase 8A branch 4 (brief D5): the git-commit producer. Routed to the
    harness queue and scheduled on the runner's own beat -- it clones with
    the runner's credentials, like discovery jobs. See
    `harness.ingestion.ingest_repo_commits`."""
    return ingestion.ingest_repo_commits()


@shared_task(
    soft_time_limit=settings.HARNESS_MAX_JOB_SECONDS
    + settings.HARNESS_CELERY_FINALIZATION_BUFFER_SECONDS,
    time_limit=settings.HARNESS_MAX_JOB_SECONDS
    + settings.HARNESS_CELERY_FINALIZATION_BUFFER_SECONDS
    + 10,
)
def run_graphify_run(run_id):
    """Phase 8B branch 5 (brief D10): one Graphify AST-only run, on the
    runner queue. See `harness.graphify_execution.run_graphify_run`; it
    never raises, and a worker killed mid-run is reclaimed by the lease
    recovery below."""
    try:
        return graphify_execution.run_graphify_run(run_id)
    except SoftTimeLimitExceeded:
        logger.exception("Graphify run %s hit its soft time limit", run_id)
    return None


@shared_task
def recover_stale_graphify_runs():
    """Runner beat: re-dispatch lost or lease-expired Graphify runs."""
    return graphify_execution.recover_stale_graphify_runs()
