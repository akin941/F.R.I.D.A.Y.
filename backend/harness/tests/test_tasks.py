import uuid
from unittest import mock

import pytest
from celery.exceptions import SoftTimeLimitExceeded

from harness import tasks

pytestmark = pytest.mark.django_db


class TestRunHarnessJobTask:
    def test_delegates_to_execution(self, monkeypatch):
        run_mock = mock.Mock()
        monkeypatch.setattr(tasks.execution, "run_harness_job", run_mock)
        job_id = str(uuid.uuid4())
        tasks.run_harness_job(job_id)
        run_mock.assert_called_once_with(job_id)

    def test_unexpected_exception_is_logged_and_resolves_the_running_job(self, monkeypatch):
        monkeypatch.setattr(
            tasks.execution, "run_harness_job", mock.Mock(side_effect=RuntimeError("boom"))
        )
        resolve_mock = mock.Mock()
        monkeypatch.setattr(
            tasks.execution, "resolve_running_job_after_unexpected_error", resolve_mock
        )
        job_id = str(uuid.uuid4())
        # Must not raise -- mirrors agents.tasks.advance_agent_step's outer
        # safety net for anything truly unexpected.
        tasks.run_harness_job(job_id)
        resolve_mock.assert_called_once_with(job_id)

    def test_soft_time_limit_is_handled_explicitly_and_resolves_the_running_job(self, monkeypatch):
        """Codex code-review P2, branch 2 round 1: an earlier draft only
        caught this via the blanket `except Exception` and logged+swallowed
        it, leaving the job `running` with no finalize call at all."""
        monkeypatch.setattr(
            tasks.execution,
            "run_harness_job",
            mock.Mock(side_effect=SoftTimeLimitExceeded()),
        )
        resolve_mock = mock.Mock()
        monkeypatch.setattr(
            tasks.execution, "resolve_running_job_after_unexpected_error", resolve_mock
        )
        job_id = str(uuid.uuid4())
        tasks.run_harness_job(job_id)
        resolve_mock.assert_called_once_with(job_id)


class TestCelerySoftTimeLimitBuffer:
    def test_soft_time_limit_exceeds_the_internal_wait_loop_deadline(self, settings):
        """Codex code-review P2, branch 2 round 5: an earlier draft set
        soft_time_limit == HARNESS_MAX_JOB_SECONDS exactly, racing the
        wait loop's own internal timeout branch -- if Celery's limit fired
        while the loop was still tearing down/capturing/finalizing, the
        interrupted work was abandoned and the outer safety net could
        duplicate or lose the outcome."""
        assert tasks.run_harness_job.soft_time_limit == (
            settings.HARNESS_MAX_JOB_SECONDS + settings.HARNESS_CELERY_FINALIZATION_BUFFER_SECONDS
        )
        assert tasks.run_harness_job.time_limit > tasks.run_harness_job.soft_time_limit

    def test_buffer_covers_worst_case_docker_control_calls_on_timeout(self, settings):
        """Codex code-review P2, branch 2 round 7: the internal timeout path
        can call capture_logs, stop_container (grace + control timeout),
        container_is_running (an inspect call inside `_teardown_container`,
        before kill_container -- round 8 caught the round-7 fix's own math
        missing this one), kill_container, and remove_container before
        `_finalize` runs -- the buffer must cover all five calls, not just
        four, or Celery's own hard `time_limit` can fire mid-finalize (see
        the module-level assertion above this task's decorator)."""
        from harness.runner import _DOCKER_CONTROL_TIMEOUT_SECONDS

        worst_case = (
            _DOCKER_CONTROL_TIMEOUT_SECONDS  # capture_logs
            + (settings.HARNESS_KILL_GRACE_SECONDS + _DOCKER_CONTROL_TIMEOUT_SECONDS)  # stop
            + _DOCKER_CONTROL_TIMEOUT_SECONDS  # container_is_running (inspect, before kill)
            + _DOCKER_CONTROL_TIMEOUT_SECONDS  # kill (if still running)
            + _DOCKER_CONTROL_TIMEOUT_SECONDS  # remove
        )
        assert settings.HARNESS_CELERY_FINALIZATION_BUFFER_SECONDS > worst_case


class TestRecoverStaleHarnessJobsTask:
    def test_delegates_to_execution(self, monkeypatch):
        monkeypatch.setattr(
            tasks.execution, "recover_stale_harness_jobs", mock.Mock(return_value=3)
        )
        assert tasks.recover_stale_harness_jobs() == 3
