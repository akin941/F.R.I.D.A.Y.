"""The `node_action` request and decision rules (Phase 10 brief D6, D9
stub): the actor and approver checks, the pre-transaction precheck, the
trust-check ports, the snapshot and the workspace isolation rules."""

import pytest
from django.db import connection
from django.utils import timezone

from ai_core.models import ModelUsage
from approvals.models import ApprovalRequest
from approvals.services import (
    ApprovalPermissionDenied,
    InvalidTransition,
    approve_request,
    create_request,
    reject_request,
)
from audit.models import AuditLog
from core.channels import CHANNEL_WEB
from nodes import bus, services
from nodes.models import NodeActionExecution, NodeInvocation
from tools import node_action_ports, node_actions
from tools.node_action_ports import TrustVerdict
from tools.registry import get_tool
from tools.tests.conftest import REPO_ARGS, _user
from workspaces.models import WorkspaceMember

HUMAN = ApprovalRequest.Origin.HUMAN
PERSONAL_ARGS = {"repo_path": "~/code/friday", "command_kind": "test"}
AX_PRESS = {"bundle_id": "com.apple.Terminal", "role": "button", "title": "Tamam"}


def _payload(world, *, node_id=None, capability="repo.command.run", args=None, **extra):
    return {
        "node_id": str(node_id or world.node.pk),
        "capability": capability,
        "args": dict(REPO_ARGS) if args is None else args,
        "source_message_id": "",
        **extra,
    }


def request(world, workspace=None, *, actor=None, **payload):
    return create_request(
        workspace=workspace or world.firm,
        actor=actor or world.owner,
        tool_name="node_action",
        payload=_payload(world, **payload),
        origin=HUMAN,
        channel=CHANNEL_WEB,
    )


def _refusal(callable_, *args, **kwargs):
    with pytest.raises(InvalidTransition) as info:
        callable_(*args, **kwargs)
    return str(info.value)


@pytest.fixture
def calls():
    """A recording trust check (and a FakeAdapter-free one: it makes no
    model call, so no ModelUsage row may ever appear)."""
    seen = []

    def check(**kwargs):
        seen.append(connection.in_atomic_block)
        return TrustVerdict("ok", "consistent", ("typed",))

    node_action_ports.register_trust_check(check)
    return seen


@pytest.fixture(params=["firm", "personal"])
def scope(request, node_world):
    """The workspace under test with its repo args; in the personal
    workspace the member and the curator join it too, so both kinds of
    second user exist in both workspaces."""
    if request.param == "firm":
        return node_world.firm, dict(REPO_ARGS)
    for user, role in (
        (node_world.member, WorkspaceMember.Role.MEMBER),
        (node_world.curator, WorkspaceMember.Role.ADMIN),
    ):
        WorkspaceMember.objects.create(workspace=node_world.personal, user=user, role=role)
    return node_world.personal, dict(PERSONAL_ARGS)


def _refused_audits():
    return AuditLog.objects.filter(action="nodes.refused")


# --- the actor check (D6) -----------------------------------------------


@pytest.mark.django_db
def test_naming_another_users_node_is_actor_not_owner(node_world, scope, calls):
    workspace, args = scope
    message = _refusal(request, node_world, workspace, node_id=node_world.other_node.pk, args=args)
    assert message == "actor_not_owner"
    assert not ApprovalRequest.objects.exists()
    audit = _refused_audits().get()
    assert audit.metadata["node_id"] == str(node_world.other_node.pk)
    assert audit.metadata["capability"] == "repo.command.run"
    assert calls == []


@pytest.mark.django_db
def test_an_unknown_node_gets_the_same_answer(node_world, scope, calls):
    workspace, args = scope
    unknown = _refusal(
        request, node_world, workspace, node_id="00000000-0000-4000-8000-000000000000", args=args
    )
    foreign = _refusal(request, node_world, workspace, node_id=node_world.other_node.pk, args=args)
    assert unknown == foreign == "actor_not_owner"
    assert _refused_audits().count() == 2
    assert calls == []


@pytest.mark.django_db
def test_a_writer_who_is_not_the_node_owner_never_reaches_the_trust_check(node_world, scope, calls):
    workspace, args = scope
    assert _refusal(request, node_world, workspace, actor=node_world.member, args=args) == (
        "actor_not_owner"
    )
    assert calls == [] and not ModelUsage.objects.exists()


@pytest.fixture
def hook_spy():
    """Re-register `node_action` with a recording `precheck_request`."""
    import dataclasses

    from tools.registry import register_tool

    spec = get_tool("node_action")
    seen = []

    def spy(workspace, actor, payload):
        seen.append(actor)
        return node_actions.precheck_request(workspace, actor, payload)

    register_tool(dataclasses.replace(spec, precheck_request=spy))
    yield seen
    register_tool(spec)


@pytest.mark.django_db
def test_a_viewer_never_reaches_the_hook(node_world, calls, hook_spy):
    viewer = _user("viewer@test.dev")
    WorkspaceMember.objects.create(
        workspace=node_world.firm, user=viewer, role=WorkspaceMember.Role.VIEWER
    )
    with pytest.raises(ApprovalPermissionDenied):
        request(node_world, actor=viewer)
    assert hook_spy == [] and calls == [] and not ModelUsage.objects.exists()
    assert not _refused_audits().exists()  # refused before the hook, by role
    request(node_world)
    assert hook_spy == [node_world.owner]


@pytest.mark.django_db
def test_an_agent_account_never_reaches_the_trust_check(node_world, calls):
    from accounts.models import User

    agent = User.objects.create_user(email="agent@test.dev", is_agent=True)
    WorkspaceMember.objects.create(
        workspace=node_world.firm, user=agent, role=WorkspaceMember.Role.MEMBER
    )
    assert _refusal(request, node_world, actor=agent) == "actor_not_owner"
    assert calls == [] and not ModelUsage.objects.exists()


@pytest.mark.django_db
def test_anonymous_never_reaches_the_hook(node_world, calls, hook_spy):
    from rest_framework.test import APIClient

    response = APIClient().post(
        f"/api/v1/approvals/requests?workspace={node_world.firm.pk}",
        {"tool_name": "node_action", "payload": _payload(node_world)},
        format="json",
    )
    assert response.status_code == 401
    assert hook_spy == [] and calls == [] and not ApprovalRequest.objects.exists()


# --- the approver check (D6) --------------------------------------------


@pytest.mark.django_db
def test_a_non_owner_curator_cannot_approve(node_world, scope):
    workspace, args = scope
    approval = request(node_world, workspace, args=args)
    with pytest.raises(ApprovalPermissionDenied):
        approve_request(approval_request=approval, actor=node_world.curator, channel=CHANNEL_WEB)
    approval.refresh_from_db()
    assert approval.status == "pending" and approval.decided_by is None
    assert not NodeActionExecution.objects.exists()
    assert bus.drain_inbox(node_world.node.pk) == []


@pytest.mark.django_db
def test_a_non_owner_curator_can_still_reject(node_world, scope):
    workspace, args = scope
    approval = request(node_world, workspace, args=args)
    reject_request(approval_request=approval, actor=node_world.curator, channel=CHANNEL_WEB)
    approval.refresh_from_db()
    assert approval.status == "rejected"


def _forged(world, approval, *, requested_by, decided_by):
    return ApprovalRequest.objects.create(
        workspace=approval.workspace,
        tool_name="node_action",
        payload=approval.payload,
        status=ApprovalRequest.Status.APPROVED,
        requested_by=requested_by,
        decided_by=decided_by,
        decided_at=timezone.now(),
        origin=HUMAN,
        requested_channel=CHANNEL_WEB,
    )


@pytest.mark.django_db
@pytest.mark.parametrize("forged_field", ["requested_by", "decided_by"])
def test_forged_rows_with_another_actor_sign_nothing(node_world, scope, forged_field):
    workspace, args = scope
    template = request(node_world, workspace, args=args)
    actors = {"requested_by": node_world.owner, "decided_by": node_world.owner}
    actors[forged_field] = node_world.curator
    forged = _forged(node_world, template, **actors)
    execution_id = services.create_gated_execution(forged)
    execution = services.dispatch_gated(execution_id)
    assert (execution.status, execution.refusal_code) == ("server_refused", "actor_not_owner")
    assert execution.grant_id is None
    assert bus.drain_inbox(node_world.node.pk) == []
    assert NodeInvocation.objects.filter(trace_id=execution.trace_id).count() == 1
    assert (
        AuditLog.objects.filter(action="nodes.invoke", resource_id=str(execution_id)).count() == 1
    )


def _approved_execution(world, workspace, args):
    approval = request(world, workspace, args=args)
    approve_request(approval_request=approval, actor=world.owner, channel=CHANNEL_WEB)
    return NodeActionExecution.objects.get(approval=approval)


@pytest.mark.django_db
def test_the_owner_losing_the_owner_role_before_dispatch(node_world, scope):
    workspace, args = scope
    execution = _approved_execution(node_world, workspace, args)
    WorkspaceMember.objects.filter(workspace=workspace, user=node_world.owner).update(
        role=WorkspaceMember.Role.ADMIN
    )
    execution = services.dispatch_gated(execution.pk)
    assert (execution.status, execution.refusal_code) == ("server_refused", "actor_not_owner")


@pytest.mark.django_db
def test_the_owner_deactivated_before_dispatch(node_world, scope):
    workspace, args = scope
    execution = _approved_execution(node_world, workspace, args)
    type(node_world.owner).objects.filter(pk=node_world.owner.pk).update(is_active=False)
    execution = services.dispatch_gated(execution.pk)
    assert (execution.status, execution.refusal_code) == ("server_refused", "actor_not_owner")
    assert bus.drain_inbox(node_world.node.pk) == []


# --- the precheck (D6) --------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_the_trust_check_runs_outside_any_transaction(node_world, calls):
    approval = request(node_world)
    assert calls == [False]
    assert approval.payload["trust"] == {
        "verdict": "ok",
        "reason_code": "consistent",
        "evidence_modes": ["typed"],
    }


@pytest.mark.django_db
def test_a_client_sent_trust_key_is_dropped(node_world):
    approval = request(node_world, trust={"verdict": "ok", "reason_code": "consistent"})
    assert approval.payload["trust"]["verdict"] == "warn"
    assert approval.payload["trust"]["reason_code"] == "trust_check_pending"


@pytest.mark.django_db
def test_the_snapshot_holds_exactly_the_d6_keys(node_world):
    approval = request(node_world, extra_key="smuggled", source_input_mode="voice")
    assert set(approval.payload) == {
        "node_id",
        "capability",
        "capability_version",
        "class",
        "args",
        "args_sha256",
        "workspace_id",
        "source_message_id",
        "source_input_mode",
        "trust",
        "data_class",
    }
    assert approval.payload["source_input_mode"] == "unknown"  # never the client's
    assert approval.payload["class"] == "gate"
    assert approval.payload["workspace_id"] == str(node_world.firm.pk)


def test_prepare_payload_refuses_without_a_precheck_object():
    from tools.registry import ToolValidationError

    with pytest.raises(ToolValidationError):
        node_actions.prepare_payload(None, {"node_id": "x"}, precheck={"trust": "ok"})


# --- the trust-check ports (D9 stub) ------------------------------------


@pytest.mark.django_db
def test_a_typed_proposal_stores_the_pending_verdict(node_world):
    approval = request(node_world)
    assert approval.payload["trust"]["verdict"] == "warn"
    assert approval.payload["trust"]["reason_code"] == "trust_check_pending"


@pytest.mark.django_db
def test_an_unregistered_trust_check_blocks(node_world):
    node_action_ports.reset_ports()
    assert _refusal(request, node_world) == "trust_block"
    assert not ApprovalRequest.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "check",
    [
        lambda **kwargs: 1 / 0,
        lambda **kwargs: {"verdict": "ok"},
        lambda **kwargs: TrustVerdict("approve", "consistent"),
        lambda **kwargs: TrustVerdict("ok", "free text"),
        lambda **kwargs: TrustVerdict("block", "inconsistent"),
    ],
    ids=["raises", "not_a_verdict", "unknown_verdict", "unknown_reason", "block"],
)
def test_a_failing_or_blocking_trust_check_refuses(node_world, check):
    node_action_ports.register_trust_check(check)
    assert _refusal(request, node_world) == "trust_block"


@pytest.mark.django_db
def test_the_evidence_source_records_a_voice_turn(node_world):
    node_action_ports.register_evidence_source(lambda **kwargs: "voice")
    try:
        approval = request(node_world)
    finally:
        node_action_ports._evidence_source = None
    assert approval.payload["source_input_mode"] == "voice"
    summary = get_tool("node_action").describe_request(approval)
    assert summary["voice"] is True and summary["voice_label"] == "Sesle önerildi"


@pytest.mark.django_db
def test_describe_request_is_a_whitelisted_turkish_summary(node_world):
    approval = request(node_world)
    summary = get_tool("node_action").describe_request(approval)
    assert summary["title"] == "Repoda komut çalıştır"
    assert summary["repo_name"] == "acme"
    assert summary["command_kind"] == "test"
    assert summary["node_name"] == "MacBook"
    assert summary["note"] == "Mac'teki onay penceresi kendi metnini gösterir"
    assert summary["trust_verdict"] == "warn"
    assert summary["voice"] is False
    assert "~/work/acme" not in str(summary)


def test_the_tool_is_gated_proposable_and_lives_ten_minutes():
    spec = get_tool("node_action")
    assert spec.gated and spec.user_invocable and spec.proposable and spec.records_work_log
    assert spec.approval_ttl_seconds == 600
    assert spec.precheck_request is node_actions.precheck_request
    assert spec.authorize_decision is node_actions.authorize_decision


# --- workspace isolation (D3) -------------------------------------------


def _recipe(name, **params):
    return {"recipe": name, "params": params}


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("capability", "args", "code"),
    [
        ("ax.press", AX_PRESS, "workspace_not_allowed"),
        ("applescript.recipe.run", _recipe("music.play_pause"), "workspace_not_allowed"),
        ("apps.open", {"bundle_id": "com.apple.Terminal"}, "confirm_class"),
        ("apps.focus", {"bundle_id": "com.apple.Terminal"}, "confirm_class"),
        (
            "applescript.recipe.run",
            _recipe("finder.reveal_repo", repo_path="~/code/friday"),
            "path_not_in_workspace",
        ),
        (
            "applescript.recipe.run",
            _recipe("finder.reveal_repo", repo_path="~/elsewhere/x"),
            "path_not_in_workspace",
        ),
        ("repo.command.run", PERSONAL_ARGS, "path_not_in_workspace"),
        ("repo.command.run", {"repo_path": "~/elsewhere", "command_kind": "test"}, None),
    ],
)
def test_a_firm_conversation_is_refused_personal_capabilities(node_world, capability, args, code):
    code = code or "path_not_in_workspace"
    assert _refusal(request, node_world, capability=capability, args=args) == code


@pytest.mark.django_db
def test_a_firm_conversation_can_request_a_repo_recipe_of_its_own_repo(node_world):
    approval = request(
        node_world,
        capability="applescript.recipe.run",
        args=_recipe("finder.reveal_repo", repo_path="~/work/acme"),
    )
    assert approval.payload["data_class"] == "firm"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("capability", "args", "data_class"),
    [
        (
            "applescript.recipe.run",
            _recipe("finder.reveal_repo", repo_path="~/code/friday"),
            "personal",
        ),
        ("applescript.recipe.run", _recipe("music.play_pause"), "personal"),
        ("ax.press", AX_PRESS, "personal"),
        ("repo.command.run", PERSONAL_ARGS, "firm"),
    ],
)
def test_the_personal_workspace_may_request_personal_capabilities(
    node_world, capability, args, data_class
):
    approval = request(node_world, node_world.personal, capability=capability, args=args)
    assert approval.payload["data_class"] == data_class


@pytest.mark.django_db
def test_the_personal_workspace_cannot_request_a_firm_repo(node_world):
    assert (
        _refusal(
            request,
            node_world,
            node_world.personal,
            capability="applescript.recipe.run",
            args=_recipe("finder.reveal_repo", repo_path="~/work/acme"),
        )
        == "path_not_in_workspace"
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("capability", "args", "code"),
    [
        ("nope.run", {}, "unknown_capability"),
        ("git.status", {"repo_path": "~/work/acme"}, "unknown_capability"),
        ("repo.command.run", {"repo_path": "~/work/acme", "command_kind": "rm"}, "args_contract"),
        ("repo.command.run", {"repo_path": "~/work/../x", "command_kind": "test"}, "args_contract"),
    ],
)
def test_unknown_capabilities_and_bad_args_are_refused(node_world, capability, args, code):
    assert _refusal(request, node_world, capability=capability, args=args) == code


@pytest.mark.django_db
def test_a_non_invocable_capability_is_refused(node_world):
    from tools.tests.conftest import advertise

    advertise(node_world.node, [])
    assert _refusal(request, node_world) == "not_invocable"


@pytest.mark.django_db
def test_executions_are_invisible_from_the_other_workspace(node_world):
    execution = _approved_execution(node_world, node_world.firm, dict(REPO_ARGS))
    assert (
        NodeActionExecution.objects.for_actor(node_world.owner, workspace=node_world.firm)
        .filter(pk=execution.pk)
        .exists()
    )
    assert (
        not NodeActionExecution.objects.for_actor(node_world.owner, workspace=node_world.personal)
        .filter(pk=execution.pk)
        .exists()
    )
    outsider = _user("outsider@test.dev")
    assert not NodeActionExecution.objects.for_actor(outsider).filter(pk=execution.pk).exists()
