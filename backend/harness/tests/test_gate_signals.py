"""`harness.signals`'s post_save receiver on `ApprovalRequest` -- fires
through the REAL `approvals.services.reject_request`/expiry path, not a
bare call to the receiver function in isolation, mirroring the brief's
own stated test discipline for `harness.apps.HarnessConfig.ready()`'s
receiver connection (a direct test asserts the receiver actually fires on
a real `ApprovalRequest` save)."""

import pytest
from django.utils import timezone

from accounts.services import register_user
from approvals.models import ApprovalRequest
from approvals.services import InvalidTransition, reject_request
from core.channels import CHANNEL_WEB
from harness.models import HarnessImplementationJob, HarnessRepo
from harness.services import create_harness_implementation_job
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
        name="jarvis-os",
        git_url="git@example.com:x/y.git",
        enabled=True,
        test_command="pytest -q",
    )


def test_rejecting_gate_a_fails_the_implementation_job(
    workspace, owner, repo, django_capture_on_commit_callbacks
):
    job = create_harness_implementation_job(
        workspace=workspace, actor=owner, repo=repo, goal="fix the bug"
    )
    with django_capture_on_commit_callbacks(execute=True):
        reject_request(approval_request=job.gate_a_request, actor=owner, channel=CHANNEL_WEB)
    job.refresh_from_db()
    assert job.status == HarnessImplementationJob.Status.FAILED
    assert job.failure_reason == HarnessImplementationJob.FailureReason.GATE_A_REJECTED


def test_expiring_gate_a_fails_the_implementation_job_as_expired(
    workspace, owner, repo, settings, django_capture_on_commit_callbacks
):
    job = create_harness_implementation_job(
        workspace=workspace, actor=owner, repo=repo, goal="fix the bug"
    )
    settings.APPROVAL_REQUEST_TTL_HOURS = 0
    request = job.gate_a_request
    request.created_at = timezone.now() - timezone.timedelta(hours=1)
    request.save(update_fields=["created_at"])
    # The decision-time expiry backstop fires as part of any decide
    # attempt (approve or reject) once the request is stale -- calling
    # reject_request here exercises exactly that path.
    with django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(InvalidTransition):
            reject_request(approval_request=request, actor=owner, channel=CHANNEL_WEB)
    job.refresh_from_db()
    assert job.status == HarnessImplementationJob.Status.FAILED
    assert job.failure_reason == HarnessImplementationJob.FailureReason.GATE_EXPIRED


def test_approving_gate_a_does_not_go_through_the_reject_path(
    workspace, owner, repo, django_capture_on_commit_callbacks
):
    """Approval is handled by `approve_request`'s own inline
    `tool.create_execution()`/`tool.dispatch()` call, not this receiver --
    this just confirms the receiver doesn't ALSO fail the job on the
    approved branch. The actual worktree/container run is mocked out --
    this test only cares about the gate decision's own status transition,
    not the full implementation pipeline."""
    from unittest import mock

    from approvals.services import approve_request

    job = create_harness_implementation_job(
        workspace=workspace, actor=owner, repo=repo, goal="fix the bug"
    )
    with (
        mock.patch("harness.tasks.run_harness_implementation_job") as task,
        django_capture_on_commit_callbacks(execute=True),
    ):
        approve_request(approval_request=job.gate_a_request, actor=owner, channel=CHANNEL_WEB)
    task.delay.assert_called_once_with(str(job.id))
    job.refresh_from_db()
    assert job.status == HarnessImplementationJob.Status.IMPLEMENTING


def test_unrelated_approval_request_is_a_no_op(
    workspace, owner, django_capture_on_commit_callbacks
):
    """An `ApprovalRequest` with no linked `HarnessImplementationJob` at
    all (e.g. a plain vault_export request) must not raise or otherwise
    misbehave when this receiver runs against it -- mirrors
    `agents/signals.py`'s own `agent_step is None: return` no-op shape."""
    request = ApprovalRequest.objects.create(
        workspace=workspace,
        tool_name="vault_export",
        status=ApprovalRequest.Status.PENDING,
        requested_by=owner,
        origin=ApprovalRequest.Origin.HUMAN,
    )
    with django_capture_on_commit_callbacks(execute=True):
        request.status = ApprovalRequest.Status.REJECTED
        request.save(update_fields=["status"])  # must not raise
