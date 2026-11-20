"""`harness_implement`/`harness_push` ToolSpec wiring (Phase 3 branch 2,
brief §boundary) -- registration, tier-1 validation, and the
create_execution/dispatch status transitions. Full end-to-end
approve/reject flows through `approvals.services` live in
`test_gate_flows.py`; this file targets `harness.tools`'s own functions
directly."""

from unittest import mock

import pytest

from accounts.services import register_user
from approvals.models import ApprovalRequest
from harness.models import HarnessImplementationJob, HarnessRepo
from harness.tools import (
    create_harness_implement_execution,
    create_harness_push_execution,
    describe_harness_implement_execution,
    describe_harness_push_execution,
    dispatch_harness_implement_execution,
    dispatch_harness_push_execution,
    validate_harness_implement_request,
    validate_harness_push_request,
)
from tools.registry import ToolValidationError, get_tool
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


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
        name="friday",
        git_url="git@example.com:x/y.git",
        enabled=True,
        test_command="pytest -q",
    )


def make_implementation_job(workspace, repo, **overrides):
    fields = {"workspace": workspace, "repo": repo, "goal": "fix the bug"}
    fields.update(overrides)
    return HarnessImplementationJob.objects.create(**fields)


class ApprovalRequestStub:
    """A bare namespace, not a real `ApprovalRequest` row -- these
    functions only ever read `.payload`, mirroring
    `tools.export`'s own test doubles."""

    def __init__(self, payload):
        self.payload = payload


def test_both_tools_are_registered_gated():
    implement = get_tool("harness_implement")
    push = get_tool("harness_push")
    assert implement is not None and implement.gated is True
    assert push is not None and push.gated is True


class TestHarnessImplementValidation:
    def test_valid_pending_gate_a_job_passes(self, workspace, repo):
        job = make_implementation_job(
            workspace, repo, status=HarnessImplementationJob.Status.PENDING_GATE_A
        )
        validate_harness_implement_request(workspace, {"job_id": str(job.id)})

    def test_unknown_job_raises(self, workspace):
        with pytest.raises(ToolValidationError):
            validate_harness_implement_request(
                workspace, {"job_id": "00000000-0000-0000-0000-000000000000"}
            )

    def test_malformed_job_id_raises_validation_error_not_500(self, workspace):
        """Codex code-review P2, branch 2 round 7: a non-UUID `job_id` in
        a malformed/adversarial payload reaching the generic approvals
        API used to make the `pk` filter raise `ValidationError`
        directly, bubbling out as an unhandled 500 instead of the clean
        400 every other unknown-job case already gets."""
        with pytest.raises(ToolValidationError):
            validate_harness_implement_request(workspace, {"job_id": "not-a-uuid-at-all"})

    def test_wrong_workspace_raises(self, workspace, owner):
        other = Workspace.objects.create(name="Other", owner=owner)
        other_repo = HarnessRepo.objects.create(
            workspace=other,
            name="other-repo",
            git_url="git@example.com:a/b.git",
            enabled=True,
            test_command="pytest",
        )
        job = make_implementation_job(
            other, other_repo, status=HarnessImplementationJob.Status.PENDING_GATE_A
        )
        with pytest.raises(ToolValidationError):
            validate_harness_implement_request(workspace, {"job_id": str(job.id)})

    def test_wrong_status_raises(self, workspace, repo):
        job = make_implementation_job(
            workspace, repo, status=HarnessImplementationJob.Status.IMPLEMENTING
        )
        with pytest.raises(ToolValidationError):
            validate_harness_implement_request(workspace, {"job_id": str(job.id)})

    def test_already_requested_gate_a_raises(self, workspace, repo):
        job = make_implementation_job(
            workspace, repo, status=HarnessImplementationJob.Status.PENDING_GATE_A
        )
        fake_request = ApprovalRequest.objects.create(
            workspace=workspace, tool_name="harness_implement"
        )
        job.gate_a_request = fake_request
        job.save(update_fields=["gate_a_request"])
        with pytest.raises(ToolValidationError):
            validate_harness_implement_request(workspace, {"job_id": str(job.id)})


class TestHarnessImplementExecution:
    def test_create_execution_transitions_to_implementing(self, workspace, repo):
        job = make_implementation_job(
            workspace, repo, status=HarnessImplementationJob.Status.PENDING_GATE_A
        )
        request = ApprovalRequest.objects.create(
            workspace=workspace, tool_name="harness_implement", payload={"job_id": str(job.id)}
        )
        job.gate_a_request = request
        job.save(update_fields=["gate_a_request"])
        execution_id = create_harness_implement_execution(request)
        assert execution_id == str(job.id)
        job.refresh_from_db()
        assert job.status == HarnessImplementationJob.Status.IMPLEMENTING

    def test_does_not_revive_a_cancelled_job(self, workspace, repo):
        """Codex code-review P1, branch 2 round 1: a job can be cancelled
        while its Gate A request is still pending (`cancel_harness_
        implementation_job` moves it straight to `cancelled` without
        touching the still-pending `ApprovalRequest`); a curator who later
        approves that request anyway must not revive it back into
        `implementing`."""
        job = make_implementation_job(
            workspace, repo, status=HarnessImplementationJob.Status.CANCELLED
        )
        create_harness_implement_execution(ApprovalRequestStub({"job_id": str(job.id)}))
        job.refresh_from_db()
        assert job.status == HarnessImplementationJob.Status.CANCELLED

    def test_unlinked_approval_request_does_not_transition_the_job(self, workspace, repo):
        """Codex code-review P2, branch 2 round 7: `harness_implement` is
        a real, registered gated tool -- nothing stops a caller from
        requesting it directly through the generic approvals API with a
        fabricated payload naming an existing job. Approving THAT
        request (not the real one `create_harness_implementation_job`
        made) must not transition the job -- only the request actually
        linked as `job.gate_a_request` may."""
        job = make_implementation_job(
            workspace, repo, status=HarnessImplementationJob.Status.PENDING_GATE_A
        )
        real_request = ApprovalRequest.objects.create(
            workspace=workspace, tool_name="harness_implement"
        )
        job.gate_a_request = real_request
        job.save(update_fields=["gate_a_request"])
        unlinked_request = ApprovalRequest.objects.create(
            workspace=workspace, tool_name="harness_implement", payload={"job_id": str(job.id)}
        )
        create_harness_implement_execution(unlinked_request)
        job.refresh_from_db()
        assert job.status == HarnessImplementationJob.Status.PENDING_GATE_A

    def test_dispatch_calls_the_task(self, settings):
        settings.CELERY_TASK_ALWAYS_EAGER = False
        with mock.patch("harness.tasks.run_harness_implementation_job") as task:
            dispatch_harness_implement_execution("some-id")
            task.delay.assert_called_once_with("some-id")

    def test_dispatch_swallows_broker_publish_failure(self, settings):
        settings.CELERY_TASK_ALWAYS_EAGER = False
        with mock.patch("harness.tasks.run_harness_implementation_job") as task:
            task.delay.side_effect = Exception("broker down")
            dispatch_harness_implement_execution("some-id")  # must not raise

    def test_describe_execution_returns_status_summary(self, workspace, repo):
        job = make_implementation_job(
            workspace, repo, status=HarnessImplementationJob.Status.IMPLEMENTING
        )
        summary = describe_harness_implement_execution(ApprovalRequestStub({"job_id": str(job.id)}))
        assert summary["status"] == HarnessImplementationJob.Status.IMPLEMENTING

    def test_describe_execution_returns_none_for_unknown_job(self, workspace):
        assert describe_harness_implement_execution(ApprovalRequestStub({})) is None


class TestHarnessPushValidation:
    def test_valid_pending_review_job_passes(self, workspace, repo):
        job = make_implementation_job(
            workspace, repo, status=HarnessImplementationJob.Status.PENDING_REVIEW
        )
        validate_harness_push_request(workspace, {"job_id": str(job.id)})

    def test_wrong_status_raises(self, workspace, repo):
        job = make_implementation_job(
            workspace, repo, status=HarnessImplementationJob.Status.PENDING_GATE_A
        )
        with pytest.raises(ToolValidationError):
            validate_harness_push_request(workspace, {"job_id": str(job.id)})

    def test_already_requested_gate_b_raises(self, workspace, repo):
        job = make_implementation_job(
            workspace, repo, status=HarnessImplementationJob.Status.PENDING_REVIEW
        )
        fake_request = ApprovalRequest.objects.create(workspace=workspace, tool_name="harness_push")
        job.gate_b_request = fake_request
        job.save(update_fields=["gate_b_request"])
        with pytest.raises(ToolValidationError):
            validate_harness_push_request(workspace, {"job_id": str(job.id)})


class TestHarnessPushExecution:
    def test_create_execution_transitions_to_pushing(self, workspace, repo):
        job = make_implementation_job(
            workspace, repo, status=HarnessImplementationJob.Status.PENDING_GATE_B
        )
        request = ApprovalRequest.objects.create(
            workspace=workspace, tool_name="harness_push", payload={"job_id": str(job.id)}
        )
        job.gate_b_request = request
        job.save(update_fields=["gate_b_request"])
        execution_id = create_harness_push_execution(request)
        assert execution_id == str(job.id)
        job.refresh_from_db()
        assert job.status == HarnessImplementationJob.Status.PUSHING

    def test_does_not_revive_a_cancelled_job(self, workspace, repo):
        """Same as `TestHarnessImplementExecution`'s identical test, one
        gate over (Codex code-review P1, branch 2 round 1)."""
        job = make_implementation_job(
            workspace, repo, status=HarnessImplementationJob.Status.CANCELLED
        )
        create_harness_push_execution(ApprovalRequestStub({"job_id": str(job.id)}))
        job.refresh_from_db()
        assert job.status == HarnessImplementationJob.Status.CANCELLED

    def test_unlinked_approval_request_does_not_transition_the_job(self, workspace, repo):
        """Codex code-review P2, branch 2 round 7 -- see
        `TestHarnessImplementExecution`'s identical test for the full
        rationale, one gate over. Especially important here: an unlinked
        `harness_push` request could be created (and pass validation)
        while the job is still `pending_review`, well before the real
        Gate B request (carrying the actual diff summary and review
        verdict) exists at all."""
        job = make_implementation_job(
            workspace, repo, status=HarnessImplementationJob.Status.PENDING_GATE_B
        )
        real_request = ApprovalRequest.objects.create(workspace=workspace, tool_name="harness_push")
        job.gate_b_request = real_request
        job.save(update_fields=["gate_b_request"])
        unlinked_request = ApprovalRequest.objects.create(
            workspace=workspace, tool_name="harness_push", payload={"job_id": str(job.id)}
        )
        create_harness_push_execution(unlinked_request)
        job.refresh_from_db()
        assert job.status == HarnessImplementationJob.Status.PENDING_GATE_B

    def test_dispatch_calls_the_task(self, settings):
        settings.CELERY_TASK_ALWAYS_EAGER = False
        with mock.patch("harness.tasks.run_harness_push_job") as task:
            dispatch_harness_push_execution("some-id")
            task.delay.assert_called_once_with("some-id")

    def test_describe_execution_returns_push_result(self, workspace, repo):
        job = make_implementation_job(
            workspace,
            repo,
            status=HarnessImplementationJob.Status.SUCCEEDED,
            push_result={"pushed": True},
        )
        summary = describe_harness_push_execution(ApprovalRequestStub({"job_id": str(job.id)}))
        assert summary["push_result"] == {"pushed": True}
