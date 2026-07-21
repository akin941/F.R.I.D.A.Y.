"""Every test here monkeypatches `harness.implementation_execution.runner`'s
functions -- no real `docker`/`git` subprocess (mirrors
`harness/tests/test_execution.py`'s identical discipline, CLAUDE.md rule
5's "LLM-calling code must be mockable", extended to this out-of-process
call)."""

import json
from datetime import timedelta
from decimal import Decimal
from unittest import mock

import pytest
from django.utils import timezone

from accounts.services import register_user
from ai_core.models import ModelUsage
from approvals.models import ApprovalRequest
from audit.models import AuditLog
from harness import implementation_execution
from harness.models import HarnessImplementationJob, HarnessRepo
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture(autouse=True)
def git_push_credential(settings):
    """`clone_repo_writable`'s own `clone_env` (Codex code-review P2,
    branch 2 round 2) now resolves via `runner._resolve_git_push_env()`,
    which raises if unconfigured -- mirrors
    `harness/tests/test_runner.py`'s identical fixture."""
    settings.HARNESS_GIT_SSH_KEY_PATH = "/tmp/fake-deploy-key"


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


@pytest.fixture
def repo(workspace):
    return HarnessRepo.objects.create(
        workspace=workspace,
        name="jarvis-os",
        git_url="git@example.com:x/y.git",
        enabled=True,
        test_command="pytest -q",
    )


def make_job(workspace, repo, **overrides):
    fields = {
        "workspace": workspace,
        "repo": repo,
        "goal": "fix the bug",
        "agent": HarnessImplementationJob.Agent.CLAUDE_CODE,
        "status": HarnessImplementationJob.Status.IMPLEMENTING,
        "created_by": None,
    }
    fields.update(overrides)
    return HarnessImplementationJob.objects.create(**fields)


def _wrapper_result(*, passed=True, exit_code=0, commit_sha="abc123", changed_files=None):
    return {
        "tests": {"ran": True, "exit_code": exit_code, "passed": passed, "command": "pytest -q"},
        "commit_sha": commit_sha,
        "changed_files": changed_files if changed_files is not None else ["foo.py"],
    }


def _claude_json(cost="0.01", input_tokens=10, output_tokens=5):
    return json.dumps(
        {
            "total_cost_usd": cost,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        }
    )


def _patch_happy_path_runner(monkeypatch, *, result, exit_code=0):
    monkeypatch.setattr(
        implementation_execution.runner,
        "clone_repo_writable",
        mock.Mock(return_value="/tmp/harness-worktree-x"),
    )
    monkeypatch.setattr(
        implementation_execution.runner, "resolve_commit_sha", mock.Mock(return_value="base-sha")
    )
    monkeypatch.setattr(
        implementation_execution.runner, "image_for_agent", mock.Mock(return_value="img:1")
    )
    monkeypatch.setattr(
        implementation_execution.runner, "launch_implementation_container", mock.Mock()
    )
    monkeypatch.setattr(
        implementation_execution.runner, "container_is_running", mock.Mock(side_effect=[False])
    )
    monkeypatch.setattr(
        implementation_execution.runner, "container_exit_code", mock.Mock(return_value=exit_code)
    )
    # A real CLI JSON blob by default (Codex code-review P2, branch 2
    # round 6: the fail-closed "usage unavailable" check now requires a
    # `usage_source: cli_reported` context_report to reach `pending_
    # review` at all -- the happy path must actually represent a run
    # with usable cost accounting, not an empty capture).
    monkeypatch.setattr(
        implementation_execution.runner, "capture_logs", mock.Mock(return_value=_claude_json())
    )
    monkeypatch.setattr(
        implementation_execution.runner,
        "read_implementation_result",
        mock.Mock(return_value=result),
    )
    monkeypatch.setattr(implementation_execution.runner, "remove_container", mock.Mock())
    monkeypatch.setattr(implementation_execution.runner, "cleanup_directory", mock.Mock())


class TestRunHarnessImplementationJobSuccess:
    def test_passing_tests_move_job_to_pending_review(self, workspace, owner, repo, monkeypatch):
        job = make_job(workspace, repo, created_by=owner)
        _patch_happy_path_runner(monkeypatch, result=_wrapper_result())
        with mock.patch.object(implementation_execution, "dispatch_harness_review") as dispatch:
            implementation_execution.run_harness_implementation_job(job.id)
            dispatch.assert_called_once()
        job.refresh_from_db()
        assert job.status == HarnessImplementationJob.Status.PENDING_REVIEW
        assert job.base_commit_sha == "base-sha"
        assert job.produced_commit_sha == "abc123"
        assert job.changed_files == ["foo.py"]
        assert job.test_result == {
            "ran": True,
            "exit_code": 0,
            "passed": True,
            "command": "pytest -q",
        }
        assert job.worktree_path == "/tmp/harness-worktree-x"
        assert job.worktree_expires_at is not None
        assert job.diff_summary != ""

    def test_clones_using_the_snapshotted_repo_url_not_the_live_fk(
        self, workspace, owner, repo, monkeypatch
    ):
        """Codex code-review P1, branch 2 round 4: editing `HarnessRepo.
        git_url` after the job was created must not change what gets
        cloned -- execution must use `job.repo_git_url` (snapshotted),
        never a live `job.repo.git_url` read."""
        job = make_job(workspace, repo, created_by=owner)
        repo.git_url = "git@github.com:akin941/jarvis-os.git"
        repo.save(update_fields=["git_url"])
        _patch_happy_path_runner(monkeypatch, result=_wrapper_result())
        with mock.patch.object(implementation_execution, "dispatch_harness_review"):
            implementation_execution.run_harness_implementation_job(job.id)
        clone_call = implementation_execution.runner.clone_repo_writable.call_args
        assert clone_call.kwargs["git_url"] == "git@example.com:x/y.git"

    def test_worktree_is_not_cleaned_up_on_success(self, workspace, owner, repo, monkeypatch):
        job = make_job(workspace, repo, created_by=owner)
        _patch_happy_path_runner(monkeypatch, result=_wrapper_result())
        with mock.patch.object(implementation_execution, "dispatch_harness_review"):
            implementation_execution.run_harness_implementation_job(job.id)
        implementation_execution.runner.cleanup_directory.assert_not_called()

    def test_records_model_usage_on_success(self, workspace, owner, repo, monkeypatch):
        job = make_job(workspace, repo, created_by=owner)
        _patch_happy_path_runner(monkeypatch, result=_wrapper_result())
        with mock.patch.object(implementation_execution, "dispatch_harness_review"):
            implementation_execution.run_harness_implementation_job(job.id)
        usage = ModelUsage.objects.get(purpose="harness.implementation")
        assert usage.success is True

    def test_captures_logs_before_removing_the_container(self, workspace, owner, repo, monkeypatch):
        """Codex code-review P2, branch 2 round 1: an earlier version
        called `capture_logs` AFTER `_safe_remove_container` in the
        `finally` block -- `docker logs` can't read anything from an
        already-removed container, so every successful run silently
        recorded zero cost/tokens. A shared `mock.Mock` manager records
        call order across both mocks directly, rather than trusting the
        parsed `ModelUsage` values alone (which could coincidentally look
        right even with the wrong ordering if the mocks defaulted
        oddly)."""
        job = make_job(workspace, repo, created_by=owner)
        manager = mock.Mock()
        capture_mock = mock.Mock(
            return_value=json.dumps(
                {"total_cost_usd": "0.02", "usage": {"input_tokens": 10, "output_tokens": 5}}
            )
        )
        remove_mock = mock.Mock()
        manager.attach_mock(capture_mock, "capture_logs")
        manager.attach_mock(remove_mock, "remove_container")
        _patch_happy_path_runner(monkeypatch, result=_wrapper_result())
        monkeypatch.setattr(implementation_execution.runner, "capture_logs", capture_mock)
        monkeypatch.setattr(implementation_execution.runner, "remove_container", remove_mock)
        with mock.patch.object(implementation_execution, "dispatch_harness_review"):
            implementation_execution.run_harness_implementation_job(job.id)
        assert [c[0] for c in manager.mock_calls] == ["capture_logs", "remove_container"]
        usage = ModelUsage.objects.get(purpose="harness.implementation")
        assert usage.input_tokens == 10
        assert usage.output_tokens == 5


class TestClaimFencing:
    """Codex code-review P1, branch 2 round 3: a Celery redelivery (or
    `dispatch()` firing twice) must not let a second invocation claim a
    job that's already being worked on -- `implementing` alone doesn't
    distinguish "just approved, not yet claimed" from "actively running",
    so `started_at`/`push_started_at` being already non-null is the
    actual fence."""

    def test_implementation_job_already_claimed_is_not_reclaimed(
        self, workspace, owner, repo, monkeypatch
    ):
        job = make_job(workspace, repo, created_by=owner)
        implementation_execution._claim_implementation_job(job.id)
        clone_mock = mock.Mock()
        monkeypatch.setattr(implementation_execution.runner, "clone_repo_writable", clone_mock)
        implementation_execution.run_harness_implementation_job(job.id)
        clone_mock.assert_not_called()

    def test_push_job_already_claimed_is_not_reclaimed(self, workspace, owner, repo):
        job = make_job(
            workspace,
            repo,
            created_by=owner,
            status=HarnessImplementationJob.Status.PUSHING,
            worktree_path="/tmp/harness-worktree-x",
        )
        implementation_execution._claim_push_job(job.id)
        assert implementation_execution._claim_push_job(job.id) is None


class TestRunHarnessImplementationJobFailurePaths:
    def test_tests_failed_fails_job_closed_and_cleans_up_worktree(
        self, workspace, owner, repo, monkeypatch
    ):
        job = make_job(workspace, repo, created_by=owner)
        _patch_happy_path_runner(monkeypatch, result=_wrapper_result(passed=False, exit_code=1))
        implementation_execution.run_harness_implementation_job(job.id)
        job.refresh_from_db()
        assert job.status == HarnessImplementationJob.Status.FAILED
        assert job.failure_reason == HarnessImplementationJob.FailureReason.TESTS_FAILED
        implementation_execution.runner.cleanup_directory.assert_called_once_with(
            "/tmp/harness-worktree-x"
        )
        # Codex code-review P2, branch 4 round 3: round 2's own fix
        # persists `worktree_path` as soon as the clone succeeds, but
        # `_fail_and_cleanup` (every non-success terminal path) didn't
        # clear it once the directory was actually removed -- the row
        # was left FAILED while still pointing at a directory that no
        # longer existed.
        assert job.worktree_path == ""

    def test_fail_and_cleanup_preserves_worktree_path_when_cleanup_fails(
        self, workspace, owner, repo, monkeypatch, tmp_path
    ):
        """Codex code-review P2, branch 4 round 3 (companion to the test
        above): a genuinely failed cleanup must leave `worktree_path`
        populated, not clear it -- that's what lets
        `_sweep_leaked_terminal_worktree` (this same round) find and
        retry it later."""
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        job = make_job(workspace, repo, created_by=owner)
        _patch_happy_path_runner(monkeypatch, result=_wrapper_result(passed=False, exit_code=1))
        implementation_execution.runner.clone_repo_writable.return_value = str(worktree)
        # A no-op stand-in for `runner.cleanup_directory` -- the real
        # directory is deliberately left behind so `os.path.exists`
        # finds it still there afterward, mirroring the push-side test
        # of the identical confirmed-cleanup discipline.
        monkeypatch.setattr(implementation_execution.runner, "cleanup_directory", mock.Mock())
        implementation_execution.run_harness_implementation_job(job.id)
        job.refresh_from_db()
        assert job.status == HarnessImplementationJob.Status.FAILED
        assert job.worktree_path == str(worktree)

    def test_missing_result_file_fails_closed(self, workspace, owner, repo, monkeypatch):
        job = make_job(workspace, repo, created_by=owner)
        _patch_happy_path_runner(monkeypatch, result=_wrapper_result())
        implementation_execution.runner.read_implementation_result.side_effect = (
            implementation_execution.runner.RunnerError("missing")
        )
        implementation_execution.run_harness_implementation_job(job.id)
        job.refresh_from_db()
        assert job.status == HarnessImplementationJob.Status.FAILED
        assert job.failure_reason == HarnessImplementationJob.FailureReason.TEST_RESULT_UNAVAILABLE

    def test_malformed_result_shape_fails_closed(self, workspace, owner, repo, monkeypatch):
        job = make_job(workspace, repo, created_by=owner)
        _patch_happy_path_runner(
            monkeypatch, result={"tests": {"ran": True}}
        )  # no exit_code/commit_sha
        implementation_execution.run_harness_implementation_job(job.id)
        job.refresh_from_db()
        assert job.status == HarnessImplementationJob.Status.FAILED
        assert job.failure_reason == HarnessImplementationJob.FailureReason.TEST_RESULT_UNAVAILABLE

    def test_tests_not_ran_fails_closed(self, workspace, owner, repo, monkeypatch):
        job = make_job(workspace, repo, created_by=owner)
        result = _wrapper_result()
        result["tests"]["ran"] = False
        _patch_happy_path_runner(monkeypatch, result=result)
        implementation_execution.run_harness_implementation_job(job.id)
        job.refresh_from_db()
        assert job.failure_reason == HarnessImplementationJob.FailureReason.TEST_RESULT_UNAVAILABLE

    def test_launch_runner_error_fails_closed(self, workspace, owner, repo, monkeypatch):
        job = make_job(workspace, repo, created_by=owner)
        monkeypatch.setattr(
            implementation_execution.runner,
            "clone_repo_writable",
            mock.Mock(side_effect=implementation_execution.runner.RunnerError("clone failed")),
        )
        monkeypatch.setattr(implementation_execution.runner, "remove_container", mock.Mock())
        implementation_execution.run_harness_implementation_job(job.id)
        job.refresh_from_db()
        assert job.status == HarnessImplementationJob.Status.FAILED
        assert job.failure_reason == HarnessImplementationJob.FailureReason.RUNNER_ERROR

    def test_persists_worktree_path_immediately_after_clone(
        self, workspace, owner, repo, monkeypatch
    ):
        """Codex code-review P2, branch 4 round 2: `job.worktree_path` was
        only ever written to the DB on the success path, at the very end
        of this function. A worker that hard-crashes anywhere after the
        clone succeeds (no exception this function's own try/except can
        catch -- a SIGKILL/OOM) would leave the DB field empty while a
        real directory sits on disk -- the recovery scan's own worker-
        crashed case reads this exact field to know what to clean up, and
        has no access to a dead process's local variables. Persisted
        immediately once the clone returns, not only on success."""
        job = make_job(workspace, repo, created_by=owner)
        monkeypatch.setattr(
            implementation_execution.runner,
            "clone_repo_writable",
            mock.Mock(return_value="/tmp/harness-worktree-x"),
        )

        def _boom(*args, **kwargs):
            job.refresh_from_db()
            assert job.worktree_path == "/tmp/harness-worktree-x"
            raise RuntimeError("worker crashed mid-run")

        monkeypatch.setattr(
            implementation_execution.runner, "resolve_commit_sha", mock.Mock(side_effect=_boom)
        )
        with pytest.raises(RuntimeError):
            implementation_execution.run_harness_implementation_job(job.id)

    def test_missing_ssh_credential_fails_closed_before_cloning(
        self, workspace, owner, repo, monkeypatch, settings
    ):
        """Codex code-review P2, branch 2 round 2: the writable clone now
        resolves the same SSH credential push needs -- an unconfigured
        key must fail the job closed, not silently fall back to whatever
        ambient SSH access the runner host happens to have."""
        settings.HARNESS_GIT_SSH_KEY_PATH = ""
        job = make_job(workspace, repo, created_by=owner)
        clone_mock = mock.Mock()
        monkeypatch.setattr(implementation_execution.runner, "clone_repo_writable", clone_mock)
        monkeypatch.setattr(implementation_execution.runner, "remove_container", mock.Mock())
        implementation_execution.run_harness_implementation_job(job.id)
        clone_mock.assert_not_called()
        job.refresh_from_db()
        assert job.status == HarnessImplementationJob.Status.FAILED
        assert job.failure_reason == HarnessImplementationJob.FailureReason.RUNNER_ERROR

    def test_over_budget_cost_fails_closed_with_real_cost_recorded(
        self, workspace, owner, repo, monkeypatch, settings
    ):
        """Codex code-review P2, branch 2 round 2: an otherwise-passing
        run whose reported cost exceeds `HARNESS_MAX_JOB_COST` must fail
        closed as `budget_exceeded` -- mirrors `harness.execution.
        _finalize`'s identical brake -- and the REAL cost (not zero)
        must still be recorded, since it genuinely was spent."""
        settings.HARNESS_MAX_JOB_COST = "0.001"
        job = make_job(workspace, repo, created_by=owner)
        _patch_happy_path_runner(monkeypatch, result=_wrapper_result())
        monkeypatch.setattr(
            implementation_execution.runner,
            "capture_logs",
            mock.Mock(
                return_value=json.dumps(
                    {"total_cost_usd": "5.00", "usage": {"input_tokens": 100, "output_tokens": 50}}
                )
            ),
        )
        implementation_execution.run_harness_implementation_job(job.id)
        job.refresh_from_db()
        assert job.status == HarnessImplementationJob.Status.FAILED
        assert job.failure_reason == HarnessImplementationJob.FailureReason.BUDGET_EXCEEDED
        usage = ModelUsage.objects.get(purpose="harness.implementation")
        assert usage.success is False
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50
        assert usage.estimated_cost == Decimal("5.00")

    def test_unavailable_usage_fails_closed_instead_of_a_free_success(
        self, workspace, owner, repo, monkeypatch
    ):
        """Codex code-review P2, branch 2 round 6: empty/unparseable
        Docker logs must not let an otherwise-passing run through as a
        successful free run -- mirrors `harness.execution._finalize`'s
        identical `usage_source != "cli_reported"` fail-closed check."""
        job = make_job(workspace, repo, created_by=owner)
        _patch_happy_path_runner(monkeypatch, result=_wrapper_result())
        monkeypatch.setattr(
            implementation_execution.runner, "capture_logs", mock.Mock(return_value="")
        )
        implementation_execution.run_harness_implementation_job(job.id)
        job.refresh_from_db()
        assert job.status == HarnessImplementationJob.Status.FAILED
        assert job.failure_reason == HarnessImplementationJob.FailureReason.RUNNER_ERROR
        usage = ModelUsage.objects.get(purpose="harness.implementation")
        assert usage.success is False

    def test_uses_the_snapshotted_test_command_not_the_live_fk(
        self, workspace, owner, repo, monkeypatch
    ):
        """Codex code-review P2, branch 2 round 6: editing `HarnessRepo.
        test_command` to blank/whitespace after the job was created must
        not change what the wrapper actually runs -- a shell treats
        whitespace as a no-op that exits 0, letting verification be
        silently skipped."""
        job = make_job(workspace, repo, created_by=owner)
        repo.test_command = "   "
        repo.save(update_fields=["test_command"])
        _patch_happy_path_runner(monkeypatch, result=_wrapper_result())
        with mock.patch.object(implementation_execution, "dispatch_harness_review"):
            implementation_execution.run_harness_implementation_job(job.id)
        launch_call = implementation_execution.runner.launch_implementation_container.call_args
        assert launch_call.kwargs["test_command"] == "pytest -q"

    def test_cancel_requested_during_wait_marks_cancelled(
        self, workspace, owner, repo, monkeypatch
    ):
        job = make_job(workspace, repo, created_by=owner, cancel_requested=True)
        _patch_happy_path_runner(monkeypatch, result=_wrapper_result())
        monkeypatch.setattr(
            implementation_execution.runner, "container_is_running", mock.Mock(side_effect=[True])
        )
        monkeypatch.setattr(implementation_execution.runner, "stop_container", mock.Mock())
        monkeypatch.setattr(implementation_execution.runner, "kill_container", mock.Mock())
        implementation_execution.run_harness_implementation_job(job.id)
        job.refresh_from_db()
        assert job.status == HarnessImplementationJob.Status.CANCELLED
        assert job.failure_reason == HarnessImplementationJob.FailureReason.KILLED

    def test_cancel_landing_after_wait_loop_still_prevents_pending_review(
        self, workspace, owner, repo, monkeypatch
    ):
        """Codex code-review P2, branch 2 round 5: `implementing` is a
        flag-only-cancel status (see `cancel_harness_implementation_job`)
        -- a cancel landing in the gap between
        `_wait_for_implementation_container`'s own last check and the
        later transaction that would otherwise commit `pending_review`
        must still be caught, since the status never changed in that
        window. Simulated by flipping `cancel_requested` as a side effect
        of `read_implementation_result` -- a call that happens strictly
        AFTER the wait loop's own last cancel check and BEFORE the
        commit-or-not decision this test targets."""
        job = make_job(workspace, repo, created_by=owner)
        _patch_happy_path_runner(monkeypatch, result=_wrapper_result())

        def flip_cancel_and_return_result(*args, **kwargs):
            HarnessImplementationJob.objects.filter(pk=job.id).update(cancel_requested=True)
            return _wrapper_result()

        monkeypatch.setattr(
            implementation_execution.runner,
            "read_implementation_result",
            mock.Mock(side_effect=flip_cancel_and_return_result),
        )
        implementation_execution.run_harness_implementation_job(job.id)
        job.refresh_from_db()
        assert job.status == HarnessImplementationJob.Status.CANCELLED
        assert job.failure_reason == HarnessImplementationJob.FailureReason.KILLED
        # The worktree must still be cleaned up, not retained for a review
        # that will never happen for a cancelled job.
        implementation_execution.runner.cleanup_directory.assert_called_once_with(
            "/tmp/harness-worktree-x"
        )

    def test_wrong_status_is_a_safe_no_op(self, workspace, owner, repo, monkeypatch):
        job = make_job(
            workspace, repo, created_by=owner, status=HarnessImplementationJob.Status.PENDING_GATE_A
        )
        clone_mock = mock.Mock()
        monkeypatch.setattr(implementation_execution.runner, "clone_repo_writable", clone_mock)
        implementation_execution.run_harness_implementation_job(job.id)
        clone_mock.assert_not_called()

    def test_failure_writes_audit_row(self, workspace, owner, repo, monkeypatch):
        job = make_job(workspace, repo, created_by=owner)
        _patch_happy_path_runner(monkeypatch, result=_wrapper_result(passed=False, exit_code=1))
        implementation_execution.run_harness_implementation_job(job.id)
        entry = AuditLog.objects.get(action="harness.implementation_job_failed")
        assert entry.resource_id == str(job.id)


class TestRunHarnessPushJob:
    def _patch_push_runner(self, monkeypatch, *, push_raises=None):
        monkeypatch.setattr(implementation_execution.runner, "create_bundle", mock.Mock())
        push_mock = mock.Mock()
        if push_raises is not None:
            push_mock.side_effect = push_raises
        monkeypatch.setattr(implementation_execution.runner, "push_bundled_commit", push_mock)
        monkeypatch.setattr(implementation_execution.runner, "cleanup_directory", mock.Mock())

    def test_successful_push_marks_job_succeeded_and_clears_worktree(
        self, workspace, owner, repo, monkeypatch
    ):
        job = make_job(
            workspace,
            repo,
            created_by=owner,
            status=HarnessImplementationJob.Status.PUSHING,
            worktree_path="/tmp/harness-worktree-x",
            base_commit_sha="base-sha",
            produced_commit_sha="abc123",
        )
        self._patch_push_runner(monkeypatch)
        implementation_execution.run_harness_push_job(job.id)
        job.refresh_from_db()
        assert job.status == HarnessImplementationJob.Status.SUCCEEDED
        assert job.worktree_path == ""
        assert job.push_result["pushed"] is True
        # cleanup_directory is called twice: once for the ephemeral bundle
        # tempdir, once for the worktree itself -- both expected.
        implementation_execution.runner.cleanup_directory.assert_any_call("/tmp/harness-worktree-x")

    def test_cancel_requested_before_push_skips_the_network_push(
        self, workspace, owner, repo, monkeypatch
    ):
        """Codex code-review P1, branch 2 round 2: `pushing` has no
        polling loop -- a job cancelled after Gate B approval only ever
        set `cancel_requested`, and nothing previously re-checked it
        before calling `push_bundled_commit`. The actual network push
        must never run once cancellation is observed."""
        job = make_job(
            workspace,
            repo,
            created_by=owner,
            status=HarnessImplementationJob.Status.PUSHING,
            worktree_path="/tmp/harness-worktree-x",
            base_commit_sha="base-sha",
            produced_commit_sha="abc123",
            cancel_requested=True,
        )
        self._patch_push_runner(monkeypatch)
        implementation_execution.run_harness_push_job(job.id)
        implementation_execution.runner.push_bundled_commit.assert_not_called()
        job.refresh_from_db()
        assert job.status == HarnessImplementationJob.Status.CANCELLED
        assert job.failure_reason == HarnessImplementationJob.FailureReason.KILLED
        assert job.worktree_path == ""

    def test_push_failure_fails_job_closed_and_clears_worktree(
        self, workspace, owner, repo, monkeypatch
    ):
        job = make_job(
            workspace,
            repo,
            created_by=owner,
            status=HarnessImplementationJob.Status.PUSHING,
            worktree_path="/tmp/harness-worktree-x",
            base_commit_sha="base-sha",
            produced_commit_sha="abc123",
        )
        self._patch_push_runner(
            monkeypatch, push_raises=implementation_execution.runner.RunnerError("push failed")
        )
        implementation_execution.run_harness_push_job(job.id)
        job.refresh_from_db()
        assert job.status == HarnessImplementationJob.Status.FAILED
        assert job.failure_reason == HarnessImplementationJob.FailureReason.PUSH_FAILED
        assert job.worktree_path == ""
        assert job.push_result["pushed"] is False

    def test_missing_worktree_fails_closed_without_calling_runner(
        self, workspace, owner, repo, monkeypatch
    ):
        job = make_job(
            workspace,
            repo,
            created_by=owner,
            status=HarnessImplementationJob.Status.PUSHING,
            worktree_path="",
        )
        bundle_mock = mock.Mock()
        monkeypatch.setattr(implementation_execution.runner, "create_bundle", bundle_mock)
        implementation_execution.run_harness_push_job(job.id)
        bundle_mock.assert_not_called()
        job.refresh_from_db()
        assert job.status == HarnessImplementationJob.Status.FAILED
        assert job.failure_reason == HarnessImplementationJob.FailureReason.PUSH_FAILED

    def test_wrong_status_is_a_safe_no_op(self, workspace, owner, repo, monkeypatch):
        job = make_job(
            workspace, repo, created_by=owner, status=HarnessImplementationJob.Status.PENDING_GATE_B
        )
        bundle_mock = mock.Mock()
        monkeypatch.setattr(implementation_execution.runner, "create_bundle", bundle_mock)
        implementation_execution.run_harness_push_job(job.id)
        bundle_mock.assert_not_called()

    def test_expired_worktree_fails_closed_without_pushing(
        self, workspace, owner, repo, monkeypatch
    ):
        """Codex code-review P2, branch 2 round 4: Gate B approved after
        `worktree_expires_at` has passed must never push -- the retention
        deadline exists precisely to bound how long a worktree is trusted."""
        job = make_job(
            workspace,
            repo,
            created_by=owner,
            status=HarnessImplementationJob.Status.PUSHING,
            worktree_path="/tmp/harness-worktree-x",
            base_commit_sha="base-sha",
            produced_commit_sha="abc123",
            worktree_expires_at=timezone.now() - timedelta(hours=1),
        )
        bundle_mock = mock.Mock()
        monkeypatch.setattr(implementation_execution.runner, "create_bundle", bundle_mock)
        monkeypatch.setattr(implementation_execution.runner, "cleanup_directory", mock.Mock())
        implementation_execution.run_harness_push_job(job.id)
        bundle_mock.assert_not_called()
        job.refresh_from_db()
        assert job.status == HarnessImplementationJob.Status.FAILED
        assert job.failure_reason == HarnessImplementationJob.FailureReason.WORKTREE_EXPIRED
        assert job.worktree_path == ""

    def test_worktree_path_preserved_when_push_cleanup_fails(
        self, workspace, owner, repo, monkeypatch, tmp_path
    ):
        """Codex code-review P2, branch 2 rounds 4-5: `runner.
        cleanup_directory` is `shutil.rmtree(path, ignore_errors=True)` --
        it never raises, so confirmation has to come from checking the
        filesystem afterward, not from catching an exception. A real,
        left-behind directory (rather than mocking `cleanup_directory` to
        raise) is what actually exercises that check."""
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        job = make_job(
            workspace,
            repo,
            created_by=owner,
            status=HarnessImplementationJob.Status.PUSHING,
            worktree_path=str(worktree),
            base_commit_sha="base-sha",
            produced_commit_sha="abc123",
        )
        self._patch_push_runner(monkeypatch)
        # A no-op stand-in for `runner.cleanup_directory` -- the real
        # directory is deliberately left behind so `os.path.exists` finds
        # it still there afterward.
        monkeypatch.setattr(implementation_execution.runner, "cleanup_directory", mock.Mock())
        implementation_execution.run_harness_push_job(job.id)
        job.refresh_from_db()
        assert job.status == HarnessImplementationJob.Status.SUCCEEDED
        assert job.worktree_path == str(worktree)


class TestRecoveryScan:
    """Phase 3 branch 4. Mirrors `harness/tests/test_execution.py`'s
    `TestRecoveryScan` class exactly, one job type over (two active
    phases instead of one, plus the worktree-retention backstop)."""

    def test_redispatches_implementing_job_whose_dispatch_was_lost(
        self, workspace, owner, repo, monkeypatch, settings
    ):
        settings.HARNESS_DISPATCH_RECOVERY_GRACE_SECONDS = 1
        job = make_job(
            workspace, repo, created_by=owner, status=HarnessImplementationJob.Status.IMPLEMENTING
        )
        stale = timezone.now() - timedelta(seconds=100)
        HarnessImplementationJob.objects.filter(pk=job.pk).update(updated_at=stale)
        dispatch_mock = mock.Mock()
        monkeypatch.setattr("harness.tools.dispatch_harness_implement_execution", dispatch_mock)

        implementation_execution.recover_stale_harness_implementation_jobs()

        dispatch_mock.assert_called_once_with(str(job.id))

    def test_does_not_redispatch_a_freshly_transitioned_implementing_job(
        self, workspace, owner, repo, monkeypatch, settings
    ):
        settings.HARNESS_DISPATCH_RECOVERY_GRACE_SECONDS = 300
        make_job(
            workspace, repo, created_by=owner, status=HarnessImplementationJob.Status.IMPLEMENTING
        )
        dispatch_mock = mock.Mock()
        monkeypatch.setattr("harness.tools.dispatch_harness_implement_execution", dispatch_mock)

        implementation_execution.recover_stale_harness_implementation_jobs()

        dispatch_mock.assert_not_called()

    def test_does_not_redispatch_an_implementing_job_already_claimed(
        self, workspace, owner, repo, monkeypatch, settings
    ):
        """`started_at` set means a worker already claimed it -- this is
        case 2's territory (worker-crashed), not case 1's (lost
        dispatch); the two cases must not overlap."""
        settings.HARNESS_DISPATCH_RECOVERY_GRACE_SECONDS = 1
        job = make_job(
            workspace,
            repo,
            created_by=owner,
            status=HarnessImplementationJob.Status.IMPLEMENTING,
            started_at=timezone.now(),
        )
        stale = timezone.now() - timedelta(seconds=100)
        HarnessImplementationJob.objects.filter(pk=job.pk).update(updated_at=stale)
        dispatch_mock = mock.Mock()
        monkeypatch.setattr("harness.tools.dispatch_harness_implement_execution", dispatch_mock)
        monkeypatch.setattr(implementation_execution.runner, "cleanup_directory", mock.Mock())

        implementation_execution.recover_stale_harness_implementation_jobs()

        dispatch_mock.assert_not_called()

    def test_recovers_implementing_job_whose_worker_crashed(
        self, workspace, owner, repo, monkeypatch, settings, tmp_path
    ):
        settings.HARNESS_MAX_JOB_SECONDS = 1
        settings.HARNESS_RECOVERY_GRACE_SECONDS = 1
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        stale_started = timezone.now() - timedelta(seconds=100)
        job = make_job(
            workspace,
            repo,
            created_by=owner,
            status=HarnessImplementationJob.Status.IMPLEMENTING,
            started_at=stale_started,
            container_id="orphan-impl-1",
            worktree_path=str(worktree),
        )
        stop_mock = mock.Mock()
        monkeypatch.setattr(implementation_execution.runner, "stop_container", stop_mock)
        monkeypatch.setattr(
            implementation_execution.runner, "container_is_running", mock.Mock(return_value=False)
        )
        remove_mock = mock.Mock()
        monkeypatch.setattr(implementation_execution.runner, "remove_container", remove_mock)

        implementation_execution.recover_stale_harness_implementation_jobs()

        job.refresh_from_db()
        assert job.status == HarnessImplementationJob.Status.FAILED
        assert job.failure_reason == HarnessImplementationJob.FailureReason.TIMEOUT
        assert job.container_id == ""
        stop_mock.assert_called_once()
        remove_mock.assert_called_once_with("orphan-impl-1")
        assert not worktree.exists()

    def test_claim_does_not_delete_worktree_of_a_job_that_already_succeeded(
        self, workspace, owner, repo, monkeypatch, tmp_path
    ):
        """Codex code-review P2, branch 4 round 4: an earlier version
        read the stale job unlocked, tore down its container and deleted
        its worktree, and only THEN did the real, locked status check --
        so a live worker that had ALREADY finished (moved to
        `pending_review`) between the scan's own stale-id query and this
        specific recovery attempt would still have its now-needed
        worktree destroyed, even though the resulting DB write would
        have correctly no-op'd. The claim (re-check + transition,
        atomically, before ANY cleanup) must find nothing to do at all
        in this case."""
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        stale_started = timezone.now() - timedelta(seconds=100)
        job = make_job(
            workspace,
            repo,
            created_by=owner,
            status=HarnessImplementationJob.Status.PENDING_REVIEW,
            started_at=stale_started,
            container_id="orphan-impl-1",
            worktree_path=str(worktree),
        )
        remove_mock = mock.Mock()
        monkeypatch.setattr(implementation_execution.runner, "remove_container", remove_mock)
        stop_mock = mock.Mock()
        monkeypatch.setattr(implementation_execution.runner, "stop_container", stop_mock)

        threshold = timezone.now() - timedelta(seconds=1)
        implementation_execution._recover_stale_implementing_job(job.id, threshold)

        remove_mock.assert_not_called()
        stop_mock.assert_not_called()
        job.refresh_from_db()
        assert job.status == HarnessImplementationJob.Status.PENDING_REVIEW
        assert job.worktree_path == str(worktree)
        assert worktree.exists()

    def test_does_not_recover_an_implementing_job_still_within_its_window(
        self, workspace, owner, repo, monkeypatch, settings
    ):
        settings.HARNESS_MAX_JOB_SECONDS = 1800
        settings.HARNESS_RECOVERY_GRACE_SECONDS = 300
        job = make_job(
            workspace,
            repo,
            created_by=owner,
            status=HarnessImplementationJob.Status.IMPLEMENTING,
            started_at=timezone.now(),
        )
        implementation_execution.recover_stale_harness_implementation_jobs()
        job.refresh_from_db()
        assert job.status == HarnessImplementationJob.Status.IMPLEMENTING

    def test_redispatches_pushing_job_whose_dispatch_was_lost(
        self, workspace, owner, repo, monkeypatch, settings
    ):
        settings.HARNESS_DISPATCH_RECOVERY_GRACE_SECONDS = 1
        job = make_job(
            workspace, repo, created_by=owner, status=HarnessImplementationJob.Status.PUSHING
        )
        stale = timezone.now() - timedelta(seconds=100)
        HarnessImplementationJob.objects.filter(pk=job.pk).update(updated_at=stale)
        dispatch_mock = mock.Mock()
        monkeypatch.setattr("harness.tools.dispatch_harness_push_execution", dispatch_mock)

        implementation_execution.recover_stale_harness_implementation_jobs()

        dispatch_mock.assert_called_once_with(str(job.id))

    def test_recovers_pushing_job_whose_worker_crashed(
        self, workspace, owner, repo, monkeypatch, settings, tmp_path
    ):
        """No container involved in the push phase at all -- the real
        `git push` is a host-side operation (branch 1's own design), so
        recovery here only needs to fail the job and clean up the
        worktree, never a Docker teardown."""
        settings.HARNESS_MAX_JOB_SECONDS = 1
        settings.HARNESS_RECOVERY_GRACE_SECONDS = 1
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        stale_started = timezone.now() - timedelta(seconds=100)
        job = make_job(
            workspace,
            repo,
            created_by=owner,
            status=HarnessImplementationJob.Status.PUSHING,
            push_started_at=stale_started,
            worktree_path=str(worktree),
        )

        implementation_execution.recover_stale_harness_implementation_jobs()

        job.refresh_from_db()
        assert job.status == HarnessImplementationJob.Status.FAILED
        assert job.failure_reason == HarnessImplementationJob.FailureReason.TIMEOUT
        assert job.worktree_path == ""
        assert not worktree.exists()

    def test_pushing_claim_does_not_delete_worktree_of_a_job_that_already_succeeded(
        self, workspace, owner, repo, tmp_path
    ):
        """Codex code-review P2, branch 4 round 4: same race as the
        implementing-side test above, one phase later -- a live worker
        that already succeeded (moved to `succeeded`) between the scan's
        own stale-id query and this specific recovery attempt must not
        have its worktree deleted."""
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        stale_started = timezone.now() - timedelta(seconds=100)
        job = make_job(
            workspace,
            repo,
            created_by=owner,
            status=HarnessImplementationJob.Status.SUCCEEDED,
            push_started_at=stale_started,
            worktree_path=str(worktree),
        )
        threshold = timezone.now() - timedelta(seconds=1)
        implementation_execution._recover_stale_pushing_job(job.id, threshold)
        job.refresh_from_db()
        assert job.status == HarnessImplementationJob.Status.SUCCEEDED
        assert job.worktree_path == str(worktree)
        assert worktree.exists()

    def test_sweeps_expired_worktree_for_pending_gate_b_job(self, workspace, owner, repo, tmp_path):
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        gate_b_request = ApprovalRequest.objects.create(
            workspace=workspace,
            tool_name="harness_push",
            status=ApprovalRequest.Status.PENDING,
            requested_by=owner,
            origin=ApprovalRequest.Origin.HARNESS,
        )
        job = make_job(
            workspace,
            repo,
            created_by=owner,
            status=HarnessImplementationJob.Status.PENDING_GATE_B,
            worktree_path=str(worktree),
            worktree_expires_at=timezone.now() - timedelta(hours=1),
            gate_b_request=gate_b_request,
        )

        implementation_execution.recover_stale_harness_implementation_jobs()

        job.refresh_from_db()
        gate_b_request.refresh_from_db()
        assert job.status == HarnessImplementationJob.Status.FAILED
        assert job.failure_reason == HarnessImplementationJob.FailureReason.WORKTREE_EXPIRED
        assert job.worktree_path == ""
        assert not worktree.exists()
        assert gate_b_request.status == ApprovalRequest.Status.EXPIRED

    def test_does_not_sweep_a_pending_gate_b_job_still_within_retention(
        self, workspace, owner, repo, tmp_path
    ):
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        job = make_job(
            workspace,
            repo,
            created_by=owner,
            status=HarnessImplementationJob.Status.PENDING_GATE_B,
            worktree_path=str(worktree),
            worktree_expires_at=timezone.now() + timedelta(hours=1),
        )
        implementation_execution.recover_stale_harness_implementation_jobs()
        job.refresh_from_db()
        assert job.status == HarnessImplementationJob.Status.PENDING_GATE_B
        assert worktree.exists()

    def test_does_not_sweep_a_pending_review_job_regardless_of_worktree_deadline(
        self, workspace, owner, repo, tmp_path
    ):
        """Deliberately out of scope for this sweep -- see
        `_sweep_expired_worktree_job`'s own docstring: a `pending_review`
        job may have an actively running review still reading from this
        exact worktree."""
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        job = make_job(
            workspace,
            repo,
            created_by=owner,
            status=HarnessImplementationJob.Status.PENDING_REVIEW,
            worktree_path=str(worktree),
            worktree_expires_at=timezone.now() - timedelta(hours=1),
        )
        implementation_execution.recover_stale_harness_implementation_jobs()
        job.refresh_from_db()
        assert job.status == HarnessImplementationJob.Status.PENDING_REVIEW
        assert worktree.exists()

    @pytest.mark.parametrize(
        "status",
        [
            HarnessImplementationJob.Status.SUCCEEDED,
            HarnessImplementationJob.Status.FAILED,
            HarnessImplementationJob.Status.CANCELLED,
        ],
    )
    def test_retries_removal_of_a_leaked_terminal_worktree(
        self, workspace, owner, repo, settings, tmp_path, status
    ):
        """Codex code-review P2, branch 4 round 3: a genuinely failed
        `_apply_worktree_cleanup` at finalize time leaves a terminal job
        still pointing at a directory that was never actually removed --
        this case is the only thing that ever revisits it, mirroring
        `harness.execution`'s identical leaked-terminal-container case."""
        settings.HARNESS_LEAKED_CONTAINER_RECOVERY_GRACE_SECONDS = 1
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        job = make_job(
            workspace, repo, created_by=owner, status=status, worktree_path=str(worktree)
        )
        stale_updated = timezone.now() - timedelta(seconds=100)
        HarnessImplementationJob.objects.filter(pk=job.pk).update(updated_at=stale_updated)

        implementation_execution.recover_stale_harness_implementation_jobs()

        job.refresh_from_db()
        assert job.worktree_path == ""
        assert not worktree.exists()
        assert job.status == status

    def test_does_not_sweep_a_freshly_finalized_terminal_worktree(
        self, workspace, owner, repo, settings, tmp_path
    ):
        settings.HARNESS_LEAKED_CONTAINER_RECOVERY_GRACE_SECONDS = 300
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        job = make_job(
            workspace,
            repo,
            created_by=owner,
            status=HarnessImplementationJob.Status.FAILED,
            worktree_path=str(worktree),
        )
        implementation_execution.recover_stale_harness_implementation_jobs()
        job.refresh_from_db()
        assert job.worktree_path == str(worktree)
        assert worktree.exists()

    def test_leaves_worktree_path_populated_when_removal_still_fails(
        self, workspace, owner, repo, monkeypatch, settings, tmp_path
    ):
        settings.HARNESS_LEAKED_CONTAINER_RECOVERY_GRACE_SECONDS = 1
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        job = make_job(
            workspace,
            repo,
            created_by=owner,
            status=HarnessImplementationJob.Status.FAILED,
            worktree_path=str(worktree),
        )
        stale_updated = timezone.now() - timedelta(seconds=100)
        HarnessImplementationJob.objects.filter(pk=job.pk).update(updated_at=stale_updated)
        # A no-op stand-in for `runner.cleanup_directory` -- the real
        # directory is deliberately left behind so `os.path.exists` finds
        # it still there afterward, confirming removal genuinely failed.
        monkeypatch.setattr(implementation_execution.runner, "cleanup_directory", mock.Mock())

        implementation_execution.recover_stale_harness_implementation_jobs()

        job.refresh_from_db()
        assert job.worktree_path == str(worktree)

    @pytest.mark.parametrize(
        "status",
        [
            HarnessImplementationJob.Status.SUCCEEDED,
            HarnessImplementationJob.Status.FAILED,
            HarnessImplementationJob.Status.CANCELLED,
        ],
    )
    def test_retries_removal_of_a_leaked_terminal_container(
        self, workspace, owner, repo, monkeypatch, settings, status
    ):
        """Codex code-review P2, branch 4 round 5: case 6 (leaked
        worktree) has a sibling case for `container_id` -- a genuinely
        failed `docker rm` at finalize time otherwise leaves a stopped
        container, still carrying the CLI's own credentials in its
        environment, on the host indefinitely, mirroring `harness.
        execution`'s/`harness.review_execution`'s identical cases."""
        settings.HARNESS_LEAKED_CONTAINER_RECOVERY_GRACE_SECONDS = 1
        job = make_job(
            workspace, repo, created_by=owner, status=status, container_id="leaked-impl-1"
        )
        stale_updated = timezone.now() - timedelta(seconds=100)
        HarnessImplementationJob.objects.filter(pk=job.pk).update(updated_at=stale_updated)
        remove_mock = mock.Mock()
        monkeypatch.setattr(implementation_execution.runner, "remove_container", remove_mock)

        implementation_execution.recover_stale_harness_implementation_jobs()

        remove_mock.assert_called_once_with("leaked-impl-1")
        job.refresh_from_db()
        assert job.container_id == ""
        assert job.status == status

    def test_does_not_sweep_a_freshly_finalized_terminal_container(
        self, workspace, owner, repo, monkeypatch, settings
    ):
        settings.HARNESS_LEAKED_CONTAINER_RECOVERY_GRACE_SECONDS = 300
        job = make_job(
            workspace,
            repo,
            created_by=owner,
            status=HarnessImplementationJob.Status.FAILED,
            container_id="leaked-impl-1",
        )
        remove_mock = mock.Mock()
        monkeypatch.setattr(implementation_execution.runner, "remove_container", remove_mock)

        implementation_execution.recover_stale_harness_implementation_jobs()

        remove_mock.assert_not_called()
        job.refresh_from_db()
        assert job.container_id == "leaked-impl-1"

    def test_leaves_container_id_populated_when_removal_still_fails(
        self, workspace, owner, repo, monkeypatch, settings
    ):
        settings.HARNESS_LEAKED_CONTAINER_RECOVERY_GRACE_SECONDS = 1
        job = make_job(
            workspace,
            repo,
            created_by=owner,
            status=HarnessImplementationJob.Status.FAILED,
            container_id="leaked-impl-1",
        )
        stale_updated = timezone.now() - timedelta(seconds=100)
        HarnessImplementationJob.objects.filter(pk=job.pk).update(updated_at=stale_updated)
        monkeypatch.setattr(
            implementation_execution.runner,
            "remove_container",
            mock.Mock(side_effect=RuntimeError("still busy")),
        )

        implementation_execution.recover_stale_harness_implementation_jobs()

        job.refresh_from_db()
        assert job.container_id == "leaked-impl-1"

    def test_recovered_cancellation_is_audited_as_cancelled_not_failed(
        self, workspace, owner, repo, monkeypatch, settings, tmp_path
    ):
        """Codex code-review P3, branch 4 round 5: the audit action must
        agree with the status `_claim_stale_implementing_job` actually
        stored -- a job whose `cancel_requested` flag was set before the
        recovery scan caught it ends up `cancelled`, not `failed`."""
        settings.HARNESS_MAX_JOB_SECONDS = 1
        settings.HARNESS_RECOVERY_GRACE_SECONDS = 1
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        stale_started = timezone.now() - timedelta(seconds=100)
        job = make_job(
            workspace,
            repo,
            created_by=owner,
            status=HarnessImplementationJob.Status.IMPLEMENTING,
            started_at=stale_started,
            worktree_path=str(worktree),
            cancel_requested=True,
        )
        implementation_execution.recover_stale_harness_implementation_jobs()
        job.refresh_from_db()
        assert job.status == HarnessImplementationJob.Status.CANCELLED
        entry = AuditLog.objects.filter(
            action="harness.implementation_job_cancelled", resource_id=str(job.pk)
        ).first()
        assert entry is not None
        assert not AuditLog.objects.filter(
            action="harness.implementation_job_failed", resource_id=str(job.pk)
        ).exists()
