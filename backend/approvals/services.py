"""Approval gate services (ADR-004) -- the only sanctioned write path for
`ApprovalRequest` rows.

State machine (illegal transitions raise InvalidTransition -> 400):

    pending -> approved | rejected | expired

There is no restore/reopen: a decided (or expired) request is terminal;
requesting the same action again creates a new row. `expired` is set by a
scheduled sweep (`approvals.tasks`), as a backstop by the decision services
themselves (`_expire_if_stale`) -- so a request the sweep hasn't gotten to
yet can never be approved by accident, regardless of the sweep's timing --
and, explicitly, by `revoke_request` (Phase 3 branch 4): a caller elsewhere
withdrawing a request whose underlying job/action no longer needs a
decision, distinguished from the passive cases only in its audit action
(`approval.revoked` vs `approval.expired`), never in the stored status.

Approving also resolves and runs the request's gated tool (`tools.registry`,
branch 2): `create_execution()` inside the approval's own transaction, then
`dispatch()` via `transaction.on_commit` -- see `approve_request`'s
docstring.

Role policy (identical for personal and company workspaces):

    action              writer roles   curator roles
    request              yes            yes
    approve/reject        no             yes

The decision model (ADR-008 decision 3, replacing MVP 5's two-human
requester != decider rule): a gated action requires an explicit, separate,
audited decision by a curator-role human over the high-trust web channel.
With a single owner there is no second human by definition, so the owner
may decide requests they (or their agents -- `ApprovalRequest.origin`)
produced; what is enforced instead is that the decision is never implicit
(its own endpoint, its own audit row), never available to a machine actor,
and never accepted from a low-trust channel -- `channel` is therefore a
required argument: every caller must state the real channel explicitly,
there is no safe default (Codex phase-0 plan-review P1).

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
from core.channels import CHANNEL_SYSTEM, CHANNEL_WEB
from tools.registry import ToolValidationError
from workspaces.models import WorkspaceMember

_CURATOR_ROLES = {WorkspaceMember.Role.OWNER, WorkspaceMember.Role.ADMIN}
_WRITER_ROLES = _CURATOR_ROLES | {WorkspaceMember.Role.MEMBER}


class ApprovalServiceError(Exception):
    """Base class; views translate subclasses to HTTP statuses."""


class InvalidTransition(ApprovalServiceError):
    """Illegal state-machine move, or an unknown/non-gated tool -> 400."""

    code = "approval.invalid_transition"


class ApprovalExpired(InvalidTransition):
    """The request expired before (or while) it was decided -> 400."""

    code = "approval.expired"


class ApprovalPermissionDenied(ApprovalServiceError):
    """Actor's role (or the channel-trust rule) does not allow the
    action -> 403."""

    code = "approval.permission_denied"


class ApprovalAgentForbidden(ApprovalPermissionDenied):
    """A machine (`is_agent`) actor tried to decide -> 403."""

    code = "approval.agent_forbidden"


class ApprovalChannelForbidden(ApprovalPermissionDenied):
    """A decision arrived over a channel other than web -> 403."""

    code = "approval.channel_forbidden"


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


def revoke_request(*, approval_request, actor=None):
    """Withdraws a still-`pending` request because the underlying job/
    action it gates no longer needs a decision -- e.g. a `harness`
    implementation job cancelled while its Gate A/B request was still
    pending (Phase 3 branch 4, `harness.services.
    cancel_harness_implementation_job`), or one abandoned until its
    worktree retention deadline passed with no decision ever made
    (`harness.implementation_execution._sweep_expired_worktree_job`).
    Reuses the existing `expired` terminal state rather than inventing a
    new one: the state machine this module's own docstring documents
    (`pending -> approved | rejected | expired`, no restore/reopen)
    already treats `expired` as "no longer decidable," which is exactly
    what a revoke means too -- and `harness.signals`'s existing
    `post_save` receiver already reacts to an `expired` decision by
    failing the linked job closed (`GATE_EXPIRED`), so this needs no
    separate job-transition logic of its own; the caller that revoked
    the request has typically already transitioned its own job by the
    time this commits, and that signal-triggered attempt safely no-ops
    against a job that's no longer in the gate-pending status it
    re-checks.

    `actor` is optional (Codex code-review P2, branch 4 round 6): a
    caller with no real human decision-maker behind the revoke --
    exactly the worktree-retention sweep, a passive system timeout, not
    a human choosing to withdraw anything -- must be able to say so
    explicitly, rather than being forced to attribute the action to
    whichever human happens to own the underlying job. `record_revoked`
    below records `actor_type=SYSTEM` whenever `actor` is `None`,
    mirroring `record_expired`'s identical convention for the TTL sweep.

    A safe no-op if the request is no longer `pending` (already decided,
    or already expired by the TTL sweep/decision-time backstop) --
    callers are never expected to treat this as an error; the
    underlying cancellation flow this exists for has already achieved
    its own goal regardless of whether this specific request could
    still be revoked."""
    with transaction.atomic():
        locked = (
            ApprovalRequest.objects.select_for_update(of=("self",))
            .filter(pk=approval_request.pk, status=ApprovalRequest.Status.PENDING)
            .first()
        )
        if locked is None:
            return
        locked.status = ApprovalRequest.Status.EXPIRED
        locked.save(update_fields=["status", "updated_at"])
    record_revoked(locked, actor)


def record_revoked(approval_request, actor):
    """Distinct audit action from `approval.expired` (`record_expired`,
    the TTL-sweep/decision-time-backstop case) even though the STORED
    status is identical -- a revoke is always triggered by an explicit
    cancellation elsewhere, never a passive timeout by itself, and the
    audit trail should be able to tell the two apart. `actor_type` is
    `SYSTEM` when no real human actor is behind this call (Codex
    code-review P2, branch 4 round 6), mirroring `record_expired`'s
    identical convention -- misattributing a passive system timeout
    (e.g. the worktree-retention sweep) to whichever human happens to
    own the underlying job would misrepresent it as an explicit human
    decision in the audit trail."""
    record_audit(
        action="approval.revoked",
        actor=actor,
        actor_type=AuditLog.ActorType.SYSTEM if actor is None else AuditLog.ActorType.HUMAN,
        workspace=approval_request.workspace,
        resource=approval_request,
        # A revoke is always a side effect of another flow (a cancellation
        # or a sweep), never a decision taken over a channel of its own.
        metadata=_channel_metadata(approval_request, CHANNEL_SYSTEM),
    )


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
        metadata=_channel_metadata(approval_request, CHANNEL_SYSTEM),
    )


def _channel_metadata(approval_request, channel, **extra):
    """Every `approval.*` audit row (Phase 4 branch 1) carries both the
    channel the recorded action happened over (`channel`: the request's
    own channel for `approval.requested`, the decision channel for
    approved/rejected, `system` for expiry/revoke) and the channel the
    request originally arrived over (`requested_channel`). Ids/status/
    tool_name only otherwise -- never `payload`, which is not
    unconditionally safe to log verbatim."""
    return {
        "status": approval_request.status,
        "tool_name": approval_request.tool_name,
        "channel": channel,
        "requested_channel": approval_request.requested_channel,
        **extra,
    }


def _check_actor_is_human(actor):
    """ADR-008 decision 3: a machine actor can request but never decide.
    Checked first, before role and channel (defense in depth -- no agent
    service account exists today, and none may ever decide even if one
    were to reach the web channel)."""
    if getattr(actor, "is_agent", False):
        raise ApprovalAgentForbidden("Agent accounts cannot decide approval requests.")


def _check_channel_is_web(channel):
    """No approvals from low-trust channels (MVP 9 §1,
    docs/integrations/README.md) -- default-deny by channel identity, not
    by denylisting known-untrusted channels: only CHANNEL_WEB is ever
    accepted for a decision, so a future channel this check doesn't yet
    know about is untrusted by default until someone deliberately extends
    this one check (the safer failure direction for a hard rule ADR-004
    says no configuration may relax)."""
    if channel != CHANNEL_WEB:
        raise ApprovalChannelForbidden("Decisions are not permitted over this channel.")


def _record(approval_request, action, actor, channel, **metadata):
    record_audit(
        action=action,
        actor=actor,
        workspace=approval_request.workspace,
        resource=approval_request,
        metadata=_channel_metadata(approval_request, channel, **metadata),
    )


@transaction.atomic
def create_request(*, workspace, actor, tool_name, payload=None, origin, channel):
    """`origin` is required, never defaulted (Codex phase-0 code-review
    P2, mirroring `channel` on the decision services): the human/machine
    provenance split is a security invariant, and a future machine caller
    (the phase-2 harness calls exactly this service) that forgot to state
    its origin would otherwise be silently stamped human. Callers declare
    it explicitly -- the web view passes HUMAN, the agent engine AGENT.

    `channel` (Phase 4 branch 1) is required for the same reason: the
    channel the request arrived over, persisted as `requested_channel` and
    recorded in the audit trail. The HTTP view passes the request's real
    channel (`web` or `cli` -- a CLI request stays origin=human, a person
    typed it); machine callers (agent engine, harness gates) pass
    `CHANNEL_SYSTEM`."""
    if channel not in ApprovalRequest.RequestedChannel.values:
        raise InvalidTransition(f"Unknown request channel {channel!r}.")
    _require(get_role(actor, workspace) in _WRITER_ROLES, "requesting a gated action")
    tool = _resolve_tool(tool_name)
    if tool is None or not tool.gated:
        raise InvalidTransition(f"{tool_name!r} is not a valid gated tool.")
    # Phase 3 branch 6 (plan-review round 9 item 3): system-only gates
    # (e.g. the harness's own Gate A/B) can only be minted by their owning
    # component, never by a human/agent request. getattr for the same
    # SimpleNamespace test stubs as below.
    if not getattr(tool, "user_invocable", True) and origin != ApprovalRequest.Origin.HARNESS:
        raise InvalidTransition(f"{tool_name!r} can only be requested by the system.")
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
        origin=origin,
        requested_channel=channel,
    )
    _record(approval_request, "approval.requested", actor, channel, origin=str(origin))
    return approval_request


def approve_request(*, approval_request, actor, channel):
    """Approving also triggers the gated tool: `tool.create_execution()`
    runs and commits INSIDE this same transaction -- before the broker is
    ever contacted -- so a durable execution row exists even if the
    publish that follows never lands (durable-before-dispatch, brief §2).
    `tool.dispatch()` is registered via `transaction.on_commit`, so it only
    ever runs after this transaction has actually committed.

    `channel` (MVP 9 §1) is required, never defaulted: a decision must
    carry an explicit high-trust decision context from its caller
    (`approvals.views` passes the request's actual channel) -- a default
    would let a future internal caller decide "as web" by accident
    (ADR-008 decision 3; Codex phase-0 plan-review P1)."""
    _check_actor_is_human(actor)
    with transaction.atomic():
        approval_request = _locked(approval_request, actor)
        _require(
            get_role(actor, approval_request.workspace) in _CURATOR_ROLES, "approving this request"
        )
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
            _record(approval_request, "approval.approved", actor, channel)
            tool = _resolve_tool(approval_request.tool_name)
            if tool is not None:
                execution_id = tool.create_execution(approval_request)
                transaction.on_commit(lambda: tool.dispatch(execution_id))
    if expired:
        raise ApprovalExpired("This request has expired.")
    return approval_request


def reject_request(*, approval_request, actor, channel):
    _check_actor_is_human(actor)
    with transaction.atomic():
        approval_request = _locked(approval_request, actor)
        _require(
            get_role(actor, approval_request.workspace) in _CURATOR_ROLES, "rejecting this request"
        )
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
            _record(approval_request, "approval.rejected", actor, channel)
    if expired:
        raise ApprovalExpired("This request has expired.")
    return approval_request
