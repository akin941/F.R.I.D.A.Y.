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
from datetime import date, timedelta

from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from agents.models import Agent, AgentStep
from ai_core.exceptions import GatewayError
from ai_core.gateway import InferenceGateway
from ai_core.models import PromptTemplate
from approvals.models import ApprovalRequest
from approvals.services import ApprovalServiceError, create_request
from core.channels import CHANNEL_SYSTEM
from knowledge.context import render_knowledge_block, select_context_chunks
from tasks.models import Task
from tasks.services import create_task
from workspaces.models import WorkspaceMember

logger = logging.getLogger(__name__)

_AGENT_READ_STEP_PROMPT_NAME = "friday_agent_read_step"

_CURATOR_ROLES = {WorkspaceMember.Role.OWNER, WorkspaceMember.Role.ADMIN}
_WRITER_ROLES = _CURATOR_ROLES | {WorkspaceMember.Role.MEMBER}


class AgentServiceError(Exception):
    """Base class; views translate subclasses to HTTP statuses."""


class AgentPermissionDenied(AgentServiceError):
    """Actor's role does not allow the action -> 403."""

    code = "agent.permission_denied"


class AgentInvalidTransition(AgentServiceError):
    """Illegal state-machine move (e.g. confirming an already-resolved
    step, or one that no longer exists) -> 400."""

    code = "agent.invalid_transition"


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
    identity) so the accountable human stays on the row, and
    `origin=agent` records the machine provenance. Under ADR-008's
    single-owner decision model the owner CAN decide this request -- the
    gate is their explicit, separate, audited decision over the web
    channel, not a second human."""
    tool_name = step.payload["tool_name"]
    try:
        approval_request = create_request(
            workspace=agent.workspace,
            actor=agent.created_by,
            tool_name=tool_name,
            payload={},
            origin=ApprovalRequest.Origin.AGENT,
            channel=CHANNEL_SYSTEM,
        )
    except ApprovalServiceError:
        logger.exception(
            "Failed to create approval request for agent %s step %s", agent.id, step.id
        )
        step.status = AgentStep.Status.FAILED
        step.result = {"error": "approval_request_failed"}
        step.save(update_fields=["status", "result", "updated_at"])
        # Codex round-3 fix: decide halt-or-continue INLINE, in this same
        # already-open transaction (`agent` is already locked by
        # `_claim_next_step`) -- never as a separate later call. Closes the
        # gap where a duplicate Celery delivery could see `agent.status ==
        # running` with no `running` step in between and claim the next
        # pending step before this failure was actually counted toward the
        # consecutive-failure brake.
        _decide_halt_or_continue(agent, step_succeeded=False)
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
    calls already happened, their cost is real. The halt-or-continue
    decision (`_decide_halt_or_continue`) is applied INLINE, in this same
    transaction, immediately after (Codex round-3 fix) -- never as a
    separate later call, which left a gap where a duplicate Celery
    delivery could observe `agent.status == running` with no `running`
    step in between and claim the next pending step before this one's
    outcome was actually counted toward the consecutive-failure brake.

    Compare-and-swap fenced (Codex round-2 fix): only proceeds if the step
    is still `running` at lock time. This is what makes
    `recover_stale_agent_execution`'s reclaim of a worker-lost step safe --
    whichever of {the original (possibly just-slow, not actually dead)
    worker, the recovery scan} commits its own finalize call first wins;
    the other observes the step is no longer `running` and becomes a safe
    no-op instead of double-writing `gateway_calls_used` or double-applying
    the halt-or-continue decision. Returns `True` if this call actually
    finalized the step, `False` if it was already resolved by someone
    else (in which case nothing else happens)."""
    step = (
        AgentStep.objects.select_for_update()
        .filter(pk=step_id, status=AgentStep.Status.RUNNING)
        .first()
    )
    if step is None:
        return False
    agent = Agent.objects.select_for_update().get(pk=step.agent_id)
    step.status = AgentStep.Status.SUCCEEDED if success else AgentStep.Status.FAILED
    step.result = result
    step.save(update_fields=["status", "result", "updated_at"])
    agent.gateway_calls_used += 2
    agent.save(update_fields=["gateway_calls_used", "updated_at"])
    _decide_halt_or_continue(agent, step_succeeded=success)
    return True


# --- the single place that decides halt-or-continue ---


def _decide_halt_or_continue(agent, *, step_succeeded):
    """The halt-or-continue decision body: a cancelled `Agent` never
    advances again, regardless of how its most recent step resolved.
    Otherwise this is the consecutive-failure brake (brief §3, brake #2b):
    reset to 0 on any clean outcome (succeeded or skipped -- a discarded
    task draft is not a failure), incremented on any failure, halting the
    whole `Agent` at `AGENT_MAX_CONSECUTIVE_FAILURES` with zero internal
    retries. If still viable, schedules the next step via
    `transaction.on_commit`.

    Factored out of `_after_step_resolved` (Codex round-3 fix) so callers
    that already hold `agent` `select_for_update`-locked within their OWN
    open transaction (`_finalize_read_step`, `_dispatch_tool_request_step`)
    can apply this decision in that SAME transaction, rather than
    releasing the lock and re-acquiring it in a separate later call --
    closing the gap where a duplicate delivery could sneak in between the
    two and claim the next step before this one's outcome was counted.
    MUST NOT be called with an `agent` that isn't already locked in the
    caller's own open transaction."""
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
    transaction.on_commit(lambda: dispatch_agent_execution(str(agent.id)))


@transaction.atomic
def _after_step_resolved(agent_id, *, step_succeeded):
    """Kill-switch fencing, RESUME/CONTINUE boundary: locks `agent` fresh
    and applies `_decide_halt_or_continue`. Safe to call as a separate,
    later transaction ONLY because every current caller's own preceding
    step-mutation leaves `Agent.status` at a non-`running` paused status
    the whole time in between (`resume_after_approval_decision`'s
    `blocked_on_approval` -> resolved-step write; `confirm_task_draft_step`/
    `discard_task_draft_step`'s nested-atomic call, where Django's
    savepoint nesting means there IS no separate transaction at all) -- so
    a duplicate `_claim_next_step` call in that gap always sees
    `agent.status != running` and no-ops, unlike the read-step/tool_request
    paths `_finalize_read_step`/`_dispatch_tool_request_step` now decide
    inline for exactly that reason."""
    agent = Agent.objects.select_for_update().get(pk=agent_id)
    _decide_halt_or_continue(agent, step_succeeded=step_succeeded)


# --- the Celery task's own entry point (agents/tasks.py) ---


def advance_step(agent_id):
    """Called by the `advance_agent_step` Celery task (`agents/tasks.py`
    -- its own `soft_time_limit` bounds this whole call, the per-step
    timeout brake, brief §3 brake #2a). Kept Celery-agnostic so it's
    directly unit-testable without a broker/worker. `SoftTimeLimitExceeded`
    is only ever expected around `run_read_step`'s gateway calls -- the
    claim/finalize DB work is fast local work well within the budget."""
    claim = _claim_next_step(agent_id)
    if claim.outcome in ("halted", "done", "blocked", "step_failed"):
        # "step_failed" already applied its own halt-or-continue decision
        # inline, within the same transaction `_claim_next_step` held the
        # Agent lock in (see `_dispatch_tool_request_step`) -- nothing left
        # to do here.
        return
    # claim.outcome == "read"
    try:
        success, result = run_read_step(claim.step_id)
    except SoftTimeLimitExceeded:
        _finalize_read_step(claim.step_id, success=False, result={"error": "timeout"})
        return
    _finalize_read_step(claim.step_id, success=success, result=result)


def resolve_running_step_after_unexpected_error(agent_id):
    """Called by `agents.tasks.advance_agent_step`'s outer safety net when
    `advance_step` raises something other than `SoftTimeLimitExceeded`
    (Codex round-7 fix) -- e.g. a missing `friday_agent_read_step` prompt
    raising `ImproperlyConfigured`, or any other unexpected bug. Without
    this, `_claim_next_step` may have already committed a read step to
    `running` before the exception hit; the outer handler used to just log
    and swallow it, leaving the step `running` -- not stuck forever (the
    recovery scan reclaims it after `AGENT_STEP_RECOVERY_GRACE_SECONDS`),
    but stalled for that whole window when the failure could be resolved
    immediately. A safe no-op if no step is currently `running` for this
    agent (the exception happened before or without claiming one) --
    `_finalize_read_step`'s own compare-and-swap fencing makes this safe
    even if the step was somehow already resolved by the time this runs."""
    step = AgentStep.objects.filter(agent_id=agent_id, status=AgentStep.Status.RUNNING).first()
    if step is None:
        return
    _finalize_read_step(str(step.id), success=False, result={"error": "unexpected_error"})


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
    brief's kill-switch test requires).

    The step's own resolution and the halt-or-continue decision are
    applied in the SAME transaction (Codex round-6 fix -- the same
    atomicity gap round 3 already closed for `_finalize_read_step`/
    `_dispatch_tool_request_step`): previously this was two separate
    transactions, so a process death in between left the step resolved
    but the Agent stuck `blocked_on_approval` forever -- unrecoverable
    even by the recovery scan, which only looks for steps still
    `blocked_on_approval`."""
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
        _decide_halt_or_continue(agent, step_succeeded=approved)


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


# --- recovery scan (Codex round-2 fix; agents/tasks.py's periodic task) ---


def recover_stale_agent_execution():
    """Periodic scan (Celery beat, `agents/tasks.py`) for three distinct
    ways an `Agent`'s execution can get permanently stuck with no live
    worker ever coming back to unstick it -- none of these are covered by
    the per-step Celery timeout alone, which only fires if the SAME worker
    process survives long enough to catch its own `SoftTimeLimitExceeded`:

    1. A `read`-kind `AgentStep` still `running` past
       `AGENT_STEP_RECOVERY_GRACE_SECONDS` since its own last update: the
       worker that claimed it died (OOM-kill, host crash, or a hard
       `time_limit` SIGKILL before the soft-limit handler ran) before
       `_finalize_read_step` ever executed. Reclaimed as a failure (mirrors
       the timeout path's `{"error": "timeout"}` shape) via the SAME
       compare-and-swap fenced `_finalize_read_step` the real worker itself
       uses -- so if that worker was actually just slow, not dead, and
       finalizes for real either just before or just after this scan, only
       one of the two calls ever takes effect (see `_finalize_read_step`'s
       own docstring). Counts toward the existing consecutive-failure brake
       exactly like a genuine per-step timeout does -- no separate retry
       budget needed.
    2. An `Agent` still `running`, with no currently-running step, past
       `AGENT_DISPATCH_RECOVERY_GRACE_SECONDS` since its own last update:
       its `transaction.on_commit`-scheduled dispatch to the next `pending`
       step was lost (e.g. the broker was down at commit time -- the exact
       swallowed-exception path in `dispatch_agent_execution`). Simply
       re-dispatched; `_claim_next_step`'s own fencing makes this safe even
       if the original dispatch actually does arrive later (whichever
       claims first wins, the other observes `running` is no longer
       matched by its own claim conditions and no-ops).
    3. An `AgentStep` still `blocked_on_approval` past
       `AGENT_APPROVAL_RESUME_RECOVERY_GRACE_SECONDS` since its linked
       `ApprovalRequest` was actually decided -- (Codex round-4 fix):
       `agents.signals`' own resume is ALSO only an in-memory
       `transaction.on_commit` callback; if the process exits after the
       `ApprovalRequest`'s decision commits but before that callback runs,
       the decision is durable but nothing is ever told about it. Covers
       BOTH a human decision (`approved`/`rejected`, anchored on
       `decided_at`) AND the expiry sweep's own decision (`expired`,
       anchored on `updated_at` instead -- Codex round-5 fix: the expiry
       paths, `approvals.services._expire_if_stale`/`approvals.tasks.
       _expire_one`, never set `decided_at` at all, so the round-4 query
       alone would never match an expired request whose resume callback
       was lost). Resolved by calling `resume_after_approval_decision`
       directly -- which is itself fenced/idempotent (re-checks `Agent`/
       `AgentStep` are still in the expected paused state under lock), so
       this is a safe no-op if the real signal callback did fire in the
       meantime.

    Also runnable manually via `manage.py recover_agents` (calls this
    function directly, not `.delay()` -- no broker/worker required)."""
    now = timezone.now()

    step_cutoff = now - timedelta(seconds=settings.AGENT_STEP_RECOVERY_GRACE_SECONDS)
    stale_steps = list(
        AgentStep.objects.filter(
            status=AgentStep.Status.RUNNING, updated_at__lt=step_cutoff
        ).values_list("id", "agent_id")
    )
    reclaimed_steps = 0
    for step_id, _agent_id in stale_steps:
        # `_finalize_read_step` applies the halt-or-continue decision
        # inline now (Codex round-3 fix) -- nothing further to do here.
        if _finalize_read_step(str(step_id), success=False, result={"error": "worker_lost"}):
            reclaimed_steps += 1

    dispatch_cutoff = now - timedelta(seconds=settings.AGENT_DISPATCH_RECOVERY_GRACE_SECONDS)
    running_step_agent_ids = AgentStep.objects.filter(status=AgentStep.Status.RUNNING).values_list(
        "agent_id", flat=True
    )
    stale_agent_ids = list(
        Agent.objects.filter(status=Agent.Status.RUNNING, updated_at__lt=dispatch_cutoff)
        .exclude(id__in=running_step_agent_ids)
        .values_list("id", flat=True)
    )
    for agent_id in stale_agent_ids:
        dispatch_agent_execution(str(agent_id))

    approval_cutoff = now - timedelta(seconds=settings.AGENT_APPROVAL_RESUME_RECOVERY_GRACE_SECONDS)
    stale_approval_steps = list(
        AgentStep.objects.filter(status=AgentStep.Status.BLOCKED_ON_APPROVAL)
        .filter(
            Q(
                approval_request__status__in=[
                    ApprovalRequest.Status.APPROVED,
                    ApprovalRequest.Status.REJECTED,
                ],
                approval_request__decided_at__lt=approval_cutoff,
            )
            # Expiry never sets decided_at (Codex round-5 fix) -- anchor on
            # updated_at instead, which both expiry paths do touch.
            | Q(
                approval_request__status=ApprovalRequest.Status.EXPIRED,
                approval_request__updated_at__lt=approval_cutoff,
            )
        )
        .values_list("id", "approval_request__status")
    )
    for step_id, approval_status in stale_approval_steps:
        resume_after_approval_decision(
            agent_step_id=step_id, approved=approval_status == ApprovalRequest.Status.APPROVED
        )

    return {
        "reclaimed_steps": reclaimed_steps,
        "redispatched_agents": len(stale_agent_ids),
        "resumed_approvals": len(stale_approval_steps),
    }
