"""Execution engine: advances an `Agent` one `AgentStep` at a time (MVP 7
§3, brief). This is where the brake systems live -- per-step timeout (the
Celery task's own `soft_time_limit`, `agents/tasks.py`), consecutive-
failure halt, resource/token budget, and the `select_for_update`-guarded
kill switch covering start, resume, AND finalization (the round-2 brief
fix: fencing only at start/resume left a stale-worker gap at a step's own
completion).

One-step-at-a-time, mirroring `tools.export`'s claim/work/finalize shape
one level further: `_claim_next_step` (fenced, fast) decides what kind of
step is next; a `read`-kind step's actual gateway calls run with NO lock
held (`run_read_step`), then `_finalize_read_step` (fenced) writes the
result; `_after_step_resolved` (fenced) is the single place that decides
"halt or continue" and, if continuing, schedules the next step via
`transaction.on_commit` -- never inside an open transaction, mirroring
`tools.export.dispatch_vault_export_run`'s non-propagating broker-publish
shape exactly.

Every function here takes/returns primitives (ids, dicts) across the
claim/work/finalize boundary, never a model instance carried across a
transaction gap -- the same discipline `tools.export` already uses,
because an instance fetched under one lock is not safe to write back to
after that lock is released.
"""

import logging
from datetime import date

from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction

from agents.models import Agent, AgentStep
from ai_core.exceptions import GatewayError
from ai_core.gateway import InferenceGateway
from ai_core.models import PromptTemplate
from approvals.services import ApprovalServiceError, create_request
from knowledge.context import render_knowledge_block, select_context_chunks
from tasks.models import Task
from tasks.services import create_task
from workspaces.models import WorkspaceMember

logger = logging.getLogger(__name__)

_AGENT_READ_STEP_PROMPT_NAME = "jarvis_agent_read_step"

_CURATOR_ROLES = {WorkspaceMember.Role.OWNER, WorkspaceMember.Role.ADMIN}
_WRITER_ROLES = _CURATOR_ROLES | {WorkspaceMember.Role.MEMBER}


class AgentServiceError(Exception):
    """Base class; views translate subclasses to HTTP statuses."""


class AgentPermissionDenied(AgentServiceError):
    """Actor's role does not allow the action -> 403."""


class AgentInvalidTransition(AgentServiceError):
    """Illegal state-machine move (e.g. confirming an already-resolved
    step, or one that no longer exists) -> 400."""


def get_role(actor, workspace):
    membership = (
        WorkspaceMember.objects.for_actor(actor, workspace=workspace).filter(user=actor).first()
    )
    return membership.role if membership else None


def _require(condition, action):
    if not condition:
        raise AgentPermissionDenied(f"Role does not allow {action}.")


def _is_own(agent, actor):
    return agent.created_by_id is not None and agent.created_by_id == actor.id


def _agent_read_step_prompt_version():
    try:
        template = PromptTemplate.objects.get(name=_AGENT_READ_STEP_PROMPT_NAME)
    except PromptTemplate.DoesNotExist as exc:
        raise ImproperlyConfigured(
            f"{_AGENT_READ_STEP_PROMPT_NAME} prompt template is not configured"
        ) from exc
    if template.active_version_id is None:
        raise ImproperlyConfigured(
            f"{_AGENT_READ_STEP_PROMPT_NAME} prompt template has no active version"
        )
    return template.active_version


# --- dispatch (non-propagating, mirrors tools.export.dispatch_vault_export_run) ---


def dispatch_agent_execution(agent_id):
    """Registered via `transaction.on_commit` by every caller -- never
    called directly inside an open transaction. A broker-publish failure
    must never propagate into the caller's own response/transaction."""
    from agents.tasks import advance_agent_step

    if settings.CELERY_TASK_ALWAYS_EAGER:
        advance_agent_step.delay(agent_id)
        return
    try:
        advance_agent_step.delay(agent_id)
    except Exception:
        logger.exception("Failed to publish agent %s step-advance to the broker", agent_id)


# --- claim ---


class _Claim:
    """Internal result of `_claim_next_step` -- `outcome` is one of
    "read" (caller must now run `run_read_step`), "blocked" (a gate-worthy
    step paused, nothing more to do), "step_failed" (a gate-worthy step's
    own dispatch raised, already recorded as a step failure -- caller must
    still run `_after_step_resolved`), "done" (plan completed), or "halted"
    (budget/status fencing stopped it, nothing more to do)."""

    def __init__(self, outcome, *, step_id=None):
        self.outcome = outcome
        self.step_id = step_id


@transaction.atomic
def _claim_next_step(agent_id):
    """Kill-switch fencing, START boundary: refuses to advance once the
    `Agent` is no longer `running` (covers `cancelled`, but also any other
    terminal/paused status a stale re-invocation might race against).

    Also refuses to advance while a step is already `running` for this
    Agent (Codex round-1 P2 fix): Celery's at-least-once delivery means
    `advance_agent_step` can be redelivered while a prior invocation's read
    step is still mid-flight (its own gateway calls not yet finalized) --
    without this check, the redelivered call would skip straight past it
    to the next `pending` step (or mark the Agent `completed` early),
    breaking the one-step-at-a-time guarantee this whole engine exists to
    provide. Safe under the same row lock as the RUNNING status write
    itself: only one worker can hold this Agent's lock at a time, so a
    concurrent redelivery always observes the committed RUNNING state."""
    agent = Agent.objects.select_for_update().get(pk=agent_id)
    if agent.status != Agent.Status.RUNNING:
        return _Claim("halted")
    if agent.steps.filter(status=AgentStep.Status.RUNNING).exists():
        return _Claim("halted")

    step = agent.steps.filter(status=AgentStep.Status.PENDING).order_by("order").first()
    if step is None:
        agent.status = Agent.Status.COMPLETED
        agent.save(update_fields=["status", "updated_at"])
        return _Claim("done")

    if step.kind == AgentStep.Kind.READ:
        # Budget brake (brief §3, brake #2c): checked before starting a
        # NEW read step only -- a step already in flight is always let
        # finish (bounded one-step overshoot), never aborted mid-call.
        if agent.gateway_calls_used >= settings.AGENT_MAX_GATEWAY_CALLS:
            agent.status = Agent.Status.FAILED
            agent.failure_reason = Agent.FailureReason.GATEWAY_BUDGET_EXCEEDED
            agent.save(update_fields=["status", "failure_reason", "updated_at"])
            return _Claim("halted")
        step.status = AgentStep.Status.RUNNING
        step.save(update_fields=["status", "updated_at"])
        return _Claim("read", step_id=str(step.id))

    if step.kind == AgentStep.Kind.TOOL_REQUEST:
        return _dispatch_tool_request_step(agent, step)
    return _dispatch_task_draft_step(agent, step)


def _dispatch_tool_request_step(agent, step):
    """`tool_request`-kind steps request, then pause -- the engine's own
    advance IS the ADR-004-gated trigger here (brief §3), since this kind
    of step is genuinely in ADR-004's risk class, unlike `task_draft`.
    `requested_by` is `agent.created_by` (never a synthetic "agent"
    identity), so `requester != decider` still binds the real human who
    submitted the goal, exactly as it would for a human-typed request."""
    tool_name = step.payload["tool_name"]
    try:
        approval_request = create_request(
            workspace=agent.workspace, actor=agent.created_by, tool_name=tool_name, payload={}
        )
    except ApprovalServiceError:
        logger.exception(
            "Failed to create approval request for agent %s step %s", agent.id, step.id
        )
        step.status = AgentStep.Status.FAILED
        step.result = {"error": "approval_request_failed"}
        step.save(update_fields=["status", "result", "updated_at"])
        return _Claim("step_failed", step_id=str(step.id))
    step.approval_request = approval_request
    step.status = AgentStep.Status.BLOCKED_ON_APPROVAL
    step.save(update_fields=["approval_request", "status", "updated_at"])
    agent.status = Agent.Status.BLOCKED_ON_APPROVAL
    agent.save(update_fields=["status", "updated_at"])
    return _Claim("blocked", step_id=str(step.id))


def _dispatch_task_draft_step(agent, step):
    """`task_draft`-kind steps pause at `blocked_on_confirmation` -- a
    lighter, agents-local pause requiring an explicit human click (the new
    confirm/discard endpoint, below), never the engine calling
    `tasks.services.create_task` on its own advance (brief §3's round-1
    CRITICAL fix)."""
    step.status = AgentStep.Status.BLOCKED_ON_CONFIRMATION
    step.save(update_fields=["status", "updated_at"])
    agent.status = Agent.Status.BLOCKED_ON_CONFIRMATION
    agent.save(update_fields=["status", "updated_at"])
    return _Claim("blocked", step_id=str(step.id))


# --- read step: unbounded-latency work, then fenced finalize ---


def run_read_step(step_id):
    """The unbounded-latency part of a `read`-kind step's work -- called
    with NO database lock held (the claim above already committed and
    released it), so a slow gateway round-trip never holds a Postgres row
    lock. Returns `(success, result)`. Always attempts exactly two gateway
    calls -- `select_context_chunks`'s own embed, then this step's own
    `agent.read_step` summarize -- regardless of either individual
    outcome; both are counted toward `gateway_calls_used` by
    `_finalize_read_step` (brief §3's round-4 accounting fix)."""
    step = AgentStep.objects.select_related("agent").get(pk=step_id)
    agent = step.agent

    chunks = select_context_chunks(
        actor=agent.created_by, workspace=agent.workspace, query_text=step.description
    )
    knowledge_block = render_knowledge_block(chunks)
    prompt_version = _agent_read_step_prompt_version()
    messages = [
        {"role": "system", "content": prompt_version.content + knowledge_block},
        {"role": "user", "content": step.description},
    ]
    gateway = InferenceGateway()
    try:
        result = gateway.complete(
            purpose="agent.read_step",
            messages=messages,
            actor=agent.created_by,
            workspace=agent.workspace,
            prompt_version=prompt_version,
            stream=False,
        )
    except GatewayError:
        return False, {"error": "gateway_error", "chunks_used": len(chunks)}
    return True, {"summary": result.text, "chunks_used": len(chunks)}


@transaction.atomic
def _finalize_read_step(step_id, *, success, result):
    """Kill-switch fencing, FINALIZATION boundary (brief §3's round-2 fix):
    the step's own result/status is always written here -- the gateway
    calls already happened, their cost is real -- but whether this counts
    as license to advance to the next step is decided by
    `_after_step_resolved`, which independently re-checks `Agent.status`."""
    step = AgentStep.objects.select_for_update().get(pk=step_id)
    agent = Agent.objects.select_for_update().get(pk=step.agent_id)
    step.status = AgentStep.Status.SUCCEEDED if success else AgentStep.Status.FAILED
    step.result = result
    step.save(update_fields=["status", "result", "updated_at"])
    agent.gateway_calls_used += 2
    agent.save(update_fields=["gateway_calls_used", "updated_at"])


# --- the single place that decides halt-or-continue ---


@transaction.atomic
def _after_step_resolved(agent_id, *, step_succeeded):
    """Kill-switch fencing, RESUME/CONTINUE boundary: a cancelled `Agent`
    never advances again, regardless of how its most recent step resolved.
    Otherwise this is the consecutive-failure brake (brief §3, brake #2b):
    reset to 0 on any clean outcome (succeeded or skipped -- a discarded
    task draft is not a failure), incremented on any failure, halting the
    whole `Agent` at `AGENT_MAX_CONSECUTIVE_FAILURES` with zero internal
    retries. If still viable, schedules the next step via
    `transaction.on_commit`."""
    agent = Agent.objects.select_for_update().get(pk=agent_id)
    if agent.status == Agent.Status.CANCELLED:
        return
    if step_succeeded:
        agent.consecutive_failures = 0
        agent.status = Agent.Status.RUNNING
        agent.save(update_fields=["consecutive_failures", "status", "updated_at"])
    else:
        agent.consecutive_failures += 1
        if agent.consecutive_failures >= settings.AGENT_MAX_CONSECUTIVE_FAILURES:
            agent.status = Agent.Status.FAILED
            agent.failure_reason = Agent.FailureReason.CONSECUTIVE_FAILURES
            agent.save(
                update_fields=["status", "failure_reason", "consecutive_failures", "updated_at"]
            )
            return
        agent.status = Agent.Status.RUNNING
        agent.save(update_fields=["status", "consecutive_failures", "updated_at"])
    transaction.on_commit(lambda: dispatch_agent_execution(str(agent_id)))


# --- the Celery task's own entry point (agents/tasks.py) ---


def advance_step(agent_id):
    """Called by the `advance_agent_step` Celery task (`agents/tasks.py`
    -- its own `soft_time_limit` bounds this whole call, the per-step
    timeout brake, brief §3 brake #2a). Kept Celery-agnostic so it's
    directly unit-testable without a broker/worker. `SoftTimeLimitExceeded`
    is only ever expected around `run_read_step`'s gateway calls -- the
    claim/finalize DB work is fast local work well within the budget."""
    claim = _claim_next_step(agent_id)
    if claim.outcome in ("halted", "done", "blocked"):
        return
    if claim.outcome == "step_failed":
        _after_step_resolved(agent_id, step_succeeded=False)
        return
    # claim.outcome == "read"
    try:
        success, result = run_read_step(claim.step_id)
    except SoftTimeLimitExceeded:
        _finalize_read_step(claim.step_id, success=False, result={"error": "timeout"})
        _after_step_resolved(agent_id, step_succeeded=False)
        return
    _finalize_read_step(claim.step_id, success=success, result=result)
    _after_step_resolved(agent_id, step_succeeded=success)


# --- kill switch ---


@transaction.atomic
def cancel_agent(*, agent, actor):
    """Kill switch (brief §3, brake #3, critical). Idempotent: cancelling
    an already-terminal `Agent` is a no-op, not an error (mirrors
    `tasks.services`' repeat-transition idempotency). The actual guarantee
    that no further step ever starts once cancelled lives in
    `_claim_next_step`'s and `_after_step_resolved`'s own
    `select_for_update` fencing -- this function only flips the flag."""
    locked = (
        Agent.objects.select_for_update()
        .filter(pk=agent.pk, workspace__members__user=actor)
        .first()
    )
    if locked is None:
        raise AgentInvalidTransition("Agent no longer exists.")
    role = get_role(actor, locked.workspace)
    _require(
        role in _CURATOR_ROLES or (role in _WRITER_ROLES and _is_own(locked, actor)),
        "cancelling this agent",
    )
    if locked.status in (Agent.Status.COMPLETED, Agent.Status.FAILED, Agent.Status.CANCELLED):
        return locked  # idempotent no-op, already terminal
    locked.status = Agent.Status.CANCELLED
    locked.save(update_fields=["status", "updated_at"])
    return locked


# --- resume after an ApprovalRequest decision (agents/signals.py) ---


def resume_after_approval_decision(*, agent_step_id, approved):
    """Called by the post_save signal receiver (`agents/signals.py`) when
    an `ApprovalRequest` tied to an `AgentStep` transitions away from
    `pending`. Deliberately a *safe, idempotent no-op* if the `Agent` is no
    longer `blocked_on_approval` (already cancelled, or somehow already
    resolved) -- the signal firing more than once, or after a cancellation,
    must never double-process or resurrect a stopped `Agent` (this is the
    exact "cancel, then approve the orphaned request anyway" scenario the
    brief's kill-switch test requires)."""
    try:
        step = AgentStep.objects.get(pk=agent_step_id)
    except AgentStep.DoesNotExist:
        return
    with transaction.atomic():
        agent = Agent.objects.select_for_update().get(pk=step.agent_id)
        if agent.status != Agent.Status.BLOCKED_ON_APPROVAL:
            return
        locked_step = AgentStep.objects.select_for_update().get(pk=agent_step_id)
        if locked_step.status != AgentStep.Status.BLOCKED_ON_APPROVAL:
            return
        locked_step.status = AgentStep.Status.SUCCEEDED if approved else AgentStep.Status.FAILED
        locked_step.save(update_fields=["status", "updated_at"])
    _after_step_resolved(str(step.agent_id), step_succeeded=approved)


# --- task_draft confirm/discard (agents/views.py) ---


def _require_can_confirm(agent, step, actor):
    role = get_role(actor, agent.workspace)
    _require(
        role in _CURATOR_ROLES or (role in _WRITER_ROLES and _is_own(agent, actor)),
        "confirming this task draft",
    )
    if (
        agent.status != Agent.Status.BLOCKED_ON_CONFIRMATION
        or step.status != AgentStep.Status.BLOCKED_ON_CONFIRMATION
    ):
        raise AgentInvalidTransition("This step is not awaiting confirmation.")


@transaction.atomic
def confirm_task_draft_step(*, step, actor):
    """The agents-local confirm endpoint's service call (brief §3's
    round-4 fix -- NOT the existing tasks API's `draft_message_id`
    mechanism, since an `AgentStep` can never resolve as an
    `assistant.Message`). The `AgentStep` itself is the server-verified
    reference here, playing the same role `draft_message_id`'s resolved
    `Message` plays in MVP 6 -- no client-asserted `source` either way; the
    confirming human never supplies title/due_date, only the step's own
    already-planned payload does."""
    locked_step = AgentStep.objects.select_for_update().get(pk=step.pk)
    agent = Agent.objects.select_for_update().get(pk=locked_step.agent_id)
    _require_can_confirm(agent, locked_step, actor)
    # payload["due_date"] is a plain JSON string (or None) as the planner
    # produced it -- mirrors assistant.task_draft.parse_task_draft's own
    # explicit str -> date conversion rather than relying on DateField's
    # implicit get_prep_value coercion.
    raw_due_date = locked_step.payload["due_date"]
    due_date = date.fromisoformat(raw_due_date) if raw_due_date else None
    # No except here, deliberately: unlike the tool_request dispatch path
    # above (an autonomous advance with nobody watching to retry, so it
    # must always resolve the step one way or another), a human is
    # actively clicking confirm right now -- if create_task raises
    # (TaskServiceError, e.g. the goal-submitter's role changed since
    # planning), the whole transaction rolls back, the step stays
    # `blocked_on_confirmation` untouched, and the view surfaces a normal
    # error the human can act on and retry -- silently marking the step
    # `failed` here would deny them that retry.
    task = create_task(
        workspace=agent.workspace,
        actor=agent.created_by,
        title=locked_step.payload["title"],
        due_date=due_date,
        source=Task.Source.AI_SUGGESTED,
    )
    locked_step.status = AgentStep.Status.SUCCEEDED
    locked_step.result = {"task_id": str(task.id)}
    locked_step.save(update_fields=["status", "result", "updated_at"])
    _after_step_resolved(str(agent.id), step_succeeded=True)
    return task


@transaction.atomic
def discard_task_draft_step(*, step, actor):
    """Discarding is not a step *failure* (brief §3) -- nothing gated was
    denied, the human just chose not to create that task. Counts as a
    clean outcome for the consecutive-failure brake."""
    locked_step = AgentStep.objects.select_for_update().get(pk=step.pk)
    agent = Agent.objects.select_for_update().get(pk=locked_step.agent_id)
    _require_can_confirm(agent, locked_step, actor)
    locked_step.status = AgentStep.Status.SKIPPED
    locked_step.save(update_fields=["status", "updated_at"])
    _after_step_resolved(str(agent.id), step_succeeded=True)
    return locked_step
