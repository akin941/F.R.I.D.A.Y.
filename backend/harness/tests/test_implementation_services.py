from unittest import mock

import pytest

from accounts.services import register_user
from approvals.models import ApprovalRequest
from audit.models import AuditLog
from harness.models import HarnessImplementationJob, HarnessRepo, HarnessReview
from harness.services import (
    HarnessInvalidTransition,
    HarnessPermissionDenied,
    cancel_harness_implementation_job,
    create_harness_implementation_job,
    fail_implementation_job_for_gate_a_decision,
    fail_implementation_job_for_gate_b_decision,
    fail_implementation_job_for_review,
    get_implementation_job,
    list_implementation_jobs,
    request_harness_push,
)
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


def user_with_role(workspace, role, email):
    user = register_user(email=email, password=PASSWORD, name=role)
    WorkspaceMember.objects.create(workspace=workspace, user=user, role=role)
    return user


@pytest.fixture
def repo(workspace):
    return HarnessRepo.objects.create(
        workspace=workspace,
        name="jarvis-os",
        git_url="git@example.com:x/y.git",
        enabled=True,
        test_command="pytest -q",
    )


def make_implementation_job(workspace, repo, **overrides):
    fields = {
        "workspace": workspace,
        "repo": repo,
        "goal": "fix the bug",
        "agent": HarnessImplementationJob.Agent.CLAUDE_CODE,
    }
    fields.update(overrides)
    return HarnessImplementationJob.objects.create(**fields)


def make_review(job, **overrides):
    fields = {
        "workspace": job.workspace,
        "implementation_job": job,
        "agent": HarnessReview.Agent.CODEX_CLI,
        "status": HarnessReview.Status.SUCCEEDED,
        "verdict": HarnessReview.Verdict.APPROVED,
    }
    fields.update(overrides)
    return HarnessReview.objects.create(**fields)


class TestCreateHarnessImplementationJob:
    def test_owner_can_create_and_requests_gate_a(self, workspace, owner, repo):
        job = create_harness_implementation_job(
            workspace=workspace, actor=owner, repo=repo, goal="fix the bug"
        )
        assert job.status == HarnessImplementationJob.Status.PENDING_GATE_A
        assert job.gate_a_request is not None
        assert job.gate_a_request.tool_name == "harness_implement"
        assert job.gate_a_request.payload == {"job_id": str(job.id)}
        assert job.gate_a_request.origin == ApprovalRequest.Origin.HARNESS
        assert job.branch_name == f"harness/p3-{job.id}"

    def test_create_writes_audit_row(self, workspace, owner, repo):
        job = create_harness_implementation_job(
            workspace=workspace, actor=owner, repo=repo, goal="fix the bug"
        )
        entry = AuditLog.objects.get(action="harness.implementation_job_created")
        assert entry.resource_id == str(job.id)

    @pytest.mark.parametrize("role", [WorkspaceMember.Role.MEMBER, WorkspaceMember.Role.VIEWER])
    def test_non_curator_cannot_create(self, workspace, repo, role):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        with pytest.raises(HarnessPermissionDenied):
            create_harness_implementation_job(
                workspace=workspace, actor=user, repo=repo, goal="fix the bug"
            )
        assert not HarnessImplementationJob.objects.filter(workspace=workspace).exists()

    def test_disabled_repo_refused(self, workspace, owner, repo):
        repo.enabled = False
        repo.save(update_fields=["enabled"])
        with pytest.raises(HarnessInvalidTransition):
            create_harness_implementation_job(
                workspace=workspace, actor=owner, repo=repo, goal="fix the bug"
            )

    def test_self_repo_hard_block(self, workspace, owner, repo, settings):
        settings.HARNESS_SELF_REPO_IDENTIFIERS = ["git@example.com:x/y.git"]
        with pytest.raises(HarnessInvalidTransition):
            create_harness_implementation_job(
                workspace=workspace, actor=owner, repo=repo, goal="fix the bug"
            )
        assert not HarnessImplementationJob.objects.filter(workspace=workspace).exists()

    def test_self_repo_hard_block_matches_a_different_url_form(
        self, workspace, owner, repo, settings
    ):
        # repo.git_url is the scp-like form; configure the identifier as
        # the https form -- the canonical normalizer must still catch it.
        settings.HARNESS_SELF_REPO_IDENTIFIERS = ["https://example.com/x/y"]
        with pytest.raises(HarnessInvalidTransition):
            create_harness_implementation_job(
                workspace=workspace, actor=owner, repo=repo, goal="fix the bug"
            )

    def test_missing_test_command_refused(self, workspace, owner, repo):
        repo.test_command = ""
        repo.save(update_fields=["test_command"])
        with pytest.raises(HarnessInvalidTransition):
            create_harness_implementation_job(
                workspace=workspace, actor=owner, repo=repo, goal="fix the bug"
            )

    def test_whitespace_only_test_command_refused(self, workspace, owner, repo):
        """Codex code-review P2, branch 2 round 4: a bare truthiness check
        treats `"   "` as configured, but a shell runs it as a no-op that
        exits 0 -- implementation jobs would pass without verification."""
        repo.test_command = "   "
        repo.save(update_fields=["test_command"])
        with pytest.raises(HarnessInvalidTransition):
            create_harness_implementation_job(
                workspace=workspace, actor=owner, repo=repo, goal="fix the bug"
            )

    def test_snapshots_repo_git_url_and_default_branch(self, workspace, owner, repo):
        job = create_harness_implementation_job(
            workspace=workspace, actor=owner, repo=repo, goal="fix the bug"
        )
        assert job.repo_git_url == repo.git_url
        assert job.repo_default_branch == repo.default_branch
        assert job.repo_test_command == repo.test_command

    def test_editing_repo_test_command_after_creation_does_not_change_the_job(
        self, workspace, owner, repo
    ):
        """Codex code-review P2, branch 2 round 6 -- same rationale as
        `repo_git_url`'s own equivalent test: editing `test_command` to
        blank/whitespace after the job was created must not silently let
        an already-approved job skip verification."""
        job = create_harness_implementation_job(
            workspace=workspace, actor=owner, repo=repo, goal="fix the bug"
        )
        original_command = job.repo_test_command
        repo.test_command = "   "
        repo.save(update_fields=["test_command"])
        job.refresh_from_db()
        assert job.repo_test_command == original_command

    def test_editing_repo_git_url_after_creation_does_not_change_the_job(
        self, workspace, owner, repo
    ):
        """Codex code-review P1, branch 2 round 4: `job.repo.git_url` is a
        LIVE read through an editable FK -- the self-modification hard
        block only ever runs once, at creation, so an edit to the repo's
        own `git_url` afterward must not silently redirect an
        already-approved job. The snapshotted field is what execution
        code actually uses, and it must never change."""
        job = create_harness_implementation_job(
            workspace=workspace, actor=owner, repo=repo, goal="fix the bug"
        )
        original_url = job.repo_git_url
        repo.git_url = "git@github.com:akin941/jarvis-os.git"
        repo.save(update_fields=["git_url"])
        job.refresh_from_db()
        assert job.repo_git_url == original_url
        assert job.repo_git_url != repo.git_url

    def test_unselectable_agent_refused(self, workspace, owner, repo):
        with pytest.raises(HarnessInvalidTransition):
            create_harness_implementation_job(
                workspace=workspace,
                actor=owner,
                repo=repo,
                goal="fix the bug",
                agent=HarnessImplementationJob.Agent.CODEX_CLI,
            )

    def test_cross_workspace_repo_refused(self, workspace, owner):
        other = Workspace.objects.create(name="Other", owner=owner)
        WorkspaceMember.objects.create(workspace=other, user=owner, role=WorkspaceMember.Role.OWNER)
        other_repo = HarnessRepo.objects.create(
            workspace=other,
            name="other",
            git_url="git@example.com:a/b.git",
            enabled=True,
            test_command="pytest",
        )
        with pytest.raises(HarnessPermissionDenied):
            create_harness_implementation_job(
                workspace=workspace, actor=owner, repo=other_repo, goal="fix the bug"
            )


class TestListGetImplementationJob:
    def test_curator_can_list_and_get(self, workspace, owner, repo):
        job = create_harness_implementation_job(
            workspace=workspace, actor=owner, repo=repo, goal="fix the bug"
        )
        assert list_implementation_jobs(workspace=workspace, actor=owner).count() == 1
        assert get_implementation_job(workspace=workspace, actor=owner, job_id=job.id).id == job.id

    def test_non_curator_cannot_list(self, workspace, repo, owner):
        create_harness_implementation_job(
            workspace=workspace, actor=owner, repo=repo, goal="fix the bug"
        )
        member = user_with_role(workspace, WorkspaceMember.Role.MEMBER, "m@test.dev")
        with pytest.raises(HarnessPermissionDenied):
            list_implementation_jobs(workspace=workspace, actor=member)


class TestCancelHarnessImplementationJob:
    def test_pending_gate_a_cancels_directly(self, workspace, owner, repo):
        job = create_harness_implementation_job(
            workspace=workspace, actor=owner, repo=repo, goal="fix the bug"
        )
        cancelled = cancel_harness_implementation_job(job=job, actor=owner)
        assert cancelled.status == HarnessImplementationJob.Status.CANCELLED
        assert cancelled.failure_reason == HarnessImplementationJob.FailureReason.KILLED

    @pytest.mark.parametrize(
        "status",
        [HarnessImplementationJob.Status.IMPLEMENTING, HarnessImplementationJob.Status.PUSHING],
    )
    def test_active_loop_status_sets_cancel_requested_only(self, workspace, owner, repo, status):
        """`implementing`/`pushing` have a real polling loop watching the
        flag -- cancellation is a flag-flip only, the loop itself does the
        actual teardown (Codex code-review P1, branch 2 round 1 -- see
        `cancel_harness_implementation_job`'s docstring)."""
        job = make_implementation_job(workspace, repo, status=status)
        cancelled = cancel_harness_implementation_job(job=job, actor=owner)
        assert cancelled.status == status
        assert cancelled.cancel_requested is True

    @pytest.mark.parametrize(
        "status",
        [
            HarnessImplementationJob.Status.PENDING_REVIEW,
            HarnessImplementationJob.Status.PENDING_GATE_B,
        ],
    )
    def test_idle_wait_status_cancels_directly_and_cleans_up_worktree(
        self, workspace, owner, repo, status, tmp_path
    ):
        """`pending_review`/`pending_gate_b` have no active loop watching
        the flag at all -- a flag-only cancel would silently do nothing
        (Codex code-review P1, branch 2 round 1), so these cancel straight
        to `cancelled` and clean up the worktree immediately, same as
        every other terminal path."""
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        job = make_implementation_job(workspace, repo, status=status, worktree_path=str(worktree))
        cancelled = cancel_harness_implementation_job(job=job, actor=owner)
        assert cancelled.status == HarnessImplementationJob.Status.CANCELLED
        assert cancelled.failure_reason == HarnessImplementationJob.FailureReason.KILLED
        assert not worktree.exists()
        cancelled.refresh_from_db()
        assert cancelled.worktree_path == ""

    def test_terminal_status_is_a_no_op(self, workspace, owner, repo):
        job = make_implementation_job(
            workspace, repo, status=HarnessImplementationJob.Status.SUCCEEDED
        )
        cancelled = cancel_harness_implementation_job(job=job, actor=owner)
        assert cancelled.status == HarnessImplementationJob.Status.SUCCEEDED
        assert cancelled.cancel_requested is False

    def test_pending_gate_a_cancel_revokes_the_gate_a_request(self, workspace, owner, repo):
        """Phase 3 branch 4 (`approvals.services.revoke_request`): a
        curator who later approves the now-orphaned request anyway can
        no longer revive the job either way (`harness.tools`'s own
        status re-check), but leaving it sitting `pending` forever was
        its own UX gap -- nothing ever closed it out."""
        job = create_harness_implementation_job(
            workspace=workspace, actor=owner, repo=repo, goal="fix the bug"
        )
        gate_a_request = job.gate_a_request
        assert gate_a_request.status == ApprovalRequest.Status.PENDING
        cancel_harness_implementation_job(job=job, actor=owner)
        gate_a_request.refresh_from_db()
        assert gate_a_request.status == ApprovalRequest.Status.EXPIRED
        assert "approval.revoked" in AuditLog.objects.values_list("action", flat=True)

    def test_pending_gate_b_cancel_revokes_the_gate_b_request(
        self, workspace, owner, repo, tmp_path
    ):
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        gate_b_request = ApprovalRequest.objects.create(
            workspace=workspace,
            tool_name="harness_push",
            status=ApprovalRequest.Status.PENDING,
            requested_by=owner,
            origin=ApprovalRequest.Origin.HARNESS,
        )
        job = make_implementation_job(
            workspace,
            repo,
            status=HarnessImplementationJob.Status.PENDING_GATE_B,
            worktree_path=str(worktree),
            gate_b_request=gate_b_request,
        )
        cancel_harness_implementation_job(job=job, actor=owner)
        gate_b_request.refresh_from_db()
        assert gate_b_request.status == ApprovalRequest.Status.EXPIRED

    def test_pending_review_cancel_does_not_touch_any_approval_request(
        self, workspace, owner, repo, tmp_path
    ):
        """`pending_review` has no live gate approval at all -- only
        `pending_gate_b`, within the same idle-wait branch, does."""
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        gate_b_request = ApprovalRequest.objects.create(
            workspace=workspace,
            tool_name="harness_push",
            status=ApprovalRequest.Status.PENDING,
            requested_by=owner,
            origin=ApprovalRequest.Origin.HARNESS,
        )
        job = make_implementation_job(
            workspace,
            repo,
            status=HarnessImplementationJob.Status.PENDING_REVIEW,
            worktree_path=str(worktree),
            gate_b_request=gate_b_request,
        )
        cancel_harness_implementation_job(job=job, actor=owner)
        gate_b_request.refresh_from_db()
        assert gate_b_request.status == ApprovalRequest.Status.PENDING


class TestRequestHarnessPush:
    def test_approved_review_requests_gate_b(self, workspace, owner, repo):
        job = make_implementation_job(
            workspace,
            repo,
            status=HarnessImplementationJob.Status.PENDING_REVIEW,
            created_by=owner,
            diff_summary="changed 1 file",
        )
        review = make_review(job)
        updated = request_harness_push(job=job, review=review)
        assert updated.status == HarnessImplementationJob.Status.PENDING_GATE_B
        assert updated.gate_b_request is not None
        assert updated.gate_b_request.tool_name == "harness_push"
        assert updated.gate_b_request.payload["review_id"] == str(review.id)
        assert updated.gate_b_request.origin == ApprovalRequest.Origin.HARNESS

    def test_changes_requested_verdict_refused(self, workspace, owner, repo):
        job = make_implementation_job(
            workspace, repo, status=HarnessImplementationJob.Status.PENDING_REVIEW, created_by=owner
        )
        review = make_review(job, verdict=HarnessReview.Verdict.CHANGES_REQUESTED)
        with pytest.raises(HarnessInvalidTransition):
            request_harness_push(job=job, review=review)

    def test_review_for_a_different_job_refused(self, workspace, owner, repo):
        job = make_implementation_job(
            workspace, repo, status=HarnessImplementationJob.Status.PENDING_REVIEW, created_by=owner
        )
        other_job = make_implementation_job(
            workspace, repo, status=HarnessImplementationJob.Status.PENDING_REVIEW, created_by=owner
        )
        review = make_review(other_job)
        with pytest.raises(HarnessInvalidTransition):
            request_harness_push(job=job, review=review)

    def test_wrong_job_status_refused(self, workspace, owner, repo):
        job = make_implementation_job(
            workspace, repo, status=HarnessImplementationJob.Status.IMPLEMENTING, created_by=owner
        )
        review = make_review(job)
        with pytest.raises(HarnessInvalidTransition):
            request_harness_push(job=job, review=review)


class TestFailImplementationJobForReview:
    def test_changes_requested_fails_job_and_cleans_up_worktree(
        self, workspace, owner, repo, tmp_path
    ):
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        job = make_implementation_job(
            workspace,
            repo,
            status=HarnessImplementationJob.Status.PENDING_REVIEW,
            created_by=owner,
            worktree_path=str(worktree),
        )
        review = make_review(job, verdict=HarnessReview.Verdict.CHANGES_REQUESTED)
        failed = fail_implementation_job_for_review(job=job, review=review)
        assert failed.status == HarnessImplementationJob.Status.FAILED
        assert (
            failed.failure_reason == HarnessImplementationJob.FailureReason.REVIEW_CHANGES_REQUESTED
        )
        assert not worktree.exists()
        failed.refresh_from_db()
        assert failed.worktree_path == ""

    def test_wrong_status_is_a_safe_no_op(self, workspace, owner, repo):
        job = make_implementation_job(
            workspace, repo, status=HarnessImplementationJob.Status.SUCCEEDED, created_by=owner
        )
        review = make_review(job)
        assert fail_implementation_job_for_review(job=job, review=review) is None

    def test_worktree_path_preserved_when_cleanup_fails(self, workspace, owner, repo, tmp_path):
        """Codex code-review P2, branch 2 rounds 3 and 5: `runner.
        cleanup_directory` is `shutil.rmtree(path, ignore_errors=True)` --
        it never raises, so a left-behind real directory (not a mocked
        exception) is what actually exercises the confirmed-cleanup
        check."""
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        job = make_implementation_job(
            workspace,
            repo,
            status=HarnessImplementationJob.Status.PENDING_REVIEW,
            created_by=owner,
            worktree_path=str(worktree),
        )
        review = make_review(job, verdict=HarnessReview.Verdict.CHANGES_REQUESTED)
        with mock.patch("harness.runner.cleanup_directory"):
            failed = fail_implementation_job_for_review(job=job, review=review)
        assert failed.status == HarnessImplementationJob.Status.FAILED
        failed.refresh_from_db()
        assert failed.worktree_path == str(worktree)
        assert worktree.exists()


class TestGateDecisionFailures:
    def test_gate_a_rejection_fails_job(self, workspace, owner, repo):
        job = make_implementation_job(
            workspace,
            repo,
            status=HarnessImplementationJob.Status.PENDING_GATE_A,
            created_by=owner,
        )
        fail_implementation_job_for_gate_a_decision(
            job.id, decision_status=ApprovalRequest.Status.REJECTED
        )
        job.refresh_from_db()
        assert job.status == HarnessImplementationJob.Status.FAILED
        assert job.failure_reason == HarnessImplementationJob.FailureReason.GATE_A_REJECTED

    def test_gate_a_expiry_fails_job(self, workspace, owner, repo):
        job = make_implementation_job(
            workspace,
            repo,
            status=HarnessImplementationJob.Status.PENDING_GATE_A,
            created_by=owner,
        )
        fail_implementation_job_for_gate_a_decision(
            job.id, decision_status=ApprovalRequest.Status.EXPIRED
        )
        job.refresh_from_db()
        assert job.failure_reason == HarnessImplementationJob.FailureReason.GATE_EXPIRED

    def test_gate_b_rejection_fails_job_and_cleans_up_worktree(
        self, workspace, owner, repo, tmp_path
    ):
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        job = make_implementation_job(
            workspace,
            repo,
            status=HarnessImplementationJob.Status.PENDING_GATE_B,
            created_by=owner,
            worktree_path=str(worktree),
        )
        fail_implementation_job_for_gate_b_decision(
            job.id, decision_status=ApprovalRequest.Status.REJECTED
        )
        job.refresh_from_db()
        assert job.status == HarnessImplementationJob.Status.FAILED
        assert job.failure_reason == HarnessImplementationJob.FailureReason.GATE_B_REJECTED
        assert not worktree.exists()
        assert job.worktree_path == ""

    def test_wrong_status_is_a_safe_no_op(self, workspace, owner, repo):
        job = make_implementation_job(
            workspace, repo, status=HarnessImplementationJob.Status.SUCCEEDED, created_by=owner
        )
        fail_implementation_job_for_gate_a_decision(
            job.id, decision_status=ApprovalRequest.Status.REJECTED
        )
        job.refresh_from_db()
        assert job.status == HarnessImplementationJob.Status.SUCCEEDED
