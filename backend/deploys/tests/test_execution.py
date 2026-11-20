"""The runner's state machine (brief D10) against a scripted remote
(`FakeRemote`) and a fake clock -- success, rollback paths, drift,
self-repo, config changes, cancellation, lost runners, output handling and
the work_log/audit written on every terminal state."""

from datetime import timedelta

import pytest
from django.utils import timezone

from approvals.services import approve_request
from audit.models import AuditLog
from core.channels import CHANNEL_WEB
from deploys import execution
from deploys.models import DeployRun
from deploys.services import DeployInvalidTransition, cancel_deploy_run, request_deploy
from deploys.tasks import recover_stale_deploy_runs
from deploys.tests.conftest import REPO, SELF_REPO, SHA_A, SHA_B
from memory.models import Memory

pytestmark = pytest.mark.django_db

S = DeployRun.Status
E = DeployRun.ErrorCode


def approved_run(owner, workspace, target, sha=SHA_B):
    request = request_deploy(
        workspace=workspace, actor=owner, target=target, sha=sha, channel=CHANNEL_WEB
    )
    approve_request(approval_request=request, actor=owner, channel=CHANNEL_WEB)
    return DeployRun.objects.get(approval_request=request)


def run_it(run):
    execution.execute_deploy_run(str(run.id))
    run.refresh_from_db()
    return run


def work_log_for(run):
    return Memory.objects.filter(
        workspace=run.workspace,
        memory_type=Memory.Type.WORK_LOG,
        content__contains=str(run.approval_request_id),
    )


def assert_terminal_bookkeeping(run):
    assert run.finished_at is not None
    assert run.claim_token is None and run.lease_expires_at is None
    assert AuditLog.objects.filter(action=f"deploy.{run.status}", resource_id=str(run.id)).exists()
    log = work_log_for(run).get()
    assert f"deploy: {run.status}" in log.title
    # Jarvis-authored facts only: never remote output.
    assert "healthy" not in log.content and "deployed" not in log.content


# --- happy path ---


def test_success_needs_two_consecutive_health_passes(owner, workspace, target, remote):
    run = run_it(approved_run(owner, workspace, target))
    assert run.status == S.SUCCEEDED
    assert run.error_code == ""
    assert remote.calls == [("status", None), ("deploy", SHA_B), ("health", None), ("health", None)]
    target.refresh_from_db()
    assert target.current_sha == SHA_B
    assert run.deploy_started_at is not None
    assert [c["verb_ok"] for c in run.health_checks] == [True, True]
    assert AuditLog.objects.filter(action="deploy.started", resource_id=str(run.id)).exists()
    assert_terminal_bookkeeping(run)


def test_health_checks_are_spaced_across_the_window(owner, workspace, target, remote, fake_clock):
    remote.health = [False, False, False]
    run_it(approved_run(owner, workspace, target))
    # 120 s window / 4 checks: every check 30 s apart.
    assert set(fake_clock.sleeps) == {30.0}


def test_one_blip_never_rolls_back(owner, workspace, target, remote):
    remote.health = [False, True, True]
    run = run_it(approved_run(owner, workspace, target))
    assert run.status == S.SUCCEEDED
    assert "rollback" not in remote.verbs()


# --- health failure and rollback ---


def test_three_consecutive_failures_roll_back_to_the_decided_sha(owner, workspace, target, remote):
    remote.health = [False, False, False, True, True]
    run = run_it(approved_run(owner, workspace, target))
    assert run.status == S.ROLLED_BACK
    assert run.error_code == E.HEALTH_CHECKS_FAILED
    assert ("rollback", SHA_A) in remote.calls
    assert remote.verbs().count("health") == 5
    assert [c["phase"] for c in run.health_checks] == ["deploy"] * 3 + ["rollback"] * 2
    target.refresh_from_db()
    assert target.current_sha == SHA_A
    assert run.rollback_started_at is not None
    assert AuditLog.objects.filter(action="deploy.rollback_started").exists()
    assert_terminal_bookkeeping(run)


def test_two_failures_then_a_pass_is_not_three_consecutive(owner, workspace, target, remote):
    remote.health = [False, False, True, True]
    run = run_it(approved_run(owner, workspace, target))
    assert run.status == S.SUCCEEDED


def test_alternating_results_fail_closed_when_the_window_elapses(owner, workspace, target, remote):
    remote.health = [True, False, True, False, True, True]
    run = run_it(approved_run(owner, workspace, target))
    assert run.status == S.ROLLED_BACK
    assert run.error_code == E.HEALTH_WINDOW_ELAPSED
    assert [c["verb_ok"] for c in run.health_checks if c["phase"] == "deploy"] == [
        True,
        False,
        True,
        False,
    ]


def test_health_url_failure_alone_is_degraded_never_rolled_back(
    owner, workspace, workspace_target_with_url, remote, url_results
):
    url_results.extend([False, False])
    run = run_it(approved_run(owner, workspace, workspace_target_with_url))
    assert run.status == S.SUCCEEDED_DEGRADED
    assert run.error_code == E.HEALTH_URL_FAILED
    assert "rollback" not in remote.verbs()
    assert [c["url_ok"] for c in run.health_checks] == [False, False]
    assert_terminal_bookkeeping(run)


def test_health_url_passing_with_the_verb_is_a_clean_success(
    owner, workspace, workspace_target_with_url, remote, url_results
):
    run = run_it(approved_run(owner, workspace, workspace_target_with_url))
    assert run.status == S.SUCCEEDED


def test_auto_rollback_off_ends_health_failed_without_a_rollback_verb(
    owner, workspace, target, remote
):
    target.auto_rollback = False
    target.save()
    remote.health = [False, False, False]
    run = run_it(approved_run(owner, workspace, target))
    assert run.status == S.HEALTH_FAILED
    assert "rollback" not in remote.verbs()
    assert run.rollback_started_at is None
    target.refresh_from_db()
    assert target.current_sha == SHA_B
    assert_terminal_bookkeeping(run)


def test_failed_rollback_command_is_rollback_failed(owner, workspace, target, remote):
    remote.health = [False, False, False]
    remote.rollback_exit = 1
    run = run_it(approved_run(owner, workspace, target))
    assert run.status == S.ROLLBACK_FAILED
    assert run.error_code == E.ROLLBACK_COMMAND_FAILED
    target.refresh_from_db()
    assert target.current_sha == ""  # unknown: a status refresh must come first
    assert_terminal_bookkeeping(run)


def test_failed_rollback_health_is_rollback_failed(owner, workspace, target, remote):
    remote.health_default = False
    run = run_it(approved_run(owner, workspace, target))
    assert run.status == S.ROLLBACK_FAILED
    assert run.error_code == E.ROLLBACK_HEALTH_FAILED
    assert remote.verbs().count("rollback") == 1
    # The rollback verb succeeded: the live checkout is the rollback SHA.
    target.refresh_from_db()
    assert target.current_sha == run.rollback_sha


def test_rollback_transport_failure_is_an_unknown_outcome(owner, workspace, target, remote):
    remote.health_default = False
    remote.transport_fail.add("rollback")
    run = run_it(approved_run(owner, workspace, target))
    assert run.status == S.ROLLBACK_FAILED
    assert run.error_code == E.ROLLBACK_OUTCOME_UNKNOWN
    assert remote.verbs().count("rollback") == 1
    target.refresh_from_db()
    assert target.current_sha == ""


# --- refusals before the deploy verb ---


def test_drift_touches_nothing(owner, workspace, target, remote):
    remote.live_sha = "d" * 40
    run = run_it(approved_run(owner, workspace, target))
    assert run.status == S.FAILED
    assert run.error_code == E.TARGET_DRIFTED
    assert remote.verbs() == ["status"]
    assert run.deploy_started_at is None
    assert_terminal_bookkeeping(run)


def test_self_repo_remote_is_refused(owner, workspace, target, remote):
    remote.remote = "https://github.com/akin941/jarvis-os"
    run = run_it(approved_run(owner, workspace, target))
    assert run.status == S.FAILED
    assert run.error_code == E.SELF_REPO
    assert remote.verbs() == ["status"]


def test_remote_of_another_repo_is_refused(owner, workspace, target, remote):
    remote.remote = "git@github.com:acme/other.git"
    run = run_it(approved_run(owner, workspace, target))
    assert run.error_code == E.REPO_MISMATCH
    assert remote.verbs() == ["status"]


def test_same_repo_in_another_spelling_is_accepted(owner, workspace, target, remote):
    remote.remote = "https://github.com/acme/adia-web"
    assert run_it(approved_run(owner, workspace, target)).status == S.SUCCEEDED


def test_unparseable_status_is_refused(owner, workspace, target, remote):
    remote.app = "other-app"
    run = run_it(approved_run(owner, workspace, target))
    assert run.error_code == E.STATUS_UNPARSEABLE


def test_config_hash_changed_after_approval_refuses_before_any_contact(
    owner, workspace, target, remote
):
    run = approved_run(owner, workspace, target)
    target.ssh_host = "198.51.100.9"
    target.save()
    run = run_it(run)
    assert run.status == S.FAILED
    assert run.error_code == E.TARGET_CHANGED
    assert remote.calls == []


def test_config_change_during_the_status_check_stops_before_deploy(
    owner, workspace, target, remote
):
    def edit():
        target.health_timeout_seconds = 60
        target.save()

    remote.hooks["status"] = edit
    run = run_it(approved_run(owner, workspace, target))
    assert run.error_code == E.TARGET_CHANGED
    assert remote.verbs() == ["status"]


def test_disabled_target_is_refused(owner, workspace, target, remote):
    run = approved_run(owner, workspace, target)
    target.enabled = False
    target.save()
    assert run_it(run).error_code == E.TARGET_DISABLED
    assert remote.calls == []


def test_self_repo_identifiers_unconfigured_fail_closed(owner, workspace, target, remote, settings):
    run = approved_run(owner, workspace, target)
    settings.HARNESS_SELF_REPO_IDENTIFIERS = []
    assert run_it(run).error_code == E.SELF_REPO_UNCONFIGURED
    assert remote.calls == []


def test_missing_key_fails_closed(owner, workspace, target, settings):
    # The real ssh.run_verb: it refuses before spawning (spawning would
    # hit the autouse `_forbidden` stub).
    run = approved_run(owner, workspace, target)
    settings.DEPLOY_SSH_KEY_PATH = ""
    run = run_it(run)
    assert run.status == S.FAILED
    assert run.error_code == E.SSH_KEY_MISSING


def test_status_transport_failure(owner, workspace, target, remote):
    remote.transport_fail.add("status")
    run = run_it(approved_run(owner, workspace, target))
    assert run.error_code == E.SSH_FAILED
    assert run.status == S.FAILED


# --- the deploy verb's own outcomes ---


@pytest.mark.parametrize(
    "exit_code, error",
    [
        (65, E.DEPLOY_REFUSED),
        (64, E.DEPLOY_REFUSED),
        (66, E.BUILD_FAILED),
        (69, E.REMOTE_UNAVAILABLE),
        (75, E.REMOTE_BUSY),
    ],
)
def test_untouched_deploy_refusals_fail_without_health_or_rollback(
    owner, workspace, target, remote, exit_code, error
):
    remote.deploy_exit = exit_code
    run = run_it(approved_run(owner, workspace, target))
    assert run.status == S.FAILED
    assert run.error_code == error
    assert remote.verbs() == ["status", "deploy"]
    target.refresh_from_db()
    assert target.current_sha == SHA_A


def test_deploy_transport_failure_is_unknown_outcome_never_retried(
    owner, workspace, target, remote
):
    remote.transport_fail.add("deploy")
    run = run_it(approved_run(owner, workspace, target))
    assert run.status == S.UNKNOWN_OUTCOME
    assert remote.verbs() == ["status", "deploy"]
    target.refresh_from_db()
    assert target.current_sha == ""
    # Nothing re-runs it: not the task, not the recovery scan.
    execution.execute_deploy_run(str(run.id))
    recover_stale_deploy_runs()
    assert remote.verbs() == ["status", "deploy"]
    assert_terminal_bookkeeping(run)


def test_failed_restart_that_still_passes_health_is_degraded(owner, workspace, target, remote):
    remote.deploy_exit = 1
    run = run_it(approved_run(owner, workspace, target))
    assert run.status == S.SUCCEEDED_DEGRADED
    assert run.error_code == E.DEPLOY_COMMAND_FAILED


def test_unexpected_error_after_the_deploy_verb_is_unknown_outcome(
    owner, workspace, target, remote, monkeypatch
):
    def boom(url):
        raise RuntimeError("bug")

    monkeypatch.setattr("deploys.execution.run_health_phase", lambda *a, **k: boom(None))
    run = run_it(approved_run(owner, workspace, target))
    assert run.status == S.UNKNOWN_OUTCOME
    assert run.error_code == E.UNEXPECTED_ERROR


def test_unexpected_error_before_the_deploy_verb_is_failed(
    owner, workspace, target, remote, monkeypatch
):
    monkeypatch.setattr(
        "deploys.execution.parse_status", lambda text: (_ for _ in ()).throw(RuntimeError())
    )
    run = run_it(approved_run(owner, workspace, target))
    assert run.status == S.FAILED
    assert run.error_code == E.UNEXPECTED_ERROR
    assert remote.verbs() == ["status"]


# --- cancel ---


def test_cancel_a_pending_run(owner, workspace, target, remote):
    run = approved_run(owner, workspace, target)
    cancel_deploy_run(run=run, actor=owner, channel=CHANNEL_WEB)
    run = run_it(run)
    assert run.status == S.CANCELLED
    assert remote.calls == []
    assert AuditLog.objects.get(action="deploy.cancelled").actor == owner
    assert_terminal_bookkeeping(run)


def test_cancel_during_the_status_check_prevents_the_deploy_verb(owner, workspace, target, remote):
    run = approved_run(owner, workspace, target)
    remote.hooks["status"] = lambda: cancel_deploy_run(run=run, actor=owner, channel=CHANNEL_WEB)
    run = run_it(run)
    assert run.status == S.CANCELLED
    assert remote.verbs() == ["status"]


def test_cancel_after_the_deploy_verb_is_refused(owner, workspace, target, remote):
    run = approved_run(owner, workspace, target)
    refusals = []

    def try_cancel():
        try:
            cancel_deploy_run(run=run, actor=owner, channel=CHANNEL_WEB)
        except DeployInvalidTransition as exc:
            refusals.append(exc)

    remote.hooks["deploy"] = try_cancel
    run = run_it(run)
    assert len(refusals) == 1
    assert run.status == S.SUCCEEDED


# --- lost runners ---


def _stale(run, status, *, deploy_started):
    run.status = status
    run.claim_token = "11111111-1111-1111-1111-111111111111"
    run.lease_expires_at = timezone.now() - timedelta(seconds=5)
    run.deploy_started_at = timezone.now() if deploy_started else None
    run.save()


@pytest.mark.parametrize("status", [S.DEPLOYING, S.HEALTH_CHECKING, S.ROLLING_BACK])
def test_runner_lost_after_the_deploy_verb_is_unknown_outcome(
    owner, workspace, target, remote, status
):
    run = approved_run(owner, workspace, target)
    _stale(run, status, deploy_started=True)
    result = recover_stale_deploy_runs()
    assert result == {"redispatched": 0, "lost": 1}
    run.refresh_from_db()
    assert run.status == S.UNKNOWN_OUTCOME
    assert run.error_code == E.RUNNER_LOST
    assert remote.calls == []
    target.refresh_from_db()
    assert target.current_sha == ""
    assert_terminal_bookkeeping(run)


def test_runner_lost_before_the_deploy_verb_is_redone(owner, workspace, target, remote):
    run = approved_run(owner, workspace, target)
    _stale(run, S.CHECKING, deploy_started=False)
    assert recover_stale_deploy_runs() == {"redispatched": 1, "lost": 0}
    run.refresh_from_db()
    assert run.status == S.SUCCEEDED


def test_live_lease_is_left_alone(owner, workspace, target, remote):
    run = approved_run(owner, workspace, target)
    run.status = S.HEALTH_CHECKING
    run.lease_expires_at = timezone.now() + timedelta(minutes=5)
    run.deploy_started_at = timezone.now()
    run.save()
    assert recover_stale_deploy_runs() == {"redispatched": 0, "lost": 0}


def test_a_stale_pending_run_is_redispatched(owner, workspace, target, remote):
    run = approved_run(owner, workspace, target)
    DeployRun.objects.filter(pk=run.pk).update(updated_at=timezone.now() - timedelta(minutes=5))
    assert recover_stale_deploy_runs()["redispatched"] == 1
    run.refresh_from_db()
    assert run.status == S.SUCCEEDED


def test_a_terminal_run_is_never_reclaimed(owner, workspace, target, remote):
    run = run_it(approved_run(owner, workspace, target))
    calls = list(remote.calls)
    assert execution.claim_deploy_run(str(run.id)) is None
    assert remote.calls == calls


# --- output ---


def test_output_is_bounded_and_redacted(owner, workspace, target, remote):
    remote.deploy_output = "DB_PASSWORD=hunter2\n" + "z" * 40000
    run = run_it(approved_run(owner, workspace, target))
    assert len(run.output) <= 16 * 1024
    assert "hunter2" not in run.output


def test_status_output_secrets_are_redacted(owner, workspace, target, remote):
    remote.live_sha = "e" * 40  # drift, so the status output is the whole log
    remote.status_extra = "token=abc123secret\n"
    run = run_it(approved_run(owner, workspace, target))
    assert "abc123secret" not in run.output
    assert "[REDACTED]" in run.output


# --- status refresh ---


def test_status_refresh_sets_current_sha(target, remote):
    remote.live_sha = SHA_B
    assert execution.refresh_target_status(str(target.id)) is None
    target.refresh_from_db()
    assert target.current_sha == SHA_B
    assert target.last_status_error == ""
    assert target.last_status_at is not None


@pytest.mark.parametrize(
    "remote_url, error",
    [(SELF_REPO, E.SELF_REPO), ("git@github.com:acme/other.git", E.REPO_MISMATCH)],
)
def test_status_refresh_refuses_a_wrong_repo(target, remote, remote_url, error):
    remote.remote = remote_url
    remote.live_sha = SHA_B
    assert execution.refresh_target_status(str(target.id)) == error
    target.refresh_from_db()
    assert target.current_sha == SHA_A
    assert target.last_status_error == error


def test_status_parser():
    good = f"app=adia-web\nsha={SHA_A}\nbranch=main\nremote={REPO}\nprevious=\n"
    assert execution.parse_status(good)["sha"] == SHA_A
    assert execution.parse_status("sha=abc\napp=x\nremote=y") is None
    assert execution.parse_status(f"app=adia-web\nsha={SHA_A}\n") is None
    # The first occurrence wins.
    assert execution.parse_status(good + f"sha={SHA_B}\n")["sha"] == SHA_A
