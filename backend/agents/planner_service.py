"""Planner orchestration: the one gateway call this MVP's planning step
makes (MVP 7 §2, brief), plus the tool-name resolution that fails a plan
before any `AgentStep` row is written.

`agents` may import `tools` (the one-way direction this MVP establishes),
so `tools.registry.list_tool_names()` is imported directly here, unlike
`assistant.services`'s local import of the same function (which exists
there only to avoid a *different* app's import-time constraints -- no such
constraint applies here).
"""

from django.core.exceptions import ImproperlyConfigured

from agents.models import Agent
from agents.planner import PlanParseError, parse_plan
from agents.services import fail_agent, finalize_plan, start_agent
from ai_core.exceptions import GatewayError
from ai_core.gateway import InferenceGateway
from ai_core.models import PromptTemplate
from tools.registry import list_tool_names

_AGENT_PLAN_PROMPT_NAME = "jarvis_agent_plan"


def _agent_plan_prompt_version():
    try:
        template = PromptTemplate.objects.get(name=_AGENT_PLAN_PROMPT_NAME)
    except PromptTemplate.DoesNotExist as exc:
        raise ImproperlyConfigured(
            f"{_AGENT_PLAN_PROMPT_NAME} prompt template is not configured"
        ) from exc
    if template.active_version_id is None:
        raise ImproperlyConfigured(
            f"{_AGENT_PLAN_PROMPT_NAME} prompt template has no active version"
        )
    return template.active_version


def create_agent_from_goal(*, workspace, actor, goal):
    """Creates an `Agent` in `planning` status, runs the `purpose="agent.
    plan"` gateway call, and either finalizes a valid plan (`agents.
    services.finalize_plan`) or fails the whole thing with `failure_reason
    = planner_error` (Codex plan-review round-1 CRITICAL precedent from
    MVP 6: a router classification failure degrades safely rather than
    corrupting state -- the same discipline applies here to a planner
    failure, except there is no safe "chat" fallback for an agent, so it
    fails loudly instead of silently).

    Three distinct failure modes all collapse to the same `planner_error`
    reason (brief §2: "an unresolvable tool name fails the plan for the
    same reason a malformed shape does, not a per-step skip") -- a
    `GatewayError` from the gateway call itself, a `PlanParseError` from
    the model's output not matching the schema, and a `tool_request` step
    naming a tool that isn't (or is no longer) registered. Never raises
    any of these to the caller.
    """
    agent = start_agent(workspace=workspace, actor=actor, goal=goal)

    prompt_version = _agent_plan_prompt_version()
    messages = [
        {"role": "system", "content": prompt_version.content},
        {"role": "user", "content": goal},
    ]
    gateway = InferenceGateway()
    try:
        result = gateway.complete(
            purpose="agent.plan",
            messages=messages,
            actor=actor,
            workspace=workspace,
            prompt_version=prompt_version,
            stream=False,
        )
    except GatewayError:
        return fail_agent(agent=agent, failure_reason=Agent.FailureReason.PLANNER_ERROR)

    try:
        steps = parse_plan(result.text)
    except PlanParseError:
        return fail_agent(agent=agent, failure_reason=Agent.FailureReason.PLANNER_ERROR)

    registered_tool_names = list_tool_names()
    for step in steps:
        if step["kind"] != "tool_request":
            continue
        if step["payload"]["tool_name"] not in registered_tool_names:
            return fail_agent(agent=agent, failure_reason=Agent.FailureReason.PLANNER_ERROR)

    return finalize_plan(agent=agent, steps=steps)
