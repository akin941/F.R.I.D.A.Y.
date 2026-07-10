"""Agent & plan creation -- the only sanctioned write path for `Agent`/
`AgentStep` rows (MVP 7 §1, brief). No dispatch side effects: this module
never calls `approvals.services.create_request`, `tasks.services.
create_task`, or any tool. That's the execution engine (branch 3).
"""

from django.conf import settings
from django.db import transaction

from agents.models import Agent, AgentStep


@transaction.atomic
def create_plan(*, workspace, actor, goal, steps):
    """`steps` is an ordered list of `{"kind", "payload"}` dicts, already
    parsed and validated by the caller (the planner, branch 2 -- this
    branch's own tests stand in for it with fixture lists). An `Agent` row
    always exists after this call, so the human who submitted the goal can
    see what happened and why, even on failure.

    Two conditions reject the whole plan before any `AgentStep` row is
    written -- never a partial/truncated one:

    - more steps than `AGENT_MAX_PLAN_STEPS` (the plan step-count bound);
    - zero steps (most likely a planner malfunction, not a legitimately
      "already done" goal -- see the brief's §2 empty-plan note).

    Either way `Agent.status` goes straight to `failed`, with
    `failure_reason` recording which of the two it was (Codex round-1
    MEDIUM fix -- a bare `failed` status was not the "user-visible reason"
    brief §1 actually calls for). A valid, non-empty, within-bound plan
    creates all its `AgentStep` rows and moves the `Agent` to `running`.
    """
    agent = Agent.objects.create(
        workspace=workspace,
        goal=goal,
        created_by=actor,
        status=Agent.Status.PLANNING,
    )
    if len(steps) > settings.AGENT_MAX_PLAN_STEPS:
        agent.status = Agent.Status.FAILED
        agent.failure_reason = Agent.FailureReason.PLAN_TOO_LARGE
        agent.save(update_fields=["status", "failure_reason", "updated_at"])
        return agent
    if not steps:
        agent.status = Agent.Status.FAILED
        agent.failure_reason = Agent.FailureReason.PLAN_EMPTY
        agent.save(update_fields=["status", "failure_reason", "updated_at"])
        return agent
    AgentStep.objects.bulk_create(
        AgentStep(
            agent=agent,
            order=index,
            kind=step["kind"],
            payload=step.get("payload", {}),
        )
        for index, step in enumerate(steps)
    )
    agent.status = Agent.Status.RUNNING
    agent.save(update_fields=["status", "updated_at"])
    return agent
