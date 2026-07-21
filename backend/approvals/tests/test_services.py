import threading
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.db import connection
from django.utils import timezone

from accounts.services import register_user
from approvals.models import ApprovalRequest
from approvals.services import (
    ApprovalPermissionDenied,
    InvalidTransition,
    approve_request,
    create_request,
    reject_request,
    revoke_request,
)
from audit.models import AuditLog
from core.channels import CHANNEL_WEB
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"

_GATED_TOOL = SimpleNamespace(gated=True)


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


@pytest.fixture
def second_admin(workspace):
    user = register_user(email="admin2@test.dev", password=PASSWORD, name="Admin2")
    WorkspaceMember.objects.create(workspace=workspace, user=user, role=WorkspaceMember.Role.ADMIN)
    return user


@pytest.fixture
def member(workspace):
    user = register_user(email="member@test.dev", password=PASSWORD, name="Member")
    WorkspaceMember.objects.create(workspace=workspace, user=user, role=WorkspaceMember.Role.MEMBER)
    return user


@pytest.fixture
def viewer(workspace):
    user = register_user(email="viewer@test.dev", password=PASSWORD, name="Viewer")
    WorkspaceMember.objects.create(workspace=workspace, user=user, role=WorkspaceMember.Role.VIEWER)
    return user


def audit_actions():
    return list(AuditLog.objects.order_by("created_at").values_list("action", flat=True))


def make_request(workspace, requested_by=None, **overrides):
    fields = {"workspace": workspace, "tool_name": "some_gated_tool", "requested_by": requested_by}
    fields.update(overrides)
    return ApprovalRequest.objects.create(**fields)


class TestCreate:
    def test_unknown_tool_is_rejected(self, workspace, owner):
        # No monkeypatch: "anything" is genuinely unregistered in the real
        # tools.registry (unlike "some_gated_tool" below, whose resolution
        # this test suite always monkeypatches -- these tests intentionally
        # never touch the real registry, so they stay independent of
        # whatever concrete tools tools/ actually registers, e.g. the real
        # vault_export tool, tested in tools/tests/test_export.py).
        with pytest.raises(InvalidTransition):
            create_request(
                workspace=workspace,
                actor=owner,
                tool_name="anything",
                origin=ApprovalRequest.Origin.HUMAN,
            )

    def test_known_gated_tool_creates_a_pending_request(self, workspace, owner, monkeypatch):
        monkeypatch.setattr("approvals.services._resolve_tool", lambda name: _GATED_TOOL)
        request = create_request(
            workspace=workspace,
            actor=owner,
            tool_name="some_gated_tool",
            origin=ApprovalRequest.Origin.HUMAN,
        )
        assert request.status == ApprovalRequest.Status.PENDING
        assert request.requested_by == owner
        assert "approval.requested" in audit_actions()

    def test_viewer_cannot_create(self, workspace, viewer, monkeypatch):
        monkeypatch.setattr("approvals.services._resolve_tool", lambda name: _GATED_TOOL)
        with pytest.raises(ApprovalPermissionDenied):
            create_request(
                workspace=workspace,
                actor=viewer,
                tool_name="some_gated_tool",
                origin=ApprovalRequest.Origin.HUMAN,
            )

    def test_member_can_create(self, workspace, member, monkeypatch):
        monkeypatch.setattr("approvals.services._resolve_tool", lambda name: _GATED_TOOL)
        request = create_request(
            workspace=workspace,
            actor=member,
            tool_name="some_gated_tool",
            origin=ApprovalRequest.Origin.HUMAN,
        )
        assert request.requested_by == member

    def test_non_gated_tool_is_rejected(self, workspace, owner, monkeypatch):
        monkeypatch.setattr(
            "approvals.services._resolve_tool", lambda name: SimpleNamespace(gated=False)
        )
        with pytest.raises(InvalidTransition):
            create_request(
                workspace=workspace,
                actor=owner,
                tool_name="not_gated",
                origin=ApprovalRequest.Origin.HUMAN,
            )

    def test_origin_human_is_persisted_and_audited(self, workspace, owner, monkeypatch):
        monkeypatch.setattr(
            "approvals.services._resolve_tool", lambda name: SimpleNamespace(gated=True)
        )
        request = create_request(
            workspace=workspace,
            actor=owner,
            tool_name="some_gated_tool",
            origin=ApprovalRequest.Origin.HUMAN,
        )
        assert request.origin == ApprovalRequest.Origin.HUMAN
        entry = AuditLog.objects.filter(action="approval.requested").latest("created_at")
        assert entry.metadata["origin"] == "human"

    def test_origin_agent_is_persisted(self, workspace, owner, monkeypatch):
        monkeypatch.setattr(
            "approvals.services._resolve_tool", lambda name: SimpleNamespace(gated=True)
        )
        request = create_request(
            workspace=workspace,
            actor=owner,
            tool_name="some_gated_tool",
            origin=ApprovalRequest.Origin.AGENT,
        )
        assert request.origin == ApprovalRequest.Origin.AGENT

    def test_origin_is_required_not_defaulted(self, workspace, owner, monkeypatch):
        # Codex phase-0 code-review P2 (mirrors the decision services'
        # required channel): a machine caller that forgets to state its
        # origin must fail loudly, never be silently stamped human.
        monkeypatch.setattr(
            "approvals.services._resolve_tool", lambda name: SimpleNamespace(gated=True)
        )
        with pytest.raises(TypeError):
            create_request(workspace=workspace, actor=owner, tool_name="some_gated_tool")


class TestDecisions:
    def test_second_admin_approves(self, workspace, owner, second_admin):
        request = make_request(workspace, requested_by=owner)
        decided = approve_request(approval_request=request, actor=second_admin, channel=CHANNEL_WEB)
        assert decided.status == ApprovalRequest.Status.APPROVED
        assert decided.decided_by == second_admin
        assert decided.decided_at is not None
        assert "approval.approved" in audit_actions()

    def test_second_admin_rejects(self, workspace, owner, second_admin):
        request = make_request(workspace, requested_by=owner)
        decided = reject_request(approval_request=request, actor=second_admin, channel=CHANNEL_WEB)
        assert decided.status == ApprovalRequest.Status.REJECTED
        assert "approval.rejected" in audit_actions()

    def test_owner_approves_their_own_request(self, workspace, owner):
        # ADR-008 decision 3: the gate is an explicit, separate, audited
        # owner decision -- not a two-human rule. The single owner may
        # decide requests they produced.
        request = make_request(workspace, requested_by=owner)
        decided = approve_request(approval_request=request, actor=owner, channel=CHANNEL_WEB)
        assert decided.status == ApprovalRequest.Status.APPROVED
        assert decided.decided_by == owner
        assert "approval.approved" in audit_actions()

    def test_owner_rejects_their_own_request(self, workspace, owner):
        request = make_request(workspace, requested_by=owner)
        decided = reject_request(approval_request=request, actor=owner, channel=CHANNEL_WEB)
        assert decided.status == ApprovalRequest.Status.REJECTED

    def test_owner_decides_an_agent_origin_request(self, workspace, owner):
        # The production defect ADR-008/phase 0 fixes: agent-created
        # requests carry requested_by=owner and must be decidable by the
        # owner (there is no second human in a single-owner system).
        request = make_request(workspace, requested_by=owner, origin=ApprovalRequest.Origin.AGENT)
        decided = approve_request(approval_request=request, actor=owner, channel=CHANNEL_WEB)
        assert decided.status == ApprovalRequest.Status.APPROVED

    def test_channel_is_required_not_defaulted(self, workspace, owner, second_admin):
        # Codex phase-0 plan-review P1: a CHANNEL_WEB default would let a
        # future internal caller decide "as web" by accident. The service
        # must refuse any call without an explicit decision context.
        request = make_request(workspace, requested_by=owner)
        with pytest.raises(TypeError):
            approve_request(approval_request=request, actor=second_admin)
        with pytest.raises(TypeError):
            reject_request(approval_request=request, actor=second_admin)

    def test_second_decide_on_approved_request_is_400(self, workspace, owner, second_admin):
        request = approve_request(
            approval_request=make_request(workspace, requested_by=owner),
            actor=second_admin,
            channel=CHANNEL_WEB,
        )
        with pytest.raises(InvalidTransition):
            reject_request(approval_request=request, actor=second_admin, channel=CHANNEL_WEB)

    def test_second_decide_on_rejected_request_is_400(self, workspace, owner, second_admin):
        request = reject_request(
            approval_request=make_request(workspace, requested_by=owner),
            actor=second_admin,
            channel=CHANNEL_WEB,
        )
        with pytest.raises(InvalidTransition):
            approve_request(approval_request=request, actor=second_admin, channel=CHANNEL_WEB)


class TestRolePolicy:
    def test_member_cannot_decide(self, workspace, owner, member):
        request = make_request(workspace, requested_by=owner)
        with pytest.raises(ApprovalPermissionDenied):
            approve_request(approval_request=request, actor=member, channel=CHANNEL_WEB)
        with pytest.raises(ApprovalPermissionDenied):
            reject_request(approval_request=request, actor=member, channel=CHANNEL_WEB)

    def test_viewer_cannot_decide(self, workspace, owner, viewer):
        request = make_request(workspace, requested_by=owner)
        with pytest.raises(ApprovalPermissionDenied):
            approve_request(approval_request=request, actor=viewer, channel=CHANNEL_WEB)

    def test_audit_metadata_never_contains_payload(self, workspace, owner, second_admin):
        request = make_request(
            workspace, requested_by=owner, payload={"secret_detail": "classified"}
        )
        approve_request(approval_request=request, actor=second_admin, channel=CHANNEL_WEB)
        for entry in AuditLog.objects.all():
            blob = str(entry.metadata).lower()
            assert "classified" not in blob
            assert "secret_detail" not in blob


class TestExpiry:
    def test_decision_time_backstop_expires_a_stale_pending_request(
        self, workspace, owner, second_admin, settings
    ):
        settings.APPROVAL_REQUEST_TTL_HOURS = 24
        request = make_request(workspace, requested_by=owner)
        ApprovalRequest.objects.filter(pk=request.pk).update(
            created_at=timezone.now() - timedelta(hours=25)
        )
        request.refresh_from_db()
        with pytest.raises(InvalidTransition):
            approve_request(approval_request=request, actor=second_admin, channel=CHANNEL_WEB)
        request.refresh_from_db()
        assert request.status == ApprovalRequest.Status.EXPIRED
        assert "approval.expired" in audit_actions()

    def test_fresh_pending_request_is_not_expired(self, workspace, owner, second_admin):
        request = make_request(workspace, requested_by=owner)
        decided = approve_request(approval_request=request, actor=second_admin, channel=CHANNEL_WEB)
        assert decided.status == ApprovalRequest.Status.APPROVED


class TestRevoke:
    """Phase 3 branch 4: withdraws a still-pending request because the
    underlying job/action it gates no longer needs a decision (e.g. a
    harness implementation job cancelled while its Gate A/B request was
    still pending)."""

    def test_revokes_a_pending_request(self, workspace, owner, second_admin):
        request = make_request(workspace, requested_by=owner)
        revoke_request(approval_request=request, actor=second_admin)
        request.refresh_from_db()
        assert request.status == ApprovalRequest.Status.EXPIRED
        assert "approval.revoked" in audit_actions()
        entry = AuditLog.objects.get(action="approval.revoked")
        assert entry.actor_id == second_admin.id

    def test_revoking_an_already_decided_request_is_a_safe_no_op(
        self, workspace, owner, second_admin
    ):
        request = make_request(workspace, requested_by=owner)
        approve_request(approval_request=request, actor=second_admin, channel=CHANNEL_WEB)
        request.refresh_from_db()
        revoke_request(approval_request=request, actor=second_admin)
        request.refresh_from_db()
        assert request.status == ApprovalRequest.Status.APPROVED
        assert "approval.revoked" not in audit_actions()

    def test_revoking_an_already_expired_request_is_a_safe_no_op(
        self, workspace, owner, second_admin
    ):
        request = make_request(workspace, requested_by=owner)
        ApprovalRequest.objects.filter(pk=request.pk).update(status=ApprovalRequest.Status.EXPIRED)
        request.refresh_from_db()
        revoke_request(approval_request=request, actor=second_admin)
        assert "approval.revoked" not in audit_actions()

    def test_revoked_status_is_indistinguishable_from_ttl_expiry(
        self, workspace, owner, second_admin
    ):
        """Deliberately reuses the existing `expired` terminal state
        rather than inventing a new one -- `harness.signals`'s own
        `post_save` receiver already reacts to `expired` regardless of
        which path produced it, so a revoked request must fail a linked
        job closed the identical way a TTL-expired one already does."""
        request = make_request(workspace, requested_by=owner)
        revoke_request(approval_request=request, actor=second_admin)
        request.refresh_from_db()
        assert request.status == ApprovalRequest.Status.EXPIRED
        with pytest.raises(InvalidTransition):
            approve_request(approval_request=request, actor=second_admin, channel=CHANNEL_WEB)


class TestLockedScoping:
    def test_forged_instance_cannot_mutate_foreign_row(self, workspace, owner, second_admin):
        stranger = register_user(email="stranger@test.dev", password=PASSWORD, name="S")
        foreign_ws = Workspace.objects.get(owner=stranger)
        foreign = make_request(foreign_ws, requested_by=stranger, tool_name="foreign")
        forged = ApprovalRequest.objects.get(pk=foreign.pk)
        forged.workspace = workspace
        with pytest.raises(InvalidTransition):
            approve_request(approval_request=forged, actor=second_admin, channel=CHANNEL_WEB)
        foreign.refresh_from_db()
        assert foreign.status == ApprovalRequest.Status.PENDING


@pytest.mark.django_db(transaction=True)
def test_concurrent_approve_and_reject_serialize():
    """Genuine two-connection concurrency test: two threads race to
    approve/reject the same pending request. The actor-scoped
    `select_for_update` lock in `_locked()` must serialize them -- whichever
    transaction commits first wins, and the other observes the post-lock
    committed state (no longer `pending`) and raises InvalidTransition,
    never both succeeding against a stale `pending` read."""
    owner = register_user(email="racer-owner@test.dev", password=PASSWORD, name="Owner")
    workspace = Workspace.objects.get(owner=owner)
    admin_a = register_user(email="racer-a@test.dev", password=PASSWORD, name="A")
    admin_b = register_user(email="racer-b@test.dev", password=PASSWORD, name="B")
    WorkspaceMember.objects.create(
        workspace=workspace, user=admin_a, role=WorkspaceMember.Role.ADMIN
    )
    WorkspaceMember.objects.create(
        workspace=workspace, user=admin_b, role=WorkspaceMember.Role.ADMIN
    )
    request = make_request(workspace, requested_by=owner)

    barrier = threading.Barrier(2)
    results = {}
    errors = []

    def worker(key, action, actor):
        try:
            barrier.wait(timeout=5)
            row = ApprovalRequest.objects.get(pk=request.pk)
            try:
                action(approval_request=row, actor=actor, channel=CHANNEL_WEB)
                results[key] = "succeeded"
            except InvalidTransition:
                results[key] = "invalid_transition"
        except Exception as exc:  # pragma: no cover - surfaced via `errors`
            errors.append(exc)
        finally:
            connection.close()

    t1 = threading.Thread(target=worker, args=("approve", approve_request, admin_a))
    t2 = threading.Thread(target=worker, args=("reject", reject_request, admin_b))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not errors, errors
    assert set(results) == {"approve", "reject"}
    assert set(results.values()) == {"succeeded", "invalid_transition"}
    request.refresh_from_db()
    assert request.status in (ApprovalRequest.Status.APPROVED, ApprovalRequest.Status.REJECTED)


@pytest.mark.django_db(transaction=True)
def test_concurrent_approve_and_sweep_expiry_serialize():
    """Two-connection test: one thread approves a request while another
    runs the expiry sweep on the same (now-stale) request. Whichever
    transaction's lock wins must leave the row in a single consistent
    state -- never approved AND expired, never neither."""
    from approvals.tasks import sweep_expired_approval_requests

    owner = register_user(email="racer-owner2@test.dev", password=PASSWORD, name="Owner2")
    workspace = Workspace.objects.get(owner=owner)
    admin = register_user(email="racer-admin2@test.dev", password=PASSWORD, name="Admin2")
    WorkspaceMember.objects.create(workspace=workspace, user=admin, role=WorkspaceMember.Role.ADMIN)
    request = make_request(workspace, requested_by=owner)
    ApprovalRequest.objects.filter(pk=request.pk).update(
        created_at=timezone.now() - timedelta(hours=25)
    )

    barrier = threading.Barrier(2)
    results = {}
    errors = []

    def approve_worker():
        try:
            barrier.wait(timeout=5)
            row = ApprovalRequest.objects.get(pk=request.pk)
            try:
                approve_request(approval_request=row, actor=admin, channel=CHANNEL_WEB)
                results["approve"] = "succeeded"
            except InvalidTransition:
                results["approve"] = "invalid_transition"
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            connection.close()

    def sweep_worker():
        try:
            barrier.wait(timeout=5)
            results["sweep_count"] = sweep_expired_approval_requests()
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            connection.close()

    t1 = threading.Thread(target=approve_worker)
    t2 = threading.Thread(target=sweep_worker)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not errors, errors
    request.refresh_from_db()
    # Whichever won, the request must end up expired (a stale pending
    # request can never actually be approved -- the decision-time backstop
    # guarantees this even if the sweep's lock lost the race), and the
    # approve attempt itself must always observe this, never succeed.
    assert request.status == ApprovalRequest.Status.EXPIRED
    assert results.get("approve") == "invalid_transition"
