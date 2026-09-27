"""Phase 5 branch 3 (brief D12): the shared container lifecycle leaf layer.
`wait_for_container` is exercised directly against a mocked `runner`
(timeout, stop predicate, post-exit re-check, `None` exit code failing
closed, transcript capture on/off); the per-engine outcome maps are pinned
to the `FailureReason`s the pre-extraction loops returned."""

import time
from unittest import mock

import pytest

from harness import container_lifecycle, execution, implementation_execution, review_execution
from harness.container_lifecycle import WaitOutcome, WaitResult, wait_for_container
from harness.models import HarnessImplementationJob, HarnessJob

CONTAINER = "harness-job-x"


@pytest.fixture
def docker(monkeypatch, settings):
    """A mocked `harness.runner`, patched through the module attribute the
    lifecycle layer calls it by. One parent mock records call order."""
    settings.HARNESS_CANCEL_POLL_SECONDS = 0
    settings.HARNESS_MAX_JOB_SECONDS = 1800
    calls = mock.Mock()
    calls.container_is_running.return_value = False
    calls.capture_logs.return_value = "cli transcript"
    calls.container_exit_code.return_value = 0
    for name in (
        "container_is_running",
        "capture_logs",
        "container_exit_code",
        "stop_container",
        "kill_container",
        "remove_container",
        "cleanup_directory",
    ):
        monkeypatch.setattr(container_lifecycle.runner, name, getattr(calls, name))
    return calls


def _wait(docker, **kwargs):
    kwargs.setdefault("started_monotonic", time.monotonic())
    kwargs.setdefault("teardown", docker.teardown)
    return wait_for_container(CONTAINER, **kwargs)


def _names(docker):
    return [call[0] for call in docker.mock_calls]


# --- timeout -------------------------------------------------------------


def test_timeout_captures_then_tears_down(docker, settings):
    settings.HARNESS_MAX_JOB_SECONDS = 5
    docker.container_is_running.return_value = True
    should_stop = mock.Mock(return_value=True)

    outcome = _wait(docker, started_monotonic=time.monotonic() - 10, should_stop=should_stop)

    assert outcome == WaitOutcome(WaitResult.TIMED_OUT, "cli transcript")
    # Logs are read while the container is still inspectable, before teardown.
    assert _names(docker) == ["container_is_running", "capture_logs", "teardown"]
    docker.teardown.assert_called_once_with(CONTAINER)
    # The time brake is checked first; the stop predicate never runs.
    should_stop.assert_not_called()
    docker.container_exit_code.assert_not_called()


def test_timeout_without_a_stop_predicate(docker, settings):
    settings.HARNESS_MAX_JOB_SECONDS = 0
    docker.container_is_running.return_value = True

    outcome = _wait(docker)

    assert outcome.result == WaitResult.TIMED_OUT
    docker.teardown.assert_called_once_with(CONTAINER)


# --- stop predicate ------------------------------------------------------


def test_stop_predicate_while_running_tears_down(docker):
    docker.container_is_running.return_value = True

    outcome = _wait(docker, should_stop=lambda: True)

    assert outcome == WaitOutcome(WaitResult.STOPPED, "cli transcript")
    assert _names(docker) == ["container_is_running", "capture_logs", "teardown"]
    docker.container_exit_code.assert_not_called()


def test_polls_until_the_container_exits(docker):
    docker.container_is_running.side_effect = [True, True, False]
    should_stop = mock.Mock(return_value=False)

    outcome = _wait(docker, should_stop=should_stop)

    assert outcome == WaitOutcome(WaitResult.EXITED_CLEANLY, "cli transcript")
    assert docker.container_is_running.call_count == 3
    assert should_stop.call_count == 2
    docker.teardown.assert_not_called()


def test_stop_predicate_observed_on_a_later_poll(docker):
    docker.container_is_running.return_value = True
    should_stop = mock.Mock(side_effect=[False, False, True])

    outcome = _wait(docker, should_stop=should_stop)

    assert outcome.result == WaitResult.STOPPED
    assert should_stop.call_count == 3
    docker.teardown.assert_called_once_with(CONTAINER)


# --- post-exit re-check --------------------------------------------------


def test_post_exit_recheck_wins_over_a_clean_exit_code(docker):
    """A stop arriving between the last "still running" poll and the
    container's own exit is not dropped (Codex code-review P1, Phase 2
    branch 2 round 3) -- and it is checked before the exit code."""
    docker.container_is_running.return_value = False
    docker.container_exit_code.return_value = 0

    outcome = _wait(docker, should_stop=lambda: True, recheck_stop_after_exit=True)

    assert outcome == WaitOutcome(WaitResult.STOPPED, "cli transcript")
    docker.teardown.assert_not_called()
    docker.container_exit_code.assert_not_called()
    # The transcript is captured before the re-check.
    assert _names(docker) == ["container_is_running", "capture_logs"]


def test_without_recheck_the_predicate_is_not_consulted_after_exit(docker):
    docker.container_is_running.return_value = False
    should_stop = mock.Mock(return_value=True)

    outcome = _wait(docker, should_stop=should_stop, recheck_stop_after_exit=False)

    assert outcome == WaitOutcome(WaitResult.EXITED_CLEANLY, "cli transcript")
    should_stop.assert_not_called()


def test_recheck_without_a_predicate_falls_through_to_the_exit_code(docker):
    docker.container_is_running.return_value = False

    outcome = _wait(docker, should_stop=None, recheck_stop_after_exit=True)

    assert outcome.result == WaitResult.EXITED_CLEANLY


def test_recheck_false_predicate_falls_through_to_the_exit_code(docker):
    docker.container_is_running.return_value = False
    docker.container_exit_code.return_value = 1

    outcome = _wait(docker, should_stop=lambda: False, recheck_stop_after_exit=True)

    assert outcome.result == WaitResult.EXITED_WITH_ERROR


# --- exit code -----------------------------------------------------------


@pytest.mark.parametrize("exit_code", [None, 1, 137, -1])
def test_unknown_or_non_zero_exit_code_fails_closed(docker, exit_code):
    """Only a confirmed `0` is a clean exit; `None` (inspect failed or was
    unparsable) is a Docker-control failure, never evidence of success."""
    docker.container_is_running.return_value = False
    docker.container_exit_code.return_value = exit_code

    outcome = _wait(docker)

    assert outcome == WaitOutcome(WaitResult.EXITED_WITH_ERROR, "cli transcript")
    docker.container_exit_code.assert_called_once_with(CONTAINER)


def test_zero_exit_code_is_a_clean_exit(docker):
    docker.container_is_running.return_value = False
    docker.container_exit_code.return_value = 0

    assert _wait(docker) == WaitOutcome(WaitResult.EXITED_CLEANLY, "cli transcript")


def test_runner_error_propagates(docker):
    docker.container_is_running.side_effect = container_lifecycle.runner.RunnerError("down")

    with pytest.raises(container_lifecycle.runner.RunnerError):
        _wait(docker)


# --- transcript capture on/off ------------------------------------------


@pytest.mark.parametrize(
    ("running", "max_seconds", "should_stop", "recheck", "exit_code", "expected"),
    [
        (True, 0, None, False, 0, WaitResult.TIMED_OUT),
        (True, 1800, True, False, 0, WaitResult.STOPPED),
        (False, 1800, True, True, 0, WaitResult.STOPPED),
        (False, 1800, None, False, None, WaitResult.EXITED_WITH_ERROR),
        (False, 1800, None, False, 0, WaitResult.EXITED_CLEANLY),
    ],
)
@pytest.mark.parametrize("capture", [True, False])
def test_transcript_capture_on_and_off(
    docker, settings, running, max_seconds, should_stop, recheck, exit_code, expected, capture
):
    settings.HARNESS_MAX_JOB_SECONDS = max_seconds
    docker.container_is_running.return_value = running
    docker.container_exit_code.return_value = exit_code

    outcome = _wait(
        docker,
        should_stop=(lambda: should_stop) if should_stop is not None else None,
        recheck_stop_after_exit=recheck,
        capture_transcript=capture,
    )

    assert outcome.result == expected
    if capture:
        assert outcome.transcript == "cli transcript"
        docker.capture_logs.assert_called_once_with(CONTAINER)
    else:
        assert outcome.transcript == ""
        docker.capture_logs.assert_not_called()


# --- teardown / remove / cleanup ----------------------------------------


def test_teardown_kills_only_a_container_still_running(docker, settings):
    settings.HARNESS_KILL_GRACE_SECONDS = 7
    docker.container_is_running.return_value = True
    container_lifecycle.teardown_container(CONTAINER, label="harness container")
    docker.stop_container.assert_called_once_with(CONTAINER, grace_seconds=7)
    docker.kill_container.assert_called_once_with(CONTAINER)

    docker.reset_mock()
    docker.container_is_running.return_value = False
    container_lifecycle.teardown_container(CONTAINER, label="harness container")
    docker.kill_container.assert_not_called()


def test_teardown_never_raises(docker):
    docker.stop_container.side_effect = TimeoutError("daemon stuck")
    container_lifecycle.teardown_container(CONTAINER, label="harness container")
    docker.kill_container.assert_not_called()


def test_safe_remove_reports_confirmation(docker):
    assert container_lifecycle.safe_remove_container(CONTAINER, label="harness container")
    docker.remove_container.side_effect = TimeoutError("daemon stuck")
    assert not container_lifecycle.safe_remove_container(CONTAINER, label="harness container")


def test_confirmed_cleanup_checks_the_filesystem(docker, tmp_path):
    present = tmp_path / "left-behind"
    present.mkdir()
    # `runner.cleanup_directory` is mocked (never raises, removes nothing):
    # only the filesystem check afterwards can confirm it.
    assert container_lifecycle.cleanup_directory(str(present), confirm=True, label="d") is False
    assert (
        container_lifecycle.cleanup_directory(str(tmp_path / "gone"), confirm=True, label="d")
        is True
    )


def test_confirmed_cleanup_does_not_swallow_errors(docker, tmp_path):
    docker.cleanup_directory.side_effect = OSError("boom")
    with pytest.raises(OSError):
        container_lifecycle.cleanup_directory(str(tmp_path), confirm=True, label="d")


def test_best_effort_cleanup_swallows_errors(docker, tmp_path):
    docker.cleanup_directory.side_effect = OSError("boom")
    assert container_lifecycle.cleanup_directory(str(tmp_path), confirm=False, label="d") is None
    docker.cleanup_directory.assert_called_once_with(str(tmp_path))


# --- each engine's own FailureReason mapping ------------------------------


def test_discovery_job_outcome_map():
    assert execution._WAIT_OUTCOME_STATUS == {
        WaitResult.TIMED_OUT: ("failed", HarnessJob.FailureReason.TIMEOUT),
        WaitResult.STOPPED: ("cancelled", HarnessJob.FailureReason.KILLED),
        WaitResult.EXITED_WITH_ERROR: ("failed", HarnessJob.FailureReason.RUNNER_ERROR),
        WaitResult.EXITED_CLEANLY: ("succeeded", ""),
    }


def test_implementation_job_outcome_map():
    reasons = HarnessImplementationJob.FailureReason
    assert implementation_execution._WAIT_OUTCOME_STATUS == {
        WaitResult.TIMED_OUT: ("failed", reasons.TIMEOUT),
        WaitResult.STOPPED: ("cancelled", reasons.KILLED),
        WaitResult.EXITED_WITH_ERROR: ("failed", reasons.RUNNER_ERROR),
        WaitResult.EXITED_CLEANLY: ("succeeded", ""),
    }


@pytest.mark.parametrize(
    ("result", "expected_status"),
    [
        (WaitResult.TIMED_OUT, "failed"),
        (WaitResult.STOPPED, "failed"),
        (WaitResult.EXITED_WITH_ERROR, "failed"),
        (WaitResult.EXITED_CLEANLY, "succeeded"),
    ],
)
def test_review_outcome_map(monkeypatch, result, expected_status):
    wait = mock.Mock(return_value=WaitOutcome(result, "t"))
    monkeypatch.setattr(review_execution.container_lifecycle, "wait_for_container", wait)

    assert review_execution._wait_for_review_container("c", time.monotonic(), job_id=None) == (
        expected_status,
        "t",
    )
    kwargs = wait.call_args.kwargs
    assert kwargs["should_stop"] is None
    assert kwargs["recheck_stop_after_exit"] is False
    assert kwargs["capture_transcript"] is True
