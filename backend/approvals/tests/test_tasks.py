from datetime import timedelta

import pytest
from django.utils import timezone

from accounts.services import register_user
from approvals.models import ApprovalRequest
from approvals.tasks import sweep_expired_approval_requests
from audit.models import AuditLog
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


def make_request(workspace, requested_by, age_hours=0, **overrides):
    fields = {
        "workspace": workspace,
        "tool_name": "vault_export",
        "requested_by": requested_by,
    }
    fields.update(overrides)
    request = ApprovalRequest.objects.create(**fields)
    if age_hours:
        ApprovalRequest.objects.filter(pk=request.pk).update(
            created_at=timezone.now() - timedelta(hours=age_hours)
        )
    return request


def test_sweep_expires_only_stale_pending_requests(workspace, owner, settings):
    settings.APPROVAL_REQUEST_TTL_HOURS = 24
    stale = make_request(workspace, owner, age_hours=25)
    fresh = make_request(workspace, owner, age_hours=1)
    already_decided = make_request(
        workspace, owner, age_hours=25, status=ApprovalRequest.Status.APPROVED
    )

    count = sweep_expired_approval_requests()

    assert count == 1
    stale.refresh_from_db()
    fresh.refresh_from_db()
    already_decided.refresh_from_db()
    assert stale.status == ApprovalRequest.Status.EXPIRED
    assert fresh.status == ApprovalRequest.Status.PENDING
    assert already_decided.status == ApprovalRequest.Status.APPROVED
    assert "approval.expired" in AuditLog.objects.values_list("action", flat=True)


def test_sweep_is_a_noop_when_nothing_is_stale(workspace, owner):
    make_request(workspace, owner)
    assert sweep_expired_approval_requests() == 0
