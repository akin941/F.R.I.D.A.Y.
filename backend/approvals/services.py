"""Approval gate services (ADR-004) -- the only sanctioned write path for
`ApprovalRequest` rows.

State machine (illegal transitions raise InvalidTransition -> 400):

    pending -> approved | rejected | expired

There is no restore/reopen: a decided (or expired) request is terminal;
requesting the same action again creates a new row. `expired` is set both by
a scheduled sweep (`approvals.tasks`) and, as a backstop, by the decision
services themselves (`_expire_if_stale`) -- so a request the sweep hasn't
gotten to yet can never be approved by accident, regardless of the sweep's
timing.

Approving also resolves and runs the request's gated tool (`tools.registry`,
branch 2): `create_execution()` inside the approval's own transaction, then
`dispatch()` via `transaction.on_commit` -- see `approve_request`'s
docstring.

Role policy (identical for personal and company workspaces):

    action              writer roles   curator roles
    request              yes            yes
    approve/reject        no             yes

Requester != decider is enforced unconditionally (ADR-004's core premise --
the entity that produces an action is not the entity that approves it),
except when `settings.DEV_ALLOW_SELF_APPROVAL` is True -- a narrowly-scoped,
dev-only escape hatch that `approvals.apps.ApprovalsConfig.ready()` asserts
can never be True outside DEBUG mode.

Every mutating service re-fetches its row under `select_for_update` inside
its transaction (mirrors `memory.services._locked()`), so concurrent
decisions serialize and a stale instance can never double-decide a row.
"""

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from approvals.models import ApprovalRequest
from audit.models import AuditLog
from audit.services import record_audit
from core.channels import CHANNEL_WEB
from tools.registry import ToolValidationError
from workspaces.models import WorkspaceMember

_CURATOR_ROLES = {WorkspaceMember.Role.OWNER, WorkspaceMember.Role.ADMIN}
_WRITER_ROLES = _CURATOR_ROLES | {WorkspaceMember.Role.MEMBER}


class ApprovalServiceError(Exception):
    """Base class; views translate subclasses to HTTP statuses."""


class InvalidTransition(ApprovalServiceError):
    """Illegal state-machine move, or an unknown/non-gated tool -> 400."""


class ApprovalPermissionDenied(ApprovalServiceError):
    """Actor's role (or the requester/decider separation rule) does not
    allow the action -> 403."""


def get_role(actor, workspace):
    membership = (
        WorkspaceMember.objects.for_actor(actor, workspace=workspace).filter(user=actor).first()
    )
    return membership.role if membership else None


def _require(condition, action):
    if not condition:
        raise ApprovalPermissionDenied(f"Role does not allow {action}.")


def _locked(approval_request, actor):
    """Re-fetch the row under lock, scoped to the actor, inside the caller's
    transaction. Mirrors `memory.services._locked()` exactly -- see that
    docstring for the full `for_actor()`-vs-`FOR UPDATE` rationale."""
    locked = (
        ApprovalRequest.objects.filter(pk=approval_request.pk, workspace__members__user=actor)
        .select_for_update(of=("self",))
        .first()
    )
    if locked is None:
        raise InvalidTransition("Approval request no longer exists.")
    return locked


def _resolve_tool(tool_name):
    """Look up `tool_name` in the `tools` app's registry (branch 2). Kept
    as its own function -- rather than importing `tools.registry` at every
    call site -- so tests can monkeypatch tool resolution independently of
    what's actually registered (see approvals/tests/test_services.py)."""
    from tools.registry import get_tool

    return get_tool(tool_name)


def _expire_if_stale(approval_request):
    """Decision-time expiry backstop: the periodic sweep (approvals.tasks)
    is best-effort, not the only enforcement point. A `pending` request
    older than the TTL is transitioned to `expired` right here, synchronously,
    instead of ever being decided -- closing the "a months-old pending
    request nobody remembered gets approved by accident" scenario without
    depending on the sweep's timing.

    Returns True if the request was just expired. Deliberately does NOT
    raise: this write must survive even though the overall approve/reject
    call ultimately still fails, and raising from inside the same
    `transaction.atomic()` block that made this write would roll the write
    back along with everything else in that block. The caller raises
    `InvalidTransition` itself, only after this function's transaction has
    already committed.
    """
    ttl = timedelta(hours=settings.APPROVAL_REQUEST_TTL_HOURS)
    if (
        approval_request.status == ApprovalRequest.Status.PENDING
        and timezone.now() - approval_request.created_at > ttl
    ):
        approval_request.status = ApprovalRequest.Status.EXPIRED
        approval_request.save(update_fields=["status", "updated_at"])
        record_expired(approval_request)
        return True
    return False


def record_expired(approval_request):
    """`approval.expired` is a system-classified transition even when a
    human's decide attempt is what triggered the decision-time backstop
    check -- the human didn't actually decide anything, the request simply
    turned out to already be stale. Shared by the decision-time backstop
    above and the periodic sweep (`approvals.tasks`)."""
    record_audit(
        action="approval.expired",
        actor=None,
        actor_type=AuditLog.ActorType.SYSTEM,
        workspace=approval_request.workspace,
        resource=approval_request,
        metadata={"status": approval_request.status, "tool_name": approval_request.tool_name},
    )


def _check_requester_not_decider(approval_request, actor):
    if approval_request.requested_by_id == actor.id and not settings.DEV_ALLOW_SELF_APPROVAL:
        raise ApprovalPermissionDenied("The requester cannot decide their own request.")


def _check_channel_is_web(channel):
    """No approvals from low-trust channels (MVP 9 §1,
    docs/integrations/README.md) -- default-deny by channel identity, not
    by denylisting known-untrusted channels: only CHANNEL_WEB is ever
    accepted for a decision, so a future channel this check doesn't yet
    know about is untrusted by default until someone deliberately extends
    this one check (the safer failure direction for a hard rule ADR-004
    says no configuration may relax)."""
    if channel != CHANNEL_WEB:
        raise ApprovalPermissionDenied("Decisions are not permitted over this channel.")


def _record(approval_request, action, actor, **metadata):
    # Audit metadata carries ids/status/tool_name only -- never `payload`,
    # which is not unconditionally safe to log verbatim (a future tool's
    # payload could carry something sensitive).
    record_audit(
        action=action,
        actor=actor,
        workspace=approval_request.workspace,
        resource=approval_request,
        metadata={
            "status": approval_request.status,
            "tool_name": approval_request.tool_name,
            **metadata,
        },
    )


@transaction.atomic
def create_request(*, workspace, actor, tool_name, payload=None):
    _require(get_role(actor, workspace) in _WRITER_ROLES, "requesting a gated action")
    tool = _resolve_tool(tool_name)
    if tool is None or not tool.gated:
        raise InvalidTransition(f"{tool_name!r} is not a valid gated tool.")
    # getattr, not tool.validate_request: existing tests stub tools with a
    # bare SimpleNamespace(gated=True), predating this field.
    validate_request = getattr(tool, "validate_request", None)
    if validate_request is not None:
        try:
            validate_request(workspace, payload or {})
        except ToolValidationError as exc:
            raise InvalidTransition(str(exc)) from exc
    approval_request = ApprovalRequest.objects.create(
        workspace=workspace,
        tool_name=tool_name,
        payload=payload or {},
        status=ApprovalRequest.Status.PENDING,
        requested_by=actor,
    )
    _record(approval_request, "approval.requested", actor)
    return approval_request


def approve_request(*, approval_request, actor, channel=CHANNEL_WEB):
    """Approving also triggers the gated tool: `tool.create_execution()`
    runs and commits INSIDE this same transaction -- before the broker is
    ever contacted -- so a durable execution row exists even if the
    publish that follows never lands (durable-before-dispatch, brief §2).
    `tool.dispatch()` is registered via `transaction.on_commit`, so it only
    ever runs after this transaction has actually committed.

    `channel` (MVP 9 §1) defaults to CHANNEL_WEB for backward-compatible
    direct callers (e.g. tests); `approvals.views` always passes the
    request's actual channel explicitly."""
    with transaction.atomic():
        approval_request = _locked(approval_request, actor)
        _require(
            get_role(actor, approval_request.workspace) in _CURATOR_ROLES, "approving this request"
        )
        _check_requester_not_decider(approval_request, actor)
        _check_channel_is_web(channel)
        expired = _expire_if_stale(approval_request)
        if not expired:
            if approval_request.status != ApprovalRequest.Status.PENDING:
                raise InvalidTransition(f"Cannot approve a {approval_request.status} request.")
            approval_request.status = ApprovalRequest.Status.APPROVED
            approval_request.decided_by = actor
            approval_request.decided_at = timezone.now()
            approval_request.save(
                update_fields=["status", "decided_by", "decided_at", "updated_at"]
            )
            _record(approval_request, "approval.approved", actor)
            tool = _resolve_tool(approval_request.tool_name)
            if tool is not None:
                execution_id = tool.create_execution(approval_request)
                transaction.on_commit(lambda: tool.dispatch(execution_id))
    if expired:
        raise InvalidTransition("This request has expired.")
    return approval_request


def reject_request(*, approval_request, actor, channel=CHANNEL_WEB):
    with transaction.atomic():
        approval_request = _locked(approval_request, actor)
        _require(
            get_role(actor, approval_request.workspace) in _CURATOR_ROLES, "rejecting this request"
        )
        _check_requester_not_decider(approval_request, actor)
        _check_channel_is_web(channel)
        expired = _expire_if_stale(approval_request)
        if not expired:
            if approval_request.status != ApprovalRequest.Status.PENDING:
                raise InvalidTransition(f"Cannot reject a {approval_request.status} request.")
            approval_request.status = ApprovalRequest.Status.REJECTED
            approval_request.decided_by = actor
            approval_request.decided_at = timezone.now()
            approval_request.save(
                update_fields=["status", "decided_by", "decided_at", "updated_at"]
            )
            _record(approval_request, "approval.rejected", actor)
    if expired:
        raise InvalidTransition("This request has expired.")
    return approval_request
