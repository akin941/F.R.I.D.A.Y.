"""Phase 5 branch 1 (ADR-010): the harness is the owning service of Gates
A and B, so it -- and only it -- passes `system_created=True`. The gates
are still minted exactly as before (origin HARNESS, system channel), and
stating the harness origin without the flag no longer mints one."""

from unittest import mock

import pytest

from accounts.services import register_user
from approvals.models import ApprovalRequest
from approvals.services import InvalidTransition, create_request
from audit.models import AuditLog
from core.channels import CHANNEL_SYSTEM
from harness import services
from harness.models import HarnessImplementationJob, HarnessRepo, HarnessReview
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


@pytest.fixture
def spy():
    calls = []
    real = services.create_request

    def wrapper(**kwargs):
        calls.append(kwargs)
        return real(**kwargs)

    with mock.patch.object(services, "create_request", wrapper):
        yield calls


def _requested_audit(request):
    return AuditLog.objects.get(action="approval.requested", resource_id=str(request.id))


def test_gate_a_is_minted_system_created(workspace, owner, repo, spy):
    job = services.create_harness_implementation_job(
        workspace=workspace, actor=owner, repo=repo, goal="fix the bug"
    )
    assert [call["system_created"] for call in spy] == [True]
    gate = job.gate_a_request
    assert gate.tool_name == "harness_implement"
    assert gate.origin == ApprovalRequest.Origin.HARNESS
    assert gate.requested_channel == ApprovalRequest.RequestedChannel.SYSTEM
    assert _requested_audit(gate).metadata["system_created"] is True


def test_gate_b_is_minted_system_created(workspace, owner, repo, spy):
    job = HarnessImplementationJob.objects.create(
        workspace=workspace,
        repo=repo,
        goal="fix the bug",
        agent=HarnessImplementationJob.Agent.CLAUDE_CODE,
        status=HarnessImplementationJob.Status.PENDING_REVIEW,
        created_by=owner,
    )
    review = HarnessReview.objects.create(
        workspace=workspace,
        implementation_job=job,
        agent=HarnessReview.Agent.CODEX_CLI,
        status=HarnessReview.Status.SUCCEEDED,
        verdict=HarnessReview.Verdict.APPROVED,
    )
    updated = services.request_harness_push(job=job, review=review)
    assert [call["system_created"] for call in spy] == [True]
    gate = updated.gate_b_request
    assert gate.tool_name == "harness_push"
    assert gate.origin == ApprovalRequest.Origin.HARNESS
    assert _requested_audit(gate).metadata["system_created"] is True


@pytest.mark.parametrize("tool_name", ["harness_implement", "harness_push"])
def test_harness_origin_without_the_flag_is_refused(workspace, owner, tool_name):
    with pytest.raises(InvalidTransition, match="only be requested by the system"):
        create_request(
            workspace=workspace,
            actor=owner,
            tool_name=tool_name,
            payload={},
            origin=ApprovalRequest.Origin.HARNESS,
            channel=CHANNEL_SYSTEM,
        )
    assert not ApprovalRequest.objects.filter(tool_name=tool_name).exists()
