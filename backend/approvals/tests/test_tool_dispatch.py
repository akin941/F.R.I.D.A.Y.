"""Branch-2 wiring (brief §2): `approvals.services.approve_request` calls a
registered tool's `create_execution()` inside its own transaction, then
`dispatch()` via `transaction.on_commit`. Uses a trivial in-test-only stub
`ToolSpec` -- no real backing table needed to prove call ordering; branch 3
proves the same invariant again with a real `VaultExportRun` row."""

import pytest

from accounts.services import register_user
from approvals.models import ApprovalRequest
from approvals.services import approve_request
from tools.registry import ToolSpec, register_tool, unregister_tool
from workspaces.models import Workspace, WorkspaceMember

PASSWORD = "Str0ngDevPass!42"
_TOOL_NAME = "stub_dispatch_tool"


@pytest.fixture
def recorded_calls():
    return []


@pytest.fixture
def stub_tool(recorded_calls):
    def create_execution(approval_request):
        recorded_calls.append(("create_execution", approval_request.pk))
        return "exec-sentinel-1"

    def dispatch(execution_id):
        recorded_calls.append(("dispatch", execution_id))

    spec = ToolSpec(
        name=_TOOL_NAME, gated=True, create_execution=create_execution, dispatch=dispatch
    )
    register_tool(spec)
    yield spec
    unregister_tool(_TOOL_NAME)


def _set_up_workspace_with_admin(owner_email, admin_email):
    owner = register_user(email=owner_email, password=PASSWORD, name="Owner")
    workspace = Workspace.objects.get(owner=owner)
    admin = register_user(email=admin_email, password=PASSWORD, name="Admin")
    WorkspaceMember.objects.create(workspace=workspace, user=admin, role=WorkspaceMember.Role.ADMIN)
    return owner, workspace, admin


def _make_request(workspace, requested_by, tool_name=_TOOL_NAME):
    return ApprovalRequest.objects.create(
        workspace=workspace,
        tool_name=tool_name,
        status=ApprovalRequest.Status.PENDING,
        requested_by=requested_by,
    )


@pytest.mark.django_db(transaction=True)
def test_create_execution_then_dispatch_fire_in_order(stub_tool, recorded_calls):
    owner, workspace, admin = _set_up_workspace_with_admin(
        "dispatch-owner@test.dev", "dispatch-admin@test.dev"
    )
    request = _make_request(workspace, requested_by=owner)

    approve_request(approval_request=request, actor=admin)

    assert recorded_calls == [
        ("create_execution", request.pk),
        ("dispatch", "exec-sentinel-1"),
    ]


def test_dispatch_is_deferred_to_a_real_commit(stub_tool, recorded_calls, db):
    """Deliberately NOT `transaction=True`: pytest-django wraps a plain
    `db`-marked test in an outer atomic block that is rolled back at the
    end, so it never truly commits -- `transaction.on_commit` callbacks
    registered inside it are captured but never fire. `create_execution`
    must still have been called (it runs inline, inside the transaction),
    while `dispatch` must NOT appear -- proving dispatch is genuinely
    deferred to a real commit, not invoked eagerly alongside creation."""
    owner, workspace, admin = _set_up_workspace_with_admin(
        "dispatch-owner2@test.dev", "dispatch-admin2@test.dev"
    )
    request = _make_request(workspace, requested_by=owner)

    approve_request(approval_request=request, actor=admin)

    assert recorded_calls == [("create_execution", request.pk)]


@pytest.mark.django_db(transaction=True)
def test_unregistered_tool_name_skips_dispatch_without_error(recorded_calls):
    """`_resolve_tool` returning None (nothing registered under this name)
    is a legitimate no-op, not an error -- approving still succeeds."""
    owner, workspace, admin = _set_up_workspace_with_admin(
        "dispatch-owner3@test.dev", "dispatch-admin3@test.dev"
    )
    request = _make_request(workspace, requested_by=owner, tool_name="nothing_registered")

    decided = approve_request(approval_request=request, actor=admin)

    assert decided.status == ApprovalRequest.Status.APPROVED
    assert recorded_calls == []
