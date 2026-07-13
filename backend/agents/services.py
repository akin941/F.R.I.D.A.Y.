"""Agent & plan creation -- the only sanctioned write path for `Agent`/
`AgentStep` rows (MVP 7 §1-2, brief). No dispatch side effects: this module
never calls `approvals.services.create_request`, `tasks.services.
create_task`, or any tool. That's the execution engine (branch 3).

Split into three primitives (branch 2) so the planner orchestration
(`agents.planner_service.create_agent_from_goal`) can create the `Agent` row
in `planning` status *before* its gateway call begins -- the goal-submitter
sees a row immediately, not only after the call returns:

- `start_agent`: creates the `planning`-status `Agent` row, nothing else.
- `fail_agent`: moves an existing `Agent` straight to `failed` with a reason.
- `finalize_plan`: given an already-`planning` `Agent` and a steps list,
  either creates all its `AgentStep` rows and moves it to `running`, or
  rejects the whole plan via `fail_agent` -- never a partial/truncated one.
- `create_plan`: a convenience wrapper combining the three in one atomic
  call, for callers (and branch 1's own tests) that already have a full
  steps list in hand and don't need the two-phase shape.

`finalize_plan`'s success path dispatches the first step's execution
(branch 3) via `transaction.on_commit` -- mirrors `tools.export`'s
non-propagating broker-publish shape exactly, through `agents.execution.
dispatch_agent_execution` (imported locally to avoid a module-import
cycle, same convention `tools.export.dispatch_vault_export_run` already
uses for its own `from tools.tasks import run_vault_export`).
"""

from django.conf import settings
from django.db import transaction

from agents.models import Agent, AgentStep


def start_agent(*, workspace, actor, goal):
    """Creates the `Agent` row in `planning` status. Call this first, then
    run the planner's gateway call, then `finalize_plan` or `fail_agent`."""
    return Agent.objects.create(
        workspace=workspace,
        goal=goal,
        created_by=actor,
        status=Agent.Status.PLANNING,
    )


@transaction.atomic
def fail_agent(*, agent, failure_reason):
    """Moves `agent` straight to `failed` with a recorded, user-visible
    reason (Codex round-1 MEDIUM fix, branch 1) -- never a bare status.

    Re-fetches and locks the row fresh rather than trusting the in-memory
    `agent` instance the caller passed in (Codex round-5 fix, branch 3):
    `agents.planner_service.create_agent_from_goal` holds `agent` across
    its own (possibly slow) planner gateway call with no lock held, so a
    `cancel_agent()` call landing in that window durably sets `cancelled`
    in the DB while the caller's own Python object still says `planning`.
    A blind `.save()` here would silently resurrect an already-cancelled
    Agent to `failed`. No-ops (returns the current, cancelled instance
    untouched) if that happened -- kill-switch fencing, PLANNING-
    COMPLETION boundary (a fourth boundary alongside start/resume/
    finalization: a plan that finishes deciding AFTER a cancel must never
    overwrite it)."""
    locked = Agent.objects.select_for_update().get(pk=agent.pk)
    if locked.status == Agent.Status.CANCELLED:
        return locked
    locked.status = Agent.Status.FAILED
    locked.failure_reason = failure_reason
    locked.save(update_fields=["status", "failure_reason", "updated_at"])
    return locked


@transaction.atomic
def finalize_plan(*, agent, steps):
    """`steps` is an ordered list of `{"kind", "description", "payload"}`
    dicts, already parsed and validated by the caller (the planner,
    `agents.planner_service` -- this branch's own tests stand in for it
    with fixture lists).

    Re-fetches and locks `agent` fresh, same reasoning and same fix as
    `fail_agent` above (Codex round-5 fix): a cancel landing while the
    planner's gateway call was still in flight must never be overwritten
    by this call finally completing -- no-ops (returns the current,
    cancelled instance untouched, no `AgentStep` rows created, no
    dispatch) if the Agent is already `cancelled`.

    Otherwise, two conditions reject the whole plan before any `AgentStep`
    row is written -- never a partial/truncated one:

    - more steps than `AGENT_MAX_PLAN_STEPS` (the plan step-count bound);
    - zero steps (most likely a planner malfunction, not a legitimately
      "already done" goal -- see the brief's §2 empty-plan note).

    Either way `agent` goes straight to `failed` via `fail_agent`. A valid,
    non-empty, within-bound plan creates all its `AgentStep` rows and moves
    `agent` to `running`.
    """
    locked = Agent.objects.select_for_update().get(pk=agent.pk)
    if locked.status == Agent.Status.CANCELLED:
        return locked
    if len(steps) > settings.AGENT_MAX_PLAN_STEPS:
        return fail_agent(agent=locked, failure_reason=Agent.FailureReason.PLAN_TOO_LARGE)
    if not steps:
        return fail_agent(agent=locked, failure_reason=Agent.FailureReason.PLAN_EMPTY)
    AgentStep.objects.bulk_create(
        AgentStep(
            agent=locked,
            order=index,
            kind=step["kind"],
            description=step.get("description", ""),
            payload=step.get("payload", {}),
        )
        for index, step in enumerate(steps)
    )
    locked.status = Agent.Status.RUNNING
    locked.save(update_fields=["status", "updated_at"])
    from agents.execution import dispatch_agent_execution

    transaction.on_commit(lambda: dispatch_agent_execution(str(locked.id)))
    return locked


@transaction.atomic
def create_plan(*, workspace, actor, goal, steps):
    """`start_agent` + `finalize_plan` in one atomic call, for callers that
    already have a full steps list in hand."""
    agent = start_agent(workspace=workspace, actor=actor, goal=goal)
    return finalize_plan(agent=agent, steps=steps)
