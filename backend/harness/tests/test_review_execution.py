"""Every test here monkeypatches `harness.review_execution.runner`'s
functions -- no real `docker`/`git` subprocess, mirroring
`harness/tests/test_execution.py`'s/`test_implementation_execution.py`'s
identical discipline (CLAUDE.md rule 5)."""

import json
from unittest import mock

import pytest

from accounts.services import register_user
from ai_core.models import ModelProvider, ModelUsage, PromptTemplate, PromptVersion
from audit.models import AuditLog
from harness import review_execution
from harness.models import HarnessImplementationJob, HarnessRepo, HarnessReview
from harness.services import HarnessInvalidTransition
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture(autouse=True)
def ai_settings(settings):
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    return settings


@pytest.fixture(autouse=True)
def fake_provider():
    return ModelProvider.objects.create(
        name="fake-local",
        provider_type=ModelProvider.ProviderType.FAKE,
        model_id="fake-1",
        priority=0,
    )


@pytest.fixture
def review_prompt():
    template = PromptTemplate.objects.create(name="jarvis_harness_review", purpose="harness.review")
    version = PromptVersion.objects.create(
        template=template, version=1, content="Review the diff and respond with JSON."
    )
    template.active_version = version
    template.save(update_fields=["active_version"])
    return version


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
        "status": HarnessImplementationJob.Status.PENDING_REVIEW,
        "worktree_path": "/tmp/harness-worktree-x",
        "base_commit_sha": "base-sha",
        "produced_commit_sha": "abc123",
    }
    fields.update(overrides)
    return HarnessImplementationJob.objects.create(**fields)


def make_review(workspace, job, **overrides):
    fields = {
        "workspace": workspace,
        "implementation_job": job,
        "agent": HarnessReview.Agent.CODEX_CLI,
        "status": HarnessReview.Status.PENDING,
    }
    fields.update(overrides)
    return HarnessReview.objects.create(**fields)


def _review_json(summary="looks good", findings=None, verdict="approved"):
    return json.dumps({"summary": summary, "findings": findings or [], "verdict": verdict})


class TestSelectReviewAgent:
    def test_picks_an_agent_that_differs_from_the_implementer(self):
        agent = review_execution.select_review_agent(HarnessImplementationJob.Agent.CLAUDE_CODE)
        assert agent == HarnessReview.Agent.CODEX_CLI

    def test_raises_when_no_eligible_agent_is_selectable(self, monkeypatch):
        monkeypatch.setattr(
            "harness.services._SELECTABLE_REVIEW_AGENTS",
            {HarnessImplementationJob.Agent.CLAUDE_CODE},
        )
        with pytest.raises(HarnessInvalidTransition):
            review_execution.select_review_agent(HarnessImplementationJob.Agent.CLAUDE_CODE)


class TestDispatchHarnessReview:
    def test_creates_a_pending_review_and_dispatches(self, workspace, owner, repo, settings):
        settings.CELERY_TASK_ALWAYS_EAGER = False
        job = make_job(workspace, repo, created_by=owner)
        with mock.patch("harness.tasks.run_harness_review_job") as task:
            review = review_execution.dispatch_harness_review(job)
        assert review.status == HarnessReview.Status.PENDING
        assert review.agent == HarnessReview.Agent.CODEX_CLI
        assert review.implementation_job_id == job.id
        task.delay.assert_called_once_with(str(review.id))

    def test_dispatch_failure_fails_review_and_job_closed(
        self, workspace, owner, repo, settings, monkeypatch
    ):
        """Codex code-review P2, branch 3 round 2: an earlier version
        only logged a lost broker publish, leaving the review `pending`
        and the implementation job `pending_review` forever -- there is
        no periodic recovery scan yet (branch 4's job) to reclaim it."""
        settings.CELERY_TASK_ALWAYS_EAGER = False
        job = make_job(workspace, repo, created_by=owner)
        monkeypatch.setattr(
            "harness.tasks.run_harness_review_job",
            mock.Mock(delay=mock.Mock(side_effect=Exception("broker down"))),
        )
        review = review_execution.dispatch_harness_review(job)
        review.refresh_from_db()
        job.refresh_from_db()
        assert review.status == HarnessReview.Status.FAILED
        assert job.status == HarnessImplementationJob.Status.FAILED
        assert job.failure_reason == HarnessImplementationJob.FailureReason.REVIEW_UNAVAILABLE


class TestResolveRunningReviewAfterUnexpectedError:
    def test_resolves_a_stuck_running_review_closed(self, workspace, owner, repo, monkeypatch):
        job = make_job(workspace, repo, created_by=owner)
        review = make_review(workspace, job, status=HarnessReview.Status.RUNNING)
        monkeypatch.setattr(review_execution.runner, "cleanup_directory", mock.Mock())
        review_execution.resolve_running_review_after_unexpected_error(review.id)
        review.refresh_from_db()
        job.refresh_from_db()
        assert review.status == HarnessReview.Status.FAILED
        assert job.status == HarnessImplementationJob.Status.FAILED
        assert job.failure_reason == HarnessImplementationJob.FailureReason.REVIEW_UNAVAILABLE

    def test_tears_down_a_leftover_container(self, workspace, owner, repo, monkeypatch):
        job = make_job(workspace, repo, created_by=owner)
        review = make_review(
            workspace, job, status=HarnessReview.Status.RUNNING, container_id="harness-review-x"
        )
        stop_mock = mock.Mock()
        monkeypatch.setattr(review_execution.runner, "stop_container", stop_mock)
        monkeypatch.setattr(
            review_execution.runner, "container_is_running", mock.Mock(return_value=False)
        )
        monkeypatch.setattr(review_execution.runner, "remove_container", mock.Mock())
        review_execution.resolve_running_review_after_unexpected_error(review.id)
        stop_mock.assert_called_once()
        review.refresh_from_db()
        assert review.container_id == ""

    def test_wrong_status_is_a_safe_no_op(self, workspace, owner, repo):
        job = make_job(workspace, repo, created_by=owner)
        review = make_review(workspace, job, status=HarnessReview.Status.SUCCEEDED)
        review_execution.resolve_running_review_after_unexpected_error(review.id)
        review.refresh_from_db()
        assert review.status == HarnessReview.Status.SUCCEEDED


def _patch_happy_path_runner(monkeypatch, *, raw_output):
    monkeypatch.setattr(review_execution.runner, "create_bundle", mock.Mock())
    monkeypatch.setattr(
        review_execution.runner,
        "prepare_review_clone",
        mock.Mock(return_value="/tmp/harness-review-clean-clone-x"),
    )
    monkeypatch.setattr(review_execution.runner, "image_for_agent", mock.Mock(return_value="img:1"))
    monkeypatch.setattr(review_execution.runner, "launch_container", mock.Mock())
    monkeypatch.setattr(
        review_execution.runner, "container_is_running", mock.Mock(side_effect=[False])
    )
    monkeypatch.setattr(review_execution.runner, "container_exit_code", mock.Mock(return_value=0))
    monkeypatch.setattr(review_execution.runner, "capture_logs", mock.Mock(return_value=raw_output))
    monkeypatch.setattr(review_execution.runner, "remove_container", mock.Mock())
    monkeypatch.setattr(review_execution.runner, "cleanup_directory", mock.Mock())


class TestRunHarnessReviewJobSuccess:
    def test_approved_verdict_requests_gate_b(
        self, workspace, owner, repo, monkeypatch, review_prompt
    ):
        job = make_job(workspace, repo, created_by=owner)
        review = make_review(workspace, job)
        _patch_happy_path_runner(monkeypatch, raw_output="raw transcript")
        with mock.patch.object(
            review_execution, "_produce_review_summary", return_value=("looks good", [], "approved")
        ):
            review_execution.run_harness_review_job(review.id)
        review.refresh_from_db()
        job.refresh_from_db()
        assert review.status == HarnessReview.Status.SUCCEEDED
        assert review.verdict == HarnessReview.Verdict.APPROVED
        assert review.summary == "looks good"
        assert job.status == HarnessImplementationJob.Status.PENDING_GATE_B
        assert job.gate_b_request is not None

    def test_changes_requested_verdict_fails_the_job(
        self, workspace, owner, repo, monkeypatch, review_prompt
    ):
        job = make_job(workspace, repo, created_by=owner)
        review = make_review(workspace, job)
        _patch_happy_path_runner(monkeypatch, raw_output="raw transcript")
        with mock.patch.object(
            review_execution,
            "_produce_review_summary",
            return_value=("needs work", ["bug here"], "changes_requested"),
        ):
            review_execution.run_harness_review_job(review.id)
        review.refresh_from_db()
        job.refresh_from_db()
        assert review.status == HarnessReview.Status.SUCCEEDED
        assert review.verdict == HarnessReview.Verdict.CHANGES_REQUESTED
        assert review.findings == ["bug here"]
        assert job.status == HarnessImplementationJob.Status.FAILED
        assert job.failure_reason == HarnessImplementationJob.FailureReason.REVIEW_CHANGES_REQUESTED

    def test_mounts_the_clean_clone_not_the_agent_worktree(
        self, workspace, owner, repo, monkeypatch, review_prompt
    ):
        """Codex code-review P1, branch 3 round 1: the review container
        must never mount `job.worktree_path/repo` directly -- the agent
        had full write access to that worktree's own `.git/config`/
        `.gitattributes` right up until tests passed, and `git diff`'s
        config-driven mechanisms (`diff.external`/`textconv`) could hide
        the real diff from the reviewer or run arbitrary code inside the
        review container. Only the clean checkout `prepare_review_clone`
        produced may ever be mounted."""
        job = make_job(workspace, repo, created_by=owner)
        review = make_review(workspace, job)
        _patch_happy_path_runner(monkeypatch, raw_output="raw transcript")
        with mock.patch.object(
            review_execution, "_produce_review_summary", return_value=("ok", [], "approved")
        ):
            review_execution.run_harness_review_job(review.id)
        launch_call = review_execution.runner.launch_container.call_args
        assert launch_call.kwargs["clone_dir"] == "/tmp/harness-review-clean-clone-x"
        assert launch_call.kwargs["clone_dir"] != f"{job.worktree_path}/repo"
        prepare_call = review_execution.runner.prepare_review_clone.call_args
        assert prepare_call.kwargs["base_sha"] == job.base_commit_sha
        assert prepare_call.kwargs["produced_sha"] == job.produced_commit_sha
        assert prepare_call.kwargs["branch_name"] == job.branch_name

    def test_cleans_up_bundle_and_clean_clone_directories(
        self, workspace, owner, repo, monkeypatch, review_prompt
    ):
        job = make_job(workspace, repo, created_by=owner)
        review = make_review(workspace, job)
        _patch_happy_path_runner(monkeypatch, raw_output="raw transcript")
        with mock.patch.object(
            review_execution, "_produce_review_summary", return_value=("ok", [], "approved")
        ):
            review_execution.run_harness_review_job(review.id)
        cleanup_calls = [
            c.args[0] for c in review_execution.runner.cleanup_directory.call_args_list
        ]
        assert "/tmp/harness-review-clean-clone-x" in cleanup_calls
        assert any("harness-review-bundle-" in path for path in cleanup_calls)

    def test_records_model_usage(self, workspace, owner, repo, monkeypatch, review_prompt):
        job = make_job(workspace, repo, created_by=owner)
        review = make_review(workspace, job)
        _patch_happy_path_runner(monkeypatch, raw_output="raw transcript")
        with mock.patch.object(
            review_execution, "_produce_review_summary", return_value=("ok", [], "approved")
        ):
            review_execution.run_harness_review_job(review.id)
        usage = ModelUsage.objects.get(purpose="harness.review")
        assert usage.success is True

    def test_writes_audit_row(self, workspace, owner, repo, monkeypatch, review_prompt):
        job = make_job(workspace, repo, created_by=owner)
        review = make_review(workspace, job)
        _patch_happy_path_runner(monkeypatch, raw_output="raw transcript")
        with mock.patch.object(
            review_execution, "_produce_review_summary", return_value=("ok", [], "approved")
        ):
            review_execution.run_harness_review_job(review.id)
        entry = AuditLog.objects.get(action="harness.review_succeeded")
        assert entry.resource_id == str(review.id)


class TestRunHarnessReviewJobFailurePaths:
    def test_launch_failure_marks_review_and_job_unavailable(
        self, workspace, owner, repo, monkeypatch
    ):
        job = make_job(workspace, repo, created_by=owner)
        review = make_review(workspace, job)
        monkeypatch.setattr(review_execution.runner, "create_bundle", mock.Mock())
        monkeypatch.setattr(
            review_execution.runner,
            "prepare_review_clone",
            mock.Mock(return_value="/tmp/harness-review-clean-clone-x"),
        )
        monkeypatch.setattr(
            review_execution.runner,
            "image_for_agent",
            mock.Mock(side_effect=review_execution.runner.RunnerError("no image")),
        )
        monkeypatch.setattr(review_execution.runner, "remove_container", mock.Mock())
        monkeypatch.setattr(review_execution.runner, "cleanup_directory", mock.Mock())
        review_execution.run_harness_review_job(review.id)
        review.refresh_from_db()
        job.refresh_from_db()
        assert review.status == HarnessReview.Status.FAILED
        assert job.status == HarnessImplementationJob.Status.FAILED
        assert job.failure_reason == HarnessImplementationJob.FailureReason.REVIEW_UNAVAILABLE

    def test_empty_transcript_marks_review_unavailable(self, workspace, owner, repo, monkeypatch):
        job = make_job(workspace, repo, created_by=owner)
        review = make_review(workspace, job)
        _patch_happy_path_runner(monkeypatch, raw_output="")
        review_execution.run_harness_review_job(review.id)
        review.refresh_from_db()
        job.refresh_from_db()
        assert review.status == HarnessReview.Status.FAILED
        assert job.failure_reason == HarnessImplementationJob.FailureReason.REVIEW_UNAVAILABLE

    def test_malformed_gateway_response_marks_review_unavailable(
        self, workspace, owner, repo, monkeypatch, review_prompt
    ):
        job = make_job(workspace, repo, created_by=owner)
        review = make_review(workspace, job)
        _patch_happy_path_runner(monkeypatch, raw_output="raw transcript")
        with mock.patch.object(
            review_execution, "_produce_review_summary", return_value=("", [], "")
        ):
            review_execution.run_harness_review_job(review.id)
        review.refresh_from_db()
        job.refresh_from_db()
        assert review.status == HarnessReview.Status.FAILED
        assert job.failure_reason == HarnessImplementationJob.FailureReason.REVIEW_UNAVAILABLE

    def test_missing_prompt_template_marks_review_unavailable(
        self, workspace, owner, repo, monkeypatch
    ):
        # No `review_prompt` fixture -- the template is deliberately absent.
        job = make_job(workspace, repo, created_by=owner)
        review = make_review(workspace, job)
        _patch_happy_path_runner(monkeypatch, raw_output="raw transcript")
        review_execution.run_harness_review_job(review.id)
        review.refresh_from_db()
        job.refresh_from_db()
        assert review.status == HarnessReview.Status.FAILED
        assert job.failure_reason == HarnessImplementationJob.FailureReason.REVIEW_UNAVAILABLE

    def test_wrong_status_is_a_safe_no_op(self, workspace, owner, repo, monkeypatch):
        job = make_job(workspace, repo, created_by=owner)
        review = make_review(workspace, job, status=HarnessReview.Status.SUCCEEDED)
        launch_mock = mock.Mock()
        monkeypatch.setattr(review_execution.runner, "launch_container", launch_mock)
        review_execution.run_harness_review_job(review.id)
        launch_mock.assert_not_called()


class TestParseReviewResponse:
    def test_parses_valid_response(self):
        summary, findings, verdict = review_execution._parse_review_response(
            _review_json(summary="good", findings=["a", "b"], verdict="approved")
        )
        assert summary == "good"
        assert findings == ["a", "b"]
        assert verdict == "approved"

    @pytest.mark.parametrize(
        "raw",
        [
            "not json",
            "{}",
            '{"summary": "ok", "verdict": "maybe"}',
            '{"summary": 123, "verdict": "approved"}',
            "null",
        ],
    )
    def test_falls_back_on_unrecognized_shape(self, raw):
        summary, findings, verdict = review_execution._parse_review_response(raw)
        assert (summary, findings, verdict) == ("", [], "")

    def test_bounds_summary_and_findings(self):
        long_summary = "x" * (review_execution._REVIEW_SUMMARY_MAX_CHARS + 500)
        many_findings = [
            f"finding {i}" for i in range(review_execution._REVIEW_FINDINGS_MAX_COUNT + 5)
        ]
        summary, findings, verdict = review_execution._parse_review_response(
            json.dumps({"summary": long_summary, "findings": many_findings, "verdict": "approved"})
        )
        assert len(summary) == review_execution._REVIEW_SUMMARY_MAX_CHARS
        assert len(findings) == review_execution._REVIEW_FINDINGS_MAX_COUNT
