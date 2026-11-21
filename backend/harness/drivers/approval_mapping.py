"""Agent escalation -> `harness_agent_action` approval (Phase 10 brief D10
"Approval mapping").

Every escalation a driver-run agent asks for becomes one system-created,
gate-class `harness_agent_action` `ApprovalRequest` (origin `harness`,
channel `system`, 600 s TTL), decided by the owner on `/approvals`. The
`ApprovalRequest` row is the source of truth: the waiting driver re-reads
it every `HARNESS_AGENT_ACTION_POLL_SECONDS` and answers `accept` ONLY when
the row is `approved` and its `HarnessAgentAction` execution exists.
`rejected`, `expired`, a missing row, a job cancel, the runtime brake or the
TTL elapsing each answer `decline`. The Redis key
`harness:agent_action:<approval id>` that `dispatch` pushes on approve is
only a wake-up hint that shortens the wait: a lost or forged key changes
nothing, because the row is re-read before any answer.
"""

import logging
import time

from django.conf import settings
from django.utils import timezone

from approvals.models import ApprovalRequest
from approvals.services import ApprovalServiceError, approval_ttl, create_request, revoke_request
from audit.models import AuditLog
from audit.services import record_audit
from core.channels import CHANNEL_SYSTEM
from harness.models import HarnessAgentAction

logger = logging.getLogger(__name__)

TOOL_NAME = "harness_agent_action"
AGENT_ACTION_TTL_SECONDS = 600

# Why a wait ended (recorded on the audit row; only APPROVED accepts).
APPROVED = "approved"
REJECTED = "rejected"
EXPIRED = "expired"
MISSING = "missing"
NO_EXECUTION = "no_execution"
TTL_ELAPSED = "ttl_elapsed"
JOB_CANCELLED = "job_cancelled"
RUNTIME_BRAKE = "runtime_brake"
REQUEST_REFUSED = "request_refused"
ESCALATION_LIMIT = "escalation_limit"
DRIVER_ENDED = "driver_ended"

_WAKE_TIMEOUT_SECONDS = 1.0


def wake_key(approval_id):
    return f"harness:agent_action:{approval_id}"


def _redis():
    import redis

    return redis.Redis.from_url(
        settings.REDIS_URL,
        socket_timeout=_WAKE_TIMEOUT_SECONDS + 5,
        socket_connect_timeout=_WAKE_TIMEOUT_SECONDS,
    )


def push_wake_hint(approval_id):
    """Best effort: the decision never depends on it. Never raises."""
    try:
        client = _redis()
        try:
            key = wake_key(approval_id)
            client.rpush(key, "1")
            client.expire(key, AGENT_ACTION_TTL_SECONDS)
        finally:
            client.close()
    except Exception:  # best effort: the driver's poll still sees the row
        logger.warning("harness agent action wake hint not written", exc_info=True)
        return False
    return True


def wait_wake_hint(approval_id, timeout):
    """Blocks up to `timeout` seconds for the wake-up hint. Whatever it
    returns, the caller re-reads the row; on a Redis error it just sleeps."""
    timeout = max(0.0, float(timeout))
    if timeout <= 0:
        return False
    try:
        client = _redis()
        try:
            # BLPOP takes whole or fractional seconds; 0 would block forever.
            return client.blpop([wake_key(approval_id)], timeout=max(timeout, 0.01)) is not None
        finally:
            client.close()
    except Exception:
        time.sleep(timeout)
        return False


def request_agent_action(job, escalation):
    """Mints the `harness_agent_action` gate for one escalation, or returns
    None when it cannot be requested (the creator lost their role, the job
    moved on): the caller then declines."""
    if job.created_by_id is None:
        return None
    try:
        return create_request(
            workspace=job.workspace,
            actor=job.created_by,
            tool_name=TOOL_NAME,
            payload={
                "job_id": str(job.id),
                "agent": job.agent,
                "kind": escalation.kind,
                "command": escalation.command,
                "cwd": escalation.cwd,
                "reason": escalation.reason,
            },
            origin=ApprovalRequest.Origin.HARNESS,
            channel=CHANNEL_SYSTEM,
            system_created=True,
        )
    except ApprovalServiceError:
        logger.warning("harness agent action for job %s could not be requested", job.id)
        return None


def decide(job, approval_id, *, now=None):
    """One poll step: `(True|False, reason)` once decided, else
    `(None, "")`. Reads the row through the job's own workspace."""
    row = (
        ApprovalRequest.objects.filter(workspace=job.workspace, pk=approval_id, tool_name=TOOL_NAME)
        .only("id", "status", "created_at", "tool_name")
        .first()
    )
    if row is None:
        return False, MISSING
    if row.status == ApprovalRequest.Status.APPROVED:
        executed = HarnessAgentAction.objects.filter(
            workspace=job.workspace, job=job, approval_id=row.pk
        ).exists()
        return (True, APPROVED) if executed else (False, NO_EXECUTION)
    if row.status == ApprovalRequest.Status.REJECTED:
        return False, REJECTED
    if row.status == ApprovalRequest.Status.EXPIRED:
        return False, EXPIRED
    if row.status != ApprovalRequest.Status.PENDING:
        return False, MISSING
    now = now if now is not None else timezone.now()
    if now - row.created_at >= approval_ttl(TOOL_NAME):
        return False, TTL_ELAPSED
    return None, ""


def wait_for_decision(
    job,
    approval_id,
    *,
    deadline,
    should_stop,
    clock=time.monotonic,
    now=None,
    wait=None,
    poll_seconds=None,
):
    """Blocks until the row decides (or a brake fires). A job cancel or
    the runtime brake wins over a decision read in the same step."""
    now = now or timezone.now
    wait = wait or wait_wake_hint
    poll_seconds = (
        poll_seconds if poll_seconds is not None else settings.HARNESS_AGENT_ACTION_POLL_SECONDS
    )
    while True:
        if should_stop():
            return False, JOB_CANCELLED
        remaining = deadline - clock()
        if remaining <= 0:
            return False, RUNTIME_BRAKE
        decision, reason = decide(job, approval_id, now=now())
        if decision is not None:
            return decision, reason
        wait(approval_id, min(float(poll_seconds), remaining))


def record_answer(job, approval, *, accept, reason):
    """The driver's own record of what it sent: the execution row's
    `answer` for an accept, an audit row either way; a still-pending
    request the driver gave up on (cancel, brake, the run ending) is
    revoked so it stops looking actionable."""
    approval_id = getattr(approval, "pk", None)
    if accept and approval_id is not None:
        HarnessAgentAction.objects.filter(
            workspace=job.workspace, job=job, approval_id=approval_id
        ).update(answer=HarnessAgentAction.Answer.ACCEPT, answered_at=timezone.now())
    if (
        not accept
        and approval is not None
        and reason in (JOB_CANCELLED, RUNTIME_BRAKE, DRIVER_ENDED)
    ):
        revoke_request(approval_request=approval, actor=None)
    record_audit(
        action="harness.agent_action_answered",
        actor=None,
        actor_type=AuditLog.ActorType.SYSTEM,
        workspace=job.workspace,
        resource=job,
        metadata={
            "approval_id": str(approval_id) if approval_id is not None else "",
            "decision": "accept" if accept else "decline",
            "reason": reason,
            "agent": job.agent,
        },
    )
