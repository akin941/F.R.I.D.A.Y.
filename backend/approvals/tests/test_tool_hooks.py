"""Phase 10 branch 3 (brief D6) approvals mechanics: the per-tool approval
TTL, the pre-transaction `precheck_request` hook (the `create_request`
split) and the `authorize_decision` approver hook -- exercised with stub
tools, so they hold for every future tool, not only `node_action`."""

import dataclasses
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from accounts.services import register_user
from approvals import services
from approvals.models import ApprovalRequest
from approvals.services import (
    ApprovalExpired,
    ApprovalPermissionDenied,
    InvalidTransition,
    approve_request,
    create_request,
    reject_request,
)
from approvals.tasks import sweep_expired_approval_requests
from core.channels import CHANNEL_WEB
from tools.registry import (
    ToolDecisionForbidden,
    ToolSpec,
    ToolValidationError,
    register_tool,
    unregister_tool,
)
from workspaces.models import Workspace, WorkspaceMember

PASSWORD = "Str0ngDevPass!42"
HUMAN = ApprovalRequest.Origin.HUMAN


@pytest.fixture
def owner(db):
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


@pytest.fixture
def tools():
    """Register stub tools for the test and remove them afterwards."""
    names = []

    def register(name, **fields):
        spec = ToolSpec(
            name=name,
            gated=True,
            create_execution=lambda request: None,
            dispatch=lambda execution_id: None,
            **fields,
        )
        register_tool(spec)
        names.append(name)
        return spec

    yield register
    for name in names:
        unregister_tool(name)


def _create(workspace, actor, tool_name, payload=None):
    return create_request(
        workspace=workspace,
        actor=actor,
        tool_name=tool_name,
        payload=payload or {},
        origin=HUMAN,
        channel=CHANNEL_WEB,
    )


def _age(request, seconds):
    ApprovalRequest.objects.filter(pk=request.pk).update(
        created_at=timezone.now() - timedelta(seconds=seconds)
    )


# --- per-tool TTL -------------------------------------------------------


def test_a_tool_without_its_own_ttl_keeps_the_global_24_hours(workspace, owner, tools):
    tools("stub_plain")
    request = _create(workspace, owner, "stub_plain")
    _age(request, 23 * 3600)
    approve_request(approval_request=request, actor=owner, channel=CHANNEL_WEB)
    late = _create(workspace, owner, "stub_plain")
    _age(late, 24 * 3600 + 5)
    with pytest.raises(ApprovalExpired):
        approve_request(approval_request=late, actor=owner, channel=CHANNEL_WEB)


def test_a_short_tool_ttl_expires_at_decision_time(workspace, owner, tools):
    tools("stub_short", approval_ttl_seconds=600)
    request = _create(workspace, owner, "stub_short")
    _age(request, 601)
    with pytest.raises(ApprovalExpired):
        reject_request(approval_request=request, actor=owner, channel=CHANNEL_WEB)
    request.refresh_from_db()
    assert request.status == "expired"


def test_a_tool_ttl_longer_than_the_global_one_is_capped(workspace, owner, tools, settings):
    tools("stub_long", approval_ttl_seconds=48 * 3600)
    assert services.approval_ttl("stub_long") == timedelta(hours=24)
    settings.APPROVAL_REQUEST_TTL_HOURS = 1
    assert services.approval_ttl("stub_long") == timedelta(hours=1)
    request = _create(workspace, owner, "stub_long")
    _age(request, 3601)
    with pytest.raises(ApprovalExpired):
        approve_request(approval_request=request, actor=owner, channel=CHANNEL_WEB)


def test_the_sweep_uses_each_tools_ttl(workspace, owner, tools):
    tools("stub_short", approval_ttl_seconds=600)
    tools("stub_plain")
    short = _create(workspace, owner, "stub_short")
    plain = _create(workspace, owner, "stub_plain")
    _age(short, 601)
    _age(plain, 601)
    assert sweep_expired_approval_requests() == 1
    short.refresh_from_db()
    plain.refresh_from_db()
    assert (short.status, plain.status) == ("expired", "pending")
    _age(plain, 24 * 3600 + 1)
    assert sweep_expired_approval_requests() == 1


# --- the precheck hook --------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_the_precheck_runs_outside_a_transaction_and_reaches_prepare_payload(
    workspace, owner, tools
):
    seen = {}
    sentinel = object()

    def precheck(ws, actor, payload):
        seen["atomic"] = connection.in_atomic_block
        seen["actor"] = actor
        return sentinel

    def prepare(ws, payload, *, precheck):
        seen["prepare_atomic"] = connection.in_atomic_block
        seen["precheck"] = precheck
        return {"kept": payload.get("kept"), "from_precheck": precheck is sentinel}

    tools("stub_pre", precheck_request=precheck, prepare_payload=prepare)
    request = _create(workspace, owner, "stub_pre", {"kept": 1, "trust": "ok"})
    assert seen["atomic"] is False
    assert seen["prepare_atomic"] is True
    assert seen["precheck"] is sentinel and seen["actor"] == owner
    assert request.payload == {"kept": 1, "from_precheck": True}


def test_existing_tools_keep_the_old_prepare_payload_signature(workspace, owner, tools):
    def prepare(ws, payload):  # no `precheck` keyword at all
        return {"ok": True}

    tools("stub_old", prepare_payload=prepare)
    assert _create(workspace, owner, "stub_old").payload == {"ok": True}


def test_a_precheck_refusal_creates_nothing(workspace, owner, tools):
    def precheck(ws, actor, payload):
        raise ToolValidationError("nope")

    tools("stub_refuse", precheck_request=precheck, prepare_payload=lambda w, p, *, precheck: p)
    with pytest.raises(InvalidTransition, match="nope"):
        _create(workspace, owner, "stub_refuse")
    assert not ApprovalRequest.objects.exists()


def test_non_writers_never_reach_the_precheck(workspace, owner, tools):
    calls = []
    tools("stub_spy", precheck_request=lambda *a: calls.append(a))
    viewer = register_user(email="viewer@test.dev", password=PASSWORD, name="V")
    WorkspaceMember.objects.create(
        workspace=workspace, user=viewer, role=WorkspaceMember.Role.VIEWER
    )
    outsider = register_user(email="outsider@test.dev", password=PASSWORD, name="O")
    for actor in (viewer, outsider):
        with pytest.raises(ApprovalPermissionDenied):
            _create(workspace, actor, "stub_spy")
    assert calls == []


def test_validate_request_failures_never_reach_the_precheck(workspace, owner, tools):
    calls = []

    def validate(ws, payload):
        raise ToolValidationError("bad")

    tools("stub_val", validate_request=validate, precheck_request=lambda *a: calls.append(a))
    with pytest.raises(InvalidTransition):
        _create(workspace, owner, "stub_val")
    assert calls == []


def test_a_role_revoked_during_the_precheck_still_refuses(workspace, owner, tools):
    member = register_user(email="member@test.dev", password=PASSWORD, name="M")
    WorkspaceMember.objects.create(
        workspace=workspace, user=member, role=WorkspaceMember.Role.MEMBER
    )

    def precheck(ws, actor, payload):
        WorkspaceMember.objects.filter(workspace=ws, user=actor).delete()

    tools("stub_revoke", precheck_request=precheck)
    with pytest.raises(ApprovalPermissionDenied):
        _create(workspace, member, "stub_revoke")
    assert not ApprovalRequest.objects.exists()


# --- the approver hook --------------------------------------------------


def test_authorize_decision_can_refuse_an_approver(workspace, owner, tools):
    def authorize(request, actor):
        raise ToolDecisionForbidden("not you")

    executions = []
    spec = tools("stub_auth", authorize_decision=authorize)
    register_tool(dataclasses.replace(spec, create_execution=lambda r: executions.append(r)))
    request = _create(workspace, owner, "stub_auth")
    with pytest.raises(ApprovalPermissionDenied):
        approve_request(approval_request=request, actor=owner, channel=CHANNEL_WEB)
    request.refresh_from_db()
    assert request.status == "pending" and executions == []
    # Declining is the safe direction: reject never asks the hook.
    reject_request(approval_request=request, actor=owner, channel=CHANNEL_WEB)
    request.refresh_from_db()
    assert request.status == "rejected"


def test_authorize_decision_sees_the_actor_and_allows(workspace, owner, tools):
    seen = []
    tools("stub_ok", authorize_decision=lambda request, actor: seen.append(actor))
    request = _create(workspace, owner, "stub_ok")
    approve_request(approval_request=request, actor=owner, channel=CHANNEL_WEB)
    request.refresh_from_db()
    assert request.status == "approved" and seen == [owner]


def test_an_expired_request_never_reaches_the_approver_hook(workspace, owner, tools):
    seen = []
    tools(
        "stub_exp",
        approval_ttl_seconds=60,
        authorize_decision=lambda request, actor: seen.append(actor),
    )
    request = _create(workspace, owner, "stub_exp")
    _age(request, 61)
    with pytest.raises(ApprovalExpired):
        approve_request(approval_request=request, actor=owner, channel=CHANNEL_WEB)
    assert seen == []
