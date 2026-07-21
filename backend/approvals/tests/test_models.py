import pytest

from accounts.services import register_user
from approvals.models import ApprovalRequest
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


def test_defaults(workspace):
    request = ApprovalRequest.objects.create(workspace=workspace, tool_name="vault_export")
    assert request.status == ApprovalRequest.Status.PENDING
    assert request.payload == {}
    assert request.decided_by is None
    assert request.decided_at is None
    assert str(request) == f"approval request {request.id} (vault_export, pending)"


def test_workspace_scoping(owner, workspace):
    stranger = register_user(email="stranger@test.dev", password=PASSWORD, name="Stranger")
    other_workspace = Workspace.objects.get(owner=stranger)
    ApprovalRequest.objects.create(workspace=workspace, tool_name="mine")
    ApprovalRequest.objects.create(workspace=other_workspace, tool_name="theirs")

    visible = ApprovalRequest.objects.for_actor(owner)
    assert visible.count() == 1
    assert visible.first().tool_name == "mine"
